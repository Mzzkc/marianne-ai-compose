"""The sheet timeout and route knobs are declared at every override scope."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from marianne.core.config import JobConfig
from marianne.core.config.instrument_settings import InstrumentSettings
from marianne.core.sheet import build_sheets
from marianne.instruments.loader import capture_resolved_instrument_route


@given(
    timeout=st.floats(min_value=0.001, max_value=100_000, allow_nan=False),
    model=st.text(min_size=1, max_size=30),
)
def test_instrument_settings_mapping_roundtrip(timeout: float, model: str) -> None:
    settings = InstrumentSettings(timeout_seconds=timeout, model=model)
    assert dict(settings) == {"timeout_seconds": timeout, "model": model}
    assert InstrumentSettings.model_validate(settings.model_dump()) == settings


def test_direct_construction_retains_legacy_extra_settings() -> None:
    settings = InstrumentSettings.model_validate({"custom_flag": True})
    assert dict(settings) == {"custom_flag": True}
    assert settings.model_dump() == {"custom_flag": True}


@pytest.mark.parametrize(
    ("scope", "body"),
    [
        ("score", "instrument_config:\n  timeout_secnds: 99\n"),
        ("movement", "movements:\n  1:\n    instrument_config:\n      timeout_secnds: 99\n"),
        (
            "sheet",
            "sheet:\n  size: 1\n  total_items: 1\n"
            "  per_sheet_instrument_config:\n    1:\n      timeout_secnds: 99\n",
        ),
        (
            "alias",
            "instruments:\n  named:\n    profile: cli\n"
            "    config:\n      timeout_secnds: 99\n",
        ),
    ],
)
def test_timeout_typo_is_reported_at_each_scope(
    tmp_path: Path, scope: str, body: str
) -> None:
    path = tmp_path / "score.yaml"
    path.write_text(
        "name: typed-settings\nworkspace: ./output\n"
        "sheet: {size: 1, total_items: 1}\n"
        "prompt: {template: hi}\n" + body,
        encoding="utf-8",
    )
    config = JobConfig.from_yaml(path)
    assert [field.key for field in config.unknown_fields] == ["timeout_secnds"], scope
    assert "timeout_seconds" in config.unknown_fields[0].candidates


def test_provider_survives_yaml_load_and_scope_merge(tmp_path: Path) -> None:
    profile_dir = tmp_path / ".marianne/instruments"
    profile_dir.mkdir(parents=True)
    (profile_dir / "fiction.yaml").write_text(
        "name: fiction-cli\ndisplay_name: Fiction CLI\nkind: cli\n"
        "default_model: fiction-model\ncli:\n  command:\n"
        "    executable: /bin/true\n  output:\n    format: text\n",
        encoding="utf-8",
    )
    path = tmp_path / "guarded.yaml"
    path.write_text(
        "name: guarded-settings\nworkspace: ./output\n"
        "instrument: named\n"
        "instrument_config: {timeout_seconds: 100, provider: ScoreProvider}\n"
        "instruments:\n  named:\n    profile: fiction-cli\n"
        "    config: {timeout_seconds: 300, provider: ReviewedProvider}\n"
        "movements:\n  1:\n    instrument_config: {timeout_seconds: 200}\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "  per_sheet_instrument_config: {1: {timeout_seconds: 400}}\n"
        "prompt: {template: hi}\n",
        encoding="utf-8",
    )
    config = JobConfig.from_yaml(path)
    sheet = build_sheets(config)[0]
    assert config.unknown_fields == ()
    assert sheet.instrument_config["provider"] == "ReviewedProvider"
    assert sheet.timeout_seconds == 400
    assert config.instrument_config.__class__.__name__ == "InstrumentSettings"
    binding = capture_resolved_instrument_route(
        sheet.instrument_name, sheet.instrument_config,
        now=datetime.now(UTC), arm="remote",
        organization_dir=tmp_path / "no-organization", venue_dir=profile_dir,
    )
    assert binding.effective_provider == "ReviewedProvider"


def test_runtime_instrument_key_census_is_declared() -> None:
    """A new runtime read in the known consumer files needs a schema field."""
    root = Path(__file__).resolve().parents[1] / "src/marianne"
    readers = {
        "core/sheet.py": {"instrument_config", "fb_config"},
        "instruments/loader.py": {"instrument_config"},
        "daemon/baton/adapter.py": {"_icfg", "_icfg_rf", "knobs"},
    }
    observed: set[str] = set()
    for relative, local_names in readers.items():
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get" or not node.args:
                continue
            receiver = node.func.value
            is_settings = (
                isinstance(receiver, ast.Name) and receiver.id in local_names
            ) or (
                isinstance(receiver, ast.Attribute) and receiver.attr == "instrument_config"
            )
            if is_settings and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    observed.add(node.args[0].value)
    assert observed <= set(InstrumentSettings.model_fields), observed
    assert set(InstrumentSettings.model_fields) == {
        "timeout_seconds", "model", "provider", "interactive",
        "interactive_max_nudges", "interactive_nudge_message", "max_tokens",
        "temperature", "response_format",
    }
