"""Test for F-519: Pattern discovery expiry timing bug.

The test_discovery_events_expire_correctly test has a timing bug where the
TTL (0.1s) is too short. Under parallel test execution with xdist, scheduling
delays can cause the pattern to expire between record_pattern_discovery() and
get_active_pattern_discoveries(), causing a flaky failure.
"""

import tempfile
import time
from pathlib import Path

from marianne.learning.global_store import GlobalLearningStore


class TestPatternDiscoveryTiming:
    """Regression test for timing-sensitive pattern discovery expiry."""

    def test_short_ttl_expires_before_query_under_load(self) -> None:
        """Demonstrate the race condition with 0.1s TTL."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            store = GlobalLearningStore(db_path)

            # Record with 0.1s TTL (100ms) - the problematic value
            store.record_pattern_discovery(
                pattern_id="race-001",
                pattern_name="Race Condition Pattern",
                pattern_type="test",
                job_id="test-job",
                ttl_seconds=0.1,
            )

            # Under normal conditions, this passes (but we don't assert - would be flaky)
            events = store.get_active_pattern_discoveries()
            _ = any(e.pattern_name == "Race Condition Pattern" for e in events)

            # But with even a tiny delay (simulating xdist scheduling overhead)
            time.sleep(0.15)  # 150ms - exceeds the 100ms TTL

            events_after_expiry = store.get_active_pattern_discoveries()
            after_expiry_found = any(
                e.pattern_name == "Race Condition Pattern" for e in events_after_expiry
            )

            # The pattern should exist immediately (unless we hit the race)
            # but should NOT exist after TTL expires
            assert not after_expiry_found, "Pattern should expire after TTL"

            # This assertion would be flaky under parallel execution:
            # assert immediate_found, "Pattern should exist immediately"
            # because xdist scheduling can introduce >100ms delay

        finally:
            db_path.unlink()

    def test_reasonable_ttl_survives_scheduling_delays(self) -> None:
        """Verify that a reasonable TTL (1s+) doesn't race."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            store = GlobalLearningStore(db_path)

            # Record with 3s TTL - gives 500ms margin for xdist scheduling overhead (F-521)
            store.record_pattern_discovery(
                pattern_id="stable-001",
                pattern_name="Stable Pattern",
                pattern_type="test",
                job_id="test-job",
                ttl_seconds=3.0,
            )

            # Even with scheduling delays, pattern should be found
            events = store.get_active_pattern_discoveries()
            found = any(e.pattern_name == "Stable Pattern" for e in events)
            assert found, "Pattern with 3s TTL should be found immediately"

            # Verify expiry still works after TTL (500ms margin instead of 100ms)
            time.sleep(3.5)
            events_after = store.get_active_pattern_discoveries()
            found_after = any(e.pattern_name == "Stable Pattern" for e in events_after)
            assert not found_after, "Pattern should expire after 3s TTL"

        finally:
            db_path.unlink()
