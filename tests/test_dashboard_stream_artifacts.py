"""Additional tests for dashboard stream and artifact routes.

Covers SSE streaming helpers (_read_tail_lines, _make_log_event, _log_stream),
stream endpoint parameter validation, and artifact security paths.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.dashboard.app import create_app
from marianne.state.json_backend import JsonStateBackend

# Fixed timestamp for deterministic tests
_FIXED_TIME = datetime(2024, 1, 15, 12, 0, 0)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_state_dir():
    """Create temporary directory for state backend."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def app(temp_state_dir):
    """Create test app with temp state backend."""
    backend = JsonStateBackend(temp_state_dir)
    return create_app(state_backend=backend, cors_origins=["*"])


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def workspace_with_logs(temp_state_dir):
    """Create a workspace with a log file and return (workspace, log_file)."""
    workspace = temp_state_dir / "test-workspace"
    workspace.mkdir()
    log_file = workspace / "marianne.log"
    log_content = "Line 1: Job started\nLine 2: Sheet 1 executing\nLine 3: Sheet 1 done\n"
    log_file.write_text(log_content)
    return workspace, log_file


@pytest.fixture
def job_state_with_worktree(workspace_with_logs):
    """Create a CheckpointState pointing to the workspace with logs."""
    workspace, _ = workspace_with_logs
    return CheckpointState(
        job_id="test-123",
        job_name="Test Job",
        status=JobStatus.RUNNING,
        total_sheets=3,
        worktree_path=str(workspace),
        created_at=_FIXED_TIME,
        updated_at=_FIXED_TIME,
    )


# =============================================================================
# Stream helper unit tests
# =============================================================================


class TestReadTailLines:
    """Tests for _read_tail_lines helper function."""

    def test_read_tail_lines_basic(self, tmp_path: Path) -> None:
        """Should return last N lines and total count."""
        from marianne.dashboard.routes.stream import _read_tail_lines

        log = tmp_path / "test.log"
        log.write_text("line1\nline2\nline3\nline4\nline5\n")

        lines, total = _read_tail_lines(log, 3)
        assert total == 5
        assert len(lines) == 3
        assert "line3\n" in lines
        assert "line5\n" in lines

    def test_read_tail_lines_fewer_than_requested(self, tmp_path: Path) -> None:
        """When file has fewer lines than requested, return all."""
        from marianne.dashboard.routes.stream import _read_tail_lines

        log = tmp_path / "test.log"
        log.write_text("only\ntwo\n")

        lines, total = _read_tail_lines(log, 10)
        assert total == 2
        assert len(lines) == 2

    def test_read_tail_lines_zero(self, tmp_path: Path) -> None:
        """Requesting 0 tail lines should return empty list."""
        from marianne.dashboard.routes.stream import _read_tail_lines

        log = tmp_path / "test.log"
        log.write_text("line1\nline2\n")

        lines, total = _read_tail_lines(log, 0)
        assert total == 2
        assert len(lines) == 0

    def test_read_tail_lines_empty_file(self, tmp_path: Path) -> None:
        """Empty file should return empty list with 0 total."""
        from marianne.dashboard.routes.stream import _read_tail_lines

        log = tmp_path / "test.log"
        log.write_text("")

        lines, total = _read_tail_lines(log, 10)
        assert total == 0
        assert len(lines) == 0


class TestMakeLogEvent:
    """Tests for _make_log_event helper function."""

    def test_make_log_event_initial(self) -> None:
        """Initial log event should strip trailing newline."""
        from marianne.dashboard.routes.stream import _make_log_event

        event_str = _make_log_event("hello world\n", 1, is_initial_event=True, event_id="log-0")
        assert "event: log" in event_str
        assert "hello world" in event_str
        # Newline should be stripped for initial events
        data_line = [x for x in event_str.split("\n") if x.startswith("data:")][0]
        data = json.loads(data_line.replace("data: ", ""))
        assert data["line"] == "hello world"
        assert data["initial"] is True
        assert data["line_number"] == 1

    def test_make_log_event_follow(self) -> None:
        """Follow-mode log event should preserve line content."""
        from marianne.dashboard.routes.stream import _make_log_event

        event_str = _make_log_event("new line", 42, is_initial_event=False, event_id="log-42")
        data_line = [x for x in event_str.split("\n") if x.startswith("data:")][0]
        data = json.loads(data_line.replace("data: ", ""))
        assert data["line"] == "new line"
        assert data["initial"] is False
        assert data["line_number"] == 42

    def test_make_log_event_strips_terminal_escape_codes(self) -> None:
        """Displayed logs should not include raw terminal control codes."""
        from marianne.dashboard.routes.stream import _make_log_event

        event_str = _make_log_event("\x1b[31mERROR\x1b[0m\x07\n", 7, True, "log-7")
        data_line = [x for x in event_str.split("\n") if x.startswith("data:")][0]
        data = json.loads(data_line.replace("data: ", ""))

        assert data["line"] == "ERROR"


