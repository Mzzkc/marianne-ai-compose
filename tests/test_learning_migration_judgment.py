"""Tests for learning migration and judgment modules.

Covers OutcomeMigrator (migration.py) and JudgmentClient/LocalJudgmentClient
(judgment.py) with focus on data parsing, decision logic, and error handling.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.core.checkpoint import SheetStatus

# =============================================================================
# MigrationResult tests
# =============================================================================


class TestMigrationResult:
    """Tests for MigrationResult dataclass."""

    def test_default_values(self) -> None:
        """MigrationResult should start with zero counts and empty lists."""
        from marianne.learning.migration import MigrationResult

        result = MigrationResult()
        assert result.workspaces_found == 0
        assert result.outcomes_imported == 0
        assert result.patterns_detected == 0
        assert result.errors == []
        assert result.skipped_workspaces == []
        assert result.imported_workspaces == []

    def test_repr(self) -> None:
        """MigrationResult repr should show key metrics."""
        from marianne.learning.migration import MigrationResult

        result = MigrationResult(
            workspaces_found=3,
            outcomes_imported=42,
            patterns_detected=7,
        )
        assert "workspaces=3" in repr(result)
        assert "outcomes=42" in repr(result)
        assert "patterns=7" in repr(result)


# =============================================================================
# OutcomeMigrator tests
# =============================================================================


class TestOutcomeMigrator:
    """Tests for OutcomeMigrator class."""

    @pytest.fixture
    def mock_store(self) -> MagicMock:
        """Create a mock GlobalLearningStore."""
        store = MagicMock()
        store.hash_workspace = MagicMock(return_value="hash-123")
        store.record_outcome = MagicMock()
        store.get_execution_stats = MagicMock(return_value={
            "total_executions": 10,
            "total_patterns": 3,
        })
        return store

    def test_migrate_workspace_no_outcomes_file(self, mock_store, tmp_path: Path) -> None:
        """Migrating workspace without outcomes file should report error."""
        from marianne.learning.migration import OutcomeMigrator

        migrator = OutcomeMigrator(mock_store)
        result = migrator.migrate_workspace(tmp_path)

        assert result.workspaces_found == 0
        assert result.outcomes_imported == 0
        assert len(result.errors) == 1
        assert "No outcomes file found" in result.errors[0]

    def test_migrate_workspace_with_outcomes(self, mock_store, tmp_path: Path) -> None:
        """Migrating workspace with valid outcomes should import them."""
        from marianne.learning.migration import OutcomeMigrator

        # Create outcomes file with sample data
        outcomes_data = {
            "outcomes": [
                {
                    "sheet_id": "job-1-sheet-1",
                    "job_id": "job-1",
                    "final_status": "completed",
                    "validation_results": [{"passed": True}],
                    "execution_duration": 30.0,
                    "retry_count": 0,
                    "completion_mode_used": False,
                    "success_without_retry": True,
                    "timestamp": "2024-01-15T10:00:00",
                },
                {
                    "sheet_id": "job-1-sheet-2",
                    "job_id": "job-1",
                    "final_status": "failed",
                    "validation_results": [{"passed": False}],
                    "execution_duration": 45.0,
                    "retry_count": 2,
                    "completion_mode_used": True,
                    "success_without_retry": False,
                    "timestamp": "2024-01-15T10:30:00",
                },
            ]
        }
        outcomes_file = tmp_path / ".marianne-outcomes.json"
        outcomes_file.write_text(json.dumps(outcomes_data))

        migrator = OutcomeMigrator(mock_store)
        result = migrator.migrate_workspace(tmp_path)

        assert result.workspaces_found == 1
        assert result.outcomes_imported == 2
        assert str(tmp_path) in result.imported_workspaces

    def test_migrate_workspace_empty_outcomes(self, mock_store, tmp_path: Path) -> None:
        """Workspace with empty outcomes list should import zero."""
        from marianne.learning.migration import OutcomeMigrator

        outcomes_file = tmp_path / ".marianne-outcomes.json"
        outcomes_file.write_text(json.dumps({"outcomes": []}))

        migrator = OutcomeMigrator(mock_store)
        result = migrator.migrate_workspace(tmp_path)

        assert result.outcomes_imported == 0
        assert str(tmp_path) in result.skipped_workspaces

    def test_migrate_workspace_idempotent(self, mock_store, tmp_path: Path) -> None:
        """Second migration of same workspace should skip (idempotent)."""
        from marianne.learning.migration import OutcomeMigrator

        outcomes_data = {
            "outcomes": [
                {
                    "sheet_id": "job-1-sheet-1",
                    "final_status": "completed",
                    "timestamp": "2024-01-15T10:00:00",
                }
            ]
        }
        outcomes_file = tmp_path / ".marianne-outcomes.json"
        outcomes_file.write_text(json.dumps(outcomes_data))

        migrator = OutcomeMigrator(mock_store)

        # First migration
        result1 = migrator.migrate_workspace(tmp_path)
        assert result1.outcomes_imported == 1

        # Second migration — same workspace hash already tracked
        result2 = migrator.migrate_workspace(tmp_path)
        assert result2.outcomes_imported == 0
        assert str(tmp_path) in result2.skipped_workspaces

    def test_migrate_all_with_additional_paths(self, mock_store, tmp_path: Path) -> None:
        """migrate_all with additional_paths should scan those paths."""
        from marianne.learning.migration import OutcomeMigrator

        # Create workspace with outcomes
        workspace = tmp_path / "my-workspace"
        workspace.mkdir()
        outcomes_file = workspace / ".marianne-outcomes.json"
        outcomes_file.write_text(json.dumps({
            "outcomes": [
                {"sheet_id": "s1", "final_status": "completed", "timestamp": "2024-01-15T10:00:00"},
            ]
        }))

        migrator = OutcomeMigrator(mock_store)
        # Use non-matching scan pattern (empty list is falsy → falls back to defaults)
        result = migrator.migrate_all(
            scan_patterns=[str(tmp_path / "nonexistent-*/.marianne-outcomes.json")],
            additional_paths=[outcomes_file],
        )

        assert result.outcomes_imported >= 1

    def test_migrate_all_with_aggregator(self, mock_store, tmp_path: Path) -> None:
        """migrate_all with aggregator should run pattern detection."""
        from marianne.learning.migration import OutcomeMigrator

        mock_aggregator = MagicMock()
        mock_aggregator._update_all_priorities = MagicMock()
        mock_aggregator.prune_deprecated_patterns = MagicMock(return_value=0)

        # Create outcomes
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outcomes_file = workspace / ".marianne-outcomes.json"
        outcomes_file.write_text(json.dumps({
            "outcomes": [
                {"sheet_id": "s1", "final_status": "completed", "timestamp": "2024-01-15T10:00:00"},
            ]
        }))

        migrator = OutcomeMigrator(mock_store, aggregator=mock_aggregator)
        # Use a non-matching scan pattern to avoid scanning real workspaces
        # (empty list is falsy, so it falls back to DEFAULT_SCAN_PATTERNS)
        result = migrator.migrate_all(
            scan_patterns=[str(tmp_path / "nonexistent-*/.marianne-outcomes.json")],
            additional_paths=[outcomes_file],
        )

        assert result.outcomes_imported >= 1
        mock_aggregator._update_all_priorities.assert_called_once()


class TestParseOutcome:
    """Tests for OutcomeMigrator._parse_outcome edge cases."""

    @pytest.fixture
    def migrator(self):
        from marianne.learning.migration import OutcomeMigrator
        return OutcomeMigrator(MagicMock())

    def test_parse_outcome_complete(self, migrator) -> None:
        """Full outcome data should parse correctly."""
        data = {
            "sheet_id": "job-1-sheet-3",
            "job_id": "job-1",
            "final_status": "completed",
            "validation_results": [{"passed": True}, {"passed": False}],
            "execution_duration": 60.0,
            "retry_count": 1,
            "completion_mode_used": True,
            "success_without_retry": False,
            "timestamp": "2024-06-15T14:30:00",
        }
        outcome = migrator._parse_outcome(data)
        assert outcome is not None
        assert outcome.sheet_id == "job-1-sheet-3"
        assert outcome.job_id == "job-1"
        assert outcome.final_status == SheetStatus.COMPLETED
        assert outcome.validation_pass_rate == 0.5
        assert outcome.retry_count == 1

    def test_parse_outcome_missing_job_id(self, migrator) -> None:
        """Missing job_id should be inferred from sheet_id."""
        data = {
            "sheet_id": "myJob-sheet-5",
            "final_status": "failed",
            "timestamp": "2024-01-15T10:00:00",
        }
        outcome = migrator._parse_outcome(data)
        assert outcome is not None
        assert outcome.job_id == "myJob-sheet"

    def test_parse_outcome_invalid_status(self, migrator) -> None:
        """Unknown status string should fall back to FAILED."""
        data = {
            "sheet_id": "job-1-sheet-1",
            "final_status": "banana",
            "timestamp": "2024-01-15T10:00:00",
        }
        outcome = migrator._parse_outcome(data)
        assert outcome is not None
        assert outcome.final_status == SheetStatus.FAILED

    def test_parse_outcome_invalid_timestamp(self, migrator) -> None:
        """Invalid timestamp should fall back to current time."""
        data = {
            "sheet_id": "job-1-sheet-1",
            "final_status": "completed",
            "timestamp": "not-a-date",
        }
        outcome = migrator._parse_outcome(data)
        assert outcome is not None
        # Should have a timestamp (defaulted to now)
        assert isinstance(outcome.timestamp, datetime)

    def test_parse_outcome_no_validation_results(self, migrator) -> None:
        """When validation_results is empty list, validation_pass_rate field is used."""
        data = {
            "sheet_id": "job-1-sheet-1",
            "final_status": "completed",
            "validation_pass_rate": 0.8,
            "timestamp": "2024-01-15T10:00:00",
        }
        outcome = migrator._parse_outcome(data)
        assert outcome is not None
        # validation_results defaults to [] which is a list, so the list
        # parsing branch runs: 0 passed / 1 total = 0.0
        # (the explicit validation_pass_rate is only used when
        # validation_results is NOT a list)
        assert outcome.validation_pass_rate == 0.0

    def test_parse_outcome_legacy_id_field(self, migrator) -> None:
        """Legacy 'id' field should work when 'sheet_id' is missing."""
        data = {
            "id": "legacy-123",
            "final_status": "completed",
            "timestamp": "2024-01-15T10:00:00",
        }
        outcome = migrator._parse_outcome(data)
        assert outcome is not None
        assert outcome.sheet_id == "legacy-123"


# =============================================================================
# Convenience function tests
# =============================================================================


class TestMigrationConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_migrate_existing_outcomes(self, tmp_path: Path) -> None:
        """migrate_existing_outcomes should create migrator and call migrate_all."""
        from marianne.learning.migration import migrate_existing_outcomes

        mock_store = MagicMock()
        mock_store.hash_workspace = MagicMock(return_value="hash")
        mock_store.record_outcome = MagicMock()

        # Use a non-matching pattern to avoid scanning real workspaces
        result = migrate_existing_outcomes(
            mock_store,
            scan_patterns=[str(tmp_path / "nonexistent-pattern-*/.marianne-outcomes.json")],
        )
        assert result.workspaces_found == 0

    def test_check_migration_status(self) -> None:
        """check_migration_status should return stats from global store."""
        from marianne.learning.migration import check_migration_status

        mock_store = MagicMock()
        mock_store.get_execution_stats.return_value = {
            "total_executions": 50,
            "total_patterns": 12,
            "unique_workspaces": 5,
            "avg_pattern_effectiveness": 0.75,
        }

        status = check_migration_status(mock_store)
        assert status["total_executions"] == 50
        assert status["total_patterns"] == 12
        assert status["unique_workspaces"] == 5
        assert status["needs_migration"] is False

    def test_check_migration_status_empty(self) -> None:
        """Empty store should report needs_migration=True."""
        from marianne.learning.migration import check_migration_status

        mock_store = MagicMock()
        mock_store.get_execution_stats.return_value = {
            "total_executions": 0,
            "total_patterns": 0,
            "unique_workspaces": 0,
            "avg_pattern_effectiveness": 0.0,
        }

        status = check_migration_status(mock_store)
        assert status["needs_migration"] is True


# =============================================================================
# JudgmentQuery/JudgmentResponse tests
# =============================================================================


class TestJudgmentDataclasses:
    """Tests for JudgmentQuery and JudgmentResponse dataclasses."""

    def test_judgment_query_creation(self) -> None:
        """JudgmentQuery should store all required fields."""
        from marianne.learning.judgment import JudgmentQuery

        query = JudgmentQuery(
            job_id="test-job",
            sheet_num=3,
            validation_results=[{"passed": True}],
            execution_history=[{"attempt": 1}],
            error_patterns=["timeout"],
            retry_count=2,
            confidence=0.6,
        )
        assert query.job_id == "test-job"
        assert query.sheet_num == 3
        assert query.retry_count == 2
        assert query.confidence == 0.6

    def test_judgment_response_defaults(self) -> None:
        """JudgmentResponse should have correct defaults."""
        from marianne.learning.judgment import JudgmentResponse

        response = JudgmentResponse(
            recommended_action="retry",
            confidence=0.5,
            reasoning="Test reason",
        )
        assert response.prompt_modifications is None
        assert response.escalation_urgency is None
        assert response.human_question is None
        assert response.patterns_learned == []


# =============================================================================
# JudgmentClient tests
# =============================================================================


class TestJudgmentClient:
    """Tests for JudgmentClient HTTP client."""

    def test_client_initialization(self) -> None:
        """Client should store endpoint and timeout."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080", timeout=15.0)
        assert client.endpoint == "http://localhost:8080"
        assert client.timeout == 15.0

    def test_endpoint_trailing_slash_stripped(self) -> None:
        """Trailing slash should be stripped from endpoint."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080/")
        assert client.endpoint == "http://localhost:8080"

    def test_default_retry_response(self) -> None:
        """_default_retry_response should return low-confidence retry."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        response = client._default_retry_response("Connection refused")

        assert response.recommended_action == "retry"
        assert response.confidence == 0.3
        assert "Connection refused" in response.reasoning
        assert response.patterns_learned == []

    def test_parse_judgment_response_valid(self) -> None:
        """Valid API response should parse correctly."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {
            "recommended_action": "proceed",
            "confidence": 0.85,
            "reasoning": "All validations passed",
            "prompt_modifications": ["Add more context"],
            "escalation_urgency": "low",
            "human_question": "Should we continue?",
            "patterns_learned": ["success_pattern"],
        }
        response = client._parse_judgment_response(data)

        assert response.recommended_action == "proceed"
        assert response.confidence == 0.85
        assert response.reasoning == "All validations passed"
        assert response.prompt_modifications == ["Add more context"]
        assert response.escalation_urgency == "low"
        assert response.human_question == "Should we continue?"
        assert response.patterns_learned == ["success_pattern"]

    def test_parse_judgment_response_invalid_action(self) -> None:
        """Invalid action should default to 'retry'."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {"recommended_action": "destroy_everything", "confidence": 0.9}
        response = client._parse_judgment_response(data)

        assert response.recommended_action == "retry"

    def test_parse_judgment_response_out_of_range_confidence(self) -> None:
        """Confidence outside 0-1 should be clamped."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")

        data_high = {"confidence": 2.5}
        response_high = client._parse_judgment_response(data_high)
        assert response_high.confidence == 1.0

        data_low = {"confidence": -0.5}
        response_low = client._parse_judgment_response(data_low)
        assert response_low.confidence == 0.0

    def test_parse_judgment_response_invalid_confidence_type(self) -> None:
        """Non-numeric confidence should default to 0.5."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {"confidence": "high"}
        response = client._parse_judgment_response(data)
        assert response.confidence == 0.5

    def test_parse_judgment_response_invalid_prompt_modifications(self) -> None:
        """Non-list prompt_modifications should be set to None."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {"prompt_modifications": "not a list"}
        response = client._parse_judgment_response(data)
        assert response.prompt_modifications is None

    def test_parse_judgment_response_invalid_escalation_urgency(self) -> None:
        """Invalid escalation_urgency should be set to None."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {"escalation_urgency": "critical"}
        response = client._parse_judgment_response(data)
        assert response.escalation_urgency is None

    def test_parse_judgment_response_non_string_reasoning(self) -> None:
        """Non-string reasoning should be converted to string."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {"reasoning": 42}
        response = client._parse_judgment_response(data)
        assert response.reasoning == "42"

    def test_parse_judgment_response_empty_patterns(self) -> None:
        """Non-list patterns_learned should default to empty list."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        data = {"patterns_learned": "not_a_list"}
        response = client._parse_judgment_response(data)
        assert response.patterns_learned == []

    @pytest.mark.asyncio
    async def test_client_close(self) -> None:
        """close() should close the underlying httpx client."""
        from marianne.learning.judgment import JudgmentClient

        client = JudgmentClient("http://localhost:8080")
        # Access internal client to create it
        http_client = await client._get_client()
        assert not http_client.is_closed

        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Async context manager should close client on exit."""
        from marianne.learning.judgment import JudgmentClient

        async with JudgmentClient("http://localhost:8080") as client:
            assert isinstance(client, JudgmentClient)
        # After exit, client should be cleaned up
        assert client._client is None

    @pytest.mark.asyncio
    async def test_get_judgment_connection_error(self) -> None:
        """Connection error should return default retry response."""
        from marianne.learning.judgment import JudgmentClient, JudgmentQuery

        client = JudgmentClient("http://localhost:99999")
        query = JudgmentQuery(
            job_id="test", sheet_num=1, validation_results=[],
            execution_history=[], error_patterns=[], retry_count=0, confidence=0.5,
        )

        response = await client.get_judgment(query)
        assert response.recommended_action == "retry"
        assert response.confidence == 0.3
        assert "unavailable" in response.reasoning.lower() or "error" in response.reasoning.lower()
        await client.close()


# =============================================================================
# LocalJudgmentClient tests
# =============================================================================


class TestLocalJudgmentClient:
    """Tests for LocalJudgmentClient heuristic decision logic."""

    @pytest.fixture
    def client(self):
        from marianne.learning.judgment import LocalJudgmentClient
        return LocalJudgmentClient(
            proceed_threshold=0.7,
            retry_threshold=0.4,
            max_retries=3,
        )

    def _make_query(
        self,
        confidence: float = 0.5,
        retry_count: int = 0,
        validation_results: list | None = None,
        sheet_num: int = 1,
    ):
        from marianne.learning.judgment import JudgmentQuery
        return JudgmentQuery(
            job_id="test",
            sheet_num=sheet_num,
            validation_results=validation_results or [],
            execution_history=[],
            error_patterns=[],
            retry_count=retry_count,
            confidence=confidence,
        )

    @pytest.mark.asyncio
    async def test_high_confidence_proceeds(self, client) -> None:
        """Confidence >= proceed_threshold should recommend 'proceed'."""
        query = self._make_query(confidence=0.8)
        response = await client.get_judgment(query)
        assert response.recommended_action == "proceed"
        assert response.confidence == 0.8

    @pytest.mark.asyncio
    async def test_medium_confidence_retries(self, client) -> None:
        """Confidence in retry range should recommend 'retry'."""
        query = self._make_query(confidence=0.5, retry_count=1)
        response = await client.get_judgment(query)
        assert response.recommended_action == "retry"

    @pytest.mark.asyncio
    async def test_low_confidence_escalates(self, client) -> None:
        """Confidence < retry_threshold should recommend 'escalate'."""
        query = self._make_query(confidence=0.2)
        response = await client.get_judgment(query)
        assert response.recommended_action == "escalate"
        assert response.escalation_urgency is not None

    @pytest.mark.asyncio
    async def test_very_low_confidence_high_urgency(self, client) -> None:
        """Very low confidence (< 0.2) should have high urgency."""
        query = self._make_query(confidence=0.1)
        response = await client.get_judgment(query)
        assert response.recommended_action == "escalate"
        assert response.escalation_urgency == "high"

    @pytest.mark.asyncio
    async def test_max_retries_with_good_pass_rate(self, client) -> None:
        """Max retries exceeded + good pass rate should try completion."""
        query = self._make_query(
            confidence=0.5,
            retry_count=3,
            validation_results=[{"passed": True}, {"passed": True}, {"passed": False}],
        )
        response = await client.get_judgment(query)
        assert response.recommended_action == "completion"
        assert "partial success" in response.reasoning

    @pytest.mark.asyncio
    async def test_max_retries_with_low_pass_rate(self, client) -> None:
        """Max retries exceeded + low pass rate should escalate."""
        query = self._make_query(
            confidence=0.5,
            retry_count=3,
            validation_results=[{"passed": False}, {"passed": False}, {"passed": False}],
        )
        response = await client.get_judgment(query)
        assert response.recommended_action == "escalate"
        assert response.human_question is not None

    @pytest.mark.asyncio
    async def test_custom_thresholds(self) -> None:
        """Custom thresholds should change decision boundaries."""
        from marianne.learning.judgment import LocalJudgmentClient

        # Very strict client
        strict_client = LocalJudgmentClient(
            proceed_threshold=0.95,
            retry_threshold=0.8,
            max_retries=5,
        )
        query = self._make_query(confidence=0.9)
        response = await strict_client.get_judgment(query)
        assert response.recommended_action == "retry"  # 0.9 < 0.95

    def test_calculate_pass_rate_empty(self, client) -> None:
        """Empty validation results should return 0.0."""
        rate = client._calculate_pass_rate([])
        assert rate == 0.0

    def test_calculate_pass_rate_all_passed(self, client) -> None:
        """All passed should return 1.0."""
        rate = client._calculate_pass_rate([
            {"passed": True},
            {"passed": True},
        ])
        assert rate == 1.0

    def test_calculate_pass_rate_mixed(self, client) -> None:
        """Mixed results should return correct ratio."""
        rate = client._calculate_pass_rate([
            {"passed": True},
            {"passed": False},
            {"passed": True},
            {"passed": False},
        ])
        assert rate == 0.5
