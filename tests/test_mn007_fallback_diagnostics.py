"""Fallbacks preserve their result while retaining the dependency failure."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import structlog

from marianne.cli import helpers
from marianne.core import tokens
from marianne.daemon import detect, process
from marianne.daemon.baton import musician
from marianne.daemon.ipc import client
from marianne.daemon.profiler.storage import MonitorStorage
from marianne.dashboard.app import _create_daemon_client
from marianne.execution.instruments.openai_compat_backend import OpenAICompatibleBackend
from marianne.instruments import loader
from marianne.schema.registry import get_field_specs


@pytest.fixture(autouse=True)
def capture_diagnostics(caplog: pytest.LogCaptureFixture) -> None:
    structlog.configure(
        processors=[structlog.processors.format_exc_info, structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    caplog.set_level(logging.DEBUG)


def fail(*args: object, **kwargs: object) -> None:
    raise RuntimeError("dependency unavailable")


def assert_diagnostic(caplog: pytest.LogCaptureFixture, event: str, level: int) -> None:
    records = [r for r in caplog.records if event in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == level
    payload = json.loads(records[0].getMessage())
    assert payload["event"] == event
    assert "RuntimeError: dependency unavailable" in payload["exception"]


@pytest.mark.parametrize("probe,event", [
    (process._probe_shutting_down, "conductor.shutdown_probe_failed"),
    (process._check_running_jobs, "conductor.running_jobs_probe_failed"),
])
def test_ipc_probe_failure_retains_unknown_state_and_detail(
    probe, event, monkeypatch, caplog, tmp_path,
):
    monkeypatch.setattr(client, "DaemonClient", fail)
    assert probe(tmp_path / "missing.sock") is None
    assert_diagnostic(caplog, event, logging.DEBUG)


@pytest.mark.parametrize("probe,event", [
    (process._probe_shutting_down, "conductor.shutdown_probe_cleanup_failed"),
    (process._check_running_jobs, "conductor.running_jobs_probe_cleanup_failed"),
])
def test_probe_cleanup_failure_retains_unknown_state_and_detail(
    probe, event, monkeypatch, caplog, tmp_path,
):
    class Client:
        async def readiness(self):
            return {"shutting_down": False, "running_jobs": 0, "job_ids": []}

        async def close(self):
            fail()

    monkeypatch.setattr(client, "DaemonClient", lambda *a, **kw: Client())
    assert probe(tmp_path / "conductor.sock") is None
    assert_diagnostic(caplog, event, logging.DEBUG)


def test_unknown_instrument_window_keeps_failure_detail(monkeypatch, caplog):
    monkeypatch.setattr(loader, "load_all_profiles", fail)
    assert tokens._resolve_instrument_window_from_registry("custom") is None
    assert_diagnostic(caplog, "tokens.registry_window_unavailable", logging.DEBUG)


def test_advisory_preflight_keeps_dispatch_possible(monkeypatch, caplog):
    monkeypatch.setattr(musician, "estimate_tokens", fail)
    assert musician._preflight_token_warnings("prompt", "custom", None) == []
    assert_diagnostic(caplog, "musician.token_preflight_failed", logging.DEBUG)


def test_dashboard_client_construction_failure_is_visible(monkeypatch, caplog):
    monkeypatch.setattr(detect, "_resolve_socket_path", fail)
    assert _create_daemon_client() is None
    assert_diagnostic(caplog, "dashboard.daemon_client_unavailable", logging.WARNING)


async def test_rate_limit_query_retains_unavailable_result(monkeypatch, caplog):
    monkeypatch.setattr(
        detect, "try_daemon_route",
        AsyncMock(side_effect=RuntimeError("dependency unavailable")),
    )
    assert await helpers.query_rate_limits() is None
    assert_diagnostic(caplog, "cli.rate_limits_unavailable", logging.DEBUG)


async def test_early_failure_probe_keeps_failure_detail(monkeypatch, caplog):
    monkeypatch.setattr(detect, "_resolve_socket_path", fail)
    assert await helpers.await_early_failure("job-1") is None
    assert_diagnostic(caplog, "cli.early_failure_probe_failed", logging.DEBUG)


async def test_backend_initialization_failure_reports_unavailable(monkeypatch, caplog):
    backend = OpenAICompatibleBackend("test-model", "http://localhost")
    monkeypatch.setattr(
        backend, "_get_client",
        AsyncMock(side_effect=RuntimeError("dependency unavailable")),
    )
    assert await backend.availability_check() is False
    assert_diagnostic(caplog, "openai_compatible_initialization_failed", logging.WARNING)


async def test_dead_pool_is_replaced_and_diagnosed(tmp_path: Path, caplog):
    storage = MonitorStorage(tmp_path / "profiler.db")
    try:
        old = await storage._get_connection()
        await old.close()
        new = await storage._get_connection()
        assert new is not old
        async with new.execute("SELECT 42") as cursor:
            row = await cursor.fetchone()
            assert tuple(row) == (42,)
        records = [
            r for r in caplog.records if "profiler.storage_pool_unavailable" in r.getMessage()
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "no active connection" in records[0].getMessage()
    finally:
        await storage.close()


def test_unresolved_annotation_preserves_raw_fields_and_warns(caplog):
    @dataclass
    class Row:
        value: MissingType  # noqa: F821 — unresolved annotation exercises fallback

    fields = get_field_specs(Row)
    assert [(f.name, f.annotation) for f in fields] == [("value", "MissingType")]
    records = [r for r in caplog.records if "schema.type_hints_unresolved" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "MissingType" in records[0].getMessage()
    assert json.loads(records[0].getMessage())["model"] == Row.__qualname__


@pytest.mark.parametrize("method,event", [
    ("_last_conductor_reload", "doctor.reload_status_unavailable"),
    ("_active_rate_limited_instruments", "doctor.rate_limits_unavailable"),
])
def test_doctor_optional_status_retains_failure_detail(
    method, event, monkeypatch, caplog, tmp_path,
):
    import importlib

    doctor = importlib.import_module("marianne.cli.commands.doctor")
    if method == "_last_conductor_reload":
        socket = tmp_path / "conductor.sock"
        socket.touch()
        monkeypatch.setattr(detect, "_resolve_socket_path", lambda _: socket)
        monkeypatch.setattr(client, "DaemonClient", fail)
        expected = None
    else:
        monkeypatch.setattr(
            doctor, "_fetch_active_rate_limited_instruments",
            AsyncMock(side_effect=RuntimeError("dependency unavailable")),
        )
        expected = {}
    assert getattr(doctor, method)() == expected
    assert_diagnostic(caplog, event, logging.DEBUG)


@pytest.mark.parametrize("page,event,title", [
    ("jobs_list_partial", "dashboard.jobs_load_failed", "Failed to Load Jobs"),
    ("job_details_page", "dashboard.job_details_load_failed", "Failed to Load Job Details"),
    ("job_logs_page", "dashboard.job_logs_load_failed", "Failed to Load Job Logs"),
    ("templates_list_partial", "dashboard.templates_load_failed", "Failed to Load Templates"),
])
async def test_dashboard_fallback_renders_error_and_logs_traceback(
    page, event, title, monkeypatch, caplog,
):
    from fastapi.templating import Jinja2Templates
    from starlette.requests import Request

    from marianne.dashboard.routes import pages
    from marianne.scores import templates as score_templates

    class BrokenBackend:
        async def list_jobs(self):
            fail()

        async def load(self, job_id):
            fail()

    templates = Jinja2Templates(directory=str(
        Path(pages.__file__).parents[1] / "templates"
    ))
    kwargs = {
        "request": Request({"type": "http", "method": "GET", "path": "/"}),
        "templates": templates,
    }
    if page == "templates_list_partial":
        monkeypatch.setattr(score_templates, "list_templates", fail)
    else:
        kwargs["backend"] = BrokenBackend()
        if page != "jobs_list_partial":
            kwargs["job_id"] = "job-1"
    response = await getattr(pages, page)(**kwargs)
    assert title in response.body.decode()
    assert "dependency unavailable" in response.body.decode()
    assert_diagnostic(caplog, event, logging.WARNING)
