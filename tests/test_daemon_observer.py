"""Tests for mozart.daemon.observer module.

Covers JobObserver lifecycle, filesystem event detection, process tree
monitoring, and graceful stop.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
import pytest

from marianne.daemon.event_bus import EventBus
from marianne.daemon.observer import JobObserver
from marianne.daemon.types import ObserverEvent


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_observer(
    tmp_path: Path,
    bus: EventBus,
    *,
    pid: int | None = None,
    watch_interval: float = 0.2,
) -> JobObserver:
    """Create a test observer with short poll interval."""
    return JobObserver(
        job_id="test-job",
        workspace=tmp_path,
        pid=pid or os.getpid(),
        event_bus=bus,
        watch_interval=watch_interval,
    )


# ─── Lifecycle ────────────────────────────────────────────────────────


class TestLifecycle:
    """Tests for JobObserver start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running(self, tmp_path: Path):
        """Starting the observer sets running=True and creates tasks."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        observer = _make_observer(tmp_path, bus)

        assert observer.running is False
        await observer.start()
        assert observer.running is True
        assert observer._fs_task is not None
        assert observer._proc_task is not None

        await observer.stop()
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, tmp_path: Path):
        """Calling start() twice doesn't create duplicate tasks."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        observer = _make_observer(tmp_path, bus)

        await observer.start()
        fs_task = observer._fs_task
        await observer.start()  # Second call
        assert observer._fs_task is fs_task  # Same task

        await observer.stop()
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_stop_clears_tasks(self, tmp_path: Path):
        """Stopping clears running flag and task references."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        observer = _make_observer(tmp_path, bus)

        await observer.start()
        await observer.stop()

        assert observer.running is False
        assert observer._fs_task is None
        assert observer._proc_task is None

        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, tmp_path: Path):
        """Calling stop() when not running is a no-op."""
        bus = EventBus(max_queue_size=100)
        await bus.start()
        observer = _make_observer(tmp_path, bus)

        # Stop without starting — should not raise
        await observer.stop()
        assert observer.running is False

        await bus.shutdown()


# ─── Filesystem Events ───────────────────────────────────────────────


class TestFilesystemEvents:
    """Tests for filesystem monitoring."""

    @pytest.mark.asyncio
    async def test_detects_file_creation(self, tmp_path: Path):
        """Observer detects new files in the workspace."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(
            callback=lambda e: received.append(e),
            event_filter=lambda e: e["event"].startswith("observer.file_"),
        )

        observer = _make_observer(tmp_path, bus, watch_interval=0.1)
        await observer.start()

        # Give watchfiles time to start monitoring
        await asyncio.sleep(0.5)

        # Create a file in the workspace
        test_file = tmp_path / "new_file.txt"
        test_file.write_text("hello")

        # Wait for the event to propagate
        await asyncio.sleep(0.8)

        await observer.stop()
        await bus.shutdown()

        # Should have at least one file event
        file_events = [e for e in received if "new_file.txt" in (e.get("data") or {}).get("path", "")]
        assert len(file_events) >= 1, f"Expected file events, got: {received}"
        assert file_events[0]["event"] in (
            "observer.file_created",
            "observer.file_modified",
        )

    @pytest.mark.asyncio
    async def test_detects_file_modification(self, tmp_path: Path):
        """Observer detects modified files in the workspace.

        Uses a fresh file creation + modification to ensure watchfiles
        sees the change regardless of OS-level file notification timing.
        """
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(
            callback=lambda e: received.append(e),
            event_filter=lambda e: e["event"].startswith("observer.file_"),
        )

        observer = _make_observer(tmp_path, bus, watch_interval=0.1)
        await observer.start()

        # Give watchfiles time to start monitoring
        await asyncio.sleep(0.5)

        # Create and then modify — ensures watchfiles sees a change
        test_file = tmp_path / "modify_test.txt"
        test_file.write_text("initial")
        await asyncio.sleep(0.5)
        test_file.write_text("modified content that is different")

        # Wait for the events to propagate
        await asyncio.sleep(0.8)

        await observer.stop()
        await bus.shutdown()

        # Should have at least one file event for this file
        file_events = [
            e for e in received
            if "modify_test.txt" in (e.get("data") or {}).get("path", "")
        ]
        assert len(file_events) >= 1, f"Expected file events, got: {received}"

    @pytest.mark.asyncio
    async def test_detects_file_deletion(self, tmp_path: Path):
        """Observer detects deleted files in the workspace."""
        test_file = tmp_path / "to_delete.txt"
        test_file.write_text("bye")

        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(
            callback=lambda e: received.append(e),
            event_filter=lambda e: e["event"].startswith("observer.file_"),
        )

        observer = _make_observer(tmp_path, bus, watch_interval=0.1)
        await observer.start()

        await asyncio.sleep(0.5)

        # Delete the file
        test_file.unlink()

        await asyncio.sleep(0.8)

        await observer.stop()
        await bus.shutdown()

        delete_events = [e for e in received if "to_delete.txt" in (e.get("data") or {}).get("path", "")]
        assert len(delete_events) >= 1


