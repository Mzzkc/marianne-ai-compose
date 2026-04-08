"""Tests for InstrumentProfileLoader — YAML loading from directories.

TDD: These tests define the contract for loading InstrumentProfile configs
from ~/.marianne/instruments/ (org) and .marianne/instruments/ (venue) directories.

The loader:
- Scans directories for *.yaml and *.yml files
- Parses each into an InstrumentProfile via Pydantic validation
- Venue profiles override org profiles on name collision
- Invalid files are logged and skipped, not fatal
- Returns dict[str, InstrumentProfile] keyed by profile name
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from marianne.core.config.instruments import InstrumentProfile


# --- Helpers ---


def _write_yaml(path: Path, content: str) -> Path:
    """Write YAML content to a file and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).strip())
    return path


def _minimal_cli_yaml(name: str = "test-cli", executable: str = "test") -> str:
    """Return minimal valid instrument YAML."""
    return f"""\
        name: {name}
        display_name: "{name} display"
        kind: cli
        cli:
          command:
            executable: {executable}
            prompt_flag: "-p"
          output:
            format: text
    """


def _gemini_yaml() -> str:
    """Return a realistic Gemini CLI profile YAML."""
    return """\
        name: gemini-cli
        display_name: "Gemini CLI"
        description: "Google's Gemini CLI with tool use and vision"
        kind: cli
        capabilities: [tool_use, file_editing, shell_access, vision, structured_output]
        default_model: gemini-2.5-pro
        default_timeout_seconds: 1800
        models:
          - name: gemini-2.5-pro
            context_window: 1000000
            cost_per_1k_input: 0.00125
            cost_per_1k_output: 0.005
            max_output_tokens: 65536
          - name: gemini-2.5-flash
            context_window: 1000000
            cost_per_1k_input: 0.00015
            cost_per_1k_output: 0.0006
        cli:
          command:
            executable: gemini
            prompt_flag: "-p"
            model_flag: "-m"
            auto_approve_flag: "--yolo"
            output_format_flag: "--output-format"
            output_format_value: "json"
          output:
            format: json
            result_path: "response"
            error_path: "error.message"
            input_tokens_path: "stats.models.*.tokens.prompt"
            output_tokens_path: "stats.models.*.tokens.candidates"
          errors:
            rate_limit_patterns: ["rate.?limit", "quota.?exceeded", "429"]
            auth_error_patterns: ["authenticat", "unauthorized"]
    """


# =============================================================================
# Happy Path
# =============================================================================


