"""Tests for F-470: BatonAdapter._synced_status memory leak on job deregister.

The _synced_status dict caches (job_id, sheet_num) → checkpoint_status for
dedup during state sync (F-211). Without cleanup on deregister, entries
accumulate O(total_sheets_ever) for long-running daemons.

TDD tests — red first, then verify fix makes them green.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from marianne.daemon.baton.adapter import BatonAdapter


def _make_adapter() -> BatonAdapter:
    """Create a BatonAdapter with minimal dependencies."""
    event_bus = MagicMock()
    adapter = BatonAdapter(
        event_bus=event_bus,
        max_concurrent_sheets=10,
    )
    return adapter


class TestSyncedStatusCleanupOnDeregister:
    """F-470: _synced_status must be cleaned when a job is deregistered."""

    def test_deregister_removes_synced_entries(self) -> None:
        """After deregister_job(), no entries for that job remain in _synced_status."""
        adapter = _make_adapter()

        # Simulate synced state for job "abc" with 5 sheets
        for i in range(5):
            adapter._synced_status[("abc", i)] = "COMPLETED"

        adapter.deregister_job("abc")

        remaining = {k for k in adapter._synced_status if k[0] == "abc"}
        assert remaining == set(), f"Leaked entries: {remaining}"

    def test_deregister_preserves_other_jobs(self) -> None:
        """Deregistering one job must not affect another job's cached state."""
        adapter = _make_adapter()

        for i in range(3):
            adapter._synced_status[("job-a", i)] = "COMPLETED"
            adapter._synced_status[("job-b", i)] = "RUNNING"

        adapter.deregister_job("job-a")

        # job-b entries must still be present
        assert len([k for k in adapter._synced_status if k[0] == "job-b"]) == 3
        # job-a entries must be gone
        assert len([k for k in adapter._synced_status if k[0] == "job-a"]) == 0

    def test_deregister_large_scale_cleanup(self) -> None:
        """100 simulated jobs × 10 sheets each — all cleaned on deregister."""
        adapter = _make_adapter()

        for job_idx in range(100):
            job_id = f"job-{job_idx}"
            for sheet_num in range(10):
                adapter._synced_status[(job_id, sheet_num)] = "COMPLETED"

        assert len(adapter._synced_status) == 1000

        # Deregister all jobs
        for job_idx in range(100):
            adapter.deregister_job(f"job-{job_idx}")

        assert len(adapter._synced_status) == 0, (
            f"Memory leak: {len(adapter._synced_status)} stale entries remain"
        )

    def test_deregister_empty_cache_is_noop(self) -> None:
        """Deregistering when no synced entries exist must not error."""
        adapter = _make_adapter()
        assert len(adapter._synced_status) == 0
        # Should not raise
        adapter.deregister_job("nonexistent")
        assert len(adapter._synced_status) == 0

    def test_deregister_with_mixed_statuses(self) -> None:
        """Entries with different statuses for the same job are all cleaned."""
        adapter = _make_adapter()

        adapter._synced_status[("job-x", 1)] = "RUNNING"
        adapter._synced_status[("job-x", 2)] = "COMPLETED"
        adapter._synced_status[("job-x", 3)] = "FAILED"
        adapter._synced_status[("job-y", 1)] = "COMPLETED"

        adapter.deregister_job("job-x")

        assert ("job-x", 1) not in adapter._synced_status
        assert ("job-x", 2) not in adapter._synced_status
        assert ("job-x", 3) not in adapter._synced_status
        assert ("job-y", 1) in adapter._synced_status
