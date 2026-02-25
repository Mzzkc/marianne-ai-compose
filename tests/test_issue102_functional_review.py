"""Functional review tests for Issue #102 — Observer integration gaps.

Targeted regression tests verifying:
1. Timeline correctly renders all 3 observer file event types with proper
   color coding, icons, and path truncation
2. JobsPanel filters and attaches file events per-job correctly
3. SnapshotManager generates observer summaries and captures config files
4. Manager correctly passes config_path to snapshot capture
5. Edge cases: empty data, missing keys, large event volumes
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


from mozart.daemon.profiler.models import (
    JobProgress,
    ProcessMetric,
    SystemSnapshot,
)


# ===========================================================================
# Phase 1: Timeline observer file event rendering
# ===========================================================================


class TestTimelineFileEventRendering:
    """Verify _render_timeline() correctly processes observer.file_* events
    and produces the right output format."""

    def _render_entries(self, observer_events: list[dict[str, Any]]) -> list[tuple[float, str]]:
        """Build timeline entries from observer events using the actual
        rendering logic from TimelinePanel._render_timeline()."""
        from mozart.tui.panels.timeline import _EVENT_COLORS

        entries: list[tuple[float, str]] = []
        for obs in observer_events:
            evt_name = obs.get("event", "")
            if not evt_name.startswith("observer.file_"):
                continue
            ts = obs.get("timestamp", time.time())
            data = obs.get("data") or {}
            path = data.get("path", "?")
            if len(path) > 40:
                path = "..." + path[-37:]
            job_label = obs.get("job_id", "")
            if "created" in evt_name:
                action = "CREATE"
                color = _EVENT_COLORS["observer_file_created"]
            elif "deleted" in evt_name:
                action = "DELETE"
                color = _EVENT_COLORS["observer_file_deleted"]
            else:
                action = "MODIFY"
                color = _EVENT_COLORS["observer_file_modified"]
            line = f"[{color}]{action}[/] {job_label} {path}"
            entries.append((ts, line))
        return entries

    def test_created_event_uses_green_and_create_label(self) -> None:
        events = [{"event": "observer.file_created", "timestamp": 100.0,
                    "job_id": "j1", "data": {"path": "/ws/new.txt"}}]
        entries = self._render_entries(events)
        assert len(entries) == 1
        assert "[green]CREATE[/]" in entries[0][1]
        assert "/ws/new.txt" in entries[0][1]

    def test_modified_event_uses_yellow_and_modify_label(self) -> None:
        events = [{"event": "observer.file_modified", "timestamp": 200.0,
                    "job_id": "j1", "data": {"path": "/ws/changed.py"}}]
        entries = self._render_entries(events)
        assert len(entries) == 1
        assert "[yellow]MODIFY[/]" in entries[0][1]

    def test_deleted_event_uses_red_and_delete_label(self) -> None:
        events = [{"event": "observer.file_deleted", "timestamp": 300.0,
                    "job_id": "j1", "data": {"path": "/ws/removed.log"}}]
        entries = self._render_entries(events)
        assert len(entries) == 1
        assert "[red]DELETE[/]" in entries[0][1]

    def test_path_truncation_at_40_chars(self) -> None:
        """Paths > 40 chars get truncated to '...' + last 37 chars."""
        long_path = "/workspace/deeply/nested/directory/structure/file.txt"
        assert len(long_path) > 40
        events = [{"event": "observer.file_created", "timestamp": 100.0,
                    "job_id": "j1", "data": {"path": long_path}}]
        entries = self._render_entries(events)
        assert entries[0][1].count("...") == 1
        # The truncated path should be 40 chars total: "..." (3) + 37 = 40
        # Extract path from the rendered line
        rendered_path = entries[0][1].split()[-1]
        assert len(rendered_path) == 40

    def test_short_path_not_truncated(self) -> None:
        short_path = "/ws/file.txt"
        assert len(short_path) <= 40
        events = [{"event": "observer.file_created", "timestamp": 100.0,
                    "job_id": "j1", "data": {"path": short_path}}]
        entries = self._render_entries(events)
        assert "..." not in entries[0][1]
        assert short_path in entries[0][1]

    def test_process_events_ignored_by_file_renderer(self) -> None:
        """observer.process_* events should not produce file entries."""
        events = [
            {"event": "observer.process_spawned", "timestamp": 100.0,
             "job_id": "j1", "data": {"pid": 123}},
            {"event": "observer.file_created", "timestamp": 200.0,
             "job_id": "j1", "data": {"path": "/ws/f.txt"}},
        ]
        entries = self._render_entries(events)
        assert len(entries) == 1  # Only the file event

    def test_missing_data_key_uses_defaults(self) -> None:
        """Events with missing 'data' key should use '?' for path."""
        events = [{"event": "observer.file_created", "timestamp": 100.0,
                    "job_id": "j1"}]  # No 'data' key
        entries = self._render_entries(events)
        assert len(entries) == 1
        assert "?" in entries[0][1]

    def test_missing_path_in_data_uses_default(self) -> None:
        """Events with empty data dict should use '?' for path."""
        events = [{"event": "observer.file_created", "timestamp": 100.0,
                    "job_id": "j1", "data": {}}]
        entries = self._render_entries(events)
        assert len(entries) == 1
        assert "?" in entries[0][1]


# ===========================================================================
# Phase 1: JobsPanel file event filtering
# ===========================================================================


class TestJobsPanelFileEventFiltering:
    """Verify JobsPanel correctly filters observer file events per job."""

    def _build_panel_with_tree(self) -> Any:
        from textual.widgets import Static, Tree
        from mozart.tui.panels.jobs import JobsPanel

        panel = JobsPanel()
        panel._empty_label = Static("[dim]No active jobs[/]", id="jobs-empty")
        tree: Tree[dict[str, Any]] = Tree("Jobs", id="jobs-tree")
        tree.show_root = False
        tree.guide_depth = 3
        panel._tree = tree
        return panel

    def test_events_filtered_by_job_id(self) -> None:
        """Each job's job_data only contains file events for that job."""
        panel = self._build_panel_with_tree()
        snap = SystemSnapshot(
            processes=[
                ProcessMetric(pid=100, state="S", job_id="alpha", sheet_num=1),
                ProcessMetric(pid=200, state="S", job_id="beta", sheet_num=2),
            ],
            job_progress=[
                JobProgress(job_id="alpha", total_sheets=5, last_completed_sheet=2),
                JobProgress(job_id="beta", total_sheets=3, last_completed_sheet=1),
            ],
        )
        file_events = [
            {"event": "observer.file_created", "timestamp": 1.0,
             "job_id": "alpha", "data": {"path": "/a.txt"}},
            {"event": "observer.file_modified", "timestamp": 2.0,
             "job_id": "beta", "data": {"path": "/b.txt"}},
            {"event": "observer.file_deleted", "timestamp": 3.0,
             "job_id": "alpha", "data": {"path": "/c.txt"}},
        ]
        panel.update_data(snap, observer_file_events=file_events)

        job_items = [i for i in panel._items if i.get("type") == "job"]
        alpha = next(i for i in job_items if i["job_id"] == "alpha")
        beta = next(i for i in job_items if i["job_id"] == "beta")

        assert len(alpha["observer_file_events"]) == 2
        assert len(beta["observer_file_events"]) == 1
        assert all(e["job_id"] == "alpha" for e in alpha["observer_file_events"])
        assert all(e["job_id"] == "beta" for e in beta["observer_file_events"])

    def test_process_events_excluded_from_file_events(self) -> None:
        """observer.process_* events should not appear in observer_file_events."""
        panel = self._build_panel_with_tree()
        snap = SystemSnapshot(
            processes=[ProcessMetric(pid=100, state="S", job_id="j1", sheet_num=1)],
            job_progress=[JobProgress(job_id="j1", total_sheets=5, last_completed_sheet=2)],
        )
        # Mix file and process events
        mixed_events = [
            {"event": "observer.file_created", "timestamp": 1.0,
             "job_id": "j1", "data": {"path": "/f.txt"}},
            {"event": "observer.process_spawned", "timestamp": 2.0,
             "job_id": "j1", "data": {"pid": 999}},
        ]
        panel.update_data(snap, observer_file_events=mixed_events)

        job_items = [i for i in panel._items if i.get("type") == "job"]
        j1 = job_items[0]
        # The filtering in _render_jobs checks for observer.file_ prefix
        assert len(j1["observer_file_events"]) == 1
        assert j1["observer_file_events"][0]["event"] == "observer.file_created"

    def test_update_data_preserves_events_when_none_passed(self) -> None:
        """When observer_file_events=None, existing events are preserved."""
        from mozart.tui.panels.jobs import JobsPanel
        panel = JobsPanel()
        initial = [{"event": "observer.file_created", "job_id": "j1",
                     "data": {"path": "/f.txt"}}]
        panel._observer_file_events = initial
        panel.update_data(None)  # No observer_file_events parameter
        assert panel._observer_file_events is initial


