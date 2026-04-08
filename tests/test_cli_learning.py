"""Tests for CLI learning commands.

Covers the 10 learning subcommands: learning-stats, learning-insights,
learning-activity, patterns-list, patterns-why, patterns-budget,
patterns-entropy, entropy-status, learning-drift, learning-epistemic-drift.

All commands use deferred imports (inside function body), so we patch
at the source module: marianne.learning.global_store.get_global_store

GH#82 — Extended coverage for Q003 (budget), Q004 (entropy), Q005 (drift),
Q006 (patterns) covering history mode, JSON output, color thresholds, and
edge cases.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()

# Patch target for get_global_store (deferred import at source)
_GS_PATCH = "marianne.learning.global_store.get_global_store"
# Patch target for GlobalLearningStore class (used directly in learning-insights)
_GS_CLS_PATCH = "marianne.learning.global_store.GlobalLearningStore"
# Patch target for check_migration_status
_MIGRATION_PATCH = "marianne.learning.migration.check_migration_status"


def _make_mock_store() -> MagicMock:
    """Create a mock GlobalLearningStore with common return values."""
    store = MagicMock()
    store.get_execution_stats.return_value = {
        "total_executions": 42,
        "success_without_retry_rate": 0.85,
        "total_patterns": 7,
        "avg_pattern_effectiveness": 0.72,
        "unique_workspaces": 3,
        "total_error_recoveries": 5,
        "avg_recovery_success_rate": 0.80,
    }
    store.get_patterns.return_value = []
    store.get_optimal_execution_window.return_value = {
        "optimal_hours": [9, 10, 14],
        "avoid_hours": [2, 3],
        "confidence": 0.75,
    }
    store.get_similar_executions.return_value = []
    store.get_drifting_patterns.return_value = []
    store.get_pattern_drift_summary.return_value = {}
    store.get_epistemic_drifting_patterns.return_value = []
    store.get_epistemic_drift_summary.return_value = {}
    # Return None so the command shows "no records" message (avoids comparison ops)
    store.get_exploration_budget.return_value = None
    store.get_exploration_budget_history.return_value = []
    store.get_exploration_budget_statistics.return_value = {
        "avg_budget": 0.0,
        "min_budget": 0.0,
        "max_budget": 0.0,
        "total_adjustments": 0,
        "floor_enforcements": 0,
        "boost_count": 0,
        "decay_count": 0,
    }
    store.calculate_pattern_entropy.return_value = MagicMock(
        calculated_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        shannon_entropy=2.5,
        max_possible_entropy=3.0,
        diversity_index=0.83,
        unique_pattern_count=7,
        effective_pattern_count=5,
        total_applications=42,
        dominant_pattern_share=0.35,
        threshold_exceeded=False,
    )
    store.get_pattern_entropy_history.return_value = []
    store.check_entropy_response_needed.return_value = False
    store.get_entropy_response_history.return_value = []
    store.get_entropy_response_statistics.return_value = {
        "total_responses": 0,
        "avg_entropy_at_trigger": 0.0,
        "budget_boosts": 0,
        "quarantine_revisits": 0,
        "last_response": None,
    }
    store.get_last_entropy_response.return_value = None
    return store


class TestLearningStatsCommand:
    """Tests for the learning-stats command."""

    @patch(_MIGRATION_PATCH)
    @patch(_GS_PATCH)
    def test_learning_stats_human_output(self, mock_get_store, mock_migration):
        """learning-stats shows human-readable statistics."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store
        mock_migration.return_value = {"pending_workspaces": 0, "migrated": True}

        result = runner.invoke(app, ["learning-stats"])

        assert result.exit_code == 0
        assert "42" in result.stdout  # total_executions

    @patch(_MIGRATION_PATCH)
    @patch(_GS_PATCH)
    def test_learning_stats_json_output(self, mock_get_store, mock_migration):
        """learning-stats --json returns valid JSON."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store
        mock_migration.return_value = {"pending_workspaces": 0, "migrated": True}

        result = runner.invoke(app, ["learning-stats", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "executions" in data
        assert data["executions"]["total"] == 42


class TestLearningInsightsCommand:
    """Tests for the learning-insights command."""

    @patch(_GS_CLS_PATCH)
    def test_insights_no_patterns(self, mock_cls):
        """learning-insights shows message when no patterns exist."""
        mock_store = MagicMock()
        mock_store.get_patterns.return_value = []
        mock_cls.return_value = mock_store

        result = runner.invoke(app, ["learning-insights"])

        assert result.exit_code == 0
        assert "No patterns" in result.stdout

    @patch(_GS_CLS_PATCH)
    def test_insights_with_patterns(self, mock_cls):
        """learning-insights shows pattern table with data."""
        pattern = MagicMock()
        pattern.pattern_type = "output_pattern"
        pattern.description = "Test pattern description"
        pattern.occurrence_count = 5
        pattern.effectiveness_score = 0.85

        mock_store = MagicMock()
        mock_store.get_patterns.return_value = [pattern]
        mock_cls.return_value = mock_store

        result = runner.invoke(app, ["learning-insights"])

        assert result.exit_code == 0
        assert "output_pattern" in result.stdout


class TestLearningActivityCommand:
    """Tests for the learning-activity command."""

    @patch(_GS_PATCH)
    def test_activity_default_hours(self, mock_get_store):
        """learning-activity shows recent activity."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-activity"])

        assert result.exit_code == 0

    @patch(_GS_PATCH)
    def test_activity_json_output(self, mock_get_store):
        """learning-activity --json returns valid JSON."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-activity", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)


class TestPatternsListCommand:
    """Tests for the patterns-list command (Q006 — GH#82)."""

    @patch(_GS_PATCH)
    def test_patterns_list_empty(self, mock_get_store):
        """patterns-list shows message when no patterns."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list"])

        assert result.exit_code == 0
        assert "No patterns" in result.stdout

    @patch(_GS_PATCH)
    def test_patterns_list_with_data(self, mock_get_store):
        """patterns-list shows patterns in table."""
        pattern = MagicMock()
        pattern.id = "pat-001abcdef"
        pattern.pattern_type = "retry_pattern"
        pattern.pattern_name = "Test Retry"
        pattern.description = "A test retry pattern"
        pattern.occurrence_count = 10
        pattern.effectiveness_score = 0.9
        pattern.priority_score = 0.85
        pattern.context_tags = {"test"}
        pattern.quarantine_status = MagicMock(value="validated")
        pattern.trust_score = 0.95

        mock_store = MagicMock()
        mock_store.get_patterns.return_value = [pattern]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list"])

        assert result.exit_code == 0
        assert "1 pattern" in result.stdout

    @patch(_GS_PATCH)
    def test_patterns_list_auto_eligible(self, mock_get_store):
        """Shows auto-eligible marker for high-trust validated patterns."""
        pattern = MagicMock()
        pattern.id = "pat-002abcdef"
        pattern.pattern_type = "retry_pattern"
        pattern.pattern_name = "Auto Apply"
        pattern.description = ""
        pattern.occurrence_count = 20
        pattern.effectiveness_score = 0.95
        pattern.priority_score = 0.90
        pattern.context_tags = set()
        pattern.quarantine_status = MagicMock(value="validated")
        pattern.trust_score = 0.90

        mock_store = MagicMock()
        mock_store.get_patterns.return_value = [pattern]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list"])

        assert result.exit_code == 0

    @patch(_GS_PATCH)
    def test_patterns_list_various_statuses(self, mock_get_store):
        """Renders different quarantine statuses with correct styling."""
        patterns = []
        for status in ["pending", "quarantined", "validated", "retired"]:
            p = MagicMock()
            p.id = f"pat-{status[:3]}abcdef"
            p.pattern_type = "test"
            p.pattern_name = f"Pattern {status}"
            p.description = ""
            p.occurrence_count = 5
            p.effectiveness_score = 0.5
            p.priority_score = 0.5
            p.context_tags = set()
            p.quarantine_status = MagicMock(value=status)
            p.trust_score = 0.5
            patterns.append(p)

        mock_store = MagicMock()
        mock_store.get_patterns.return_value = patterns
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list"])

        assert result.exit_code == 0
        assert "4 pattern" in result.stdout

    @patch(_GS_PATCH)
    def test_patterns_list_json_output(self, mock_get_store):
        """patterns-list --json returns structured array."""
        pattern = MagicMock()
        pattern.id = "pat-001"
        pattern.pattern_type = "retry_pattern"
        pattern.pattern_name = "Test Retry"
        pattern.description = "A test"
        pattern.occurrence_count = 10
        pattern.effectiveness_score = 0.9
        pattern.priority_score = 0.85
        pattern.context_tags = {"test", "retry"}
        pattern.quarantine_status = MagicMock(value="validated")
        pattern.trust_score = 0.95

        mock_store = MagicMock()
        mock_store.get_patterns.return_value = [pattern]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["id"] == "pat-001"
        assert data[0]["trust_score"] == 0.95

    @patch(_GS_PATCH)
    def test_patterns_list_quarantined_filter(self, mock_get_store):
        """patterns-list --quarantined passes filter to store."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list", "--quarantined"])

        assert result.exit_code == 0
        # Verify quarantine_status was passed
        call_kwargs = mock_store.get_patterns.call_args
        assert "quarantine_status" in call_kwargs.kwargs

    @patch(_GS_PATCH)
    def test_patterns_list_high_trust_filter(self, mock_get_store):
        """patterns-list --high-trust passes min_trust to store."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list", "--high-trust"])

        assert result.exit_code == 0
        call_kwargs = mock_store.get_patterns.call_args
        assert call_kwargs.kwargs.get("min_trust") == 0.7

    @patch(_GS_PATCH)
    def test_patterns_list_low_trust_filter(self, mock_get_store):
        """patterns-list --low-trust passes max_trust to store."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list", "--low-trust"])

        assert result.exit_code == 0
        call_kwargs = mock_store.get_patterns.call_args
        assert call_kwargs.kwargs.get("max_trust") == 0.3

    @patch(_GS_PATCH)
    def test_patterns_list_min_priority(self, mock_get_store):
        """patterns-list --min-priority passes threshold to store."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-list", "--min-priority", "0.5"])

        assert result.exit_code == 0
        call_kwargs = mock_store.get_patterns.call_args
        assert call_kwargs.kwargs.get("min_priority") == 0.5


class TestPatternsBudgetCommand:
    """Tests for the patterns-budget command (Q003 — GH#82)."""

    @patch(_GS_PATCH)
    def test_budget_no_records(self, mock_get_store):
        """patterns-budget shows hint when no budget records exist."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget"])

        assert result.exit_code == 0
        assert "No budget records" in result.stdout

    @patch(_GS_PATCH)
    def test_budget_current_status_low(self, mock_get_store):
        """Budget value <= 0.10 renders with 'Low' status."""
        mock_store = _make_mock_store()
        current = MagicMock(
            budget_value=0.08,
            entropy_at_time=0.25,
            adjustment_type="floor_enforced",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_exploration_budget.return_value = current
        mock_store.get_exploration_budget_statistics.return_value = {
            "avg_budget": 0.10,
            "min_budget": 0.05,
            "max_budget": 0.15,
            "total_adjustments": 5,
            "floor_enforcements": 3,
            "boost_count": 1,
            "decay_count": 1,
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget"])

        assert result.exit_code == 0
        assert "Low" in result.stdout
        assert "floor" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_budget_current_status_high(self, mock_get_store):
        """Budget value >= 0.30 renders with 'High' status."""
        mock_store = _make_mock_store()
        current = MagicMock(
            budget_value=0.45,
            entropy_at_time=0.15,
            adjustment_type="boost",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_exploration_budget.return_value = current
        mock_store.get_exploration_budget_statistics.return_value = {
            "avg_budget": 0.30,
            "min_budget": 0.10,
            "max_budget": 0.45,
            "total_adjustments": 8,
            "floor_enforcements": 1,
            "boost_count": 5,
            "decay_count": 2,
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget"])

        assert result.exit_code == 0
        assert "High" in result.stdout

    @patch(_GS_PATCH)
    def test_budget_current_status_normal(self, mock_get_store):
        """Budget value between 0.10 and 0.30 renders 'Normal'."""
        mock_store = _make_mock_store()
        current = MagicMock(
            budget_value=0.20,
            entropy_at_time=None,
            adjustment_type="decay",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_exploration_budget.return_value = current
        mock_store.get_exploration_budget_statistics.return_value = {
            "avg_budget": 0.18,
            "min_budget": 0.10,
            "max_budget": 0.25,
            "total_adjustments": 3,
            "floor_enforcements": 0,
            "boost_count": 1,
            "decay_count": 2,
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget"])

        assert result.exit_code == 0
        assert "Normal" in result.stdout
        assert "adjusting normally" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_budget_frequent_boosts_hint(self, mock_get_store):
        """Shows hint when boosts exceed 2x decays."""
        mock_store = _make_mock_store()
        current = MagicMock(
            budget_value=0.20,
            entropy_at_time=0.5,
            adjustment_type="boost",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_exploration_budget.return_value = current
        mock_store.get_exploration_budget_statistics.return_value = {
            "avg_budget": 0.20,
            "min_budget": 0.10,
            "max_budget": 0.35,
            "total_adjustments": 10,
            "floor_enforcements": 0,
            "boost_count": 7,
            "decay_count": 3,
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget"])

        assert result.exit_code == 0
        assert "Frequent boosts" in result.stdout

    @patch(_GS_PATCH)
    def test_budget_json_output_with_data(self, mock_get_store):
        """Budget --json returns structured data with current and statistics."""
        mock_store = _make_mock_store()
        current = MagicMock(
            budget_value=0.20,
            entropy_at_time=0.6,
            adjustment_type="decay",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_exploration_budget.return_value = current
        mock_store.get_exploration_budget_statistics.return_value = {
            "avg_budget": 0.18,
            "min_budget": 0.10,
            "max_budget": 0.25,
            "total_adjustments": 3,
            "floor_enforcements": 0,
            "boost_count": 1,
            "decay_count": 2,
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["current"]["budget_value"] == 0.2
        assert data["statistics"]["total_adjustments"] == 3

    @patch(_GS_PATCH)
    def test_budget_json_output_no_data(self, mock_get_store):
        """Budget --json returns null current when no records."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["current"]["budget_value"] is None

    @patch(_GS_PATCH)
    def test_budget_history_empty(self, mock_get_store):
        """Budget --history shows hint when no history."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--history"])

        assert result.exit_code == 0
        assert "No budget history" in result.stdout

    @patch(_GS_PATCH)
    def test_budget_history_with_records(self, mock_get_store):
        """Budget --history renders table with records."""
        mock_store = _make_mock_store()
        record = MagicMock(
            id="rec-001",
            job_hash="abc123",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            budget_value=0.20,
            entropy_at_time=0.5,
            adjustment_type="boost",
            adjustment_reason="Low entropy detected",
        )
        mock_store.get_exploration_budget_history.return_value = [record]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--history"])

        assert result.exit_code == 0
        assert "History" in result.stdout
        assert "1 record" in result.stdout

    @patch(_GS_PATCH)
    def test_budget_history_json(self, mock_get_store):
        """Budget --history --json returns array of records."""
        mock_store = _make_mock_store()
        record = MagicMock(
            id="rec-001",
            job_hash="abc123",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            budget_value=0.20,
            entropy_at_time=0.5,
            adjustment_type="boost",
            adjustment_reason="Low entropy detected",
        )
        mock_store.get_exploration_budget_history.return_value = [record]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--history", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["adjustment_type"] == "boost"

    @patch(_GS_PATCH)
    def test_budget_history_color_thresholds(self, mock_get_store):
        """Budget history uses yellow for low, cyan for high values."""
        mock_store = _make_mock_store()
        records = [
            MagicMock(
                id="r1", job_hash="j1",
                recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
                budget_value=0.05, entropy_at_time=0.2,
                adjustment_type="floor_enforced", adjustment_reason="Floor hit",
            ),
            MagicMock(
                id="r2", job_hash="j1",
                recorded_at=datetime(2026, 1, 15, 11, 0, tzinfo=UTC),
                budget_value=0.40, entropy_at_time=None,
                adjustment_type="boost", adjustment_reason="Boost applied",
            ),
        ]
        mock_store.get_exploration_budget_history.return_value = records
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--history"])

        assert result.exit_code == 0
        assert "2 record" in result.stdout

    @patch(_GS_PATCH)
    def test_budget_job_filter(self, mock_get_store):
        """Budget --job filters by job hash."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-budget", "--job", "abc123"])

        assert result.exit_code == 0
        mock_store.get_exploration_budget.assert_called_once_with(job_hash="abc123")


