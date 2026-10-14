"""S3 flow checks consume Forge's real models and expression parser."""

import json
from pathlib import Path

from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.validation.checks.flow import (
    BypassedRecoveryCheck,
    ConstantUntilCheck,
    FlowConcertTargetCheck,
    ForwardGotoFanOutCheck,
    GotoCycleCheck,
    JinjaLoopCollisionCheck,
    LegacyConditionCheck,
    MixedFanOutKeyingCheck,
    RunOrphanReapingScopeCheck,
    UndefinedFlowVariableCheck,
)


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "score.yaml"
    path.write_text(text)
    return path


def test_flow_load_error_is_coded_and_exit_one(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """name: flow-invalid
sheet:
  size: 1
  total_items: 2
  triggers: {1: {on_fail: [{goto: 99}]}}
prompt: {template: hello}
""",
    )
    result = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["issues"][0]["check_id"] == "V314"
    assert data["summary"]["triggers"] == ["1"]


def test_run_orphan_reaping_info_only_on_unsupported_host(
    tmp_path: Path, monkeypatch,
) -> None:
    from marianne.validation.checks import flow

    path = _file(
        tmp_path,
        "name: run-scope\nsheet:\n  size: 1\n  total_items: 2\n"
        "  triggers: {1: {on_success: [{run: 'echo done'}]}}\n"
        "prompt: {template: hello}\n",
    )
    config = JobConfig.from_yaml(path)
    monkeypatch.setattr(flow, "subreaper_available", lambda: False)
    issues = RunOrphanReapingScopeCheck().check(config, path, path.read_text())
    assert len(issues) == 1
    assert issues[0].check_id == "V326"
    rendered = CliRunner().invoke(app, ["validate", str(path), "--json"])
    assert rendered.exit_code == 0
    assert "V326" in {item["check_id"] for item in json.loads(rendered.stdout)["issues"]}
    monkeypatch.setattr(flow, "subreaper_available", lambda: True)
    assert RunOrphanReapingScopeCheck().check(config, path, path.read_text()) == []
    plain = _file(
        tmp_path,
        "name: no-run\nsheet: {size: 1, total_items: 2}\n"
        "prompt: {template: hello}\n",
    )
    assert RunOrphanReapingScopeCheck().check(
        JobConfig.from_yaml(plain), plain, plain.read_text()
    ) == []


def test_until_and_trigger_advisories(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """name: flow-advisory
retry: {max_retries: 3}
sheet:
  size: 1
  total_items: 2
  loops:
    1: {index: i, until: 'var.missing == 3', max_iterations: 3}
  triggers:
    1: {on_fail: [{goto: 2}]}
    2: {on_success: [{goto: 1}, {concert: missing-child.yaml}]}
prompt: {template: '{{ loop.i }}'}
validations:
  - {type: file_exists, path: '{workspace}/out', condition: 'sheet_num == 1 or sheet_num == 2'}
""",
    )
    config = JobConfig.from_yaml(path)
    checks = (
        UndefinedFlowVariableCheck,
        BypassedRecoveryCheck,
        ConstantUntilCheck,
        JinjaLoopCollisionCheck,
        FlowConcertTargetCheck,
        GotoCycleCheck,
        LegacyConditionCheck,
    )
    codes = {
        issue.check_id
        for check in checks
        for issue in check().check(config, path, path.read_text())
    }
    assert codes == {"V220", "V224", "V226", "V227", "V229", "V315", "V232"}


def test_fan_out_keying_and_forward_goto_warning(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        """name: flow-fan
sheet:
  size: 1
  total_items: 2
  fan_out: {1: 2}
  prompt_extensions: {1: [note]}
  triggers: {1: {on_success: [{goto: 2}]}}
prompt: {template: hello}
""",
    )
    config = JobConfig.from_yaml(path)
    assert MixedFanOutKeyingCheck().check(config, path, path.read_text())[0].check_id == "V225"
    assert ForwardGotoFanOutCheck().check(config, path, path.read_text())[0].check_id == "V231"
