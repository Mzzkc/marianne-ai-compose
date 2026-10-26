"""Telemetry remains opt-in, including when a score supplies its settings."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from marianne.core.config import JobConfig


def test_default_disabled_and_explicit_config() -> None:
    from marianne.telemetry import TelemetryConfig

    defaults = TelemetryConfig()
    assert defaults.enabled is False
    assert defaults.submit_mode == "local_only"
    assert defaults.endpoint is None
    raw = yaml.safe_load(Path("tests/fixtures/test-daemon-job.yaml").read_text())
    assert JobConfig.model_validate(raw).telemetry == defaults
    raw["telemetry"] = {"enabled": True, "spool_path": "local.jsonl"}
    configured = JobConfig.model_validate(raw).telemetry
    assert configured.enabled is True
    assert configured.spool_path == Path("local.jsonl")


@pytest.mark.parametrize("settings", [{"enable": True}, {"submit_mode": "automatic"}])
def test_invalid_settings_rejected(settings: dict) -> None:
    from marianne.telemetry import TelemetryConfig

    with pytest.raises(ValidationError):
        TelemetryConfig.model_validate(settings)


def test_unknown_nested_setting_rejected() -> None:
    raw = yaml.safe_load(Path("tests/fixtures/test-daemon-job.yaml").read_text())
    raw["telemetry"] = {"consent": "granted"}
    with pytest.raises(ValidationError):
        JobConfig.model_validate(raw)