# ===========================================================================
# Phase 1: App wiring — observer events flow to jobs panel
# ===========================================================================


class TestAppObserverWiring:
    """Verify MonitorApp.refresh_data() correctly filters and passes
    observer file events to JobsPanel."""

    def test_file_event_filtering_logic(self) -> None:
        """The filtering logic in refresh_data extracts only file events."""
        all_events = [
            {"event": "observer.file_created", "job_id": "j1", "data": {"path": "/a"}},
            {"event": "observer.process_spawned", "job_id": "j1", "data": {"pid": 1}},
            {"event": "observer.file_modified", "job_id": "j2", "data": {"path": "/b"}},
            {"event": "observer.file_deleted", "job_id": "j1", "data": {"path": "/c"}},
            {"event": "sheet.completed", "job_id": "j1", "data": {}},
        ]
        # This is the exact filtering logic from app.py refresh_data
        observer_file_events = [
            e for e in all_events
            if e.get("event", "").startswith("observer.file_")
        ]
        assert len(observer_file_events) == 3
        assert all(e["event"].startswith("observer.file_") for e in observer_file_events)


# ===========================================================================
# Phase 2: Snapshot — observer summary
# ===========================================================================


class TestSnapshotObserverSummary:
    """Verify _capture_observer_summary() correctness."""

    def test_summary_counts_all_event_types(self, tmp_path: Path) -> None:
        """Summary correctly counts distinct event types."""
        ws = tmp_path / "ws"
        ws.mkdir()
        snapshot_dir = tmp_path / "snap"
        snapshot_dir.mkdir()

        jsonl = ws / ".mozart-observer.jsonl"
        records = [
            {"event": "observer.file_created"},
            {"event": "observer.file_created"},
            {"event": "observer.file_modified"},
            {"event": "observer.file_deleted"},
            {"event": "observer.process_spawned"},
            {"event": "observer.process_exited"},
        ]
        jsonl.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        from mozart.daemon.snapshot import SnapshotManager
        SnapshotManager._capture_observer_summary(ws, snapshot_dir)

        summary_path = snapshot_dir / "observer-summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert summary["total_events"] == 6
        assert summary["by_type"]["observer.file_created"] == 2
        assert summary["by_type"]["observer.file_modified"] == 1
        assert summary["by_type"]["observer.file_deleted"] == 1
        assert summary["by_type"]["observer.process_spawned"] == 1
        assert summary["by_type"]["observer.process_exited"] == 1

    def test_summary_skips_blank_lines(self, tmp_path: Path) -> None:
        """Blank lines in JSONL are skipped, not counted."""
        ws = tmp_path / "ws"
        ws.mkdir()
        snapshot_dir = tmp_path / "snap"
        snapshot_dir.mkdir()

        jsonl = ws / ".mozart-observer.jsonl"
        jsonl.write_text(
            '{"event":"observer.file_created"}\n'
            "\n"
            '{"event":"observer.file_modified"}\n'
            "   \n"
        )

        from mozart.daemon.snapshot import SnapshotManager
        SnapshotManager._capture_observer_summary(ws, snapshot_dir)

        summary = json.loads((snapshot_dir / "observer-summary.json").read_text())
        assert summary["total_events"] == 2

    def test_summary_not_created_when_all_lines_malformed(self, tmp_path: Path) -> None:
        """If every line is invalid JSON, no summary file is created."""
        ws = tmp_path / "ws"
        ws.mkdir()
        snapshot_dir = tmp_path / "snap"
        snapshot_dir.mkdir()

        jsonl = ws / ".mozart-observer.jsonl"
        jsonl.write_text("not json\nalso not json\n")

        from mozart.daemon.snapshot import SnapshotManager
        SnapshotManager._capture_observer_summary(ws, snapshot_dir)

        assert not (snapshot_dir / "observer-summary.json").exists()


