"""Independent functional acceptance and lifecycle regressions for GH #467."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import structlog.testing
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands.diagnose import _build_diagnostic_report
from marianne.core.checkpoint import CheckpointState
from marianne.core.config.workspace import mailbox_root
from marianne.core.mailbox import merge_traffic, read_messages, send_message, traffic_summary
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta


@pytest.mark.parametrize("body", ["", "line one\nline two", "日本語 🦊"])
def test_shell_polling_preserves_payload_and_cursor(tmp_path, monkeypatch, body):
    """An adapter-less musician gets exact payloads and can poll incrementally."""
    root = mailbox_root(tmp_path)
    monkeypatch.setenv("MARIANNE_MAILBOX", str(root))
    monkeypatch.setenv("MARIANNE_SHEET", "1")
    runner = CliRunner()
    sent = runner.invoke(app, ["mail", "send", "2", body])
    assert sent.exit_code == 0, sent.output
    monkeypatch.setenv("MARIANNE_SHEET", "2")
    received = runner.invoke(app, ["mail", "read"])
    assert received.exit_code == 0, received.output
    message = json.loads(received.stdout)
    assert (message["sender"], message["recipient"], message["body"]) == (1, 2, body)
    poll = runner.invoke(app, ["mail", "read", "--since", str(message["id"])])
    assert poll.exit_code == 0 and poll.stdout == ""
    assert traffic_summary(root)["sheets"]["2"]["bytes"] == len(body.encode("utf-8"))


def test_resume_reusing_same_recipient_cursor_keeps_both_sends(tmp_path):
    """Removing scratch resets byte cursors without erasing earlier traffic."""
    root = mailbox_root(tmp_path)
    first = send_message(root, 1, 2, "before resume")
    retained = traffic_summary(root)
    shutil.rmtree(root)
    second = send_message(root, 1, 2, "after resume")
    assert first.id == second.id
    combined = merge_traffic(retained, traffic_summary(root), retained)
    assert len(combined["sends"]) == 2
    assert combined["sheets"]["2"]["messages"] == 2
    assert combined["sheets"]["2"]["bytes"] == len("before resumeafter resume")
    assert "before resume" not in json.dumps(combined)
    assert "after resume" not in json.dumps(combined)


@pytest.mark.parametrize("outcome", ["completed", "failed_return", "error", "timeout", "cancel"])
async def test_terminal_outcomes_record_conductor_sends(tmp_path: Path, outcome: str):
    """Each delivered send must reach conductor logs on every terminal path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    score = tmp_path / "score.yaml"
    score.write_text(
        f"name: mail-review\nworkspace: {workspace}\nsheet:\n  total_items: 1\n"
        "prompt:\n  template: test\n"
    )
    config = DaemonConfig(state_db_path=tmp_path / "registry.db")
    manager = JobManager(config)
    if outcome == "timeout":
        # Match the repository's timeout-test technique without a minute-long test.
        manager._config = config.model_copy(update={"job_timeout_seconds": 0.1})
    job_id = "mail-review"
    await manager._registry.open()
    try:
        await manager._registry.register_job(job_id, score, workspace)
        manager._job_meta[job_id] = JobMeta(
            job_id=job_id, config_path=score, workspace=workspace,
        )
        state = CheckpointState(job_id=job_id, job_name="Mail review", total_sheets=2)
        manager._live_states[job_id] = state
        body = "PRIVATE-LIFECYCLE-PAYLOAD"

        async def musician():
            send_message(mailbox_root(workspace), 1, 2, body)
            if outcome == "error":
                raise OSError("instrument execution failed")
            if outcome == "timeout":
                await asyncio.Event().wait()
            if outcome == "cancel":
                raise asyncio.CancelledError("operator cancelled")
            return (DaemonJobStatus.FAILED if outcome == "failed_return"
                    else DaemonJobStatus.COMPLETED)

        with structlog.testing.capture_logs() as logs:
            if outcome == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await manager._run_managed_task(job_id, musician())
            else:
                await manager._run_managed_task(job_id, musician())
        expected_status = {
            "completed": DaemonJobStatus.COMPLETED,
            "failed_return": DaemonJobStatus.FAILED,
            "error": DaemonJobStatus.FAILED,
            "timeout": DaemonJobStatus.FAILED,
            "cancel": DaemonJobStatus.CANCELLED,
        }[outcome]
        assert manager._job_meta[job_id].status == expected_status
        sends = [entry for entry in logs if entry["event"] == "mailbox.send"]
        assert len(sends) == 1, f"Delivered send omitted for terminal outcome {outcome}"
        assert (sends[0]["sender"], sends[0]["recipient"], sends[0]["bytes"]) == (
            1, 2, len(body.encode()),
        )
        assert body not in json.dumps(logs, default=str)
        saved = json.loads(await manager._registry.load_checkpoint(job_id))
        assert len(saved["mailbox_traffic"]["sends"]) == 1
        assert body not in json.dumps(saved["mailbox_traffic"])
    finally:
        await manager._registry.close()


def test_corrupt_live_mailbox_preserves_retained_diagnosis(tmp_path):
    """A damaged live inbox must not hide previously checkpointed traffic."""
    root = mailbox_root(tmp_path)
    send_message(root, 1, 2, "retained private body")
    job = CheckpointState(job_id="mail-review", job_name="Mail review", total_sheets=2)
    job.mailbox_traffic = traffic_summary(root)
    with (root / "2" / "inbox.jsonl").open("ab") as stream:
        stream.write(b"PRIVATE CORRUPT RECORD\n")
    report = _build_diagnostic_report(job, workspace=tmp_path)
    assert len(report["mailbox_traffic"]["sends"]) == 1
    assert "line 2" in report["mailbox_error"]
    assert "private body" not in json.dumps(report)
    assert "PRIVATE CORRUPT" not in json.dumps(report)
    with pytest.raises(ValueError, match="line 2"):
        read_messages(root, 2)
