"""MN-007: degraded validation must retain a warning and the failure detail."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import structlog

from marianne.core.config import JobConfig
from marianne.instruments import classes, loader
from marianne.validation.base import ValidationCheck
from marianne.validation.checks import capability_classes, techniques
from marianne.validation.checks.cli import CliRawPromptBashCheck
from marianne.validation.checks.flow import ZeroCostLoopLimitCheck
from marianne.validation.output_contract import class_summary


@pytest.fixture(autouse=True)
def capture_warnings(caplog: pytest.LogCaptureFixture) -> None:
    structlog.configure(
        processors=[
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    caplog.set_level(logging.WARNING)


@pytest.fixture
def config(tmp_path: Path) -> JobConfig:
    return JobConfig.model_validate({
        "name": "skip-diagnostics",
        "workspace": str(tmp_path),
        "instrument": "cli",
        "sheet": {
            "size": 1,
            "total_items": 1,
            "loops": {"1": {"count": 2, "cost_limit_usd": 1, "index": "i"}},
        },
        "prompt": {"template": "echo ok"},
        "techniques": {"tools": {"kind": "mcp", "phases": ["work"]}},
    })


def _fail(*args: object, **kwargs: object) -> None:
    raise RuntimeError("broken validation dependency")


def _assert_warning(caplog: pytest.LogCaptureFixture, check_id: str) -> None:
    warnings = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "validation.profiles_unavailable_skip"
    assert warnings[0]["check"] == check_id
    assert "RuntimeError: broken validation dependency" in warnings[0]["exception"]


@pytest.mark.parametrize("check", [CliRawPromptBashCheck(), ZeroCostLoopLimitCheck()])
def test_profile_failure_warns_before_empty_result(
    check: ValidationCheck, config: JobConfig, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(loader, "load_all_profiles", _fail)
    assert check.check(config, tmp_path / "score.yaml", "") == []
    _assert_warning(caplog, check.check_id)


def test_builtin_profile_failure_warns_before_skipping_mcp_check(
    config: JobConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(techniques, "_builtin_profiles", _fail)
    assert techniques.TechniqueMcpInstrumentCheck().check(config, tmp_path / "score.yaml", "") == []
    _assert_warning(caplog, "V302")


@pytest.mark.parametrize("dependency", ["profiles", "classes"])
@pytest.mark.parametrize("check", [
    capability_classes.ClassNameResolutionCheck(),
    capability_classes.ClassChainAvailabilityCheck(),
    capability_classes.ClassAliasProfileCheck(),
    capability_classes.AliasShadowsClassCheck(),
    capability_classes.ClassModelConfigCheck(),
    capability_classes.ClassesFileValidCheck(),
    capability_classes.ClassChainSummaryCheck(),
])
def test_class_checks_warn_with_their_id_when_context_is_unavailable(
    dependency: str, check: ValidationCheck, config: JobConfig, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(loader, "load_all_profiles", _fail if dependency == "profiles" else dict)
    monkeypatch.setattr(classes, "load_class_map", _fail)
    assert check.check(config, tmp_path / "score.yaml", "") == []
    _assert_warning(caplog, check.check_id)


@pytest.mark.parametrize("dependency", ["profiles", "classes"])
def test_summary_failure_warns_and_preserves_fallback_shape(
    dependency: str, config: JobConfig, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(loader, "load_all_profiles", _fail if dependency == "profiles" else dict)
    monkeypatch.setattr(classes, "load_class_map", _fail)
    assert class_summary(config) == {"layers": [], "used": [], "per_sheet": [], "failures": []}
    _assert_warning(caplog, "class_summary")


def test_cli_class_map_failure_keeps_checking_raw_shell_prompts(
    config: JobConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    profiles = loader.InstrumentProfileLoader.load_directory(
        Path(loader.__file__).parent / "builtins"
    )
    monkeypatch.setattr(loader, "load_all_profiles", lambda: profiles)
    monkeypatch.setattr(classes, "load_class_map", _fail)
    config.prompt.template = "```bash\necho ok\n```"
    issues = CliRawPromptBashCheck().check(config, tmp_path / "score.yaml", "")
    assert len(issues) == 1
    assert issues[0].check_id == "V307"
    assert "markdown code fence" in issues[0].message
    _assert_warning(caplog, "V307")


@pytest.fixture
def class_context(tmp_path: Path) -> capability_classes._ClassContext:
    class_map = classes.load_class_map(
        user_path=tmp_path / "user.yaml", venue_path=tmp_path / "venue.yaml",
    )
    return capability_classes._ClassContext(profiles={}, class_map=class_map)


@pytest.mark.parametrize("dependency", ["snapshot", "sheets"])
def test_response_format_collection_warns_when_resolution_fails(
    dependency: str, config: JobConfig, class_context: capability_classes._ClassContext,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    if dependency == "snapshot":
        monkeypatch.setattr(classes.ClassMap, "snapshot", _fail)
    else:
        monkeypatch.setattr("marianne.core.sheet.build_sheets", _fail)
    check = capability_classes.ClassChainAvailabilityCheck()
    assert check._sheet_rf_requests(config, class_context, {"strong"}) == {}
    _assert_warning(caplog, check.check_id)


def test_model_check_warns_when_snapshot_fails(
    config: JobConfig, tmp_path: Path, class_context: capability_classes._ClassContext,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    config.instrument = "strong"
    monkeypatch.setattr(capability_classes, "_load_context", lambda *args: class_context)
    monkeypatch.setattr(classes.ClassMap, "snapshot", _fail)
    check = capability_classes.ClassModelConfigCheck()
    assert check.check(config, tmp_path / "score.yaml", "") == []
    _assert_warning(caplog, "V323")
