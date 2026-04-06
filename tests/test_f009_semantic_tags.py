"""Tests for F-009/F-144 fix: semantic context tag generation.

The root cause of F-009 was a tag namespace mismatch: patterns were stored
with semantic tags (validation:file_exists, retry:effective, error_code:E001)
but queried with positional tags (sheet:N, job:X) — zero overlap.

These tests verify that _query_relevant_patterns() now generates semantic
context tags from the execution context (validation types, instrument name)
that match what the storage side produces.

TDD: Written before implementation. All should FAIL until the fix lands.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from marianne.core.config import JobConfig
from marianne.execution.runner.patterns import PatternsMixin, build_semantic_context_tags


# =============================================================================
# Fixtures
# =============================================================================


def _make_config(**overrides: object) -> JobConfig:
    """Create a JobConfig with optional overrides."""
    base = {
        "name": "test-semantic-tags",
        "description": "Test for F-009 semantic tag fix",
        "backend": {
            "type": "claude_cli",
            "skip_permissions": True,
            "cli_model": "claude-sonnet-4-5-20250929",
        },
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Process sheet {{ sheet_num }}."},
        "learning": {"enabled": True},
    }
    base.update(overrides)
    return JobConfig.model_validate(base)


def _make_pattern(
    pattern_id: str = "p1",
    name: str = "Test Pattern",
    effectiveness: float = 0.8,
    priority: float = 0.7,
    occurrence_count: int = 10,
) -> MagicMock:
    """Create a mock PatternRecord."""
    p = MagicMock()
    p.id = pattern_id
    p.pattern_name = name
    p.description = f"Description: {name}"
    p.effectiveness_score = effectiveness
    p.priority_score = priority
    p.occurrence_count = occurrence_count
    return p


def _make_runner_mixin(config: JobConfig) -> PatternsMixin:
    """Create a bare PatternsMixin with required attributes."""
    mixin = PatternsMixin.__new__(PatternsMixin)
    mixin.config = config
    mixin._logger = MagicMock()
    mixin.console = MagicMock()
    mixin._global_learning_store = MagicMock()
    mixin._exploration_pattern_ids = []
    mixin._exploitation_pattern_ids = []
    # Default: store returns patterns on first call (tag-filtered)
    mixin._global_learning_store.get_patterns.return_value = [
        _make_pattern("p1", "Pattern One", effectiveness=0.9, priority=0.8),
    ]
    mixin._global_learning_store.get_patterns_for_auto_apply.return_value = []
    return mixin


# =============================================================================
# Tests for build_semantic_context_tags (the new function)
# =============================================================================


class TestBuildSemanticContextTags:
    """Tests for the semantic tag builder extracted from _query_relevant_patterns."""

    def test_includes_validation_type_tags(self) -> None:
        """Validation types from config produce validation:TYPE tags."""
        config = _make_config(
            validations=[
                {"type": "file_exists", "path": "{workspace}/out.txt"},
                {"type": "command_succeeds", "command": "echo ok"},
            ]
        )
        tags = build_semantic_context_tags(config)
        assert "validation:file_exists" in tags
        assert "validation:command_succeeds" in tags

    def test_includes_broad_category_tags(self) -> None:
        """Always includes broad category tags that match common stored patterns."""
        config = _make_config()
        tags = build_semantic_context_tags(config)
        # These match the tags assigned during pattern discovery
        assert "success:first_attempt" in tags
        assert "retry:effective" in tags

    def test_no_positional_tags(self) -> None:
        """Must NOT generate positional tags (the F-009 root cause)."""
        config = _make_config()
        tags = build_semantic_context_tags(config)
        # No positional tags — these were the root cause of F-009
        for tag in tags:
            assert not tag.startswith("sheet:"), f"Positional tag found: {tag}"
            assert not tag.startswith("job:"), f"Positional tag found: {tag}"

    def test_deduplicates_validation_types(self) -> None:
        """Multiple validations with the same type produce one tag."""
        config = _make_config(
            validations=[
                {"type": "file_exists", "path": "{workspace}/a.txt"},
                {"type": "file_exists", "path": "{workspace}/b.txt"},
                {"type": "content_contains", "path": "{workspace}/a.txt", "pattern": "ok"},
            ]
        )
        tags = build_semantic_context_tags(config)
        assert tags.count("validation:file_exists") == 1
        assert "validation:content_contains" in tags

    def test_empty_validations(self) -> None:
        """Config with no validations still produces broad category tags."""
        config = _make_config(validations=[])
        tags = build_semantic_context_tags(config)
        assert len(tags) > 0  # Broad categories still present
        assert "success:first_attempt" in tags

    def test_all_five_validation_types(self) -> None:
        """All five validation types produce corresponding tags."""
        config = _make_config(
            validations=[
                {"type": "file_exists", "path": "{workspace}/a.txt"},
                {"type": "file_modified", "path": "{workspace}/a.txt"},
                {"type": "content_contains", "path": "{workspace}/a.txt", "pattern": "ok"},
                {"type": "content_regex", "path": "{workspace}/a.txt", "pattern": ".*"},
                {"type": "command_succeeds", "command": "echo ok"},
            ]
        )
        tags = build_semantic_context_tags(config)
        assert "validation:file_exists" in tags
        assert "validation:file_modified" in tags
        assert "validation:content_contains" in tags
        assert "validation:content_regex" in tags
        assert "validation:command_succeeds" in tags


# =============================================================================
# Tests for _query_relevant_patterns with semantic tags
# =============================================================================


class TestQueryWithSemanticTags:
    """Tests that _query_relevant_patterns passes semantic tags to the store."""

    def test_semantic_tags_passed_to_get_patterns(self) -> None:
        """Verify semantic tags (not positional) reach get_patterns."""
        config = _make_config(
            validations=[
                {"type": "file_exists", "path": "{workspace}/out.txt"},
            ]
        )
        mixin = _make_runner_mixin(config)

        mixin._query_relevant_patterns(job_id="test-job", sheet_num=1)

        # First call should use semantic tags
        call_kwargs = mixin._global_learning_store.get_patterns.call_args_list[0].kwargs
        tags = call_kwargs.get("context_tags", [])
        assert "validation:file_exists" in tags
        # Should NOT have positional tags
        assert "sheet:1" not in tags
        assert "job:test-job" not in tags

    def test_instrument_name_passed_to_get_patterns(self) -> None:
        """When config has instrument, it's passed as instrument_name filter."""
        config = _make_config(instrument="gemini-cli")
        mixin = _make_runner_mixin(config)

        mixin._query_relevant_patterns(job_id="test-job", sheet_num=1)

        call_kwargs = mixin._global_learning_store.get_patterns.call_args_list[0].kwargs
        assert call_kwargs.get("instrument_name") == "gemini-cli"

    def test_no_instrument_when_not_configured(self) -> None:
        """When config has no instrument, instrument_name is None."""
        config = _make_config()  # No instrument: field
        mixin = _make_runner_mixin(config)

        mixin._query_relevant_patterns(job_id="test-job", sheet_num=1)

        call_kwargs = mixin._global_learning_store.get_patterns.call_args_list[0].kwargs
        assert call_kwargs.get("instrument_name") is None

    def test_explicit_context_tags_override_semantic(self) -> None:
        """Explicit context_tags parameter still takes precedence."""
        config = _make_config(
            validations=[
                {"type": "file_exists", "path": "{workspace}/out.txt"},
            ]
        )
        mixin = _make_runner_mixin(config)

        explicit_tags = ["error:rate_limit", "phase:execution"]
        mixin._query_relevant_patterns(
            job_id="test-job",
            sheet_num=1,
            context_tags=explicit_tags,
        )

        call_kwargs = mixin._global_learning_store.get_patterns.call_args_list[0].kwargs
        tags = call_kwargs.get("context_tags", [])
        assert "error:rate_limit" in tags
        assert "phase:execution" in tags

    def test_fallback_still_works_when_no_semantic_matches(self) -> None:
        """When semantic tag query returns empty, fallback queries without tags."""
        config = _make_config()
        mixin = _make_runner_mixin(config)
        # First call (with tags) returns nothing, second call (without) returns patterns
        mixin._global_learning_store.get_patterns.side_effect = [
            [],  # Tag-filtered: no match
            [_make_pattern("p1", "Fallback Pattern")],  # Unfiltered: found one
        ]

        descriptions, pattern_ids = mixin._query_relevant_patterns(
            job_id="test-job", sheet_num=1,
        )

        assert len(descriptions) == 1
        assert "p1" in pattern_ids
        # Second call should have no context_tags
        second_call_kwargs = mixin._global_learning_store.get_patterns.call_args_list[1].kwargs
        assert second_call_kwargs.get("context_tags") is None

    def test_fallback_also_passes_instrument_name(self) -> None:
        """Fallback query preserves instrument_name filter."""
        config = _make_config(instrument="claude-code")
        mixin = _make_runner_mixin(config)
        mixin._global_learning_store.get_patterns.side_effect = [
            [],  # Tag-filtered: no match
            [_make_pattern("p1", "Fallback")],  # Unfiltered: found one
        ]

        mixin._query_relevant_patterns(job_id="test-job", sheet_num=1)

        # Both calls should pass instrument_name
        for call in mixin._global_learning_store.get_patterns.call_args_list:
            assert call.kwargs.get("instrument_name") == "claude-code"


# =============================================================================
# Tests for auto_apply also using semantic tags
# =============================================================================


class TestAutoApplySemanticTags:
    """Tests that auto_apply queries also get semantic tags."""

    def test_auto_apply_gets_semantic_tags(self) -> None:
        """Auto-apply query should use semantic tags, not positional."""
        config = _make_config(
            validations=[
                {"type": "command_succeeds", "command": "pytest"},
            ],
            learning={
                "enabled": True,
                "auto_apply": {
                    "enabled": True,
                    "trust_threshold": 0.8,
                    "require_validated_status": False,
                    "max_patterns_per_sheet": 3,
                    "log_applications": True,
                },
            },
        )
        mixin = _make_runner_mixin(config)

        mixin._query_relevant_patterns(job_id="test-job", sheet_num=1)

        # Auto-apply call should also have semantic tags
        auto_call = mixin._global_learning_store.get_patterns_for_auto_apply.call_args
        if auto_call:
            tags = auto_call.kwargs.get("context_tags", [])
            assert "validation:command_succeeds" in tags
            assert "sheet:1" not in tags
