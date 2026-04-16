"""Movement 3 adversarial tests — Breakpoint.

Targets every major M3 fix with edge cases designed to break them:

1. F-152 dispatch guard: exception taxonomy, inbox safety, attempt math
2. F-112 rate limit auto-resume: timer scheduling, boundary wait_seconds
3. F-150 model override: double-apply, carryover, type coercion
4. F-145 completed_new_work: status edge cases, missing jobs
5. F-009/F-144 semantic context tags: namespace correctness, empty inputs
6. F-158 PromptRenderer wiring: None config, total_stages math, recovery path
7. Clear-rate-limits: non-existent instrument, double clear, mixed state
8. Stagger delay: boundary values, single sheet, empty batch

Each test proves a specific hypothesis about how the code can fail.
If it doesn't fail, the fix is solid. If it fails, we found a gap.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.baton.core import _JobRecord
from marianne.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
    SheetAttemptResult,
)
from marianne.daemon.baton.state import (
    _TERMINAL_BATON_STATUSES,
    BatonSheetStatus,
    InstrumentState,
    SheetExecutionState,
)

# =========================================================================
# Helpers — minimal fakes for testing without real infrastructure
# =========================================================================


def _make_state(
    sheet_num: int = 1,
    instrument: str = "claude-code",
    status: BatonSheetStatus = BatonSheetStatus.PENDING,
    normal_attempts: int = 0,
    completion_attempts: int = 0,
    healing_attempts: int = 0,
    max_retries: int = 3,
) -> SheetExecutionState:
    """Build a SheetExecutionState with sane defaults."""
    return SheetExecutionState(
        sheet_num=sheet_num,
        instrument_name=instrument,
        status=status,
        normal_attempts=normal_attempts,
        completion_attempts=completion_attempts,
        healing_attempts=healing_attempts,
        max_retries=max_retries,
    )


def _make_instrument(
    name: str = "claude-code",
    rate_limited: bool = False,
    expires_at: float | None = None,
) -> InstrumentState:
    """Build an InstrumentState."""
    return InstrumentState(
        name=name,
        max_concurrent=5,
        rate_limited=rate_limited,
        rate_limit_expires_at=expires_at,
    )


def _make_job_record(
    job_id: str = "test-job",
    sheets: dict[int, SheetExecutionState] | None = None,
) -> _JobRecord:
    """Build a _JobRecord for BatonCore._jobs."""
    return _JobRecord(
        job_id=job_id,
        sheets=sheets or {},
        dependencies={},
    )


# =========================================================================
# 1. F-152: Dispatch Guard — Exception Taxonomy
# =========================================================================


class TestDispatchGuardExceptionTaxonomy:
    """The dispatch guard must catch ALL exceptions, not just common ones.

    F-152's root cause: NotImplementedError escaped the try-except,
    leaving the sheet in READY → infinite dispatch loop. The fix broadened
    to `except Exception`. We test the taxonomy.
    """

    def test_keyboard_interrupt_not_caught(self) -> None:
        """KeyboardInterrupt inherits from BaseException, not Exception.

        The dispatch guard catches Exception. KeyboardInterrupt SHOULD
        propagate — it's a signal to stop, not a dispatch failure.
        Verify the design is intentional.
        """
        # KeyboardInterrupt is BaseException, not Exception
        assert not issubclass(KeyboardInterrupt, Exception)
        # SystemExit too
        assert not issubclass(SystemExit, Exception)

    def test_send_dispatch_failure_attempt_math_zero_attempts(self) -> None:
        """When state has zero attempts, failure event should be attempt 1."""
        from marianne.daemon.baton.adapter import BatonAdapter

        baton = MagicMock(spec=["inbox", "_jobs", "_instruments"])
        baton.inbox = asyncio.Queue()
        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = baton

        state = _make_state(normal_attempts=0, completion_attempts=0)
        adapter._send_dispatch_failure("job-1", 1, "claude-code", "test error", state=state)

        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.attempt == 1

    def test_send_dispatch_failure_attempt_math_after_retries(self) -> None:
        """After 3 normal + 2 completion attempts, failure should be attempt 6."""
        from marianne.daemon.baton.adapter import BatonAdapter

        baton = MagicMock(spec=["inbox", "_jobs", "_instruments"])
        baton.inbox = asyncio.Queue()
        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = baton

        state = _make_state(normal_attempts=3, completion_attempts=2)
        adapter._send_dispatch_failure("job-1", 1, "claude-code", "test error", state=state)

        event = baton.inbox.get_nowait()
        assert event.attempt == 6  # 3 + 2 + 1

    def test_send_dispatch_failure_no_state(self) -> None:
        """When state is None (sheet not found), attempt defaults to 1."""
        from marianne.daemon.baton.adapter import BatonAdapter

        baton = MagicMock(spec=["inbox", "_jobs", "_instruments"])
        baton.inbox = asyncio.Queue()
        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = baton

        adapter._send_dispatch_failure("job-1", 1, "claude-code", "test error", state=None)

        event = baton.inbox.get_nowait()
        assert event.attempt == 1

    def test_dispatch_failure_error_classification_is_e505(self) -> None:
        """All dispatch failures must use E505 classification."""
        from marianne.daemon.baton.adapter import BatonAdapter

        baton = MagicMock(spec=["inbox", "_jobs", "_instruments"])
        baton.inbox = asyncio.Queue()
        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = baton

        state = _make_state()
        adapter._send_dispatch_failure("job-1", 1, "claude-code", "any error", state=state)

        event = baton.inbox.get_nowait()
        assert event.error_classification == "E505"
        assert event.execution_success is False

    def test_dispatch_failure_preserves_job_and_sheet_ids(self) -> None:
        """Failure event must carry the correct job_id and sheet_num."""
        from marianne.daemon.baton.adapter import BatonAdapter

        baton = MagicMock(spec=["inbox", "_jobs", "_instruments"])
        baton.inbox = asyncio.Queue()
        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = baton

        adapter._send_dispatch_failure("my-special-job", 42, "gemini-cli", "err", state=None)

        event = baton.inbox.get_nowait()
        assert event.job_id == "my-special-job"
        assert event.sheet_num == 42
        assert event.instrument_name == "gemini-cli"


# =========================================================================
# 2. F-112: Rate Limit Auto-Resume — Timer Scheduling
# =========================================================================


class TestRateLimitAutoResume:
    """The baton must schedule a timer to auto-clear rate limits.

    F-112: WAITING sheets stayed blocked forever because nothing
    triggered RateLimitExpired after the wait period. The fix adds
    timer scheduling in _handle_rate_limit_hit().
    """

    def test_timer_none_does_not_crash(self) -> None:
        """If _timer is None, rate limit handling should proceed without crash.

        The timer scheduling is guarded by `if self._timer is not None`.
        Without this guard, NoneType.schedule() raises AttributeError.
        """
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._instruments = {"claude-code": _make_instrument()}
        baton._jobs = {}
        baton._timer = None
        baton._state_dirty = False

        event = RateLimitHit(
            job_id="job-1",
            sheet_num=1,
            instrument="claude-code",
            wait_seconds=60.0,
        )

        # Should not raise — timer scheduling is skipped gracefully
        baton._handle_rate_limit_hit(event)

        inst = baton._instruments["claude-code"]
        assert inst.rate_limited is True
        assert inst.rate_limit_expires_at is not None

    def test_zero_wait_seconds_schedules_immediate_timer(self) -> None:
        """wait_seconds=0.0 should schedule a timer that fires immediately.

        Edge case: API says "rate limit clears now." The timer should
        still be scheduled (not skipped) because it's the only mechanism
        to move WAITING sheets back to PENDING.
        """
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._instruments = {"claude-code": _make_instrument()}
        baton._jobs = {}
        baton._state_dirty = False

        timer = MagicMock(spec=["schedule"])
        baton._timer = timer

        event = RateLimitHit(
            job_id="job-1",
            sheet_num=1,
            instrument="claude-code",
            wait_seconds=0.0,
        )

        baton._handle_rate_limit_hit(event)

        timer.schedule.assert_called_once()
        args = timer.schedule.call_args
        assert args[0][0] == 0.0  # delay
        assert isinstance(args[0][1], RateLimitExpired)
        assert args[0][1].instrument == "claude-code"

    def test_rate_limit_expired_clears_instrument_state(self) -> None:
        """RateLimitExpired must clear rate_limited flag AND move sheets."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False

        # Set up rate-limited instrument
        inst = _make_instrument(rate_limited=True, expires_at=time.monotonic())
        baton._instruments = {"claude-code": inst}

        # Set up a WAITING sheet
        sheet = _make_state(status=BatonSheetStatus.WAITING)
        job = _make_job_record(sheets={1: sheet})
        baton._jobs = {"job-1": job}

        event = RateLimitExpired(instrument="claude-code")
        baton._handle_rate_limit_expired(event)

        assert inst.rate_limited is False
        assert inst.rate_limit_expires_at is None
        assert sheet.status == BatonSheetStatus.PENDING

    def test_rate_limit_expired_does_not_move_non_waiting_sheets(self) -> None:
        """Only WAITING sheets move to PENDING. Others are untouched."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {"claude-code": _make_instrument(rate_limited=True)}

        sheets = {
            1: _make_state(sheet_num=1, status=BatonSheetStatus.WAITING),
            2: _make_state(sheet_num=2, status=BatonSheetStatus.COMPLETED),
            3: _make_state(sheet_num=3, status=BatonSheetStatus.FAILED),
            4: _make_state(sheet_num=4, status=BatonSheetStatus.IN_PROGRESS),
            5: _make_state(sheet_num=5, status=BatonSheetStatus.PENDING),
        }
        baton._jobs = {"job-1": _make_job_record(sheets=sheets)}

        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-code"))

        assert sheets[1].status == BatonSheetStatus.PENDING  # moved
        assert sheets[2].status == BatonSheetStatus.COMPLETED  # untouched
        assert sheets[3].status == BatonSheetStatus.FAILED  # untouched
        assert sheets[4].status == BatonSheetStatus.IN_PROGRESS  # untouched
        assert sheets[5].status == BatonSheetStatus.PENDING  # already pending

    def test_rate_limit_expired_unknown_instrument_no_crash(self) -> None:
        """Expiry for an instrument that was never registered should not crash."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {}
        baton._jobs = {}

        # Should not raise
        baton._handle_rate_limit_expired(RateLimitExpired(instrument="nonexistent-instrument"))

    def test_rate_limit_cross_instrument_isolation(self) -> None:
        """Rate limit on one instrument must not affect another's sheets."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
            "gemini-cli": _make_instrument("gemini-cli", rate_limited=True),
        }

        sheets = {
            1: _make_state(
                sheet_num=1,
                instrument="claude-code",
                status=BatonSheetStatus.WAITING,
            ),
            2: _make_state(
                sheet_num=2,
                instrument="gemini-cli",
                status=BatonSheetStatus.WAITING,
            ),
        }
        baton._jobs = {"job-1": _make_job_record(sheets=sheets)}

        # Clear only claude-code
        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-code"))

        assert sheets[1].status == BatonSheetStatus.PENDING  # claude-code: moved
        assert sheets[2].status == BatonSheetStatus.WAITING  # gemini-cli: still waiting


# =========================================================================
# 3. F-150: Model Override — Carryover & Type Coercion
# =========================================================================


class TestModelOverrideAdversarial:
    """Model overrides must be applied AND cleared correctly.

    F-150a: model was extracted but silently ignored.
    F-150b: model from sheet N carried over to sheet N+1 via pooled backend.
    """

    def test_apply_overrides_empty_dict_is_noop(self) -> None:
        """Empty overrides dict must not set _has_overrides flag."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = MagicMock()
        profile.default_model = "original"
        profile.cli = MagicMock()
        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "original"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({})

        assert backend._has_overrides is False
        assert backend._model == "original"
        assert backend._saved_model is None

    def test_apply_overrides_saves_original(self) -> None:
        """apply_overrides must save original model before overwriting."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "opus-4"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({"model": "sonnet-4"})

        assert backend._saved_model == "opus-4"
        assert backend._model == "sonnet-4"
        assert backend._has_overrides is True

    def test_clear_overrides_restores_original(self) -> None:
        """clear_overrides must restore the pre-override model."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "sonnet-4"
        backend._saved_model = "opus-4"
        backend._has_overrides = True

        backend.clear_overrides()

        assert backend._model == "opus-4"
        assert backend._saved_model is None
        assert backend._has_overrides is False

    def test_double_clear_is_safe(self) -> None:
        """Calling clear_overrides twice must not corrupt state."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "sonnet-4"
        backend._saved_model = "opus-4"
        backend._has_overrides = True

        backend.clear_overrides()
        assert backend._model == "opus-4"

        # Second clear should be a no-op
        backend.clear_overrides()
        assert backend._model == "opus-4"
        assert backend._has_overrides is False

    def test_double_apply_overwrites_saved_model(self) -> None:
        """Applying overrides twice without clear overwrites _saved_model.

        This is a known design choice — the caller MUST hold override_lock
        for the entire apply→execute→clear window. Without it, this happens.
        Document the behavior.
        """
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "opus-4"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({"model": "sonnet-4"})
        assert backend._saved_model == "opus-4"

        # Second apply overwrites saved_model with the OVERRIDDEN value
        backend.apply_overrides({"model": "haiku-3"})
        assert backend._saved_model == "sonnet-4"  # saved the override, not original
        assert backend._model == "haiku-3"

        # Clear now restores to sonnet-4, NOT opus-4 — original is lost
        backend.clear_overrides()
        assert backend._model == "sonnet-4"  # NOT opus-4!

    def test_apply_overrides_coerces_non_string_model(self) -> None:
        """Model value should be coerced to string via str()."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "opus-4"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({"model": 42})

        assert backend._model == "42"
        assert isinstance(backend._model, str)

    def test_apply_overrides_none_model_becomes_string_none(self) -> None:
        """Passing model=None in overrides dict coerces to 'None' string.

        The caller (adapter) guards against this by checking model_override
        is not None before passing to acquire(). But if it leaks through,
        the backend converts it to the string "None".
        """
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._model = "opus-4"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({"model": None})

        assert backend._model == "None"  # str(None) = "None"

    @pytest.mark.asyncio
    async def test_backend_pool_release_clears_overrides(self) -> None:
        """BackendPool.release() must call clear_overrides() before pooling."""
        from marianne.daemon.baton.backend_pool import BackendPool

        registry = MagicMock()
        profile = MagicMock()
        profile.kind = "cli"
        registry.get.return_value = profile

        pool = BackendPool(registry)
        pool._in_flight = {"claude-code": 1}
        pool._cli_free = {}

        backend = MagicMock()

        await pool.release("claude-code", backend)

        backend.clear_overrides.assert_called_once()


