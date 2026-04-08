"""Dependency DAG for sheet execution ordering.

This module implements a Directed Acyclic Graph (DAG) for managing sheet
dependencies in Marianne jobs. It enables:
- Explicit dependency declarations between sheets
- Topological sorting for valid execution order
- Cycle detection to prevent infinite loops
- Parallel group identification for concurrent execution

The DAG is a foundation for parallel sheet execution (Evolution 2 of v17).
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any


class DAGReadyStatus(Enum):
    """Result of querying the DAG for the next executable sheet.

    Distinguishes between "all sheets are done" and "remaining sheets are
    blocked by failed/incomplete dependencies" — previously both returned
    None, causing silent deadlocks.
    """

    READY = "ready"
    ALL_COMPLETE = "all_complete"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DAGNextResult:
    """Result of a DAG-aware next-sheet query.

    Attributes:
        status: Whether a sheet is ready, all are complete, or remaining are blocked.
        sheet_num: The next sheet to execute (only set when status == READY).
        blocked_sheets: Sheets that can't run because deps failed (only when BLOCKED).
    """

    status: DAGReadyStatus
    sheet_num: int | None = None
    blocked_sheets: list[int] = field(default_factory=list)


class _VisitState(IntEnum):
    """DFS visit states for cycle detection."""
    UNVISITED = 0
    IN_PROGRESS = 1
    COMPLETED = 2


class CycleDetectedError(Exception):
    """Raised when a cycle is detected in sheet dependencies.

    Attributes:
        cycle: The detected cycle as a list of sheet numbers.
        message: Human-readable description of the cycle.
    """

    def __init__(self, cycle: list[int], message: str | None = None):
        self.cycle = cycle
        if message is None:
            cycle_str = " -> ".join(str(s) for s in cycle)
            message = f"Circular dependency detected: {cycle_str}"
        super().__init__(message)


class InvalidDependencyError(Exception):
    """Raised when a dependency references an invalid sheet.

    Attributes:
        sheet_num: The sheet containing the invalid dependency.
        invalid_dep: The invalid dependency value.
        reason: Why the dependency is invalid.
    """

    def __init__(self, sheet_num: int, invalid_dep: int, reason: str):
        self.sheet_num = sheet_num
        self.invalid_dep = invalid_dep
        self.reason = reason
        super().__init__(f"Sheet {sheet_num}: invalid dependency {invalid_dep} - {reason}")


@dataclass
class DependencyDAG:
    """Directed Acyclic Graph for sheet dependencies.

    Builds a DAG from sheet dependency declarations and provides methods
    for determining valid execution order and identifying parallelizable groups.

    Example:
        >>> dag = DependencyDAG.from_dependencies(
        ...     total_sheets=5,
        ...     dependencies={2: [1], 3: [1], 4: [2, 3], 5: [4]}
        ... )
        >>> dag.get_execution_order()
        [1, 2, 3, 4, 5]
        >>> dag.get_parallel_groups()
        [[1], [2, 3], [4], [5]]

    Attributes:
        total_sheets: Total number of sheets in the job.
        edges: Forward edges (sheet -> sheets that depend on it).
        reverse_edges: Backward edges (sheet -> sheets it depends on).
        in_degree: Number of dependencies for each sheet.
        validated: Whether the DAG has been validated for cycles.
    """

    total_sheets: int
    edges: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    reverse_edges: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    in_degree: dict[int, int] = field(default_factory=dict)
    validated: bool = False

    @classmethod
    def from_dependencies(
        cls,
        total_sheets: int,
        dependencies: dict[int, list[int]] | None = None,
    ) -> "DependencyDAG":
        """Create a DAG from sheet dependency declarations.

        Args:
            total_sheets: Total number of sheets (1-indexed, so sheets 1..total_sheets).
            dependencies: Map of sheet_num -> list of sheets it depends on.
                         If None, assumes sequential dependencies (each sheet depends on previous).

        Returns:
            Validated DependencyDAG ready for use.

        Raises:
            InvalidDependencyError: If a dependency references a non-existent sheet.
            CycleDetectedError: If dependencies contain a cycle.

        Example:
            >>> # Sheet 3 depends on 1 and 2, sheet 4 depends on 3
            >>> dag = DependencyDAG.from_dependencies(
            ...     total_sheets=4,
            ...     dependencies={3: [1, 2], 4: [3]}
            ... )
        """
        dag = cls(total_sheets=total_sheets)

        # Initialize in_degree for all sheets
        for sheet in range(1, total_sheets + 1):
            dag.in_degree[sheet] = 0

        # Build graph from dependencies
        if dependencies:
            for sheet_num, deps in dependencies.items():
                # Validate sheet_num is in range
                if sheet_num < 1 or sheet_num > total_sheets:
                    raise InvalidDependencyError(
                        sheet_num=sheet_num,
                        invalid_dep=sheet_num,
                        reason=f"sheet number out of range (1-{total_sheets})",
                    )

                for dep in deps:
                    # Validate dependency is in range
                    if dep < 1 or dep > total_sheets:
                        raise InvalidDependencyError(
                            sheet_num=sheet_num,
                            invalid_dep=dep,
                            reason=f"dependency out of range (1-{total_sheets})",
                        )

                    # Validate no self-dependency
                    if dep == sheet_num:
                        raise InvalidDependencyError(
                            sheet_num=sheet_num,
                            invalid_dep=dep,
                            reason="sheet cannot depend on itself",
                        )

                    # Add edges (dep -> sheet_num means sheet_num depends on dep)
                    # Forward: dep completes -> sheet_num can run
                    dag.edges[dep].append(sheet_num)
                    # Reverse: sheet_num depends on dep
                    dag.reverse_edges[sheet_num].append(dep)
                    dag.in_degree[sheet_num] += 1

        # Validate no cycles
        dag._validate_no_cycles()
        dag.validated = True

        return dag

    def _validate_no_cycles(self) -> None:
        """Check for cycles using iterative DFS.

        Uses an explicit stack instead of recursion to handle large
        DAGs (thousands of sheets) without hitting Python's recursion
        limit.

        Raises:
            CycleDetectedError: If a cycle is detected, includes the cycle path.
        """
        state: dict[int, _VisitState] = dict.fromkeys(
            range(1, self.total_sheets + 1), _VisitState.UNVISITED
        )
        # Track path for cycle reconstruction
        path: list[int] = []
        path_set: set[int] = set()

        for start in range(1, self.total_sheets + 1):
            if state[start] != _VisitState.UNVISITED:
                continue

            # Stack holds (sheet, iterator_over_dependents)
            stack: list[tuple[int, list[int], int]] = []
            state[start] = _VisitState.IN_PROGRESS
            path.append(start)
            path_set.add(start)
            dependents = self.edges.get(start, [])
            stack.append((start, dependents, 0))

            while stack:
                node, deps, idx = stack[-1]

                if idx < len(deps):
                    # Advance to next dependent
                    stack[-1] = (node, deps, idx + 1)
                    child = deps[idx]

                    if state[child] == _VisitState.IN_PROGRESS:
                        # Cycle found — reconstruct from path
                        cycle_start = path.index(child)
                        cycle = path[cycle_start:] + [child]
                        raise CycleDetectedError(cycle)

                    if state[child] == _VisitState.UNVISITED:
                        state[child] = _VisitState.IN_PROGRESS
                        path.append(child)
                        path_set.add(child)
                        child_deps = self.edges.get(child, [])
                        stack.append((child, child_deps, 0))
                else:
                    # All dependents visited — mark complete
                    stack.pop()
                    state[node] = _VisitState.COMPLETED
                    path.pop()
                    path_set.discard(node)

    def get_execution_order(self) -> list[int]:
        """Get a valid topological execution order using Kahn's algorithm.

        Returns sheets in an order where all dependencies are satisfied
        before a sheet executes.

        Returns:
            List of sheet numbers in valid execution order.

        Example:
            >>> dag = DependencyDAG.from_dependencies(4, {2: [1], 3: [1, 2], 4: [3]})
            >>> dag.get_execution_order()
            [1, 2, 3, 4]
        """
        # Copy in_degree since we'll modify it
        in_degree = dict(self.in_degree)
        result: list[int] = []

        # Start with sheets that have no dependencies (min-heap for O(log n) ordering)
        ready: list[int] = sorted(sheet for sheet, degree in in_degree.items() if degree == 0)
        heapq.heapify(ready)

        while ready:
            # Process the lowest-numbered ready sheet (deterministic via min-heap)
            sheet = heapq.heappop(ready)
            result.append(sheet)

            # Reduce in-degree of dependent sheets
            for dependent in self.edges.get(sheet, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    heapq.heappush(ready, dependent)

        return result

    def get_parallel_groups(self) -> list[list[int]]:
        """Get groups of sheets that can execute in parallel.

        Returns sheets grouped by "level" in the dependency graph.
        All sheets in a group have their dependencies satisfied
        by previous groups.

        Returns:
            List of lists, where each inner list contains sheet numbers
            that can execute concurrently.

        Example:
            >>> dag = DependencyDAG.from_dependencies(
            ...     total_sheets=5,
            ...     dependencies={2: [1], 3: [1], 4: [2, 3], 5: [4]}
            ... )
            >>> dag.get_parallel_groups()
            [[1], [2, 3], [4], [5]]  # 2 and 3 can run in parallel
        """
        # Copy in_degree since we'll modify it
        in_degree = dict(self.in_degree)
        groups: list[list[int]] = []

        remaining = set(range(1, self.total_sheets + 1))

        while remaining:
            # Find all sheets with no remaining dependencies
            ready = sorted(sheet for sheet in remaining if in_degree[sheet] == 0)

            if not ready:
                # This shouldn't happen if DAG was validated
                break

            groups.append(ready)

            # Remove ready sheets and update in-degrees
            for sheet in ready:
                remaining.remove(sheet)
                for dependent in self.edges.get(sheet, []):
                    in_degree[dependent] -= 1

        return groups

    def get_ready_sheets(self, completed: set[int]) -> list[int]:
        """Get sheets that are ready to execute given completed sheets.

        Args:
            completed: Set of sheet numbers that have completed.

        Returns:
            Sorted list of sheet numbers whose dependencies are all satisfied.

        Example:
            >>> dag = DependencyDAG.from_dependencies(4, {2: [1], 3: [1], 4: [2, 3]})
            >>> dag.get_ready_sheets({1})
            [2, 3]  # Both 2 and 3 can run after 1 completes
            >>> dag.get_ready_sheets({1, 2})
            [3]  # 3 is still ready (was already), 4 needs 3 too
            >>> dag.get_ready_sheets({1, 2, 3})
            [4]  # Now 4 can run
        """
        ready = []
        for sheet in range(1, self.total_sheets + 1):
            if sheet in completed:
                continue

            # Check if all dependencies are completed
            deps = self.reverse_edges.get(sheet, [])
            if all(dep in completed for dep in deps):
                ready.append(sheet)

        return sorted(ready)

    def get_dependencies(self, sheet_num: int) -> list[int]:
        """Get direct dependencies for a sheet.

        Args:
            sheet_num: The sheet to get dependencies for.

        Returns:
            Sorted list of sheet numbers that must complete before this sheet.
        """
        return sorted(self.reverse_edges.get(sheet_num, []))

    def get_dependents(self, sheet_num: int) -> list[int]:
        """Get sheets that depend on a given sheet.

        Args:
            sheet_num: The sheet to get dependents for.

        Returns:
            Sorted list of sheet numbers that depend on this sheet.
        """
        return sorted(self.edges.get(sheet_num, []))

    def has_dependencies(self) -> bool:
        """Check if any sheet has explicit dependencies.

        Returns:
            True if any sheet depends on another, False if all independent.
        """
        return any(degree > 0 for degree in self.in_degree.values())

    def is_parallelizable(self) -> bool:
        """Check if the DAG allows any parallel execution.

        Returns:
            True if any parallel group has more than one sheet.
        """
        groups = self.get_parallel_groups()
        return any(len(group) > 1 for group in groups)

    def to_dict(self) -> dict[str, Any]:
        """Serialize DAG to dictionary for JSON storage/display.

        Returns:
            Dictionary representation of the DAG.
        """
        return {
            "total_sheets": self.total_sheets,
            "dependencies": {
                sheet: sorted(self.reverse_edges.get(sheet, []))
                for sheet in range(1, self.total_sheets + 1)
                if self.reverse_edges.get(sheet)
            },
            "execution_order": self.get_execution_order(),
            "parallel_groups": self.get_parallel_groups(),
            "parallelizable": self.is_parallelizable(),
        }

    def __str__(self) -> str:
        """Human-readable string representation."""
        lines = [f"DependencyDAG({self.total_sheets} sheets)"]

        # Show dependencies
        has_deps = False
        for sheet in range(1, self.total_sheets + 1):
            deps = self.get_dependencies(sheet)
            if deps:
                has_deps = True
                deps_str = ", ".join(str(d) for d in deps)
                lines.append(f"  Sheet {sheet} depends on: [{deps_str}]")

        if not has_deps:
            lines.append("  No dependencies (sequential execution)")

        # Show parallel groups if parallelizable
        if self.is_parallelizable():
            groups = self.get_parallel_groups()
            parallel_str = " | ".join(
                f"[{', '.join(str(s) for s in g)}]" for g in groups
            )
            lines.append(f"  Parallel groups: {parallel_str}")

        return "\n".join(lines)


def build_dag_from_config(
    total_sheets: int,
    sheet_dependencies: dict[int, list[int]] | None = None,
) -> DependencyDAG:
    """Convenience function to build DAG from config values.

    Args:
        total_sheets: Number of sheets in the job.
        sheet_dependencies: Optional dependency declarations from config.

    Returns:
        Validated DependencyDAG.

    Raises:
        CycleDetectedError: If dependencies contain cycles.
        InvalidDependencyError: If dependencies reference invalid sheets.
    """
    return DependencyDAG.from_dependencies(
        total_sheets=total_sheets,
        dependencies=sheet_dependencies,
    )
