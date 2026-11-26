"""SC1c: ``mzt agents list`` and ``mzt agents run`` (interim conducting surface).

No real conductor and no real agents: a fake agents dir and inventory live in
``tmp_path``, the conductor seam (``conduct._route``) and the submit
(``agents_cmd._submit_score``) are replaced. The JSON shapes are checked against
the scenario fixtures the SC1 shim served to the qualified conductor card.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("marianne_compiler")

from marianne.cli import app  # noqa: E402
from marianne.cli.commands import agents_cmd, conduct  # noqa: E402
from marianne.cli.commands.agents_cmd import SubmitOutcome, parse_task  # noqa: E402

runner = CliRunner()

LIST_KEYS = {"name", "role", "owns", "free", "open_debt", "last_outcome"}  # the shim's s1.yaml
RUN_OK_KEYS = {"ok", "job_id", "workspace", "result_path"}
RUN_FAIL_KEYS = {"ok", "code", "hint"}
CODES = {
    "TASK_FIELD_MISSING",
    "AGENT_BUSY",
    "AGENT_UNKNOWN",
    "DEBT_REFUSED",
    "SUBMIT_REFUSED",
    "CONDUCTOR_DOWN",
    "COMPILER_MISSING",
}

TASK = (
    "GOAL: Add input validation to the signup form.\n"
    "FILES: src/signup/\n"
    "KIND: internal\n"
    "ACCEPT: pytest tests/signup/\n"
    "DO_NOT_TOUCH: none stated\n"
    "REPORT: REPORT.md\n"
)


def _score(shape: str, agent: str) -> dict[str, Any]:
    return {
        "name": f"{shape}-{agent}",
        "workspace": "/REQUIRES-LIVE-BINDING",
        "instrument": "stale",
        "instrument_fallbacks": [],
        # a real installed score carries total_items; without it JobConfig
        # rejects the score and an e2e test measures D5, not the submit
        "sheet": {"total_items": 12, "per_sheet_instruments": {}, "per_sheet_fallbacks": {}},
        "prompt": {
            "variables": {
                "marianne_agent": {
                    "schema_version": 1,
                    "agent_id": agent,
                    "score_shape": shape,
                    "phase_requirements": {
                        "work": {"required_capabilities": ["file_editing"], "load_bearing": True}
                    },
                    "routing_receipts": {},
                }
            }
        },
    }


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    agents = tmp_path / "agents"
    people = {
        "ines": ("implementer", "forms and validation", "implementation", ["src/signup/"]),
        "quill": ("implementer", "backend billing", "implementation", ["src/billing/"]),
        "marek": ("documenter", "docs", "experience", ["docs/"]),
    }
    for name, (role, focus, group, owns) in people.items():
        home = agents / name
        (home / "scores").mkdir(parents=True)
        (home / "profile.yaml").write_text(
            yaml.safe_dump(
                {"name": name, "role": role, "focus": focus, "group": group, "owns": owns}
            )
        )
        (home / "identity.md").write_text(f"# {name}\n")
        (home / "scores" / "targeted-work.yaml").write_text(
            yaml.safe_dump(_score("targeted-work", name), sort_keys=False)
        )
    (agents / "noscore").mkdir()
    (agents / "noscore" / "profile.yaml").write_text("name: noscore\nrole: x\n")
    stamp = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "name": "verified",
                        "provider": "anthropic",
                        "model": "claude-opus-5-5",
                        "available": True,
                        "invocation_contract_verified": True,
                        "entitlement_verified": True,
                        "verified_at": stamp,
                        "capabilities": ["file_editing"],
                        "context_tokens": 200000,
                        "reliability_class": "high",
                        "latency_class": "normal",
                        "metered_cost": "subscription",
                        "priority": 1,
                        "evidence": [f"{stamp} checked"],
                    }
                ],
            }
        )
    )
    task = tmp_path / "TASK.md"
    task.write_text(TASK)
    runs = tmp_path / "runs"
    monkeypatch.setenv("MZT_AGENTS_DIR", str(agents))
    monkeypatch.setenv("MZT_AGENTS_RUNS_DIR", str(runs))
    monkeypatch.setenv("MZT_AGENTS_INVENTORY", str(inventory))
    monkeypatch.setenv("MZT_AGENTS_RESERVATIONS", str(tmp_path / "reserved.yaml"))
    state: dict[str, Any] = {"rows": [], "submits": []}

    async def route(method: str, params: dict[str, Any]) -> Any:
        if isinstance(state["rows"], Exception):
            raise state["rows"]
        assert method == "job.list"
        return state["rows"]

    def submit(score: Path, job_id: str) -> SubmitOutcome:
        state["submits"].append((score, job_id))
        return state.get("outcome") or SubmitOutcome(True, job_id=job_id)

    monkeypatch.setattr(conduct, "_route", route)
    monkeypatch.setattr(agents_cmd, "_submit_score", submit)
    return {
        "agents": agents,
        "inventory": inventory,
        "task": task,
        "runs": runs,
        "state": state,
        "tmp": tmp_path,
    }


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _json(result: Any) -> Any:
    assert "\x1b[" not in result.stdout
    return json.loads(result.stdout)


def _write_debt(agents: Path, name: str, **fields: Any) -> None:
    folder = agents / name / ".marianne"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pending-lifecycle-debt.yaml").write_text(
        yaml.safe_dump({"status": "pending", **fields})
    )


# --------------------------------------------------------------------------- list


def test_list_shape_matches_the_shim_fixture(world: dict[str, Any]) -> None:
    shim_s1 = Path("~/Projects/SCORES/sc1-small-model-eval/scenarios/s1.yaml").expanduser()
    if shim_s1.is_file():
        fixture = yaml.safe_load(shim_s1.read_text())["agents"]
        assert all(set(row) == LIST_KEYS for row in fixture)
    result = runner.invoke(app, ["agents", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = _json(result)
    assert [r["name"] for r in rows] == ["ines", "marek", "quill"]  # no-score agent is not listed
    for row in rows:
        assert set(row) == LIST_KEYS
        assert isinstance(row["owns"], list) and isinstance(row["free"], bool)
        assert isinstance(row["open_debt"], int)
    assert rows[0]["owns"] == ["src/signup/"] and rows[0]["last_outcome"] is None


def test_list_for_ranks_by_owned_path_and_words_deterministically(world: dict[str, Any]) -> None:
    args = ["agents", "list", "--for", str(world["task"]), "--json"]
    first = _json(runner.invoke(app, args))
    assert [r["name"] for r in first][0] == "ines"  # owns src/signup/, role and focus overlap
    assert [r["name"] for r in first][1:] == ["marek", "quill"]  # both 0: ties break by name
    assert _json(runner.invoke(app, args)) == first
    docs = world["tmp"] / "DOCS.md"
    docs.write_text(
        TASK.replace("Add input validation to the signup form.", "Rewrite the docs").replace(
            "src/signup/", "docs/guide.md"
        )
    )
    ranked = _json(runner.invoke(app, ["agents", "list", "--for", str(docs), "--json"]))
    assert ranked[0]["name"] == "marek"


def test_list_free_excludes_busy_by_job_id_and_marks_them(world: dict[str, Any]) -> None:
    world["state"]["rows"] = [
        {"job_id": "targeted-work-ines-task-ab12-20261116", "status": "running"},
        {"job_id": "targeted-work-quill-x-20261116", "status": "completed"},
        {"job_id": "lifecycle-integration-marek-care-20261116", "status": "queued"},
        {"job_id": "unrelated-job", "status": "running"},
    ]
    everyone = {r["name"]: r for r in _json(runner.invoke(app, ["agents", "list", "--json"]))}
    assert [n for n, r in everyone.items() if not r["free"]] == ["ines", "marek"]
    free = _json(runner.invoke(app, ["agents", "list", "--free", "--json"]))
    assert [r["name"] for r in free] == ["quill"]


def test_list_open_debt_comes_from_the_engage_counter(world: dict[str, Any]) -> None:
    from marianne_compiler.agent_cli import pending_debt

    _write_debt(world["agents"], "quill", carried_pending_from=["a", "b"])
    rows = {r["name"]: r for r in _json(runner.invoke(app, ["agents", "list", "--json"]))}
    assert rows["quill"]["open_debt"] == 3 == pending_debt(world["agents"], "quill")[0]
    assert rows["ines"]["open_debt"] == 0


def test_list_last_outcome_reads_newest_run_verdict(world: dict[str, Any]) -> None:
    run = world["runs"] / "ines-task-aaaa1111-20261112" / "cycle-state"
    run.mkdir(parents=True)
    (run / "ines-inspection.md").write_text("# Inspect\nVERDICT: PASS\n")
    rows = {r["name"]: r for r in _json(runner.invoke(app, ["agents", "list", "--json"]))}
    assert rows["ines"]["last_outcome"].startswith("PASS 20")
    assert rows["quill"]["last_outcome"] is None


def test_list_is_read_only_for_agents(world: dict[str, Any]) -> None:
    before = _snapshot(world["agents"])
    assert (
        runner.invoke(app, ["agents", "list", "--for", str(world["task"]), "--json"]).exit_code == 0
    )
    assert _snapshot(world["agents"]) == before


def test_list_conductor_down_is_a_coded_failure(world: dict[str, Any]) -> None:
    world["state"]["rows"] = conduct.ConductorUnreachable()
    result = runner.invoke(app, ["agents", "list", "--json"])
    assert result.exit_code == 1
    assert _json(result)["code"] == "CONDUCTOR_DOWN"


# ---------------------------------------------------------------------------- run


def _run(world: dict[str, Any], *extra: str, task: Path | None = None, name: str = "ines") -> Any:
    return runner.invoke(
        app, ["agents", "run", name, "--task", str(task or world["task"]), "--json", *extra]
    )


def test_run_ok_shape_and_in_process_engage(world: dict[str, Any]) -> None:
    result = _run(world, "--class", "work=strong")
    assert result.exit_code == 0, result.output
    out = _json(result)
    assert set(out) == RUN_OK_KEYS and out["ok"] is True
    ((score, job_id),) = world["state"]["submits"]
    assert out["job_id"] == job_id and job_id.startswith("targeted-work-ines-task-")
    assert out["result_path"] == str(Path(out["workspace"]) / "REPORT.md")
    assert Path(out["workspace"]).parent == world["runs"].resolve()
    bound = yaml.safe_load(score.read_text())
    contract = bound["prompt"]["variables"]["marianne_agent"]
    assert contract["score_shape"] == "targeted-work"
    assert contract["task"]["sha256"].startswith("sha256:")  # the task rode through --task
    assert contract["phase_classes"] == {"work": "strong"}  # --class passed through
    assert (score.parent / "task-TASK.md").is_file()


def test_run_writes_nothing_in_the_agents_home(world: dict[str, Any]) -> None:
    before = _snapshot(world["agents"])
    assert _run(world).exit_code == 0
    assert _snapshot(world["agents"]) == before
    assert not any(
        p.stat().st_mtime > 0 and world["runs"] not in p.parents
        for p in world["tmp"].rglob("task-*")
    )


@pytest.mark.parametrize(
    ("drop", "expect"),
    [("ACCEPT", "ACCEPT"), ("KIND", "KIND"), ("REPORT", "REPORT"), ("GOAL", "GOAL")],
)
def test_run_task_field_missing(world: dict[str, Any], drop: str, expect: str) -> None:
    bad = world["tmp"] / "bad.md"
    bad.write_text("".join(ln for ln in TASK.splitlines(True) if not ln.startswith(drop)))
    result = _run(world, task=bad)
    out = _json(result)
    assert result.exit_code == 1 and set(out) == RUN_FAIL_KEYS
    assert out["code"] == "TASK_FIELD_MISSING" and expect in out["hint"]
    assert world["state"]["submits"] == [] and not world["runs"].exists()


def test_run_empty_accept_is_missing(world: dict[str, Any]) -> None:
    bad = world["tmp"] / "bad.md"
    bad.write_text(TASK.replace("ACCEPT: pytest tests/signup/", "ACCEPT:"))
    out = _json(_run(world, task=bad))
    assert out["code"] == "TASK_FIELD_MISSING" and "ACCEPT" in out["hint"]


def test_run_accept_on_continuation_line_counts(world: dict[str, Any]) -> None:
    ok = world["tmp"] / "ok.md"
    ok.write_text(TASK.replace("ACCEPT: pytest tests/signup/", "ACCEPT:\n  pytest tests/signup/"))
    assert _run(world, task=ok).exit_code == 0


def test_run_missing_task_file_is_task_field_missing(world: dict[str, Any]) -> None:
    out = _json(_run(world, task=world["tmp"] / "nope.md"))
    assert out["code"] == "TASK_FIELD_MISSING"


def test_run_agent_unknown(world: dict[str, Any]) -> None:
    for name in ("ghost", "noscore", "../etc"):
        result = _run(world, name=name)
        assert result.exit_code == 1 and _json(result)["code"] == "AGENT_UNKNOWN"
    assert world["state"]["submits"] == []


def test_run_agent_busy(world: dict[str, Any]) -> None:
    world["state"]["rows"] = [{"job_id": "targeted-work-ines-old-20261115", "status": "running"}]
    result = _run(world)
    assert result.exit_code == 1 and _json(result)["code"] == "AGENT_BUSY"
    assert _run(world, name="quill").exit_code == 0  # someone else is still free
    world["state"]["rows"] = [{"job_id": "targeted-work-ines-old-20261115", "status": "completed"}]
    assert _run(world).exit_code == 0  # finished is not busy


def test_run_debt_refused_at_three(world: dict[str, Any]) -> None:
    _write_debt(world["agents"], "ines", carried_pending_from=["a", "b"])
    result = _run(world)
    out = _json(result)
    assert result.exit_code == 1 and out["code"] == "DEBT_REFUSED" and "3" in out["hint"]
    assert world["state"]["submits"] == [] and not world["runs"].exists()
    _write_debt(world["agents"], "ines", carried_pending_from=["a"])
    assert _run(world).exit_code == 0  # two is below the threshold


def test_run_without_inventory_is_refused_with_a_hint(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MZT_AGENTS_INVENTORY")
    out = _json(_run(world))
    assert out["code"] == "SUBMIT_REFUSED" and "MZT_AGENTS_INVENTORY" in out["hint"]
    missing = _json(_run(world, "--inventory", str(world["tmp"] / "none.yaml")))
    assert missing["code"] == "SUBMIT_REFUSED" and "not a file" in missing["hint"]
    assert world["state"]["submits"] == []


def test_run_stale_inventory_is_submit_refused_and_leaves_nothing(world: dict[str, Any]) -> None:
    data = yaml.safe_load(world["inventory"].read_text())
    data["profiles"][0]["verified_at"] = "2026-01-01T00:00:00Z"
    world["inventory"].write_text(yaml.safe_dump(data))
    out = _json(_run(world))
    assert out["code"] == "SUBMIT_REFUSED" and "stale" in out["hint"]
    assert world["state"]["submits"] == []


def test_run_conductor_refusal_unbinds_and_allows_a_retry(world: dict[str, Any]) -> None:
    world["state"]["outcome"] = SubmitOutcome(False, hint="Conductor rejected score: busy")
    result = _run(world)
    out = _json(result)
    assert result.exit_code == 1 and out["code"] == "SUBMIT_REFUSED" and "busy" in out["hint"]
    assert not list(world["runs"].glob("*/*.yaml"))  # no orphan bound score
    world["state"]["outcome"] = None
    assert _run(world).exit_code == 0  # the same task can be run again


def test_run_conductor_down_is_submit_refused(world: dict[str, Any]) -> None:
    world["state"]["rows"] = conduct.ConductorUnreachable()
    out = _json(_run(world))
    assert out["code"] == "SUBMIT_REFUSED" and "mzt start" in out["hint"]


def test_run_second_round_task_gets_a_new_engagement(world: dict[str, Any]) -> None:
    assert _run(world).exit_code == 0
    task2 = world["tmp"] / "TASK-2.md"
    task2.write_text(TASK + "FINDINGS: the validator accepts empty emails\n")
    out = _json(_run(world, task=task2))
    assert out["ok"] is True and len(world["state"]["submits"]) == 2
    assert world["state"]["submits"][0][1] != world["state"]["submits"][1][1]


def test_run_same_task_twice_same_day_is_refused_not_overwritten(world: dict[str, Any]) -> None:
    assert _run(world).exit_code == 0
    again = _json(_run(world))
    assert again["code"] == "SUBMIT_REFUSED" and "already bound" in again["hint"]


def test_all_error_codes_are_in_the_closed_enum() -> None:
    assert set(agents_cmd.ERROR_CODES) == CODES


# ------------------------------------------------------------ the real submit seam

# Bound at import, before any fixture replaces the module attribute: the REAL
# value-returning submit. `world` puts a fake over `agents_cmd._submit_score`;
# the end-to-end D tests must reach the real seam to prove anything, so they
# restore it and patch the conductor underneath it instead.
_REAL_SUBMIT = agents_cmd._submit_score


@pytest.fixture
def real_seam(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Put the REAL ``_submit_score`` back over ``world``'s fake."""
    monkeypatch.setattr(agents_cmd, "_submit_score", _REAL_SUBMIT)
    return []