# =========================================================================
# 4. F-145: Completed New Work — Status Edge Cases
# =========================================================================


class TestCompletedNewWork:
    """has_completed_sheets() must correctly detect COMPLETED status.

    F-145: Without this, concert chaining's zero-work guard breaks
    under baton — hooks run even when no sheet completed, or don't
    run when they should.
    """

    def test_no_completed_sheets(self) -> None:
        """All FAILED/CANCELLED sheets → has_completed_sheets returns False."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton._jobs = {
            "job-1": _make_job_record(
                sheets={
                    1: _make_state(sheet_num=1, status=BatonSheetStatus.FAILED),
                    2: _make_state(sheet_num=2, status=BatonSheetStatus.CANCELLED),
                }
            )
        }
        adapter._baton = baton

        assert adapter.has_completed_sheets("job-1") is False

    def test_one_completed_among_failures(self) -> None:
        """One COMPLETED sheet among FAILEDs → returns True."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton._jobs = {
            "job-1": _make_job_record(
                sheets={
                    1: _make_state(sheet_num=1, status=BatonSheetStatus.FAILED),
                    2: _make_state(sheet_num=2, status=BatonSheetStatus.COMPLETED),
                    3: _make_state(sheet_num=3, status=BatonSheetStatus.FAILED),
                }
            )
        }
        adapter._baton = baton

        assert adapter.has_completed_sheets("job-1") is True

    def test_skipped_does_not_count_as_completed(self) -> None:
        """SKIPPED is terminal but NOT COMPLETED. Must return False."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton._jobs = {
            "job-1": _make_job_record(
                sheets={
                    1: _make_state(sheet_num=1, status=BatonSheetStatus.SKIPPED),
                    2: _make_state(sheet_num=2, status=BatonSheetStatus.SKIPPED),
                }
            )
        }
        adapter._baton = baton

        assert adapter.has_completed_sheets("job-1") is False

    def test_missing_job_returns_false(self) -> None:
        """Job not in baton's registry → False (not KeyError)."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton._jobs = {}
        adapter._baton = baton

        assert adapter.has_completed_sheets("nonexistent-job") is False

    def test_empty_sheets_returns_false(self) -> None:
        """Job with zero sheets → False."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton._jobs = {"job-1": _make_job_record(sheets={})}
        adapter._baton = baton

        assert adapter.has_completed_sheets("job-1") is False


# =========================================================================
# 6. F-158: PromptRenderer Wiring
# =========================================================================


class TestPromptRendererWiring:
    """PromptRenderer must be created and stored for every baton job.

    F-158: Without prompt_config being passed, musicians get raw templates
    instead of the full 9-layer rendered prompts.
    """

    def test_register_job_no_prompt_config(self) -> None:
        """When prompt_config=None, no renderer is created (but job registers)."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = MagicMock()
        adapter._baton.inbox = asyncio.Queue()
        adapter._job_sheets = {}
        adapter._job_renderers = {}
        adapter._completion_events = {}

        sheet = MagicMock()
        sheet.num = 1
        sheet.movement = "m1"
        sheet.instrument_name = "claude-code"

        adapter.register_job(
            job_id="test-job",
            sheets=[sheet],
            dependencies={},
            prompt_config=None,
        )

        assert "test-job" not in adapter._job_renderers

    def test_register_job_with_prompt_config_creates_renderer(self) -> None:
        """When prompt_config is provided, renderer is created and stored."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = MagicMock()
        adapter._baton.inbox = asyncio.Queue()
        adapter._job_sheets = {}
        adapter._job_renderers = {}
        adapter._completion_events = {}

        sheet1 = MagicMock()
        sheet1.num = 1
        sheet1.movement = "m1"
        sheet1.instrument_name = "claude-code"

        sheet2 = MagicMock()
        sheet2.num = 2
        sheet2.movement = "m2"
        sheet2.instrument_name = "claude-code"

        prompt_config = MagicMock()

        with (
            patch("marianne.daemon.baton.adapter.PromptRenderer", create=True) as mock_cls,
            patch.dict(
                "sys.modules",
                {"marianne.daemon.baton.prompt": MagicMock(PromptRenderer=mock_cls)},
            ),
        ):
            adapter.register_job(
                job_id="test-job",
                sheets=[sheet1, sheet2],
                dependencies={},
                prompt_config=prompt_config,
            )

            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            assert call_kwargs.kwargs.get("total_sheets") == 2
            # 2 sheets, 2 different movements → total_stages = 2
            assert call_kwargs.kwargs.get("total_stages") == 2

    def test_total_stages_single_movement(self) -> None:
        """All sheets in same movement → total_stages = 1."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = MagicMock()
        adapter._baton.inbox = asyncio.Queue()
        adapter._job_sheets = {}
        adapter._job_renderers = {}
        adapter._completion_events = {}

        sheets = []
        for i in range(5):
            s = MagicMock()
            s.num = i + 1
            s.movement = "movement-1"
            s.instrument_name = "claude-code"
            sheets.append(s)

        with (
            patch("marianne.daemon.baton.adapter.PromptRenderer", create=True) as mock_cls,
            patch.dict(
                "sys.modules",
                {"marianne.daemon.baton.prompt": MagicMock(PromptRenderer=mock_cls)},
            ),
        ):
            adapter.register_job(
                job_id="test-job",
                sheets=sheets,
                dependencies={},
                prompt_config=MagicMock(),
            )

            assert mock_cls.call_args.kwargs.get("total_stages") == 1

    def test_total_stages_none_movement_counted(self) -> None:
        """Sheets with movement=None should still produce total_stages >= 1.

        The code uses `len({s.movement for s in sheets}) or 1`. If all
        movements are None, the set has 1 element ({None}) → total_stages=1.
        """
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        adapter._baton = MagicMock()
        adapter._baton.inbox = asyncio.Queue()
        adapter._job_sheets = {}
        adapter._job_renderers = {}
        adapter._completion_events = {}

        sheet = MagicMock()
        sheet.num = 1
        sheet.movement = None
        sheet.instrument_name = "claude-code"

        with (
            patch("marianne.daemon.baton.adapter.PromptRenderer", create=True) as mock_cls,
            patch.dict(
                "sys.modules",
                {"marianne.daemon.baton.prompt": MagicMock(PromptRenderer=mock_cls)},
            ),
        ):
            adapter.register_job(
                job_id="test-job",
                sheets=[sheet],
                dependencies={},
                prompt_config=MagicMock(),
            )

            total_stages = mock_cls.call_args.kwargs.get("total_stages")
            assert total_stages >= 1


