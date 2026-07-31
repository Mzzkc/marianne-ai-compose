"""Contracts for Marianne's authoritative instrument catalog."""

from pathlib import Path

import pytest
import yaml

CATALOG_PATH = (
    Path(__file__).parent.parent
    / "plugins"
    / "marianne"
    / "docs"
    / "ref"
    / "instrument-catalog.yaml"
)


def test_current_catalog_places_gpt_5_6_under_codex() -> None:
    """GPT-5.6 is a Codex model family, never a separate instrument."""
    if not CATALOG_PATH.is_file():
        pytest.skip("instrument catalog is an optional plugin submodule asset")

    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    codex = catalog["instruments"]["codex-cli"]
    expected = {
        "gpt-5.6-sol",
        "gpt-5.6-sol[1m]",
        "gpt-5.6-terra",
        "gpt-5.6-terra[1m]",
        "gpt-5.6-luna",
        "gpt-5.6-luna[1m]",
    }
    assert expected <= set(codex["runs_models"])
    assert "gpt-5.6" not in catalog["instruments"]
    assert "thinking" in codex["capabilities"]
