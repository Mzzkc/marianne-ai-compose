"""GH #547: ``mzt wait``, ``mzt next`` and ``mzt verdict``.

No real conductor: every test replaces the single conductor seam
(``conduct._route``) with a scripted fake, and ``wait`` runs on an injected
clock and sleeper, so no test sleeps.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.cli.commands import conduct
from marianne.cli.commands.conduct import (
    ConductorUnreachable,
    JobNotFound,
    compute_next,
    find_verdict,
    wait_for_job,
)

NOW = 1_800_000_000.0


def _iso(offset_seconds: float) -> str:
    return (datetime.fromtimestamp(NOW, UTC) + timedelta(seconds=offset_seconds)).isoformat()


class FakeConductor:
    """Scripted ``_route``: ``status`` entries are dicts, exceptions, or a callable."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        statuses: dict[str, Any] | None = None,
    ) -> None:
        self.rows = rows or []
        self.statuses = statuses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, method: str, params: dict[str, Any]) -> Any:
        self.calls.append((method, params))
        if method == "job.list":
            return self.rows
        entry = self.statuses[params["job_id"]]
        if isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if isinstance(entry, Exception):
            raise entry
        return entry


class Clock:
    """Fake time: ``sleep`` advances the clock and records the delay."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _status(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


# ---------------------------------------------------------------------------
# wait
# ---------------------------------------------------------------------------


async def _wait(statuses: list[Any], max_seconds: float = 100.0) -> tuple[dict[str, Any], Clock]:
    clock = Clock()
    script = list(statuses)

    async def fetch() -> dict[str, Any]:
        entry = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(entry, Exception):
            raise entry
        return entry

    result = await wait_for_job(fetch, "j", max_seconds, clock=clock, sleep=clock.sleep)
    return result, clock


@pytest.mark.asyncio
async def test_wait_done_after_running_polls_with_backoff_and_no_real_sleep():
    result, clock = await _wait([_status("running")] * 3 + [_status("completed")])
    assert result == {
        "state": "done", "job_id": "j", "status": "completed",
        "outcome": "completed", "reason": "job completed", "verdict": None,
    }
    assert clock.sleeps == [2.0, 3.0, 4.5]


@pytest.mark.asyncio
async def test_wait_backoff_is_capped():
    result, clock = await _wait([_status("running")] * 12 + [_status("completed")])
    assert result["state"] == "done"
    assert max(clock.sleeps) == 10.0


@pytest.mark.asyncio
async def test_wait_cancelled_is_done_with_cancelled_outcome():
    result, _ = await _wait([_status("cancelled")])
    assert (result["state"], result["outcome"]) == ("done", "cancelled")


@pytest.mark.asyncio
async def test_wait_failed_needs_you_and_carries_the_error():
    result, _ = await _wait([_status("failed", error_message="boom")])
    assert (result["state"], result["outcome"], result["reason"]) == (
        "needs_you", "failed", "boom",
    )


@pytest.mark.asyncio
async def test_wait_paused_at_fermata_names_sheet_and_reason():
    state = _status("paused", sheets={
        "1": {"status": "completed"},
        "20": {"status": "fermata", "fermata_reason": "read the brief"},
    })
    result, _ = await _wait([state])
    assert result["state"] == "needs_you"
    assert result["outcome"] == "paused"
    assert "sheet 20" in result["reason"] and "read the brief" in result["reason"]


@pytest.mark.asyncio
async def test_wait_running_job_with_fermata_sheet_needs_you():
    state = _status("running", sheets={"3": {"status": "fermata", "fermata_reason": None}})
    result, _ = await _wait([state])
    assert (result["state"], result["outcome"]) == ("needs_you", "paused")


@pytest.mark.asyncio
async def test_wait_paused_at_chain_needs_you_and_says_how_to_continue():
    result, _ = await _wait([_status("paused_at_chain")])
    assert (result["state"], result["outcome"]) == ("needs_you", "paused")
    assert "mzt resume" in result["reason"]


@pytest.mark.asyncio
async def test_wait_user_paused_without_fermata_needs_you():
    result, _ = await _wait([_status("paused")])
    assert result["state"] == "needs_you"
    assert result["reason"] == "job is paused"


@pytest.mark.asyncio
async def test_wait_expires_without_outcome():
    result, clock = await _wait([_status("running")], max_seconds=7.0)
    assert result["state"] == "wait_expired"
    assert result["outcome"] is None and result["status"] == "running"
    assert clock.now == pytest.approx(7.0)  # the last sleep is clipped to the deadline


@pytest.mark.asyncio
async def test_wait_conductor_down_after_three_unreachable_polls():
    result, clock = await _wait([ConductorUnreachable()])
    assert result["state"] == "conductor_down"
    assert result["status"] is None and result["outcome"] is None
    assert len(clock.sleeps) == 2


@pytest.mark.asyncio
async def test_wait_survives_a_transient_blip():
    result, _ = await _wait([
        _status("running"), ConductorUnreachable(), ConductorUnreachable(),
        _status("running"), ConductorUnreachable(), ConductorUnreachable(),
        _status("completed"),
    ])
    assert result["state"] == "done"


@pytest.mark.asyncio
async def test_wait_unknown_job_propagates():
    with pytest.raises(JobNotFound):
        await _wait([JobNotFound("x")])


def _invoke(monkeypatch, fake: FakeConductor, args: list[str]):
    monkeypatch.setattr(conduct, "_route", fake)
    return CliRunner().invoke(app, args)


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (_status("completed"), 0),
        (_status("failed", error_message="x"), 2),
    ],
)
def test_wait_cli_json_and_exit_codes(monkeypatch, state, code):
    result = _invoke(monkeypatch, FakeConductor(statuses={"job-a": state}),
                     ["wait", "job-a", "--json"])
    assert result.exit_code == code
    payload = json.loads(result.output)
    assert set(payload) == {"state", "job_id", "status", "outcome", "reason", "verdict"}
    assert payload["job_id"] == "job-a"
    assert "\x1b" not in result.output


def test_wait_cli_wait_expired_exit_4(monkeypatch):
    result = _invoke(monkeypatch, FakeConductor(statuses={"job-a": _status("running")}),
                     ["wait", "job-a", "--max", "0", "--json"])
    assert result.exit_code == 4
    assert json.loads(result.output)["state"] == "wait_expired"


def test_wait_cli_conductor_down_exit_3(monkeypatch):
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(conduct.asyncio, "sleep", instant)
    result = _invoke(monkeypatch, FakeConductor(statuses={"job-a": ConductorUnreachable()}),
                     ["wait", "job-a", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.output)["state"] == "conductor_down"


def test_wait_cli_unknown_job_uses_error_envelope(monkeypatch):
    result = _invoke(monkeypatch, FakeConductor(statuses={"job-a": JobNotFound("no")}),
                     ["wait", "job-a", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["success"] is False and payload["hints"]


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------


def _write(path: Path, text: str, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("PASS", "PASS"), ("approve", "PASS"), ("Accept", "PASS"),
        ("REWORK", "REWORK"), ("REJECT", "REWORK"),
        ("HOLD", "BLOCKED"), ("BLOCK", "BLOCKED"),
    ],
)
def test_find_verdict_maps_words(tmp_path, word, expected):
    _write(tmp_path / "cycle-state" / "x-inspection.md", f"intro\nVERDICT: {word}\nEVIDENCE: y\n")
    result = find_verdict(tmp_path)
    assert result["verdict"] == expected
    assert result["raw"] == f"VERDICT: {word}"
    assert result["source_path"].endswith("x-inspection.md")
    assert result["round"] is None


def test_find_verdict_unknown_when_absent(tmp_path):
    _write(tmp_path / "a.md", "no verdict here, PASS maybe\n")
    assert find_verdict(tmp_path) == {
        "verdict": "unknown", "raw": None, "source_path": None, "round": None,
    }


def test_find_verdict_unknown_for_missing_workspace(tmp_path):
    assert find_verdict(tmp_path / "nope")["verdict"] == "unknown"


def test_find_verdict_takes_newest_file_and_last_line(tmp_path):
    _write(tmp_path / "old.md", "VERDICT: PASS\n", mtime=1000)
    _write(tmp_path / "new.md", "VERDICT: PASS\nlater\nVERDICT: REWORK\n", mtime=2000)
    result = find_verdict(tmp_path)
    assert result["verdict"] == "REWORK" and result["source_path"].endswith("new.md")


def test_find_verdict_skips_template_lines_and_unmapped_words(tmp_path):
    _write(tmp_path / "prompt.md", "VERDICT: PASS | REWORK | BLOCKED\n", mtime=3000)
    _write(tmp_path / "bad.md", "VERDICT: MAYBE\n", mtime=2500)
    _write(tmp_path / "real.md", "VERDICT: HOLD\n", mtime=1000)
    result = find_verdict(tmp_path)
    assert (result["verdict"], result["source_path"].endswith("real.md")) == ("BLOCKED", True)


def test_find_verdict_tolerates_markdown_decoration(tmp_path):
    _write(tmp_path / "r.md", "## **VERDICT:** `PASS`\n")
    assert find_verdict(tmp_path)["verdict"] == "PASS"


def test_find_verdict_custom_pattern(tmp_path):
    _write(tmp_path / "r.txt", "Outcome - rework needed\n")
    result = find_verdict(tmp_path, r"Outcome\s*-\s*(?P<word>\w+)")
    assert result["verdict"] == "REWORK"


def test_find_verdict_ignores_dependency_dirs(tmp_path):
    _write(tmp_path / "node_modules" / "p" / "README.md", "VERDICT: PASS\n")
    assert find_verdict(tmp_path)["verdict"] == "unknown"


def test_verdict_cli_via_conductor_workspace_and_accept_marker(monkeypatch, tmp_path):
    _write(tmp_path / "review.md", "VERDICT: APPROVE\n")
    fake = FakeConductor(rows=[{"job_id": "j1", "workspace": str(tmp_path),
                                "status": "completed", "submitted_at": 11.5}])
    result = _invoke(monkeypatch, fake, ["verdict", "j1", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["verdict"] == "PASS"
    assert not (tmp_path / conduct.JUDGED_MARKER).exists()

    result = _invoke(monkeypatch, fake, ["verdict", "j1", "--accept", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["judged"] is True
    assert conduct.is_judged(str(tmp_path), "j1", 11.5)
    assert not conduct.is_judged(str(tmp_path), "j1", 99.0)  # a different run of the same id
    assert not conduct.is_judged(str(tmp_path), "other", 11.5)


def test_verdict_cli_unknown_job(monkeypatch):
    result = _invoke(monkeypatch, FakeConductor(rows=[]), ["verdict", "ghost", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["success"] is False


def test_verdict_cli_explicit_workspace_needs_no_conductor(monkeypatch, tmp_path):
    _write(tmp_path / "r.md", "VERDICT: REJECT\n")
    fake = FakeConductor()
    result = _invoke(monkeypatch, fake, ["verdict", "j1", "-w", str(tmp_path), "--json"])
    assert json.loads(result.output)["verdict"] == "REWORK"
    assert fake.calls == []


def test_verdict_cli_conductor_absent_is_reported(monkeypatch):
    async def down(method, params):
        raise ConductorUnreachable()

    monkeypatch.setattr(conduct, "_route", down)
    result = CliRunner().invoke(app, ["verdict", "j1", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["success"] is False


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def _row(job_id: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"job_id": job_id, "status": status, "submitted_at": NOW - 100,
            "running_seconds": 50.0, "workspace": extra.pop("workspace", None), **extra}


async def _next(fake: FakeConductor, **kw: Any) -> list[dict[str, Any]]:
    kw.setdefault("mine", None)
    kw.setdefault("since_hours", 24.0)
    kw.setdefault("idle_seconds", None)
    return await compute_next(route=fake, now=NOW, **kw)


def _actions(queue: list[dict[str, Any]]) -> dict[str, str]:
    return {item["job_id"]: item["action"] for item in queue}


@pytest.mark.asyncio
async def test_next_decision_table():
    fake = FakeConductor(
        rows=[
            _row("run-ok", "running"),
            _row("done-unjudged", "completed"),
            _row("failed-1", "failed", error_message="exploded"),
            _row("paused-fermata", "paused"),
            _row("cancelled-1", "cancelled"),
            _row("quiet", "running"),
        ],
        statuses={
            "run-ok": {"status": "running", "updated_at": _iso(-5), "sheets": {
                "1": {"status": "completed"}, "2": {"status": "in_progress"}}},
            "paused-fermata": {"status": "paused", "sheets": {
                "4": {"status": "fermata", "fermata_reason": "approve?"}}},
            "quiet": {"status": "running", "updated_at": _iso(-4000), "sheets": {
                "1": {"status": "in_progress", "sheet_timeout_seconds": 1800.0}}},
        },
    )
    queue = await _next(fake)
    assert _actions(queue) == {
        "run-ok": "WAIT", "done-unjudged": "JUDGE", "failed-1": "ESCALATE",
        "paused-fermata": "ESCALATE", "cancelled-1": "ESCALATE", "quiet": "UNSTICK",
    }
    why = {item["job_id"]: item["why"] for item in queue}
    assert why["failed-1"] == "exploded"
    assert "sheet 4" in why["paused-fermata"] and "approve?" in why["paused-fermata"]
    assert "4000s" in why["quiet"] or "no activity" in why["quiet"]
    assert why["run-ok"] == "running (1/2 sheets done)"


@pytest.mark.asyncio
async def test_next_orders_escalate_unstick_judge_wait():
    fake = FakeConductor(
        rows=[
            _row("w", "running"), _row("j", "completed"),
            _row("u", "running"), _row("e", "failed"),
        ],
        statuses={
            "w": {"status": "running", "updated_at": _iso(0), "sheets": {}},
            "u": {"status": "running", "updated_at": _iso(-9999),
                  "sheets": {"1": {"status": "in_progress"}}},
        },
    )
    queue = await _next(fake)
    assert [i["action"] for i in queue] == ["ESCALATE", "UNSTICK", "JUDGE", "WAIT"]


@pytest.mark.asyncio
async def test_next_stale_detection_threshold_from_score_config():
    status = {"status": "running", "updated_at": _iso(-200),
              "config_snapshot": {"stale_detection": {"enabled": True,
                                                       "idle_timeout_seconds": 120.0}},
              "sheets": {"1": {"status": "in_progress"}}}
    queue = await _next(FakeConductor(rows=[_row("s", "running")], statuses={"s": status}))
    assert queue[0]["action"] == "UNSTICK"
    queue = await _next(FakeConductor(rows=[_row("s", "running")], statuses={"s": status}),
                        idle_seconds=500.0)
    assert queue[0]["action"] == "WAIT"


@pytest.mark.asyncio
async def test_next_job_with_no_playing_sheet_is_never_unstick():
    status = {"status": "running", "updated_at": _iso(-99999),
              "sheets": {"1": {"status": "completed"}, "2": {"status": "pending"}}}
    queue = await _next(FakeConductor(rows=[_row("s", "running")], statuses={"s": status}))
    assert queue[0]["action"] == "WAIT"


@pytest.mark.asyncio
async def test_next_paused_at_chain_escalates_with_resume_hint():
    queue = await _next(FakeConductor(rows=[_row("c", "paused_at_chain")]))
    assert queue[0]["action"] == "ESCALATE" and "mzt resume" in queue[0]["why"]


@pytest.mark.asyncio
async def test_next_running_fermata_sheet_escalates():
    status = {"status": "running", "sheets": {"2": {"status": "fermata", "fermata_reason": "r"}}}
    queue = await _next(FakeConductor(rows=[_row("s", "running")], statuses={"s": status}))
    assert queue[0]["action"] == "ESCALATE"


@pytest.mark.asyncio
async def test_next_mine_prefix_filters():
    fake = FakeConductor(rows=[_row("legion-a", "completed"), _row("other-b", "completed")])
    queue = await _next(fake, mine="legion-")
    assert _actions(queue) == {"legion-a": "JUDGE"}


@pytest.mark.asyncio
async def test_next_judged_marker_turns_judge_into_done(tmp_path, monkeypatch):
    monkeypatch.setattr(conduct, "_is_test_artifact", lambda row: False)
    conduct._write_marker(tmp_path, "j", NOW - 100, "PASS")
    fake = FakeConductor(rows=[_row("j", "completed", workspace=str(tmp_path))])
    assert _actions(await _next(fake)) == {"j": "DONE"}


@pytest.mark.asyncio
async def test_next_marker_from_an_earlier_run_does_not_count(tmp_path, monkeypatch):
    monkeypatch.setattr(conduct, "_is_test_artifact", lambda row: False)
    conduct._write_marker(tmp_path, "j", NOW - 99999, "PASS")
    fake = FakeConductor(rows=[_row("j", "completed", workspace=str(tmp_path))])
    assert _actions(await _next(fake)) == {"j": "JUDGE"}


@pytest.mark.asyncio
async def test_next_hides_old_terminal_jobs_scheduled_anchors_and_test_artifacts():
    old = _row("old", "failed")
    old["submitted_at"] = NOW - 10 * 86400
    fake = FakeConductor(rows=[
        old,
        {"job_id": "anchor", "status": "scheduled", "submitted_at": NOW},
        _row("pytest-run", "completed", workspace="/tmp/pytest-of-x/y"),
    ])
    queue = await _next(fake)
    assert queue == [{"job_id": None, "action": "DONE", "why": "no jobs need attention"}]
    queue = await _next(fake, since_hours=0)
    assert _actions(queue) == {"old": "ESCALATE"}


@pytest.mark.asyncio
async def test_next_status_failure_for_one_job_falls_back_to_wait():
    fake = FakeConductor(rows=[_row("x", "running")], statuses={"x": JobNotFound("gone")})
    assert _actions(await _next(fake)) == {"x": "WAIT"}


def test_next_cli_json_array(monkeypatch):
    fake = FakeConductor(rows=[_row("a", "completed")])
    result = _invoke(monkeypatch, fake, ["next", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert set(payload[0]) == {"job_id", "action", "why"}
    assert payload[0]["action"] == "JUDGE"


def test_next_cli_conductor_absent(monkeypatch):
    async def down(method, params):
        raise ConductorUnreachable()

    monkeypatch.setattr(conduct, "_route", down)
    result = CliRunner().invoke(app, ["next", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["success"] is False