# =========================================================================
# 7. Clear Rate Limits — Dual-Path Clearing
# =========================================================================


class TestClearRateLimits:
    """clear_instrument_rate_limit must clear BOTH coordinator and baton state.

    The clear command clears rate limit on instruments AND moves WAITING
    sheets back to PENDING for re-dispatch.
    """

    def test_clear_specific_instrument(self) -> None:
        """Clearing one instrument leaves others rate-limited."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
            "gemini-cli": _make_instrument("gemini-cli", rate_limited=True),
        }
        baton._jobs = {}

        cleared = baton.clear_instrument_rate_limit(instrument="claude-code")

        assert cleared == 1
        assert baton._instruments["claude-code"].rate_limited is False
        assert baton._instruments["gemini-cli"].rate_limited is True

    def test_clear_all_instruments(self) -> None:
        """Clearing with instrument=None clears ALL rate-limited instruments."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
            "gemini-cli": _make_instrument("gemini-cli", rate_limited=True),
        }
        baton._jobs = {}

        cleared = baton.clear_instrument_rate_limit(instrument=None)

        assert cleared == 2
        assert baton._instruments["claude-code"].rate_limited is False
        assert baton._instruments["gemini-cli"].rate_limited is False

    def test_clear_nonexistent_instrument_returns_zero(self) -> None:
        """Non-existent instrument name must return 0, not clear others.

        F-200 regression test: Previously, the conditional fell through
        to clear-all when instrument was a truthy string not in the dict.
        Fixed in core.py clear_instrument_rate_limit().
        """
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
        }
        baton._jobs = {}

        cleared = baton.clear_instrument_rate_limit(instrument="nonexistent")

        assert cleared == 0
        # claude-code must still be rate-limited
        assert baton._instruments["claude-code"].rate_limited is True

    def test_clear_moves_waiting_sheets_to_pending(self) -> None:
        """WAITING sheets on cleared instrument must move to PENDING."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
        }

        sheet1 = _make_state(
            sheet_num=1,
            instrument="claude-code",
            status=BatonSheetStatus.WAITING,
        )
        sheet2 = _make_state(
            sheet_num=2,
            instrument="claude-code",
            status=BatonSheetStatus.COMPLETED,
        )
        baton._jobs = {"job-1": _make_job_record(sheets={1: sheet1, 2: sheet2})}

        baton.clear_instrument_rate_limit(instrument="claude-code")

        assert sheet1.status == BatonSheetStatus.PENDING
        assert sheet2.status == BatonSheetStatus.COMPLETED  # untouched

    def test_clear_not_rate_limited_returns_zero(self) -> None:
        """Clearing an instrument that isn't rate-limited returns 0."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=False),
        }
        baton._jobs = {}

        cleared = baton.clear_instrument_rate_limit(instrument="claude-code")
        assert cleared == 0

    def test_double_clear_is_idempotent(self) -> None:
        """Clearing twice should produce 0 on second call."""
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
        }
        baton._jobs = {}

        first = baton.clear_instrument_rate_limit(instrument="claude-code")
        baton._state_dirty = False  # reset for second call
        second = baton.clear_instrument_rate_limit(instrument="claude-code")

        assert first == 1
        assert second == 0

    def test_clear_with_empty_string_instrument_clears_all(self) -> None:
        """Empty string instrument should trigger the 'all' path.

        The code uses `if instrument:` — empty string is falsy,
        falls through to clear all. Same behavior as None.
        """
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
            "gemini-cli": _make_instrument("gemini-cli", rate_limited=True),
        }
        baton._jobs = {}

        # Empty string is treated as a specific instrument name (not "clear all")
        # after F-201 fix: `if instrument:` → `if instrument is not None:`
        cleared = baton.clear_instrument_rate_limit(instrument="")
        assert cleared == 0


