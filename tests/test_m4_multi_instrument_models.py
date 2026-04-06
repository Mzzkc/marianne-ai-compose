"""Tests for M4 multi-instrument data models.

TDD tests for:
- Step 38: Per-sheet instrument assignment (sheets.N.instrument)
- Step 39: Score-level instruments: named profiles
- Step 40: sheet.instrument_map for batch assignment
- Step 41: movements: YAML key

These tests define the contract for the multi-instrument data models.
Red first, then green.
"""

from __future__ import annotations

import pytest

from marianne.core.config.job import JobConfig, SheetConfig

# ---------------------------------------------------------------------------
# Step 38: Per-sheet instrument overrides
# ---------------------------------------------------------------------------

class TestPerSheetInstrumentOverrides:
    """Per-sheet instrument assignment via sheets: (plural) in SheetConfig."""

    def test_per_sheet_instruments_default_empty(self) -> None:
        """SheetConfig.per_sheet_instruments defaults to empty dict."""
        sc = SheetConfig(size=1, total_items=3)
        assert sc.per_sheet_instruments == {}

    def test_per_sheet_instruments_accepts_valid_mapping(self) -> None:
        """Can assign instruments to specific sheets."""
        sc = SheetConfig(
            size=1,
            total_items=5,
            per_sheet_instruments={3: "gemini-cli", 5: "codex-cli"},
        )
        assert sc.per_sheet_instruments == {3: "gemini-cli", 5: "codex-cli"}

    def test_per_sheet_instrument_config_default_empty(self) -> None:
        """SheetConfig.per_sheet_instrument_config defaults to empty dict."""
        sc = SheetConfig(size=1, total_items=3)
        assert sc.per_sheet_instrument_config == {}

    def test_per_sheet_instrument_config_accepts_overrides(self) -> None:
        """Can assign instrument config overrides to specific sheets."""
        sc = SheetConfig(
            size=1,
            total_items=5,
            per_sheet_instrument_config={
                3: {"model": "gemini-2.5-flash", "timeout_seconds": 300},
            },
        )
        assert sc.per_sheet_instrument_config[3]["model"] == "gemini-2.5-flash"

    def test_per_sheet_instruments_rejects_zero_sheet_num(self) -> None:
        """Sheet numbers must be >= 1."""
        with pytest.raises(ValueError, match="positive integer"):
            SheetConfig(
                size=1,
                total_items=3,
                per_sheet_instruments={0: "gemini-cli"},
            )

    def test_per_sheet_instruments_rejects_empty_name(self) -> None:
        """Instrument names must not be empty."""
        with pytest.raises(ValueError, match="empty"):
            SheetConfig(
                size=1,
                total_items=3,
                per_sheet_instruments={1: ""},
            )


# ---------------------------------------------------------------------------
# Step 39: Score-level instruments: named profiles
# ---------------------------------------------------------------------------

class TestScoreLevelInstruments:
    """Score-level instruments: dict for named instrument definitions."""

    def test_instruments_default_empty(self) -> None:
        """JobConfig.instruments defaults to empty dict."""
        config = JobConfig(
            name="test",
            sheet=SheetConfig(size=1, total_items=1),
            prompt={"template": "test"},
        )
        assert config.instruments == {}

    def test_instruments_accepts_named_definitions(self) -> None:
        """Can declare named instruments with profile references."""
        from marianne.core.config.job import InstrumentDef

        config = JobConfig(
            name="test",
            sheet=SheetConfig(size=1, total_items=1),
            prompt={"template": "test"},
            instruments={
                "fast-writer": InstrumentDef(
                    profile="gemini-cli",
                    config={"model": "gemini-2.5-flash"},
                ),
                "deep-thinker": InstrumentDef(
                    profile="claude-code",
                    config={"timeout_seconds": 3600},
                ),
            },
        )
        assert config.instruments["fast-writer"].profile == "gemini-cli"
        assert config.instruments["deep-thinker"].config["timeout_seconds"] == 3600

    def test_instrument_def_profile_required(self) -> None:
        """InstrumentDef requires a profile field."""
        from marianne.core.config.job import InstrumentDef

        with pytest.raises(ValueError):
            InstrumentDef(profile="")  # empty profile name

    def test_instrument_def_config_defaults_empty(self) -> None:
        """InstrumentDef.config defaults to empty dict."""
        from marianne.core.config.job import InstrumentDef

        idef = InstrumentDef(profile="gemini-cli")
        assert idef.config == {}

    def test_instruments_from_yaml_string(self) -> None:
        """instruments: key is parsed from YAML."""
        yaml_str = """
name: test
sheet:
  size: 1
  total_items: 1
prompt:
  template: "test"
instruments:
  fast-writer:
    profile: gemini-cli
    config:
      model: gemini-2.5-flash
"""
        config = JobConfig.from_yaml_string(yaml_str)
        assert "fast-writer" in config.instruments
        assert config.instruments["fast-writer"].profile == "gemini-cli"


