"""Tests for F-470: _synced_status memory leak on job deregister.

The BatonAdapter._synced_status cache (F-211 state-diff dedup) was not
cleaned up in deregister_job(), causing unbounded memory growth for
long-running daemons.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from marianne.daemon.baton.adapter import BatonAdapter


def _make_adapter(max_concurrent: int = 10) -> BatonAdapter:
    """Create a minimal BatonAdapter for testing."""
    adapter = BatonAdapter(
        event_bus=MagicMock(),
        max_concurrent_sheets=max_concurrent,
        state_sync_callback=MagicMock(),
    )
    adapter.set_backend_pool(MagicMock())
    return adapter


class TestSyncedStatusCleanup:
    """Verify _synced_status is cleaned when a job is deregistered."""

    def test_deregister_removes_job_entries(self) -> None:
        """After deregister_job(), no _synced_status entries remain for that job."""
        adapter = _make_adapter()

        # Populate cache for two jobs
        adapter._synced_status[("job-a", 1)] = "completed"
        adapter._synced_status[("job-a", 2)] = "failed"
        adapter._synced_status[("job-a", 3)] = "completed"
        adapter._synced_status[("job-b", 1)] = "in_progress"
        adapter._synced_status[("job-b", 2)] = "completed"

        # Deregister job-a
        adapter.deregister_job("job-a")

        # job-a entries must be gone
        assert ("job-a", 1) not in adapter._synced_status
        assert ("job-a", 2) not in adapter._synced_status
        assert ("job-a", 3) not in adapter._synced_status

        # job-b entries must be intact
        assert ("job-b", 1) in adapter._synced_status
        assert ("job-b", 2) in adapter._synced_status
        assert len(adapter._synced_status) == 2

    def test_deregister_empty_cache_no_error(self) -> None:
        """Deregistering when _synced_status has no entries for the job is safe."""
        adapter = _make_adapter()

        # No entries for job-x
        adapter._synced_status[("other-job", 1)] = "completed"

        # Should not raise
        adapter.deregister_job("job-x")

        assert len(adapter._synced_status) == 1
        assert ("other-job", 1) in adapter._synced_status

    def test_deregister_clears_all_sheets_for_large_job(self) -> None:
        """A job with many sheets gets fully cleaned up."""
        adapter = _make_adapter()

        # Simulate a 706-sheet job (like the v3 orchestra)
        for i in range(1, 707):
            adapter._synced_status[("v3-orchestra", i)] = "completed"

        # Plus a small job
        adapter._synced_status[("small-job", 1)] = "completed"

        assert len(adapter._synced_status) == 707

        adapter.deregister_job("v3-orchestra")

        # Only small-job remains
        assert len(adapter._synced_status) == 1
        assert ("small-job", 1) in adapter._synced_status

    def test_sequential_deregisters_clean_independently(self) -> None:
        """Multiple deregisters each clean only their own entries."""
        adapter = _make_adapter()

        for i in range(1, 4):
            adapter._synced_status[("job-1", i)] = "completed"
            adapter._synced_status[("job-2", i)] = "failed"
            adapter._synced_status[("job-3", i)] = "in_progress"

        assert len(adapter._synced_status) == 9

        adapter.deregister_job("job-2")
        assert len(adapter._synced_status) == 6

        adapter.deregister_job("job-1")
        assert len(adapter._synced_status) == 3

        adapter.deregister_job("job-3")
        assert len(adapter._synced_status) == 0

    def test_deregister_twice_is_idempotent(self) -> None:
        """Deregistering the same job twice doesn't break anything."""
        adapter = _make_adapter()

        adapter._synced_status[("job-1", 1)] = "completed"
        adapter._synced_status[("job-1", 2)] = "completed"

        adapter.deregister_job("job-1")
        assert len(adapter._synced_status) == 0

        # Second deregister is a no-op
        adapter.deregister_job("job-1")
        assert len(adapter._synced_status) == 0
