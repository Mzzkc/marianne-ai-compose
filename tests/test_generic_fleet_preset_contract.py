"""Contracts for the shipped generic fleet preset."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

try:
    from marianne_compiler.pipeline import CompilationPipeline
    from marianne_compiler.presets import load_builtin_preset, prepare_builtin_preset
except ModuleNotFoundError as exc:
    if exc.name != "marianne_compiler":
        raise
    pytestmark = pytest.mark.skip(reason="marianne_compiler is an optional submodule package")
    CompilationPipeline = None
    load_builtin_preset = None
    prepare_builtin_preset = None

from marianne.core.config import JobConfig
from marianne.core.config.techniques import TechniqueConfig
from marianne.daemon.baton.techniques import resolve_techniques_for_sheet
from marianne.execution.validation.engine import ValidationEngine
from marianne.validation.rendering import generate_preview

REQUIRED_WORKSPACE_DIRS = {
    "shared/active",
    "shared/plans",
    "shared/findings",
    "shared/decisions",
    "shared/directives",
    "shared/specs",
    "shared/archive",
    "agents",
    "collective",
    "playspace",
    "cycle-state",
}

REQUIRED_ACTIVE_FILES = {
    "00-cadenza-coordination.md",
    "01-task-board.md",
    "02-agent-status.md",
    "03-findings.md",
    "04-decision-log.md",
    "05-directives.md",
    "06-handoff-index.md",
}


def _compile_generic_fleet(
    tmp_path: Path,
    *,
    cwd: Path | None = None,
    output_in_workspace: bool = False,
) -> tuple[dict, Path, Path, Path]:
    base = cwd or Path.cwd()
    workspace = tmp_path / "workspace"
    output_dir = workspace / "scores" if output_in_workspace else tmp_path / "scores"
    config = prepare_builtin_preset(
        load_builtin_preset("generic-fleet"),
        name="generic-fleet",
        cwd=base,
        workspace=workspace,
    )
    pipeline = CompilationPipeline(agents_dir=tmp_path / "agents")
    pipeline.compile_config(config, output_dir, base_dir=base)
    return config, workspace, output_dir, tmp_path / "agents"


def test_generic_fleet_seeded_active_cadenza_renders(tmp_path: Path) -> None:
    _config, _workspace, output_dir, _agents_dir = _compile_generic_fleet(tmp_path)

    score_path = output_dir / "canyon.yaml"
    score = JobConfig.model_validate(yaml.safe_load(score_path.read_text()))

    preview = generate_preview(score, score_path, max_sheets=3)

    assert not preview.render_errors
    rendered = "\n\n".join(sheet.rendered_prompt or "" for sheet in preview.sheets)
    assert "Cadenza Coordination Contract" in rendered
    assert "Task Board" in rendered
    assert "Agent Status" in rendered
    assert "owner-scoped row once" in rendered
    assert "COORDINATION UPDATE BLOCKED:" in rendered
    assert "date -u +%Y-%m-%dT%H:%MZ" in rendered
    assert "never append `Z` to local time" in rendered
    assert "canyon-specialist" in rendered
    assert "{agent}-T-001" in rendered
    assert "canyon-T-001" not in rendered
    assert "north-D-001" not in rendered
    assert "T-123" not in rendered

    techniques = {
        name: TechniqueConfig.model_validate(value)
        for name, value in yaml.safe_load(score_path.read_text())["techniques"].items()
    }
    resolved = resolve_techniques_for_sheet(techniques, "3")
    assert "canyon-specialist" in resolved.skill_docs
    assert "Canyon Specialist Technique" in resolved.skill_docs["canyon-specialist"]


def test_generic_fleet_workspace_seed_contract(tmp_path: Path) -> None:
    config, workspace, output_dir, agents_dir = _compile_generic_fleet(tmp_path)

    for dirname in REQUIRED_WORKSPACE_DIRS:
        assert (workspace / dirname).is_dir(), dirname
    for filename in REQUIRED_ACTIVE_FILES:
        assert (workspace / "shared" / "active" / filename).is_file(), filename
    active_text = "\n".join(
        (workspace / "shared" / "active" / filename).read_text()
        for filename in REQUIRED_ACTIVE_FILES
    )
    assert "Concurrent Write Safety" in active_text
    assert "owner-scoped row once" in active_text
    assert "{agent}-T-001" in active_text
    assert "{agent}-F-001" in active_text
    assert "{agent}-D-001" in active_text
    assert "{source}-DIR-001" in active_text
    assert "{agent}-H-001" in active_text
    assert "canyon-T-001" not in active_text
    assert "sentinel-F-001" not in active_text
    assert "north-D-001" not in active_text
    assert "composer-DIR-001" not in active_text
    assert "canyon-H-001" not in active_text
    assert "T-123" not in active_text
    assert "F-123" not in active_text
    assert "D-123" not in active_text
    assert "DIR-123" not in active_text
    assert "H-123" not in active_text

    task_board = workspace / "shared" / "active" / "01-task-board.md"
    custom = "# Task Board\n\ncustom live coordination state\n"
    task_board.write_text(custom)
    pipeline = CompilationPipeline(agents_dir=agents_dir)
    pipeline.compile_config(config, output_dir, base_dir=Path.cwd())
    assert task_board.read_text() == custom


def test_generic_fleet_scores_wire_coordination_and_specialists(
    tmp_path: Path,
) -> None:
    config, _workspace, output_dir, agents_dir = _compile_generic_fleet(tmp_path)

    for agent in config["agents"]:
        name = agent["name"]
        score = yaml.safe_load((output_dir / f"{name}.yaml").read_text())

        cadenza_by_sheet = score["sheet"]["cadenzas"]
        for sheet_num in (1, 2, 3, 5, 7):
            assert {
                "directory": "{{workspace}}/shared/active",
                "as": "context",
            } in cadenza_by_sheet[sheet_num]

        specialist = f"{name}-specialist"
        technique = score["techniques"][specialist]
        assert technique["kind"] == "skill"
        assert "3" in technique["phases"]
        assert Path(technique["config"]["path"]).exists()
        assert "symbols-python" not in score["techniques"]
        assert "4" not in score["techniques"]["voice"]["phases"]
        assert "11" not in score["techniques"]["filesystem"]["phases"]

        cadenza_files = [
            item["file"]
            for items in cadenza_by_sheet.values()
            for item in items
            if "file" in item
        ]
        assert str(Path(technique["config"]["path"])) not in cadenza_files

        identity_dir = agents_dir / name
        for filename in ("identity.md", "profile.yaml", "recent.md", "growth.md"):
            assert (identity_dir / filename).is_file()
        assert (identity_dir / "archive").is_dir()


def test_generic_fleet_preset_is_generic_and_uses_portable_self_chain(
    tmp_path: Path,
) -> None:
    _config, _workspace, output_dir, _agents_dir = _compile_generic_fleet(
        tmp_path,
        output_in_workspace=True,
    )

    canyon = yaml.safe_load((output_dir / "canyon.yaml").read_text())
    assert "flowspec" not in str(canyon).lower()
    assert "llama-4-maverick" not in str(canyon).lower()
    assert "kimi" not in str(canyon).lower()
    assert "claude-code--glm-5.3-1m" in canyon["instruments"]
    assert "antigravity--gemini-3.7-flash-medium" in canyon["instruments"]
    assert "antigravity--gemini-3.7-flash-low" in canyon["instruments"]
    assert "antigravity--gemini-3.5-flash-medium" not in canyon["instruments"]
    assert "antigravity--gemini-3.5-flash-low" not in canyon["instruments"]
    assert "antigravity--gemini-3.5-flash" not in canyon["instruments"]
    assert "antigravity--gemini-3.5-flash-lite" not in canyon["instruments"]
    assert "gemini-cli--gemini-3.5-flash" not in canyon["instruments"]
    assert canyon["sheet"]["per_sheet_instruments"][5] == "claude-code--glm-5-turbo"
    assert canyon["instruments"]["claude-code--glm-5-turbo"]["config"] == {
        "model": "glm-5-Turbo",
    }

    codex_fallback_sheets = {
        int(sheet)
        for sheet, fallbacks in canyon["sheet"]["per_sheet_fallbacks"].items()
        if "codex-cli" in fallbacks
    }
    assert codex_fallback_sheets == {3, 5, 6, 7}
    assert "codex-cli" not in canyon["sheet"]["per_sheet_instruments"].values()
    assert canyon["on_success"][0]["job_path"] == "{workspace}/scores/canyon.yaml"

    cadenza_validation_descriptions = [
        rule["description"]
        for rule in canyon["validations"]
        if rule["description"].startswith("Cadenza completion state")
    ]
    assert cadenza_validation_descriptions == [
        "Cadenza completion state for canyon recon",
        "Cadenza completion state for canyon plan",
        "Cadenza completion state for canyon work",
        "Cadenza completion state for canyon integration",
        "Cadenza completion state for canyon inspect",
    ]

    fleet = yaml.safe_load((output_dir / "fleet.yaml").read_text())
    assert fleet["scores"]
    for entry in fleet["scores"]:
        assert not Path(entry["path"]).is_absolute()
        assert (output_dir / entry["path"]).is_file()


def test_generic_fleet_uses_packaged_techniques_outside_repo(
    tmp_path: Path,
) -> None:
    outside_repo = tmp_path / "outside-project"
    outside_repo.mkdir()
    _config, _workspace, output_dir, _agents_dir = _compile_generic_fleet(
        tmp_path,
        cwd=outside_repo,
    )

    canyon = yaml.safe_load((output_dir / "canyon.yaml").read_text())
    technique_path = Path(canyon["techniques"]["canyon-specialist"]["config"]["path"])
    assert technique_path.exists()
    assert "compiler/src/marianne_compiler/assets/techniques" in str(technique_path)


def test_generic_fleet_cli_phase_templates_execute(tmp_path: Path) -> None:
    _config, workspace, output_dir, agents_dir = _compile_generic_fleet(tmp_path)
    score = yaml.safe_load((output_dir / "canyon.yaml").read_text())
    template = Environment().from_string(score["prompt"]["template"])
    agent_dir = agents_dir / "canyon"
    (workspace / "TASKS.md").write_text("- [x] Done (priority: P0)\n")
    (agent_dir / "profile.yaml").write_text(
        "developmental_stage: recognition\n"
        "standing_pattern_count: 0\n"
        "cycle_count: 10\n"
        "last_play_cycle: 0\n"
    )

    common_vars = {
        **score["prompt"]["variables"],
        "workspace": str(workspace),
        "agent_identity_dir": str(agent_dir),
        "agent_name": "canyon",
        "stage": 4,
    }
    temp_command = template.render(**common_vars)
    temp_result = subprocess.run(
        ["bash", "-c", temp_command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert temp_result.returncode == 0, temp_result.stderr
    assert (workspace / "cycle-state" / "temperature-canyon-play").exists()

    maturity_command = template.render(**{**common_vars, "stage": 11})
    maturity_result = subprocess.run(
        ["bash", "-c", maturity_command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert maturity_result.returncode == 0, maturity_result.stderr
    assert (workspace / "cycle-state" / "canyon-maturity-report.yaml").exists()


async def test_generic_fleet_recon_score_count_claim_validation(
    tmp_path: Path,
) -> None:
    """Recon reports may not invent concrete agent-score counts."""
    _config, workspace, output_dir, _agents_dir = _compile_generic_fleet(
        tmp_path,
        output_in_workspace=True,
    )
    score = JobConfig.model_validate(yaml.safe_load((output_dir / "bedrock.yaml").read_text()))
    rules = [
        rule
        for rule in score.validations
        if rule.description == "Recon score-count claims match disk for bedrock"
    ]
    assert len(rules) == 1

    recon = workspace / "cycle-state" / "bedrock-recon.md"
    recon.parent.mkdir(exist_ok=True)
    recon.write_text(
        "# Bedrock Recon\n\n"
        "OBSERVED:\nThirty-six agent scores seeded under `scores/`.\n\n"
        "CHANGED:\nNo change.\n\n"
        "CANDIDATES:\nNone.\n\n"
        "RISKS:\nWrong count.\n\n"
        "EVIDENCE:\nDisk should decide.\n"
    )
    engine = ValidationEngine(workspace=workspace, sheet_context={"stage": 1})
    result = await engine.run_validations(rules)
    assert result.all_passed is False
    assert "disagree with disk count 32" in (result.results[0].error_message or "")

    recon.write_text(
        "# Bedrock Recon\n\n"
        "OBSERVED:\nThirty-two agent scores seeded under `scores/`.\n\n"
        "CHANGED:\nNo change.\n\n"
        "CANDIDATES:\nNone.\n\n"
        "RISKS:\nNone.\n\n"
        "EVIDENCE:\nDisk count verified.\n"
    )
    result = await engine.run_validations(rules)
    assert result.all_passed is True


async def test_generic_fleet_cadenza_completion_validation_catches_stale_claim(
    tmp_path: Path,
) -> None:
    """Generated sheets may not pass with stale claimed cadenza rows."""
    _config, workspace, output_dir, _agents_dir = _compile_generic_fleet(
        tmp_path,
        output_in_workspace=True,
    )
    score = JobConfig.model_validate(yaml.safe_load((output_dir / "bedrock.yaml").read_text()))
    rules = [
        rule
        for rule in score.validations
        if rule.description == "Cadenza completion state for bedrock plan"
    ]
    assert len(rules) == 1

    plan = workspace / "cycle-state" / "bedrock-plan.md"
    plan.parent.mkdir(exist_ok=True)
    plan.write_text(
        "CLAIMED WORK:\nPlan the work.\n\n"
        "SUCCESS CRITERIA:\nEvidence is concrete.\n\n"
        "STEPS:\nCheck the files.\n\n"
        "RISKS:\nStale cadenza state.\n\n"
        "VALIDATION:\nRun the generated cadenza validation.\n"
    )
    task_board = workspace / "shared" / "active" / "01-task-board.md"
    task_board.write_text(
        "# Task Board\n\n"
        "| id | owner | status | task | evidence |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| bedrock-T-002 | bedrock | claimed | Write cycle plan. | "
        "`cycle-state/bedrock-plan.md` |\n"
    )
    status_board = workspace / "shared" / "active" / "02-agent-status.md"
    current_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    status_board.write_text(
        "# Agent Status\n\n"
        "| agent | phase | state | current work | next handoff | updated |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| bedrock | plan | claimed | Write cycle plan. | | {current_utc} |\n"
    )

    engine = ValidationEngine(workspace=workspace, sheet_context={"stage": 2})
    result = await engine.run_validations(rules)
    assert result.all_passed is False
    error = result.results[0].error_message or ""
    assert "missing done task-board row" in error
    assert "missing complete agent-status row" in error

    task_board.write_text(
        "# Task Board\n\n"
        "| id | owner | status | task | evidence |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| bedrock-T-002 | bedrock | done | Write cycle plan. | `cycle-state/bedrock-plan.md` |\n"
    )
    status_board.write_text(
        "# Agent Status\n\n"
        "| agent | phase | state | current work | next handoff | updated |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| bedrock | plan | complete | Write cycle plan. | | {current_utc} |\n"
    )

    result = await engine.run_validations(rules)
    assert result.all_passed is True

    task_board.write_text(
        "# Task Board\n\n"
        "| id | owner | status | task | evidence |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| bedrock-T-002 | bedrock | done | Write cycle plan. | `cycle-state/bedrock-plan.md` |\n"
        "| bedrock-T-002 | bedrock | done | Duplicate row. | `cycle-state/bedrock-plan.md` |\n"
    )
    result = await engine.run_validations(rules)
    assert result.all_passed is False
    error = result.results[0].error_message or ""
    assert "repeats concrete cadenza id 'bedrock-T-002'" in error

    task_board.write_text(
        "# Task Board\n\n"
        "| id | owner | status | task | evidence |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| T-002 | bedrock | done | Write cycle plan. | `cycle-state/bedrock-plan.md` |\n"
    )
    result = await engine.run_validations(rules)
    assert result.all_passed is False
    error = result.results[0].error_message or ""
    assert "uses global numeric cadenza id 'T-002'" in error

    task_board.write_text(
        "# Task Board\n\n"
        "| id | owner | status | task | evidence |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| bedrock-T-002 | bedrock | done | Write cycle plan. | `cycle-state/bedrock-plan.md` |\n"
    )

    future_local_as_z = (datetime.now(UTC) + timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%MZ"
    )
    status_board.write_text(
        "# Agent Status\n\n"
        "| agent | phase | state | current work | next handoff | updated |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| bedrock | plan | complete | Write cycle plan. | | {future_local_as_z} |\n"
    )
    result = await engine.run_validations(rules)
    assert result.all_passed is False
    error = result.results[0].error_message or ""
    assert "agent-status timestamp for bedrock plan is in the future" in error
    assert "date -u +%Y-%m-%dT%H:%MZ" in error

    task_board.write_text("# Task Board\n\n")
    status_board.write_text("# Agent Status\n\n")
    plan.write_text(
        plan.read_text()
        + "\nCOORDINATION UPDATE BLOCKED: shared/active/01-task-board.md and "
        "shared/active/02-agent-status.md changed twice while applying the "
        "owner-scoped rows.\n"
    )
    result = await engine.run_validations(rules)
    assert result.all_passed is True
