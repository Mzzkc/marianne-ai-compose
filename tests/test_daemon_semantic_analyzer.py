"""Tests for marianne.daemon.semantic_analyzer module."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.backends.base import Backend, ExecutionResult
from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.daemon.config import SemanticLearningConfig
from marianne.daemon.event_bus import EventBus
from marianne.daemon.learning_hub import LearningHub
from marianne.daemon.semantic_analyzer import SemanticAnalyzer
from marianne.daemon.types import ObserverEvent

# ─── Fixtures ────────────────────────────────────────────────────────


def _make_event(
    *,
    job_id: str = "test-job",
    sheet_num: int = 1,
    event: str = "sheet.completed",
    data: dict[str, Any] | None = None,
) -> ObserverEvent:
    """Create a test ObserverEvent."""
    return {
        "job_id": job_id,
        "sheet_num": sheet_num,
        "event": event,
        "data": data,
        "timestamp": 1000.0,
    }


def _make_live_state(
    job_id: str = "test-job",
    sheet_num: int = 1,
    status: SheetStatus = SheetStatus.COMPLETED,
    stdout_tail: str = "Task completed successfully",
    stderr_tail: str = "",
    exit_code: int = 0,
) -> CheckpointState:
    """Create a minimal CheckpointState with one sheet."""
    sheet = SheetState(
        sheet_num=sheet_num,
        status=status,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        exit_code=exit_code,
    )
    return CheckpointState(
        job_id=job_id,
        job_name="test",
        total_sheets=1,
        sheets={sheet_num: sheet},
    )


def _make_llm_response_json(insights: list[dict[str, Any]]) -> str:
    """Create a mock LLM response JSON string."""
    return json.dumps(insights)


def _make_mock_backend() -> MagicMock:
    """Create a mock Backend for SemanticAnalyzer tests."""
    backend = MagicMock(spec=Backend)
    backend.name = "mock-backend"
    backend.execute = AsyncMock(return_value=ExecutionResult(
        success=True, stdout="[]", stderr="", duration_seconds=0.1,
    ))
    backend.close = AsyncMock()
    return backend


# ─── Lifecycle Tests ─────────────────────────────────────────────────


class TestLifecycle:
    """Test start/stop/subscribe/unsubscribe lifecycle."""

    @pytest.mark.asyncio
    async def test_start_subscribes_to_event_bus(self):
        """Starting the analyzer subscribes to the event bus."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {}

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        bus = EventBus()
        await bus.start()

        assert bus.subscriber_count == 0
        await analyzer.start(bus)
        # Sheet events + anomaly events = 2 subscriptions
        assert bus.subscriber_count == 2

        await analyzer.stop(bus)
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_stop_unsubscribes_from_event_bus(self):
        """Stopping the analyzer unsubscribes from the event bus."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {}

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        bus = EventBus()
        await bus.start()

        await analyzer.start(bus)
        # Sheet events + anomaly events = 2 subscriptions
        assert bus.subscriber_count == 2

        await analyzer.stop(bus)
        assert bus.subscriber_count == 0

        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_disabled_does_not_subscribe(self):
        """When enabled=False, start() should not subscribe."""
        config = SemanticLearningConfig(enabled=False)
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {}

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        bus = EventBus()
        await bus.start()

        await analyzer.start(bus)
        assert bus.subscriber_count == 0

        await analyzer.stop(bus)
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        """Stopping without starting should not raise."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {}

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        await analyzer.stop()  # No event_bus passed, no subscription


# ─── Event Filtering Tests ───────────────────────────────────────────