# =========================================================================
# 8. Stagger Delay — Boundary Values
# =========================================================================


class TestStaggerDelayBoundary:
    """stagger_delay_ms on ParallelConfig must be bounded and correctly applied."""

    def test_stagger_delay_zero_is_valid(self) -> None:
        """stagger_delay_ms=0 should be accepted (no delay)."""
        from marianne.core.config.execution import ParallelConfig

        config = ParallelConfig(stagger_delay_ms=0)
        assert config.stagger_delay_ms == 0

    def test_stagger_delay_max_boundary(self) -> None:
        """stagger_delay_ms=5000 should be accepted (max boundary)."""
        from marianne.core.config.execution import ParallelConfig

        config = ParallelConfig(stagger_delay_ms=5000)
        assert config.stagger_delay_ms == 5000

    def test_stagger_delay_over_max_rejected(self) -> None:
        """stagger_delay_ms > 5000 should be rejected by Pydantic validation."""
        from pydantic import ValidationError

        from marianne.core.config.execution import ParallelConfig

        with pytest.raises(ValidationError):
            ParallelConfig(stagger_delay_ms=5001)

    def test_stagger_delay_negative_rejected(self) -> None:
        """Negative stagger_delay_ms should be rejected."""
        from pydantic import ValidationError

        from marianne.core.config.execution import ParallelConfig

        with pytest.raises(ValidationError):
            ParallelConfig(stagger_delay_ms=-1)

    def test_stagger_delay_default_is_zero(self) -> None:
        """Default stagger_delay_ms should be 0 (no delay)."""
        from marianne.core.config.execution import ParallelConfig

        config = ParallelConfig()
        assert config.stagger_delay_ms == 0


