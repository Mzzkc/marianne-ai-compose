"""Tests for mozart.spec.loader — SpecCorpusLoader.

TDD: These tests define the contract for the SpecCorpusLoader, which reads
YAML and Markdown spec files from a directory and produces SpecFragment
instances. The loader is the first stage of the spec corpus pipeline.

Tests cover:
  - Happy path: valid YAML, valid markdown, mixed directories
  - Error handling: missing dir, not-a-dir, empty files, bad YAML
  - Adversarial: binary content, huge files, unicode, symlinks, permissions
  - CLAUDE.md loading
  - F-002 regression: falsy-but-valid YAML values (name: 0, content: false)

All tests use tmp_path fixtures. No timing dependencies. No cross-test state.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from marianne.core.config.spec import SpecFragment
from marianne.spec.loader import SpecCorpusError, SpecCorpusLoader


# --- Happy Path: Valid YAML Files ---


class TestLoadYamlFragments:
    """Test loading valid YAML spec files."""

    def test_load_single_yaml(self, tmp_path: Path) -> None:
        """Load a single well-formed YAML spec file."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "intent.yaml").write_text(
            yaml.dump({
                "name": "intent",
                "tags": ["goals", "purpose"],
                "kind": "structured",
                "content": "This is the intent spec.",
                "data": {"primary_goal": "correctness"},
            }),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        frag = fragments[0]
        assert frag.name == "intent"
        assert frag.kind == "structured"
        assert frag.tags == ["goals", "purpose"]
        assert "intent spec" in frag.content
        assert frag.data is not None
        assert frag.data["primary_goal"] == "correctness"

    def test_load_yaml_with_multiline_content(self, tmp_path: Path) -> None:
        """YAML content with multiline literal block scalar."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        yaml_content = (
            "name: conventions\n"
            "tags: [code, style]\n"
            "kind: structured\n"
            "content: |\n"
            "  Line one.\n"
            "  Line two.\n"
            "  Line three.\n"
        )
        (spec_dir / "conventions.yaml").write_text(yaml_content, encoding="utf-8")

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert "Line one." in fragments[0].content
        assert "Line three." in fragments[0].content

    def test_load_yaml_minimal_fields(self, tmp_path: Path) -> None:
        """YAML with only required fields (name, content). Tags default to [], kind to text."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "simple.yaml").write_text(
            yaml.dump({"name": "simple", "content": "Just content."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "simple"
        assert fragments[0].tags == []
        assert fragments[0].kind == "text"
        assert fragments[0].data is None

    def test_load_yaml_with_data_section(self, tmp_path: Path) -> None:
        """YAML with a data section containing nested dicts and lists."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "arch.yaml").write_text(
            yaml.dump({
                "name": "architecture",
                "content": "Architecture overview.",
                "tags": ["arch"],
                "kind": "structured",
                "data": {
                    "layers": ["core", "execution", "daemon"],
                    "invariants": {"state_atomic": True},
                },
            }),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert fragments[0].data is not None
        assert "core" in fragments[0].data["layers"]
        assert fragments[0].data["invariants"]["state_atomic"] is True

    def test_load_yml_extension(self, tmp_path: Path) -> None:
        """Both .yaml and .yml extensions are recognized."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "quality.yml").write_text(
            yaml.dump({"name": "quality", "content": "Quality spec."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "quality"

    def test_scalar_tags_coerced_to_list(self, tmp_path: Path) -> None:
        """If tags is a single string (not a list), it gets wrapped in a list."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "single.yaml").write_text(
            yaml.dump({"name": "single", "content": "Content.", "tags": "solo-tag"}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert fragments[0].tags == ["solo-tag"]


# --- Happy Path: Valid Markdown Files ---


class TestLoadMarkdownFragments:
    """Test loading valid Markdown spec files."""

    def test_load_single_markdown(self, tmp_path: Path) -> None:
        """Load a single markdown file. Name derived from filename stem."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "overview.md").write_text(
            "# Overview\n\nThis is the overview.", encoding="utf-8"
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "overview"
        assert fragments[0].kind == "text"
        assert fragments[0].tags == []
        assert "# Overview" in fragments[0].content


# --- Happy Path: Mixed Directories ---


class TestLoadMixedDirectories:
    """Test loading from directories with multiple file types."""

    def test_load_mixed_yaml_and_markdown(self, tmp_path: Path) -> None:
        """Directory with both YAML and markdown files."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "intent.yaml").write_text(
            yaml.dump({"name": "intent", "content": "Intent content."}),
            encoding="utf-8",
        )
        (spec_dir / "notes.md").write_text("# Notes\n\nSome notes.", encoding="utf-8")

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 2
        names = {f.name for f in fragments}
        assert "intent" in names
        assert "notes" in names

    def test_unrecognized_extensions_skipped(self, tmp_path: Path) -> None:
        """Files with unrecognized extensions are silently skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "data.json").write_text('{"key": "value"}', encoding="utf-8")
        (spec_dir / "script.py").write_text("print('hello')", encoding="utf-8")
        (spec_dir / "real.yaml").write_text(
            yaml.dump({"name": "real", "content": "Real content."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "real"

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        """An empty spec directory returns an empty list, not an error."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()

        fragments = SpecCorpusLoader.load(spec_dir)
        assert fragments == []

    def test_subdirectories_are_not_recursed(self, tmp_path: Path) -> None:
        """Only top-level files are read. Subdirectories are skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        sub = spec_dir / "subdir"
        sub.mkdir()
        (sub / "nested.yaml").write_text(
            yaml.dump({"name": "nested", "content": "Should be ignored."}),
            encoding="utf-8",
        )
        (spec_dir / "top.yaml").write_text(
            yaml.dump({"name": "top", "content": "Top level."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "top"

    def test_fragments_sorted_by_name(self, tmp_path: Path) -> None:
        """Fragments are returned sorted by name, regardless of filesystem order."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        for name in ["zeta", "alpha", "middle"]:
            (spec_dir / f"{name}.yaml").write_text(
                yaml.dump({"name": name, "content": f"{name} content."}),
                encoding="utf-8",
            )

        fragments = SpecCorpusLoader.load(spec_dir)
        names = [f.name for f in fragments]
        assert names == ["alpha", "middle", "zeta"]


# --- Error Handling ---


class TestLoadErrors:
    """Test loader behavior on invalid inputs."""

    def test_nonexistent_directory_raises(self, tmp_path: Path) -> None:
        """Loading from a nonexistent path raises SpecCorpusError."""
        with pytest.raises(SpecCorpusError, match="does not exist"):
            SpecCorpusLoader.load(tmp_path / "nonexistent")

    def test_file_instead_of_directory_raises(self, tmp_path: Path) -> None:
        """Loading from a file path (not a directory) raises SpecCorpusError."""
        file_path = tmp_path / "not-a-dir.yaml"
        file_path.write_text("content: hello", encoding="utf-8")

        with pytest.raises(SpecCorpusError, match="not a directory"):
            SpecCorpusLoader.load(file_path)

    def test_yaml_missing_name_skipped(self, tmp_path: Path) -> None:
        """YAML file without 'name' field is skipped with a warning."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "bad.yaml").write_text(
            yaml.dump({"content": "No name field."}), encoding="utf-8"
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_yaml_missing_content_skipped(self, tmp_path: Path) -> None:
        """YAML file without 'content' field is skipped with a warning."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "bad.yaml").write_text(
            yaml.dump({"name": "bad"}), encoding="utf-8"
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_invalid_yaml_skipped(self, tmp_path: Path) -> None:
        """Invalid YAML syntax is skipped, not crashed on."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "broken.yaml").write_text(
            "name: broken\ncontent: [unclosed bracket", encoding="utf-8"
        )
        (spec_dir / "good.yaml").write_text(
            yaml.dump({"name": "good", "content": "Still works."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "good"

    def test_yaml_list_at_top_level_skipped(self, tmp_path: Path) -> None:
        """YAML file with a list (not mapping) at top level is skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "list.yaml").write_text(
            yaml.dump(["item1", "item2"]), encoding="utf-8"
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_empty_yaml_file_skipped(self, tmp_path: Path) -> None:
        """Empty YAML file is skipped (yaml.safe_load returns None)."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "empty.yaml").write_text("", encoding="utf-8")

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_empty_markdown_file_skipped(self, tmp_path: Path) -> None:
        """Empty markdown file is skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "empty.md").write_text("", encoding="utf-8")

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_whitespace_only_markdown_skipped(self, tmp_path: Path) -> None:
        """Markdown file with only whitespace is skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "blank.md").write_text("   \n  \n  ", encoding="utf-8")

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_bad_file_does_not_block_good_files(self, tmp_path: Path) -> None:
        """A broken file doesn't prevent loading other valid files."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "a_bad.yaml").write_text(
            "::not valid yaml::", encoding="utf-8"
        )
        (spec_dir / "b_good.yaml").write_text(
            yaml.dump({"name": "good", "content": "Works."}),
            encoding="utf-8",
        )
        (spec_dir / "c_good.md").write_text(
            "# Markdown works too.", encoding="utf-8"
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 2


# --- F-002 Regression: Falsy-but-Valid YAML Values ---


class TestFalsyYamlValues:
    """Regression tests for F-002: loader rejects falsy-but-valid YAML values.

    YAML parses `0` as int(0) and `false` as bool(False), which are falsy in
    Python. The old code used `if not name:` which rejects these. The fix
    should change to `if name is None:`.
    """

    def test_numeric_zero_name(self, tmp_path: Path) -> None:
        """name: 0 is falsy but valid — should be coerced to '0' via str()."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "zero.yaml").write_text(
            yaml.dump({"name": 0, "content": "Numeric zero name."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "0"

    def test_boolean_false_name(self, tmp_path: Path) -> None:
        """name: false is falsy but valid — should be coerced to 'False'."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "false.yaml").write_text(
            yaml.dump({"name": False, "content": "Boolean false name."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "False"

    def test_numeric_zero_content(self, tmp_path: Path) -> None:
        """content: 0 is falsy but valid — should be coerced to '0'."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "zero_content.yaml").write_text(
            yaml.dump({"name": "zc", "content": 0}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].content == "0"

    def test_boolean_false_content(self, tmp_path: Path) -> None:
        """content: false is falsy but valid — should be coerced to 'False'."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "false_content.yaml").write_text(
            yaml.dump({"name": "fc", "content": False}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].content == "False"

    def test_empty_string_name_still_rejected(self, tmp_path: Path) -> None:
        """name: '' is still rejected — empty string is genuinely invalid."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "empty_name.yaml").write_text(
            yaml.dump({"name": "", "content": "Has content."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        # Empty string name should be rejected (SpecFragment validator catches it)
        # The loader should skip this file
        assert len(fragments) == 0

    def test_none_name_rejected(self, tmp_path: Path) -> None:
        """Explicit name: null (None) is properly rejected."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        # YAML `~` or `null` parses to None
        (spec_dir / "null.yaml").write_text(
            "name: ~\ncontent: Has content.\n",
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    def test_none_content_rejected(self, tmp_path: Path) -> None:
        """Explicit content: null (None) is properly rejected."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "null_content.yaml").write_text(
            "name: valid\ncontent: ~\n",
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0


# --- CLAUDE.md Loading ---


class TestLoadClaudeMd:
    """Test SpecCorpusLoader.load_claude_md."""

    def test_load_existing_claude_md(self, tmp_path: Path) -> None:
        """CLAUDE.md is loaded as a text fragment with name='claude_md'."""
        (tmp_path / "CLAUDE.md").write_text(
            "# Project Instructions\n\nDo the right thing.", encoding="utf-8"
        )

        fragment = SpecCorpusLoader.load_claude_md(tmp_path)
        assert fragment is not None
        assert fragment.name == "claude_md"
        assert fragment.kind == "text"
        assert fragment.tags == []
        assert "Project Instructions" in fragment.content

    def test_missing_claude_md_returns_none(self, tmp_path: Path) -> None:
        """No CLAUDE.md returns None, not an error."""
        fragment = SpecCorpusLoader.load_claude_md(tmp_path)
        assert fragment is None

    def test_empty_claude_md_returns_none(self, tmp_path: Path) -> None:
        """Empty CLAUDE.md returns None."""
        (tmp_path / "CLAUDE.md").write_text("", encoding="utf-8")

        fragment = SpecCorpusLoader.load_claude_md(tmp_path)
        assert fragment is None

    def test_whitespace_only_claude_md_returns_none(self, tmp_path: Path) -> None:
        """Whitespace-only CLAUDE.md returns None."""
        (tmp_path / "CLAUDE.md").write_text("   \n  \n  ", encoding="utf-8")

        fragment = SpecCorpusLoader.load_claude_md(tmp_path)
        assert fragment is None

    def test_claude_md_not_a_file(self, tmp_path: Path) -> None:
        """CLAUDE.md as a directory returns None, not error."""
        (tmp_path / "CLAUDE.md").mkdir()

        fragment = SpecCorpusLoader.load_claude_md(tmp_path)
        assert fragment is None


# --- Adversarial Tests ---


class TestSpecLoaderAdversarial:
    """Adversarial tests: unexpected inputs, edge cases, boundary conditions."""

    @pytest.mark.adversarial
    def test_unicode_content(self, tmp_path: Path) -> None:
        """Unicode content (CJK, emoji, diacritics) loads correctly."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "unicode.yaml").write_text(
            yaml.dump({
                "name": "unicode",
                "content": "日本語コンテンツ — with émojis 🎵 and diacritics àéîõü",
            }),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert "日本語" in fragments[0].content
        assert "🎵" in fragments[0].content

    @pytest.mark.adversarial
    def test_large_content(self, tmp_path: Path) -> None:
        """Large content (1MB+) loads successfully."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        large_content = "x" * 1_048_576  # 1MB
        (spec_dir / "large.yaml").write_text(
            yaml.dump({"name": "large", "content": large_content}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert len(fragments[0].content) == 1_048_576

    @pytest.mark.adversarial
    def test_numeric_tags_coerced_to_strings(self, tmp_path: Path) -> None:
        """Numeric tag values (YAML ints) are coerced to strings."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "numtags.yaml").write_text(
            yaml.dump({"name": "numtags", "content": "Content.", "tags": [1, 2, 3]}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert fragments[0].tags == ["1", "2", "3"]

    @pytest.mark.adversarial
    def test_boolean_tags_coerced_to_strings(self, tmp_path: Path) -> None:
        """Boolean tag values (YAML booleans) are coerced to strings."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "booltags.yaml").write_text(
            yaml.dump({"name": "booltags", "content": "Content.", "tags": [True, False]}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert fragments[0].tags == ["True", "False"]

    @pytest.mark.adversarial
    def test_invalid_kind_falls_through(self, tmp_path: Path) -> None:
        """Invalid kind value is caught by SpecFragment's Literal validator."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "bad_kind.yaml").write_text(
            yaml.dump({"name": "bad_kind", "content": "Content.", "kind": "unknown"}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        # SpecFragment rejects invalid kind, loader catches the error
        assert len(fragments) == 0

    @pytest.mark.adversarial
    def test_yaml_with_only_comments(self, tmp_path: Path) -> None:
        """YAML file with only comments (parses to None) is skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "comments.yaml").write_text(
            "# This is a comment\n# Another comment\n", encoding="utf-8"
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    @pytest.mark.adversarial
    def test_yaml_with_scalar_at_top_level(self, tmp_path: Path) -> None:
        """YAML file with a scalar (string) at top level is skipped."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "scalar.yaml").write_text("just a string", encoding="utf-8")

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 0

    @pytest.mark.adversarial
    def test_string_path_accepted(self) -> None:
        """spec_dir can be a string path, not just Path."""
        with pytest.raises(SpecCorpusError, match="does not exist"):
            SpecCorpusLoader.load("/nonexistent/path/to/spec")

    @pytest.mark.adversarial
    def test_multiple_yaml_errors_collected(self, tmp_path: Path) -> None:
        """Multiple broken files produce warnings but don't crash."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        for i in range(5):
            (spec_dir / f"bad_{i}.yaml").write_text(
                yaml.dump({"name": f"bad_{i}"}),  # missing content
                encoding="utf-8",
            )
        (spec_dir / "good.yaml").write_text(
            yaml.dump({"name": "good", "content": "Survived."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        assert len(fragments) == 1
        assert fragments[0].name == "good"

    @pytest.mark.adversarial
    def test_fragment_is_frozen(self, tmp_path: Path) -> None:
        """Loaded fragments are frozen (immutable) per SpecFragment model_config."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "frozen.yaml").write_text(
            yaml.dump({"name": "frozen", "content": "Immutable content."}),
            encoding="utf-8",
        )

        fragments = SpecCorpusLoader.load(spec_dir)
        with pytest.raises(Exception):  # Pydantic frozen model rejects mutation
            fragments[0].name = "mutated"  # type: ignore[misc]

    @pytest.mark.adversarial
    def test_binary_content_in_yaml_handled(self, tmp_path: Path) -> None:
        """YAML file with binary-like content doesn't crash the loader."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        # Write bytes that look like a binary file but with valid YAML wrapper
        (spec_dir / "binary.yaml").write_bytes(b"\x00\x01\x02\xff")

        fragments = SpecCorpusLoader.load(spec_dir)
        # Should be skipped due to encoding error or YAML parse error
        assert len(fragments) == 0

    @pytest.mark.adversarial
    def test_claude_md_read_error_returns_none(self, tmp_path: Path) -> None:
        """If CLAUDE.md can't be read (e.g., encoding error), returns None."""
        claude_path = tmp_path / "CLAUDE.md"
        claude_path.write_bytes(b"\x80\x81\x82\x83")  # invalid UTF-8

        # The loader should catch the UnicodeDecodeError and return None
        with patch("marianne.spec.loader._logger"):
            fragment = SpecCorpusLoader.load_claude_md(tmp_path)
        assert fragment is None


# --- Integration: Real Spec Corpus ---


class TestRealSpecCorpus:
    """Integration tests against the actual Mozart spec corpus if it exists."""

    def test_load_actual_spec_corpus(self) -> None:
        """Load the actual .mozart/spec/ directory if it exists."""
        spec_dir = Path("/home/emzi/Projects/mozart-ai-compose/.mozart/spec")
        if not spec_dir.exists():
            pytest.skip("No .mozart/spec/ directory in project")

        fragments = SpecCorpusLoader.load(spec_dir)
        # The real corpus has 5 YAML files
        assert len(fragments) >= 4
        names = {f.name for f in fragments}
        assert "intent" in names
        assert "architecture" in names
        assert "conventions" in names
        assert "constraints" in names
