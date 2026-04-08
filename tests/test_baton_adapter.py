"""Tests for the BatonAdapter — wiring the baton into the conductor.

Step 28: Wire baton into conductor (replace monolithic execution).
These tests cover:
- State synchronization (BatonSheetStatus ↔ CheckpointState)
- Job submission → baton registration
- Dispatch callback → backend acquisition → musician spawning
- EventBus integration (baton events → ObserverEvents)
- Feature flag (use_baton: true/false)
- Job completion detection and cleanup

TDD: Tests written first (red), then implementation (green).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import (
    SheetAttemptResult,
    SheetSkipped,
)
from marianne.daemon.baton.state import (
    BatonSheetStatus,
    SheetExecutionState,
)

# =========================================================================
# Fixtures
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    workspace: str = "/tmp/test-ws",
    prompt: str = "test prompt",
    timeout: float = 60.0,
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=Path(workspace),
        instrument_name=instrument,
        prompt_template=prompt,
        timeout_seconds=timeout,
    )


def _make_execution_state(
    sheet_num: int = 1,
    instrument: str = "claude-code",
    max_retries: int = 3,
) -> SheetExecutionState:
    """Create a SheetExecutionState for testing."""
    return SheetExecutionState(
        sheet_num=sheet_num,
        instrument_name=instrument,
        max_retries=max_retries,
    )


# =========================================================================
# BatonAdapter import and construction
# =========================================================================


class TestBatonAdapterConstruction:
    """Test that BatonAdapter can be constructed with required dependencies."""

    def test_import(self) -> None:
        """BatonAdapter is importable from the baton package."""
        from marianne.daemon.baton.adapter import BatonAdapter

        assert BatonAdapter is not None

    def test_construction_minimal(self) -> None:
        """BatonAdapter can be constructed with minimal args."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        assert adapter is not None
        assert adapter.baton is not None
        assert not adapter.is_running

    def test_construction_with_event_bus(self) -> None:
        """BatonAdapter accepts an optional EventBus."""
        from marianne.daemon.baton.adapter import BatonAdapter

        mock_bus = MagicMock()
        adapter = BatonAdapter(event_bus=mock_bus)
        assert adapter._event_bus is mock_bus


# =========================================================================
# State synchronization — Surface 4
# =========================================================================