def test_d1_stdout_noise_then_accepted_is_ok(tmp_path: Path) -> None:
    """D1, unit form (red on the old stdout capture).

    A log line on stdout during the submit must not turn an accepted job
    into a refusal. Under the repo's conftest the default structlog factory
    prints to stdout — exactly what ``redirect_stdout`` captured and
    ``json.loads`` choked on.
    """
    import structlog

    score = tmp_path / "s.yaml"
    score.write_text(
        "name: t\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt:\n  template: hi\n"
    )
    sent: list[tuple[str, dict[str, Any]]] = []

    async def noisy_route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        structlog.get_logger("test").warning("submit_noise", hint="non-JSON on stdout")
        sent.append((method, params))
        return True, {"job_id": params["job_id"], "status": "accepted", "message": "queued"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=noisy_route)),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        ok = agents_cmd._submit_score(score, "targeted-work-x-1")
    assert ok == SubmitOutcome(True, job_id="targeted-work-x-1")
    method, params = sent[0]
    assert method == "job.submit" and params["job_id"] == "targeted-work-x-1"
    assert params["escalation"] is True and params["config_path"] == str(score.resolve())


def test_d3_early_failure_keeps_the_engagement_and_names_the_job(
    world: dict[str, Any], real_seam: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D3, end to end: accepted, then failed inside the early-failure poll.

    The old code reported SUBMIT_REFUSED and (with a conductor-faithful
    status reply) left the engagement bound with no next step. The fix keeps
    the files AND says the job exists, naming ``mzt diagnose JOB``.
    """
    # A conductor-faithful reply: the real conductor's status reply carries job_id.
    monkeypatch.setattr(
        conduct,
        "_route",
        AsyncMock(return_value=[{"job_id": "x", "status": "failed", "error_message": "boom"}]),
    )
    submit_route = AsyncMock(
        return_value=(True, {"job_id": "x", "status": "accepted", "message": ""})
    )
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=submit_route),
        patch(
            "marianne.cli.commands.run.await_early_failure",
            new=AsyncMock(
                return_value={"job_id": "x", "status": "failed", "error_message": "boom"}
            ),
        ),
    ):
        result = _run(world)
    out = _json(result)
    assert result.exit_code == 1 and out["code"] == "SUBMIT_REFUSED"
    # The submission reached the conductor: an early failure can only be
    # reported for a job that was submitted (anti-vacuity, plan Phase 3).
    assert [c.args[0] for c in submit_route.await_args_list] == ["job.submit"]
    assert "accepted" in out["hint"] and "boom" in out["hint"] and "diagnose" in out["hint"]
    # The bound score is KEPT: the conductor holds a registered job for it.
    bound = list(world["runs"].glob("*/*.yaml"))
    assert bound, "the bound score must survive an early failure"
    # And the retry of the same task is still refused (it is already bound),
    # but now with a hint that says the job exists, not a silent dead end.
    result2 = _run(world)
    out2 = _json(result2)
    assert result2.exit_code == 1 and out2["code"] == "SUBMIT_REFUSED"
    assert "already bound" in out2["hint"]


def test_d4_bad_job_id_is_a_refusal(tmp_path: Path) -> None:
    """D4, unit form: an invalid job id comes back as a refusal, not an escape."""
    score = tmp_path / "s.yaml"
    score.write_text(
        "name: t\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt:\n  template: hi\n"
    )
    called: list[str] = []

    async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        called.append(method)
        return True, {"job_id": "x", "status": "accepted"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
    ):
        out = agents_cmd._submit_score(score, "bad job id!")
    assert not called, "an invalid job id must not reach the conductor"
    assert not out.ok and out.hint and "bad job id!" in out.hint


def test_d4_bad_job_id_through_the_cli_is_exit_1_with_a_json_body(
    world: dict[str, Any], real_seam: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D4, end to end: exit 1 with ``{ok, code, hint}``, and no job.submit routed."""
    import marianne_compiler.agent_cli as agent_cli_mod

    real_bind = agent_cli_mod.bind_engagement

    def binder(agent: str, **kw: Any) -> Any:
        engagement = real_bind(agent, **kw)
        # The binder derives job_name from validated names, so force an
        # invalid one the way an embedding caller could.
        object.__setattr__(engagement, "job_name", "bad job id!")
        return engagement

    monkeypatch.setattr(agent_cli_mod, "bind_engagement", binder)
    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch(
            "marianne.daemon.detect.try_daemon_route",
            new=AsyncMock(return_value=(True, {"job_id": "x", "status": "accepted"})),
        ),
    ):
        result = _run(world)
    out = _json(result)
    assert result.exit_code == 1 and set(out) == RUN_FAIL_KEYS
    assert out["code"] == "SUBMIT_REFUSED" and "bad job id!" in out["hint"]
    # No bound score is left behind (BAD_JOB_ID carries no job_exists).
    assert not list(world["runs"].glob("*/*.yaml"))


