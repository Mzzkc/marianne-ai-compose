"""S3 flow checks consume Forge's real models and expression parser."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config import JobConfig
from marianne.validation.checks.flow import (
    BypassedRecoveryCheck,
    ConstantUntilCheck,
    FlowConcertTargetCheck,
    ForwardGotoFanOutCheck,
    GotoCycleCheck,
    JinjaFilePathCheck,
    JinjaLoopCollisionCheck,
    LegacyConditionCheck,
    MixedFanOutKeyingCheck,
    RunOrphanReapingScopeCheck,
    UndefinedFlowVariableCheck,
    ZeroCostLoopLimitCheck,
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


def _loop_score(tmp_path: Path, name: str, instrument_block: str) -> Path:
    return _file(
        tmp_path,
        f"""name: {name}
workspace: {tmp_path.as_posix()}/ws
{instrument_block}
sheet:
  size: 1
  total_items: 1
  loops:
    1: {{index: i, until: 'file("x").exists', max_iterations: 2, cost_limit_usd: 1}}
prompt: {{template: 'true'}}
""",
    )


def _v228(path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Hermetic: shipped profiles only, so a developer's ~/.marianne layer
    (e.g. a user claude-code defaulting to a $0 route) is not the subject."""
    from functools import lru_cache

    from marianne.instruments import loader

    orig = loader.load_all_profiles
    monkeypatch.setattr(
        loader,
        "load_all_profiles",
        lru_cache(maxsize=1)(
            lambda: orig(organization_dir=tmp_path / "no-org", venue_dir=tmp_path / "no-venue")
        ),
    )
    config = JobConfig.from_yaml(path)
    return [
        issue.message
        for issue in ZeroCostLoopLimitCheck().check(config, path, path.read_text())
    ]


def test_v228_names_the_route_that_reports_no_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw shell route can never report a cost; the warning says which and why."""
    path = _loop_score(tmp_path, "raw", "instrument: cli")
    messages = _v228(path, monkeypatch, tmp_path)
    assert messages == [
        "loop 1: cost_limit_usd cannot stop a route that reports no cost: "
        "`cli` is a raw shell profile"
    ]


def test_v228_is_silent_on_a_priced_cli_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI profile with token paths and a priced model reports cost, so the
    loop cap binds and the check says nothing (GH #453: it used to fire on
    every `kind: cli` profile, telling authors a working cap was inert)."""
    path = _loop_score(
        tmp_path, "priced", "instrument: claude-code\ninstrument_config:\n  model: claude-opus-5-5"
    )
    assert _v228(path, monkeypatch, tmp_path) == []


def test_v228_reads_the_pinned_model_not_the_profile_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The musician prices the model that runs; a sheet pinned to a model the
    profile has no price for is named as such."""
    path = _loop_score(
        tmp_path, "unpriced", "instrument: claude-code\ninstrument_config:\n  model: made-up-model"
    )
    messages = _v228(path, monkeypatch, tmp_path)
    assert messages == [
        "loop 1: cost_limit_usd cannot stop a route that reports no cost: "
        "`claude-code` has no price for pinned model `made-up-model`"
    ]


@pytest.mark.parametrize(
    ("until", "fires"),
    [
        ("""file("{{ workspace }}/done.txt").exists""", True),
        ("""file("{% if x %}a{% endif %}").exists""", True),
        ("""file("{workspace}/done.txt").exists""", False),
        ("""file("done.txt").exists""", False),
    ],
)
def test_v233_refuses_jinja_braces_inside_a_flow_file_path(
    tmp_path: Path, until: str, fires: bool
) -> None:
    path = _file(
        tmp_path,
        f"""name: flow-file-path
sheet:
  size: 1
  total_items: 1
  loops:
    1: {{index: i, until: '{until}', max_iterations: 3}}
prompt: {{template: hello}}
""",
    )
    config = JobConfig.from_yaml(path)
    issues = JinjaFilePathCheck().check(config, path, path.read_text())
    assert [i.check_id for i in issues] == (["V233"] if fires else [])
    if fires:
        assert "never exists" in issues[0].message and "{workspace}" in issues[0].message