class TestInstrumentLoaderHappyPath:
    """Tests for successful profile loading."""

    def test_load_single_profile(self, tmp_path: Path) -> None:
        """Load a single valid YAML profile from a directory."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "test.yaml", _minimal_cli_yaml())

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert "test-cli" in profiles
        assert profiles["test-cli"].display_name == "test-cli display"

    def test_load_multiple_profiles(self, tmp_path: Path) -> None:
        """Load multiple YAML profiles from a directory."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "alpha.yaml", _minimal_cli_yaml("alpha"))
        _write_yaml(instruments_dir / "beta.yml", _minimal_cli_yaml("beta"))

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 2
        assert "alpha" in profiles
        assert "beta" in profiles

    def test_load_realistic_profile(self, tmp_path: Path) -> None:
        """Load a realistic Gemini CLI profile."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "gemini-cli.yaml", _gemini_yaml())

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert "gemini-cli" in profiles
        p = profiles["gemini-cli"]
        assert p.display_name == "Gemini CLI"
        assert p.default_model == "gemini-2.5-pro"
        assert len(p.models) == 2
        assert "vision" in p.capabilities
        assert p.cli is not None
        assert p.cli.command.auto_approve_flag == "--yolo"
        assert p.cli.output.result_path == "response"

    def test_yml_extension_accepted(self, tmp_path: Path) -> None:
        """Both .yaml and .yml extensions are recognized."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "foo.yml", _minimal_cli_yaml("foo"))

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert "foo" in profiles

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        """Empty directory returns empty dict, not error."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "empty"
        instruments_dir.mkdir()

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert profiles == {}

    def test_nonexistent_directory_returns_empty(self, tmp_path: Path) -> None:
        """Nonexistent directory returns empty dict, not error."""
        from marianne.instruments.loader import InstrumentProfileLoader

        profiles = InstrumentProfileLoader.load_directory(
            tmp_path / "does-not-exist"
        )
        assert profiles == {}


# =============================================================================
# Multi-Directory Loading (org + venue)
# =============================================================================


class TestInstrumentLoaderMultiDirectory:
    """Tests for loading from multiple directories with override semantics."""

    def test_venue_overrides_org(self, tmp_path: Path) -> None:
        """Venue profiles override org profiles on name collision."""
        from marianne.instruments.loader import InstrumentProfileLoader

        org_dir = tmp_path / "org"
        venue_dir = tmp_path / "venue"
        _write_yaml(org_dir / "tool.yaml", _minimal_cli_yaml("tool", "org-binary"))
        _write_yaml(venue_dir / "tool.yaml", _minimal_cli_yaml("tool", "venue-binary"))

        profiles = InstrumentProfileLoader.load_directories(
            [org_dir, venue_dir]
        )
        assert len(profiles) == 1
        assert profiles["tool"].cli is not None
        assert profiles["tool"].cli.command.executable == "venue-binary"

    def test_non_overlapping_profiles_merged(self, tmp_path: Path) -> None:
        """Non-overlapping profiles from different dirs are all included."""
        from marianne.instruments.loader import InstrumentProfileLoader

        org_dir = tmp_path / "org"
        venue_dir = tmp_path / "venue"
        _write_yaml(org_dir / "alpha.yaml", _minimal_cli_yaml("alpha"))
        _write_yaml(venue_dir / "beta.yaml", _minimal_cli_yaml("beta"))

        profiles = InstrumentProfileLoader.load_directories(
            [org_dir, venue_dir]
        )
        assert len(profiles) == 2
        assert "alpha" in profiles
        assert "beta" in profiles

    def test_later_dir_wins_on_collision(self, tmp_path: Path) -> None:
        """Last directory in list wins when names collide (expected: venue last)."""
        from marianne.instruments.loader import InstrumentProfileLoader

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_c = tmp_path / "c"
        _write_yaml(dir_a / "x.yaml", _minimal_cli_yaml("x", "exec-a"))
        _write_yaml(dir_b / "x.yaml", _minimal_cli_yaml("x", "exec-b"))
        _write_yaml(dir_c / "x.yaml", _minimal_cli_yaml("x", "exec-c"))

        profiles = InstrumentProfileLoader.load_directories(
            [dir_a, dir_b, dir_c]
        )
        assert profiles["x"].cli is not None
        assert profiles["x"].cli.command.executable == "exec-c"

    def test_missing_dirs_in_list_skipped(self, tmp_path: Path) -> None:
        """Missing directories in the list are skipped gracefully."""
        from marianne.instruments.loader import InstrumentProfileLoader

        real_dir = tmp_path / "real"
        _write_yaml(real_dir / "a.yaml", _minimal_cli_yaml("a"))

        profiles = InstrumentProfileLoader.load_directories(
            [tmp_path / "fake1", real_dir, tmp_path / "fake2"]
        )
        assert len(profiles) == 1
        assert "a" in profiles


# =============================================================================
# Error Handling
# =============================================================================


class TestInstrumentLoaderErrors:
    """Tests for graceful error handling on invalid inputs."""

    def test_invalid_yaml_skipped(self, tmp_path: Path) -> None:
        """Invalid YAML syntax is logged and skipped."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "good.yaml", _minimal_cli_yaml("good"))
        _write_yaml(instruments_dir / "bad.yaml", "{{not yaml at all!!")

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert "good" in profiles

    def test_validation_failure_skipped(self, tmp_path: Path) -> None:
        """YAML that fails Pydantic validation is skipped."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "good.yaml", _minimal_cli_yaml("good"))
        # kind=cli but no cli profile → fails model_validator
        _write_yaml(instruments_dir / "bad.yaml", """\
            name: bad-instrument
            display_name: "Bad"
            kind: cli
        """)

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert "good" in profiles

    def test_empty_yaml_skipped(self, tmp_path: Path) -> None:
        """Empty YAML file is skipped."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "good.yaml", _minimal_cli_yaml("good"))
        _write_yaml(instruments_dir / "empty.yaml", "")

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert "good" in profiles

    def test_yaml_list_instead_of_dict_skipped(self, tmp_path: Path) -> None:
        """YAML that parses as a list (not dict) is skipped."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "good.yaml", _minimal_cli_yaml("good"))
        _write_yaml(instruments_dir / "list.yaml", "- item1\n- item2")

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1

    def test_non_yaml_files_ignored(self, tmp_path: Path) -> None:
        """Non-YAML files (.json, .txt, .md) are silently ignored."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "good.yaml", _minimal_cli_yaml("good"))
        (instruments_dir / "readme.md").write_text("# docs")
        (instruments_dir / "notes.txt").write_text("notes")
        (instruments_dir / "data.json").write_text("{}")

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert "good" in profiles

    def test_subdirectories_not_recursed(self, tmp_path: Path) -> None:
        """Subdirectories are not recursed into."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "top.yaml", _minimal_cli_yaml("top"))
        _write_yaml(
            instruments_dir / "subdir" / "nested.yaml",
            _minimal_cli_yaml("nested"),
        )

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert "top" in profiles
        assert "nested" not in profiles

    def test_duplicate_names_in_same_dir_last_wins(self, tmp_path: Path) -> None:
        """Two files defining the same name: last alphabetically wins."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(
            instruments_dir / "a_tool.yaml",
            _minimal_cli_yaml("tool", "exec-a"),
        )
        _write_yaml(
            instruments_dir / "b_tool.yaml",
            _minimal_cli_yaml("tool", "exec-b"),
        )

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1
        assert profiles["tool"].cli is not None
        # Alphabetical sort: b_tool.yaml loads after a_tool.yaml
        assert profiles["tool"].cli.command.executable == "exec-b"


