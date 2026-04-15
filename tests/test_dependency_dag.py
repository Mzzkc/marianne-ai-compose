"""Tests for Sheet Dependency DAG (v17 evolution).

Comprehensive tests for the DependencyDAG class including:
- DAG construction and validation
- Cycle detection
- Topological sort
- Parallel group identification
- Invalid dependency handling
- Integration with config schema
"""

import pytest

from marianne.execution.dag import (
    CycleDetectedError,
    DependencyDAG,
    InvalidDependencyError,
    build_dag_from_config,
)

# =============================================================================
# DAG Construction Tests
# =============================================================================


class TestDependencyDAGConstruction:
    """Tests for DAG construction from dependencies."""

    def test_empty_dependencies(self) -> None:
        """DAG with no dependencies creates valid structure."""
        dag = DependencyDAG.from_dependencies(total_sheets=5, dependencies=None)

        assert dag.total_sheets == 5
        assert dag.validated is True
        assert not dag.has_dependencies()

    def test_empty_dict_dependencies(self) -> None:
        """DAG with empty dict creates valid structure."""
        dag = DependencyDAG.from_dependencies(total_sheets=3, dependencies={})

        assert dag.total_sheets == 3
        assert dag.validated is True
        assert not dag.has_dependencies()

    def test_simple_linear_chain(self) -> None:
        """DAG with linear chain: 1 -> 2 -> 3."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )

        assert dag.validated is True
        assert dag.has_dependencies()
        assert dag.get_dependencies(1) == []
        assert dag.get_dependencies(2) == [1]
        assert dag.get_dependencies(3) == [2]

    def test_diamond_pattern(self) -> None:
        """DAG with diamond pattern: 1 -> 2,3 -> 4."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        assert dag.validated is True
        assert dag.get_dependencies(4) == [2, 3]
        assert dag.get_dependents(1) == [2, 3]
        assert dag.get_dependents(2) == [4]
        assert dag.get_dependents(3) == [4]

    def test_multiple_roots(self) -> None:
        """DAG with multiple root nodes (sheets with no dependencies)."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=5,
            dependencies={3: [1], 4: [2], 5: [3, 4]},
        )

        assert dag.validated is True
        # Sheets 1 and 2 have no dependencies (roots)
        assert dag.get_dependencies(1) == []
        assert dag.get_dependencies(2) == []
        # Sheet 5 depends on both 3 and 4
        assert dag.get_dependencies(5) == [3, 4]

    def test_sparse_dependencies(self) -> None:
        """DAG with some sheets having no dependencies."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=10,
            dependencies={5: [1, 2], 10: [5]},
        )

        assert dag.validated is True
        # Only sheets 5 and 10 have dependencies
        assert dag.has_dependencies()
        assert dag.get_dependencies(3) == []
        assert dag.get_dependencies(5) == [1, 2]

    def test_build_dag_from_config_helper(self) -> None:
        """Test the convenience helper function."""
        dag = build_dag_from_config(
            total_sheets=4,
            sheet_dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        assert dag.validated is True
        assert dag.total_sheets == 4
        assert dag.has_dependencies()


# =============================================================================
# Cycle Detection Tests
# =============================================================================


class TestCycleDetection:
    """Tests for cycle detection during DAG construction."""

    def test_direct_cycle(self) -> None:
        """Direct cycle (1 -> 2 -> 1) should raise error."""
        with pytest.raises(CycleDetectedError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=3,
                dependencies={1: [2], 2: [1]},
            )

        assert exc_info.value.cycle is not None
        assert "Circular dependency" in str(exc_info.value)

    def test_indirect_cycle(self) -> None:
        """Indirect cycle (1 -> 2 -> 3 -> 1) should raise error."""
        with pytest.raises(CycleDetectedError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=4,
                dependencies={1: [3], 2: [1], 3: [2]},
            )

        assert exc_info.value.cycle is not None
        assert len(exc_info.value.cycle) >= 3

    def test_self_dependency_rejected(self) -> None:
        """Self-dependency (3 -> 3) should raise error during construction."""
        with pytest.raises(InvalidDependencyError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=5,
                dependencies={3: [3]},
            )

        assert exc_info.value.sheet_num == 3
        assert exc_info.value.invalid_dep == 3
        assert "cannot depend on itself" in str(exc_info.value)

    def test_complex_cycle(self) -> None:
        """Complex cycle in a larger graph."""
        # 1 -> 2 -> 3 -> 4 -> 2 (cycle 2 -> 3 -> 4 -> 2)
        with pytest.raises(CycleDetectedError):
            DependencyDAG.from_dependencies(
                total_sheets=5,
                dependencies={2: [1], 3: [2], 4: [3], 2: [4]},  # noqa: F601
            )

    def test_no_cycle_complex_graph(self) -> None:
        """Complex graph without cycles should validate."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=6,
            dependencies={
                2: [1],
                3: [1],
                4: [2, 3],
                5: [2],
                6: [4, 5],
            },
        )

        assert dag.validated is True


# =============================================================================
# Invalid Dependency Tests
# =============================================================================


class TestInvalidDependencies:
    """Tests for invalid dependency detection."""

    def test_dependency_out_of_range_high(self) -> None:
        """Dependency referencing non-existent sheet (too high)."""
        with pytest.raises(InvalidDependencyError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=3,
                dependencies={2: [5]},  # Sheet 5 doesn't exist
            )

        assert exc_info.value.sheet_num == 2
        assert exc_info.value.invalid_dep == 5
        assert "out of range" in str(exc_info.value)

    def test_dependency_out_of_range_zero(self) -> None:
        """Dependency referencing sheet 0 (invalid)."""
        with pytest.raises(InvalidDependencyError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=3,
                dependencies={2: [0]},
            )

        assert exc_info.value.invalid_dep == 0

    def test_dependency_out_of_range_negative(self) -> None:
        """Dependency referencing negative sheet number."""
        with pytest.raises(InvalidDependencyError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=3,
                dependencies={2: [-1]},
            )

        assert exc_info.value.invalid_dep == -1

    def test_sheet_num_out_of_range(self) -> None:
        """Sheet number in dependencies out of range."""
        with pytest.raises(InvalidDependencyError) as exc_info:
            DependencyDAG.from_dependencies(
                total_sheets=3,
                dependencies={10: [1]},  # Sheet 10 doesn't exist
            )

        assert exc_info.value.sheet_num == 10


# =============================================================================
# Topological Sort Tests
# =============================================================================


class TestTopologicalSort:
    """Tests for get_execution_order (topological sort)."""

    def test_empty_dag_order(self) -> None:
        """Empty DAG returns sheets in natural order."""
        dag = DependencyDAG.from_dependencies(total_sheets=5, dependencies=None)

        order = dag.get_execution_order()

        assert order == [1, 2, 3, 4, 5]

    def test_linear_chain_order(self) -> None:
        """Linear chain maintains dependency order."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [2], 4: [3]},
        )

        order = dag.get_execution_order()

        assert order == [1, 2, 3, 4]

    def test_diamond_pattern_order(self) -> None:
        """Diamond pattern produces valid order."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        order = dag.get_execution_order()

        # Must satisfy: 1 before 2, 1 before 3, 2&3 before 4
        assert order.index(1) < order.index(2)
        assert order.index(1) < order.index(3)
        assert order.index(2) < order.index(4)
        assert order.index(3) < order.index(4)
        # Deterministic: prefer lower sheet numbers
        assert order == [1, 2, 3, 4]

    def test_reverse_dependency_order(self) -> None:
        """Dependencies declared in reverse order still produces valid execution."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={1: [], 2: [1], 3: [1, 2]},
        )

        order = dag.get_execution_order()

        assert order == [1, 2, 3]

    def test_complex_dag_order(self) -> None:
        """Complex DAG produces valid topological order."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=7,
            dependencies={
                2: [1],
                3: [1],
                4: [2],
                5: [2, 3],
                6: [3],
                7: [4, 5, 6],
            },
        )

        order = dag.get_execution_order()

        # Verify all dependencies satisfied
        for sheet_num, deps in dag.reverse_edges.items():
            for dep in deps:
                assert order.index(dep) < order.index(sheet_num), (
                    f"Sheet {dep} should come before {sheet_num}"
                )


# =============================================================================
# Parallel Groups Tests
# =============================================================================


class TestParallelGroups:
    """Tests for get_parallel_groups."""

    def test_empty_dag_groups(self) -> None:
        """Empty DAG has all sheets in one parallel group."""
        dag = DependencyDAG.from_dependencies(total_sheets=4, dependencies=None)

        groups = dag.get_parallel_groups()

        # All sheets can run in parallel (no dependencies)
        assert groups == [[1, 2, 3, 4]]

    def test_linear_chain_groups(self) -> None:
        """Linear chain has one sheet per group (no parallelism)."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )

        groups = dag.get_parallel_groups()

        assert groups == [[1], [2], [3]]
        assert not dag.is_parallelizable()

    def test_diamond_pattern_groups(self) -> None:
        """Diamond pattern allows parallel execution of middle layer."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        groups = dag.get_parallel_groups()

        assert groups == [[1], [2, 3], [4]]
        assert dag.is_parallelizable()

    def test_wide_parallel_group(self) -> None:
        """Wide graph creates large parallel group."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=6,
            dependencies={6: [1, 2, 3, 4, 5]},
        )

        groups = dag.get_parallel_groups()

        assert groups == [[1, 2, 3, 4, 5], [6]]
        assert dag.is_parallelizable()

    def test_multiple_roots_groups(self) -> None:
        """Multiple root nodes form first parallel group."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=5,
            dependencies={3: [1], 4: [2], 5: [3, 4]},
        )

        groups = dag.get_parallel_groups()

        # 1 and 2 can run in parallel (roots)
        # 3 and 4 can run in parallel (after 1,2)
        # 5 runs last
        assert groups == [[1, 2], [3, 4], [5]]


# =============================================================================
# Ready Sheets Tests
# =============================================================================


class TestReadySheets:
    """Tests for get_ready_sheets (runtime dependency checking)."""

    def test_empty_completed_set(self) -> None:
        """No completed sheets returns all root sheets."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        ready = dag.get_ready_sheets(completed=set())

        # Only sheet 1 has no dependencies
        assert ready == [1]

    def test_after_root_completes(self) -> None:
        """After root completes, dependents become ready."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        ready = dag.get_ready_sheets(completed={1})

        # After 1 completes, 2 and 3 are ready
        assert ready == [2, 3]

    def test_partial_dependencies_satisfied(self) -> None:
        """Sheet with multiple deps only ready when all complete."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        # Only 2 completed, 3 still pending
        ready = dag.get_ready_sheets(completed={1, 2})

        # 3 is still ready (was ready before), 4 needs both 2 AND 3
        assert ready == [3]

    def test_all_dependencies_satisfied(self) -> None:
        """When all deps satisfied, sheet becomes ready."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        ready = dag.get_ready_sheets(completed={1, 2, 3})

        # Now 4 is ready
        assert ready == [4]

    def test_all_completed_empty_result(self) -> None:
        """When all sheets complete, no sheets are ready."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1], 3: [2]},
        )

        ready = dag.get_ready_sheets(completed={1, 2, 3})

        assert ready == []

    def test_no_dependencies_all_ready(self) -> None:
        """DAG with no dependencies has all sheets ready initially."""
        dag = DependencyDAG.from_dependencies(total_sheets=5, dependencies=None)

        ready = dag.get_ready_sheets(completed=set())

        assert ready == [1, 2, 3, 4, 5]

    def test_independent_and_dependent_sheets_mixed(self) -> None:
        """Independent sheets stay ready even when dependent ones are blocked."""
        # Sheet 2 depends on 1, but 3 has no deps
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={2: [1]},
        )

        ready = dag.get_ready_sheets(completed=set())

        # Sheets 1 and 3 are ready (no deps); sheet 2 is blocked on 1
        assert ready == [1, 3]

    def test_completed_set_larger_than_total(self) -> None:
        """Superset of completed sheets doesn't break ready calculation."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={3: [1, 2]},
        )

        # Pass extra sheet numbers that don't exist in the DAG
        ready = dag.get_ready_sheets(completed={1, 2, 3, 99})

        assert ready == []

    def test_progressive_readiness_complex_dag(self) -> None:
        """Track progressive readiness through a complex DAG step by step."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=6,
            dependencies={
                2: [1],
                3: [1],
                4: [2, 3],
                5: [2],
                6: [4, 5],
            },
        )

        # Step 0: nothing completed
        assert dag.get_ready_sheets(set()) == [1]

        # Step 1: 1 done → 2, 3 unblocked
        assert dag.get_ready_sheets({1}) == [2, 3]

        # Step 2: 1,2 done → 3 still ready, 5 now ready (needs only 2)
        assert dag.get_ready_sheets({1, 2}) == [3, 5]

        # Step 3: 1,2,3 done → 4 ready (needs 2,3), 5 still ready
        assert dag.get_ready_sheets({1, 2, 3}) == [4, 5]

        # Step 4: 1,2,3,4,5 done → 6 ready
        assert dag.get_ready_sheets({1, 2, 3, 4, 5}) == [6]

    def test_single_sheet_ready_then_done(self) -> None:
        """Single-sheet DAG: ready when empty, done when completed."""
        dag = DependencyDAG.from_dependencies(total_sheets=1, dependencies=None)

        assert dag.get_ready_sheets(set()) == [1]
        assert dag.get_ready_sheets({1}) == []


