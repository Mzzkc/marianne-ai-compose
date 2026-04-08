"""Tests for mzt learning-export CLI command."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()

_GS_PATCH = "marianne.learning.global_store.get_global_store"


def _make_pattern(
    *,
    pattern_id: str = "p1",
    pattern_type: str = "SEMANTIC_INSIGHT",
    pattern_name: str = "test_pattern",
    description: str = "Test insight description",
    effectiveness_score: float = 0.75,
    trust_score: float = 0.8,
    occurrence_count: int = 5,
    category: str = "root_cause",
    tags: list[str] | None = None,
) -> MagicMock:
    p = MagicMock()
    p.id = pattern_id
    p.pattern_type = pattern_type
    p.pattern_name = pattern_name
    p.description = description
    p.effectiveness_score = effectiveness_score
    p.trust_score = trust_score
    p.occurrence_count = occurrence_count
    p.variance = 0.1
    p.first_seen = "2026-02-01T00:00:00"
    p.last_seen = "2026-02-20T00:00:00"
    p.quarantine_status = "VALIDATED"
    p.context_tags = tags or [category]
    p.led_to_success_count = 4
    p.led_to_failure_count = 1
    return p


def _make_drift_metrics(
    *,
    pattern_id: str = "p1",
    pattern_name: str = "drifting_pattern",
    effectiveness_before: float = 0.8,
    effectiveness_after: float = 0.5,
    drift_magnitude: float = 0.3,
    drift_direction: str = "negative",
) -> MagicMock:
    m = MagicMock()
    m.pattern_id = pattern_id
    m.pattern_name = pattern_name
    m.effectiveness_before = effectiveness_before
    m.effectiveness_after = effectiveness_after
    m.drift_magnitude = drift_magnitude
    m.drift_direction = drift_direction
    m.grounding_confidence_avg = 0.7
    m.applications_analyzed = 10
    return m


def _make_trajectory_entry(
    *,
    cycle: int = 25,
    evolutions_completed: int = 2,
    evolutions_deferred: int = 1,
    issue_classes: list[str] | None = None,
    implementation_loc: int = 150,
    test_loc: int = 200,
) -> MagicMock:
    e = MagicMock()
    e.cycle = cycle
    e.recorded_at = "2026-02-20T00:00:00"
    e.evolutions_completed = evolutions_completed
    e.evolutions_deferred = evolutions_deferred
    e.issue_classes = issue_classes or ["infrastructure_activation"]
    e.cv_avg = 0.78
    e.implementation_loc = implementation_loc
    e.test_loc = test_loc
    e.loc_accuracy = 0.95
    e.research_candidates_resolved = 1
    e.research_candidates_created = 0
    e.notes = "Test cycle"
    return e


def _setup_mock_store(mock_get_store: MagicMock, **overrides: object) -> MagicMock:
    """Create a fully-configured mock store with sensible defaults."""
    mock_store = MagicMock()
    mock_store.get_patterns.return_value = overrides.get("patterns", [])
    mock_store.get_drifting_patterns.return_value = overrides.get(
        "drifting_patterns", []
    )
    mock_store.get_epistemic_drifting_patterns.return_value = overrides.get(
        "epistemic_drift", []
    )
    mock_store.calculate_pattern_entropy.return_value = overrides.get(
        "entropy",
        MagicMock(
            shannon_entropy=2.5,
            diversity_index=0.7,
            unique_pattern_count=10,
            effective_pattern_count=8,
            dominant_pattern_share=0.15,
        ),
    )
    mock_store.get_entropy_response_history.return_value = overrides.get(
        "entropy_history", []
    )
    mock_store.get_trajectory.return_value = overrides.get("trajectory", [])
    mock_store.get_execution_stats.return_value = overrides.get("exec_stats", {})
    mock_get_store.return_value = mock_store
    return mock_store


class TestLearningExport:
    """Tests for learning-export command."""

    @patch(_GS_PATCH)
    def test_creates_output_directory(
        self, mock_get_store: MagicMock, tmp_path: Path
    ) -> None:
        _setup_mock_store(mock_get_store, patterns=[_make_pattern()])

        out_dir = tmp_path / "export"
        result = runner.invoke(
            app, ["learning-export", "--output-dir", str(out_dir)]
        )
        assert result.exit_code == 0, result.stdout
        assert out_dir.exists()

    @patch(_GS_PATCH)
    def test_writes_semantic_insights_file(
        self, mock_get_store: MagicMock, tmp_path: Path
    ) -> None:
        _setup_mock_store(
            mock_get_store,
            patterns=[
                _make_pattern(pattern_id="p1", category="root_cause"),
                _make_pattern(pattern_id="p2", category="knowledge"),
            ],
        )

        out_dir = tmp_path / "export"
        result = runner.invoke(
            app, ["learning-export", "--output-dir", str(out_dir)]
        )
        assert result.exit_code == 0, result.stdout

        insights_file = out_dir / "semantic-insights.md"
        assert insights_file.exists()
        content = insights_file.read_text()
        assert "SEMANTIC_INSIGHT" in content or "Semantic Insights" in content
        assert "test_pattern" in content

    @patch(_GS_PATCH)
    def test_writes_drift_report(
        self, mock_get_store: MagicMock, tmp_path: Path
    ) -> None:
        _setup_mock_store(
            mock_get_store, drifting_patterns=[_make_drift_metrics()]
        )

        out_dir = tmp_path / "export"
        result = runner.invoke(
            app, ["learning-export", "--output-dir", str(out_dir)]
        )
        assert result.exit_code == 0, result.stdout
        assert (out_dir / "drift-report.md").exists()

    @patch(_GS_PATCH)
    def test_writes_all_six_files(
        self, mock_get_store: MagicMock, tmp_path: Path
    ) -> None:
        _setup_mock_store(mock_get_store)

        out_dir = tmp_path / "export"
        result = runner.invoke(
            app, ["learning-export", "--output-dir", str(out_dir)]
        )
        assert result.exit_code == 0, result.stdout

        expected_files = [
            "semantic-insights.md",
            "drift-report.md",
            "entropy-state.md",
            "pattern-health.md",
            "evolution-history.md",
            "error-landscape.md",
        ]
        for fname in expected_files:
            assert (out_dir / fname).exists(), f"Missing: {fname}"

    @patch(_GS_PATCH)
    def test_evolution_history_includes_trajectory(
        self, mock_get_store: MagicMock, tmp_path: Path
    ) -> None:
        _setup_mock_store(
            mock_get_store,
            trajectory=[
                _make_trajectory_entry(cycle=25),
                _make_trajectory_entry(cycle=24, evolutions_completed=3),
            ],
        )

        out_dir = tmp_path / "export"
        result = runner.invoke(
            app, ["learning-export", "--output-dir", str(out_dir)]
        )
        assert result.exit_code == 0, result.stdout

        content = (out_dir / "evolution-history.md").read_text()
        assert "25" in content  # cycle number
        assert "24" in content

    @patch(_GS_PATCH)
    def test_json_format_writes_json_files(
        self, mock_get_store: MagicMock, tmp_path: Path
    ) -> None:
        _setup_mock_store(mock_get_store, patterns=[_make_pattern()])

        out_dir = tmp_path / "export"
        result = runner.invoke(
            app,
            ["learning-export", "--output-dir", str(out_dir), "--format", "json"],
        )
        assert result.exit_code == 0, result.stdout
        assert (out_dir / "semantic-insights.json").exists()
        # Verify valid JSON
        data = json.loads((out_dir / "semantic-insights.json").read_text())
        assert isinstance(data, (list, dict))


class TestLearningRecordEvolution:
    """Tests for learning-record-evolution command."""

    @patch(_GS_PATCH)
    def test_records_evolution_entry(
        self, mock_get_store: MagicMock
    ) -> None:
        mock_store = MagicMock()
        mock_store.record_evolution_entry.return_value = "uuid-123"
        mock_get_store.return_value = mock_store

        result = runner.invoke(
            app,
            [
                "learning-record-evolution",
                "--cycle", "26",
                "--evolutions-completed", "2",
                "--issue-classes", "infrastructure_activation,testing_depth",
                "--implementation-loc", "150",
                "--test-loc", "200",
            ],
        )
        assert result.exit_code == 0, result.stdout
        mock_store.record_evolution_entry.assert_called_once()

    @patch(_GS_PATCH)
    def test_rejects_missing_required_fields(
        self, mock_get_store: MagicMock
    ) -> None:
        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        result = runner.invoke(
            app, ["learning-record-evolution", "--cycle", "26"]
        )
        # Should fail due to missing required options
        assert result.exit_code != 0
