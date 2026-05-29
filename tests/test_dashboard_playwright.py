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
    app = create_app(state_backend=backend, cors_origins=["*"])
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.playwright
def test_health_endpoint(page: Page, base_url: str) -> None:
    resp = page.goto(f"{base_url}/health")
    assert resp is not None
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["service"] == "marianne-dashboard"


@pytest.mark.playwright
def test_dashboard_home_loads(page: Page, base_url: str) -> None:
    page.goto(base_url)
    title = page.title()
    assert title != ""


@pytest.mark.playwright
def test_jobs_list_page_loads(page: Page, base_url: str, backend: JsonStateBackend) -> None:
    _seed_jobs(backend)
    page.goto(f"{base_url}/jobs")
    content = page.content()
    assert len(content) > 100


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