# =============================================================================
# Serialization Tests
# =============================================================================


class TestSerialization:
    """Tests for DAG serialization."""

    def test_to_dict_basic(self) -> None:
        """Basic DAG serializes correctly."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        data = dag.to_dict()

        assert data["total_sheets"] == 4
        assert data["dependencies"] == {2: [1], 3: [1], 4: [2, 3]}
        assert data["execution_order"] == [1, 2, 3, 4]
        assert data["parallel_groups"] == [[1], [2, 3], [4]]
        assert data["parallelizable"] is True

    def test_to_dict_empty(self) -> None:
        """Empty DAG serializes correctly."""
        dag = DependencyDAG.from_dependencies(total_sheets=3, dependencies=None)

        data = dag.to_dict()

        assert data["total_sheets"] == 3
        assert data["dependencies"] == {}
        assert data["execution_order"] == [1, 2, 3]
        # Empty DAG is parallelizable - all 3 sheets can run concurrently
        assert data["parallelizable"] is True
        assert data["parallel_groups"] == [[1, 2, 3]]

    def test_str_representation(self) -> None:
        """String representation is human-readable."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=4,
            dependencies={2: [1], 3: [1], 4: [2, 3]},
        )

        str_repr = str(dag)

        assert "DependencyDAG" in str_repr
        assert "4 sheets" in str_repr
        assert "depends on" in str_repr
        assert "Parallel groups" in str_repr