def test_submit_sends_job_id_and_escalation(tmp_path: Path) -> None:
    """Guard: the refactor keeps today's ``params`` (job id, escalation, path)."""
    score = tmp_path / "s.yaml"
    score.write_text(
        "name: t\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt:\n  template: hi\n"
    )
    sent: list[tuple[str, dict[str, Any]]] = []

    async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        sent.append((method, params))
        return True, {"job_id": params["job_id"], "status": "accepted", "message": "queued"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=route)),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        ok = agents_cmd._submit_score(score, "targeted-work-x-2")
    assert ok == SubmitOutcome(True, job_id="targeted-work-x-2")
    method, params = sent[0]
    assert method == "job.submit" and params["job_id"] == "targeted-work-x-2"
    assert params["escalation"] is True and params["config_path"] == str(score.resolve())


# ---- the status -> SubmitOutcome mapping, one branch per test (review Q5).
# `submit_score` itself is patched, so each test pins one branch of
# _submit_score and nothing below it. Before these existed, four of the five
# failure branches could be inverted with every test green.


def _mapped(tmp_path: Path, result: Any) -> SubmitOutcome:
    """What ``_submit_score`` turns one fixed ``SubmitResult`` into."""
    score = tmp_path / "s.yaml"
    score.write_text("name: t\nsheet: {size: 1, total_items: 1}\nprompt:\n  template: hi\n")
    with patch(
        "marianne.cli.commands.run.submit_score",
        new=AsyncMock(return_value=result),
    ):
        return agents_cmd._submit_score(score, "targeted-work-x-3")


