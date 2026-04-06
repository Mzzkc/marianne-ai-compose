"""Integration tests for the spec corpus pipeline.

Tests the complete data flow:
    YAML/MD files → SpecCorpusLoader → list[SpecFragment]
                                         ↓
                              SpecCorpusConfig.get_fragments_by_tags()
                                         ↓
                              PromptBuilder._format_spec_fragments()
                                         ↓
                              Injected into agent prompts

Each test validates that the pipeline correctly loads, filters, formats,
and hashes spec fragments. Adversarial tests verify that bad input at
any stage produces clear errors, not silent corruption.

TDD: Written by Blueprint (Movement 1) from Litmus test spec.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from marianne.core.config.spec import SpecCorpusConfig, SpecFragment
from marianne.prompts.templating import PromptBuilder
from marianne.spec.loader import SpecCorpusLoader


# =============================================================================
# SpecFragment model pipeline tests
# =============================================================================


class TestSpecFragmentModel:
    """Test SpecFragment as the pipeline's core data unit."""

    def test_immutable_after_creation(self) -> None:
        """SpecFragment is frozen — no mutation after creation."""
        frag = SpecFragment(name="test", content="body", tags=["a"])
        with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
            frag.name = "changed"  # type: ignore[misc]

    def test_tags_default_to_empty(self) -> None:
        frag = SpecFragment(name="test", content="body")
        assert frag.tags == []

    def test_structured_with_data(self) -> None:
        frag = SpecFragment(
            name="intent",
            content="## Goals",
            kind="structured",
            data={"goals": ["correctness", "reliability"]},
        )
        assert frag.kind == "structured"
        assert frag.data is not None
        assert "correctness" in frag.data["goals"]

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SpecFragment(name="", content="body")

    def test_rejects_whitespace_name(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SpecFragment(name="   ", content="body")

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SpecFragment(name="test", content="")

    def test_rejects_whitespace_content(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            SpecFragment(name="test", content="   ")


# =============================================================================
# SpecCorpusConfig pipeline tests
# =============================================================================


class TestSpecCorpusConfigFiltering:
    """Test tag-based fragment filtering — the pipeline's routing layer."""

    @pytest.fixture()
    def config_with_fragments(self) -> SpecCorpusConfig:
        return SpecCorpusConfig(
            spec_dir=".mozart/spec",
            fragments=[
                SpecFragment(name="intent", content="Goals doc", tags=["goals", "purpose"]),
                SpecFragment(name="constraints", content="MUSTs", tags=["safety", "guardrails"]),
                SpecFragment(name="conventions", content="Code style", tags=["code", "style"]),
                SpecFragment(name="readme", content="General", tags=[]),
            ],
        )

    def test_empty_tags_returns_all(self, config_with_fragments: SpecCorpusConfig) -> None:
        """No tag filter → all fragments returned."""
        result = config_with_fragments.get_fragments_by_tags([])
        assert len(result) == 4

    def test_single_tag_match(self, config_with_fragments: SpecCorpusConfig) -> None:
        """Single tag matches fragments with that tag."""
        result = config_with_fragments.get_fragments_by_tags(["safety"])
        assert len(result) == 1
        assert result[0].name == "constraints"

    def test_multi_tag_union(self, config_with_fragments: SpecCorpusConfig) -> None:
        """Multiple tags → union of matches (OR, not AND)."""
        result = config_with_fragments.get_fragments_by_tags(["goals", "code"])
        assert len(result) == 2
        names = {f.name for f in result}
        assert names == {"intent", "conventions"}

    def test_no_match_returns_empty(self, config_with_fragments: SpecCorpusConfig) -> None:
        """Tags that don't match any fragment return empty list."""
        result = config_with_fragments.get_fragments_by_tags(["nonexistent"])
        assert result == []

    def test_untagged_fragments_excluded_by_tag_filter(
        self, config_with_fragments: SpecCorpusConfig
    ) -> None:
        """Fragments with no tags are NOT matched by tag filters."""
        result = config_with_fragments.get_fragments_by_tags(["goals"])
        names = {f.name for f in result}
        assert "readme" not in names

    def test_partial_tag_overlap(self, config_with_fragments: SpecCorpusConfig) -> None:
        """Fragment with multiple tags matches if ANY tag matches."""
        result = config_with_fragments.get_fragments_by_tags(["purpose"])
        assert len(result) == 1
        assert result[0].name == "intent"


class TestSpecCorpusConfigHash:
    """Test corpus hashing — used for drift detection across runs."""

    def test_empty_corpus_hash(self) -> None:
        config = SpecCorpusConfig(fragments=[])
        expected = hashlib.sha256(b"").hexdigest()
        assert config.corpus_hash() == expected

    def test_hash_deterministic(self) -> None:
        frags = [
            SpecFragment(name="a", content="alpha"),
            SpecFragment(name="b", content="beta"),
        ]
        c1 = SpecCorpusConfig(fragments=frags)
        c2 = SpecCorpusConfig(fragments=frags)
        assert c1.corpus_hash() == c2.corpus_hash()

    def test_hash_order_independent(self) -> None:
        """Different insertion order → same hash (sorted by name internally)."""
        frag_a = SpecFragment(name="a", content="alpha")
        frag_b = SpecFragment(name="b", content="beta")
        c1 = SpecCorpusConfig(fragments=[frag_a, frag_b])
        c2 = SpecCorpusConfig(fragments=[frag_b, frag_a])
        assert c1.corpus_hash() == c2.corpus_hash()

    def test_hash_changes_on_content_change(self) -> None:
        c1 = SpecCorpusConfig(fragments=[SpecFragment(name="a", content="v1")])
        c2 = SpecCorpusConfig(fragments=[SpecFragment(name="a", content="v2")])
        assert c1.corpus_hash() != c2.corpus_hash()

    def test_hash_changes_on_name_change(self) -> None:
        c1 = SpecCorpusConfig(fragments=[SpecFragment(name="a", content="same")])
        c2 = SpecCorpusConfig(fragments=[SpecFragment(name="b", content="same")])
        assert c1.corpus_hash() != c2.corpus_hash()


# =============================================================================
# SpecCorpusLoader → SpecCorpusConfig integration
# =============================================================================


class TestLoaderToConfigIntegration:
    """Test loading files into fragments and building a config."""

    def test_load_yaml_and_build_config(self, tmp_path: Path) -> None:
        """YAML spec file → fragment → config → filterable."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "intent.yaml").write_text(textwrap.dedent("""\
            name: intent
            tags: [goals, purpose]
            kind: structured
            content: |
              ## Goals
              Correctness above all.
            data:
              primary_goal: correctness
        """))

        fragments = SpecCorpusLoader.load(str(spec_dir))
        config = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=fragments)

        assert len(config.fragments) == 1
        assert config.fragments[0].name == "intent"
        assert config.fragments[0].kind == "structured"
        assert "correctness" in (config.fragments[0].data or {}).get("primary_goal", "")

        # Tag filtering works
        goals = config.get_fragments_by_tags(["goals"])
        assert len(goals) == 1
        assert goals[0].name == "intent"

    def test_load_markdown_and_build_config(self, tmp_path: Path) -> None:
        """Markdown spec file → text fragment → config."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "readme.md").write_text("# Project\n\nThis is the project.")

        fragments = SpecCorpusLoader.load(str(spec_dir))
        config = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=fragments)

        assert len(config.fragments) == 1
        assert config.fragments[0].kind == "text"
        assert "Project" in config.fragments[0].content

    def test_load_mixed_directory(self, tmp_path: Path) -> None:
        """Mix of YAML and markdown files → combined fragment list."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        (spec_dir / "intent.yaml").write_text(
            "name: intent\ntags: [goals]\nkind: text\ncontent: Goals here\n"
        )
        (spec_dir / "conventions.yaml").write_text(
            "name: conventions\ntags: [code]\nkind: text\ncontent: Code rules\n"
        )
        (spec_dir / "notes.md").write_text("# Notes\n\nMarkdown notes.")

        fragments = SpecCorpusLoader.load(str(spec_dir))
        config = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=fragments)

        assert len(config.fragments) == 3
        names = {f.name for f in config.fragments}
        assert "intent" in names
        assert "conventions" in names

    def test_empty_dir_produces_empty_config(self, tmp_path: Path) -> None:
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()

        fragments = SpecCorpusLoader.load(str(spec_dir))
        config = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=fragments)

        assert config.fragments == []
        assert config.corpus_hash() == hashlib.sha256(b"").hexdigest()


# =============================================================================
# SpecCorpusConfig → PromptBuilder integration
# =============================================================================


class TestConfigToPromptIntegration:
    """Test fragment formatting for prompt injection."""

    def test_format_empty_fragments(self) -> None:
        """Empty fragment list → empty string."""
        result = PromptBuilder._format_spec_fragments([])
        assert result == ""

    def test_format_single_fragment(self) -> None:
        frag = SpecFragment(name="intent", content="## Goals\n\nCorrectness.")
        result = PromptBuilder._format_spec_fragments([frag])
        assert "## Injected Specs" in result
        assert "Correctness" in result

    def test_format_multiple_fragments(self) -> None:
        frags = [
            SpecFragment(name="intent", content="Goals content"),
            SpecFragment(name="constraints", content="Safety content"),
        ]
        result = PromptBuilder._format_spec_fragments(frags)
        assert "Goals content" in result
        assert "Safety content" in result

    def test_format_preserves_content_exactly(self) -> None:
        """Content should not be modified during formatting."""
        content = "Line 1\n  indented\n    more indent\n\n## Header"
        frag = SpecFragment(name="test", content=content)
        result = PromptBuilder._format_spec_fragments([frag])
        assert content in result


# =============================================================================
# End-to-end pipeline tests
# =============================================================================


class TestEndToEndPipeline:
    """Test the full pipeline: files → load → filter → format."""

    def test_full_pipeline_with_tag_filtering(self, tmp_path: Path) -> None:
        """Full pipeline: load files, filter by tags, format for injection."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()

        # Create spec files with different tags
        (spec_dir / "intent.yaml").write_text(textwrap.dedent("""\
            name: intent
            tags: [goals, mission]
            kind: text
            content: |
              Build the best orchestrator.
        """))
        (spec_dir / "constraints.yaml").write_text(textwrap.dedent("""\
            name: constraints
            tags: [safety, limits]
            kind: text
            content: |
              Never delete user data.
        """))

        # Step 1: Load
        fragments = SpecCorpusLoader.load(str(spec_dir))
        assert len(fragments) == 2

        # Step 2: Build config
        config = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=fragments)

        # Step 3: Filter by tags (simulating per-sheet tag selection)
        safety_frags = config.get_fragments_by_tags(["safety"])
        assert len(safety_frags) == 1
        assert safety_frags[0].name == "constraints"

        # Step 4: Format for injection
        formatted = PromptBuilder._format_spec_fragments(safety_frags)
        assert "Never delete user data" in formatted
        assert "Build the best orchestrator" not in formatted

    def test_pipeline_with_real_spec_corpus(self) -> None:
        """Integration: load the REAL .mozart/spec/ directory."""
        spec_dir = Path(".mozart/spec")
        if not spec_dir.is_dir():
            pytest.skip("Real spec corpus not available")

        fragments = SpecCorpusLoader.load(str(spec_dir))
        assert len(fragments) >= 4, f"Expected ≥4 spec files, got {len(fragments)}"

        config = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=fragments)

        # Verify known fragments exist
        names = {f.name for f in config.fragments}
        assert "intent" in names
        assert "constraints" in names
        assert "conventions" in names

        # Verify hash is deterministic
        hash1 = config.corpus_hash()
        hash2 = config.corpus_hash()
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex digest

        # Verify tag filtering works on real data
        for frag in config.fragments:
            if frag.tags:
                result = config.get_fragments_by_tags([frag.tags[0]])
                assert any(f.name == frag.name for f in result)

    def test_corpus_hash_detects_drift(self, tmp_path: Path) -> None:
        """Changing spec content changes the hash — drift detection works."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()

        (spec_dir / "test.yaml").write_text(
            "name: test\ntags: []\nkind: text\ncontent: version 1\n"
        )
        frags_v1 = SpecCorpusLoader.load(str(spec_dir))
        config_v1 = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=frags_v1)

        # Modify the file
        (spec_dir / "test.yaml").write_text(
            "name: test\ntags: []\nkind: text\ncontent: version 2\n"
        )
        frags_v2 = SpecCorpusLoader.load(str(spec_dir))
        config_v2 = SpecCorpusConfig(spec_dir=str(spec_dir), fragments=frags_v2)

        assert config_v1.corpus_hash() != config_v2.corpus_hash()