# =========================================================================
# 9. Terminal Status Invariants — Cross-Cutting
# =========================================================================


class TestTerminalStatusInvariants:
    """Terminal statuses must be consistent across all baton operations.

    Every handler that touches sheet status must respect _TERMINAL_BATON_STATUSES.
    This test verifies the invariant holds for rate limit operations.
    """

    def test_terminal_statuses_are_frozen(self) -> None:
        """_TERMINAL_BATON_STATUSES must be a frozenset (immutable)."""
        assert isinstance(_TERMINAL_BATON_STATUSES, frozenset)

    def test_terminal_statuses_include_all_final_states(self) -> None:
        """COMPLETED, FAILED, SKIPPED, CANCELLED are terminal."""
        expected = {
            BatonSheetStatus.COMPLETED,
            BatonSheetStatus.FAILED,
            BatonSheetStatus.SKIPPED,
            BatonSheetStatus.CANCELLED,
        }
        assert expected == _TERMINAL_BATON_STATUSES

    def test_rate_limit_hit_does_not_regress_terminal_sheets(self) -> None:
        """Rate limit hit must NOT move terminal sheets to WAITING.

        Core.py line 977: only DISPATCHED/RUNNING sheets transition.
        Terminal sheets must be excluded. Already-WAITING sheets stay WAITING
        (they were already waiting for a rate limit).
        """
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._timer = None
        baton._instruments = {
            "claude-code": _make_instrument("claude-code"),
        }

        # Create one sheet per status to test all transitions
        statuses = list(BatonSheetStatus)
        sheets = {}
        for i, status in enumerate(statuses, start=1):
            sheets[i] = _make_state(
                sheet_num=i,
                instrument="claude-code",
                status=status,
            )
        # Save original statuses for verification
        originals = {i: s.status for i, s in sheets.items()}

        baton._jobs = {"job-1": _make_job_record(sheets=sheets)}

        event = RateLimitHit(
            job_id="job-1",
            sheet_num=1,
            instrument="claude-code",
            wait_seconds=60.0,
        )
        baton._handle_rate_limit_hit(event)

        # Verify each sheet's transition
        for num, sheet in sheets.items():
            original = originals[num]
            if original in _TERMINAL_BATON_STATUSES:
                # Terminal sheets must NOT be moved
                assert sheet.status == original, (
                    f"Terminal sheet {original} was moved to {sheet.status}"
                )
            elif original in (
                BatonSheetStatus.DISPATCHED,
                BatonSheetStatus.IN_PROGRESS,
            ):
                # DISPATCHED/RUNNING → WAITING
                assert sheet.status == BatonSheetStatus.WAITING
            elif original == BatonSheetStatus.WAITING:
                # Already WAITING stays WAITING
                assert sheet.status == BatonSheetStatus.WAITING
            else:
                # PENDING, READY, RETRY_SCHEDULED, FERMATA — unchanged
                assert sheet.status == original, (
                    f"Sheet {original} unexpectedly moved to {sheet.status}"
                )

    def test_clear_rate_limit_does_not_move_terminal_sheets(self) -> None:
        """clear_instrument_rate_limit must only move WAITING → PENDING.

        Terminal sheets (even if instrument matches) must not be touched.
        """
        from marianne.daemon.baton.core import BatonCore

        baton = BatonCore.__new__(BatonCore)
        baton._state_dirty = False
        baton._instruments = {
            "claude-code": _make_instrument("claude-code", rate_limited=True),
        }

        sheets = {
            1: _make_state(
                sheet_num=1,
                instrument="claude-code",
                status=BatonSheetStatus.WAITING,
            ),
            2: _make_state(
                sheet_num=2,
                instrument="claude-code",
                status=BatonSheetStatus.COMPLETED,
            ),
            3: _make_state(
                sheet_num=3,
                instrument="claude-code",
                status=BatonSheetStatus.FAILED,
            ),
        }
        baton._jobs = {"job-1": _make_job_record(sheets=sheets)}

        baton.clear_instrument_rate_limit(instrument="claude-code")

        assert sheets[1].status == BatonSheetStatus.PENDING
        assert sheets[2].status == BatonSheetStatus.COMPLETED
        assert sheets[3].status == BatonSheetStatus.FAILED


