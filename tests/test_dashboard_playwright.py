"""Playwright end-to-end tests for the Marianne dashboard.

Verifies the dashboard UI, API endpoints, and SSE streaming behavior
using a real headless browser.  The dashboard server is started in a
background thread.

These tests MUST NOT run under pytest-xdist (each worker would try to
bind the same port).  Mark them accordingly.
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Dashboard E2E requires Playwright + browser binaries and is run separately
# (`pytest -m playwright -n0`). Skip the whole module when Playwright is not
# installed — e.g. the hermetic CI environment — so collection stays clean.
pytest.importorskip("playwright")

import uvicorn  # noqa: E402
from playwright.sync_api import Browser, Page, sync_playwright  # noqa: E402

from marianne.core.checkpoint import (  # noqa: E402
    CheckpointState,
    JobStatus,
    SheetState,
    SheetStatus,
)
from marianne.dashboard.app import create_app  # noqa: E402
from marianne.dashboard.auth.rate_limit import RateLimitConfig  # noqa: E402
from marianne.state.json_backend import JsonStateBackend


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def temp_state_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture(scope="module")
def backend(temp_state_dir: Path) -> JsonStateBackend:
    return JsonStateBackend(temp_state_dir)


@pytest.fixture(scope="module")
def port() -> int:
    return _find_free_port()


@pytest.fixture(scope="module")
def base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def _server(backend: JsonStateBackend, port: int) -> Generator[None, None, None]:
    app = create_app(
        state_backend=backend,
        cors_origins=["*"],
        rate_limit_config=RateLimitConfig(enabled=False),
    )
    stop_event = threading.Event()

    def _run() -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        stop_event.wait()
        server.should_exit = True
        thread.join(timeout=5)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(1.5)
    yield
    stop_event.set()
    t.join(timeout=5)


@pytest.fixture(scope="module")
def browser(_server: None) -> Generator[Browser, None, None]:
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True)
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser: Browser) -> Generator[Page, None, None]:
    context = browser.new_context()
    p = context.new_page()
    yield p
    context.close()


def _seed_jobs(backend: JsonStateBackend) -> None:
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)

    running = CheckpointState(
        job_id="pw-running-1",
        job_name="Playwright Running Job",
        status=JobStatus.RUNNING,
        total_sheets=4,
        last_completed_sheet=1,
        current_sheet=2,
        worktree_path="/tmp/pw-workspace",
        created_at=now,
        updated_at=now,
    )
    running.sheets[1] = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)

    completed = CheckpointState(
        job_id="pw-completed-1",
        job_name="Playwright Completed Job",
        status=JobStatus.COMPLETED,
        total_sheets=2,
        last_completed_sheet=2,
        current_sheet=2,
        worktree_path="/tmp/pw-workspace-done",
        created_at=now,
        updated_at=now,
    )

    import asyncio

    async def _save() -> None:
        await backend.save(running)
        await backend.save(completed)

    result: list[Exception | None] = [None]

    def _run() -> None:
        try:
            asyncio.run(_save())
        except Exception as exc:
            result[0] = exc

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=5)
    if result[0] is not None:
        raise result[0]


def _save_state(backend: JsonStateBackend, state: CheckpointState) -> None:
    import asyncio

    result: list[Exception | None] = [None]

    async def _save() -> None:
        await backend.save(state)

    def _run() -> None:
        try:
            asyncio.run(_save())
        except Exception as exc:
            result[0] = exc

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=5)
    if result[0] is not None:
        raise result[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.playwright
def test_health_endpoint(page: Page, base_url: str) -> None:
    resp = page.goto(f"{base_url}/health")
    assert resp is not None
    body = resp.json()
    assert body["status"] in ("healthy", "degraded")
    assert body["service"] == "marianne-dashboard"
    assert body["conductor"] in ("up", "down")
    assert (body["status"] == "healthy") == (body["conductor"] == "up")


@pytest.mark.playwright
def test_dashboard_home_loads(page: Page, base_url: str) -> None:
    page.goto(base_url)
    title = page.title()
    assert title != ""


@pytest.mark.playwright
def test_header_shows_isolated_conductor_unavailable(page: Page, base_url: str) -> None:
    page.goto(base_url, wait_until="networkidle")

    page.get_by_text(
        "Conductor disconnected: Conductor unavailable for isolated dashboard"
    ).wait_for(state="visible")


@pytest.mark.playwright
def test_score_editor_dark_mode_themes_editing_surface(page: Page, base_url: str) -> None:
    page.add_init_script("localStorage.setItem('darkMode', 'true')")
    page.goto(f"{base_url}/editor", wait_until="networkidle")
    page.wait_for_selector(".cm-content", timeout=10_000)
    expect_dark_toggle = page.get_by_role("button", name="Switch to light mode")
    assert expect_dark_toggle.count() == 1

    colors = page.eval_on_selector(
        ".cm-content",
        """
        el => {
            const s = getComputedStyle(el);
            return {background: s.backgroundColor, color: s.color};
        }
        """,
    )

    assert colors["background"] != "rgb(255, 255, 255)"
    assert colors["color"] != "rgb(255, 255, 255)"


@pytest.mark.playwright
def test_jobs_list_page_loads(page: Page, base_url: str, backend: JsonStateBackend) -> None:
    _seed_jobs(backend)
    page.goto(f"{base_url}/jobs")
    content = page.content()
    assert len(content) > 100


@pytest.mark.playwright
def test_jobs_list_mid_viewport_keeps_log_and_artifact_actions_visible(
    page: Page, base_url: str, backend: JsonStateBackend
) -> None:
    _seed_jobs(backend)
    page.set_viewport_size({"width": 900, "height": 800})
    page.goto(f"{base_url}/jobs", wait_until="networkidle")
    page.get_by_text("Playwright Running Job").first.wait_for(state="visible")

    assert page.locator("a", has_text="Logs").first.is_visible()
    assert page.locator("a", has_text="Artifacts").first.is_visible()


@pytest.mark.playwright
def test_jobs_api_returns_seeded_jobs(page: Page, base_url: str, backend: JsonStateBackend) -> None:
    _seed_jobs(backend)
    resp = page.goto(f"{base_url}/api/jobs")
    assert resp is not None
    body = resp.json()
    job_ids = [j["job_id"] for j in body["jobs"]]
    assert "pw-running-1" in job_ids
    assert "pw-completed-1" in job_ids


@pytest.mark.playwright
def test_job_detail_api(page: Page, base_url: str, backend: JsonStateBackend) -> None:
    _seed_jobs(backend)
    resp = page.goto(f"{base_url}/api/jobs/pw-running-1")
    assert resp is not None
    body = resp.json()
    assert body["job_id"] == "pw-running-1"
    assert body["status"] == "running"
    assert body["total_sheets"] == 4


@pytest.mark.playwright
def test_job_status_api(page: Page, base_url: str, backend: JsonStateBackend) -> None:
    _seed_jobs(backend)
    resp = page.goto(f"{base_url}/api/jobs/pw-running-1/status")
    assert resp is not None
    body = resp.json()
    assert body["job_id"] == "pw-running-1"
    assert body["progress_percent"] == 25.0


@pytest.mark.playwright
def test_job_detail_action_shows_request_sent_without_optimistic_final_status(
    page: Page, base_url: str, backend: JsonStateBackend
) -> None:
    _seed_jobs(backend)
    page.route(
        "**/api/jobs/pw-running-1/stream",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body="event: heartbeat\ndata: {}\n\n",
        ),
    )
    page.route(
        "**/api/jobs/pw-running-1/pause",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "success": True,
                    "job_id": "pw-running-1",
                    "status": "pause_requested",
                    "message": "Pause request sent to conductor for job pw-running-1",
                    "via_daemon": True,
                }
            ),
        ),
    )

    page.goto(f"{base_url}/jobs/pw-running-1/details", wait_until="domcontentloaded")
    page.get_by_role("button", name="Pause").click()

    page.get_by_text("Pause request sent to conductor").wait_for(state="visible")
    assert page.get_by_role("button", name="Pause").is_visible()
    assert page.get_by_role("button", name="Resume").is_hidden()
    assert page.locator("span", has_text="running").first.is_visible()


@pytest.mark.playwright
def test_paused_at_chain_shows_resume_and_cancel_controls(
    page: Page, base_url: str, backend: JsonStateBackend
) -> None:
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    state = CheckpointState(
        job_id="pw-paused-chain",
        job_name="Playwright Paused Chain",
        status=JobStatus.PAUSED_AT_CHAIN,
        total_sheets=2,
        last_completed_sheet=1,
        current_sheet=2,
        created_at=now,
        updated_at=now,
    )
    _save_state(backend, state)
    page.route(
        "**/api/jobs/pw-paused-chain/stream",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body="event: heartbeat\ndata: {}\n\n",
        ),
    )

    page.goto(f"{base_url}/jobs/pw-paused-chain/details", wait_until="domcontentloaded")

    assert page.get_by_role("button", name="Resume").is_visible()
    assert page.get_by_role("button", name="Cancel").is_visible()


@pytest.mark.playwright
def test_job_detail_current_sheet_updates_from_sse(
    page: Page, base_url: str, backend: JsonStateBackend
) -> None:
    _seed_jobs(backend)
    page.route(
        "**/api/jobs/pw-running-1/stream",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body=(
                "event: job_status\n"
                f"data: {json.dumps({'status': 'running', 'current_sheet': 3})}\n\n"
            ),
        ),
    )

    page.goto(f"{base_url}/jobs/pw-running-1/details", wait_until="domcontentloaded")

    page.get_by_text("Current: Sheet 3").wait_for(state="visible")


@pytest.mark.playwright
def test_job_detail_artifact_panel_shows_available_files_and_freshness(
    page: Page,
    base_url: str,
    backend: JsonStateBackend,
    temp_state_dir: Path,
) -> None:
    workspace = temp_state_dir / "pw-artifacts"
    workspace.mkdir(exist_ok=True)
    (workspace / "artifact.txt").write_text("artifact")
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    state = CheckpointState(
        job_id="pw-artifact-job",
        job_name="Playwright Artifact Job",
        status=JobStatus.COMPLETED,
        total_sheets=1,
        last_completed_sheet=1,
        worktree_path=str(workspace),
        created_at=now,
        updated_at=now,
    )
    _save_state(backend, state)

    page.goto(f"{base_url}/jobs/pw-artifact-job/details", wait_until="networkidle")

    page.get_by_text("available artifacts").wait_for(state="visible")
    assert page.get_by_text("Freshness not verified", exact=True).is_visible()
    assert page.get_by_text("artifact.txt").is_visible()


@pytest.mark.playwright
def test_analytics_endpoint_failure_renders_unavailable(
    page: Page, base_url: str
) -> None:
    page.route(
        "**/api/analytics/**",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"detail": "analytics unavailable"}),
        ),
    )
    page.route(
        "**/api/dashboard/stats",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"detail": "stats unavailable"}),
        ),
    )

    page.goto(f"{base_url}/analytics", wait_until="networkidle")

    page.get_by_text("Analytics unavailable").first.wait_for(state="visible")


# ---------------------------------------------------------------------------
# SSE streaming tests
# ---------------------------------------------------------------------------


@pytest.mark.playwright
def test_sse_stream_404_for_unknown_job(page: Page, base_url: str) -> None:
    resp = page.goto(f"{base_url}/api/jobs/nonexistent/stream")
    assert resp is not None
    assert resp.status in (404, 429)


@pytest.mark.playwright
def test_sse_stream_produces_events(page: Page, base_url: str, backend: JsonStateBackend) -> None:
    _seed_jobs(backend)
    page.goto(f"{base_url}/api/jobs")

    result = page.evaluate("""
        () => {
            return new Promise((resolve) => {
                const events = [];
                const es = new EventSource('/api/jobs/pw-completed-1/stream');
                es.addEventListener('job_status', (e) => {
                    events.push({event: 'job_status', data: e.data});
                    es.close();
                    resolve(events);
                });
                es.addEventListener('job_finished', (e) => {
                    events.push({event: 'job_finished', data: e.data});
                    es.close();
                    resolve(events);
                });
                es.addEventListener('error', () => {
                    es.close();
                    resolve(events);
                });
                setTimeout(() => { es.close(); resolve(events); }, 5000);
            });
        }
    """)

    assert len(result) >= 1, f"Expected at least 1 SSE event, got {result}"
    first = result[0]
    assert first["event"] in ("job_status", "job_finished")
    data = json.loads(first["data"])
    assert data["job_id"] == "pw-completed-1"


@pytest.mark.playwright
def test_log_viewer_shows_error_when_stream_has_no_sources(
    page: Page,
    base_url: str,
    backend: JsonStateBackend,
    temp_state_dir: Path,
) -> None:
    workspace = temp_state_dir / "pw-empty-log-workspace"
    workspace.mkdir(exist_ok=True)
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    _save_state(
        backend,
        CheckpointState(
            job_id="pw-no-logs",
            job_name="Playwright No Logs Job",
            status=JobStatus.COMPLETED,
            total_sheets=1,
            worktree_path=str(workspace),
            created_at=now,
            updated_at=now,
            completed_at=now,
        ),
    )

    page.goto(f"{base_url}/jobs/pw-no-logs/logs", wait_until="domcontentloaded")
    page.wait_for_selector("text=Log stream unavailable for this job.", timeout=10_000)


@pytest.mark.playwright
def test_log_viewer_marks_oversized_download_as_stream_only(
    page: Page, base_url: str, backend: JsonStateBackend
) -> None:
    _seed_jobs(backend)
    page.route(
        "**/api/jobs/pw-completed-1/logs/info",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "job_id": "pw-completed-1",
                    "log_file": "combined log sources",
                    "size_bytes": 53 * 1024 * 1024,
                    "lines": 1000,
                    "last_modified": "2026-04-14T12:00:00Z",
                    "sources": ["observer events: /tmp/observer.jsonl"],
                    "download_available": False,
                    "download_limit_bytes": 50 * 1024 * 1024,
                }
            ),
        ),
    )
    page.route(
        "**/api/jobs/pw-completed-1/logs",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body="event: log_complete\ndata: {}\n\n",
        ),
    )

    page.goto(f"{base_url}/jobs/pw-completed-1/logs", wait_until="domcontentloaded")
    page.get_by_text("Stream only").wait_for(state="visible")
    assert page.get_by_role("link", name="Download").count() == 0


# ---------------------------------------------------------------------------
# Conductor-only behavior
# ---------------------------------------------------------------------------


@pytest.mark.playwright
def test_start_job_503_without_conductor(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/api/jobs")
    result = page.evaluate("""
        async () => {
            const yaml = [
                'name: test',
                'workspace: /tmp',
                'backend:',
                '  type: claude_cli',
                'sheet:',
                '  total_sheets: 1',
                'prompt:',
                '  template: hello',
            ].join('\\n');
            const r = await fetch('/api/jobs', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({config_content: yaml, workspace: '/tmp'}),
            });
            return {status: r.status, body: await r.json()};
        }
    """)
    assert result["status"] in (400, 503)


@pytest.mark.playwright
def test_pause_job_without_conductor(page: Page, base_url: str) -> None:
    """Pause returns 503 (no conductor) or 200/404 (conductor available)."""
    page.goto(f"{base_url}/api/jobs")
    result = page.evaluate("""
        async () => {
            const r = await fetch('/api/jobs/fake-job/pause', {method: 'POST'});
            return {status: r.status};
        }
    """)
    assert result["status"] in (200, 404, 429, 503)


@pytest.mark.playwright
def test_cancel_job_without_conductor(page: Page, base_url: str) -> None:
    """Cancel returns 503 (no conductor) or 200/404 (conductor available)."""
    page.goto(f"{base_url}/api/jobs")
    result = page.evaluate("""
        async () => {
            const r = await fetch('/api/jobs/fake-job/cancel', {method: 'POST'});
            return {status: r.status};
        }
    """)
    assert result["status"] in (200, 404, 429, 503)