# ---------------------------------------------------------------------------
# Step 40: sheet.instrument_map for batch assignment
# ---------------------------------------------------------------------------

class TestInstrumentMap:
    """sheet.instrument_map for batch instrument assignment."""

    def test_instrument_map_default_empty(self) -> None:
        """SheetConfig.instrument_map defaults to empty dict."""
        sc = SheetConfig(size=1, total_items=3)
        assert sc.instrument_map == {}

    def test_instrument_map_accepts_valid_mapping(self) -> None:
        """Can assign instruments to groups of sheets."""
        sc = SheetConfig(
            size=1,
            total_items=10,
            instrument_map={
                "gemini-cli": [1, 2, 3, 4, 5],
                "claude-code": [6, 7, 8, 9, 10],
            },
        )
        assert sc.instrument_map["gemini-cli"] == [1, 2, 3, 4, 5]

    def test_instrument_map_rejects_duplicate_sheet_assignment(self) -> None:
        """A sheet cannot be assigned to two instruments."""
        with pytest.raises(ValueError, match="assigned to multiple"):
            SheetConfig(
                size=1,
                total_items=5,
                instrument_map={
                    "gemini-cli": [1, 2, 3],
                    "claude-code": [3, 4, 5],  # sheet 3 is duplicated
                },
            )

    def test_instrument_map_rejects_zero_sheet_num(self) -> None:
        """Sheet numbers in instrument_map must be >= 1."""
        with pytest.raises(ValueError, match="positive integer"):
            SheetConfig(
                size=1,
                total_items=3,
                instrument_map={"gemini-cli": [0, 1, 2]},
            )

    def test_instrument_map_rejects_empty_instrument_name(self) -> None:
        """Instrument names in instrument_map must not be empty."""
        with pytest.raises(ValueError, match="empty"):
            SheetConfig(
                size=1,
                total_items=3,
                instrument_map={"": [1, 2]},
            )


# ---------------------------------------------------------------------------
# Step 41: movements: YAML key
# ---------------------------------------------------------------------------

class TestMovementsKey:
    """movements: YAML key for movement declarations."""

    def test_movements_default_empty(self) -> None:
        """JobConfig.movements defaults to empty dict."""
        config = JobConfig(
            name="test",
            sheet=SheetConfig(size=1, total_items=1),
            prompt={"template": "test"},
        )
        assert config.movements == {}

    def test_movements_accepts_valid_declarations(self) -> None:
        """Can declare movements with name, instrument, and voices."""
        from marianne.core.config.job import MovementDef

        config = JobConfig(
            name="test",
            sheet=SheetConfig(size=1, total_items=3),
            prompt={"template": "test"},
            movements={
                1: MovementDef(name="Planning", instrument="claude-code"),
                2: MovementDef(name="Implementation", voices=3, instrument="gemini-cli"),
                3: MovementDef(name="Review", instrument="claude-code"),
            },
        )
        assert config.movements[1].name == "Planning"
        assert config.movements[2].voices == 3
        assert config.movements[2].instrument == "gemini-cli"

    def test_movement_def_all_optional(self) -> None:
        """All MovementDef fields are optional."""
        from marianne.core.config.job import MovementDef

        m = MovementDef()
        assert m.name is None
        assert m.instrument is None
        assert m.instrument_config == {}
        assert m.voices is None

    def test_movement_def_voices_must_be_positive(self) -> None:
        """MovementDef.voices must be >= 1 when set."""
        from marianne.core.config.job import MovementDef

        with pytest.raises(ValueError):
            MovementDef(voices=0)

    def test_movements_from_yaml_string(self) -> None:
        """movements: key is parsed from YAML."""
        yaml_str = """
name: test
sheet:
  size: 1
  total_items: 3
prompt:
  template: "test"
movements:
  1:
    name: Planning
    instrument: claude-code
  2:
    name: Implementation
    voices: 3
    instrument: gemini-cli
"""
        config = JobConfig.from_yaml_string(yaml_str)
        assert 1 in config.movements
        assert config.movements[1].name == "Planning"
        assert config.movements[2].voices == 3

    def test_movement_def_instrument_config(self) -> None:
        """MovementDef can carry instrument config overrides."""
        from marianne.core.config.job import MovementDef

        m = MovementDef(
            instrument="gemini-cli",
            instrument_config={"model": "gemini-2.5-pro"},
        )
        assert m.instrument_config["model"] == "gemini-2.5-pro"

    def test_movements_rejects_zero_key(self) -> None:
        """Movement numbers must be >= 1."""
        from marianne.core.config.job import MovementDef

        with pytest.raises(ValueError, match="positive integer"):
            JobConfig(
                name="test",
                sheet=SheetConfig(size=1, total_items=1),
                prompt={"template": "test"},
                movements={0: MovementDef(name="bad")},
            )


# ---------------------------------------------------------------------------
# Integration: Resolution chain
# ---------------------------------------------------------------------------

