"""Tests for per-sheet instrument fallback chains.

TDD: Tests written BEFORE implementation.

Covers:
1. Config model: instrument_fallbacks on JobConfig, MovementDef, SheetConfig
2. Sheet entity: instrument_fallbacks resolved in build_sheets()
3. Checkpoint: instrument_fallback_history on SheetState
4. Validation: V211 warns on unknown fallback instrument names

Design spec: docs/plans/2026-04-04-instrument-fallbacks-spec.md
"""

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from marianne.core.checkpoint import SheetState
from marianne.core.config import JobConfig
from marianne.core.config.job import MovementDef
from marianne.core.sheet import build_sheets
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.config import InstrumentFallbackCheck

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_yaml() -> str:
    """Minimal valid score YAML for testing."""
    return dedent("""
        name: fallback-test
        instrument: claude-code
        sheet:
          size: 1
          total_items: 5
        prompt:
          template: "Do work on sheet {{ sheet_num }}"
    """).strip()


@pytest.fixture
def _mock_profiles():
    """Mock load_all_profiles to return a fixed set of instruments."""
    profiles = {
        "claude-code": None,
        "gemini-cli": None,
        "codex-cli": None,
        "ollama": None,
    }
    with patch(
        "marianne.instruments.loader.load_all_profiles",
        return_value=profiles,
    ):
        yield profiles


@pytest.fixture
def fallback_check() -> InstrumentFallbackCheck:
    return InstrumentFallbackCheck()


# ===========================================================================
# PART 1: Config Model — instrument_fallbacks field
# ===========================================================================


class TestJobConfigFallbacks:
    """instrument_fallbacks on JobConfig (score-level default)."""

    def test_default_is_empty_list(self) -> None:
        """Existing scores without fallbacks work unchanged."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: no-fallbacks
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.instrument_fallbacks == []

    def test_score_level_fallbacks_parsed(self) -> None:
        """Score-level instrument_fallbacks parses correctly."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: with-fallbacks
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
              - ollama
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.instrument_fallbacks == ["gemini-cli", "ollama"]

    def test_single_fallback(self) -> None:
        """A single fallback entry works."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: one-fallback
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.instrument_fallbacks == ["gemini-cli"]

    def test_empty_fallback_list(self) -> None:
        """Explicitly empty fallback list parses correctly."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: empty-fallbacks
            instrument: claude-code
            instrument_fallbacks: []
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.instrument_fallbacks == []


