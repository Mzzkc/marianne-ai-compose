"""Tests for ObserverRecorder and ObserverConfig persistence fields."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from marianne.daemon.config import ObserverConfig
from marianne.daemon.observer_recorder import ObserverRecorder
from marianne.daemon.types import ObserverEvent


@pytest.fixture(autouse=True)
def _cleanup_handles():
    """Prevent FD leaks on test failure — explicit cleanup of file handles."""
    yield
    # GC will close handles, but explicit cleanup prevents FD exhaustion
    import gc

    gc.collect()


class TestObserverConfigPersistence:
    """Verify new persistence fields on ObserverConfig."""

    def test_defaults(self) -> None:
        config = ObserverConfig()
        assert config.persist_events is True
        assert ".git/" in config.exclude_patterns
        assert "__pycache__/" in config.exclude_patterns
        assert "node_modules/" in config.exclude_patterns
        assert ".venv/" in config.exclude_patterns
        assert "*.pyc" in config.exclude_patterns
        assert config.coalesce_window_seconds == 2.0
        assert config.max_timeline_bytes == 10_485_760

    def test_disable_persistence(self) -> None:
        config = ObserverConfig(persist_events=False)
        assert config.persist_events is False

    def test_custom_exclude_patterns(self) -> None:
        config = ObserverConfig(exclude_patterns=[".git/", "build/"])
        assert config.exclude_patterns == [".git/", "build/"]

    def test_coalesce_window_minimum(self) -> None:
        config = ObserverConfig(coalesce_window_seconds=0.0)
        assert config.coalesce_window_seconds == 0.0

    def test_max_timeline_bytes_minimum(self) -> None:
        with pytest.raises(ValidationError):
            ObserverConfig(max_timeline_bytes=100)  # Below 4KB minimum


class TestPathExclusion:
    """Verify path exclusion filtering."""

    def _make_recorder(self, **config_kwargs: object) -> ObserverRecorder:
        config = ObserverConfig(**config_kwargs)
        return ObserverRecorder(config=config)

    def test_default_excludes_git(self) -> None:
        recorder = self._make_recorder()
        assert recorder._should_exclude(".git/objects/abc123")

    def test_default_excludes_pycache(self) -> None:
        recorder = self._make_recorder()
        assert recorder._should_exclude("src/__pycache__/foo.cpython-312.pyc")

    def test_default_excludes_node_modules(self) -> None:
        recorder = self._make_recorder()
        assert recorder._should_exclude("node_modules/lodash/index.js")

    def test_default_excludes_venv(self) -> None:
        recorder = self._make_recorder()
        assert recorder._should_exclude(".venv/lib/python3.12/site.py")

    def test_default_excludes_pyc(self) -> None:
        recorder = self._make_recorder()
        assert recorder._should_exclude("src/foo.pyc")

    def test_allows_normal_paths(self) -> None:
        recorder = self._make_recorder()
        assert not recorder._should_exclude("src/main.py")
        assert not recorder._should_exclude("output-3.md")
        assert not recorder._should_exclude("tests/test_foo.py")

    def test_custom_patterns(self) -> None:
        recorder = self._make_recorder(exclude_patterns=["build/", "*.tmp"])
        assert recorder._should_exclude("build/output.js")
        assert recorder._should_exclude("data.tmp")
        assert not recorder._should_exclude(".git/HEAD")  # Not in custom list

    def test_empty_patterns_excludes_nothing(self) -> None:
        recorder = self._make_recorder(exclude_patterns=[])
        assert not recorder._should_exclude(".git/HEAD")
        assert not recorder._should_exclude("src/__pycache__/foo.pyc")


class TestJSONLPersistence:
    """Verify JSONL write, register/unregister lifecycle."""

    def _make_recorder(self, **config_kwargs: object) -> ObserverRecorder:
        config = ObserverConfig(**config_kwargs)
        return ObserverRecorder(config=config)

    def test_register_creates_state(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        assert "job-1" in recorder._jobs

    def test_register_opens_jsonl(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        assert jsonl_path.exists()

    def test_unregister_closes_file(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        recorder.unregister_job("job-1")
        assert "job-1" not in recorder._jobs

    def test_write_event_produces_jsonl_line(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        event: ObserverEvent = {
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_created",
            "data": {"path": "output.md"},
            "timestamp": time.time(),
        }
        recorder._write_event("job-1", event)
        recorder.flush("job-1")

        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event"] == "observer.file_created"

    def test_excluded_path_not_written(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        event: ObserverEvent = {
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_modified",
            "data": {"path": ".git/objects/abc123"},
            "timestamp": time.time(),
        }
        recorder._write_event("job-1", event)
        recorder.flush("job-1")

        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        content = jsonl_path.read_text().strip()
        assert content == ""

    def test_unregister_unknown_job_is_noop(self) -> None:
        recorder = self._make_recorder()
        recorder.unregister_job("nonexistent")  # Should not raise

    def test_disabled_persistence_skips_file(self, tmp_path: Path) -> None:
        recorder = self._make_recorder(persist_events=False)
        recorder.register_job("job-1", tmp_path)
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        assert not jsonl_path.exists()

    # --- Expert review edge case tests ---

    def test_register_twice_is_idempotent(self, tmp_path: Path) -> None:
        """Registering the same job_id twice should not create a second state."""
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        state_before = recorder._jobs["job-1"]
        recorder.register_job("job-1", tmp_path)
        assert recorder._jobs["job-1"] is state_before

    def test_flush_after_unregister_is_noop(self, tmp_path: Path) -> None:
        """Flush on a job that was already unregistered should not raise."""
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        recorder.unregister_job("job-1")
        recorder.flush("job-1")  # Should not raise

    def test_event_with_none_data_does_not_crash(self, tmp_path: Path) -> None:
        """An event with data=None should be written without error."""
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        event: ObserverEvent = {
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.process_spawned",
            "data": None,
            "timestamp": time.time(),
        }
        recorder._write_event("job-1", event)
        recorder.flush("job-1")

        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["data"] is None

    def test_event_for_unregistered_job_is_ignored(self) -> None:
        """Writing an event for a job that was never registered is a no-op."""
        recorder = self._make_recorder()
        event: ObserverEvent = {
            "job_id": "job-unknown",
            "sheet_num": 0,
            "event": "observer.file_created",
            "data": {"path": "foo.py"},
            "timestamp": time.time(),
        }
        recorder._write_event("job-unknown", event)  # Should not raise

    def test_file_write_failure_still_populates_ring_buffer(
        self, tmp_path: Path
    ) -> None:
        """Ring buffer MUST receive events even when file I/O fails."""
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        # Sabotage the file handle to simulate write failure
        state = recorder._jobs["job-1"]
        state.file_handle.close()
        state.file_handle = _BrokenWriter()

        event: ObserverEvent = {
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_created",
            "data": {"path": "output.md"},
            "timestamp": time.time(),
        }
        recorder._write_event("job-1", event)

        # Ring buffer should still have the event
        assert len(state.recent_events) == 1
        assert state.recent_events[0]["event"] == "observer.file_created"

    def test_excluded_event_not_in_ring_buffer(self, tmp_path: Path) -> None:
        """Excluded path events should not appear in the ring buffer either."""
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        event: ObserverEvent = {
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_modified",
            "data": {"path": ".git/HEAD"},
            "timestamp": time.time(),
        }
        recorder._write_event("job-1", event)
        state = recorder._jobs["job-1"]
        assert len(state.recent_events) == 0


class TestCoalescing:
    """Verify same-file modification coalescing."""

    def _make_recorder(self, **kw: object) -> ObserverRecorder:
        config = ObserverConfig(**kw)
        return ObserverRecorder(config=config)

    def test_rapid_same_file_mods_coalesce(self, tmp_path: Path) -> None:
        """Multiple edits to same file within window produce one JSONL line."""
        recorder = self._make_recorder(coalesce_window_seconds=2.0)
        recorder.register_job("job-1", tmp_path)
        now = time.time()
        for i in range(5):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_modified",
                "data": {"path": "same-file.md"},
                "timestamp": now + i * 0.1,  # 100ms apart, within 2s window
            }
            recorder._handle_event(event)

        recorder.flush("job-1")
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        # Should produce 1 coalesced event, not 5
        assert len(lines) == 1

    def test_different_files_not_coalesced(self, tmp_path: Path) -> None:
        """Events for different files are never coalesced."""
        recorder = self._make_recorder(coalesce_window_seconds=2.0)
        recorder.register_job("job-1", tmp_path)
        now = time.time()
        for i in range(3):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_modified",
                "data": {"path": f"file-{i}.md"},
                "timestamp": now + i * 0.1,
            }
            recorder._handle_event(event)

        recorder.flush("job-1")
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        assert len(lines) == 3

    def test_process_events_not_coalesced(self, tmp_path: Path) -> None:
        """Process events always write immediately, never coalesced."""
        recorder = self._make_recorder(coalesce_window_seconds=2.0)
        recorder.register_job("job-1", tmp_path)
        now = time.time()
        for i in range(3):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.process_spawned",
                "data": {"pid": 1000 + i, "name": "pytest"},
                "timestamp": now + i * 0.1,
            }
            recorder._handle_event(event)

        recorder.flush("job-1")
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        assert len(lines) == 3

    def test_zero_coalesce_window_disables(self, tmp_path: Path) -> None:
        """Setting coalesce window to 0 disables coalescing entirely."""
        recorder = self._make_recorder(coalesce_window_seconds=0.0)
        recorder.register_job("job-1", tmp_path)
        now = time.time()
        for i in range(3):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_modified",
                "data": {"path": "same-file.md"},
                "timestamp": now + i * 0.1,
            }
            recorder._handle_event(event)

        recorder.flush("job-1")
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        assert len(lines) == 3  # No coalescing

    def test_coalescing_uses_event_timestamps(self, tmp_path: Path) -> None:
        """REVIEW FIX 4: Coalescing compares event['timestamp'], not wall clock.

        Events with fabricated timestamps that span beyond the window
        should NOT be coalesced, even if sent in rapid succession.
        """
        recorder = self._make_recorder(coalesce_window_seconds=1.0)
        recorder.register_job("job-1", tmp_path)

        # Event 1: ts=1000.0
        recorder._handle_event({
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_modified",
            "data": {"path": "same-file.md"},
            "timestamp": 1000.0,
        })
        # Event 2: ts=1005.0 — 5 seconds later, outside 1s window
        recorder._handle_event({
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_modified",
            "data": {"path": "same-file.md"},
            "timestamp": 1005.0,
        })

        recorder.flush("job-1")
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        # Both events should be written: first flushed when window expired,
        # second flushed by explicit flush()
        assert len(lines) == 2

    def test_coalesced_events_appear_in_ring_buffer(self, tmp_path: Path) -> None:
        """REVIEW FIX 3: Coalesced events MUST still enter ring buffer immediately."""
        recorder = self._make_recorder(coalesce_window_seconds=2.0)
        recorder.register_job("job-1", tmp_path)
        state = recorder._jobs["job-1"]
        now = time.time()

        for i in range(5):
            recorder._handle_event({
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_modified",
                "data": {"path": "active-file.md"},
                "timestamp": now + i * 0.1,
            })

        # Ring buffer should have ALL 5 events, even though JSONL will coalesce
        assert len(state.recent_events) == 5

    def test_file_created_not_coalesced(self, tmp_path: Path) -> None:
        """Only file_modified events are coalesced, not file_created."""
        recorder = self._make_recorder(coalesce_window_seconds=2.0)
        recorder.register_job("job-1", tmp_path)
        now = time.time()
        for i in range(3):
            recorder._handle_event({
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_created",
                "data": {"path": "same-file.md"},
                "timestamp": now + i * 0.1,
            })

        recorder.flush("job-1")
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        assert len(lines) == 3  # file_created always written


class TestSizeCap:
    """Verify JSONL size cap and truncation."""

    def _make_recorder(self, **kw: object) -> ObserverRecorder:
        config = ObserverConfig(**kw)
        return ObserverRecorder(config=config)

    @pytest.mark.asyncio
    async def test_truncates_when_over_cap(self, tmp_path: Path) -> None:
        """REVIEW FIX 5: Use max_timeline_bytes=4096 (Pydantic ge=4096)."""
        import asyncio

        recorder = self._make_recorder(
            max_timeline_bytes=4096, coalesce_window_seconds=0.0,
        )
        recorder.register_job("job-1", tmp_path)

        # Write enough events to exceed 4KB
        for i in range(200):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_created",
                "data": {"path": f"file-{i:04d}.md"},
                "timestamp": time.time(),
            }
            recorder._handle_event(event)

        # REVIEW FIX 2: Truncation is async — give background task time to run
        await asyncio.sleep(0.1)
        recorder.flush("job-1")

        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        size = jsonl_path.stat().st_size
        assert size <= 4096

    @pytest.mark.asyncio
    async def test_surviving_lines_are_valid_json(self, tmp_path: Path) -> None:
        """REVIEW FIX 1+5: Every surviving line after truncation must be valid JSON.

        This catches the corrupt-first-line bug from naive midpoint truncation.
        """
        import asyncio

        recorder = self._make_recorder(
            max_timeline_bytes=4096, coalesce_window_seconds=0.0,
        )
        recorder.register_job("job-1", tmp_path)

        for i in range(200):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_created",
                "data": {"path": f"file-{i:04d}.md"},
                "timestamp": time.time(),
            }
            recorder._handle_event(event)

        # Let async truncation complete
        await asyncio.sleep(0.1)
        recorder.flush("job-1")

        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        lines = [ln for ln in jsonl_path.read_text().strip().split("\n") if ln]
        assert len(lines) > 0, "Should have surviving events"
        # Every line must parse as valid JSON
        for i, line in enumerate(lines):
            try:
                parsed = json.loads(line)
                assert "event" in parsed, f"Line {i} missing 'event' key"
            except json.JSONDecodeError:
                pytest.fail(f"Line {i} is corrupt JSONL: {line!r}")


class TestLifecycle:
    """Verify start/stop subscribes/unsubscribes from EventBus.

    REVIEW FIX 1: Uses SemanticAnalyzer pattern — event_bus passed to start()/stop().
    REVIEW FIX 2: start() launches coalesce flush timer task.
    REVIEW FIX 4: Verifies unsubscribe() is called with the correct sub_id.
    REVIEW FIX 6: stop() closes all file handles for still-registered jobs.
    """

    @pytest.mark.asyncio
    async def test_start_subscribes(self) -> None:
        """start(event_bus) should subscribe and store the sub_id."""
        config = ObserverConfig()
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-123"
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        assert recorder._sub_id == "sub-123"
        await recorder.stop(bus)

    @pytest.mark.asyncio
    async def test_stop_unsubscribes(self) -> None:
        """stop(event_bus) should call unsubscribe and clear _sub_id."""
        config = ObserverConfig()
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-123"
        bus.unsubscribe = lambda sub_id: True
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        await recorder.stop(bus)
        assert recorder._sub_id is None

    @pytest.mark.asyncio
    async def test_stop_calls_unsubscribe_with_correct_id(self) -> None:
        """REVIEW FIX 4: Verify unsubscribe() receives the exact sub_id."""
        config = ObserverConfig()
        unsubscribe_calls: list[str] = []
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-456"
        bus.unsubscribe = lambda sub_id: unsubscribe_calls.append(sub_id) or True
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        await recorder.stop(bus)
        assert unsubscribe_calls == ["sub-456"]

    @pytest.mark.asyncio
    async def test_disabled_does_not_subscribe(self) -> None:
        """When observer is disabled, start() should not subscribe."""
        config = ObserverConfig(enabled=False)
        bus = AsyncMock()
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        assert recorder._sub_id is None
        await recorder.stop(bus)

    @pytest.mark.asyncio
    async def test_start_launches_coalesce_flush_timer(self) -> None:
        """REVIEW FIX 2: start() should create a periodic flush task when window > 0."""
        config = ObserverConfig(coalesce_window_seconds=1.0)
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-id"
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        assert recorder._flush_task is not None
        assert not recorder._flush_task.done()
        await recorder.stop(bus)

    @pytest.mark.asyncio
    async def test_stop_cancels_coalesce_flush_timer(self) -> None:
        """REVIEW FIX 2: stop() should cancel the periodic flush task."""
        config = ObserverConfig(coalesce_window_seconds=1.0)
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-id"
        bus.unsubscribe = lambda sub_id: True
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        flush_task = recorder._flush_task
        await recorder.stop(bus)
        assert recorder._flush_task is None
        assert flush_task.cancelled() or flush_task.done()

    @pytest.mark.asyncio
    async def test_no_flush_timer_when_zero_window(self) -> None:
        """REVIEW FIX 2: No flush timer when coalesce_window_seconds == 0."""
        config = ObserverConfig(coalesce_window_seconds=0.0)
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-id"
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        assert recorder._flush_task is None
        await recorder.stop(bus)

    @pytest.mark.asyncio
    async def test_stop_closes_all_remaining_jobs(self, tmp_path: Path) -> None:
        """REVIEW FIX 6: stop() should unregister all still-registered jobs."""
        config = ObserverConfig()
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-id"
        bus.unsubscribe = lambda sub_id: True
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)

        ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        recorder.register_job("job-1", ws1)
        recorder.register_job("job-2", ws2)

        await recorder.stop(bus)
        # All jobs should be unregistered
        assert len(recorder._jobs) == 0

    @pytest.mark.asyncio
    async def test_periodic_flush_expires_coalesced_events(
        self, tmp_path: Path,
    ) -> None:
        """REVIEW FIX 2: The periodic flush timer writes expired coalesce entries."""
        config = ObserverConfig(coalesce_window_seconds=0.2)
        bus = AsyncMock()
        bus.subscribe = lambda callback, event_filter=None: "sub-id"
        bus.unsubscribe = lambda sub_id: True
        recorder = ObserverRecorder(config=config)
        await recorder.start(bus)
        recorder.register_job("job-1", tmp_path)

        # Buffer a file_modified event via _handle_event (it goes into coalesce buffer)
        recorder._handle_event({
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_modified",
            "data": {"path": "delayed-file.md"},
            "timestamp": time.time(),
        })

        # The event should be in coalesce buffer, not yet in JSONL
        jsonl_path = tmp_path / ".marianne-observer.jsonl"
        assert jsonl_path.read_text().strip() == ""

        # Wait for the periodic flush to fire (window=0.2s, so wait a bit longer)
        await asyncio.sleep(0.5)

        # Now the event should have been flushed to disk
        content = jsonl_path.read_text().strip()
        assert content != "", "Periodic flush should have written the coalesced event"
        parsed = json.loads(content)
        assert parsed["data"]["path"] == "delayed-file.md"

        await recorder.stop(bus)


class TestGetRecentEvents:
    """Verify in-memory ring buffer retrieval.

    REVIEW FIX 5: get_recent_events returns newest-first ordering.
    """

    def _make_recorder(self) -> ObserverRecorder:
        config = ObserverConfig()
        return ObserverRecorder(config=config)

    def _make_recorder_started(self, bus: AsyncMock) -> ObserverRecorder:
        config = ObserverConfig()
        bus.subscribe = lambda callback, event_filter=None: "sub-id"
        return ObserverRecorder(config=config)

    def test_returns_events_for_job(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        event: ObserverEvent = {
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.process_spawned",
            "data": {"pid": 1234, "name": "pytest"},
            "timestamp": time.time(),
        }
        recorder._handle_event(event)
        recent = recorder.get_recent_events("job-1", limit=10)
        assert len(recent) == 1
        assert recent[0]["event"] == "observer.process_spawned"

    def test_respects_limit(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        for i in range(20):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_created",
                "data": {"path": f"file-{i}.md"},
                "timestamp": time.time(),
            }
            recorder._handle_event(event)
        recent = recorder.get_recent_events("job-1", limit=5)
        assert len(recent) == 5

    def test_returns_newest_first(self, tmp_path: Path) -> None:
        """REVIEW FIX 5: Write 20 events, request 3, verify they're the last 3 newest-first."""
        recorder = self._make_recorder()
        recorder.register_job("job-1", tmp_path)
        for i in range(20):
            event: ObserverEvent = {
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_created",
                "data": {"path": f"file-{i:02d}.md"},
                "timestamp": 1000.0 + i,
            }
            recorder._handle_event(event)

        recent = recorder.get_recent_events("job-1", limit=3)
        assert len(recent) == 3
        # Newest first: file-19, file-18, file-17
        assert recent[0]["data"]["path"] == "file-19.md"
        assert recent[1]["data"]["path"] == "file-18.md"
        assert recent[2]["data"]["path"] == "file-17.md"

    def test_none_job_id_returns_all(self, tmp_path: Path) -> None:
        recorder = self._make_recorder()
        ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        recorder.register_job("job-1", ws1)
        recorder.register_job("job-2", ws2)
        for jid in ("job-1", "job-2"):
            event: ObserverEvent = {
                "job_id": jid,
                "sheet_num": 0,
                "event": "observer.process_spawned",
                "data": {"pid": 1234, "name": "test"},
                "timestamp": time.time(),
            }
            recorder._handle_event(event)
        recent = recorder.get_recent_events(None, limit=50)
        assert len(recent) == 2

    def test_unknown_job_returns_empty(self) -> None:
        recorder = self._make_recorder()
        assert recorder.get_recent_events("nonexistent", limit=10) == []


