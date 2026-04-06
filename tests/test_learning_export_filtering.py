"""Tests for learning export command filtering logic.

This module tests the fixes to the `mozart learning-export` command that
ensure correct pattern retrieval and filtering:

1. Case sensitivity fix: Use PatternType.SEMANTIC_INSIGHT.value instead of
   string literal "SEMANTIC_INSIGHT"
2. PENDING pattern filtering: Add --include-pending flag (default True)
3. Effectiveness filtering: Add --min-effectiveness parameter (default 0.0)
4. Pattern health reporting: Show PENDING patterns in pattern-health.md

Covers:
- Pattern type case sensitivity (uppercase vs lowercase)
- Include/exclude PENDING quarantine patterns
- Effectiveness threshold filtering
- Correct pattern counts in exported files
- Filter documentation in export headers
"""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from marianne.learning.patterns import PatternType
from marianne.learning.store.models import QuarantineStatus


class TestPatternTypeCase:
    """Test that pattern type filtering is case-insensitive and uses enum values."""

    def test_semantic_insight_enum_value_is_lowercase(self):
        """Verify that PatternType.SEMANTIC_INSIGHT.value is lowercase."""
        assert PatternType.SEMANTIC_INSIGHT.value == "semantic_insight"

    @patch("marianne.learning.global_store.get_global_store")
    def test_export_uses_enum_value_not_uppercase_string(self, mock_store_factory):
        """Export should use PatternType.SEMANTIC_INSIGHT.value, not 'SEMANTIC_INSIGHT'."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        mock_store.get_patterns.return_value = []
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            learning_export(output_dir="/tmp/test-export")

        # Verify get_patterns was called with lowercase pattern type
        calls = [
            call
            for call in mock_store.get_patterns.call_args_list
            if call[1].get("pattern_type") is not None
        ]
        assert len(calls) == 1
        assert calls[0][1]["pattern_type"] == "semantic_insight"


class TestPendingPatternFiltering:
    """Test --include-pending flag behavior."""

    def _create_mock_pattern(
        self,
        pattern_id: str,
        pattern_name: str,
        quarantine_status: QuarantineStatus,
        effectiveness: float = 0.5,
    ):
        """Helper to create a mock pattern with required attributes."""
        p = Mock()
        p.id = pattern_id
        p.pattern_name = pattern_name
        p.pattern_type = PatternType.SEMANTIC_INSIGHT.value
        p.description = "Test pattern"
        p.effectiveness_score = effectiveness
        p.trust_score = 0.5
        p.occurrence_count = 1
        p.quarantine_status = quarantine_status
        p.variance = 0.0
        p.context_tags = []
        return p

    @patch("marianne.learning.global_store.get_global_store")
    def test_include_pending_true_exports_pending_patterns(self, mock_store_factory):
        """When include_pending=True (default), PENDING patterns should be exported."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        patterns = [
            self._create_mock_pattern(
                "p1", "Pattern 1", QuarantineStatus.PENDING, 0.5
            ),
            self._create_mock_pattern(
                "p2", "Pattern 2", QuarantineStatus.PENDING, 0.5
            ),
        ]
        mock_store.get_patterns.return_value = patterns
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test", include_pending=True)

                # Check semantic-insights.md was written with patterns
                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "Pattern 1" in content
                assert "Pattern 2" in content

    @patch("marianne.learning.global_store.get_global_store")
    def test_include_pending_false_excludes_pending_patterns(self, mock_store_factory):
        """When include_pending=False, PENDING patterns should be filtered out."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        patterns = [
            self._create_mock_pattern(
                "p1", "Pattern 1", QuarantineStatus.PENDING, 0.5
            ),
            self._create_mock_pattern(
                "p2", "Pattern 2", QuarantineStatus.PENDING, 0.5
            ),
        ]
        mock_store.get_patterns.return_value = patterns
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test", include_pending=False)

                # Check semantic-insights.md shows no patterns
                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "No semantic insights found" in content


class TestEffectivenessFiltering:
    """Test --min-effectiveness parameter behavior."""

    def _create_mock_pattern(
        self,
        pattern_id: str,
        effectiveness: float,
        quarantine_status: QuarantineStatus = QuarantineStatus.PENDING,
    ):
        """Helper to create a mock pattern."""
        p = Mock()
        p.id = pattern_id
        p.pattern_name = f"Pattern {pattern_id}"
        p.pattern_type = PatternType.SEMANTIC_INSIGHT.value
        p.description = "Test pattern"
        p.effectiveness_score = effectiveness
        p.trust_score = 0.5
        p.occurrence_count = 1
        p.quarantine_status = quarantine_status
        p.variance = 0.0
        p.context_tags = []
        return p

    @patch("marianne.learning.global_store.get_global_store")
    def test_min_effectiveness_filters_low_effectiveness_patterns(
        self, mock_store_factory
    ):
        """Patterns below min_effectiveness should be filtered out."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        patterns = [
            self._create_mock_pattern("p1", 0.4),
            self._create_mock_pattern("p2", 0.6),
            self._create_mock_pattern("p3", 0.8),
        ]
        mock_store.get_patterns.return_value = patterns
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test", min_effectiveness=0.6)

                # Check semantic-insights.md only has patterns >= 0.6
                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "Pattern p1" not in content  # 0.4 < 0.6
                assert "Pattern p2" in content  # 0.6 >= 0.6
                assert "Pattern p3" in content  # 0.8 >= 0.6

    @patch("marianne.learning.global_store.get_global_store")
    def test_min_effectiveness_zero_includes_all_patterns(self, mock_store_factory):
        """When min_effectiveness=0.0 (default), all patterns should be included."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        patterns = [
            self._create_mock_pattern("p1", 0.0),
            self._create_mock_pattern("p2", 0.5),
            self._create_mock_pattern("p3", 1.0),
        ]
        mock_store.get_patterns.return_value = patterns
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test", min_effectiveness=0.0)

                # Check semantic-insights.md has all patterns
                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "Pattern p1" in content
                assert "Pattern p2" in content
                assert "Pattern p3" in content


class TestFilterDocumentation:
    """Test that export file headers document applied filters."""

    @patch("marianne.learning.global_store.get_global_store")
    def test_no_filters_shows_no_filters_applied(self, mock_store_factory):
        """When no filters are applied, header should say so."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        mock_store.get_patterns.return_value = []
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(
                    output_dir="/tmp/test", include_pending=True, min_effectiveness=0.0
                )

                # Check headers
                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "No filters applied" in content

    @patch("marianne.learning.global_store.get_global_store")
    def test_pending_filter_documented_in_header(self, mock_store_factory):
        """When --no-include-pending is used, header should document it."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        mock_store.get_patterns.return_value = []
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test", include_pending=False)

                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "excluding PENDING patterns" in content

    @patch("marianne.learning.global_store.get_global_store")
    def test_effectiveness_filter_documented_in_header(self, mock_store_factory):
        """When --min-effectiveness is set, header should document it."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        mock_store.get_patterns.return_value = []
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test", min_effectiveness=0.6)

                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]
                assert "min_effectiveness >= 60.0%" in content