# ─── Process Monitoring ──────────────────────────────────────────────


class TestProcessMonitoring:
    """Tests for process tree monitoring."""

    @pytest.mark.asyncio
    async def test_monitors_current_process(self, tmp_path: Path):
        """Observer can monitor the current process without errors."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        observer = _make_observer(tmp_path, bus, pid=os.getpid())
        await observer.start()

        # Let it run a few poll cycles
        await asyncio.sleep(0.5)

        # Should still be running (process exists)
        assert observer.running is True

        await observer.stop()
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_detects_process_exit(self, tmp_path: Path):
        """Observer detects when the monitored process exits."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(
            callback=lambda e: received.append(e),
            event_filter=lambda e: e["event"].startswith("observer.process_"),
        )

        # Use a non-existent PID to simulate process exit
        observer = _make_observer(tmp_path, bus, pid=99999999, watch_interval=0.1)
        await observer.start()

        await asyncio.sleep(0.5)

        await observer.stop()
        await bus.shutdown()

        exit_events = [e for e in received if e["event"] == "observer.process_exited"]
        assert len(exit_events) >= 1
        assert exit_events[0]["data"]["pid"] == 99999999

    @pytest.mark.asyncio
    async def test_detects_child_process_spawn(self, tmp_path: Path):
        """Observer detects new child processes."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(
            callback=lambda e: received.append(e),
            event_filter=lambda e: e["event"] == "observer.process_spawned",
        )

        observer = _make_observer(tmp_path, bus, pid=os.getpid(), watch_interval=0.1)
        await observer.start()

        # Give observer time to capture initial state
        await asyncio.sleep(0.3)

        # Spawn a child process
        proc = await asyncio.create_subprocess_exec(
            "sleep", "2",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        await asyncio.sleep(0.5)

        # Terminate the child
        proc.terminate()
        await proc.wait()

        await asyncio.sleep(0.3)

        await observer.stop()
        await bus.shutdown()

        # Should have detected the child spawn
        assert len(received) >= 1, f"Expected spawn events, got: {received}"
        assert any(e["data"]["pid"] == proc.pid for e in received)


# ─── Graceful Stop ───────────────────────────────────────────────────


class TestGracefulStop:
    """Tests for graceful observer shutdown."""

    @pytest.mark.asyncio
    async def test_stop_cancels_both_tasks(self, tmp_path: Path):
        """Stop cancels both filesystem and process monitoring tasks."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        observer = _make_observer(tmp_path, bus)
        await observer.start()

        fs_task = observer._fs_task
        proc_task = observer._proc_task

        await observer.stop()

        assert fs_task.done()
        assert proc_task.done()

        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_stop_is_fast(self, tmp_path: Path):
        """Stopping the observer doesn't block for a long time."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        observer = _make_observer(tmp_path, bus)
        await observer.start()

        start = time.monotonic()
        await observer.stop()
        elapsed = time.monotonic() - start

        # Stop should be reasonably fast
        assert elapsed < 30.0, f"Stop took {elapsed:.1f}s"

        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_events_include_job_id(self, tmp_path: Path):
        """All observer events include the correct job_id."""
        bus = EventBus(max_queue_size=100)
        await bus.start()

        received: list[ObserverEvent] = []
        bus.subscribe(callback=lambda e: received.append(e))

        observer = JobObserver(
            job_id="my-special-job",
            workspace=tmp_path,
            pid=os.getpid(),
            event_bus=bus,
            watch_interval=0.1,
        )
        await observer.start()

        # Create a file to trigger an event
        (tmp_path / "trigger.txt").write_text("data")
        await asyncio.sleep(0.5)

        await observer.stop()
        await bus.shutdown()

        # All events should have the correct job_id
        for event in received:
            assert event["job_id"] == "my-special-job"