# =============================================================================
# Config Integration Tests
# =============================================================================


class TestConfigIntegration:
    """Tests for integration with JobConfig schema."""

    def test_sheet_config_accepts_dependencies(self) -> None:
        """SheetConfig accepts valid dependencies."""
        from marianne.core.config import SheetConfig

        config = SheetConfig(
            size=1,
            total_items=5,
            dependencies={3: [1, 2], 4: [3], 5: [3, 4]},
        )

        assert config.dependencies == {3: [1, 2], 4: [3], 5: [3, 4]}
        assert config.total_sheets == 5

    def test_sheet_config_empty_dependencies(self) -> None:
        """SheetConfig with no dependencies uses empty dict."""
        from marianne.core.config import SheetConfig

        config = SheetConfig(size=10, total_items=100)

        assert config.dependencies == {}

    def test_sheet_config_rejects_self_dependency(self) -> None:
        """SheetConfig rejects self-dependency at validation time."""
        from pydantic import ValidationError

        from marianne.core.config import SheetConfig

        with pytest.raises(ValidationError) as exc_info:
            SheetConfig(
                size=1,
                total_items=5,
                dependencies={3: [3]},
            )

        assert "cannot depend on itself" in str(exc_info.value)

    def test_sheet_config_rejects_invalid_dep_type(self) -> None:
        """SheetConfig rejects non-integer dependencies."""
        from pydantic import ValidationError

        from marianne.core.config import SheetConfig

        with pytest.raises(ValidationError):
            SheetConfig(
                size=1,
                total_items=5,
                dependencies={2: ["one"]},  # type: ignore
            )

    def test_job_config_with_dependencies(self) -> None:
        """Full JobConfig with dependencies parses correctly."""
        from marianne.core.config import JobConfig

        config = JobConfig.from_yaml_string("""
name: test-job
sheet:
  size: 1
  total_items: 4
  dependencies:
    2: [1]
    3: [1]
    4: [2, 3]
prompt:
  template: "Process sheet {{ sheet_num }}"
""")

        assert config.sheet.dependencies == {2: [1], 3: [1], 4: [2, 3]}


