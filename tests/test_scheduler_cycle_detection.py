"""Tests for GlobalSheetScheduler._detect_cycle() — iterative DFS cycle detection.

These tests verify that the scheduler's cycle detection handles:
1. Acyclic graphs (no false positives)
2. Simple cycles (A→B→A)
3. Complex cycles (A→B→C→A)
4. Self-loops (A→A)
5. Large chains (1500+ nodes) without stack overflow
6. Disconnected components with and without cycles
7. Diamond patterns (not cycles)
8. Multiple independent cycles
9. Empty and single-node graphs

All tests call _detect_cycle directly since it's a @staticmethod.

Created by Ghost, Movement 1.
"""

import pytest

from marianne.daemon.scheduler import GlobalSheetScheduler


class TestDetectCycleAcyclic:
    """Tests that acyclic graphs return None (no cycle)."""

    def test_empty_deps(self) -> None:
        """Empty dependency dict has no cycles."""
        assert GlobalSheetScheduler._detect_cycle({}) is None

    def test_single_node_no_deps(self) -> None:
        """Single node with no dependencies."""
        assert GlobalSheetScheduler._detect_cycle({1: set()}) is None

    def test_linear_chain(self) -> None:
        """Linear chain: 1←2←3←4 (no cycle)."""
        deps = {2: {1}, 3: {2}, 4: {3}}
        assert GlobalSheetScheduler._detect_cycle(deps) is None

    def test_diamond_pattern(self) -> None:
        """Diamond: 3 depends on 1 and 2. Not a cycle."""
        deps = {2: {1}, 3: {1, 2}}
        assert GlobalSheetScheduler._detect_cycle(deps) is None

    def test_wide_fan_in(self) -> None:
        """Many nodes feeding into one. No cycle."""
        deps = {10: {1, 2, 3, 4, 5, 6, 7, 8, 9}}
        assert GlobalSheetScheduler._detect_cycle(deps) is None


class TestDetectCycleCyclic:
    """Tests that cycles are correctly detected and reported."""

    def test_self_loop(self) -> None:
        """Node depends on itself."""
        deps = {1: {1}}
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        assert 1 in result

    def test_simple_two_node_cycle(self) -> None:
        """A→B→A cycle."""
        deps = {1: {2}, 2: {1}}
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        assert len(result) >= 3  # [1, 2, 1] or [2, 1, 2]
        # Cycle closes: first == last
        assert result[0] == result[-1]

    def test_three_node_cycle(self) -> None:
        """A→B→C→A cycle."""
        deps = {1: {3}, 2: {1}, 3: {2}}
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        assert result[0] == result[-1]  # cycle closes

    def test_cycle_with_non_cyclic_branch(self) -> None:
        """Graph has a cycle (2→3→2) plus a non-cyclic branch (4→1)."""
        deps = {2: {1}, 3: {2}, 2: {3}, 4: {1}}
        # Rebuild deps properly — dict keys must be unique
        deps = {3: {2}, 4: {1}}
        # Add cycle: 2 depends on 3 and 3 depends on 2
        deps[2] = {3}
        deps[3] = {2}
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        assert result[0] == result[-1]


class TestDetectCycleLargeGraphs:
    """Tests that the iterative implementation handles large graphs."""

    def test_large_linear_chain_no_cycle(self) -> None:
        """1500-node linear chain — must not cause RecursionError."""
        n = 1500
        deps: dict[int, set[int]] = {}
        for i in range(2, n + 1):
            deps[i] = {i - 1}
        # Should complete without RecursionError
        assert GlobalSheetScheduler._detect_cycle(deps) is None

    def test_large_chain_with_back_edge(self) -> None:
        """1500-node chain with a back edge creating a cycle."""
        n = 1500
        deps: dict[int, set[int]] = {}
        for i in range(2, n + 1):
            deps[i] = {i - 1}
        # Add back edge: node 1 depends on node n (cycle)
        deps[1] = {n}
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        assert result[0] == result[-1]

    def test_3000_node_wide_graph_no_cycle(self) -> None:
        """3000 nodes, star topology — all depend on node 1."""
        deps: dict[int, set[int]] = {}
        for i in range(2, 3001):
            deps[i] = {1}
        assert GlobalSheetScheduler._detect_cycle(deps) is None


class TestDetectCycleEdgeCases:
    """Edge cases and disconnected components."""

    def test_disconnected_components_no_cycle(self) -> None:
        """Two disconnected acyclic components."""
        deps = {
            2: {1},  # component 1: 1←2
            4: {3},  # component 2: 3←4
        }
        assert GlobalSheetScheduler._detect_cycle(deps) is None

    def test_disconnected_one_has_cycle(self) -> None:
        """Two components, one has a cycle."""
        deps = {
            2: {1},        # component 1: acyclic
            3: {4}, 4: {3},  # component 2: 3↔4 cycle
        }
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        assert result[0] == result[-1]

    def test_multiple_independent_cycles(self) -> None:
        """Two separate cycles in the graph."""
        deps = {
            1: {2}, 2: {1},   # cycle 1
            3: {4}, 4: {3},   # cycle 2
        }
        result = GlobalSheetScheduler._detect_cycle(deps)
        assert result is not None
        # Should find at least one cycle
        assert result[0] == result[-1]