class TestStateSynchronization:
    """BatonSheetStatus → CheckpointState mapping.

    The baton tracks more states than CheckpointState. This mapping
    must be correct because state corruption is the worst outcome.
    """

    def test_status_mapping_completed(self) -> None:
        """COMPLETED maps to SheetStatus.COMPLETED."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.COMPLETED)
        assert result == "completed"

    def test_status_mapping_failed(self) -> None:
        """FAILED maps to SheetStatus.FAILED."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.FAILED)
        assert result == "failed"

    def test_status_mapping_skipped(self) -> None:
        """SKIPPED maps to SheetStatus.SKIPPED."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.SKIPPED)
        assert result == "skipped"

    def test_status_mapping_pending(self) -> None:
        """PENDING maps to SheetStatus.PENDING."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.PENDING)
        assert result == "pending"

    def test_status_mapping_dispatched(self) -> None:
        """DISPATCHED maps to dispatched (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.DISPATCHED)
        assert result == "dispatched"

    def test_status_mapping_running(self) -> None:
        """RUNNING maps to in_progress."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.IN_PROGRESS)
        assert result == "in_progress"

    def test_status_mapping_waiting(self) -> None:
        """WAITING maps to waiting (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.WAITING)
        assert result == "waiting"

    def test_status_mapping_retry_scheduled(self) -> None:
        """RETRY_SCHEDULED maps to retry_scheduled (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.RETRY_SCHEDULED)
        assert result == "retry_scheduled"

    def test_status_mapping_fermata(self) -> None:
        """FERMATA maps to fermata (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.FERMATA)
        assert result == "fermata"

    def test_status_mapping_cancelled(self) -> None:
        """CANCELLED maps to cancelled (11-state unified model)."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        result = baton_to_checkpoint_status(BatonSheetStatus.CANCELLED)
        assert result == "cancelled"

    def test_all_baton_statuses_mapped(self) -> None:
        """Every BatonSheetStatus value has a mapping."""
        from marianne.daemon.baton.adapter import baton_to_checkpoint_status

        for status in BatonSheetStatus:
            result = baton_to_checkpoint_status(status)
            assert isinstance(result, str), f"{status} not mapped"

    def test_checkpoint_to_baton_completed(self) -> None:
        """CheckpointState completed → BatonSheetStatus.COMPLETED."""
        from marianne.daemon.baton.adapter import checkpoint_to_baton_status

        result = checkpoint_to_baton_status("completed")
        assert result == BatonSheetStatus.COMPLETED

    def test_checkpoint_to_baton_failed(self) -> None:
        """CheckpointState failed → BatonSheetStatus.FAILED."""
        from marianne.daemon.baton.adapter import checkpoint_to_baton_status

        result = checkpoint_to_baton_status("failed")
        assert result == BatonSheetStatus.FAILED

    def test_checkpoint_to_baton_pending(self) -> None:
        """CheckpointState pending → BatonSheetStatus.PENDING."""
        from marianne.daemon.baton.adapter import checkpoint_to_baton_status

        result = checkpoint_to_baton_status("pending")
        assert result == BatonSheetStatus.PENDING

    def test_checkpoint_to_baton_in_progress(self) -> None:
        """CheckpointState in_progress → BatonSheetStatus.DISPATCHED."""
        from marianne.daemon.baton.adapter import checkpoint_to_baton_status

        result = checkpoint_to_baton_status("in_progress")
        assert result == BatonSheetStatus.DISPATCHED

    def test_checkpoint_to_baton_skipped(self) -> None:
        """CheckpointState skipped → BatonSheetStatus.SKIPPED."""
        from marianne.daemon.baton.adapter import checkpoint_to_baton_status

        result = checkpoint_to_baton_status("skipped")
        assert result == BatonSheetStatus.SKIPPED


# =========================================================================
# Job registration — Surface 1
# =========================================================================


