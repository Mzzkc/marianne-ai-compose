"""Recurring status JSON and Rich rendering contracts."""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.cli.commands.status import _list_jobs, _status_job
from marianne.cli.output import console
from marianne.core.checkpoint import CheckpointState, JobStatus

status_module = importlib.import_module("marianne.cli.commands.status")


def _checkpoint_payload() -> dict[str, Any]:
    return CheckpointState(
        job_id="recurring-job",
        job_name="Recurring Job",
        status=JobStatus.PAUSED,
        total_sheets=1,
        last_completed_sheet=0,
    ).model_dump(mode="json")


def _schedule_payload() -> dict[str, Any]:
    return {
        "enabled": False,
        "next_due_at": 2_000_000_000.0,
        "last_due_at": 1_999_999_700.0,
        "last_run_id": "recurring-job--scheduled--child",
        "last_outcome": "overlap_skipped",
        "consecutive_drops": 2,
    }


@pytest.mark.asyncio
async def test_status_json_has_exact_additive_schedule_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _checkpoint_payload()
    payload["schedule"] = _schedule_payload()
    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        AsyncMock(return_value=(True, payload)),
    )

    with console.capture() as capture:
        await _status_job("recurring-job", True, None)

    rendered = json.loads(capture.get())
    assert rendered["schedule"] == _schedule_payload()
    assert set(rendered["schedule"]) == {
        "enabled",
        "next_due_at",
        "last_due_at",
        "last_run_id",
        "last_outcome",
        "consecutive_drops",
    }


@pytest.mark.asyncio
async def test_unscheduled_status_json_snapshot_has_no_schedule_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        AsyncMock(return_value=(True, _checkpoint_payload())),
    )

    with console.capture() as capture:
        await _status_job("recurring-job", True, None)

    assert "schedule" not in json.loads(capture.get())


@pytest.mark.asyncio
async def test_recurring_rich_status_shows_local_due_pause_and_last_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _checkpoint_payload()
    schedule = _schedule_payload()
    payload["schedule"] = schedule
    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        AsyncMock(return_value=(True, payload)),
    )
    local_due = datetime.fromtimestamp(schedule["next_due_at"]).astimezone()

    with console.capture() as capture:
        await _status_job("recurring-job", False, None)

    rendered = capture.get()
    assert "[recurring]" in rendered
    assert "Recurring schedule: PAUSED" in rendered
    assert local_due.strftime("%Y-%m-%d %H:%M %Z") in rendered
    assert "Last drop: overlap_skipped (2 consecutive)" in rendered


@pytest.mark.asyncio
async def test_default_list_keeps_terminal_job_with_live_recurrence_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = {
        "job_id": "recurring-job",
        "status": "completed",
        "workspace": "/work/recurring-job",
        "submitted_at": 1_999_999_000.0,
        "schedule": _schedule_payload(),
    }
    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        AsyncMock(return_value=(True, [job])),
    )

    with console.capture() as capture:
        await _list_jobs(False, None, 20, None, False)

    rendered = capture.get()
    assert "recurring-job [recurring]" in rendered
    assert "[paused]" in rendered


@pytest.mark.asyncio
async def test_meta_fallback_json_strips_private_schedule_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "job_id": "recurring-job",
        "status": "scheduled",
        "config_path": "/scores/recurring.yaml",
        "submitted_at": 1_999_999_000.0,
        "schedule": {
            **_schedule_payload(),
            "lease_digest": "private",
            "timer_handle": "private",
        },
    }
    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        AsyncMock(return_value=(True, payload)),
    )

    with console.capture() as capture:
        await _status_job("recurring-job", True, None)

    rendered = json.loads(capture.get())
    assert rendered["schedule"] == _schedule_payload()
    assert "lease_digest" not in capture.get()
    assert "timer_handle" not in capture.get()


@pytest.mark.asyncio
async def test_meta_fallback_omits_malformed_schedule_with_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "job_id": "recurring-job",
        "status": "scheduled",
        "config_path": "/scores/recurring.yaml",
        "schedule": {"enabled": True, "lease_digest": "private"},
    }
    warning = MagicMock()
    monkeypatch.setattr(status_module._logger, "warning", warning)
    monkeypatch.setattr(
        "marianne.daemon.detect.try_daemon_route",
        AsyncMock(return_value=(True, payload)),
    )

    with console.capture() as capture:
        await _status_job("recurring-job", False, None)

    rendered = capture.get()
    assert "recurring-job" in rendered
    assert "[recurring]" not in rendered
    assert "lease_digest" not in rendered
    warning.assert_called_once()
