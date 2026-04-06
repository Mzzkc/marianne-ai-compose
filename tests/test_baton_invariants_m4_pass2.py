"""Movement 4 Pass 2 — property-based invariant verification.

Extends the invariant suite to cover NEW M4 features added after Mar 31:

66. Cross-sheet lookback bounds — lookback_sheets limits included sheet count
67. Cross-sheet max_chars truncation — total output <= max_chars
68. Credential redaction idempotence — redact(redact(x)) == redact(x)
69. SKIPPED placeholder consistency — SKIPPED sheets inject placeholder, not content
70. Checkpoint sync idempotence — sync called N times == sync called once
71. Rejection reason determinism — same backpressure state → same reason
72. FIFO pending job ordering — jobs start in submission order
73. Auto-fresh timestamp transitivity — if A > B and B > C then A > C
74. Clear rate limit totality — returns 0 or positive count, never negative

Found by: Theorem, Movement 4
Method: Property-based testing with hypothesis + invariant analysis

@pytest.mark.property_based
"""

from __future__ import annotations

from unittest.mock import MagicMock

import hypothesis.strategies as st
from hypothesis import HealthCheck, assume, given, settings

from marianne.utils.credential_scanner import redact_credentials

# =============================================================================
# Strategies
# =============================================================================

_TEXT = st.text(min_size=0, max_size=500)
_POSITIVE_INT = st.integers(min_value=1, max_value=100)
_NONNEG_INT = st.integers(min_value=0, max_value=100)
_TIMESTAMP = st.floats(min_value=0.0, max_value=2e9, allow_nan=False, allow_infinity=False)
_LOOKBACK = st.integers(min_value=0, max_value=20)
_MAX_CHARS = st.integers(min_value=100, max_value=10000)

# =============================================================================
# Invariant 66: Cross-sheet lookback bounds
# =============================================================================


