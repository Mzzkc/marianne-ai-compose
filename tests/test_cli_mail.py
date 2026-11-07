"""The shell-only musician channel, without any native harness adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.mailbox import read_messages

runner = CliRunner()


@pytest.fixture
def job_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mailbox"
    for sheet in (1, 2, 3):
        (root / str(sheet)).mkdir(parents=True)
    monkeypatch.setenv("MARIANNE_MAILBOX", str(root))
    monkeypatch.setenv("MARIANNE_SHEET", "1")
    return root


def test_send_read_and_since(job_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = job_env.parent / "mail-send.jsonl"
    result = runner.invoke(app, ["--log-file", str(log_path), "--log-format", "json",
                                 "mail", "send", "2", "private payload λ"])
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert result.exit_code == 0, result.output
    assert "private payload" not in result.output
    sends = [event for event in events if event["event"] == "mailbox.send"]
    assert len(sends) == 1
    assert (sends[0]["sender"], sends[0]["recipient"], sends[0]["bytes"]) == (1, 2, 18)
    assert "body" not in json.dumps(events)
    assert "private payload" not in json.dumps(events)
    monkeypatch.setenv("MARIANNE_SHEET", "2")
    result = runner.invoke(app, ["mail", "read"])
    assert result.exit_code == 0, result.output
    messages = [json.loads(line) for line in result.output.splitlines()]
    assert messages[0]["body"] == "private payload λ"
    cursor = str(messages[0]["id"])
    assert runner.invoke(app, ["mail", "read", "--since", cursor]).output == ""
    assert runner.invoke(app, ["mail", "send", "1", "reply"]).exit_code == 0
    assert read_messages(job_env, 1)[0].body == "reply"


def test_broadcast_excludes_self(job_env: Path) -> None:
    assert runner.invoke(app, ["mail", "send", "all", "notice"]).exit_code == 0
    assert read_messages(job_env, 1) == []
    assert [m.body for m in read_messages(job_env, 2)] == ["notice"]
    assert [m.body for m in read_messages(job_env, 3)] == ["notice"]


@pytest.mark.parametrize("args", [["read"], ["send", "2", "hello"]])
def test_no_job_context_is_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch, args: list[str],
) -> None:
    for key in ("MARIANNE_MAILBOX", "MARIANNE_SCRATCH", "MARIANNE_SHEET"):
        monkeypatch.delenv(key, raising=False)
    result = runner.invoke(app, ["mail", *args])
    assert result.exit_code != 0
    assert "job context" in result.output


@pytest.mark.parametrize("recipient", ["../outside", "0", "-1", "bad"])
def test_invalid_recipient_does_not_write(job_env: Path, recipient: str) -> None:
    result = runner.invoke(app, ["mail", "send", "--", recipient, "hello"])
    assert result.exit_code != 0
    assert read_messages(job_env, 2) == []


def test_scratch_fallback(job_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARIANNE_MAILBOX")
    monkeypatch.setenv("MARIANNE_SCRATCH", str(job_env.parent))
    assert runner.invoke(app, ["mail", "send", "2", "fallback"]).exit_code == 0
    assert read_messages(job_env, 2)[0].body == "fallback"


def test_help_describes_channel() -> None:
    result = runner.invoke(app, ["mail", "--help"])
    assert result.exit_code == 0
    assert "send" in result.output and "read" in result.output


def test_read_reports_corrupt_inbox_without_disclosing_contents(job_env: Path) -> None:
    (job_env / "1" / "inbox.jsonl").write_text("private body, not JSON\n")
    result = runner.invoke(app, ["mail", "read"])
    assert result.exit_code != 0
    assert "line 1" in result.output
    assert "private body" not in result.output