class TestLogStreamNoFollow:
    """Tests for _log_stream in non-follow mode."""

    @pytest.mark.asyncio
    async def test_log_stream_no_follow(self, tmp_path: Path) -> None:
        """Non-follow mode should emit initial lines then complete event."""
        from marianne.dashboard.routes.stream import _log_stream

        log = tmp_path / "test.log"
        log.write_text("line1\nline2\nline3\n")

        events = []
        async for event in _log_stream(log, follow=False, tail_lines=2):
            events.append(event)

        # Should get 2 log events + 1 log_complete event
        assert len(events) == 3
        # Last event should be log_complete
        assert "log_complete" in events[-1]

    @pytest.mark.asyncio
    async def test_log_stream_no_follow_missing_file(self, tmp_path: Path) -> None:
        """Non-follow mode with missing file should emit complete event."""
        from marianne.dashboard.routes.stream import _log_stream

        log = tmp_path / "nonexistent.log"

        events = []
        async for event in _log_stream(log, follow=False, tail_lines=10):
            events.append(event)

        # Should get just a log_complete event
        assert len(events) == 1
        assert "log_complete" in events[0]


# =============================================================================
# Stream endpoint tests via TestClient
# =============================================================================


class TestStreamEndpoints:
    """Tests for stream API endpoints."""

    def test_stream_status_poll_interval_too_low(self, client, job_state_with_worktree) -> None:
        """Poll interval below 0.1 should be rejected."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state_with_worktree)
            response = client.get("/api/jobs/test-123/stream?poll_interval=0.01")

        assert response.status_code == 400
        assert "Poll interval must be between" in response.json()["detail"]

    def test_stream_status_poll_interval_too_high(self, client, job_state_with_worktree) -> None:
        """Poll interval above 30.0 should be rejected."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state_with_worktree)
            response = client.get("/api/jobs/test-123/stream?poll_interval=60")

        assert response.status_code == 400

    def test_stream_logs_tail_lines_negative(self, client, job_state_with_worktree) -> None:
        """Negative tail_lines should be rejected."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state_with_worktree)
            response = client.get("/api/jobs/test-123/logs?tail_lines=-1")

        assert response.status_code == 400
        assert "tail_lines must be between" in response.json()["detail"]

    def test_stream_logs_tail_lines_too_high(self, client, job_state_with_worktree) -> None:
        """tail_lines above 1000 should be rejected."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state_with_worktree)
            response = client.get("/api/jobs/test-123/logs?tail_lines=5000")

        assert response.status_code == 400

    def test_stream_logs_job_not_found(self, client) -> None:
        """Log streaming for nonexistent job should return 404."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=None)
            response = client.get("/api/jobs/nonexistent/logs")

        assert response.status_code == 404

    def test_download_logs_for_non_worktree_registry_workspace(
        self, client, temp_state_dir
    ) -> None:
        """Non-isolated jobs should read logs from registry workspace metadata."""
        workspace = temp_state_dir / "plain-workspace"
        workspace.mkdir()
        observer_log = workspace / ".marianne-observer.jsonl"
        observer_log.write_text('{"event":"observer.started","job_id":"plain-job"}\n')
        os.utime(observer_log, (_FIXED_TIME.timestamp(), _FIXED_TIME.timestamp()))

        job_state = CheckpointState(
            job_id="plain-job",
            job_name="Plain Job",
            status=JobStatus.COMPLETED,
            total_sheets=1,
            started_at=_FIXED_TIME,
            completed_at=_FIXED_TIME,
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        registry_metadata = {
            "workspace": str(workspace),
            "log_path": None,
            "snapshot_path": None,
            "config_path": None,
        }
        with (
            patch("marianne.dashboard.app._state_backend") as mock_backend,
            patch(
                "marianne.dashboard.routes.stream._read_registry_job_metadata",
                return_value=registry_metadata,
            ),
            patch("marianne.dashboard.routes.stream._append_conductor_log_source"),
        ):
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/plain-job/logs/static")

        assert response.status_code == 200
        assert "observer.started" in response.text

    def test_download_logs_ignores_stale_workspace_logs(
        self, client, temp_state_dir
    ) -> None:
        """Shared workspace logs outside the job window should not be shown."""
        workspace = temp_state_dir / "shared-workspace"
        logs_dir = workspace / "logs"
        logs_dir.mkdir(parents=True)
        stale_log = logs_dir / "sheet-01.stdout.log"
        stale_log.write_text("old unrelated sheet output\n")
        old_time = _FIXED_TIME.timestamp() - (90 * 24 * 60 * 60)
        os.utime(stale_log, (old_time, old_time))

        observer_log = workspace / ".marianne-observer.jsonl"
        observer_log.write_text(
            '{"event":"observer.started","job_id":"other-job"}\n'
            '{"event":"observer.stopped","job_id":"fresh-job"}\n'
        )
        os.utime(observer_log, (_FIXED_TIME.timestamp(), _FIXED_TIME.timestamp()))

        job_state = CheckpointState(
            job_id="fresh-job",
            job_name="Fresh Job",
            status=JobStatus.COMPLETED,
            total_sheets=1,
            started_at=_FIXED_TIME,
            completed_at=_FIXED_TIME,
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        registry_metadata = {
            "workspace": str(workspace),
            "log_path": None,
            "snapshot_path": None,
            "config_path": None,
        }
        with (
            patch("marianne.dashboard.app._state_backend") as mock_backend,
            patch(
                "marianne.dashboard.routes.stream._read_registry_job_metadata",
                return_value=registry_metadata,
            ),
            patch("marianne.dashboard.routes.stream._append_conductor_log_source"),
        ):
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/fresh-job/logs/static")

        assert response.status_code == 200
        assert "observer.stopped" in response.text
        assert "other-job" not in response.text
        assert "old unrelated sheet output" not in response.text

    def test_download_logs_content(self, client, job_state_with_worktree) -> None:
        """Static log download should include header and content."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state_with_worktree)
            response = client.get("/api/jobs/test-123/logs/static")

        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert "Job started" in response.text
        assert "# Marianne Job Logs" in response.text

    def test_log_info_returns_metadata(self, client, job_state_with_worktree) -> None:
        """Log info endpoint should return size and line count."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state_with_worktree)
            response = client.get("/api/jobs/test-123/logs/info")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-123"
        assert data["log_file"] == "marianne.log"
        assert data["lines"] == 3
        assert data["size_bytes"] > 0
        assert data["download_available"] is True
        assert data["download_limit_bytes"] == 50 * 1024 * 1024

    def test_log_info_job_not_found(self, client) -> None:
        """Log info for nonexistent job should return 404."""
        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=None)
            response = client.get("/api/jobs/nonexistent/logs/info")

        assert response.status_code == 404


class TestJobStatusBridgeStream:
    """Tests for bridge-backed job status SSE behavior."""

    @pytest.mark.asyncio
    async def test_bridge_emits_current_sheet_only_change(self) -> None:
        from marianne.dashboard.routes.stream import _job_status_via_bridge

        initial_state = CheckpointState(
            job_id="stream-current",
            job_name="Stream Current",
            status=JobStatus.RUNNING,
            total_sheets=2,
            current_sheet=1,
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.IN_PROGRESS),
                2: SheetState(sheet_num=2, status=SheetStatus.PENDING),
            },
        )
        changed_state = initial_state.model_copy(update={"current_sheet": 2})

        class _Backend:
            async def load(self, job_id: str) -> CheckpointState | None:
                assert job_id == "stream-current"
                return changed_state

        class _Bridge:
            async def job_events(self, job_id: str):
                assert job_id == "stream-current"
                yield {"event": "job.updated", "data": "{}"}
                yield {"event": "bridge_stopped", "data": "{}"}

        events = [
            event
            async for event in _job_status_via_bridge(
                "stream-current",
                initial_state,
                _Backend(),
                _Bridge(),
            )
        ]
        status_events = [event for event in events if "event: job_status" in event]

        assert len(status_events) == 2
        assert '"current_sheet": 1' in status_events[0]
        assert '"current_sheet": 2' in status_events[1]


# =============================================================================
# Artifact security tests
# =============================================================================


class TestArtifactSecurity:
    """Additional security tests for artifact routes."""

    def test_artifact_symlink_blocked(self, client, temp_state_dir) -> None:
        """Symlink to outside workspace should be blocked."""
        workspace = temp_state_dir / "test-workspace"
        workspace.mkdir()

        # Create a file outside workspace
        outside_file = temp_state_dir / "secret.txt"
        outside_file.write_text("sensitive data")

        # Create symlink inside workspace pointing outside
        symlink = workspace / "link.txt"
        try:
            symlink.symlink_to(outside_file)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        job_state = CheckpointState(
            job_id="test-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            worktree_path=str(workspace),
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/test-123/artifacts/link.txt")

        # Should be blocked (400 or 404)
        assert response.status_code in (400, 404)

    def test_artifact_listing_filters_symlink_entries(self, client, temp_state_dir) -> None:
        """Artifact listing should not offer entries the read route rejects."""
        workspace = temp_state_dir / "test-workspace"
        workspace.mkdir()
        (workspace / "visible.txt").write_text("visible")
        outside_file = temp_state_dir / "secret.txt"
        outside_file.write_text("sensitive data")
        symlink = workspace / "secret-link.txt"
        try:
            symlink.symlink_to(outside_file)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        job_state = CheckpointState(
            job_id="test-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            worktree_path=str(workspace),
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/test-123/artifacts")

        assert response.status_code == 200
        data = response.json()
        names = {item["name"] for item in data["files"]}
        assert "visible.txt" in names
        assert "secret-link.txt" not in names

    def test_artifact_not_a_file(self, client, temp_state_dir) -> None:
        """Requesting a directory as artifact should return 400."""
        workspace = temp_state_dir / "test-workspace"
        workspace.mkdir()
        (workspace / "subdir").mkdir()

        job_state = CheckpointState(
            job_id="test-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            worktree_path=str(workspace),
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/test-123/artifacts/subdir")

        assert response.status_code == 400
        assert "not a file" in response.json()["detail"]

    def test_artifact_hidden_files_excluded(self, client, temp_state_dir) -> None:
        """Hidden files should be excluded from default artifact listing."""
        workspace = temp_state_dir / "test-workspace"
        workspace.mkdir()
        (workspace / "visible.txt").write_text("visible")
        (workspace / ".hidden").write_text("hidden")

        job_state = CheckpointState(
            job_id="test-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            worktree_path=str(workspace),
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/test-123/artifacts")

        assert response.status_code == 200
        data = response.json()
        file_names = [f["name"] for f in data["files"]]
        assert "visible.txt" in file_names
        assert ".hidden" not in file_names

    def test_artifact_hidden_files_included(self, client, temp_state_dir) -> None:
        """Hidden files should be included when include_hidden=true."""
        workspace = temp_state_dir / "test-workspace"
        workspace.mkdir()
        (workspace / "visible.txt").write_text("visible")
        (workspace / ".hidden").write_text("hidden")

        job_state = CheckpointState(
            job_id="test-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            worktree_path=str(workspace),
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/test-123/artifacts?include_hidden=true")

        assert response.status_code == 200
        data = response.json()
        file_names = [f["name"] for f in data["files"]]
        assert "visible.txt" in file_names
        assert ".hidden" in file_names

    def test_artifact_file_pattern_filter(self, client, temp_state_dir) -> None:
        """File pattern filter should only return matching files."""
        workspace = temp_state_dir / "test-workspace"
        workspace.mkdir()
        (workspace / "file1.py").write_text("python")
        (workspace / "file2.py").write_text("python")
        (workspace / "readme.md").write_text("markdown")

        job_state = CheckpointState(
            job_id="test-123",
            job_name="Test Job",
            status=JobStatus.RUNNING,
            total_sheets=3,
            worktree_path=str(workspace),
            created_at=_FIXED_TIME,
            updated_at=_FIXED_TIME,
        )

        with patch("marianne.dashboard.app._state_backend") as mock_backend:
            mock_backend.load = AsyncMock(return_value=job_state)
            response = client.get("/api/jobs/test-123/artifacts?file_pattern=*.py&recursive=false")

        assert response.status_code == 200
        data = response.json()
        file_names = [f["name"] for f in data["files"]]
        assert "file1.py" in file_names
        assert "file2.py" in file_names
        assert "readme.md" not in file_names


# =============================================================================
# _is_safe_path unit tests
# =============================================================================


class TestIsSafePath:
    """Tests for _is_safe_path security helper."""

    def test_safe_path(self, tmp_path: Path) -> None:
        """Normal relative path should be safe."""
        from marianne.dashboard.routes.artifacts import _is_safe_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "file.txt").write_text("ok")

        assert _is_safe_path("file.txt", workspace) is True

    def test_traversal_path(self, tmp_path: Path) -> None:
        """Path with .. should be blocked."""
        from marianne.dashboard.routes.artifacts import _is_safe_path

        workspace = tmp_path / "ws"
        workspace.mkdir()

        assert _is_safe_path("../../etc/passwd", workspace) is False

    def test_absolute_path_outside(self, tmp_path: Path) -> None:
        """Absolute path outside workspace should be blocked."""
        from marianne.dashboard.routes.artifacts import _is_safe_path

        workspace = tmp_path / "ws"
        workspace.mkdir()

        assert _is_safe_path("/etc/passwd", workspace) is False

    def test_symlink_blocked(self, tmp_path: Path) -> None:
        """Symlink should be blocked even if resolved path is inside workspace."""
        from marianne.dashboard.routes.artifacts import _is_safe_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        target = workspace / "real.txt"
        target.write_text("content")
        link = workspace / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Cannot create symlinks")

        assert _is_safe_path("link.txt", workspace) is False