# =============================================================================
# Runner Integration Tests
# =============================================================================


# TestRunnerIntegration removed — tested JobRunner DAG integration
# which no longer exists (runner package deleted).


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_single_sheet_dag(self) -> None:
        """DAG with single sheet works correctly."""
        dag = DependencyDAG.from_dependencies(total_sheets=1, dependencies=None)

        assert dag.get_execution_order() == [1]
        assert dag.get_parallel_groups() == [[1]]
        assert dag.get_ready_sheets(set()) == [1]
        assert not dag.is_parallelizable()

    def test_single_sheet_with_empty_deps(self) -> None:
        """Single sheet with explicit empty deps list."""
        dag = DependencyDAG.from_dependencies(
            total_sheets=1,
            dependencies={1: []},
        )

        assert dag.get_execution_order() == [1]

    def test_large_dag_performance(self) -> None:
        """Large DAG operations complete in reasonable time."""
        import time

        # Build a wide DAG: sheets 2-99 depend on 1, sheet 100 depends on all
        deps = {i: [1] for i in range(2, 100)}
        deps[100] = list(range(2, 100))

        start = time.monotonic()
        dag = DependencyDAG.from_dependencies(total_sheets=100, dependencies=deps)
        build_time = time.monotonic() - start

        start = time.monotonic()
        _ = dag.get_execution_order()
        order_time = time.monotonic() - start

        start = time.monotonic()
        _ = dag.get_parallel_groups()
        groups_time = time.monotonic() - start

        # All operations should complete in < 100ms for 100 sheets
        assert build_time < 0.1, f"Build took {build_time:.3f}s"
        assert order_time < 0.1, f"Order took {order_time:.3f}s"
        assert groups_time < 0.1, f"Groups took {groups_time:.3f}s"

    def test_dependency_on_last_sheet(self) -> None:
        """First sheet depending on last creates valid linear chain."""
        # This tests reverse ordering in dependencies
        dag = DependencyDAG.from_dependencies(
            total_sheets=3,
            dependencies={3: [2], 2: [1]},
        )

        order = dag.get_execution_order()
        assert order == [1, 2, 3]

    def test_many_dependents_on_single_sheet(self) -> None:
        """Single sheet with many dependents."""
        deps = {i: [1] for i in range(2, 51)}
        dag = DependencyDAG.from_dependencies(total_sheets=50, dependencies=deps)

        groups = dag.get_parallel_groups()
        # First group: [1], second group: [2,3,...,50]
        assert groups[0] == [1]
        assert groups[1] == list(range(2, 51))