class TestObserverEventsIPC:
    """Verify the daemon.observer_events IPC handler in _register_methods."""

    def _extract_handler(self, method_name: str) -> Any:
        """Register RPC methods and extract the handler for *method_name*."""
        from unittest.mock import MagicMock

        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.process import DaemonProcess

        config = DaemonConfig()
        dp = DaemonProcess(config)
        handler = MagicMock()
        manager = MagicMock()
        dp._register_methods(handler, manager, health=None)

        for call in handler.register.call_args_list:
            if call.args[0] == method_name:
                return call.args[1], manager
        pytest.fail(f"Handler {method_name!r} not registered")

    @pytest.mark.asyncio
    async def test_handler_registered(self) -> None:
        """daemon.observer_events must be in registered RPC methods."""
        from unittest.mock import MagicMock

        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.process import DaemonProcess

        config = DaemonConfig()
        dp = DaemonProcess(config)
        handler = MagicMock()
        manager = MagicMock()
        dp._register_methods(handler, manager, health=None)

        registered = {c.args[0] for c in handler.register.call_args_list}
        assert "daemon.observer_events" in registered

    @pytest.mark.asyncio
    async def test_returns_empty_when_recorder_is_none(self) -> None:
        """When observer_recorder is None, return {"events": []}."""
        handler_fn, manager = self._extract_handler("daemon.observer_events")
        manager.observer_recorder = None

        result = await handler_fn({"job_id": None, "limit": 10}, None)
        assert result == {"events": []}

    @pytest.mark.asyncio
    async def test_delegates_to_recorder(self, tmp_path: Path) -> None:
        """When observer_recorder exists, delegate to get_recent_events."""
        handler_fn, manager = self._extract_handler("daemon.observer_events")
        recorder = ObserverRecorder(config=ObserverConfig())
        recorder.register_job("job-1", tmp_path)
        recorder._handle_event({
            "job_id": "job-1",
            "sheet_num": 0,
            "event": "observer.file_created",
            "data": {"path": "output.md"},
            "timestamp": 1000.0,
        })
        manager.observer_recorder = recorder

        result = await handler_fn({"job_id": "job-1", "limit": 50}, None)
        assert len(result["events"]) == 1
        assert result["events"][0]["event"] == "observer.file_created"

    @pytest.mark.asyncio
    async def test_defaults_limit_to_50(self, tmp_path: Path) -> None:
        """When limit is omitted, default to 50."""
        handler_fn, manager = self._extract_handler("daemon.observer_events")
        recorder = ObserverRecorder(config=ObserverConfig())
        recorder.register_job("job-1", tmp_path)
        for i in range(60):
            recorder._handle_event({
                "job_id": "job-1",
                "sheet_num": 0,
                "event": "observer.file_created",
                "data": {"path": f"file-{i}.md"},
                "timestamp": 1000.0 + i,
            })
        manager.observer_recorder = recorder

        result = await handler_fn({"job_id": "job-1"}, None)
        assert len(result["events"]) == 50

    @pytest.mark.asyncio
    async def test_null_job_id_returns_all_jobs(self, tmp_path: Path) -> None:
        """job_id=null aggregates events across all registered jobs."""
        handler_fn, manager = self._extract_handler("daemon.observer_events")
        recorder = ObserverRecorder(config=ObserverConfig())
        ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
        ws1.mkdir()
        ws2.mkdir()
        recorder.register_job("job-1", ws1)
        recorder.register_job("job-2", ws2)
        for jid in ("job-1", "job-2"):
            recorder._handle_event({
                "job_id": jid,
                "sheet_num": 0,
                "event": "observer.process_spawned",
                "data": {"pid": 1234},
                "timestamp": 1000.0,
            })
        manager.observer_recorder = recorder

        result = await handler_fn({"job_id": None, "limit": 50}, None)
        assert len(result["events"]) == 2


class _BrokenWriter:
    """A file-like object that raises OSError on write."""

    def write(self, _data: str) -> int:
        raise OSError("disk full")

    def flush(self) -> None:
        raise OSError("disk full")

    def close(self) -> None:
        pass
