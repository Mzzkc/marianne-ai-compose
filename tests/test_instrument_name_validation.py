"""Tests for V210 InstrumentNameCheck — validates instrument names against registry.

TDD: Tests written first, implementation follows.
Covers:
- Known instrument names pass (no issues)
- Unknown instrument names produce WARNING
- Legacy backend: syntax (no instrument:) skips check
- Per-sheet instrument names validated
- instrument_map entries validated
- Movement-level instrument names validated
- Suggestion includes available instruments
- Multiple unknown instruments produce separate warnings
"""

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from marianne.core.config import JobConfig
from marianne.validation.base import ValidationSeverity
from marianne.validation.checks.config import InstrumentNameCheck


@pytest.fixture
def _mock_profiles():
    """Mock load_all_profiles to return a fixed set of instruments."""
    profiles = {
        "claude-code": None,  # Value doesn't matter for name check
        "gemini-cli": None,
        "codex-cli": None,
        "ollama": None,
        "anthropic_api": None,
    }
    with patch(
        "marianne.instruments.loader.load_all_profiles",
        return_value=profiles,
    ):
        yield profiles


@pytest.fixture
def check() -> InstrumentNameCheck:
    return InstrumentNameCheck()


class TestInstrumentNameCheckProperties:
    """Test check metadata."""

    def test_check_id(self, check: InstrumentNameCheck) -> None:
        assert check.check_id == "V210"

    def test_severity_is_warning(self, check: InstrumentNameCheck) -> None:
        assert check.severity == ValidationSeverity.WARNING

    def test_description(self, check: InstrumentNameCheck) -> None:
        assert "instrument" in check.description.lower()


class TestInstrumentNameCheckKnownNames:
    """Known instrument names should produce zero issues."""

    def test_known_instrument_no_issues(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: claude-code
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 0

    def test_no_instrument_field_skips_check(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        """Scores using legacy backend: syntax should not trigger V210."""
        yaml_text = dedent("""
            name: test-job
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 0


class TestInstrumentNameCheckUnknownNames:
    """Unknown instrument names should produce WARNING."""

    def test_unknown_instrument_produces_warning(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: nonexistent-instrument-12345
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert issues[0].check_id == "V210"
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "nonexistent-instrument-12345" in issues[0].message

    def test_typo_instrument_warns(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        """Common typo: clause-code instead of claude-code."""
        yaml_text = dedent("""
            name: test-job
            instrument: clause-code
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert "clause-code" in issues[0].message

    def test_suggestion_lists_available_instruments(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: unknown-tool
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert issues[0].suggestion is not None
        assert "claude-code" in issues[0].suggestion


class TestInstrumentNameCheckPerSheet:
    """Per-sheet instrument names should also be validated."""

    def test_per_sheet_instrument_unknown(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: claude-code
            sheet:
              size: 10
              total_items: 3
              per_sheet_instruments:
                2: bad-instrument
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert "bad-instrument" in issues[0].message
        assert "sheet 2" in issues[0].message.lower()

    def test_per_sheet_instrument_known(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: claude-code
            sheet:
              size: 10
              total_items: 3
              per_sheet_instruments:
                2: gemini-cli
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 0

    def test_instrument_map_unknown(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: claude-code
            sheet:
              size: 10
              total_items: 3
              instrument_map:
                fake-llm: [2, 3]
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert "fake-llm" in issues[0].message


class TestInstrumentNameCheckMovements:
    """Movement-level instrument names should be validated."""

    def test_movement_instrument_unknown(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: claude-code
            sheet:
              size: 10
              total_items: 3
            movements:
              2:
                instrument: bad-model-thing
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 1
        assert "bad-model-thing" in issues[0].message
        assert "movement 2" in issues[0].message.lower()

    def test_movement_instrument_known(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: claude-code
            sheet:
              size: 10
              total_items: 3
            movements:
              2:
                instrument: gemini-cli
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 0


class TestInstrumentNameCheckMultiple:
    """Multiple unknown instruments produce separate issues."""

    def test_multiple_unknown_instruments(
        self, check: InstrumentNameCheck, tmp_path: Path, _mock_profiles: dict
    ) -> None:
        yaml_text = dedent("""
            name: test-job
            instrument: bad-top-level
            sheet:
              size: 10
              total_items: 3
              per_sheet_instruments:
                2: bad-per-sheet
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        issues = check.check(config, config_path, yaml_text)
        assert len(issues) == 2
        messages = [i.message for i in issues]
        assert any("bad-top-level" in m for m in messages)
        assert any("bad-per-sheet" in m for m in messages)


class TestInstrumentNameCheckProfileLoadFailure:
    """Graceful degradation when profiles can't be loaded."""

    def test_profile_load_error_skips_check(
        self, check: InstrumentNameCheck, tmp_path: Path
    ) -> None:
        """If load_all_profiles fails, skip check (don't block validation)."""
        yaml_text = dedent("""
            name: test-job
            instrument: anything
            sheet:
              size: 10
              total_items: 100
            prompt:
              template: "Do work"
        """).strip()
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_text)
        config = JobConfig.from_yaml(config_path)

        with patch(
            "marianne.instruments.loader.load_all_profiles",
            side_effect=Exception("Profile load failed"),
        ):
            issues = check.check(config, config_path, yaml_text)
        # Should return empty — not block validation on profile load failure
        assert len(issues) == 0