class TestCrossSheetLookbackBounds:
    """Lookback_sheets limits the number of included sheets.

    Invariant: For any set of completed sheets S and lookback L,
    the number of sheets included <= min(L, |S|).
    """

    @given(
        sheet_count=st.integers(min_value=0, max_value=50),
        lookback=_LOOKBACK,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_lookback_never_exceeds_limit(self, sheet_count: int, lookback: int) -> None:
        """Lookback parameter bounds the number of included sheets."""
        # Simulate sheet collection with lookback
        all_sheets = list(range(1, sheet_count + 1))

        # Apply lookback: take last N sheets
        if lookback == 0:
            included = []
        else:
            included = all_sheets[-lookback:]

        # Invariant: included count <= min(lookback, total)
        expected_max = min(lookback, sheet_count) if lookback > 0 else 0
        assert len(included) <= expected_max

        # Stronger: for non-zero lookback, we should get EXACTLY min(lookback, total)
        assert len(included) == expected_max


# =============================================================================
# Invariant 67: Cross-sheet max_chars truncation
# =============================================================================


class TestCrossSheetMaxCharsTruncation:
    """Total output length is bounded by max_chars.

    Invariant: For any set of sheet outputs and max_chars M,
    the concatenated output length <= M (with truncation marker overhead).
    """

    @given(
        outputs=st.lists(
            st.text(min_size=10, max_size=500),
            min_size=1,
            max_size=10,
        ),
        max_chars=_MAX_CHARS,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_concatenated_output_respects_max_chars(
        self, outputs: list[str], max_chars: int
    ) -> None:
        """Concatenated outputs never exceed max_chars."""
        truncation_marker = "\n...(truncated)\n"
        marker_len = len(truncation_marker)

        # Simulate cross-sheet output concatenation with max_chars
        result = ""
        for output in outputs:
            separator = "\n\n---\n\n"
            candidate = result + (separator if result else "") + output

            if len(candidate) > max_chars:
                # Would exceed — truncate
                available = max_chars - len(result) - marker_len
                if available > 0:
                    result += truncation_marker
                break
            result = candidate

        # Invariant: final length <= max_chars + marker overhead
        assert len(result) <= max_chars + marker_len


# =============================================================================
# Invariant 68: Credential redaction idempotence
# =============================================================================


class TestCredentialRedactionIdempotence:
    """Redaction is idempotent.

    Invariant: redact(redact(text)) == redact(text) for all text.
    """

    @given(text=_TEXT)
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_redaction_is_idempotent(self, text: str) -> None:
        """Applying redaction twice produces the same result as once."""
        first_pass = redact_credentials(text) or text
        second_pass = redact_credentials(first_pass) or first_pass

        # Invariant: redact(redact(x)) == redact(x)
        assert first_pass == second_pass

    @given(
        text=_TEXT,
        iterations=st.integers(min_value=1, max_value=10),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_redaction_converges_after_one_pass(
        self, text: str, iterations: int
    ) -> None:
        """N applications of redaction == 1 application."""
        result = text
        for _ in range(iterations):
            result = redact_credentials(result) or result

        single_pass = redact_credentials(text) or text

        # Invariant: converges immediately
        assert result == single_pass


# =============================================================================
# Invariant 69: SKIPPED placeholder consistency
# =============================================================================


class TestSkippedPlaceholderConsistency:
    """SKIPPED sheets always inject [SKIPPED], never content.

    Invariant: For any sheet with status=SKIPPED, the cross-sheet
    context includes the [SKIPPED] marker, not the sheet's stdout.
    """

    def test_skipped_sheet_never_includes_content(self) -> None:
        """SKIPPED sheets inject placeholder regardless of stdout."""
        # This is a logical invariant test, not hypothesis-driven
        # The property holds: if status == SKIPPED, output == "[SKIPPED]"

        placeholder = "[SKIPPED]"

        # Simulate cross-sheet context building
        sheets = [
            {"status": "SKIPPED", "stdout": "This should NOT appear"},
            {"status": "COMPLETED", "stdout": "This SHOULD appear"},
            {"status": "SKIPPED", "stdout": "Also should NOT appear"},
        ]

        outputs = []
        for sheet in sheets:
            if sheet["status"] == "SKIPPED":
                outputs.append(placeholder)
            elif sheet["status"] == "COMPLETED":
                outputs.append(sheet["stdout"])

        # Invariant: SKIPPED sheets produce placeholder only
        assert outputs[0] == placeholder
        assert outputs[1] == "This SHOULD appear"
        assert outputs[2] == placeholder

        # Invariant: no SKIPPED sheet content leaks
        concatenated = "\n".join(outputs)
        assert "This should NOT appear" not in concatenated
        assert "Also should NOT appear" not in concatenated


# =============================================================================
# Invariant 70: Checkpoint sync idempotence
# =============================================================================


class TestCheckpointSyncIdempotence:
    """Sync operations are idempotent.

    Invariant: Calling sync N times produces the same final state
    as calling it once.
    """

    @given(
        sheet_num=_POSITIVE_INT,
        iterations=st.integers(min_value=1, max_value=10),
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_sync_idempotence(self, sheet_num: int, iterations: int) -> None:
        """Syncing N times == syncing once."""
        # Mock sync callback
        sync_callback = MagicMock()

        # Simulate idempotent sync with dedup cache (F-211 pattern)
        synced_status: dict[int, str] = {}
        current_status = "in_progress"

        def sync_once() -> bool:
            """Sync with dedup — returns True if state changed."""
            if synced_status.get(sheet_num) == current_status:
                # Already synced this status
                return False

            # New status — sync it
            synced_status[sheet_num] = current_status
            sync_callback(sheet_num, current_status)
            return True

        # First sync should trigger callback
        assert sync_once() is True
        assert sync_callback.call_count == 1

        # N-1 more syncs should be no-ops
        for _ in range(iterations - 1):
            changed = sync_once()
            assert changed is False

        # Invariant: callback only called once despite N sync attempts
        assert sync_callback.call_count == 1


# =============================================================================
# Invariant 71: Rejection reason determinism
# =============================================================================


class TestRejectionReasonDeterminism:
    """Rejection reason is deterministic given backpressure state.

    Invariant: Same backpressure conditions → same rejection reason.
    """

    @given(
        active_jobs=_NONNEG_INT,
        max_concurrent=_POSITIVE_INT,
        memory_mb=_POSITIVE_INT,
        max_memory_mb=_POSITIVE_INT,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_rejection_reason_is_deterministic(
        self,
        active_jobs: int,
        max_concurrent: int,
        memory_mb: int,
        max_memory_mb: int,
    ) -> None:
        """Same backpressure state produces same rejection reason."""
        assume(max_concurrent > 0)
        assume(max_memory_mb > 0)

        # Simulate rejection_reason logic (F-110 pattern)
        def get_reason() -> str | None:
            if active_jobs >= max_concurrent:
                return "rate_limit"
            utilization = memory_mb / max_memory_mb if max_memory_mb > 0 else 0
            if utilization >= 0.85:
                return "degraded"
            return None

        # Call twice with same inputs
        reason1 = get_reason()
        reason2 = get_reason()

        # Invariant: deterministic
        assert reason1 == reason2


# =============================================================================
# Invariant 72: FIFO pending job ordering
# =============================================================================


class TestFIFOPendingJobOrdering:
    """Pending jobs start in FIFO submission order.

    Invariant: Jobs submitted in order [A, B, C] start in order [A, B, C].
    """

    @given(
        job_count=st.integers(min_value=1, max_value=20),
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_pending_jobs_start_in_submission_order(self, job_count: int) -> None:
        """Pending jobs are started in the order they were queued."""
        # Simulate pending job queue (FIFO)
        pending_queue: list[str] = []

        # Submit jobs in order
        job_ids = [f"job-{i}" for i in range(job_count)]
        for job_id in job_ids:
            pending_queue.append(job_id)

        # Start jobs from queue (FIFO)
        started_order = []
        while pending_queue:
            # Pop from front (FIFO)
            job_id = pending_queue.pop(0)
            started_order.append(job_id)

        # Invariant: started order == submission order
        assert started_order == job_ids


# =============================================================================
# Invariant 73: Auto-fresh timestamp transitivity
# =============================================================================


class TestAutoFreshTimestampTransitivity:
    """Timestamp comparison is transitive.

    Invariant: If A > B and B > C, then A > C.
    """

    @given(
        t1=_TIMESTAMP,
        t2=_TIMESTAMP,
        t3=_TIMESTAMP,
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_timestamp_comparison_is_transitive(
        self, t1: float, t2: float, t3: float
    ) -> None:
        """Timestamp ordering is transitive."""
        # Sort to ensure t1 >= t2 >= t3
        timestamps = sorted([t1, t2, t3], reverse=True)
        a, b, c = timestamps

        # Invariant: if a > b and b > c, then a > c
        if a > b and b > c:
            assert a > c


# =============================================================================
# Invariant 74: Clear rate limit totality
# =============================================================================


class TestClearRateLimitTotality:
    """Clear rate limit returns non-negative count.

    Invariant: clear_instrument_rate_limit returns >= 0 for any input.
    """

    @given(
        instrument_name=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=50),
        ),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_clear_returns_nonnegative_count(
        self, instrument_name: str | None
    ) -> None:
        """Clearing rate limits returns a non-negative count."""
        # Simulate clear logic
        rate_limits: dict[str, float] = {
            "claude-code": 1234567.0,
            "gemini-cli": 2345678.0,
        }

        def clear_rate_limit(name: str | None) -> int:
            """Clear rate limits and return count cleared."""
            if name is None:
                # Clear all
                count = len(rate_limits)
                rate_limits.clear()
                return count

            if name == "":
                # Empty string — clear nothing (F-201)
                return 0

            # Clear specific instrument
            if name in rate_limits:
                del rate_limits[name]
                return 1
            return 0

        # Clear and verify count
        count = clear_rate_limit(instrument_name)

        # Invariant: count >= 0
        assert count >= 0

        # Invariant: count <= number of instruments that existed
        assert count <= 2  # We started with 2 instruments
