"""SC1c: ``mzt agents list`` and ``mzt agents run`` (interim conducting surface).

No real conductor and no real agents: a fake agents dir and inventory live in
``tmp_path``, the conductor seam (``conduct._route``) and the submit
(``agents_cmd._submit_score``) are replaced. The JSON shapes are checked against
the scenario fixtures the SC1 shim served to the qualified conductor card.
"""

from __future__ import annotations

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
        "sheet": {"per_sheet_instruments": {}, "per_sheet_fallbacks": {}},
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


# ------------------------------------------------------------ submit goes through mzt run


def test_submit_score_uses_the_mzt_run_path_and_reads_its_json(tmp_path: Path) -> None:
    score = tmp_path / "s.yaml"
    score.write_text(
        yaml.safe_dump(
            {
                "name": "t",
                "workspace": str(tmp_path / "ws"),
                "sheet": {"size": 1, "total_items": 1},
                "prompt": {"template": "hi"},
            }
        )
    )
    sent: list[tuple[str, dict[str, Any]]] = []

    async def route(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        sent.append((method, params))
        return True, {"job_id": params["job_id"], "status": "accepted", "message": "queued"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=route),
        patch("marianne.cli.commands.run.await_early_failure", new=AsyncMock(return_value=None)),
    ):
        ok = agents_cmd._submit_score(score, "targeted-work-x-1")
    assert ok == SubmitOutcome(True, job_id="targeted-work-x-1")
    method, params = sent[0]
    assert method == "job.submit" and params["job_id"] == "targeted-work-x-1"
    assert params["escalation"] is True and params["config_path"] == str(score.resolve())

    async def reject(method: str, params: dict[str, Any]) -> tuple[bool, Any]:
        return True, {"job_id": "", "status": "rejected", "message": "System under pressure"}

    with (
        patch("marianne.daemon.detect.is_daemon_available", new=AsyncMock(return_value=True)),
        patch("marianne.daemon.detect.try_daemon_route", new=reject),
        patch("marianne.cli.commands.run._show_rate_limits_on_rejection", new=AsyncMock()),
    ):
        refused = agents_cmd._submit_score(score, "targeted-work-x-2")
    assert not refused.ok and "pressure" in refused.hint


def test_parse_task_matches_the_shim_rules() -> None:
    fields = parse_task(
        "- GOAL: a\n> FILES: unknown\nKIND:internal\nACCEPT:\n  run it\nREPORT: r.md\n"
    )
    assert fields["GOAL"] == "a" and fields["KIND"] == "internal"
    assert fields["ACCEPT"] == "run it" and "DO_NOT_TOUCH" not in fields