class TestEventFiltering:
    """Test that analyze_on config filters events correctly."""

    @pytest.mark.asyncio
    async def test_success_only_skips_failures(self):
        """When analyze_on=['success'], failure events are skipped."""
        config = SemanticLearningConfig(analyze_on=["success"])
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {
            "test-job": _make_live_state(),
        }

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)

        # Failure event should be filtered out
        event = _make_event(event="sheet.failed")
        await analyzer._on_sheet_event(event)

        # No analysis task should have been spawned
        assert len(analyzer._pending_tasks) == 0

    @pytest.mark.asyncio
    async def test_failure_only_skips_successes(self):
        """When analyze_on=['failure'], success events are skipped."""
        config = SemanticLearningConfig(analyze_on=["failure"])
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {
            "test-job": _make_live_state(),
        }

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)

        # Success event should be filtered out
        event = _make_event(event="sheet.completed")
        await analyzer._on_sheet_event(event)

        assert len(analyzer._pending_tasks) == 0

    @pytest.mark.asyncio
    async def test_both_analyzes_all(self):
        """When analyze_on=['success', 'failure'], both events spawn tasks."""
        config = SemanticLearningConfig(analyze_on=["success", "failure"])
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {
            "test-job": _make_live_state(),
        }

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)

        # Mock _analyze_with_semaphore to avoid actual LLM calls
        analyzer._analyze_with_semaphore = AsyncMock()  # type: ignore[method-assign]

        event1 = _make_event(event="sheet.completed")
        await analyzer._on_sheet_event(event1)

        event2 = _make_event(event="sheet.failed")
        await analyzer._on_sheet_event(event2)

        # Both events should have spawned analysis tasks
        # Wait for async tasks to complete
        await asyncio.sleep(0.05)
        assert analyzer._analyze_with_semaphore.call_count == 2


# ─── Missing Live State Tests ────────────────────────────────────────


class TestMissingLiveState:
    """Test behavior when live state is missing."""

    @pytest.mark.asyncio
    async def test_no_live_state_skips_analysis(self):
        """When no live state exists for the job, analysis is skipped."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        live_states: dict[str, CheckpointState] = {}  # Empty

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        event = _make_event(event="sheet.completed")
        await analyzer._on_sheet_event(event)

        assert len(analyzer._pending_tasks) == 0

    @pytest.mark.asyncio
    async def test_live_state_without_sheet_still_works(self):
        """When live state exists but sheet is missing, uses event data."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        # Live state with no sheets
        live_state = CheckpointState(job_id="test-job", job_name="test", total_sheets=1, sheets={})
        live_states: dict[str, CheckpointState] = {"test-job": live_state}

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        event = _make_event(event="sheet.completed", data={"stdout_tail": "some output"})

        sheet_data = analyzer._extract_sheet_data("test-job", 1, event)
        assert sheet_data is not None
        assert sheet_data["stdout_tail"] == "some output"


# ─── Prompt Construction Tests ───────────────────────────────────────


class TestPromptConstruction:
    """Test _build_analysis_prompt output."""

    def test_prompt_contains_outcome(self):
        """Prompt should indicate SUCCESS or FAILURE."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        data = {
            "event_type": "sheet.completed",
            "stdout_tail": "All done",
            "stderr_tail": "",
            "validation_details": [],
            "exit_code": 0,
            "retry_count": 0,
            "duration_seconds": 10.0,
            "job_id": "test-job",
            "sheet_num": 1,
        }
        prompt = analyzer._build_analysis_prompt(data)
        assert "SUCCESS" in prompt

    def test_prompt_contains_failure_outcome(self):
        """Prompt should show FAILURE for failed sheets."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        data = {
            "event_type": "sheet.failed",
            "stdout_tail": "Error occurred",
            "stderr_tail": "traceback...",
            "validation_details": [],
            "exit_code": 1,
            "retry_count": 2,
            "duration_seconds": 5.0,
            "job_id": "test-job",
            "sheet_num": 1,
        }
        prompt = analyzer._build_analysis_prompt(data)
        assert "FAILURE" in prompt

    def test_prompt_includes_validation_details(self):
        """Prompt should include formatted validation results."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        data = {
            "event_type": "sheet.completed",
            "stdout_tail": "",
            "stderr_tail": "",
            "validation_details": [
                {"passed": True, "description": "File exists"},
                {"passed": False, "description": "Content matches"},
            ],
            "exit_code": 0,
            "retry_count": 0,
            "duration_seconds": 1.0,
            "job_id": "test-job",
            "sheet_num": 1,
        }
        prompt = analyzer._build_analysis_prompt(data)
        assert "[PASS] File exists" in prompt
        assert "[FAIL] Content matches" in prompt

    def test_prompt_truncates_long_output(self):
        """Stdout should be truncated to 3000 chars."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        marker = "Z"
        data = {
            "event_type": "sheet.completed",
            "stdout_tail": marker * 5000,
            "stderr_tail": "",
            "validation_details": [],
            "exit_code": 0,
            "retry_count": 0,
            "duration_seconds": 1.0,
            "job_id": "test-job",
            "sheet_num": 1,
        }
        prompt = analyzer._build_analysis_prompt(data)
        # The prompt should contain at most 3000 Z's (truncated from 5000)
        assert prompt.count(marker) == 3000


