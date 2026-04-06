"""Tests for BatonEvent types — the vocabulary of the conductor's execution heart.

Validates construction, immutability, union type coverage, default values,
and EventBus integration via to_observer_event().
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError

import pytest

from marianne.daemon.baton.events import (
    BatonEvent,
    CancelJob,
    ConfigReloaded,
    CronTick,
    DispatchRetry,
    EscalationNeeded,
    EscalationResolved,
    EscalationTimeout,
    JobTimeout,
    PacingComplete,
    PauseJob,
    ProcessExited,
    RateLimitExpired,
    RateLimitHit,
    ResourceAnomaly,
    ResumeJob,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
    ShutdownRequested,
    StaleCheck,
    to_observer_event,
)


# =============================================================================
# All event types in the union — used for parametrized tests
# =============================================================================

def _make_all_events() -> list[BatonEvent]:
    """Create one instance of every BatonEvent type with minimal args."""
    return [
        SheetAttemptResult(
            job_id="test-job", sheet_num=1, instrument_name="claude-code", attempt=1,
        ),
        SheetSkipped(job_id="test-job", sheet_num=2, reason="skip_when"),
        RateLimitHit(
            instrument="claude-code", wait_seconds=60.0,
            job_id="test-job", sheet_num=3,
        ),
        RateLimitExpired(instrument="claude-code"),
        RetryDue(job_id="test-job", sheet_num=4),
        StaleCheck(job_id="test-job", sheet_num=5),
        CronTick(entry_name="nightly", score_path="scores/cleanup.yaml"),
        JobTimeout(job_id="test-job"),
        PacingComplete(job_id="test-job"),
        EscalationNeeded(
            job_id="test-job", sheet_num=6, reason="low confidence",
            options=["retry", "accept", "skip"],
        ),
        EscalationResolved(job_id="test-job", sheet_num=6, decision="retry"),
        EscalationTimeout(job_id="test-job", sheet_num=6),
        PauseJob(job_id="test-job"),
        ResumeJob(job_id="test-job"),
        CancelJob(job_id="test-job"),
        ConfigReloaded(job_id="test-job", new_config={"retry": {"max_retries": 5}}),
        ShutdownRequested(graceful=True),
        ProcessExited(job_id="test-job", sheet_num=7, pid=12345, exit_code=137),
        ResourceAnomaly(severity="critical", metric="memory", value=95.0),
        DispatchRetry(),
    ]


ALL_EVENTS = _make_all_events()
ALL_EVENT_TYPES = [type(e) for e in ALL_EVENTS]


# =============================================================================
# Construction tests
# =============================================================================


class TestSheetAttemptResult:
    """Test the central musician-to-conductor event."""

    def test_minimal_construction(self) -> None:
        result = SheetAttemptResult(
            job_id="my-job", sheet_num=1,
            instrument_name="claude-code", attempt=1,
        )
        assert result.job_id == "my-job"
        assert result.sheet_num == 1
        assert result.instrument_name == "claude-code"
        assert result.attempt == 1
        assert result.execution_success is True  # default
        assert result.rate_limited is False  # default
        assert result.cost_usd == 0.0  # default

    def test_full_construction(self) -> None:
        result = SheetAttemptResult(
            job_id="research", sheet_num=3,
            instrument_name="gemini-cli", attempt=2,
            execution_success=True, exit_code=0,
            duration_seconds=45.2,
            validations_passed=4, validations_total=5,
            validation_pass_rate=80.0,
            validation_details={"file_exists": True, "content_match": False},
            error_classification=None, error_message=None,
            rate_limited=False,
            cost_usd=0.42, input_tokens=1500, output_tokens=3200,
            model_used="gemini-2.5-pro",
            stdout_tail="Last 10KB...", stderr_tail="",
        )
        assert result.validation_pass_rate == 80.0
        assert result.cost_usd == 0.42
        assert result.model_used == "gemini-2.5-pro"

    def test_rate_limited_is_not_failure(self) -> None:
        """Rate limit is a tempo change, not a failure.

        The musician reports rate_limited=True. The baton does NOT consume
        retry budget — it re-queues the sheet for when the instrument recovers.
        """
        result = SheetAttemptResult(
            job_id="job", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False, rate_limited=True,
        )
        assert result.rate_limited is True
        assert result.execution_success is False

    def test_timestamp_auto_populated(self) -> None:
        before = time.time()
        result = SheetAttemptResult(
            job_id="job", sheet_num=1, instrument_name="x", attempt=1,
        )
        after = time.time()
        assert before <= result.timestamp <= after


class TestSheetSkipped:
    def test_construction(self) -> None:
        event = SheetSkipped(
            job_id="job", sheet_num=2, reason="start_sheet override",
        )
        assert event.reason == "start_sheet override"

    def test_timestamp_auto(self) -> None:
        event = SheetSkipped(job_id="j", sheet_num=1, reason="skip_when")
        assert event.timestamp > 0


class TestRateLimitEvents:
    def test_hit_construction(self) -> None:
        event = RateLimitHit(
            instrument="gemini-cli", wait_seconds=3600.0,
            job_id="job", sheet_num=5,
        )
        assert event.instrument == "gemini-cli"
        assert event.wait_seconds == 3600.0

    def test_expired_construction(self) -> None:
        event = RateLimitExpired(instrument="claude-code")
        assert event.instrument == "claude-code"


class TestTimerEvents:
    def test_retry_due(self) -> None:
        event = RetryDue(job_id="j", sheet_num=3)
        assert event.job_id == "j"

    def test_stale_check(self) -> None:
        event = StaleCheck(job_id="j", sheet_num=5)
        assert event.sheet_num == 5

    def test_cron_tick(self) -> None:
        event = CronTick(entry_name="weekly-qa", score_path="scores/qa.yaml")
        assert event.entry_name == "weekly-qa"

    def test_job_timeout(self) -> None:
        event = JobTimeout(job_id="long-job")
        assert event.job_id == "long-job"

    def test_pacing_complete(self) -> None:
        event = PacingComplete(job_id="paced")
        assert event.job_id == "paced"


class TestEscalationEvents:
    def test_needed_with_options(self) -> None:
        event = EscalationNeeded(
            job_id="j", sheet_num=5, reason="low confidence",
            options=["retry", "accept", "skip"],
        )
        assert len(event.options) == 3
        assert "retry" in event.options

    def test_needed_default_empty_options(self) -> None:
        event = EscalationNeeded(
            job_id="j", sheet_num=5, reason="ambiguous",
        )
        assert event.options == []

    def test_resolved(self) -> None:
        event = EscalationResolved(
            job_id="j", sheet_num=5, decision="accept",
        )
        assert event.decision == "accept"

    def test_timeout(self) -> None:
        event = EscalationTimeout(job_id="j", sheet_num=5)
        assert event.job_id == "j"


class TestExternalCommandEvents:
    def test_pause(self) -> None:
        event = PauseJob(job_id="j")
        assert event.job_id == "j"

    def test_resume_without_config(self) -> None:
        event = ResumeJob(job_id="j")
        assert event.new_config is None

    def test_resume_with_config(self) -> None:
        event = ResumeJob(
            job_id="j",
            new_config={"retry": {"max_retries": 10}},
        )
        assert event.new_config is not None
        assert event.new_config["retry"]["max_retries"] == 10

    def test_cancel(self) -> None:
        event = CancelJob(job_id="j")
        assert event.job_id == "j"

    def test_config_reloaded(self) -> None:
        event = ConfigReloaded(
            job_id="j", new_config={"backend": {"type": "gemini-cli"}},
        )
        assert event.new_config["backend"]["type"] == "gemini-cli"

    def test_shutdown_graceful_default(self) -> None:
        event = ShutdownRequested()
        assert event.graceful is True

    def test_shutdown_not_graceful(self) -> None:
        event = ShutdownRequested(graceful=False)
        assert event.graceful is False


class TestObserverEvents:
    def test_process_exited(self) -> None:
        event = ProcessExited(
            job_id="j", sheet_num=3, pid=12345, exit_code=137,
        )
        assert event.pid == 12345
        assert event.exit_code == 137

    def test_process_exited_no_exit_code(self) -> None:
        event = ProcessExited(job_id="j", sheet_num=3, pid=99999)
        assert event.exit_code is None

    def test_resource_anomaly(self) -> None:
        event = ResourceAnomaly(
            severity="critical", metric="memory", value=95.0,
        )
        assert event.severity == "critical"
        assert event.metric == "memory"


class TestInternalEvents:
    def test_dispatch_retry(self) -> None:
        event = DispatchRetry()
        assert event.timestamp > 0


# =============================================================================
# Immutability tests — frozen dataclasses cannot be mutated
# =============================================================================


@pytest.mark.parametrize("event", ALL_EVENTS, ids=[type(e).__name__ for e in ALL_EVENTS])
def test_event_is_immutable(event: BatonEvent) -> None:
    """All BatonEvent types are frozen — mutation raises FrozenInstanceError."""
    with pytest.raises(FrozenInstanceError):
        event.timestamp = 0.0  # type: ignore[misc]


# =============================================================================
# Union type coverage — verify all types are in the union
# =============================================================================


def test_union_type_covers_all_event_classes() -> None:
    """BatonEvent union type must include every event class defined in events.py."""
    # Get all event classes from the module
    import marianne.daemon.baton.events as mod
    from dataclasses import is_dataclass

    event_classes = {
        name: obj for name, obj in vars(mod).items()
        if isinstance(obj, type) and is_dataclass(obj) and name != "BatonEvent"
    }

    # BatonEvent is a type alias (Union), get its args
    from typing import get_args
    union_args = set(get_args(mod.BatonEvent))

    for name, cls in event_classes.items():
        assert cls in union_args, (
            f"{name} is a dataclass in events.py but not in the BatonEvent union type"
        )


def test_all_event_types_have_timestamp(event: None = None) -> None:
    """Every BatonEvent type must have a timestamp field with auto-default."""
    for e in ALL_EVENTS:
        assert hasattr(e, "timestamp"), f"{type(e).__name__} missing timestamp"
        assert isinstance(e.timestamp, float), f"{type(e).__name__}.timestamp not float"
        assert e.timestamp > 0, f"{type(e).__name__}.timestamp not positive"


# =============================================================================
# to_observer_event tests — EventBus integration
# =============================================================================


@pytest.mark.parametrize("event", ALL_EVENTS, ids=[type(e).__name__ for e in ALL_EVENTS])
def test_to_observer_event_returns_valid_dict(event: BatonEvent) -> None:
    """Every BatonEvent type converts to a valid ObserverEvent dict."""
    result = to_observer_event(event)

    # Must have all required ObserverEvent fields
    assert "job_id" in result
    assert "sheet_num" in result
    assert "event" in result
    assert "data" in result
    assert "timestamp" in result

    # Types
    assert isinstance(result["job_id"], str)
    assert isinstance(result["sheet_num"], int)
    assert isinstance(result["event"], str)
    assert isinstance(result["data"], dict) or result["data"] is None
    assert isinstance(result["timestamp"], float)


@pytest.mark.parametrize("event", ALL_EVENTS, ids=[type(e).__name__ for e in ALL_EVENTS])
def test_to_observer_event_uses_baton_namespace(event: BatonEvent) -> None:
    """All baton events use the baton.* event namespace."""
    result = to_observer_event(event)
    assert result["event"].startswith("baton."), (
        f"{type(event).__name__} event name '{result['event']}' "
        "does not start with 'baton.'"
    )


def test_sheet_attempt_result_observer_event_data() -> None:
    """SheetAttemptResult carries rich data in its observer event."""
    event = SheetAttemptResult(
        job_id="research", sheet_num=3,
        instrument_name="gemini-cli", attempt=2,
        execution_success=True, cost_usd=0.42,
        validation_pass_rate=80.0,
        model_used="gemini-2.5-pro",
        duration_seconds=30.5,
    )
    result = to_observer_event(event)
    assert result["event"] == "baton.sheet.attempt_result"
    assert result["data"]["instrument"] == "gemini-cli"
    assert result["data"]["model"] == "gemini-2.5-pro"
    assert result["data"]["cost_usd"] == 0.42
    assert result["data"]["attempt"] == 2


def test_rate_limit_hit_observer_event_data() -> None:
    """RateLimitHit observer event includes instrument and wait time."""
    event = RateLimitHit(
        instrument="claude-code", wait_seconds=3600.0,
        job_id="job", sheet_num=5,
    )
    result = to_observer_event(event)
    assert result["event"] == "baton.rate_limit.active"
    assert result["data"]["instrument"] == "claude-code"
    assert result["data"]["estimated_seconds"] == 3600.0


def test_escalation_observer_event_data() -> None:
    """EscalationNeeded includes reason and options for the dashboard."""
    event = EscalationNeeded(
        job_id="j", sheet_num=5,
        reason="low confidence",
        options=["retry", "accept", "skip"],
    )
    result = to_observer_event(event)
    assert result["event"] == "baton.fermata"
    assert result["data"]["reason"] == "low confidence"
    assert len(result["data"]["options"]) == 3


def test_cron_tick_observer_event_data() -> None:
    """CronTick observer event includes entry name and score path."""
    event = CronTick(entry_name="nightly", score_path="scores/cleanup.yaml")
    result = to_observer_event(event)
    assert result["event"] == "baton.cron.fired"
    assert result["data"]["entry_name"] == "nightly"
    assert result["data"]["score_path"] == "scores/cleanup.yaml"


def test_shutdown_observer_event_data() -> None:
    """ShutdownRequested observer event includes graceful flag."""
    event = ShutdownRequested(graceful=False)
    result = to_observer_event(event)
    assert result["event"] == "baton.shutdown.requested"
    assert result["data"]["graceful"] is False


# =============================================================================
# Adversarial tests
# =============================================================================


@pytest.mark.adversarial
def test_sheet_attempt_result_with_empty_strings() -> None:
    """SheetAttemptResult handles empty string fields gracefully."""
    result = SheetAttemptResult(
        job_id="", sheet_num=0, instrument_name="", attempt=0,
        stdout_tail="", stderr_tail="",
    )
    assert result.job_id == ""
    obs = to_observer_event(result)
    assert obs["job_id"] == ""


@pytest.mark.adversarial
def test_sheet_attempt_result_with_large_output() -> None:
    """SheetAttemptResult can carry large stdout_tail without issue."""
    large_output = "x" * 100_000
    result = SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="x", attempt=1,
        stdout_tail=large_output,
    )
    assert len(result.stdout_tail) == 100_000


@pytest.mark.adversarial
def test_negative_cost_is_accepted() -> None:
    """Negative cost isn't validated at the event level — baton decides."""
    result = SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="x", attempt=1,
        cost_usd=-1.0,
    )
    assert result.cost_usd == -1.0


@pytest.mark.adversarial
def test_validation_details_with_nested_data() -> None:
    """Validation details can carry complex nested structures."""
    details = {
        "file_exists": {"path": "/workspace/out.md", "passed": True},
        "content_match": {"pattern": "class Foo", "passed": False, "line": 42},
        "composite": {"children": [{"type": "file_count", "count": 3}]},
    }
    result = SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="x", attempt=1,
        validation_details=details,
    )
    assert result.validation_details is not None
    assert result.validation_details["composite"]["children"][0]["count"] == 3