def test_map_rejected_is_not_ok(tmp_path: Path) -> None:
    run_module = importlib.import_module("marianne.cli.commands.run")
    out = _mapped(tmp_path, run_module.SubmitResult(run_module.SubmitStatus.REJECTED, message="no"))
    assert out == SubmitOutcome(False, hint="no")


def test_map_not_routed_is_not_ok_and_says_start(tmp_path: Path) -> None:
    run_module = importlib.import_module("marianne.cli.commands.run")
    out = _mapped(tmp_path, run_module.SubmitResult(run_module.SubmitStatus.NOT_ROUTED))
    assert not out.ok and not out.job_exists
    assert "mzt start" in out.hint


def test_map_route_error_is_not_ok_and_names_the_cause(tmp_path: Path) -> None:
    run_module = importlib.import_module("marianne.cli.commands.run")
    out = _mapped(
        tmp_path,
        run_module.SubmitResult(run_module.SubmitStatus.ROUTE_ERROR, cause=ValueError("x")),
    )
    assert not out.ok and not out.job_exists
    assert "x" in out.hint


def test_map_pending_is_ok_with_the_job_id(tmp_path: Path) -> None:
    run_module = importlib.import_module("marianne.cli.commands.run")
    out = _mapped(tmp_path, run_module.SubmitResult(run_module.SubmitStatus.PENDING, job_id="p-1"))
    assert out == SubmitOutcome(True, job_id="p-1")