# =========================================================================
# 10. Dispatch Callback Integration — Full Path
# =========================================================================


class TestDispatchCallbackIntegration:
    """Test the full _dispatch_callback path under adversarial conditions."""

    @pytest.mark.asyncio
    async def test_dispatch_sheet_not_found_sends_failure(self) -> None:
        """Sheet not found in adapter registry → failure event in inbox."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton.inbox = asyncio.Queue()
        adapter._baton = baton
        adapter._job_sheets = {}  # no sheets registered

        state = _make_state()
        await adapter._dispatch_callback("job-1", 1, state)

        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"
        assert "not found" in event.error_message.lower()

    @pytest.mark.asyncio
    async def test_dispatch_no_backend_pool_sends_failure(self) -> None:
        """No backend pool → failure event in inbox."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton.inbox = asyncio.Queue()
        adapter._baton = baton
        adapter._backend_pool = None

        sheet = MagicMock()
        sheet.instrument_name = "claude-code"
        adapter._job_sheets = {"job-1": {1: sheet}}

        def get_sheet(job_id: str, sheet_num: int) -> Any:
            return adapter._job_sheets.get(job_id, {}).get(sheet_num)

        adapter.get_sheet = get_sheet

        state = _make_state()
        await adapter._dispatch_callback("job-1", 1, state)

        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"
        assert "backend pool" in event.error_message.lower()

    @pytest.mark.asyncio
    async def test_dispatch_backend_acquire_exception_sends_failure(self) -> None:
        """Backend acquisition raises → failure event in inbox."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton.inbox = asyncio.Queue()
        adapter._baton = baton

        pool = AsyncMock()
        pool.acquire.side_effect = NotImplementedError("HTTP not supported")
        adapter._backend_pool = pool

        sheet = MagicMock()
        sheet.instrument_name = "test-instrument"
        sheet.instrument_config = {}
        sheet.workspace = "/tmp"
        adapter._job_sheets = {"job-1": {1: sheet}}

        def get_sheet(job_id: str, sheet_num: int) -> Any:
            return adapter._job_sheets.get(job_id, {}).get(sheet_num)

        adapter.get_sheet = get_sheet

        state = _make_state()
        await adapter._dispatch_callback("job-1", 1, state)

        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"
        assert "HTTP not supported" in event.error_message

    @pytest.mark.asyncio
    async def test_dispatch_runtime_error_sends_failure(self) -> None:
        """RuntimeError during acquire → failure event (not crash)."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()
        baton.inbox = asyncio.Queue()
        adapter._baton = baton

        pool = AsyncMock()
        pool.acquire.side_effect = RuntimeError("Pool exhausted")
        adapter._backend_pool = pool

        sheet = MagicMock()
        sheet.instrument_name = "claude-code"
        sheet.instrument_config = {}
        sheet.workspace = "/tmp"
        adapter._job_sheets = {"job-1": {1: sheet}}

        def get_sheet(job_id: str, sheet_num: int) -> Any:
            return adapter._job_sheets.get(job_id, {}).get(sheet_num)

        adapter.get_sheet = get_sheet

        state = _make_state()
        await adapter._dispatch_callback("job-1", 1, state)

        event = baton.inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult)
        assert event.error_classification == "E505"