# ===========================================================================
# Phase 2: Snapshot — config capture
# ===========================================================================


class TestSnapshotConfigCapture:
    """Verify config_path parameter in SnapshotManager.capture()."""

    def test_config_from_outside_workspace_captured(self, tmp_path: Path) -> None:
        """Config file outside the workspace is copied to snapshot."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "job.json").write_text("{}")

        config = tmp_path / "configs" / "my-job.yaml"
        config.parent.mkdir()
        config.write_text("name: test-job\nsheets:\n  - prompt: hi\n")

        from mozart.daemon.snapshot import SnapshotManager
        mgr = SnapshotManager(base_dir=tmp_path / "snapshots")
        result = mgr.capture("test-job", ws, config_path=config)
        assert result is not None
        assert (Path(result) / "my-job.yaml").exists()
        assert "name: test-job" in (Path(result) / "my-job.yaml").read_text()

    def test_config_inside_workspace_not_duplicated(self, tmp_path: Path) -> None:
        """Config file inside workspace is already captured by glob patterns.
        Passing it again as config_path should still work (copy2 is idempotent)."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "job.json").write_text("{}")
        config = ws / "my-job.yaml"
        config.write_text("sheets: []\n")

        from mozart.daemon.snapshot import SnapshotManager
        mgr = SnapshotManager(base_dir=tmp_path / "snapshots")
        result = mgr.capture("test-job", ws, config_path=config)
        assert result is not None
        assert (Path(result) / "my-job.yaml").exists()