# ─── Response Parsing Tests ──────────────────────────────────────────


class TestResponseParsing:
    """Test _parse_analysis_response."""

    def test_parse_valid_json(self):
        """Valid JSON array should be parsed correctly."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        response = _make_llm_response_json([
            {
                "insight": "Execution failed due to missing file",
                "category": "root_cause",
                "confidence": 0.9,
            },
            {
                "insight": "Always create workspace first",
                "category": "knowledge",
                "confidence": 0.8,
            },
        ])
        insights = analyzer._parse_analysis_response(response)
        assert len(insights) == 2
        assert insights[0]["category"] == "root_cause"
        assert insights[1]["category"] == "knowledge"

    def test_parse_markdown_wrapped_json(self):
        """JSON inside markdown code blocks should be extracted."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        response = """Here are my insights:

```json
[{"insight": "Test insight", "category": "root_cause", "confidence": 0.8}]
```
"""
        insights = analyzer._parse_analysis_response(response)
        assert len(insights) == 1
        assert insights[0]["insight"] == "Test insight"

    def test_parse_invalid_json_returns_empty(self):
        """Invalid JSON should return empty list."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        insights = analyzer._parse_analysis_response("not json at all")
        assert insights == []

    def test_parse_filters_invalid_categories(self):
        """Insights with invalid categories should be filtered out."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        response = _make_llm_response_json([
            {"insight": "Valid", "category": "root_cause", "confidence": 0.8},
            {"insight": "Invalid", "category": "not_a_category", "confidence": 0.8},
        ])
        insights = analyzer._parse_analysis_response(response)
        assert len(insights) == 1
        assert insights[0]["insight"] == "Valid"

    def test_parse_clamps_confidence(self):
        """Confidence should be clamped to [0.0, 1.0]."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        response = _make_llm_response_json([
            {"insight": "Too high", "category": "root_cause", "confidence": 5.0},
            {"insight": "Too low", "category": "knowledge", "confidence": -1.0},
        ])
        insights = analyzer._parse_analysis_response(response)
        assert insights[0]["confidence"] == 1.0
        assert insights[1]["confidence"] == 0.0

    def test_parse_missing_insight_text_filtered(self):
        """Items without insight text are filtered out."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        response = _make_llm_response_json([
            {"category": "root_cause", "confidence": 0.8},  # No insight text
            {"insight": "Valid", "category": "root_cause", "confidence": 0.8},
        ])
        insights = analyzer._parse_analysis_response(response)
        assert len(insights) == 1


# ─── Insight Storage Tests ───────────────────────────────────────────


class TestInsightStorage:
    """Test _store_insights stores patterns correctly."""

    def test_stores_insights_as_patterns(self):
        """Each insight should be stored as a pattern via record_pattern."""
        config = SemanticLearningConfig()
        mock_store = MagicMock()
        hub = MagicMock(spec=LearningHub)
        hub.is_running = True
        hub.store = mock_store

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        insights = [
            {"insight": "Root cause found", "category": "root_cause", "confidence": 0.9},
            {"insight": "Avoid this pattern", "category": "anti_pattern", "confidence": 0.7},
        ]

        analyzer._store_insights("test-job", 1, "sheet.completed", insights)

        assert mock_store.record_pattern.call_count == 2
        # Check first call
        call_args = mock_store.record_pattern.call_args_list[0]
        assert call_args.kwargs["pattern_type"] == "semantic_insight"
        assert "root_cause" in call_args.kwargs["pattern_name"]
        assert "outcome:success" in call_args.kwargs["context_tags"]
        assert "job:test-job" in call_args.kwargs["context_tags"]
        assert "sheet:1" in call_args.kwargs["context_tags"]

    def test_suggested_action_for_actionable_categories(self):
        """Actionable categories should have suggested_action set."""
        config = SemanticLearningConfig()
        mock_store = MagicMock()
        hub = MagicMock(spec=LearningHub)
        hub.is_running = True
        hub.store = mock_store

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        insights = [
            {"insight": "Actionable tip", "category": "prompt_improvement", "confidence": 0.8},
            {"insight": "Just a root cause", "category": "root_cause", "confidence": 0.9},
        ]

        analyzer._store_insights("test-job", 1, "sheet.failed", insights)

        # prompt_improvement should have suggested_action
        call1 = mock_store.record_pattern.call_args_list[0]
        assert call1.kwargs["suggested_action"] == "Actionable tip"

        # root_cause should not
        call2 = mock_store.record_pattern.call_args_list[1]
        assert call2.kwargs["suggested_action"] is None

    def test_store_not_running_skips_storage(self):
        """When learning hub is not running, storage should be skipped."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        hub.is_running = False

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        insights = [
            {"insight": "Test", "category": "root_cause", "confidence": 0.8},
        ]

        # Should not raise
        analyzer._store_insights("test-job", 1, "sheet.completed", insights)