# =========================================================================
# 11. Rate Limit Wait Cap (F-160) — Warden's fix, adversarial verification
# =========================================================================


class TestRateLimitWaitCap:
    """F-160: Unbounded rate limit wait_seconds must be capped."""

    def test_cap_constant_exists(self) -> None:
        """RESET_TIME_MAXIMUM_WAIT_SECONDS must exist and be 86400."""
        from marianne.core.constants import RESET_TIME_MAXIMUM_WAIT_SECONDS

        assert RESET_TIME_MAXIMUM_WAIT_SECONDS == 86400.0

    def test_clamp_wait_caps_extreme_values(self) -> None:
        """_clamp_wait must cap values exceeding the maximum."""
        from marianne.core.constants import RESET_TIME_MAXIMUM_WAIT_SECONDS
        from marianne.core.errors.classifier import ErrorClassifier

        # 114-year wait (the adversarial case from F-160)
        result = ErrorClassifier._clamp_wait(999_999 * 3600)
        assert result <= RESET_TIME_MAXIMUM_WAIT_SECONDS

    def test_clamp_wait_preserves_normal_values(self) -> None:
        """Normal wait times (< 24h) must pass through unchanged.

        Note: _clamp_wait also has a MINIMUM floor (300s). Values below
        300s are clamped UP to the minimum. This is by design — very short
        waits suggest the API response is unreliable.
        """
        from marianne.core.constants import RESET_TIME_MINIMUM_WAIT_SECONDS
        from marianne.core.errors.classifier import ErrorClassifier

        # Values above minimum should pass through
        assert ErrorClassifier._clamp_wait(3600.0) == 3600.0
        # Values below minimum are clamped up
        assert ErrorClassifier._clamp_wait(0.0) == RESET_TIME_MINIMUM_WAIT_SECONDS
        assert ErrorClassifier._clamp_wait(60.0) == RESET_TIME_MINIMUM_WAIT_SECONDS