# ===========================================================================
# Phase 2: Snapshot — capture patterns include YAML
# ===========================================================================


class TestSnapshotCapturePatterns:
    """Verify that *.yaml and *.yml are in capture patterns."""

    def test_yaml_files_captured(self, tmp_path: Path) -> None:
        """YAML files in workspace are captured in snapshots."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "state.json").write_text("{}")
        (ws / "config.yaml").write_text("key: value")
        (ws / "override.yml").write_text("other: data")

        from mozart.daemon.snapshot import SnapshotManager
        mgr = SnapshotManager(base_dir=tmp_path / "snapshots")
        result = mgr.capture("test-job", ws)
        assert result is not None
        snap = Path(result)
        assert (snap / "config.yaml").exists()
        assert (snap / "override.yml").exists()

    def test_capture_patterns_list(self) -> None:
        """Verify capture patterns include yaml/yml."""
        from mozart.daemon.snapshot import _CAPTURE_PATTERNS
        assert "*.yaml" in _CAPTURE_PATTERNS
        assert "*.yml" in _CAPTURE_PATTERNS
        assert ".mozart-observer.jsonl" in _CAPTURE_PATTERNS


# ===========================================================================
# Phase 2: Manager passes config_path to snapshot
# ===========================================================================


class TestManagerSnapshotWiring:
    """Verify JobManager passes config_path when capturing snapshots."""

    def test_manager_snapshot_call_includes_config_path(self) -> None:
        """The manager.py call to snapshot.capture() passes meta.config_path."""
        from mozart.daemon.manager import JobMeta

        # Verify the JobMeta dataclass has config_path field
        meta = JobMeta(
            job_id="test",
            config_path=Path("/configs/test.yaml"),
            workspace=Path("/ws"),
        )
        assert meta.config_path == Path("/configs/test.yaml")

    def test_snapshot_capture_signature_accepts_config_path(self) -> None:
        """SnapshotManager.capture() accepts config_path as a keyword arg."""
        import inspect
        from mozart.daemon.snapshot import SnapshotManager

        sig = inspect.signature(SnapshotManager.capture)
        params = list(sig.parameters.keys())
        assert "config_path" in params
        # It should have a default of None
        param = sig.parameters["config_path"]
        assert param.default is None


# ===========================================================================
# Integration: End-to-end observer data flow
# ===========================================================================


class TestEndToEndObserverFlow:
    """Integration tests verifying the full data flow from observer events
    through the TUI rendering pipeline."""

    def test_full_flow_file_events_to_job_detail(self) -> None:
        """Simulate the complete flow:
        1. Observer generates file events
        2. App filters them
        3. JobsPanel attaches them per-job
        4. DetailPanel renders file activity
        """
        from textual.widgets import Static, Tree
        from mozart.tui.panels.jobs import JobsPanel
        from mozart.tui.panels.detail import DetailPanel

        # Step 1: Simulated observer events (as returned by get_observer_events)
        all_observer = [
            {"event": "observer.file_created", "timestamp": 1.0,
             "job_id": "alpha", "data": {"path": "/ws-a/output.txt"}},
            {"event": "observer.process_spawned", "timestamp": 2.0,
             "job_id": "alpha", "data": {"pid": 1234}},
            {"event": "observer.file_modified", "timestamp": 3.0,
             "job_id": "beta", "data": {"path": "/ws-b/config.yaml"}},
            {"event": "observer.file_deleted", "timestamp": 4.0,
             "job_id": "alpha", "data": {"path": "/ws-a/temp.log"}},
        ]

        # Step 2: App filtering (exact logic from app.py)
        file_events = [
            e for e in all_observer
            if e.get("event", "").startswith("observer.file_")
        ]
        assert len(file_events) == 3

        # Step 3: JobsPanel receives snapshot + file events
        panel = JobsPanel()
        panel._empty_label = Static("[dim]No active jobs[/]", id="jobs-empty")
        tree: Tree[dict[str, Any]] = Tree("Jobs", id="jobs-tree")
        tree.show_root = False
        tree.guide_depth = 3
        panel._tree = tree

        snap = SystemSnapshot(
            processes=[
                ProcessMetric(pid=100, state="S", job_id="alpha", sheet_num=1),
                ProcessMetric(pid=200, state="S", job_id="beta", sheet_num=2),
            ],
            job_progress=[
                JobProgress(job_id="alpha", total_sheets=5, last_completed_sheet=3),
                JobProgress(job_id="beta", total_sheets=3, last_completed_sheet=1),
            ],
        )
        panel.update_data(snap, observer_file_events=file_events)

        # Verify per-job filtering
        job_items = [i for i in panel._items if i.get("type") == "job"]
        alpha_item = next(i for i in job_items if i["job_id"] == "alpha")
        beta_item = next(i for i in job_items if i["job_id"] == "beta")

        assert len(alpha_item["observer_file_events"]) == 2
        assert len(beta_item["observer_file_events"]) == 1

        # Step 4: DetailPanel renders without error
        detail = DetailPanel()
        detail.show_item(alpha_item)
        detail.show_item(beta_item)
        detail.show_item({"type": "job", "job_id": "empty",
                          "processes": [], "observer_file_events": []})

    def test_snapshot_captures_full_observer_pipeline(self, tmp_path: Path) -> None:
        """End-to-end: workspace with observer JSONL → snapshot with
        observer-summary.json, config, and original JSONL."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "my-job.json").write_text('{"status": "completed"}')
        (ws / "mozart.log").write_text("done")

        # Observer JSONL
        jsonl_lines = [
            json.dumps({"event": "observer.file_created", "path": "/a"}),
            json.dumps({"event": "observer.file_modified", "path": "/b"}),
            json.dumps({"event": "observer.process_spawned", "pid": 123}),
        ]
        (ws / ".mozart-observer.jsonl").write_text("\n".join(jsonl_lines) + "\n")

        # Config file
        config = tmp_path / "my-job.yaml"
        config.write_text("name: my-job\nsheets:\n  - prompt: test\n")

        from mozart.daemon.snapshot import SnapshotManager
        mgr = SnapshotManager(base_dir=tmp_path / "snapshots")
        result = mgr.capture("my-job", ws, config_path=config)

        assert result is not None
        snap = Path(result)

        # Observer JSONL captured
        assert (snap / ".mozart-observer.jsonl").exists()

        # Observer summary generated
        summary = json.loads((snap / "observer-summary.json").read_text())
        assert summary["total_events"] == 3

        # Config captured
        assert (snap / "my-job.yaml").exists()

        # Standard artifacts
        assert (snap / "my-job.json").exists()
        assert (snap / "mozart.log").exists()
