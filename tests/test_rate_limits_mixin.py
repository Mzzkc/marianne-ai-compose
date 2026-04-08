"""Tests for marianne.learning.store.rate_limits module (RateLimitMixin).

Exercises the actual SQLite-backed rate limit recording, querying,
and cleanup operations through the GlobalLearningStore.

GH#81 — Learning store rate_limits mixin at 68% coverage — 20 missed lines.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from marianne.learning.global_store import GlobalLearningStore


@pytest.fixture
def temp_db_path() -> Path:
    """Create a temporary database path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def store(temp_db_path: Path) -> Generator[GlobalLearningStore, None, None]:
    """Create a GlobalLearningStore with a temporary database."""
    s = GlobalLearningStore(temp_db_path)
    yield s
    if temp_db_path.exists():
        temp_db_path.unlink()


class TestRecordRateLimitEvent:
    """Tests for record_rate_limit_event()."""

    def test_returns_record_id(self, store: GlobalLearningStore) -> None:
        """Recording a rate limit event returns a non-empty string ID."""
        record_id = store.record_rate_limit_event(
            error_code="E101",
            duration_seconds=60.0,
            job_id="test-job",
        )
        assert isinstance(record_id, str)
        assert len(record_id) > 0

    def test_with_model(self, store: GlobalLearningStore) -> None:
        """Recording with a model name succeeds."""
        record_id = store.record_rate_limit_event(
            error_code="E102",
            duration_seconds=120.0,
            job_id="test-job-2",
            model="claude-3",
        )
        assert record_id

    def test_unique_ids(self, store: GlobalLearningStore) -> None:
        """Each recording gets a unique ID."""
        id1 = store.record_rate_limit_event("E101", 60.0, "job-1")
        id2 = store.record_rate_limit_event("E101", 60.0, "job-2")
        assert id1 != id2


class TestIsRateLimited:
    """Tests for is_rate_limited()."""

    def test_not_limited_when_empty(self, store: GlobalLearningStore) -> None:
        """No events -> not rate limited."""
        limited, ttl = store.is_rate_limited()
        assert limited is False
        assert ttl is None

    def test_limited_after_recording(self, store: GlobalLearningStore) -> None:
        """After recording a recent event, should be rate limited."""
        store.record_rate_limit_event("E101", 300.0, "job-1")
        limited, ttl = store.is_rate_limited()
        assert limited is True
        assert ttl is not None
        assert ttl > 0

    def test_filter_by_error_code(self, store: GlobalLearningStore) -> None:
        """Filter by specific error code."""
        store.record_rate_limit_event("E101", 300.0, "job-1")

        limited_e101, _ = store.is_rate_limited(error_code="E101")
        assert limited_e101 is True

        limited_e999, _ = store.is_rate_limited(error_code="E999")
        assert limited_e999 is False

    def test_filter_by_model(self, store: GlobalLearningStore) -> None:
        """Filter by specific model name."""
        store.record_rate_limit_event("E101", 300.0, "job-1", model="gpt-4")

        limited_gpt4, _ = store.is_rate_limited(model="gpt-4")
        assert limited_gpt4 is True

        limited_claude, _ = store.is_rate_limited(model="claude-3")
        assert limited_claude is False

    def test_expired_events_not_limited(self, store: GlobalLearningStore) -> None:
        """Events with very short duration expire quickly."""
        # Record with 0.01s duration -> 80% TTL = 0.008s
        store.record_rate_limit_event("E101", 0.01, "job-1")
        time.sleep(0.02)  # Wait for expiry

        limited, _ = store.is_rate_limited()
        assert limited is False


class TestGetActiveRateLimits:
    """Tests for get_active_rate_limits()."""

    def test_empty_when_no_events(self, store: GlobalLearningStore) -> None:
        """No events -> empty list."""
        events = store.get_active_rate_limits()
        assert events == []

    def test_returns_active_events(self, store: GlobalLearningStore) -> None:
        """Active events are returned."""
        store.record_rate_limit_event("E101", 300.0, "job-1")
        store.record_rate_limit_event("E102", 300.0, "job-2", model="claude-3")

        events = store.get_active_rate_limits()
        assert len(events) == 2

    def test_filter_by_model(self, store: GlobalLearningStore) -> None:
        """Filter active events by model."""
        store.record_rate_limit_event("E101", 300.0, "job-1", model="gpt-4")
        store.record_rate_limit_event("E102", 300.0, "job-2", model="claude-3")

        gpt4_events = store.get_active_rate_limits(model="gpt-4")
        assert len(gpt4_events) == 1
        assert gpt4_events[0].model == "gpt-4"

    def test_event_fields(self, store: GlobalLearningStore) -> None:
        """Returned events have correct fields."""
        store.record_rate_limit_event("E101", 120.0, "job-1", model="test-model")

        events = store.get_active_rate_limits()
        assert len(events) == 1
        event = events[0]
        assert event.error_code == "E101"
        assert event.model == "test-model"
        assert event.duration_seconds == 120.0
        assert event.id  # non-empty
        assert event.recorded_at is not None
        assert event.expires_at is not None

    def test_expired_events_not_returned(self, store: GlobalLearningStore) -> None:
        """Expired events are not in active list."""
        store.record_rate_limit_event("E101", 0.01, "job-1")
        time.sleep(0.02)

        events = store.get_active_rate_limits()
        assert events == []


class TestCleanupExpiredRateLimits:
    """Tests for cleanup_expired_rate_limits()."""

    def test_cleanup_returns_zero_when_empty(self, store: GlobalLearningStore) -> None:
        """No events -> 0 deleted."""
        deleted = store.cleanup_expired_rate_limits()
        assert deleted == 0

    def test_cleanup_removes_expired(self, store: GlobalLearningStore) -> None:
        """Expired events are deleted."""
        store.record_rate_limit_event("E101", 0.01, "job-1")
        time.sleep(0.02)

        deleted = store.cleanup_expired_rate_limits()
        assert deleted >= 1

    def test_cleanup_preserves_active(self, store: GlobalLearningStore) -> None:
        """Active (unexpired) events are preserved."""
        store.record_rate_limit_event("E101", 300.0, "job-1")

        deleted = store.cleanup_expired_rate_limits()
        # The active event should not be deleted
        events = store.get_active_rate_limits()
        assert len(events) == 1
