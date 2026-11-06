"""Exercise proof gates with real artifacts, including tempting false positives."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "scores/rosetta-corpus"
CHECKS = CORPUS / "tools/long_tail_proof.py"


def invoke(mode: str, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(CHECKS), mode, str(workspace), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def put(workspace: Path, name: str, data: object) -> None:
    (workspace / name).write_text(json.dumps(data))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    result = invoke("init", tmp_path)
    assert result.returncode == 0, result.stderr
    return tmp_path


def page_comments(workspace: Path) -> None:
    put(
        workspace,
        "commentary-1.json",
        [
            {"id": "c1", "source": "s1", "text": "Retries need an operation identifier."},
            {"id": "c2", "source": "s2", "text": "Timeouts leave completion uncertain."},
        ],
    )
    put(
        workspace,
        "commentary-2.json",
        [
            {
                "id": "r1",
                "source": "s1",
                "reply_to": "c1",
                "text": "Store the identifier before retry.",
            },
            {
                "id": "r2",
                "source": "s2",
                "reply_to": "c2",
                "text": "Inspect receipts after timeouts.",
            },
        ],
    )


def test_page_requires_commentary_to_respond_to_an_existing_layer(workspace: Path) -> None:
    page_comments(workspace)
    assert invoke("page-second", workspace).returncode == 0
    comments = json.loads((workspace / "commentary-2.json").read_text())
    comments[0]["reply_to"] = "invented"
    put(workspace, "commentary-2.json", comments)
    assert invoke("page-second", workspace).returncode != 0


def test_published_page_contains_real_layers_and_resolvable_links(workspace: Path) -> None:
    page_comments(workspace)
    source = json.loads((workspace / "source.json").read_text())["text"]
    (workspace / "page.md").write_text(
        f'<a id="s1"></a>{source["s1"]}\n<a id="s2"></a>{source["s2"]}\n'
        '<a id="c1"></a>Retries need an operation identifier. [source](#s1)\n'
        '<a id="c2"></a>Timeouts leave completion uncertain. [source](#s2)\n'
        '<a id="r1"></a>Store the identifier before retry. [reply](#c1) [source](#s1)\n'
        '<a id="r2"></a>Inspect receipts after timeouts. [reply](#c2) [source](#s2)\n'
    )
    assert invoke("page", workspace).returncode == 0
    path = workspace / "page.md"
    path.write_text(path.read_text().replace('id="c1"', 'id="wrong"'))
    assert invoke("page", workspace).returncode != 0


def editorial(workspace: Path) -> dict:
    return {
        "position": "adopt",
        "argument": "Adopt a bounded pilot because the capacity is available.",
        "support": [{"source": "ava", "quote": "The pilot needs two workers."}],
        "dissent": {
            "source": "bo",
            "author": "Bo",
            "quote": "Rollback takes one hour.",
            "response": "Keep a spare hour in the pilot schedule.",
        },
        "conclusion": "Run the pilot with reserved rollback time.",
    }


@pytest.mark.parametrize("bad", ["quote", "author", "position"])
def test_editorial_gate_requires_grounded_support_and_named_opposing_dissent(
    workspace: Path,
    bad: str,
) -> None:
    data = editorial(workspace)
    put(workspace, "editorial.json", data)
    assert invoke("weave", workspace).returncode == 0
    if bad == "quote":
        data["support"][0]["quote"] = "A fabricated statistic"
    elif bad == "author":
        data["dissent"]["author"] = "Anonymous"
    else:
        data["dissent"]["source"] = "ava"
        data["dissent"]["author"] = "Ava"
        data["dissent"]["quote"] = "The pilot needs two workers."
    put(workspace, "editorial.json", data)
    assert invoke("weave", workspace).returncode != 0


def brief(workspace: Path) -> list[dict]:
    logs = [json.loads(line) for line in (workspace / "raw-log.jsonl").read_text().splitlines()]
    return [{"id": row["id"], "quote": row["text"]} for row in logs if row["critical"]]


def test_observer_must_preserve_every_critical_finding_and_compress(workspace: Path) -> None:
    findings = brief(workspace)
    put(workspace, "brief.json", findings)
    assert invoke("observer", workspace).returncode == 0
    put(workspace, "brief.json", findings[:-1])
    assert invoke("observer", workspace).returncode != 0
    put(workspace, "brief.json", findings)
    with (workspace / "brief.json").open("a") as handle:
        handle.write(" " * 20000)
    assert invoke("observer", workspace).returncode != 0


def test_operator_recommendations_cover_the_brief(workspace: Path) -> None:
    put(workspace, "brief.json", brief(workspace))
    put(
        workspace,
        "actions.json",
        [
            {"id": "L050", "action": "Renew the expired certificate."},
            {"id": "L150", "action": "Repair the exhausted connection pool."},
        ],
    )
    assert invoke("operator", workspace).returncode == 0
    put(workspace, "actions.json", [{"id": "L050", "action": "Renew the certificate."}])
    assert invoke("operator", workspace).returncode != 0


def signals(workspace: Path) -> None:
    for owner, fact in [
        (1, "Use request_id in the API example."),
        (2, "Retry requires a saved receipt."),
    ]:
        put(workspace, f"signal-{owner}.json", {"owner": owner, "fact": fact})
        put(
            workspace,
            f"result-{3 - owner}.json",
            {
                "owner": 3 - owner,
                "peer": owner,
                "signal_sha256": digest(workspace / f"signal-{owner}.json"),
                "adopted_fact": fact,
                "proposal": "The revised guide incorporates the peer finding.",
            },
        )


def test_workspace_peer_consumption_is_bound_to_actual_signal_bytes(workspace: Path) -> None:
    signals(workspace)
    assert invoke("workspace", workspace).returncode == 0
    signal = workspace / "signal-1.json"
    signal.write_text(signal.read_text() + "\n")
    assert invoke("workspace", workspace).returncode != 0


def review(workspace: Path) -> None:
    put(
        workspace,
        "aar.json",
        {
            "intended": "Trim and casefold each label.",
            "actual": ["a", "b"],
            "cause": "The first implementation strips whitespace but preserves letter case.",
            "sustain": "Continue stripping edge whitespace.",
            "improve": "Apply casefold after strip.",
            "rule": "strip-casefold",
            "evidence": ["a", "b"],
        },
    )


def test_aar_rejects_generic_review_and_persists_specific_learning(workspace: Path) -> None:
    review(workspace)
    assert invoke("aar-review", workspace).returncode == 0
    assert invoke("aar-memory", workspace).returncode == 0
    memory = json.loads((workspace / "memory.json").read_text())
    assert memory["rule"] == "strip-casefold"
    assert memory["review_sha256"] == digest(workspace / "aar.json")
    data = json.loads((workspace / "aar.json").read_text())
    data["evidence"] = []
    put(workspace, "aar.json", data)
    assert invoke("aar-review", workspace).returncode != 0


def test_second_run_must_consume_memory_and_correct_measured_failures(workspace: Path) -> None:
    review(workspace)
    assert invoke("aar-memory", workspace).returncode == 0
    data = {
        "memory_sha256": digest(workspace / "memory.json"),
        "results": {"a": "alpha", "b": "beta", "c": "gamma"},
    }
    put(workspace, "run-2.json", data)
    assert invoke("aar-run", workspace).returncode == 0
    data["results"]["a"] = "ALPHA"
    put(workspace, "run-2.json", data)
    assert invoke("aar-run", workspace).returncode != 0
    data["results"]["a"] = "alpha"
    data["memory_sha256"] = "not-the-memory"
    put(workspace, "run-2.json", data)
    assert invoke("aar-run", workspace).returncode != 0


def screens(workspace: Path) -> None:
    put(
        workspace,
        "screen-1.json",
        [
            {"id": "a", "status": "solved", "answer": 5},
            {"id": "b", "status": "solved", "answer": 12},
            {"id": "c", "status": "escalate", "reason": "Need packet C."},
            {"id": "d", "status": "escalate", "reason": "Need policy and exception evidence."},
        ],
    )
    put(
        workspace,
        "screen-2.json",
        [
            {"id": "c", "status": "solved", "answer": 10},
            {"id": "d", "status": "escalate", "reason": "Need signed exception record."},
        ],
    )
    put(workspace, "screen-3.json", [{"id": "d", "status": "solved", "answer": 0}])


@pytest.mark.parametrize("bad", ["drop", "reprocess", "wrong", "all_escalated"])
def test_cascade_routes_only_unresolved_items_and_conserves_every_item(
    workspace: Path,
    bad: str,
) -> None:
    screens(workspace)
    assert invoke("cascade", workspace).returncode == 0
    if bad == "drop":
        put(workspace, "screen-3.json", [])
    elif bad == "reprocess":
        data = json.loads((workspace / "screen-2.json").read_text())
        data.append({"id": "a", "status": "solved", "answer": 5})
        put(workspace, "screen-2.json", data)
    elif bad == "wrong":
        put(workspace, "screen-3.json", [{"id": "d", "status": "solved", "answer": 1}])
    else:
        put(
            workspace,
            "screen-1.json",
            [{"id": key, "status": "escalate", "reason": "Everything is hard."} for key in "abcd"],
        )
    assert invoke("cascade", workspace).returncode != 0


@pytest.mark.parametrize(
    "slug",
    [
        "talmudic-page",
        "sugya-weave-editorial-synthesis",
        "forward-observer",
        "stigmergic-workspace",
        "after-action-review",
        "screening-cascade",
    ],
)
def test_score_deterministic_spine_runs_with_valid_artifacts(slug: str, tmp_path: Path) -> None:
    """Run shipped shell stages and gates against controlled agent artifacts."""
    import shlex

    from jinja2 import Environment, StrictUndefined

    from marianne.core.config import JobConfig
    from marianne.core.sheet import build_sheets

    score = CORPUS / "proof-scores" / f"{slug}.yaml"
    config = JobConfig.from_yaml(score)
    config.workspace = tmp_path
    sheets = build_sheets(config)
    if slug == "stigmergic-workspace":
        # Both second-wave consumers must wait for both first-wave producers.
        assert config.sheet.dependencies[4] == [2, 3]
        assert config.sheet.dependencies[5] == [4]
        assert config.sheet.dependencies[6] == [4]
        assert config.sheet.dependencies[7] == [5, 6]
    renderer = Environment(undefined=StrictUndefined)
    for sheet in sheets:
        stage, voice = sheet.movement, sheet.voice or 1
        if sheet.instrument_name == "cli":
            assert sheet.instrument_fallbacks == []
            command = renderer.from_string(config.prompt.template).render(
                stage=stage,
                instance=voice,
                workspace=str(tmp_path),
                score_dir=str(score.parent),
            )
            run = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert run.returncode == 0, run.stderr
        elif stage == 2:
            # Controlled artifacts stand in for agent judgments solely to exercise
            # the deterministic shell spine and declared validation commands.
            page_comments(tmp_path)
            source = json.loads((tmp_path / "source.json").read_text())["text"]
            (tmp_path / "page.md").write_text(
                f'<a id="s1"></a>{source["s1"]}\n<a id="s2"></a>{source["s2"]}\n'
                '<a id="c1"></a>Retries need an operation identifier. [source](#s1)\n'
                '<a id="c2"></a>Timeouts leave completion uncertain. [source](#s2)\n'
                '<a id="r1"></a>Store the identifier before retry. [reply](#c1) [source](#s1)\n'
                '<a id="r2"></a>Inspect receipts after timeouts. [reply](#c2) [source](#s2)\n'
            )
            put(tmp_path, "editorial.json", editorial(tmp_path))
            put(tmp_path, "brief.json", brief(tmp_path))
            put(
                tmp_path,
                "actions.json",
                [
                    {"id": "L050", "action": "Renew the expired certificate."},
                    {"id": "L150", "action": "Repair the exhausted connection pool."},
                ],
            )
            signals(tmp_path)
            review(tmp_path)
            screens(tmp_path)
        if slug == "after-action-review" and stage == 4:
            put(
                tmp_path,
                "run-2.json",
                {
                    "memory_sha256": digest(tmp_path / "memory.json"),
                    "results": {"a": "alpha", "b": "beta", "c": "gamma"},
                },
            )
        for rule in config.validations:
            if rule.condition != f"stage == {stage}":
                continue
            if rule.type == "file_exists":
                assert Path(rule.path.format(workspace=tmp_path, instance=voice)).is_file()
            elif rule.type == "command_succeeds":
                command = rule.command.format(workspace=shlex.quote(str(tmp_path)), instance=voice)
                run = subprocess.run(
                    ["bash", "-c", command],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert run.returncode == 0, run.stderr
