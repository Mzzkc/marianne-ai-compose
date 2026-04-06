"""Tests for score-level instrument name resolution in build_sheets() and V210.

F-138: Score-level instrument names (defined via instruments: block) are not
resolved by build_sheets() or checked by V210. Users define aliases like
'fast: {profile: gemini-cli}' but per_sheet_instruments, instrument_map, and
movement instruments don't resolve those aliases to profile names.

User story: Dana defines named instruments in her score:
  instruments:
    fast: {profile: gemini-cli}
    careful: {profile: claude-code}
She assigns them to sheets with per_sheet_instruments: {1: fast, 2: careful}.
She expects each sheet to use the correct profile. Instead, the sheet gets
'fast' as the instrument_name — which isn't a real profile.

TDD: All tests written BEFORE implementation. They should FAIL initially.
"""

from pathlib import Path

import pytest

from marianne.core.config.job import InstrumentDef, JobConfig, MovementDef
from marianne.core.sheet import build_sheets


class TestScoreLevelInstrumentResolution:
    """build_sheets() should resolve score-level instrument names to profile names."""

    @pytest.fixture
    def config_with_named_instruments(self, tmp_path: Path) -> JobConfig:
        """Score that defines named instruments and uses them in per_sheet_instruments."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        return JobConfig(
            name="test-named-instruments",
            workspace=str(workspace),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
                "careful": InstrumentDef(profile="claude-code", config={"model": "opus"}),
            },
            sheet={
                "size": 1,
                "total_items": 3,
                "per_sheet_instruments": {
                    1: "fast",
                    2: "careful",
                },
            },
            prompt={"template": "Do work on sheet {{ sheet_num }}"},
        )

    def test_per_sheet_instrument_resolves_to_profile(
        self, config_with_named_instruments: JobConfig
    ):
        """Sheet 1 assigned 'fast' should resolve to 'gemini-cli' profile."""
        sheets = build_sheets(config_with_named_instruments)
        assert sheets[0].instrument_name == "gemini-cli"

    def test_per_sheet_instrument_resolves_careful_to_profile(
        self, config_with_named_instruments: JobConfig
    ):
        """Sheet 2 assigned 'careful' should resolve to 'claude-code' profile."""
        sheets = build_sheets(config_with_named_instruments)
        assert sheets[1].instrument_name == "claude-code"

    def test_unassigned_sheet_uses_score_level_instrument(
        self, config_with_named_instruments: JobConfig
    ):
        """Sheet 3 has no per-sheet assignment, should fall through to score-level instrument."""
        sheets = build_sheets(config_with_named_instruments)
        assert sheets[2].instrument_name == "claude-code"

    def test_per_sheet_instrument_config_merged_from_named_def(
        self, config_with_named_instruments: JobConfig
    ):
        """Sheet 2 assigned 'careful' should get the config from InstrumentDef."""
        sheets = build_sheets(config_with_named_instruments)
        assert sheets[1].instrument_config.get("model") == "opus"


class TestInstrumentMapResolution:
    """instrument_map should also resolve score-level instrument names."""

    @pytest.fixture
    def config_with_instrument_map(self, tmp_path) -> JobConfig:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        return JobConfig(
            name="test-instrument-map",
            workspace=str(workspace),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
            },
            sheet={
                "size": 1,
                "total_items": 4,
                "instrument_map": {
                    "fast": [1, 2],
                    "claude-code": [3, 4],
                },
            },
            prompt={"template": "Do work on sheet {{ sheet_num }}"},
        )

    def test_instrument_map_resolves_named_instrument(
        self, config_with_instrument_map: JobConfig
    ):
        """Sheets 1-2 mapped to 'fast' should resolve to 'gemini-cli'."""
        sheets = build_sheets(config_with_instrument_map)
        assert sheets[0].instrument_name == "gemini-cli"
        assert sheets[1].instrument_name == "gemini-cli"

    def test_instrument_map_profile_name_unchanged(
        self, config_with_instrument_map: JobConfig
    ):
        """Sheets 3-4 mapped to 'claude-code' (already a profile) should stay as-is."""
        sheets = build_sheets(config_with_instrument_map)
        assert sheets[2].instrument_name == "claude-code"
        assert sheets[3].instrument_name == "claude-code"


class TestMovementInstrumentResolution:
    """Movement-level instrument: should resolve score-level names."""

    @pytest.fixture
    def config_with_movement_instruments(self, tmp_path) -> JobConfig:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        return JobConfig(
            name="test-movement-instruments",
            workspace=str(workspace),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
            },
            movements={
                1: MovementDef(name="exploration", instrument="fast"),
                2: MovementDef(name="synthesis"),
            },
            sheet={
                "size": 1,
                "total_items": 2,
            },
            prompt={"template": "Do work on sheet {{ sheet_num }}"},
        )

    def test_movement_instrument_resolves_named_instrument(
        self, config_with_movement_instruments: JobConfig
    ):
        """Movement 1 with instrument='fast' should resolve to 'gemini-cli'."""
        sheets = build_sheets(config_with_movement_instruments)
        assert sheets[0].instrument_name == "gemini-cli"

    def test_movement_without_instrument_uses_score_level(
        self, config_with_movement_instruments: JobConfig
    ):
        """Movement 2 with no instrument should fall through to score-level."""
        sheets = build_sheets(config_with_movement_instruments)
        assert sheets[1].instrument_name == "claude-code"


class TestV210ScoreLevelInstrumentNames:
    """V210 should recognize score-level instrument names as valid."""

    def test_v210_accepts_score_level_name_in_per_sheet(self, tmp_path):
        """V210 should not warn about 'fast' when it's defined in instruments: block."""
        from marianne.validation.checks.config import InstrumentNameCheck

        config = JobConfig(
            name="test-v210",
            workspace=str(tmp_path),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
            },
            sheet={
                "size": 1,
                "total_items": 2,
                "per_sheet_instruments": {1: "fast"},
            },
            prompt={"template": "Do work"},
        )

        raw_yaml = """
name: test-v210
instrument: claude-code
instruments:
  fast:
    profile: gemini-cli
sheet:
  size: 1
  total_items: 2
  per_sheet_instruments:
    1: fast
prompt:
  template: "Do work"
"""
        checker = InstrumentNameCheck()
        issues = checker.check(config, tmp_path / "test.yaml", raw_yaml)
        # 'fast' should NOT be flagged — it's a valid score-level instrument name
        fast_issues = [i for i in issues if "fast" in i.message.lower()]
        assert len(fast_issues) == 0, f"V210 incorrectly flagged score-level name 'fast': {fast_issues}"

    def test_v210_still_warns_on_truly_unknown_name(self, tmp_path):
        """V210 should still warn about names that aren't profiles OR score-level names."""
        from marianne.validation.checks.config import InstrumentNameCheck

        config = JobConfig(
            name="test-v210-unknown",
            workspace=str(tmp_path),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
            },
            sheet={
                "size": 1,
                "total_items": 2,
                "per_sheet_instruments": {1: "nonexistent"},
            },
            prompt={"template": "Do work"},
        )

        raw_yaml = """
name: test-v210-unknown
instrument: claude-code
instruments:
  fast:
    profile: gemini-cli
sheet:
  size: 1
  total_items: 2
  per_sheet_instruments:
    1: nonexistent
prompt:
  template: "Do work"
"""
        checker = InstrumentNameCheck()
        issues = checker.check(config, tmp_path / "test.yaml", raw_yaml)
        unknown_issues = [i for i in issues if "nonexistent" in i.message.lower()]
        assert len(unknown_issues) > 0, "V210 should warn about truly unknown instrument names"

    def test_v210_accepts_score_level_name_in_instrument_map(self, tmp_path):
        """V210 should not warn about score-level names used in instrument_map."""
        from marianne.validation.checks.config import InstrumentNameCheck

        config = JobConfig(
            name="test-v210-map",
            workspace=str(tmp_path),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
            },
            sheet={
                "size": 1,
                "total_items": 2,
                "instrument_map": {"fast": [1, 2]},
            },
            prompt={"template": "Do work"},
        )

        raw_yaml = """
name: test-v210-map
instrument: claude-code
instruments:
  fast:
    profile: gemini-cli
sheet:
  size: 1
  total_items: 2
  instrument_map:
    fast: [1, 2]
prompt:
  template: "Do work"
"""
        checker = InstrumentNameCheck()
        issues = checker.check(config, tmp_path / "test.yaml", raw_yaml)
        fast_issues = [i for i in issues if "fast" in i.message.lower()]
        assert len(fast_issues) == 0, f"V210 incorrectly flagged score-level name in instrument_map: {fast_issues}"

    def test_v210_accepts_score_level_name_in_movement(self, tmp_path):
        """V210 should not warn about score-level names used in movement instrument."""
        from marianne.validation.checks.config import InstrumentNameCheck

        config = JobConfig(
            name="test-v210-movement",
            workspace=str(tmp_path),
            instrument="claude-code",
            instruments={
                "fast": InstrumentDef(profile="gemini-cli"),
            },
            movements={
                1: MovementDef(name="explore", instrument="fast"),
            },
            sheet={
                "size": 1,
                "total_items": 1,
            },
            prompt={"template": "Do work"},
        )

        raw_yaml = """
name: test-v210-movement
instrument: claude-code
instruments:
  fast:
    profile: gemini-cli
movements:
  1:
    name: explore
    instrument: fast
sheet:
  size: 1
  total_items: 1
prompt:
  template: "Do work"
"""
        checker = InstrumentNameCheck()
        issues = checker.check(config, tmp_path / "test.yaml", raw_yaml)
        fast_issues = [i for i in issues if "fast" in i.message.lower()]
        assert len(fast_issues) == 0, f"V210 incorrectly flagged score-level name in movement: {fast_issues}"