class TestPatternsEntropyCommand:
    """Tests for the patterns-entropy command (Q004 — GH#82)."""

    @patch(_GS_PATCH)
    def test_entropy_shows_metrics(self, mock_get_store):
        """patterns-entropy shows entropy metrics when data exists."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy"])

        assert result.exit_code == 0
        assert "Shannon Entropy" in result.stdout
        assert "Diversity Index" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_no_applications(self, mock_get_store):
        """Shows hint when no pattern applications yet."""
        mock_store = _make_mock_store()
        mock_store.calculate_pattern_entropy.return_value = MagicMock(
            shannon_entropy=0.0,
            max_possible_entropy=0.0,
            diversity_index=0.0,
            unique_pattern_count=0,
            effective_pattern_count=0,
            total_applications=0,
            dominant_pattern_share=0.0,
            threshold_exceeded=False,
        )
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy"])

        assert result.exit_code == 0
        assert "No pattern applications" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_low_diversity_alert(self, mock_get_store):
        """Shows LOW DIVERSITY ALERT when below threshold."""
        mock_store = _make_mock_store()
        mock_store.calculate_pattern_entropy.return_value = MagicMock(
            shannon_entropy=0.5,
            max_possible_entropy=3.0,
            diversity_index=0.17,
            unique_pattern_count=5,
            effective_pattern_count=2,
            total_applications=20,
            dominant_pattern_share=0.70,
            threshold_exceeded=False,
        )
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--threshold", "0.5"])

        assert result.exit_code == 0
        assert "low diversity" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_entropy_dominant_pattern_warning(self, mock_get_store):
        """Shows warning when a single pattern holds >50% but diversity above threshold."""
        mock_store = _make_mock_store()
        mock_store.calculate_pattern_entropy.return_value = MagicMock(
            shannon_entropy=1.5,
            max_possible_entropy=3.0,
            diversity_index=0.55,
            unique_pattern_count=5,
            effective_pattern_count=3,
            total_applications=30,
            dominant_pattern_share=0.55,
            threshold_exceeded=False,
        )
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy"])

        assert result.exit_code == 0
        assert ">50%" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_healthy_diversity(self, mock_get_store):
        """Shows healthy message when diversity is good."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy"])

        assert result.exit_code == 0
        assert "Healthy" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_json_output(self, mock_get_store):
        """Entropy --json returns structured data."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["shannon_entropy"] == 2.5
        assert data["diversity_index"] == 0.83
        assert "alert_threshold" in data

    @patch(_GS_PATCH)
    def test_entropy_record_flag(self, mock_get_store):
        """Entropy --record triggers snapshot recording."""
        mock_store = _make_mock_store()
        mock_store.record_pattern_entropy.return_value = "snap-abc123"
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--record"])

        assert result.exit_code == 0
        assert "Recorded" in result.stdout
        mock_store.record_pattern_entropy.assert_called_once()

    @patch(_GS_PATCH)
    def test_entropy_history_empty(self, mock_get_store):
        """Entropy --history shows hint when empty."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--history"])

        assert result.exit_code == 0
        assert "No entropy history" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_history_with_records(self, mock_get_store):
        """Entropy --history renders table with records."""
        mock_store = _make_mock_store()
        record = MagicMock(
            calculated_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            shannon_entropy=2.5,
            max_possible_entropy=3.0,
            diversity_index=0.83,
            unique_pattern_count=7,
            effective_pattern_count=5,
            total_applications=42,
            dominant_pattern_share=0.25,
            threshold_exceeded=False,
        )
        mock_store.get_pattern_entropy_history.return_value = [record]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--history"])

        assert result.exit_code == 0
        assert "1 record" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_history_json(self, mock_get_store):
        """Entropy --history --json returns array of records."""
        mock_store = _make_mock_store()
        record = MagicMock(
            calculated_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            shannon_entropy=2.5,
            max_possible_entropy=3.0,
            diversity_index=0.83,
            unique_pattern_count=7,
            effective_pattern_count=5,
            total_applications=42,
            dominant_pattern_share=0.25,
            threshold_exceeded=False,
        )
        mock_store.get_pattern_entropy_history.return_value = [record]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--history", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["shannon_entropy"] == 2.5

    @patch(_GS_PATCH)
    def test_entropy_history_dominant_color_thresholds(self, mock_get_store):
        """History table applies color thresholds for dominant share."""
        mock_store = _make_mock_store()
        records = [
            MagicMock(
                calculated_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
                shannon_entropy=1.0, max_possible_entropy=3.0,
                diversity_index=0.3, unique_pattern_count=3,
                effective_pattern_count=2, total_applications=20,
                dominant_pattern_share=0.60,
                threshold_exceeded=True,
            ),
            MagicMock(
                calculated_at=datetime(2026, 1, 15, 11, 0, tzinfo=UTC),
                shannon_entropy=2.5, max_possible_entropy=3.0,
                diversity_index=0.83, unique_pattern_count=7,
                effective_pattern_count=5, total_applications=42,
                dominant_pattern_share=0.20,
                threshold_exceeded=False,
            ),
        ]
        mock_store.get_pattern_entropy_history.return_value = records
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["patterns-entropy", "--history"])

        assert result.exit_code == 0
        assert "2 record" in result.stdout