class TestInstrumentResolutionChain:
    """The instrument resolution chain in build_sheets."""

    def test_build_sheets_uses_score_instrument(self) -> None:
        """When instrument: is set, build_sheets uses it."""
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="gemini-cli",
            sheet=SheetConfig(size=1, total_items=2),
            prompt={"template": "test"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "gemini-cli"
        assert sheets[1].instrument_name == "gemini-cli"

    def test_build_sheets_per_sheet_overrides_score_default(self) -> None:
        """sheets.N.instrument overrides score-level instrument."""
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="claude-code",
            sheet=SheetConfig(
                size=1,
                total_items=3,
                per_sheet_instruments={2: "gemini-cli"},
            ),
            prompt={"template": "test"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "claude-code"
        assert sheets[1].instrument_name == "gemini-cli"  # overridden
        assert sheets[2].instrument_name == "claude-code"

    def test_build_sheets_movement_instrument_overrides_score(self) -> None:
        """movements.N.instrument overrides score-level for sheets in that movement."""
        from marianne.core.config.job import MovementDef
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="claude-code",
            sheet=SheetConfig(size=1, total_items=3),
            prompt={"template": "test"},
            movements={2: MovementDef(instrument="gemini-cli")},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "claude-code"  # movement 1 → score default
        assert sheets[1].instrument_name == "gemini-cli"   # movement 2 → movement override
        assert sheets[2].instrument_name == "claude-code"  # movement 3 → score default

    def test_build_sheets_per_sheet_overrides_movement(self) -> None:
        """sheets.N.instrument overrides movement-level instrument."""
        from marianne.core.config.job import MovementDef
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="claude-code",
            sheet=SheetConfig(
                size=1,
                total_items=3,
                per_sheet_instruments={2: "codex-cli"},
            ),
            prompt={"template": "test"},
            movements={2: MovementDef(instrument="gemini-cli")},
        )
        sheets = build_sheets(config)
        # Per-sheet overrides movement
        assert sheets[1].instrument_name == "codex-cli"

    def test_build_sheets_instrument_map_assigns(self) -> None:
        """instrument_map provides batch assignment."""
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="claude-code",
            sheet=SheetConfig(
                size=1,
                total_items=4,
                instrument_map={
                    "gemini-cli": [2, 3],
                },
            ),
            prompt={"template": "test"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "claude-code"
        assert sheets[1].instrument_name == "gemini-cli"
        assert sheets[2].instrument_name == "gemini-cli"
        assert sheets[3].instrument_name == "claude-code"

    def test_build_sheets_per_sheet_overrides_instrument_map(self) -> None:
        """sheets.N.instrument overrides instrument_map."""
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="claude-code",
            sheet=SheetConfig(
                size=1,
                total_items=3,
                per_sheet_instruments={2: "codex-cli"},
                instrument_map={"gemini-cli": [1, 2, 3]},
            ),
            prompt={"template": "test"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "gemini-cli"
        assert sheets[1].instrument_name == "codex-cli"  # per-sheet overrides map
        assert sheets[2].instrument_name == "gemini-cli"

    def test_build_sheets_per_sheet_instrument_config(self) -> None:
        """per_sheet_instrument_config is carried through to Sheet."""
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            instrument="claude-code",
            sheet=SheetConfig(
                size=1,
                total_items=2,
                per_sheet_instrument_config={
                    1: {"model": "gemini-2.5-flash"},
                },
            ),
            prompt={"template": "test"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_config == {"model": "gemini-2.5-flash"}
        assert sheets[1].instrument_config == {}

    def test_build_sheets_falls_back_to_backend_type(self) -> None:
        """When no instrument: is set, falls back to backend.type."""
        from marianne.core.sheet import build_sheets

        config = JobConfig(
            name="test",
            sheet=SheetConfig(size=1, total_items=1),
            prompt={"template": "test"},
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_name == "claude_cli"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """Existing scores with backend: still work unchanged."""

    def test_existing_score_no_new_fields(self) -> None:
        """A score without instruments/movements/per_sheet works as before."""
        yaml_str = """
name: test
workspace: /tmp/test
backend:
  type: claude_cli
sheet:
  size: 1
  total_items: 3
prompt:
  template: "test"
"""
        config = JobConfig.from_yaml_string(yaml_str)
        assert config.instruments == {}
        assert config.movements == {}
        assert config.sheet.per_sheet_instruments == {}
        assert config.sheet.instrument_map == {}

    def test_instrument_and_instruments_coexist(self) -> None:
        """instrument: (score default) coexists with instruments: (named defs)."""
        yaml_str = """
name: test
instrument: claude-code
instruments:
  fast-writer:
    profile: gemini-cli
    config:
      model: gemini-2.5-flash
sheet:
  size: 1
  total_items: 1
prompt:
  template: "test"
"""
        config = JobConfig.from_yaml_string(yaml_str)
        assert config.instrument == "claude-code"
        assert "fast-writer" in config.instruments