class TestJobRegistration:
    """Job submission → baton registration via BatonAdapter."""

    def test_register_job_creates_sheet_states(self) -> None:
        """register_job converts Sheet list to SheetExecutionState dict."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2), _make_sheet(num=3)]

        adapter.register_job("test-job", sheets, dependencies={})

        assert adapter.baton.job_count == 1
        state1 = adapter.baton.get_sheet_state("test-job", 1)
        assert state1 is not None
        assert state1.instrument_name == "claude-code"
        state2 = adapter.baton.get_sheet_state("test-job", 2)
        assert state2 is not None

    def test_register_job_with_dependencies(self) -> None:
        """Dependencies are passed through to the baton."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        deps = {2: [1]}  # sheet 2 depends on sheet 1

        adapter.register_job("test-job", sheets, dependencies=deps)

        # Sheet 1 should be ready (no deps), sheet 2 should not
        ready = adapter.baton.get_ready_sheets("test-job")
        ready_nums = [s.sheet_num for s in ready]
        assert 1 in ready_nums
        assert 2 not in ready_nums

    def test_register_job_with_cost_limits(self) -> None:
        """Job-level cost limits are set on the baton."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]

        adapter.register_job(
            "test-job", sheets, dependencies={}, max_cost_usd=10.0
        )

        # Verify cost limit was set (internal state)
        assert "test-job" in adapter.baton._job_cost_limits
        assert adapter.baton._job_cost_limits["test-job"] == 10.0

    def test_register_job_stores_sheets(self) -> None:
        """Adapter stores the Sheet objects for prompt rendering."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, prompt="my prompt")]

        adapter.register_job("test-job", sheets, dependencies={})

        stored = adapter.get_sheet("test-job", 1)
        assert stored is not None
        assert stored.prompt_template == "my prompt"

    def test_register_job_with_retry_config(self) -> None:
        """Retry config values are propagated to SheetExecutionState."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]

        adapter.register_job(
            "test-job",
            sheets,
            dependencies={},
            max_retries=5,
            max_completion=10,
        )

        state = adapter.baton.get_sheet_state("test-job", 1)
        assert state is not None
        assert state.max_retries == 5
        assert state.max_completion == 10


# =========================================================================
# Dispatch callback — Surface 2
# =========================================================================


class TestDispatchCallback:
    """The dispatch callback bridges baton decisions to backend execution."""

    @pytest.mark.asyncio
    async def test_dispatch_callback_spawns_musician(self) -> None:
        """Dispatch callback creates an asyncio task for the musician."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})

        # Mock the backend pool
        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=MagicMock(
                success=True,
                exit_code=0,
                stdout="output",
                stderr="",
                rate_limited=False,
                duration_seconds=1.0,
                input_tokens=100,
                output_tokens=50,
                model="test-model",
                error_message=None,
            )
        )
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(return_value=mock_backend)
        adapter._backend_pool.release = AsyncMock()

        state = _make_execution_state(sheet_num=1)
        await adapter._dispatch_callback("test-job", 1, state)

        # The task should have been created
        assert len(adapter._active_tasks) > 0

    @pytest.mark.asyncio
    async def test_dispatch_releases_backend_on_completion(self) -> None:
        """Backend is released after musician finishes (success or failure)."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        adapter.register_job("test-job", sheets, dependencies={})

        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=MagicMock(
                success=True,
                exit_code=0,
                stdout="done",
                stderr="",
                rate_limited=False,
                duration_seconds=0.5,
                input_tokens=10,
                output_tokens=5,
                model="test",
                error_message=None,
            )
        )
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(return_value=mock_backend)
        adapter._backend_pool.release = AsyncMock()

        state = _make_execution_state(sheet_num=1)
        await adapter._dispatch_callback("test-job", 1, state)

        # Wait for task to complete
        if adapter._active_tasks:
            await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

        adapter._backend_pool.release.assert_called_once()


# =========================================================================
# EventBus integration — Surface 5
# =========================================================================


class TestEventBusIntegration:
    """Baton events fire equivalent EventBus events."""

    def test_observer_event_from_attempt_result(self) -> None:
        """SheetAttemptResult produces a sheet.completed observer event."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            validation_pass_rate=100.0,
            cost_usd=0.05,
        )

        event = attempt_result_to_observer_event(result)
        assert event["job_id"] == "j1"
        assert event["sheet_num"] == 1
        assert event["event"] == "sheet.completed"

    def test_observer_event_from_failed_result(self) -> None:
        """Failed SheetAttemptResult produces sheet.failed event."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=2,
            instrument_name="claude-code",
            attempt=3,
            execution_success=False,
            error_classification="TRANSIENT",
        )

        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.failed"

    def test_observer_event_from_rate_limited_result(self) -> None:
        """Rate-limited result produces rate_limit.active event."""
        from marianne.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="j1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            rate_limited=True,
        )

        event = attempt_result_to_observer_event(result)
        assert event["event"] == "rate_limit.active"

    def test_observer_event_from_sheet_skipped(self) -> None:
        """SheetSkipped produces sheet.skipped event."""
        from marianne.daemon.baton.adapter import skipped_to_observer_event

        skipped = SheetSkipped(
            job_id="j1",
            sheet_num=3,
            reason="skip_when condition met",
        )

        event = skipped_to_observer_event(skipped)
        assert event["event"] == "sheet.skipped"
        assert event["sheet_num"] == 3


# =========================================================================
# Job completion detection
# =========================================================================


class TestJobCompletionDetection:
    """Adapter detects when all sheets reach terminal state."""

    def test_is_job_complete_all_completed(self) -> None:
        """Job is complete when all sheets are in terminal state."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        # Manually set both sheets to COMPLETED
        for i in (1, 2):
            state = adapter.baton.get_sheet_state("test-job", i)
            assert state is not None
            state.status = BatonSheetStatus.COMPLETED

        assert adapter.baton.is_job_complete("test-job")

    def test_is_job_complete_mixed_terminal(self) -> None:
        """Job is complete when sheets are completed/failed/skipped."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2), _make_sheet(num=3)]
        adapter.register_job("test-job", sheets, dependencies={})

        states = [
            (1, BatonSheetStatus.COMPLETED),
            (2, BatonSheetStatus.FAILED),
            (3, BatonSheetStatus.SKIPPED),
        ]
        for num, status in states:
            state = adapter.baton.get_sheet_state("test-job", num)
            assert state is not None
            state.status = status

        assert adapter.baton.is_job_complete("test-job")

    def test_is_job_not_complete_with_pending(self) -> None:
        """Job is NOT complete when sheets are still pending."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("test-job", sheets, dependencies={})

        state1 = adapter.baton.get_sheet_state("test-job", 1)
        assert state1 is not None
        state1.status = BatonSheetStatus.COMPLETED
        # Sheet 2 stays PENDING

        assert not adapter.baton.is_job_complete("test-job")