def test_d1_run_survives_a_log_line_during_submit(
    world: dict[str, Any], real_seam: list[str]
) -> None:
    """Guard, end to end: stdout is one JSON document even when the submit logs."""
    import structlog

    async def noisy_route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        structlog.get_logger("test").warning("submit_noise")
        return True, {"job_id": "x", "status": "accepted", "message": ""}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=AsyncMock(side_effect=noisy_route)),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        result = _run(world)
    assert result.exit_code == 0, result.output
    assert _json(result)["ok"] is True


def test_parse_task_matches_the_shim_rules() -> None:
    fields = parse_task(
        "- GOAL: a\n> FILES: unknown\nKIND:internal\nACCEPT:\n  run it\nREPORT: r.md\n"
    )
    assert fields["GOAL"] == "a" and fields["KIND"] == "internal"
    assert fields["ACCEPT"] == "run it" and "DO_NOT_TOUCH" not in fields


# ---------------------------------------------------------------- reservations


def test_reserved_agent_is_busy_for_list_and_refused_by_run(world: dict[str, Any]) -> None:
    """A peer seat's reservation keeps a person out of small-model casting."""
    (world["tmp"] / "reserved.yaml").write_text(
        yaml.safe_dump({"reserved": {"Ines": "CA queue: care then contract round 2"}})
    )
    rows = _json(runner.invoke(app, ["agents", "list", "--free", "--json"]))
    assert "ines" not in {r["name"] for r in rows}
    assert "quill" in {r["name"] for r in rows}
    listed = {r["name"]: r for r in _json(runner.invoke(app, ["agents", "list", "--json"]))}
    assert listed["ines"]["free"] is False
    result = _run(world)
    body = _json(result)
    assert result.exit_code == 1 and body["code"] == "AGENT_BUSY"
    assert "reserved" in body["hint"] and "CA queue" in body["hint"]
    assert world["state"]["submits"] == []
    assert _run(world, name="quill").exit_code == 0


def test_unreadable_reservations_reserve_nobody(world: dict[str, Any]) -> None:
    (world["tmp"] / "reserved.yaml").write_text("reserved: [unclosed\n")
    assert _run(world).exit_code == 0
