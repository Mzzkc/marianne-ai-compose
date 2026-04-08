"""Tests for marianne.daemon.learning_hub module.

Covers LearningHub: start/stop lifecycle, store property access,
persistence loop behavior, and multi-job shared store semantics.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from marianne.daemon.learning_hub import LearningHub
from marianne.learning.global_store import GlobalLearningStore


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Isolated database path for each test."""
    return tmp_path / "test-learning.db"


@pytest.fixture
def hub(db_path: Path) -> LearningHub:
    """Fresh LearningHub instance (not started)."""
    return LearningHub(db_path=db_path)


# ─── Lifecycle ─────────────────────────────────────────────────────────


class TestLifecycle:
    """Tests for LearningHub start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_store(self, hub: LearningHub):
        """After start(), the store is available."""
        assert hub.is_running is False

        await hub.start()
        try:
            assert hub.is_running is True
            assert hub._store is not None
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_start_creates_heartbeat_task(self, hub: LearningHub):
        """start() creates a background persistence task."""
        await hub.start()
        try:
            assert hub._heartbeat_task is not None
            assert not hub._heartbeat_task.done()
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_heartbeat_task(self, hub: LearningHub):
        """stop() cancels the persistence task cleanly."""
        await hub.start()
        task = hub._heartbeat_task
        assert task is not None

        await hub.stop()

        assert task.cancelled() or task.done()
        assert hub._heartbeat_task is None
        assert hub._store is None

    @pytest.mark.asyncio
    async def test_stop_sets_not_running(self, hub: LearningHub):
        """After stop(), is_running is False."""
        await hub.start()
        await hub.stop()

        assert hub.is_running is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self, hub: LearningHub):
        """stop() on an unstarted hub is a safe no-op."""
        await hub.stop()
        assert hub.is_running is False

    @pytest.mark.asyncio
    async def test_start_stop_start(self, hub: LearningHub):
        """Hub can be restarted after stopping."""
        await hub.start()
        await hub.stop()
        assert hub.is_running is False

        await hub.start()
        try:
            assert hub.is_running is True
            assert hub._store is not None
        finally:
            await hub.stop()


# ─── Store Property ──────────────────────────────────────────────────


class TestStoreProperty:
    """Tests for the store property."""

    @pytest.mark.asyncio
    async def test_store_returns_global_learning_store(self, hub: LearningHub):
        """The store property returns a GlobalLearningStore instance."""
        await hub.start()
        try:
            store = hub.store
            assert isinstance(store, GlobalLearningStore)
        finally:
            await hub.stop()

    def test_store_raises_when_not_started(self, hub: LearningHub):
        """Accessing store before start() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="LearningHub not started"):
            hub.store

    @pytest.mark.asyncio
    async def test_store_raises_after_stop(self, hub: LearningHub):
        """Accessing store after stop() raises RuntimeError."""
        await hub.start()
        await hub.stop()

        with pytest.raises(RuntimeError, match="LearningHub not started"):
            hub.store


# ─── Persistence Loop ────────────────────────────────────────────────


class TestPersistenceLoop:
    """Tests for the periodic persistence loop."""

    @pytest.mark.asyncio
    async def test_heartbeat_interval_default(self, hub: LearningHub):
        """Default persist interval is 60 seconds."""
        assert hub._heartbeat_interval == 60.0

    @pytest.mark.asyncio
    async def test_persist_loop_runs_periodically(self, db_path: Path):
        """Persistence loop sleeps for the configured interval."""
        hub = LearningHub(db_path=db_path)
        hub._heartbeat_interval = 0.05  # 50ms for fast testing

        await hub.start()
        try:
            # Let the loop run a couple of cycles
            await asyncio.sleep(0.15)
            # If the loop crashed, the task would be done with an exception
            assert hub._heartbeat_task is not None
            assert not hub._heartbeat_task.done()
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_persist_loop_handles_cancellation(self, db_path: Path):
        """Persistence loop exits cleanly on CancelledError."""
        hub = LearningHub(db_path=db_path)
        hub._heartbeat_interval = 0.01

        await hub.start()
        task = hub._heartbeat_task
        assert task is not None

        # Let it run briefly, then stop (which cancels)
        await asyncio.sleep(0.03)
        await hub.stop()

        # Task should complete without raising
        assert task.done()
        # Verify no unexpected exception (cancelled is expected)
        exc = task.exception() if not task.cancelled() else None
        assert exc is None


# ─── Shared Store Semantics ──────────────────────────────────────────


