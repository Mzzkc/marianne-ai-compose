"""#195: structured error classification for the baton.

The baton's `_classify_error` (musician.py) hand-rolled error classification
with 7 substring patterns, emitting one of three buckets (AUTH_FAILURE /
TRANSIENT / EXECUTION_ERROR) into the OVERLOADED `error_classification` field.
That field is consumed by two fragile matchers: an exact `== "AUTH_FAILURE"`
that drives the per-instrument auth fallback chain (core.py:1352) and a `.upper()`
substring labeler for the exhaustion reason (core.py:823).

Per the 4-model lab (UNANIMOUS on Option A): swap in the mature, tested
`ErrorClassifier` (E0xx-E9xx taxonomy, signal/timeout/pattern/exit-code
sub-classifiers) under the hood, but PRESERVE the exact three bucket strings via
a `_CATEGORY_TO_BUCKET` map so BOTH consumers stay bit-identical (zero
recovery-semantics change), and ADD an additive `error_code: str | None` to
`SheetAttemptResult` carrying the structured E-code. Verified against the live
tree: the ONLY value-consumer of `error_classification` is core.py:1352's exact
AUTH match — nothing distinguishes TRANSIENT vs EXECUTION_ERROR — so the
TIMEOUT/NETWORK -> TRANSIENT mapping (3-of-4 reviewers) is provably
behaviour-neutral, with the precise diagnostic now carried by `error_code`.
Bucket strings/E-codes below are pinned to the REAL classifier output (probed).
"""

from __future__ import annotations

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.errors.codes import ErrorCategory
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _CATEGORY_TO_BUCKET, _classify_error


def _exec(
    *,
    success: bool = False,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = 1,
    exit_signal: int | None = None,
    exit_reason: str = "completed",
    rate_limited: bool = False,
    error_message: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        success=success,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.0,
        exit_code=exit_code,
        exit_signal=exit_signal,
        exit_reason=exit_reason,  # type: ignore[arg-type]
        rate_limited=rate_limited,
        error_message=error_message,
    )


# ── The category -> bucket mapping (the design decision, deterministic) ────


class TestCategoryToBucket:
    @pytest.mark.parametrize(
        "category,expected",
        [
            (ErrorCategory.AUTH, "AUTH_FAILURE"),
            (ErrorCategory.RATE_LIMIT, "TRANSIENT"),
            (ErrorCategory.TRANSIENT, "TRANSIENT"),
            (ErrorCategory.NETWORK, "TRANSIENT"),
            (ErrorCategory.TIMEOUT, "TRANSIENT"),
            (ErrorCategory.SIGNAL, "TRANSIENT"),
            (ErrorCategory.VALIDATION, "EXECUTION_ERROR"),
            (ErrorCategory.FATAL, "EXECUTION_ERROR"),
            (ErrorCategory.CONFIGURATION, "EXECUTION_ERROR"),
            (ErrorCategory.PREFLIGHT, "EXECUTION_ERROR"),
            (ErrorCategory.ESCALATION, "EXECUTION_ERROR"),
        ],
    )
    def test_mapping(self, category: ErrorCategory, expected: str) -> None:
        assert _CATEGORY_TO_BUCKET[category] == expected

    def test_only_auth_maps_to_auth_failure(self) -> None:
        # Consumer 1 (core.py:1352) exact-matches "AUTH_FAILURE"; nothing else
        # may produce it.
        auth_buckets = [
            c for c, b in _CATEGORY_TO_BUCKET.items() if b == "AUTH_FAILURE"
        ]
        assert auth_buckets == [ErrorCategory.AUTH]

    def test_every_category_covered(self) -> None:
        # No category falls through to an unexpected default.
        for category in ErrorCategory:
            assert category in _CATEGORY_TO_BUCKET


# ── _classify_error end-to-end (pinned to real ErrorClassifier output) ─────


class TestClassifyError:
    def test_success_returns_none(self) -> None:
        c = _classify_error(_exec(success=True, exit_code=0))
        assert (c.classification, c.message, c.error_code) == (None, None, None)

    def test_rate_limited_returns_none(self) -> None:
        # Rate limits are NOT errors — early return before the classifier.
        c = _classify_error(_exec(rate_limited=True, stderr="rate limit exceeded"))
        assert (c.classification, c.message, c.error_code) == (None, None, None)

    def test_auth_failure(self) -> None:
        c = _classify_error(_exec(stderr="HTTP 401 Unauthorized", exit_code=1))
        assert c.classification == "AUTH_FAILURE"
        assert c.error_code == "E502"  # ErrorCode.BACKEND_AUTH

    def test_auth_failure_api_key(self) -> None:
        c = _classify_error(_exec(stderr="invalid_api_key provided", exit_code=1))
        assert c.classification == "AUTH_FAILURE"

    def test_signal_kill_is_transient(self) -> None:
        c = _classify_error(_exec(exit_code=None, exit_signal=9))
        assert c.classification == "TRANSIENT"
        assert c.error_code == "E002"  # EXECUTION_KILLED

    def test_timeout_is_transient(self) -> None:
        c = _classify_error(_exec(exit_code=None, exit_reason="timeout"))
        assert c.classification == "TRANSIENT"
        assert c.error_code == "E001"  # EXECUTION_TIMEOUT

    def test_network_is_transient(self) -> None:
        c = _classify_error(_exec(stderr="connection refused", exit_code=1))
        assert c.classification == "TRANSIENT"

    def test_generic_failure_has_code(self) -> None:
        c = _classify_error(_exec(stderr="something broke", exit_code=1))
        # Generic exit-1 classifies as transient/unknown (E009) — bucketed
        # TRANSIENT, which is behaviourally identical to EXECUTION_ERROR at
        # every consumer. The point is it now carries a structured code.
        assert c.classification in ("TRANSIENT", "EXECUTION_ERROR")
        assert c.error_code is not None and c.error_code.startswith("E")

    def test_message_prefers_backend_error_message(self) -> None:
        c = _classify_error(
            _exec(stderr="boom", exit_code=1, error_message="backend said boom")
        )
        assert c.message == "backend said boom"


# ── Cross-consumer invariants (the landmine) ──────────────────────────────


class TestCrossConsumerSafety:
    def test_dormant_substring_arms_stay_dormant(self) -> None:
        # core.py:823 substring-matches TIMEOUT/CRASH/STALE/AUTH on the bucket.
        # The non-auth buckets must contain NONE of TIMEOUT/CRASH/STALE so they
        # keep falling through to "execution_failed" exactly as today.
        for bucket in ("TRANSIENT", "EXECUTION_ERROR"):
            up = bucket.upper()
            assert "TIMEOUT" not in up
            assert "CRASH" not in up
            assert "STALE" not in up
            assert "AUTH" not in up

    def test_auth_failure_contains_auth_substring(self) -> None:
        # Consumer 1 (exact) AND consumer 2 (substring) both key off AUTH.
        assert "AUTH" in "AUTH_FAILURE".upper()


# ── SheetAttemptResult.error_code is additive + backward compatible ───────


class TestSheetAttemptResultErrorCode:
    def test_defaults_none(self) -> None:
        r = SheetAttemptResult(job_id="j", sheet_num=1, instrument_name="x", attempt=1)
        assert r.error_code is None

    def test_accepts_value(self) -> None:
        r = SheetAttemptResult(
            job_id="j", sheet_num=1, instrument_name="x", attempt=1,
            error_classification="AUTH_FAILURE", error_code="E502",
        )
        assert r.error_code == "E502"
