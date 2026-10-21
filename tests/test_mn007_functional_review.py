"""Independent functional checks for issue #308's diagnostic-only changes."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
import structlog
from fastapi import HTTPException
from starlette.requests import Request

from marianne.daemon import process
from marianne.daemon.ipc import client
from marianne.daemon.judgment import JudgmentClient
from marianne.dashboard.routes import pages
from marianne.execution import code_mode


@pytest.mark.parametrize("outcome", ["success", "absent", "failure"])
def test_judgment_diagnostics_preserves_result_and_reports_only_failures(outcome):
    payload = {"job_id": "review-job", "attempts": 3}
    provider = Mock(return_value=payload)
    if outcome == "failure":
        provider.side_effect = RuntimeError("diagnostic database unavailable")
    judge = JudgmentClient(
        live_states={}, resolve_fn=Mock(), backend_factory=Mock(),
        diagnostic_fn=None if outcome == "absent" else provider,
    )
    with structlog.testing.capture_logs() as events:
        result = judge._diagnostics("review-job")
    if outcome == "success":
        assert result is payload
    else:
        assert result is None
    if outcome == "absent":
        provider.assert_not_called()
    else:
        provider.assert_called_once_with("review-job")
    if outcome == "failure":
        assert len(events) == 1
        assert events[0]["event"] == "judgment.diagnostics_unavailable"
        assert events[0]["job_id"] == "review-job"
        assert events[0]["log_level"] == "warning"
        assert events[0]["exc_info"] is True
    else:
        assert events == []


def test_output_decode_failure_keeps_placeholder_and_warning():
    class Undecodable(bytes):
        def decode(self, *args, **kwargs):
            raise RuntimeError("decoder unavailable")

    with structlog.testing.capture_logs() as events:
        assert code_mode._truncate_output(Undecodable(b"result")) == "<binary output>"
    assert len(events) == 1
    assert events[0]["event"] == "code_mode.output_decode_failed"
    assert events[0]["log_level"] == "warning"
    assert events[0]["exc_info"] is True


@pytest.mark.parametrize("data,expected", [
    (b"", ""),
    (b"hello", "hello"),
    (b"\xffhello", "\ufffdhello"),
    (b"discard" + b"x" * code_mode._MAX_OUTPUT_BYTES, "x" * code_mode._MAX_OUTPUT_BYTES),
])
def test_output_decoding_preserves_replacement_and_tail_without_warning(data, expected):
    with structlog.testing.capture_logs() as events:
        assert code_mode._truncate_output(data) == expected
    assert events == []


@pytest.mark.parametrize("shutting_down", [False, True])
def test_successful_readiness_probes_keep_values_and_close_client(
    shutting_down, monkeypatch, tmp_path,
):
    response = {"shutting_down": shutting_down, "running_jobs": 2, "job_ids": ["a", "b"]}
    ipc = Mock(readiness=AsyncMock(return_value=response), close=AsyncMock())
    monkeypatch.setattr(client, "DaemonClient", lambda *args, **kwargs: ipc)
    with structlog.testing.capture_logs() as events:
        assert process._probe_shutting_down(tmp_path / "conductor.sock") is shutting_down
        assert process._check_running_jobs(tmp_path / "conductor.sock") == {
            "running_jobs": 2, "job_ids": ["a", "b"],
        }
    assert ipc.close.await_count == 2
    assert not any(e["event"].endswith("failed") for e in events)


@pytest.mark.parametrize("page", [pages.job_details_page, pages.job_logs_page])
async def test_missing_dashboard_job_preserves_404_without_failure_warning(page):
    backend = Mock(load=AsyncMock(return_value=None))
    templates = Mock()
    request = Request({"type": "http", "method": "GET", "path": "/"})
    with structlog.testing.capture_logs() as events, pytest.raises(HTTPException) as caught:
        await page("missing", request, templates=templates, backend=backend)
    assert caught.value.status_code == 404
    assert caught.value.detail == "Score not found: missing"
    templates.TemplateResponse.assert_not_called()
    assert events == []