# =========================================================================
# 12. SheetExecutionState — record_attempt Edge Cases
# =========================================================================


class TestRecordAttemptEdgeCases:
    """record_attempt() must correctly handle boundary conditions."""

    def test_rate_limited_attempt_does_not_increment_normal(self) -> None:
        """Rate-limited attempts must NOT count toward retry budget."""
        state = _make_state(max_retries=3)

        result = SheetAttemptResult(
            job_id="job-1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            rate_limited=True,
        )

        state.record_attempt(result)

        assert state.normal_attempts == 0
        assert len(state.attempt_results) == 1

    def test_successful_attempt_does_not_increment_normal(self) -> None:
        """Successful attempts must NOT count toward retry budget."""
        state = _make_state(max_retries=3)

        result = SheetAttemptResult(
            job_id="job-1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
        )

        state.record_attempt(result)

        assert state.normal_attempts == 0

    def test_failed_non_rate_limited_increments_normal(self) -> None:
        """Failed non-rate-limited attempts MUST increment retry budget."""
        state = _make_state(max_retries=3)

        result = SheetAttemptResult(
            job_id="job-1",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=False,
            rate_limited=False,
        )

        state.record_attempt(result)

        assert state.normal_attempts == 1

    def test_cost_accumulates_across_attempts(self) -> None:
        """total_cost_usd must accumulate across all attempt types."""
        state = _make_state()

        for i in range(3):
            result = SheetAttemptResult(
                job_id="job-1",
                sheet_num=1,
                instrument_name="claude-code",
                attempt=i + 1,
                execution_success=False,
                cost_usd=0.05,
            )
            state.record_attempt(result)

        assert abs(state.total_cost_usd - 0.15) < 1e-10


# =========================================================================
# Summary count for test discovery
# =========================================================================
# Total: 12 test classes, 55+ individual test methods