# =========================================================================
# Feature flag — DaemonConfig.use_baton
# =========================================================================


class TestUseBatonFeatureFlag:
    """The use_baton flag in DaemonConfig controls execution path."""

    def test_daemon_config_has_use_baton_field(self) -> None:
        """DaemonConfig has a use_baton field defaulting to True (D-027)."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig()
        assert hasattr(config, "use_baton")
        assert config.use_baton is True

    def test_daemon_config_use_baton_true(self) -> None:
        """use_baton can be set to True."""
        from marianne.daemon.config import DaemonConfig

        config = DaemonConfig(use_baton=True)
        assert config.use_baton is True


# =========================================================================
# Sheets → SheetExecutionState conversion
# =========================================================================


class TestSheetsToExecutionState:
    """Converting Sheet entities to SheetExecutionState for baton registration."""

    def test_sheets_to_execution_states(self) -> None:
        """sheets_to_execution_states creates correct mapping."""
        from marianne.daemon.baton.adapter import sheets_to_execution_states

        sheets = [
            _make_sheet(num=1, instrument="claude-code"),
            _make_sheet(num=2, instrument="gemini-cli"),
        ]

        states = sheets_to_execution_states(sheets)
        assert 1 in states
        assert 2 in states
        assert states[1].instrument_name == "claude-code"
        assert states[2].instrument_name == "gemini-cli"
        assert states[1].status == BatonSheetStatus.PENDING

    def test_sheets_to_execution_states_with_retries(self) -> None:
        """Custom retry/completion limits are applied."""
        from marianne.daemon.baton.adapter import sheets_to_execution_states

        sheets = [_make_sheet(num=1)]
        states = sheets_to_execution_states(
            sheets, max_retries=7, max_completion=15
        )

        assert states[1].max_retries == 7
        assert states[1].max_completion == 15


# =========================================================================
# Dependencies extraction
# =========================================================================


class TestDependencyExtraction:
    """Extract baton-compatible dependency graph from JobConfig."""

    def test_extract_dependencies_sequential(self) -> None:
        """Sequential stages produce a chain: 2→[1], 3→[2], etc."""
        from marianne.daemon.baton.adapter import extract_dependencies

        # Mock a config with 3 sequential stages (no fan-out)
        mock_config = MagicMock()
        mock_config.sheet.total_sheets = 3
        mock_config.sheet.stages = 3
        mock_config.sheet.fan_out = {}

        # Each sheet is its own stage
        for i in range(1, 4):
            meta = MagicMock()
            meta.stage = i
            meta.instance = 1
            meta.fan_count = 1
            mock_config.sheet.get_fan_out_metadata.side_effect = (
                lambda n: MagicMock(stage=n, instance=1, fan_count=1)
            )

        deps = extract_dependencies(mock_config)

        # Sheet 1: no deps. Sheet 2: depends on sheet 1. Sheet 3: depends on sheet 2.
        assert deps.get(1, []) == []
        assert 1 in deps.get(2, [])
        assert 2 in deps.get(3, [])

    def test_extract_dependencies_fan_out(self) -> None:
        """Fan-out sheets within the same stage have no internal deps."""
        from marianne.daemon.baton.adapter import extract_dependencies

        # Mock: stage 1 has 3 fan-out sheets (sheet 1, 2, 3), stage 2 has 1 (sheet 4)
        mock_config = MagicMock()
        mock_config.sheet.total_sheets = 4
        mock_config.sheet.stages = 2

        def get_meta(n: int) -> MagicMock:
            if n <= 3:
                return MagicMock(stage=1, instance=n, fan_count=3)
            return MagicMock(stage=2, instance=1, fan_count=1)

        mock_config.sheet.get_fan_out_metadata = get_meta

        deps = extract_dependencies(mock_config)

        # Sheets 1,2,3 (stage 1): no deps
        for i in (1, 2, 3):
            assert deps.get(i, []) == []

        # Sheet 4 (stage 2): depends on ALL of stage 1 (sheets 1,2,3)
        assert sorted(deps.get(4, [])) == [1, 2, 3]


# =========================================================================
# Completion Signaling
# =========================================================================


class TestCompletionSignaling:
    """Test that the adapter signals job completion correctly."""

    def test_completion_event_created_on_register(self) -> None:
        """Registering a job creates a completion event."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheet = _make_sheet(num=1)
        adapter.register_job("j1", [sheet], {1: []})
        assert "j1" in adapter._completion_events
        assert not adapter._completion_events["j1"].is_set()

    def test_completion_event_removed_on_deregister(self) -> None:
        """Deregistering a job removes the completion event."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheet = _make_sheet(num=1)
        adapter.register_job("j1", [sheet], {1: []})
        adapter.deregister_job("j1")
        assert "j1" not in adapter._completion_events

    def test_check_completions_signals_when_all_terminal(self) -> None:
        """_check_completions signals when all sheets reach terminal state."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheet = _make_sheet(num=1)
        adapter.register_job("j1", [sheet], {1: []})

        # Manually set sheet to COMPLETED in the baton
        job = adapter.baton._jobs["j1"]
        job.sheets[1].status = BatonSheetStatus.COMPLETED

        adapter._check_completions()

        assert adapter._completion_events["j1"].is_set()
        assert adapter._completion_results["j1"] is True

    def test_check_completions_reports_failure(self) -> None:
        """_check_completions reports failure when any sheet is FAILED."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("j1", sheets, {1: [], 2: []})

        # One completed, one failed
        job = adapter.baton._jobs["j1"]
        job.sheets[1].status = BatonSheetStatus.COMPLETED
        job.sheets[2].status = BatonSheetStatus.FAILED

        adapter._check_completions()

        assert adapter._completion_events["j1"].is_set()
        assert adapter._completion_results["j1"] is False

    def test_check_completions_skips_non_terminal(self) -> None:
        """_check_completions doesn't signal if sheets are still running."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        adapter.register_job("j1", sheets, {1: [], 2: []})

        # One completed, one still pending
        job = adapter.baton._jobs["j1"]
        job.sheets[1].status = BatonSheetStatus.COMPLETED
        # sheet 2 is still PENDING (default)

        adapter._check_completions()

        assert not adapter._completion_events["j1"].is_set()

    @pytest.mark.asyncio
    async def test_wait_for_completion_returns_on_signal(self) -> None:
        """wait_for_completion unblocks when the completion event is set."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheet = _make_sheet(num=1)
        adapter.register_job("j1", [sheet], {1: []})

        # Set completion from a background task
        async def _complete_later() -> None:
            await asyncio.sleep(0.01)
            job = adapter.baton._jobs["j1"]
            job.sheets[1].status = BatonSheetStatus.COMPLETED
            adapter._check_completions()

        task = asyncio.create_task(_complete_later())
        result = await asyncio.wait_for(
            adapter.wait_for_completion("j1"), timeout=2.0
        )
        assert result is True
        await task

    @pytest.mark.asyncio
    async def test_wait_for_completion_raises_on_unknown_job(self) -> None:
        """wait_for_completion raises KeyError for unknown jobs."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        with pytest.raises(KeyError, match="not registered"):
            await adapter.wait_for_completion("nonexistent")

    def test_check_completions_idempotent(self) -> None:
        """_check_completions doesn't signal twice for the same job."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheet = _make_sheet(num=1)
        adapter.register_job("j1", [sheet], {1: []})

        job = adapter.baton._jobs["j1"]
        job.sheets[1].status = BatonSheetStatus.COMPLETED

        # Call twice
        adapter._check_completions()
        adapter._check_completions()

        # Should still be set (idempotent)
        assert adapter._completion_events["j1"].is_set()
        assert adapter._completion_results["j1"] is True