# =============================================================================
# Adversarial
# =============================================================================


class TestInstrumentLoaderAdversarial:
    """Adversarial tests for edge cases."""

    @pytest.mark.adversarial
    def test_binary_file_in_directory_ignored(self, tmp_path: Path) -> None:
        """Binary file with .yaml extension is handled gracefully."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        _write_yaml(instruments_dir / "good.yaml", _minimal_cli_yaml("good"))
        (instruments_dir / "binary.yaml").write_bytes(b"\x00\x01\x02\x03")

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert len(profiles) == 1

    @pytest.mark.adversarial
    def test_unicode_content_in_yaml(self, tmp_path: Path) -> None:
        """Unicode instrument names and descriptions are handled."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        yaml_content = """\
            name: 模型-cli
            display_name: "模型 CLI"
            description: "中文描述"
            kind: cli
            cli:
              command:
                executable: test
                prompt_flag: "-p"
              output:
                format: text
        """
        _write_yaml(instruments_dir / "unicode.yaml", yaml_content)

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert "模型-cli" in profiles
        assert profiles["模型-cli"].description == "中文描述"

    @pytest.mark.adversarial
    def test_very_large_yaml_accepted(self, tmp_path: Path) -> None:
        """Very large profile (many models) is accepted."""
        from marianne.instruments.loader import InstrumentProfileLoader

        instruments_dir = tmp_path / "instruments"
        models = "\n".join(
            f"  - name: model-{i}\n"
            f"    context_window: {1000 * (i + 1)}\n"
            f"    cost_per_1k_input: 0.001\n"
            f"    cost_per_1k_output: 0.003"
            for i in range(50)
        )
        yaml_content = f"""\
name: big-instrument
display_name: "Big"
kind: cli
models:
{models}
cli:
  command:
    executable: big
    prompt_flag: "-p"
  output:
    format: text
"""
        (instruments_dir).mkdir(parents=True, exist_ok=True)
        (instruments_dir / "big.yaml").write_text(yaml_content)

        profiles = InstrumentProfileLoader.load_directory(instruments_dir)
        assert "big-instrument" in profiles
        assert len(profiles["big-instrument"].models) == 50
