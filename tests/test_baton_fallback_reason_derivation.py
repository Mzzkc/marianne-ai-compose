"""TDD tests for fallback-reason derivation in ``_handle_exhaustion``.

When a sheet's retry/completion budget exhausts and the baton advances to
the next instrument in the fallback chain, the emitted ``InstrumentFallback``
event (and the ``instrument_fallback_history`` entry on the sheet) carries
a ``reason`` string.

Historically that reason was hardcoded to ``"rate_limit_exhausted"`` in
``src/marianne/daemon/baton/core.py`` regardless of why exhaustion
actually happened (validation failure, execution error, timeout, …). This
produced misleading diagnostics in ``mzt status`` output and in the
baton's event bus — fallbacks triggered by a 0% validation pass rate were
labelled as rate limits.

These tests lock in a derivation that reflects the *actual* last attempt:

- rate-limited final attempt           → ``"rate_limit_exhausted"``
- execution succeeded, 0% validation   → ``"validation_failed"``
- execution succeeded, partial pass    → ``"validation_incomplete"``
- execution failed, TIMEOUT class      → ``"execution_timeout"``
- execution failed, CRASH class        → ``"execution_crashed"``
- execution failed, STALE class        → ``"execution_stale"``
- execution failed, AUTH class         → ``"auth_failure"``
- execution failed, anything else      → ``"execution_failed"``
- no attempt_results at all            → ``"exhausted"`` (generic fallback)

The reason also propagates to ``sheet.instrument_fallback_history[-1]["reason"]``
via ``advance_fallback`` — downstream consumers (status UI, observability)
see the same value as the emitted event.
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import InstrumentFallback, SheetAttemptResult
from marianne.daemon.baton.state import (
    InstrumentState,
    SheetExecutionState,
)


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    fallbacks: list[str] | None = None,
    max_retries: int = 1,
) -> SheetExecutionState:
    return SheetExecutionState(
        sheet_num=num,
        instrument_name=instrument,
        fallback_chain=fallbacks or ["gemini-cli"],
        max_retries=max_retries,
    )


def _register(baton: BatonCore, *names: str) -> None:
    for name in names:
        baton._instruments[name] = InstrumentState(name=name, max_concurrent=4)


def _push_attempt(
    sheet: SheetExecutionState,
    *,
    execution_success: bool,
    validation_pass_rate: float = 0.0,
    rate_limited: bool = False,
    error_classification: str | None = None,
    error_message: str | None = None,
    instrument_name: str = "claude-code",
) -> None:
    """Append a SheetAttemptResult to sheet.attempt_results.

    Uses the public record_attempt path so that ``normal_attempts`` is
    maintained consistently, matching what _handle_attempt_result does.
    """
    result = SheetAttemptResult(
        job_id="j1",
        sheet_num=sheet.sheet_num,
        instrument_name=instrument_name,
        attempt=sheet.normal_attempts + 1,
        execution_success=execution_success,
        validation_pass_rate=validation_pass_rate,
        rate_limited=rate_limited,
        error_classification=error_classification,
        error_message=error_message,
    )
    sheet.record_attempt(result)


def _exhaust_and_extract(
    baton: BatonCore, sheet: SheetExecutionState
) -> InstrumentFallback:
    """Run ``_handle_exhaustion`` and return the single emitted event."""
    baton.register_job("j1", {sheet.sheet_num: sheet}, {sheet.sheet_num: []})
    # Ensure both ends of the fallback chain are registered so the handler
    # doesn't short-circuit on "unknown instrument".
    _register(baton, sheet.instrument_name or "claude-code", *sheet.fallback_chain)
    asyncio.run(baton._handle_exhaustion("j1", sheet.sheet_num, sheet))
    assert len(baton._fallback_events) == 1, (
        f"Expected exactly one fallback event, got {baton._fallback_events!r}"
    )
    ev = baton._fallback_events[0]
    assert isinstance(ev, InstrumentFallback)
    return ev


class TestFallbackReasonDerivation:
    """_handle_exhaustion derives the fallback reason from the last attempt."""

    def test_validation_failed_when_last_attempt_zero_pass_rate(self) -> None:
        """Execution succeeded but 0% validation → ``validation_failed``.

        This is the opencode-bogus-model case: exit 0, no file created,
        validator returns pass_rate=0.0. Budget exhausts, fallback fires.
        The reason must reflect "your validations didn't pass", not a
        rate limit that never happened.
        """
        sheet = _make_sheet(max_retries=1)
        _push_attempt(sheet, execution_success=True, validation_pass_rate=0.0)

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "validation_failed", (
            f"Expected 'validation_failed' after 0% pass_rate, got {ev.reason!r}"
        )

    def test_validation_incomplete_when_last_attempt_partial_pass(self) -> None:
        """Execution succeeded with partial validation → ``validation_incomplete``."""
        sheet = _make_sheet(max_retries=1)
        # Simulate completion mode exhaustion: partial validation.
        _push_attempt(sheet, execution_success=True, validation_pass_rate=50.0)
        # completion_attempts would be maxed in reality; _handle_exhaustion
        # only reads attempt_results[-1] for the reason, so the specific
        # budget counter that triggered exhaustion doesn't matter.

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "validation_incomplete", (
            f"Expected 'validation_incomplete' after partial pass, got {ev.reason!r}"
        )

    def test_rate_limit_exhausted_when_last_attempt_rate_limited(self) -> None:
        """Last attempt rate-limited + failed → ``rate_limit_exhausted``.

        This is the only case the legacy hardcoded label was accurate for.
        After the fix it must still be correct.
        """
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            rate_limited=True,
            error_classification="RATE_LIMIT",
        )
        # Rate-limited attempts don't consume normal_attempts, so force
        # the exhaustion precondition artificially.
        sheet.normal_attempts = sheet.max_retries

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "rate_limit_exhausted"

    def test_execution_timeout_when_classification_is_timeout(self) -> None:
        """Exec failed, TIMEOUT classification → ``execution_timeout``."""
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            error_classification="TIMEOUT",
        )

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "execution_timeout"

    def test_execution_crashed_when_classification_is_process_crash(self) -> None:
        """Exec failed, PROCESS_CRASH classification → ``execution_crashed``."""
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            error_classification="PROCESS_CRASH",
        )

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "execution_crashed"

    def test_execution_stale_when_classification_is_stale(self) -> None:
        """Exec failed, STALE classification → ``execution_stale``."""
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            error_classification="STALE",
        )

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "execution_stale"

    def test_execution_failed_fallback_for_unknown_classification(self) -> None:
        """Exec failed, unknown classification → ``execution_failed``."""
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            error_classification="TRANSIENT",
        )

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "execution_failed"

    def test_execution_failed_when_classification_is_none(self) -> None:
        """Exec failed with no classification → ``execution_failed``."""
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            error_classification=None,
        )

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "execution_failed"

    def test_exhausted_when_no_attempt_results(self) -> None:
        """No attempt history → ``exhausted`` (generic).

        Defensive fallback for code paths that reach ``_handle_exhaustion``
        without first recording an attempt (e.g., paused/resumed sheets
        with cleared history). Never silently mislabel as rate-limited.
        """
        sheet = _make_sheet(max_retries=1)
        sheet.normal_attempts = sheet.max_retries

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == "exhausted"


class TestFallbackReasonPropagatesToHistory:
    """The derived reason must also land in ``instrument_fallback_history``
    so the status UI (``mzt status``) shows the same label."""

    def test_history_entry_carries_derived_reason(self) -> None:
        sheet = _make_sheet(max_retries=1)
        _push_attempt(sheet, execution_success=True, validation_pass_rate=0.0)

        baton = BatonCore()
        _exhaust_and_extract(baton, sheet)

        assert len(sheet.instrument_fallback_history) == 1
        entry = sheet.instrument_fallback_history[-1]
        assert entry["reason"] == "validation_failed", (
            f"history reason must match event reason, got {entry['reason']!r}"
        )
        assert entry["from"] == "claude-code"
        assert entry["to"] == "gemini-cli"


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("AUTH_FAILURE", "auth_failure"),
        ("auth", "auth_failure"),
        ("AUTH", "auth_failure"),
    ],
)
class TestAuthFailureReasonNormalization:
    """Auth-failure classifications map to the canonical ``auth_failure``
    reason, matching the spelling used elsewhere in the baton."""

    def test_auth_variants_produce_auth_failure_reason(
        self, classification: str, expected: str
    ) -> None:
        sheet = _make_sheet(max_retries=1)
        _push_attempt(
            sheet,
            execution_success=False,
            error_classification=classification,
        )

        baton = BatonCore()
        ev = _exhaust_and_extract(baton, sheet)

        assert ev.reason == expected