class TestSharedStoreSemantics:
    """Tests verifying multiple jobs share the same store instance."""

    @pytest.mark.asyncio
    async def test_same_store_instance_across_accesses(self, hub: LearningHub):
        """Multiple accesses to .store return the same object."""
        await hub.start()
        try:
            store_1 = hub.store
            store_2 = hub.store
            assert store_1 is store_2
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_store_uses_specified_db_path(self, db_path: Path):
        """The store is created at the configured db_path."""
        hub = LearningHub(db_path=db_path)
        await hub.start()
        try:
            store = hub.store
            assert store.db_path == db_path
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_default_db_path_when_none(self):
        """When no db_path is given, DEFAULT_GLOBAL_STORE_PATH is used."""
        from marianne.learning.store.base import DEFAULT_GLOBAL_STORE_PATH

        hub = LearningHub(db_path=None)
        assert hub._db_path == DEFAULT_GLOBAL_STORE_PATH


# ─── Edge Cases ──────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_hub_with_custom_heartbeat_interval(self, db_path: Path):
        """Custom persist interval is respected."""
        hub = LearningHub(db_path=db_path)
        hub._heartbeat_interval = 0.02

        await hub.start()
        try:
            assert hub._heartbeat_interval == 0.02
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_is_running_property_tracks_state(self, hub: LearningHub):
        """is_running accurately reflects hub lifecycle."""
        assert hub.is_running is False

        await hub.start()
        assert hub.is_running is True

        await hub.stop()
        assert hub.is_running is False


# ─── Concurrent Access ──────────────────────────────────────────────


class TestConcurrentAccess:
    """Tests verifying concurrent async tasks safely share the store."""

    @pytest.mark.asyncio
    async def test_concurrent_writes_through_shared_store(self, db_path: Path):
        """Multiple async tasks can write patterns concurrently."""
        hub = LearningHub(db_path=db_path)
        await hub.start()

        try:
            store = hub.store

            async def writer(job_id: str, count: int) -> None:
                for i in range(count):
                    store.record_pattern(
                        pattern_type="test",
                        pattern_name=f"{job_id}-pattern-{i}",
                        description=f"Pattern from {job_id}",
                    )
                    # Yield to event loop to enable interleaving
                    await asyncio.sleep(0)

            await asyncio.gather(
                writer("job-a", 20),
                writer("job-b", 20),
                writer("job-c", 20),
            )

            patterns = store.get_patterns(min_priority=0.0, limit=100)
            assert len(patterns) == 60
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_concurrent_reads_and_writes(self, db_path: Path):
        """Reads and writes can be interleaved across async tasks."""
        hub = LearningHub(db_path=db_path)
        await hub.start()

        try:
            store = hub.store

            # Seed some patterns first
            for i in range(5):
                store.record_pattern(
                    pattern_type="seed",
                    pattern_name=f"seed-{i}",
                )

            read_counts: list[int] = []

            async def reader() -> None:
                for _ in range(10):
                    patterns = store.get_patterns(min_priority=0.0, limit=100)
                    read_counts.append(len(patterns))
                    await asyncio.sleep(0)

            async def writer() -> None:
                for i in range(10):
                    store.record_pattern(
                        pattern_type="concurrent",
                        pattern_name=f"new-{i}",
                    )
                    await asyncio.sleep(0)

            await asyncio.gather(reader(), writer())

            # Reads should never see fewer than the seed count
            assert all(c >= 5 for c in read_counts)
            # Final count should include all patterns
            final = store.get_patterns(min_priority=0.0, limit=100)
            assert len(final) == 15  # 5 seed + 10 new
        finally:
            await hub.stop()

    @pytest.mark.asyncio
    async def test_batch_connection_isolated_per_task(self, db_path: Path):
        """batch_connection() in one task doesn't leak to another.

        Verifies that the ContextVar-based _batch_conn is scoped per
        asyncio task: a task running inside batch_connection() sees the
        batch conn, while a concurrent task sees None.
        """
        hub = LearningHub(db_path=db_path)
        await hub.start()

        try:
            store = hub.store
            batch_seen_by_task_a = False
            batch_seen_by_task_b = False
            task_b_ready = asyncio.Event()
            task_b_checked = asyncio.Event()

            async def batch_task() -> None:
                nonlocal batch_seen_by_task_a
                with store.batch_connection():
                    # Inside batch: our task should see the batch conn
                    batch_seen_by_task_a = store._batch_conn.get() is not None
                    # Signal task B to check its own view
                    task_b_ready.set()
                    # Wait for task B to check before exiting batch
                    await task_b_checked.wait()

            async def check_task() -> None:
                nonlocal batch_seen_by_task_b
                # Wait until task A is inside batch_connection
                await task_b_ready.wait()
                # This task should NOT see task A's batch connection
                batch_seen_by_task_b = store._batch_conn.get() is not None
                task_b_checked.set()

            await asyncio.gather(batch_task(), check_task())

            # Task A (batch holder) should see the batch connection
            assert batch_seen_by_task_a is True
            # Task B should NOT see task A's batch connection
            assert batch_seen_by_task_b is False
        finally:
            await hub.stop()