class TestPatternHealthPendingReporting:
    """Test that pattern-health.md correctly reports PENDING patterns."""

    def _create_mock_pattern(
        self,
        pattern_id: str,
        quarantine_status: QuarantineStatus,
        trust: float = 0.5,
        variance: float = 0.0,
        occurrences: int = 1,
    ):
        """Helper to create a mock pattern."""
        p = Mock()
        p.id = pattern_id
        p.pattern_name = f"Pattern {pattern_id}"
        p.pattern_type = PatternType.SEMANTIC_INSIGHT.value
        p.description = "Test pattern"
        p.effectiveness_score = 0.5
        p.trust_score = trust
        p.occurrence_count = occurrences
        p.quarantine_status = quarantine_status
        p.variance = variance
        p.context_tags = []
        return p

    @patch("marianne.learning.global_store.get_global_store")
    def test_pending_patterns_shown_in_health_report(self, mock_store_factory):
        """Pattern-health.md should have a 'Pending Validation Patterns' section."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        patterns = [
            self._create_mock_pattern("p1", QuarantineStatus.PENDING),
            self._create_mock_pattern("p2", QuarantineStatus.PENDING),
            self._create_mock_pattern("p3", QuarantineStatus.QUARANTINED),
        ]
        mock_store.get_patterns.side_effect = [
            [],  # semantic insights call
            patterns,  # pattern-health call
        ]
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(output_dir="/tmp/test")

                # Find pattern-health.md call
                health_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "pattern-health.md" in str(call[0][0])
                ]
                assert len(health_calls) == 1
                content = health_calls[0][0][1]

                # Should have sections for both quarantined and pending
                assert "## Quarantined Patterns (1)" in content
                assert "## Pending Validation Patterns (2)" in content
                assert "Pattern p1" in content
                assert "Pattern p2" in content

    @patch("marianne.learning.global_store.get_global_store")
    def test_quarantine_status_enum_comparison(self, mock_store_factory):
        """Pattern health should compare against QuarantineStatus enum, not strings."""
        from marianne.cli.commands.learning._export import _format_markdown_health

        patterns = [
            self._create_mock_pattern("p1", QuarantineStatus.PENDING),
            self._create_mock_pattern("p2", QuarantineStatus.QUARANTINED),
        ]

        content = _format_markdown_health(patterns, "")

        # Verify counts are correct (not 0 due to string comparison bug)
        assert "## Quarantined Patterns (1)" in content
        assert "## Pending Validation Patterns (1)" in content


class TestFilterCombinations:
    """Test combinations of filters working together."""

    def _create_mock_pattern(
        self,
        pattern_id: str,
        effectiveness: float,
        quarantine_status: QuarantineStatus,
    ):
        """Helper to create a mock pattern."""
        p = Mock()
        p.id = pattern_id
        p.pattern_name = f"Pattern {pattern_id}"
        p.pattern_type = PatternType.SEMANTIC_INSIGHT.value
        p.description = "Test pattern"
        p.effectiveness_score = effectiveness
        p.trust_score = 0.5
        p.occurrence_count = 1
        p.quarantine_status = quarantine_status
        p.variance = 0.0
        p.context_tags = []
        return p

    @patch("marianne.learning.global_store.get_global_store")
    def test_both_filters_applied_together(self, mock_store_factory):
        """Both --no-include-pending and --min-effectiveness should filter correctly."""
        from marianne.cli.commands.learning._export import learning_export

        mock_store = Mock()
        patterns = [
            self._create_mock_pattern("p1", 0.4, QuarantineStatus.PENDING),
            self._create_mock_pattern("p2", 0.7, QuarantineStatus.PENDING),
            self._create_mock_pattern("p3", 0.8, QuarantineStatus.VALIDATED),
        ]
        mock_store.get_patterns.return_value = patterns
        mock_store.get_drifting_patterns.return_value = []
        mock_store.get_epistemic_drifting_patterns.return_value = []
        mock_store.calculate_pattern_entropy.return_value = Mock(
            shannon_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            dominant_pattern_share=0.0,
        )
        mock_store.get_entropy_response_history.return_value = []
        mock_store.get_trajectory.return_value = []
        mock_store.get_execution_stats.return_value = {}
        mock_store_factory.return_value = mock_store

        with patch("marianne.cli.commands.learning._export.console"):
            with patch("marianne.cli.commands.learning._export._write_file") as mock_write:
                learning_export(
                    output_dir="/tmp/test",
                    include_pending=False,
                    min_effectiveness=0.6,
                )

                semantic_calls = [
                    call
                    for call in mock_write.call_args_list
                    if "semantic-insights.md" in str(call[0][0])
                ]
                assert len(semantic_calls) == 1
                content = semantic_calls[0][0][1]

                # p1: excluded (PENDING + low effectiveness)
                # p2: excluded (PENDING, even though eff >= 0.6)
                # p3: included (VALIDATED + eff >= 0.6)
                assert "Pattern p1" not in content
                assert "Pattern p2" not in content
                assert "Pattern p3" in content

                # Header should document both filters
                assert "excluding PENDING patterns" in content
                assert "min_effectiveness >= 60.0%" in content