class TestEntropyStatusCommand:
    """Tests for the entropy-status command (Q004 — GH#82)."""

    @patch(_GS_PATCH)
    def test_entropy_status_no_responses(self, mock_get_store):
        """Shows hint when no responses recorded."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status"])

        assert result.exit_code == 0
        assert "No entropy responses" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_with_data(self, mock_get_store):
        """Shows statistics and last response when data exists."""
        mock_store = _make_mock_store()
        mock_store.get_entropy_response_statistics.return_value = {
            "total_responses": 5,
            "avg_entropy_at_trigger": 0.25,
            "budget_boosts": 3,
            "quarantine_revisits": 2,
            "last_response": "2026-01-15",
        }
        last = MagicMock(
            entropy_at_trigger=0.20,
            threshold_used=0.30,
            actions_taken=["budget_boost", "quarantine_revisit"],
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_last_entropy_response.return_value = last
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status"])

        assert result.exit_code == 0
        assert "Total Responses" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_many_responses_warning(self, mock_get_store):
        """Shows warning when >10 responses triggered."""
        mock_store = _make_mock_store()
        mock_store.get_entropy_response_statistics.return_value = {
            "total_responses": 15,
            "avg_entropy_at_trigger": 0.20,
            "budget_boosts": 10,
            "quarantine_revisits": 5,
            "last_response": "2026-01-15",
        }
        mock_store.get_last_entropy_response.return_value = None
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status"])

        assert result.exit_code == 0
        assert "Many responses" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_active_quarantine_revisiting(self, mock_get_store):
        """Shows info when quarantine revisits exceed responses."""
        mock_store = _make_mock_store()
        mock_store.get_entropy_response_statistics.return_value = {
            "total_responses": 3,
            "avg_entropy_at_trigger": 0.22,
            "budget_boosts": 1,
            "quarantine_revisits": 5,
            "last_response": "2026-01-15",
        }
        mock_store.get_last_entropy_response.return_value = None
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status"])

        assert result.exit_code == 0
        assert "quarantine" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_entropy_status_json(self, mock_get_store):
        """entropy-status --json returns structured data."""
        mock_store = _make_mock_store()
        mock_store.get_entropy_response_statistics.return_value = {
            "total_responses": 5,
            "avg_entropy_at_trigger": 0.25,
            "budget_boosts": 3,
            "quarantine_revisits": 2,
            "last_response": "2026-01-15",
        }
        last = MagicMock(
            entropy_at_trigger=0.20,
            threshold_used=0.30,
            actions_taken=["budget_boost"],
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        mock_store.get_last_entropy_response.return_value = last
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["statistics"]["total_responses"] == 5
        assert data["last_response"]["entropy_at_trigger"] == 0.2

    @patch(_GS_PATCH)
    def test_entropy_status_check_needed(self, mock_get_store):
        """entropy-status --check shows response needed."""
        mock_store = _make_mock_store()
        mock_store.check_entropy_response_needed.return_value = (
            True, 0.20, "Entropy below threshold"
        )
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--check"])

        assert result.exit_code == 0
        assert "NEEDED" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_check_not_needed(self, mock_get_store):
        """entropy-status --check shows no response needed."""
        mock_store = _make_mock_store()
        mock_store.check_entropy_response_needed.return_value = (
            False, 0.65, "Entropy within healthy range"
        )
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--check"])

        assert result.exit_code == 0
        assert "No response needed" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_check_json(self, mock_get_store):
        """entropy-status --check --json returns structured check result."""
        mock_store = _make_mock_store()
        mock_store.check_entropy_response_needed.return_value = (
            True, 0.20, "Below threshold"
        )
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--check", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["needs_response"] is True
        assert data["current_entropy"] == 0.2

    @patch(_GS_PATCH)
    def test_entropy_status_history_empty(self, mock_get_store):
        """entropy-status --history shows hint when empty."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--history"])

        assert result.exit_code == 0
        assert "No entropy response history" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_history_with_records(self, mock_get_store):
        """entropy-status --history renders table with records."""
        mock_store = _make_mock_store()
        record = MagicMock(
            id="resp-001",
            job_hash="abc123",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            entropy_at_trigger=0.20,
            threshold_used=0.30,
            actions_taken=["budget_boost"],
            budget_boosted=True,
            quarantine_revisits=2,
        )
        mock_store.get_entropy_response_history.return_value = [record]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--history"])

        assert result.exit_code == 0
        assert "1 record" in result.stdout

    @patch(_GS_PATCH)
    def test_entropy_status_history_json(self, mock_get_store):
        """entropy-status --history --json returns array."""
        mock_store = _make_mock_store()
        record = MagicMock(
            id="resp-001",
            job_hash="abc123",
            recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            entropy_at_trigger=0.20,
            threshold_used=0.30,
            actions_taken=["budget_boost"],
            budget_boosted=True,
            quarantine_revisits=2,
        )
        mock_store.get_entropy_response_history.return_value = [record]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["entropy-status", "--history", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["budget_boosted"] is True


class TestLearningDriftCommand:
    """Tests for the learning-drift command (Q005 — GH#82)."""

    @patch(_GS_PATCH)
    def test_drift_no_drifting_patterns(self, mock_get_store):
        """learning-drift shows message when no drift detected."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift"])

        assert result.exit_code == 0
        assert "No patterns exceeding" in result.stdout

    @patch(_GS_PATCH)
    def test_drift_with_drifting_patterns(self, mock_get_store):
        """learning-drift shows table when patterns are drifting."""
        mock_store = _make_mock_store()
        p = MagicMock(
            pattern_id="pat-001",
            pattern_name="retry_on_timeout",
            effectiveness_before=0.80,
            effectiveness_after=0.50,
            drift_magnitude=0.30,
            drift_direction="negative",
            grounding_confidence_avg=0.85,
            applications_analyzed=10,
        )
        mock_store.get_drifting_patterns.return_value = [p]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift"])

        assert result.exit_code == 0
        assert "declining" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_drift_positive_direction(self, mock_get_store):
        """Positive drift shows 'improving' direction."""
        mock_store = _make_mock_store()
        p = MagicMock(
            pattern_id="pat-002",
            pattern_name="escalation_pattern",
            effectiveness_before=0.50,
            effectiveness_after=0.80,
            drift_magnitude=0.30,
            drift_direction="positive",
            grounding_confidence_avg=0.90,
            applications_analyzed=10,
        )
        mock_store.get_drifting_patterns.return_value = [p]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift"])

        assert result.exit_code == 0
        assert "improving" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_drift_json_output(self, mock_get_store):
        """learning-drift --json returns valid JSON."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "threshold" in data
        assert "patterns" in data
        assert isinstance(data["patterns"], list)

    @patch(_GS_PATCH)
    def test_drift_json_with_patterns(self, mock_get_store):
        """learning-drift --json includes pattern details."""
        mock_store = _make_mock_store()
        p = MagicMock(
            pattern_id="pat-001",
            pattern_name="retry_on_timeout",
            effectiveness_before=0.80,
            effectiveness_after=0.50,
            drift_magnitude=0.30,
            drift_direction="negative",
            grounding_confidence_avg=0.85,
            applications_analyzed=10,
        )
        mock_store.get_drifting_patterns.return_value = [p]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data["patterns"]) == 1
        assert data["patterns"][0]["drift_direction"] == "negative"

    @patch(_GS_PATCH)
    def test_drift_summary(self, mock_get_store):
        """learning-drift --summary shows summary statistics."""
        mock_store = _make_mock_store()
        mock_store.get_pattern_drift_summary.return_value = {
            "total_patterns": 10,
            "patterns_analyzed": 8,
            "patterns_drifting": 2,
            "avg_drift_magnitude": 0.15,
            "most_drifted": "retry_on_timeout",
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift", "--summary"])

        assert result.exit_code == 0
        assert "Summary" in result.stdout
        assert "retry_on_timeout" in result.stdout

    @patch(_GS_PATCH)
    def test_drift_summary_no_drifting(self, mock_get_store):
        """learning-drift --summary shows zero drifting in green."""
        mock_store = _make_mock_store()
        mock_store.get_pattern_drift_summary.return_value = {
            "total_patterns": 10,
            "patterns_analyzed": 8,
            "patterns_drifting": 0,
            "avg_drift_magnitude": 0.0,
            "most_drifted": None,
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift", "--summary"])

        assert result.exit_code == 0
        assert "0" in result.stdout

    @patch(_GS_PATCH)
    def test_drift_summary_json(self, mock_get_store):
        """learning-drift --summary --json returns dict."""
        mock_store = _make_mock_store()
        mock_store.get_pattern_drift_summary.return_value = {
            "total_patterns": 10,
            "patterns_analyzed": 8,
            "patterns_drifting": 2,
            "avg_drift_magnitude": 0.15,
            "most_drifted": "retry_on_timeout",
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift", "--summary", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["patterns_drifting"] == 2

    @patch(_GS_PATCH)
    def test_drift_custom_threshold(self, mock_get_store):
        """learning-drift -t 0.15 passes threshold to store."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-drift", "-t", "0.15"])

        assert result.exit_code == 0
        mock_store.get_drifting_patterns.assert_called_once_with(
            drift_threshold=0.15, window_size=5, limit=10,
        )


class TestEpistemicDriftCommand:
    """Tests for the learning-epistemic-drift command (Q005 — GH#82)."""

    @patch(_GS_PATCH)
    def test_epistemic_drift_no_data(self, mock_get_store):
        """learning-epistemic-drift shows message with no data."""
        mock_store = _make_mock_store()
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-epistemic-drift"])

        assert result.exit_code == 0
        assert "No patterns exceeding" in result.stdout

    @patch(_GS_PATCH)
    def test_epistemic_drift_with_patterns(self, mock_get_store):
        """Shows table with weakening/strengthening patterns."""
        mock_store = _make_mock_store()
        patterns = [
            MagicMock(
                pattern_id="pat-001", pattern_name="retry_pattern",
                confidence_before=0.90, confidence_after=0.60,
                belief_change=-0.30, belief_entropy=0.35,
                drift_direction="weakening", applications_analyzed=10,
            ),
            MagicMock(
                pattern_id="pat-002", pattern_name="escalation_pattern",
                confidence_before=0.50, confidence_after=0.85,
                belief_change=0.35, belief_entropy=0.10,
                drift_direction="strengthening", applications_analyzed=8,
            ),
        ]
        mock_store.get_epistemic_drifting_patterns.return_value = patterns
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-epistemic-drift"])

        assert result.exit_code == 0
        assert "weakening" in result.stdout.lower()
        assert "strengthening" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_epistemic_drift_high_entropy_warning(self, mock_get_store):
        """Shows warning when patterns have high belief entropy."""
        mock_store = _make_mock_store()
        p = MagicMock(
            pattern_id="pat-001", pattern_name="unstable_pattern",
            confidence_before=0.70, confidence_after=0.65,
            belief_change=-0.05, belief_entropy=0.45,
            drift_direction="weakening", applications_analyzed=10,
        )
        mock_store.get_epistemic_drifting_patterns.return_value = [p]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-epistemic-drift"])

        assert result.exit_code == 0
        assert "entropy" in result.stdout.lower()

    @patch(_GS_PATCH)
    def test_epistemic_drift_json(self, mock_get_store):
        """learning-epistemic-drift --json returns valid JSON."""
        mock_store = _make_mock_store()
        p = MagicMock(
            pattern_id="pat-001", pattern_name="retry_pattern",
            confidence_before=0.90, confidence_after=0.60,
            belief_change=-0.30, belief_entropy=0.35,
            drift_direction="weakening", applications_analyzed=10,
        )
        mock_store.get_epistemic_drifting_patterns.return_value = [p]
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-epistemic-drift", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["patterns"][0]["drift_direction"] == "weakening"

    @patch(_GS_PATCH)
    def test_epistemic_drift_summary(self, mock_get_store):
        """learning-epistemic-drift --summary shows summary stats."""
        mock_store = _make_mock_store()
        mock_store.get_epistemic_drift_summary.return_value = {
            "total_patterns": 10,
            "patterns_analyzed": 8,
            "patterns_with_epistemic_drift": 2,
            "avg_belief_change": 0.12,
            "avg_belief_entropy": 0.18,
            "most_unstable": "retry_pattern",
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-epistemic-drift", "--summary"])

        assert result.exit_code == 0
        assert "Summary" in result.stdout
        assert "retry_pattern" in result.stdout

    @patch(_GS_PATCH)
    def test_epistemic_drift_summary_json(self, mock_get_store):
        """learning-epistemic-drift --summary --json returns dict."""
        mock_store = _make_mock_store()
        mock_store.get_epistemic_drift_summary.return_value = {
            "total_patterns": 10,
            "patterns_analyzed": 8,
            "patterns_with_epistemic_drift": 2,
            "avg_belief_change": 0.12,
            "avg_belief_entropy": 0.18,
            "most_unstable": "retry_pattern",
        }
        mock_get_store.return_value = mock_store

        result = runner.invoke(app, ["learning-epistemic-drift", "--summary", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["patterns_with_epistemic_drift"] == 2
