"""Tests for parallel dispatch fix — GH#167.

Three issues:
1. extract_dependencies ignores config.sheet.dependencies (forces linear chain)
2. Pacing blocks independent sheets after every completion
3. Dispatch logging insufficient for debugging

TDD: tests written before implementation. Red first, then green.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.dispatch import DispatchConfig, dispatch_ready
from marianne.daemon.baton.events import SheetAttemptResult, SheetSkipped
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    CircuitBreakerState,
    SheetExecutionState,
)

# =============================================================================
# Issue 1: extract_dependencies must respect config.sheet.dependencies
# =============================================================================


class TestExtractDependenciesRespectsYAML:
    """extract_dependencies must use config.sheet.dependencies when present."""

    def test_uses_yaml_dependencies_when_present(self) -> None:
        """When config.sheet.dependencies is set, use it instead of
        auto-generating a linear chain from stage ordering."""
        from marianne.daemon.baton.adapter import extract_dependencies

        mock_config = MagicMock()
        mock_config.sheet.total_sheets = 4
        # Each sheet is its own stage (no fan-out)
        mock_config.sheet.get_fan_out_metadata = lambda n: MagicMock(
            stage=n, instance=1, fan_count=1
        )
        # YAML dependencies: stages 1 and 3 are independent,
        # stage 2 depends on 1, stage 4 depends on 3
        mock_config.sheet.dependencies = {
            2: [1],
            4: [3],
        }

        deps = extract_dependencies(mock_config)

        # Stages 1 and 3 should have NO dependencies
        assert deps.get(1, []) == []
        assert deps.get(3, []) == []
        # Stage 2 depends on 1, stage 4 depends on 3
        assert deps.get(2, []) == [1]
        assert deps.get(4, []) == [3]

    def test_independent_stages_not_chained(self) -> None:
        """Stages with no dependency entries should NOT depend on previous
        stages — they should be independent."""
        from marianne.daemon.baton.adapter import extract_dependencies

        mock_config = MagicMock()
        mock_config.sheet.total_sheets = 14
        mock_config.sheet.get_fan_out_metadata = lambda n: MagicMock(
            stage=n, instance=1, fan_count=1
        )
        # A3-style DAG: 4 independent flagship chains
        mock_config.sheet.dependencies = {
            2: [1],
            3: [2],
            5: [4],
            6: [5],
            8: [7],
            9: [8],
            11: [10],
            12: [11],
            13: [3, 6, 9, 12],
            14: [13],
        }

        deps = extract_dependencies(mock_config)

        # Stages 1, 4, 7, 10 are independent — no deps
        assert deps.get(1, []) == []
        assert deps.get(4, []) == []
        assert deps.get(7, []) == []
        assert deps.get(10, []) == []
        # Stage 13 depends on all 4 validate stages
        assert sorted(deps.get(13, [])) == [3, 6, 9, 12]

    def test_falls_back_to_linear_when_no_dependencies(self) -> None:
        """When config.sheet.dependencies is empty/None, fall back to
        the existing linear stage chain behavior."""
        from marianne.daemon.baton.adapter import extract_dependencies

        mock_config = MagicMock()
        mock_config.sheet.total_sheets = 3
        mock_config.sheet.get_fan_out_metadata = lambda n: MagicMock(
            stage=n, instance=1, fan_count=1
        )
        # No dependencies specified — should use linear fallback
        mock_config.sheet.dependencies = {}

        deps = extract_dependencies(mock_config)

        # Linear chain: 1→2→3
        assert deps.get(1, []) == []
        assert 1 in deps.get(2, [])
        assert 2 in deps.get(3, [])

    def test_fan_out_with_yaml_dependencies(self) -> None:
        """Fan-out within a stage should work with YAML dependencies.
        Sheets within a fan-out stage are independent of each other,
        but the stage-level dependency is respected."""
        from marianne.daemon.baton.adapter import extract_dependencies

        mock_config = MagicMock()
        # Stage 1: 1 sheet. Stage 2: 3 fan-out sheets (2,3,4). Stage 3: 1 sheet (5).
        mock_config.sheet.total_sheets = 5

        def get_meta(n: int) -> MagicMock:
            if n == 1:
                return MagicMock(stage=1, instance=1, fan_count=1)
            elif n <= 4:
                return MagicMock(stage=2, instance=n - 1, fan_count=3)
            else:
                return MagicMock(stage=3, instance=1, fan_count=1)

        mock_config.sheet.get_fan_out_metadata = get_meta
        # Stage 2 depends on stage 1, stage 3 depends on stage 2
        mock_config.sheet.dependencies = {
            2: [1],
            3: [2],
        }

        deps = extract_dependencies(mock_config)

        # Sheet 1 (stage 1): no deps
        assert deps.get(1, []) == []
        # Sheets 2,3,4 (stage 2 fan-out): all depend on sheet 1
        for sn in (2, 3, 4):
            assert 1 in deps.get(sn, []), f"Sheet {sn} should depend on sheet 1"
        # Sheet 5 (stage 3): depends on all fan-out sheets (2,3,4)
        assert sorted(deps.get(5, [])) == [2, 3, 4]


# =============================================================================
# Issue 2: Pacing must not block independent sheets
# =============================================================================


class TestPacingIndependentSheets:
    """Pacing after a completion should not prevent independent
    (non-dependent) sheets from dispatching."""

    @pytest.mark.asyncio
    async def test_independent_sheets_dispatch_despite_pacing(self) -> None:
        """When sheet 1 completes and pacing activates, sheets 4, 7, 10
        (which have no dependency on sheet 1) should still be dispatchable."""
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
            4: SheetExecutionState(sheet_num=4, instrument_name="claude-code"),
        }
        # Two independent chains: 1→2 and 3→4
        deps = {1: [], 2: [1], 3: [], 4: [3]}
        baton.register_job("j1", sheets, deps, pacing_seconds=2.0)

        # Dispatch initial — should get sheets 1 and 3 (both independent)
        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)
        assert result.dispatched_count == 2

        # Complete sheet 1 — this triggers pacing
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=1,
                validations_total=1,
                validation_pass_rate=100.0,
            )
        )

        # Sheet 2 (depends on 1) should be ready.
        # Pacing is active, but sheet 2's dependency just completed.
        # The current bug: pacing blocks ALL sheets including sheet 2.
        # The fix: pacing should not block sheets whose dependencies
        # just completed (or better: don't pace when other sheets are
        # still dispatched).
        ready = baton.get_ready_sheets("j1")
        # Sheet 2 should be ready (dep 1 completed)
        # Sheet 3 is still dispatched, sheet 4 depends on 3
        ready_nums = [s.sheet_num for s in ready]
        assert 2 in ready_nums, (
            "Sheet 2 should be ready after its dependency (1) completed, even with pacing active"
        )


class TestPacingSkipsWhenOtherSheetsDispatched:
    """Pacing should not activate when other sheets are still running."""

    @pytest.mark.asyncio
    async def test_no_pacing_when_sheets_still_dispatched(self) -> None:
        """If other sheets are still in DISPATCHED status when a sheet
        completes, pacing should NOT activate — the parallel wave should
        continue uninterrupted."""
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="gemini-cli"),
        }
        # Both independent
        deps = {1: [], 2: []}
        baton.register_job("j1", sheets, deps, pacing_seconds=2.0)

        # Dispatch both
        callback = AsyncMock()
        config = DispatchConfig(max_concurrent_sheets=10)
        await dispatch_ready(baton, config, callback)

        # Complete sheet 1 while sheet 2 is still dispatched
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=1,
                validations_total=1,
                validation_pass_rate=100.0,
            )
        )

        job = baton._jobs["j1"]
        # Pacing should NOT be active because sheet 2 is still dispatched
        assert not job.pacing_active, (
            "Pacing should not activate when other sheets are still dispatched"
        )


# =============================================================================
# Issue 3: Dispatch logging
# =============================================================================


class TestDispatchLogging:
    """Dispatch decisions should be logged with enough detail to diagnose
    parallelization issues."""

    @pytest.mark.asyncio
    async def test_dispatch_logs_ready_count_and_skips(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """dispatch_ready should log how many sheets were ready, how many
        dispatched, and why any were skipped."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=1)

        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                model="opus",
            ),
            2: SheetExecutionState(
                sheet_num=2,
                instrument_name="claude-code",
                model="opus",
            ),
        }
        baton.register_job("j1", sheets, {1: [], 2: []})

        callback = AsyncMock()
        config = baton.build_dispatch_config(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)

        # Should have dispatched 1, skipped 1 (instrument concurrency)
        assert result.dispatched_count == 1
        assert sum(result.skipped_reasons.values()) >= 1

        # Structlog writes to stdout — check captured output
        captured = capsys.readouterr()
        assert "ready" in captured.out.lower() or "dispatch" in captured.out.lower(), (
            "Dispatch should log ready sheet information"
        )

    @pytest.mark.asyncio
    async def test_dispatch_logs_skip_reasons(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When sheets are skipped due to concurrency limits, the log
        should include the specific reason and model key."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=1)
        baton.set_model_concurrency("claude-code", "claude-opus-4-6", 1)

        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                model="claude-opus-4-6",
            ),
            2: SheetExecutionState(
                sheet_num=2,
                instrument_name="claude-code",
                model="claude-opus-4-6",
            ),
        }
        baton.register_job("j1", sheets, {1: [], 2: []})

        callback = AsyncMock()
        config = baton.build_dispatch_config(max_concurrent_sheets=10)
        await dispatch_ready(baton, config, callback)

        # Structlog writes to stdout — check for model_concurrency skip log
        captured = capsys.readouterr()
        assert "model_concurrency" in captured.out, (
            "Dispatch should log why sheets were skipped (model concurrency)"
        )

    @pytest.mark.asyncio
    async def test_model_concurrency_skip_records_dispatch_wait_state(self) -> None:
        """A ready sheet held by model capacity is visible in checkpoint state."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=2)
        baton.set_model_concurrency("claude-code", "glm-5-Turbo", 1)

        sheets = {
            1: SheetExecutionState(
                sheet_num=1,
                instrument_name="claude-code",
                model="glm-5-Turbo",
            ),
            2: SheetExecutionState(
                sheet_num=2,
                instrument_name="claude-code",
                model="glm-5-Turbo",
            ),
        }
        baton.register_job("j1", sheets, {1: [], 2: []})

        callback = AsyncMock()
        config = baton.build_dispatch_config(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)

        assert result.dispatched_sheets == [("j1", 1)]
        assert result.blocked_sheets == [("j1", 2)]
        assert sheets[2].dispatch_blocked_reason == "model_concurrency"
        assert sheets[2].dispatch_blocked_details == {
            "instrument": "claude-code",
            "model_key": "claude-code:glm-5-Turbo",
            "model_count": 1,
            "model_limit": 1,
        }
        assert sheets[2].dispatch_blocked_at is not None

        await baton.handle_event(
            SheetAttemptResult(
                job_id="j1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=1,
                execution_success=True,
                validations_passed=1,
                validations_total=1,
                validation_pass_rate=100.0,
            )
        )
        result = await dispatch_ready(baton, config, callback)

        assert result.dispatched_sheets == [("j1", 2)]
        assert sheets[2].dispatch_blocked_reason is None
        assert sheets[2].dispatch_blocked_details == {}
        assert sheets[2].dispatch_blocked_at is None

    @pytest.mark.asyncio
    async def test_fallback_instrument_model_capacity_is_checked(self) -> None:
        """Fallback dispatch must honor the fallback model's concurrency limit."""
        baton = BatonCore()
        baton.register_instrument("primary", max_concurrent=4)
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.set_model_concurrency("claude-code", "glm-5-Turbo", 0)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="primary",
            fallback_chain=["claude-code"],
            fallback_configs=[{"model": "glm-5-Turbo"}],
        )
        baton.register_job("j1", {1: sheet}, {1: []})
        baton._instruments["primary"].circuit_breaker = CircuitBreakerState.OPEN

        callback = AsyncMock()
        config = baton.build_dispatch_config(max_concurrent_sheets=10)
        result = await dispatch_ready(baton, config, callback)

        assert result.dispatched_sheets == []
        assert result.blocked_sheets == [("j1", 1)]
        assert sheet.instrument_name == "claude-code"
        assert sheet.model == "glm-5-Turbo"
        assert sheet.dispatch_blocked_reason == "model_concurrency"
        assert sheet.dispatch_blocked_details["model_key"] == (
            "claude-code:glm-5-Turbo"
        )
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_clears_dispatch_wait_state(self) -> None:
        """Terminal skipped sheets must not keep stale dispatch-wait labels."""
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=1)
        baton.set_model_concurrency("claude-code", "glm-5-Turbo", 0)

        sheet = SheetExecutionState(
            sheet_num=1,
            instrument_name="claude-code",
            model="glm-5-Turbo",
        )
        baton.register_job("j1", {1: sheet}, {1: []})

        result = await dispatch_ready(
            baton,
            baton.build_dispatch_config(max_concurrent_sheets=10),
            AsyncMock(),
        )
        assert result.blocked_sheets == [("j1", 1)]
        assert sheet.dispatch_blocked_reason == "model_concurrency"

        await baton.handle_event(
            SheetSkipped(job_id="j1", sheet_num=1, reason="upstream_failed")
        )

        assert sheet.status == BatonSheetStatus.SKIPPED
        assert sheet.dispatch_blocked_reason is None
        assert sheet.dispatch_blocked_details == {}
        assert sheet.dispatch_blocked_at is None