class TestMovementDefFallbacks:
    """instrument_fallbacks on MovementDef."""

    def test_default_is_empty_list(self) -> None:
        mov = MovementDef()
        assert mov.instrument_fallbacks == []

    def test_movement_level_fallbacks_parsed(self) -> None:
        config = JobConfig.from_yaml_string(
            dedent("""
            name: movement-fallbacks
            instrument: claude-code
            movements:
              1:
                name: Research
                instrument: gemini-cli
                instrument_fallbacks:
                  - claude-code
                  - ollama
              2:
                name: Implementation
                instrument: claude-code
            sheet:
              size: 1
              total_items: 4
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.movements[1].instrument_fallbacks == ["claude-code", "ollama"]
        assert config.movements[2].instrument_fallbacks == []


class TestSheetConfigFallbacks:
    """instrument_fallbacks on SheetConfig (per-sheet level)."""

    def test_per_sheet_fallbacks_parsed(self) -> None:
        config = JobConfig.from_yaml_string(
            dedent("""
            name: per-sheet-fallbacks
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              per_sheet_fallbacks:
                2:
                  - gemini-cli
                  - ollama
                3:
                  - codex-cli
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.sheet.per_sheet_fallbacks == {
            2: ["gemini-cli", "ollama"],
            3: ["codex-cli"],
        }

    def test_default_is_empty_dict(self) -> None:
        config = JobConfig.from_yaml_string(
            dedent("""
            name: no-per-sheet
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.sheet.per_sheet_fallbacks == {}

    def test_per_sheet_fallback_empty_list(self) -> None:
        """Per-sheet empty list means 'no fallbacks for this sheet'."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: empty-per-sheet
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
            sheet:
              size: 1
              total_items: 2
              per_sheet_fallbacks:
                1: []
            prompt:
              template: "Work"
        """).strip()
        )
        assert config.sheet.per_sheet_fallbacks[1] == []

    def test_per_sheet_fallback_invalid_key_rejected(self) -> None:
        """Sheet number must be positive integer."""
        with pytest.raises(ValidationError):
            JobConfig.from_yaml_string(
                dedent("""
                name: invalid-key
                instrument: claude-code
                sheet:
                  size: 1
                  total_items: 3
                  per_sheet_fallbacks:
                    0:
                      - gemini-cli
                prompt:
                  template: "Work"
            """).strip()
            )


# ===========================================================================
# PART 2: Sheet Entity — fallback resolution in build_sheets()
# ===========================================================================


class TestBuildSheetsFallbackResolution:
    """build_sheets() resolves instrument_fallbacks per spec."""

    def test_score_level_fallbacks_inherited(self) -> None:
        """All sheets inherit score-level fallbacks."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: inherit-fallbacks
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
              - ollama
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        sheets = build_sheets(config)
        for s in sheets:
            assert s.instrument_fallbacks == ["gemini-cli", "ollama"]

    def test_movement_level_overrides_score_level(self) -> None:
        """Movement-level fallbacks replace score-level for sheets in that movement."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: movement-override
            instrument: claude-code
            instrument_fallbacks:
              - ollama
            movements:
              2:
                name: Research
                instrument_fallbacks:
                  - gemini-cli
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        )
        sheets = build_sheets(config)
        # Sheet 1 is movement 1 — inherits score-level
        assert sheets[0].instrument_fallbacks == ["ollama"]
        # Sheet 2 is movement 2 — movement override replaces
        assert sheets[1].instrument_fallbacks == ["gemini-cli"]
        # Sheet 3 is movement 3 — inherits score-level
        assert sheets[2].instrument_fallbacks == ["ollama"]

    def test_per_sheet_overrides_everything(self) -> None:
        """Per-sheet fallbacks replace both score and movement level."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: per-sheet-override
            instrument: claude-code
            instrument_fallbacks:
              - ollama
            movements:
              1:
                instrument_fallbacks:
                  - gemini-cli
            sheet:
              size: 1
              total_items: 3
              per_sheet_fallbacks:
                1:
                  - codex-cli
            prompt:
              template: "Work"
        """).strip()
        )
        sheets = build_sheets(config)
        # Sheet 1 (movement 1) — per-sheet overrides movement overrides score
        assert sheets[0].instrument_fallbacks == ["codex-cli"]
        # Sheet 2 (movement 2) — no movement or per-sheet override → score-level
        assert sheets[1].instrument_fallbacks == ["ollama"]
        # Sheet 3 (movement 3) — no movement or per-sheet override → score-level
        assert sheets[2].instrument_fallbacks == ["ollama"]

    def test_per_sheet_empty_list_means_no_fallbacks(self) -> None:
        """Per-sheet empty list explicitly disables fallbacks for that sheet."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: disable-fallbacks
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
            sheet:
              size: 1
              total_items: 2
              per_sheet_fallbacks:
                1: []
            prompt:
              template: "Work"
        """).strip()
        )
        sheets = build_sheets(config)
        assert sheets[0].instrument_fallbacks == []
        assert sheets[1].instrument_fallbacks == ["gemini-cli"]

    def test_no_fallbacks_at_any_level(self) -> None:
        """Default: no fallbacks produces empty list."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: no-fallbacks
            instrument: claude-code
            sheet:
              size: 1
              total_items: 2
            prompt:
              template: "Work"
        """).strip()
        )
        sheets = build_sheets(config)
        for s in sheets:
            assert s.instrument_fallbacks == []

    def test_fan_out_inherits_fallbacks(self) -> None:
        """Fan-out instances inherit fallbacks from movement/score level."""
        config = JobConfig.from_yaml_string(
            dedent("""
            name: fanout-fallbacks
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
            sheet:
              size: 1
              total_items: 3
              fan_out:
                2: 3
            prompt:
              template: "Work"
        """).strip()
        )
        sheets = build_sheets(config)
        # All sheets (including fan-out instances) inherit score-level
        for s in sheets:
            assert s.instrument_fallbacks == ["gemini-cli"]


# ===========================================================================
# PART 3: Checkpoint — instrument_fallback_history on SheetState
# ===========================================================================


class TestSheetStateFallbackHistory:
    """instrument_fallback_history field on SheetState."""

    def test_default_is_empty_list(self) -> None:
        state = SheetState(sheet_num=1)
        assert state.instrument_fallback_history == []

    def test_append_fallback_event(self) -> None:
        state = SheetState(sheet_num=1)
        state.instrument_fallback_history.append(
            {
                "from": "claude-code",
                "to": "gemini-cli",
                "reason": "rate_limit_exhausted",
                "timestamp": "2026-04-05T10:00:00Z",
            }
        )
        assert len(state.instrument_fallback_history) == 1
        assert state.instrument_fallback_history[0]["from"] == "claude-code"
        assert state.instrument_fallback_history[0]["to"] == "gemini-cli"
        assert state.instrument_fallback_history[0]["reason"] == "rate_limit_exhausted"

    def test_multiple_fallback_events(self) -> None:
        state = SheetState(sheet_num=1)
        state.instrument_fallback_history.extend(
            [
                {
                    "from": "claude-code",
                    "to": "gemini-cli",
                    "reason": "unavailable",
                    "timestamp": "2026-04-05T10:00:00Z",
                },
                {
                    "from": "gemini-cli",
                    "to": "ollama",
                    "reason": "rate_limit_exhausted",
                    "timestamp": "2026-04-05T10:05:00Z",
                },
            ]
        )
        assert len(state.instrument_fallback_history) == 2

    def test_serialization_roundtrip(self) -> None:
        """Fallback history survives JSON serialization (resume support)."""
        state = SheetState(sheet_num=1)
        state.instrument_fallback_history.append(
            {
                "from": "claude-code",
                "to": "gemini-cli",
                "reason": "unavailable",
                "timestamp": "2026-04-05T10:00:00Z",
            }
        )
        dumped = state.model_dump()
        restored = SheetState.model_validate(dumped)
        assert restored.instrument_fallback_history == state.instrument_fallback_history


# ===========================================================================
# PART 4: Validation — V211 InstrumentFallbackCheck
# ===========================================================================


class TestInstrumentFallbackCheckProperties:
    """V211 check metadata."""

    def test_check_id(self, fallback_check: InstrumentFallbackCheck) -> None:
        assert fallback_check.check_id == "V211"

    def test_severity_is_warning(self, fallback_check: InstrumentFallbackCheck) -> None:
        assert fallback_check.severity == ValidationSeverity.WARNING

    def test_description_mentions_fallback(self, fallback_check: InstrumentFallbackCheck) -> None:
        assert "fallback" in fallback_check.description.lower()


class TestInstrumentFallbackCheckKnown:
    """Known fallback instruments produce zero issues."""

    def test_known_fallbacks_no_issues(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: valid-fallbacks
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
              - ollama
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 0

    def test_no_fallbacks_no_issues(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: no-fallbacks
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 0


class TestInstrumentFallbackCheckUnknown:
    """Unknown fallback instruments produce WARNING issues."""

    def test_unknown_score_level_fallback(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: unknown-fallback
            instrument: claude-code
            instrument_fallbacks:
              - gemini-cli
              - nonexistent-instrument
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert issues[0].check_id == "V211"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "nonexistent-instrument" in issues[0].message

    def test_unknown_movement_level_fallback(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: movement-unknown
            instrument: claude-code
            movements:
              1:
                instrument_fallbacks:
                  - fake-instrument
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert "fake-instrument" in issues[0].message

    def test_unknown_per_sheet_fallback(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: per-sheet-unknown
            instrument: claude-code
            sheet:
              size: 1
              total_items: 3
              per_sheet_fallbacks:
                2:
                  - bad-instrument
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert "bad-instrument" in issues[0].message

    def test_score_alias_is_valid_fallback(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        """Score-level instrument aliases are valid fallback targets."""
        yaml_text = dedent("""
            name: alias-fallback
            instrument: claude-code
            instruments:
              fast-research:
                profile: gemini-cli
                config:
                  model: gemini-2.5-flash
            instrument_fallbacks:
              - fast-research
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 0

    def test_multiple_unknown_produce_separate_issues(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: multi-unknown
            instrument: claude-code
            instrument_fallbacks:
              - fake-one
              - fake-two
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 2

    def test_suggestion_lists_available_instruments(
        self, fallback_check: InstrumentFallbackCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: suggestion-test
            instrument: claude-code
            instrument_fallbacks:
              - nonexistent
            sheet:
              size: 1
              total_items: 3
            prompt:
              template: "Work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)
        issues = fallback_check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert issues[0].suggestion is not None
        assert "claude-code" in issues[0].suggestion