# ─── Concurrency Tests ──────────────────────────────────────────────


class TestConcurrency:
    """Test semaphore-based concurrency limiting."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_analyses(self):
        """Max concurrent analyses should be enforced by semaphore."""
        config = SemanticLearningConfig(max_concurrent_analyses=2)
        hub = MagicMock(spec=LearningHub)
        hub.is_running = True
        mock_store = MagicMock()
        hub.store = mock_store

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        # The semaphore should have the configured value
        assert analyzer._semaphore._value == 2


# ─── LLM Error Handling Tests ───────────────────────────────────────


class TestLLMErrorHandling:
    """Test graceful degradation when LLM fails."""

    @pytest.mark.asyncio
    async def test_llm_failure_does_not_raise(self):
        """LLM failures should be logged but not propagate."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        hub.is_running = True
        mock_store = MagicMock()
        hub.store = mock_store

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})

        # Mock _call_llm to raise
        analyzer._call_llm = AsyncMock(side_effect=RuntimeError("API down"))  # type: ignore[method-assign]

        sheet_data = {
            "event_type": "sheet.completed",
            "stdout_tail": "output",
            "stderr_tail": "",
            "validation_details": [],
            "exit_code": 0,
            "retry_count": 0,
            "duration_seconds": 1.0,
            "job_id": "test-job",
            "sheet_num": 1,
        }

        # Should not raise
        await analyzer._analyze_sheet("test-job", 1, "sheet.completed", sheet_data)

        # Store should not have been called
        mock_store.record_pattern.assert_not_called()


# ─── Extract Sheet Data Tests ───────────────────────────────────────


class TestExtractSheetData:
    """Test _extract_sheet_data method."""

    def test_extracts_from_live_state(self):
        """Should extract data from the live CheckpointState."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)
        live_states = {"test-job": _make_live_state(
            stdout_tail="hello world",
            stderr_tail="some error",
            exit_code=1,
        )}

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, live_states)
        event = _make_event()

        data = analyzer._extract_sheet_data("test-job", 1, event)
        assert data is not None
        assert data["stdout_tail"] == "hello world"
        assert data["stderr_tail"] == "some error"
        assert data["exit_code"] == 1

    def test_returns_none_for_missing_job(self):
        """Should return None when job not in live states."""
        config = SemanticLearningConfig()
        hub = MagicMock(spec=LearningHub)

        analyzer = SemanticAnalyzer(config, _make_mock_backend(), hub, {})
        event = _make_event()

        data = analyzer._extract_sheet_data("nonexistent", 1, event)
        assert data is None
