"""#342: instrument_fallbacks must resolve score-local aliases (name + config).

The primary instrument resolves score-local `instruments:` aliases to their
profile name and merges the alias config (build_sheets, core/sheet.py:261-268),
but the fallback chain was copied verbatim — so an aliased fallback (a) crashed
on the registry lookup, and (b) even resolved-by-name would run on the profile's
DEFAULT model instead of the alias's configured model (silent wrong-model).

Fix (per 4-model lab): build_sheets resolves each fallback alias → profile name
and captures its config into the index-aligned `Sheet.instrument_fallback_configs`;
SheetState carries a parallel `fallback_configs`; advance_fallback applies the
fallback's own model (or None for a bare profile — preserving GH#337).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from marianne.core.checkpoint import SheetState
from marianne.core.config.job import InstrumentDef, JobConfig
from marianne.core.sheet import build_sheets


def _config(**overrides: Any) -> JobConfig:
    defaults: dict[str, Any] = {
        "name": "fb-test",
        "workspace": Path("/tmp/fb-test-ws"),
        "sheet": {"size": 1, "total_items": 1, "start_item": 1},
        "prompt": {"template": "Do {{ sheet_num }}."},
    }
    defaults.update(overrides)
    return JobConfig(**defaults)


# ── build_sheets: fallback alias resolution + config capture ───────────────


class TestBuildSheetsFallbackResolution:
    def test_alias_fallback_resolves_to_profile_and_captures_config(self) -> None:
        config = _config(
            instrument="opus",
            instrument_fallbacks=["opencode_glm"],
            instruments={
                "opus": InstrumentDef(
                    profile="claude-code", config={"model": "claude-opus-4-7"}
                ),
                "opencode_glm": InstrumentDef(
                    profile="opencode", config={"model": "zai-coding-plan/glm-5.1"}
                ),
            },
        )
        sheet = build_sheets(config)[0]

        # Primary resolved (existing behavior)
        assert sheet.instrument_name == "claude-code"
        assert sheet.instrument_config["model"] == "claude-opus-4-7"
        # Fallback resolved to PROFILE name (not the raw alias) + config captured
        assert sheet.instrument_fallbacks == ["opencode"]
        assert sheet.instrument_fallback_configs == [{"model": "zai-coding-plan/glm-5.1"}]

    def test_bare_profile_fallback_has_empty_config(self) -> None:
        config = _config(
            instrument="claude-code",
            instrument_fallbacks=["goose"],  # bare registry profile, no alias
        )
        sheet = build_sheets(config)[0]
        assert sheet.instrument_fallbacks == ["goose"]
        assert sheet.instrument_fallback_configs == [{}]

    def test_two_aliases_same_profile_different_models_kept_by_position(self) -> None:
        config = _config(
            instrument="claude-code",
            instrument_fallbacks=["glm_fast", "glm_deep"],
            instruments={
                "glm_fast": InstrumentDef(profile="opencode", config={"model": "glm-4.7"}),
                "glm_deep": InstrumentDef(profile="opencode", config={"model": "glm-5.1"}),
            },
        )
        sheet = build_sheets(config)[0]
        assert sheet.instrument_fallbacks == ["opencode", "opencode"]
        assert sheet.instrument_fallback_configs == [
            {"model": "glm-4.7"},
            {"model": "glm-5.1"},
        ]

    def test_no_alias_leaks_into_resolved_chain(self) -> None:
        # The original crash: an alias name leaked into the chain and the global
        # registry lookup failed. After the fix, no entry is an alias key.
        config = _config(
            instrument="opus",
            instrument_fallbacks=["opencode_glm"],
            instruments={
                "opus": InstrumentDef(profile="claude-code"),
                "opencode_glm": InstrumentDef(
                    profile="opencode", config={"model": "zai-coding-plan/glm-5.1"}
                ),
            },
        )
        sheet = build_sheets(config)[0]
        for fb in sheet.instrument_fallbacks:
            assert fb not in config.instruments, f"unresolved alias '{fb}' leaked"


# ── advance_fallback: apply the fallback's own model (GH#337 preserved) ────


class TestAdvanceFallbackModel:
    def test_alias_fallback_model_applied_on_advance(self) -> None:
        sheet = SheetState(
            sheet_num=1,
            instrument_name="claude-code",
            model="claude-opus-4-7",
            fallback_chain=["opencode"],
            fallback_configs=[{"model": "zai-coding-plan/glm-5.1"}],
        )
        assert sheet.advance_fallback("exhausted") == "opencode"
        assert sheet.instrument_name == "opencode"
        # The alias's model — NOT None, NOT the primary's model.
        assert sheet.model == "zai-coding-plan/glm-5.1"

    def test_bare_profile_fallback_clears_model(self) -> None:
        # GH#337: a fallback with no per-alias config must NOT inherit the
        # primary's model — it clears to None and uses the profile default.
        sheet = SheetState(
            sheet_num=1,
            instrument_name="claude-code",
            model="claude-opus-4-7",
            fallback_chain=["goose"],
            fallback_configs=[{}],
        )
        assert sheet.advance_fallback("exhausted") == "goose"
        assert sheet.model is None

    def test_two_aliases_apply_each_model_by_position(self) -> None:
        sheet = SheetState(
            sheet_num=1,
            instrument_name="claude-code",
            model="claude-opus-4-7",
            fallback_chain=["opencode", "opencode"],
            fallback_configs=[{"model": "glm-4.7"}, {"model": "glm-5.1"}],
        )
        assert sheet.advance_fallback("first") == "opencode"
        assert sheet.model == "glm-4.7"
        assert sheet.advance_fallback("second") == "opencode"
        assert sheet.model == "glm-5.1"

    def test_alias_without_model_clears_model(self) -> None:
        # An alias whose config has no "model" key → profile default (None).
        sheet = SheetState(
            sheet_num=1,
            instrument_name="claude-code",
            model="claude-opus-4-7",
            fallback_chain=["claude-code"],
            fallback_configs=[{"timeout_seconds": 300}],
        )
        sheet.advance_fallback("exhausted")
        assert sheet.model is None

    def test_empty_fallback_configs_is_backward_compatible(self) -> None:
        # Old checkpoint: fallback_chain present, fallback_configs defaulted to [].
        # advance_fallback must not IndexError; falls through to model=None.
        sheet = SheetState(
            sheet_num=1,
            instrument_name="primary",
            model="some-model",
            fallback_chain=["a", "b"],
        )
        assert sheet.fallback_configs == []
        assert sheet.advance_fallback("r") == "a"
        assert sheet.model is None
