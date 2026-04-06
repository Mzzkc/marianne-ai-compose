"""User journey tests: what real people see when they make score YAML mistakes.

These aren't unit tests for extra='forbid'. They're stories about real users
who make real mistakes and need real guidance — not generic "check your score"
messages.

Journey's principle: the bugs that make users quietly abandon your product
aren't crashes — they're confusion. These tests prove Mozart doesn't confuse.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from marianne.cli.commands.validate import _schema_error_hints
from marianne.core.config.job import JobConfig


class TestSarahFirstScoreTypos:
    """Sarah is writing her first Mozart score. She's read the README,
    skimmed the guide, and is typing fast. Her mistakes are plausible
    near-misses from working memory, not random gibberish."""

    def test_retries_instead_of_retry(self) -> None:
        """Sarah writes 'retries:' because that's English. Mozart should
        tell her the correct field name, not just 'Extra inputs not permitted'."""
        score = {
            "name": "sarah-first-score",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Hello, world!"},
            "retries": {"max_retries": 3},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        # The error MUST mention "retries" is the problem
        assert "retries" in error_msg
        # And MUST mention extra_forbidden (not some other error)
        assert "extra_forbidden" in error_msg

        # The hint system must give Sarah actionable guidance
        hints = _schema_error_hints(error_msg)
        assert any("retry" in h for h in hints), (
            f"Hint should suggest 'retry' for typo 'retries', got: {hints}"
        )

    def test_paralel_instead_of_parallel(self) -> None:
        """Sarah misspells 'parallel' — common for non-native English speakers."""
        score = {
            "name": "sarah-fan-out",
            "sheet": {"size": 5, "total_items": 5},
            "prompt": {"template": "Analyze this"},
            "paralel": {"enabled": True},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        assert "paralel" in error_msg
        assert "extra_forbidden" in error_msg

        # The hint should suggest "parallel"
        hints = _schema_error_hints(error_msg)
        assert any("parallel" in h for h in hints), (
            f"Hint should suggest 'parallel' for typo 'paralel', got: {hints}"
        )

    def test_preamble_at_top_level(self) -> None:
        """Sarah puts 'preamble' at the top level instead of inside prompt:.
        She read an old example that mentioned preamble."""
        score = {
            "name": "sarah-preamble",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Do something"},
            "preamble": "You are a helpful assistant",
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "preamble" in str(exc_info.value)
        assert "extra_forbidden" in str(exc_info.value)


class TestMarcusMigratingFromOldVersion:
    """Marcus has an old score from before the instrument migration. He's
    updating fields but mixing old and new syntax."""

    def test_backend_type_instead_of_instrument(self) -> None:
        """Marcus writes 'backend_type: claude' — a plausible mix of
        old 'backend:' syntax and new 'instrument:' approach."""
        score = {
            "name": "marcus-migration",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Review this code"},
            "backend_type": "claude",
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "backend_type" in str(exc_info.value)
        assert "extra_forbidden" in str(exc_info.value)

    def test_max_retries_at_top_level(self) -> None:
        """Marcus puts max_retries at root instead of under retry:."""
        score = {
            "name": "marcus-retries",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Do something"},
            "max_retries": 5,
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "max_retries" in str(exc_info.value)


class TestPriyaUnimplementedFeatures:
    """Priya reads about features in issues or discussions that don't exist yet.
    She adds them to her score, and they silently do nothing — the worst kind of bug."""

    def test_instrument_priorities_not_silently_ignored(self) -> None:
        """Priya adds instrument_priorities from reading issue discussion.
        Without extra='forbid', this passes silently. With it, she gets
        a clear error telling her it doesn't exist yet."""
        score = {
            "name": "priya-priorities",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Analyze data"},
            "instrument": "claude-code",
            "instrument_priorities": {"gemini-cli": 1, "codex-cli": 2},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "instrument_priorities" in str(exc_info.value)

    def test_for_each_not_silently_ignored(self) -> None:
        """Priya tries to use a loop primitive that doesn't exist yet."""
        score = {
            "name": "priya-loop",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Process {{ item }}"},
            "for_each": ["item1", "item2", "item3"],
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "for_each" in str(exc_info.value)

    def test_repeat_until_not_silently_ignored(self) -> None:
        """Priya tries repeat_until from reading the Rosetta patterns."""
        score = {
            "name": "priya-repeat",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Iterate"},
            "repeat_until": "convergence",
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "repeat_until" in str(exc_info.value)


class TestLeoNestedFieldTypos:
    """Leo's score is complex — multiple nested configs. His typos are
    deeper in the YAML structure."""

    def test_typo_in_retry_field(self) -> None:
        """Leo writes 'max_attemps' (missing 't') in retry config."""
        score = {
            "name": "leo-retry-typo",
            "sheet": {"size": 3, "total_items": 3},
            "prompt": {"template": "Process this"},
            "retry": {"max_attemps": 5},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        # The error should surface the nested typo
        assert "max_attemps" in str(exc_info.value)

    def test_typo_in_stale_detection(self) -> None:
        """Leo writes 'timeout_seconds' instead of 'idle_timeout_seconds'."""
        score = {
            "name": "leo-stale-typo",
            "sheet": {"size": 3, "total_items": 3},
            "prompt": {"template": "Process this"},
            "stale_detection": {"timeout_seconds": 300},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "timeout_seconds" in str(exc_info.value)

    def test_typo_in_parallel_config(self) -> None:
        """Leo writes 'stager_delay_ms' instead of 'stagger_delay_ms'."""
        score = {
            "name": "leo-parallel-typo",
            "sheet": {"size": 10, "total_items": 10},
            "prompt": {"template": "Fan out"},
            "parallel": {"enabled": True, "stager_delay_ms": 150},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        assert "stager_delay_ms" in str(exc_info.value)


class TestSchemaErrorHintsForUnknownFields:
    """Test that _schema_error_hints provides useful guidance when
    extra='forbid' rejects unknown fields.

    The current fallback hint says "Ensure your score has name, sheet, prompt"
    which is useless when the user HAS those fields but also added one Mozart
    doesn't recognize."""

    def test_extra_forbidden_gets_specific_hint(self) -> None:
        """When the error contains 'extra_forbidden', the hint should NOT
        be the generic 'ensure you have name, sheet, prompt' message."""
        error_msg = (
            "1 validation error for JobConfig\n"
            "retries\n"
            "  Extra inputs are not permitted "
            "[type=extra_forbidden, input_value={'max_retries': 3}, input_type=dict]"
        )
        hints = _schema_error_hints(error_msg)
        # Must NOT be the generic fallback
        assert not any(
            "ensure your score has at minimum" in h.lower() for h in hints
        ), f"Got generic fallback hint instead of specific unknown field hint: {hints}"
        # Must suggest the correct field name
        assert any("retry" in h for h in hints), (
            f"Should suggest 'retry' for typo 'retries', got: {hints}"
        )

    def test_nested_extra_forbidden_surfaces_field_name(self) -> None:
        """The error message must include the rejected field name so the
        user knows exactly what to fix."""
        score = {
            "name": "test",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "retry": {"max_attemps": 5, "backoff": "linear"},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        # Both typos should be visible in the error
        assert "max_attemps" in error_msg
        assert "backoff" in error_msg


class TestYAMLFileValidateIntegration:
    """End-to-end: write a YAML file, parse it, validate it — the full
    path a score author takes."""

    def test_yaml_with_unknown_field_fails_jobconfig_parse(self) -> None:
        """A YAML file with unknown fields fails at JobConfig parsing,
        not at validate checks. The user sees the error early."""
        score_content = """\
name: integration-test
sheet:
  size: 3
  total_items: 3
prompt:
  template: "Process item {{ item_num }}"
instrument: claude-code
timeout: 300
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(score_content)
            f.flush()
            score_path = Path(f.name)

        try:
            parsed = yaml.safe_load(score_path.read_text())
            with pytest.raises(Exception) as exc_info:
                JobConfig(**parsed)

            assert "timeout" in str(exc_info.value)
            assert "extra_forbidden" in str(exc_info.value)
        finally:
            score_path.unlink()

    def test_yaml_with_only_valid_fields_parses_clean(self) -> None:
        """Positive test: a correct score YAML parses without errors."""
        score_content = """\
name: clean-score
sheet:
  size: 3
  total_items: 3
prompt:
  template: "Process item {{ item_num }}"
instrument: claude-code
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(score_content)
            f.flush()
            score_path = Path(f.name)

        try:
            parsed = yaml.safe_load(score_path.read_text())
            # Should NOT raise
            config = JobConfig(**parsed)
            assert config.name == "clean-score"
            # total_sheets is computed: ceil(total_items/size) = ceil(3/3) = 1
            assert config.sheet.total_sheets == 1
            assert config.sheet.size == 3
        finally:
            score_path.unlink()

    def test_multiple_errors_all_reported_at_once(self) -> None:
        """A score with multiple unknown fields reports ALL of them,
        not just the first one. The user can fix everything in one pass."""
        score = {
            "name": "multi-error",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "timeout": 300,
            "retries": 5,
            "backend_type": "claude",
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        # All three unknown fields should appear
        assert "timeout" in error_msg
        assert "retries" in error_msg
        assert "backend_type" in error_msg


class TestAlexInstrumentTypos:
    """Alex just installed Mozart and is writing their first score from memory
    after reading the README. 'instrument' has two common transposition typos."""

    def test_insturment_instead_of_instrument(self) -> None:
        """Alex transposes 'str' → 'stur' — common fast-typing error."""
        score = {
            "name": "alex-typo",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Hello"},
            "insturment": "claude-code",
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        assert "insturment" in error_msg
        assert "extra_forbidden" in error_msg

        hints = _schema_error_hints(error_msg)
        assert any("instrument" in h and "did you mean" in h.lower() for h in hints), (
            f"Should suggest 'instrument' for typo 'insturment', got: {hints}"
        )

    def test_insturment_config_instead_of_instrument_config(self) -> None:
        """Alex makes the same transposition in the config key."""
        score = {
            "name": "alex-config-typo",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Hello"},
            "insturment_config": {"timeout_seconds": 100},
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        assert "insturment_config" in error_msg

        hints = _schema_error_hints(error_msg)
        assert any("instrument_config" in h and "did you mean" in h.lower() for h in hints), (
            f"Should suggest 'instrument_config' for 'insturment_config', got: {hints}"
        )

    def test_instrumnet_instead_of_instrument(self) -> None:
        """Alex transposes 'ment' → 'mnet' — another common fast-typing error."""
        score = {
            "name": "alex-typo-2",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Hello"},
            "instrumnet": "claude-code",
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        assert "instrumnet" in error_msg

        hints = _schema_error_hints(error_msg)
        assert any("instrument" in h and "did you mean" in h.lower() for h in hints), (
            f"Should suggest 'instrument' for typo 'instrumnet', got: {hints}"
        )

    def test_validation_instead_of_validations(self) -> None:
        """Alex writes singular 'validation' — natural English intuition."""
        score = {
            "name": "alex-singular",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Hello"},
            "validation": [{"type": "file_exists", "path": "{workspace}/out.md"}],
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        assert "validation" in error_msg

        hints = _schema_error_hints(error_msg)
        assert any("validations" in h and "did you mean" in h.lower() for h in hints), (
            f"Should suggest 'validations' for 'validation', got: {hints}"
        )

    def test_notification_instead_of_notifications(self) -> None:
        """Alex writes singular 'notification' — same pattern."""
        score = {
            "name": "alex-singular-2",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Hello"},
            "notification": [{"type": "desktop"}],
        }
        with pytest.raises(Exception) as exc_info:
            JobConfig(**score)

        error_msg = str(exc_info.value)
        assert "notification" in error_msg

        hints = _schema_error_hints(error_msg)
        assert any("notifications" in h and "did you mean" in h.lower() for h in hints), (
            f"Should suggest 'notifications' for 'notification', got: {hints}"
        )
