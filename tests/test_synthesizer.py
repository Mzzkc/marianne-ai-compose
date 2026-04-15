"""Tests for Result Synthesizer Pattern (v18 evolution).

Comprehensive tests for the ResultSynthesizer and synthesis integration:
- Unit tests for SynthesisResult and SynthesisConfig
- Integration tests with mock parallel results
- Strategy tests (merge, summarize, pass_through)
- Error handling tests
- CheckpointState synthesis storage tests
- CLI display tests

Note: These tests follow patterns from test_parallel.py for consistency.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from marianne.core.config import JobConfig
from marianne.core.config.execution import ParallelConfig
from marianne.core.config.workspace import IsolationConfig

import pytest

from marianne.core.checkpoint import CheckpointState
try:
    from marianne.execution.parallel import ParallelBatchResult
except ImportError:
    ParallelBatchResult = None  # type: ignore[assignment,misc]
from marianne.execution.synthesizer import (
    ResultSynthesizer,
    SynthesisConfig,
    SynthesisResult,
    SynthesisStrategy,
    synthesize_batch,
)

# =============================================================================
# Fixtures (adapted from test_parallel.py patterns)
# =============================================================================


@pytest.fixture
def mock_state():
    """Create a mock CheckpointState for testing."""
    state = MagicMock(spec=CheckpointState)
    state.job_id = "test-job"
    state.total_sheets = 5
    state.sheets = {}
    state.synthesis_results = {}
    return state


@pytest.fixture
def synthesis_config():
    """Create default synthesis config."""
    return SynthesisConfig()


@pytest.fixture
def sample_outputs():
    """Create sample sheet outputs for testing."""
    return {
        1: "Output from sheet 1\nWith multiple lines",
        2: "Output from sheet 2\nAlso multiline",
        3: "Output from sheet 3\nFinal content here",
    }


@pytest.fixture
def batch_result_success():
    """Create a successful batch result."""
    return ParallelBatchResult(
        sheets=[1, 2, 3],
        completed=[1, 2, 3],
        failed=[],
        skipped=[],
        duration_seconds=10.5,
    )


@pytest.fixture
def batch_result_partial():
    """Create a partial success batch result."""
    return ParallelBatchResult(
        sheets=[1, 2, 3],
        completed=[1, 2],
        failed=[3],
        error_details={3: "Sheet 3 failed"},
        duration_seconds=15.0,
    )


# =============================================================================
# SynthesisStrategy Tests
# =============================================================================


class TestSynthesisStrategy:
    """Tests for SynthesisStrategy enum."""

    def test_strategy_values(self) -> None:
        """Strategy enum has expected values."""
        assert SynthesisStrategy.MERGE.value == "merge"
        assert SynthesisStrategy.SUMMARIZE.value == "summarize"
        assert SynthesisStrategy.PASS_THROUGH.value == "pass_through"

    def test_strategy_from_string(self) -> None:
        """Strategy can be created from string."""
        assert SynthesisStrategy("merge") == SynthesisStrategy.MERGE
        assert SynthesisStrategy("summarize") == SynthesisStrategy.SUMMARIZE
        assert SynthesisStrategy("pass_through") == SynthesisStrategy.PASS_THROUGH


# =============================================================================
# SynthesisConfig Tests
# =============================================================================


class TestSynthesisConfig:
    """Tests for SynthesisConfig dataclass."""

    def test_default_values(self) -> None:
        """Default config has expected values."""
        config = SynthesisConfig()

        assert config.strategy == SynthesisStrategy.MERGE
        assert config.include_metadata is True
        assert config.max_content_bytes == 1024 * 1024  # 1MB
        assert config.fail_on_partial is False

    def test_custom_values(self) -> None:
        """Custom config values are stored correctly."""
        config = SynthesisConfig(
            strategy=SynthesisStrategy.SUMMARIZE,
            include_metadata=False,
            max_content_bytes=5000,
            fail_on_partial=True,
        )

        assert config.strategy == SynthesisStrategy.SUMMARIZE
        assert config.include_metadata is False
        assert config.max_content_bytes == 5000
        assert config.fail_on_partial is True


# =============================================================================
# SynthesisResult Tests
# =============================================================================


class TestSynthesisResult:
    """Tests for SynthesisResult dataclass."""

    def test_default_values(self) -> None:
        """Result has expected defaults."""
        result = SynthesisResult(batch_id="test_batch")

        assert result.batch_id == "test_batch"
        assert result.sheets == []
        assert result.strategy == SynthesisStrategy.MERGE
        assert result.status == "pending"
        assert result.sheet_outputs == {}
        assert result.synthesized_content is None
        assert result.error_message is None
        assert result.metadata == {}

    def test_is_complete_property(self) -> None:
        """is_complete returns True for done or failed."""
        result = SynthesisResult(batch_id="test")

        result.status = "pending"
        assert result.is_complete is False

        result.status = "ready"
        assert result.is_complete is False

        result.status = "done"
        assert result.is_complete is True

        result.status = "failed"
        assert result.is_complete is True

    def test_is_success_property(self) -> None:
        """is_success returns True only for done status."""
        result = SynthesisResult(batch_id="test")

        result.status = "pending"
        assert result.is_success is False

        result.status = "done"
        assert result.is_success is True

        result.status = "failed"
        assert result.is_success is False

    def test_to_dict(self) -> None:
        """to_dict serializes all fields correctly."""
        result = SynthesisResult(
            batch_id="batch_123",
            sheets=[1, 2, 3],
            strategy=SynthesisStrategy.SUMMARIZE,
            status="done",
            sheet_outputs={1: "out1", 2: "out2"},
            synthesized_content="merged content",
            metadata={"key": "value"},
        )

        data = result.to_dict()

        assert data["batch_id"] == "batch_123"
        assert data["sheets"] == [1, 2, 3]
        assert data["strategy"] == "summarize"
        assert data["status"] == "done"
        assert data["sheet_outputs"] == {1: "out1", 2: "out2"}
        assert data["synthesized_content"] == "merged content"
        assert data["metadata"] == {"key": "value"}
        assert "created_at" in data

    def test_from_dict(self) -> None:
        """from_dict deserializes correctly."""
        data = {
            "batch_id": "batch_456",
            "sheets": [4, 5],
            "strategy": "pass_through",
            "status": "ready",
            "created_at": "2025-01-15T10:30:00",
            "completed_at": None,
            "sheet_outputs": {4: "x", 5: "y"},
            "synthesized_content": None,
            "error_message": None,
            "metadata": {},
        }

        result = SynthesisResult.from_dict(data)

        assert result.batch_id == "batch_456"
        assert result.sheets == [4, 5]
        assert result.strategy == SynthesisStrategy.PASS_THROUGH
        assert result.status == "ready"
        assert result.sheet_outputs == {4: "x", 5: "y"}

    def test_roundtrip_serialization(self) -> None:
        """to_dict -> from_dict preserves data."""
        original = SynthesisResult(
            batch_id="roundtrip",
            sheets=[1, 2, 3],
            strategy=SynthesisStrategy.MERGE,
            status="done",
            sheet_outputs={1: "a", 2: "b", 3: "c"},
            synthesized_content="merged",
            metadata={"foo": "bar"},
        )

        data = original.to_dict()
        restored = SynthesisResult.from_dict(data)

        assert restored.batch_id == original.batch_id
        assert restored.sheets == original.sheets
        assert restored.strategy == original.strategy
        assert restored.status == original.status
        assert restored.sheet_outputs == original.sheet_outputs
        assert restored.synthesized_content == original.synthesized_content
        assert restored.metadata == original.metadata


# =============================================================================
# ResultSynthesizer Tests - Preparation
# =============================================================================


class TestResultSynthesizerPrepare:
    """Tests for ResultSynthesizer.prepare_synthesis()."""

    def test_prepare_with_all_completed(self, sample_outputs):
        """Prepare synthesis with all sheets completed."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2, 3],
            failed_sheets=[],
            sheet_outputs=sample_outputs,
        )

        assert result.status == "ready"
        assert result.sheets == [1, 2, 3]
        assert len(result.sheet_outputs) == 3
        assert result.error_message is None

    def test_prepare_with_partial_completion(self, sample_outputs):
        """Prepare synthesis with some sheets failed."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2],
            failed_sheets=[3],
            sheet_outputs={k: v for k, v in sample_outputs.items() if k != 3},
        )

        assert result.status == "ready"
        assert len(result.sheet_outputs) == 2
        assert 3 not in result.sheet_outputs

    def test_prepare_fail_on_partial(self, sample_outputs):
        """Prepare fails if fail_on_partial is True and sheets failed."""
        config = SynthesisConfig(fail_on_partial=True)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2],
            failed_sheets=[3],
            sheet_outputs={k: v for k, v in sample_outputs.items() if k != 3},
        )

        assert result.status == "failed"
        assert "requires all sheets" in result.error_message

    def test_prepare_with_no_outputs(self):
        """Prepare fails with no sheet outputs."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2, 3],
            failed_sheets=[],
            sheet_outputs={},
        )

        assert result.status == "failed"
        assert "No sheet outputs" in result.error_message

    def test_prepare_generates_batch_id(self, sample_outputs):
        """Prepare generates unique batch ID."""
        synthesizer = ResultSynthesizer()

        result1 = synthesizer.prepare_synthesis(
            batch_sheets=[1],
            completed_sheets=[1],
            failed_sheets=[],
            sheet_outputs={1: sample_outputs[1]},
        )

        result2 = synthesizer.prepare_synthesis(
            batch_sheets=[2],
            completed_sheets=[2],
            failed_sheets=[],
            sheet_outputs={2: sample_outputs[2]},
        )

        assert result1.batch_id != result2.batch_id
        assert result1.batch_id.startswith("batch_")
        assert result2.batch_id.startswith("batch_")

    def test_prepare_records_metadata(self, sample_outputs):
        """Prepare records batch metadata."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2],
            failed_sheets=[3],
            sheet_outputs={1: sample_outputs[1], 2: sample_outputs[2]},
        )

        assert result.metadata["batch_size"] == 3
        assert result.metadata["completed_count"] == 2
        assert result.metadata["failed_count"] == 1
        assert result.metadata["outputs_captured"] == 2


# =============================================================================
# ResultSynthesizer Tests - Execution
# =============================================================================


class TestResultSynthesizerExecute:
    """Tests for ResultSynthesizer.execute_synthesis()."""

    def test_execute_merge_strategy(self, sample_outputs):
        """Execute merge strategy combines outputs."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs={1: sample_outputs[1], 2: sample_outputs[2]},
        )

        result = synthesizer.execute_synthesis(result)

        assert result.status == "done"
        assert result.synthesized_content is not None
        assert "Sheet 1" in result.synthesized_content
        assert "Sheet 2" in result.synthesized_content
        assert sample_outputs[1] in result.synthesized_content
        assert sample_outputs[2] in result.synthesized_content

    def test_execute_merge_without_metadata(self, sample_outputs):
        """Execute merge without metadata separators."""
        config = SynthesisConfig(include_metadata=False)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs={1: sample_outputs[1], 2: sample_outputs[2]},
        )

        result = synthesizer.execute_synthesis(result)

        assert result.status == "done"
        assert "--- Sheet" not in result.synthesized_content

    def test_execute_summarize_strategy(self, sample_outputs):
        """Execute summarize strategy creates summary."""
        config = SynthesisConfig(strategy=SynthesisStrategy.SUMMARIZE)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2, 3],
            failed_sheets=[],
            sheet_outputs=sample_outputs,
        )

        result = synthesizer.execute_synthesis(result)

        assert result.status == "done"
        assert "Summary" in result.synthesized_content
        assert "3 sheets" in result.synthesized_content

    def test_execute_pass_through_strategy(self, sample_outputs):
        """Execute pass_through returns JSON."""
        config = SynthesisConfig(strategy=SynthesisStrategy.PASS_THROUGH)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs={1: sample_outputs[1], 2: sample_outputs[2]},
        )

        result = synthesizer.execute_synthesis(result)

        assert result.status == "done"
        # Should be valid JSON
        parsed = json.loads(result.synthesized_content)
        assert "1" in parsed
        assert "2" in parsed

    def test_execute_size_limit_exceeded(self):
        """Execute fails if synthesized content exceeds limit."""
        config = SynthesisConfig(max_content_bytes=50)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1],
            completed_sheets=[1],
            failed_sheets=[],
            sheet_outputs={1: "A" * 100},  # Exceeds 50 byte limit
        )

        result = synthesizer.execute_synthesis(result)

        assert result.status == "failed"
        assert "exceeds limit" in result.error_message

    def test_execute_on_non_ready_status(self, sample_outputs):
        """Execute raises ValueError if status is not ready."""
        synthesizer = ResultSynthesizer()

        result = SynthesisResult(batch_id="test", status="failed")
        with pytest.raises(ValueError, match="expected 'ready'"):
            synthesizer.execute_synthesis(result)

    def test_execute_sets_completed_at(self, sample_outputs):
        """Execute sets completed_at timestamp."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1],
            completed_sheets=[1],
            failed_sheets=[],
            sheet_outputs={1: sample_outputs[1]},
        )

        assert result.completed_at is None

        result = synthesizer.execute_synthesis(result)

        assert result.completed_at is not None
        assert isinstance(result.completed_at, datetime)


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestSynthesizeBatch:
    """Tests for synthesize_batch convenience function."""

    def test_synthesize_batch_success(self, sample_outputs):
        """synthesize_batch completes full workflow."""
        result = synthesize_batch(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2, 3],
            failed_sheets=[],
            sheet_outputs=sample_outputs,
        )

        assert result.status == "done"
        assert result.synthesized_content is not None

    def test_synthesize_batch_with_config(self, sample_outputs):
        """synthesize_batch accepts custom config."""
        config = SynthesisConfig(strategy=SynthesisStrategy.PASS_THROUGH)

        result = synthesize_batch(
            batch_sheets=[1],
            completed_sheets=[1],
            failed_sheets=[],
            sheet_outputs={1: sample_outputs[1]},
            config=config,
        )

        assert result.status == "done"
        # Pass-through produces JSON
        parsed = json.loads(result.synthesized_content)
        assert "1" in parsed

    def test_synthesize_batch_empty_outputs(self):
        """synthesize_batch fails with empty outputs."""
        result = synthesize_batch(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs={},
        )

        assert result.status == "failed"


# =============================================================================
# CheckpointState Synthesis Integration Tests
# =============================================================================


class TestCheckpointStateSynthesis:
    """Tests for CheckpointState synthesis methods."""

    def test_add_synthesis(self):
        """add_synthesis stores result correctly."""
        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=5,
        )

        result_data = {"batch_id": "batch_1", "status": "done"}
        state.add_synthesis("batch_1", result_data)

        assert "batch_1" in state.synthesis_results
        assert state.synthesis_results["batch_1"]["status"] == "done"


# =============================================================================
# ParallelBatchResult Synthesis Fields Tests
# =============================================================================


@pytest.mark.skipif(ParallelBatchResult is None, reason="ParallelBatchResult removed with runner")
class TestParallelBatchResultSynthesis:
    """Tests for ParallelBatchResult synthesis-related fields."""

    def test_default_synthesis_fields(self):
        """New fields have correct defaults."""
        result = ParallelBatchResult()

        assert result.sheet_outputs == {}
        assert result.synthesis_ready is False

    def test_to_dict_includes_synthesis_fields(self):
        """to_dict includes synthesis fields."""
        result = ParallelBatchResult(
            sheets=[1, 2],
            completed=[1, 2],
            sheet_outputs={1: "out1", 2: "out2"},
            synthesis_ready=True,
        )

        data = result.to_dict()

        assert "sheet_outputs" in data
        assert data["sheet_outputs"] == {1: "out1", 2: "out2"}
        assert data["synthesis_ready"] is True

    def test_synthesis_fields_preserved(self):
        """Synthesis fields can be set and retrieved."""
        result = ParallelBatchResult(
            sheets=[1, 2, 3],
            completed=[1, 2],
            failed=[3],
        )

        result.sheet_outputs = {1: "content1", 2: "content2"}
        result.synthesis_ready = True

        assert len(result.sheet_outputs) == 2
        assert result.synthesis_ready is True


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestSynthesizerErrorHandling:
    """Tests for synthesizer error handling."""

    def test_empty_batch(self):
        """Empty batch is handled gracefully."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[],
            completed_sheets=[],
            failed_sheets=[],
            sheet_outputs={},
        )

        assert result.status == "failed"

    def test_all_sheets_failed(self):
        """All sheets failed is handled."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[],
            failed_sheets=[1, 2, 3],
            sheet_outputs={},
        )

        assert result.status == "failed"

    def test_missing_output_for_completed_sheet(self):
        """Missing output for completed sheet is handled."""
        synthesizer = ResultSynthesizer()

        # Sheet 2 completed but no output provided
        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs={1: "output1"},  # Missing output for sheet 2
        )

        # Should still succeed with available outputs
        assert result.status == "ready"
        assert 2 not in result.sheet_outputs


# =============================================================================
# Integration Tests with Mock Runner
# =============================================================================


class TestSynthesizerRunnerIntegration:
    """Tests for synthesizer integration with runner.

    The runner has been removed — synthesis is now handled by the baton.
    """

    @pytest.mark.skip(reason="Runner removed — synthesis now in baton")
    async def test_runner_synthesize_batch_outputs(self):
        """Obsolete: JobRunner no longer exists."""


# =============================================================================
# CLI Display Tests
# =============================================================================


class TestCLISynthesisDisplay:
    """Tests for CLI synthesis display."""

    def test_status_rich_shows_synthesis(self):
        """_output_status_rich includes synthesis section."""
        from marianne.cli import _output_status_rich

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=5,
        )

        state.synthesis_results = {
            "batch_123": {
                "sheets": [1, 2, 3],
                "strategy": "merge",
                "status": "done",
            },
        }

        # Capture output - patch where console is used, not where it's re-exported
        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(state)

            # Find synthesis section in calls
            call_args_list = mock_console.print.call_args_list
            synthesis_found = False
            for call in call_args_list:
                if call.args and "Synthesis Results" in str(call.args[0]):
                    synthesis_found = True
                    break

            assert synthesis_found, "Synthesis Results section not found in output"

    def test_status_rich_skips_empty_synthesis(self):
        """_output_status_rich skips synthesis when empty."""
        from marianne.cli import _output_status_rich

        state = CheckpointState(
            job_id="test-job",
            job_name="Test Job",
            total_sheets=5,
        )

        # Empty synthesis_results
        state.synthesis_results = {}

        with patch("marianne.cli.commands.status.console") as mock_console:
            _output_status_rich(state)

            call_args_list = mock_console.print.call_args_list
            synthesis_found = False
            for call in call_args_list:
                if call.args and "Synthesis Results" in str(call.args[0]):
                    synthesis_found = True
                    break

            assert not synthesis_found, "Synthesis section should be skipped when empty"


# =============================================================================
# Parallel Output Conflict Detection Tests (v20 evolution)
# =============================================================================


class TestOutputConflict:
    """Tests for OutputConflict dataclass."""

    def test_create_conflict(self) -> None:
        """Test creating an output conflict."""
        from marianne.execution.synthesizer import OutputConflict

        conflict = OutputConflict(
            key="STATUS",
            sheet_a=1,
            value_a="complete",
            sheet_b=2,
            value_b="failed",
            severity="warning",
        )

        assert conflict.key == "STATUS"
        assert conflict.sheet_a == 1
        assert conflict.value_a == "complete"
        assert conflict.sheet_b == 2
        assert conflict.value_b == "failed"
        assert conflict.severity == "warning"

    def test_format_message(self) -> None:
        """Test format_message produces readable output."""
        from marianne.execution.synthesizer import OutputConflict

        conflict = OutputConflict(
            key="VERSION",
            sheet_a=1,
            value_a="1.0",
            sheet_b=3,
            value_b="2.0",
        )

        msg = conflict.format_message()
        assert "VERSION" in msg
        assert "sheet 1" in msg
        assert "1.0" in msg
        assert "sheet 3" in msg
        assert "2.0" in msg


class TestConflictDetectionResult:
    """Tests for ConflictDetectionResult dataclass."""

    def test_empty_result(self) -> None:
        """Test empty result has no conflicts."""
        from marianne.execution.synthesizer import ConflictDetectionResult

        result = ConflictDetectionResult()

        assert result.has_conflicts is False
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.keys_checked == 0

    def test_result_with_conflicts(self) -> None:
        """Test result with conflicts."""
        from marianne.execution.synthesizer import (
            ConflictDetectionResult,
            OutputConflict,
        )

        result = ConflictDetectionResult(
            sheets_analyzed=[1, 2, 3],
            conflicts=[
                OutputConflict(
                    key="A", sheet_a=1, value_a="x", sheet_b=2, value_b="y",
                    severity="error",
                ),
                OutputConflict(
                    key="B", sheet_a=1, value_a="p", sheet_b=3, value_b="q",
                    severity="warning",
                ),
            ],
            keys_checked=5,
        )

        assert result.has_conflicts is True
        assert result.error_count == 1
        assert result.warning_count == 1
        assert result.keys_checked == 5

    def test_to_dict(self) -> None:
        """Test to_dict serialization."""
        from marianne.execution.synthesizer import (
            ConflictDetectionResult,
            OutputConflict,
        )

        result = ConflictDetectionResult(
            sheets_analyzed=[1, 2],
            conflicts=[
                OutputConflict(
                    key="X", sheet_a=1, value_a="a", sheet_b=2, value_b="b",
                ),
            ],
            keys_checked=3,
        )

        data = result.to_dict()

        assert data["sheets_analyzed"] == [1, 2]
        assert len(data["conflicts"]) == 1
        assert data["conflicts"][0]["key"] == "X"
        assert data["keys_checked"] == 3
        assert data["has_conflicts"] is True
        assert "checked_at" in data


class TestConflictDetector:
    """Tests for ConflictDetector class."""

    def test_detect_no_conflicts(self) -> None:
        """Test detecting when there are no conflicts."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: complete\nVERSION: 1.0",
            2: "STATUS: complete\nVERSION: 1.0",
        }

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        assert result.has_conflicts is False
        assert len(result.conflicts) == 0
        assert result.sheets_analyzed == [1, 2]

    def test_detect_single_conflict(self) -> None:
        """Test detecting a single conflict."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: complete\nVERSION: 1.0",
            2: "STATUS: failed\nVERSION: 1.0",
        }

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        assert result.has_conflicts is True
        assert len(result.conflicts) == 1
        assert result.conflicts[0].key == "STATUS"
        assert result.conflicts[0].value_a == "complete"
        assert result.conflicts[0].value_b == "failed"

    def test_detect_multiple_conflicts(self) -> None:
        """Test detecting multiple conflicts."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: complete\nVERSION: 1.0",
            2: "STATUS: failed\nVERSION: 2.0",
        }

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        assert len(result.conflicts) == 2

    def test_detect_with_key_filter(self) -> None:
        """Test key filter restricts conflict detection."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: a\nVERSION: 1",
            2: "STATUS: b\nVERSION: 2",
        }

        detector = ConflictDetector(key_filter=["STATUS"])
        result = detector.detect_conflicts(outputs)

        # Should only find STATUS conflict
        assert len(result.conflicts) == 1
        assert result.conflicts[0].key == "STATUS"

    def test_detect_strict_mode(self) -> None:
        """Test strict mode marks conflicts as errors."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: a",
            2: "STATUS: b",
        }

        detector = ConflictDetector(strict_mode=True)
        result = detector.detect_conflicts(outputs)

        assert result.conflicts[0].severity == "error"

    def test_detect_non_strict_mode(self) -> None:
        """Test non-strict mode marks conflicts as warnings."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: a",
            2: "STATUS: b",
        }

        detector = ConflictDetector(strict_mode=False)
        result = detector.detect_conflicts(outputs)

        assert result.conflicts[0].severity == "warning"

    def test_detect_case_insensitive(self) -> None:
        """Test case-insensitive value comparison."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: Complete",
            2: "STATUS: COMPLETE",
        }

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        # Should be no conflict due to case-insensitivity
        assert result.has_conflicts is False

    def test_detect_single_sheet(self) -> None:
        """Test with single sheet returns no conflicts."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {1: "STATUS: complete"}

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        assert result.has_conflicts is False
        assert result.sheets_analyzed == [1]

    def test_detect_empty_outputs(self) -> None:
        """Test with empty outputs."""
        from marianne.execution.synthesizer import ConflictDetector

        detector = ConflictDetector()
        result = detector.detect_conflicts({})

        assert result.has_conflicts is False
        assert result.sheets_analyzed == []

    def test_detect_three_sheets(self) -> None:
        """Test conflict detection across three sheets.

        Uses canonical reference comparison (first sheet vs all others),
        so 1 vs 3 conflict is detected (reference vs differing sheet).
        """
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: a",
            2: "STATUS: a",  # Same as reference (sheet 1)
            3: "STATUS: b",  # Different from reference (sheet 1)
        }

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        # Canonical reference (sheet 1) compared against sheets 2 and 3:
        # - 1 vs 2: same value "a" -> no conflict
        # - 1 vs 3: "a" vs "b" -> conflict detected
        assert len(result.conflicts) == 1
        assert result.conflicts[0].sheet_a == 1
        assert result.conflicts[0].sheet_b == 3

    def test_detect_disjoint_keys(self) -> None:
        """Test sheets with no common keys have no conflicts."""
        from marianne.execution.synthesizer import ConflictDetector

        outputs = {
            1: "STATUS: a",
            2: "VERSION: 1.0",
        }

        detector = ConflictDetector()
        result = detector.detect_conflicts(outputs)

        assert result.has_conflicts is False


class TestDetectParallelConflicts:
    """Tests for detect_parallel_conflicts convenience function."""

    def test_convenience_function(self) -> None:
        """Test the convenience function works."""
        from marianne.execution.synthesizer import detect_parallel_conflicts

        outputs = {
            1: "STATUS: a",
            2: "STATUS: b",
        }

        result = detect_parallel_conflicts(outputs)

        assert result.has_conflicts is True

    def test_convenience_with_key_filter(self) -> None:
        """Test convenience function with key filter."""
        from marianne.execution.synthesizer import detect_parallel_conflicts

        outputs = {
            1: "STATUS: a\nVERSION: 1",
            2: "STATUS: b\nVERSION: 2",
        }

        result = detect_parallel_conflicts(outputs, key_filter=["STATUS"])

        assert len(result.conflicts) == 1

    def test_convenience_with_strict_mode(self) -> None:
        """Test convenience function with strict mode."""
        from marianne.execution.synthesizer import detect_parallel_conflicts

        outputs = {
            1: "STATUS: a",
            2: "STATUS: b",
        }

        result = detect_parallel_conflicts(outputs, strict_mode=True)

        assert result.conflicts[0].severity == "error"


class TestSynthesizerConflictIntegration:
    """Tests for ResultSynthesizer conflict detection integration."""

    def test_prepare_without_conflict_detection(self, sample_outputs):
        """Test prepare without conflict detection enabled."""
        synthesizer = ResultSynthesizer()

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2, 3],
            failed_sheets=[],
            sheet_outputs=sample_outputs,
        )

        assert result.status == "ready"
        assert result.conflict_detection is None

    def test_prepare_with_conflict_detection_no_conflicts(self, sample_outputs):
        """Test prepare with conflict detection when no conflicts."""
        config = SynthesisConfig(detect_conflicts=True)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2, 3],
            completed_sheets=[1, 2, 3],
            failed_sheets=[],
            sheet_outputs=sample_outputs,
        )

        assert result.status == "ready"
        assert result.conflict_detection is not None
        assert result.conflict_detection["has_conflicts"] is False

    def test_prepare_with_conflict_detection_finds_conflicts(self):
        """Test prepare with conflict detection when conflicts exist."""
        config = SynthesisConfig(detect_conflicts=True)
        synthesizer = ResultSynthesizer(config)

        outputs = {
            1: "STATUS: complete\nVERSION: 1.0",
            2: "STATUS: failed\nVERSION: 1.0",  # STATUS conflicts
        }

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs=outputs,
        )

        assert result.status == "ready"  # Still ready, just warnings
        assert result.conflict_detection is not None
        assert result.conflict_detection["has_conflicts"] is True
        assert len(result.conflict_detection["conflicts"]) == 1

    def test_prepare_fail_on_conflict(self):
        """Test prepare fails when fail_on_conflict is True."""
        config = SynthesisConfig(detect_conflicts=True, fail_on_conflict=True)
        synthesizer = ResultSynthesizer(config)

        outputs = {
            1: "STATUS: complete",
            2: "STATUS: failed",
        }

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs=outputs,
        )

        assert result.status == "failed"
        assert "conflicts" in result.error_message.lower()

    def test_prepare_with_key_filter(self):
        """Test prepare with conflict key filter."""
        config = SynthesisConfig(
            detect_conflicts=True,
            conflict_key_filter=["STATUS"],
        )
        synthesizer = ResultSynthesizer(config)

        outputs = {
            1: "STATUS: a\nVERSION: 1",
            2: "STATUS: b\nVERSION: 2",  # Both differ
        }

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs=outputs,
        )

        # Should only report STATUS conflict due to filter
        assert result.conflict_detection is not None
        assert len(result.conflict_detection["conflicts"]) == 1
        assert result.conflict_detection["conflicts"][0]["key"] == "STATUS"

    def test_prepare_single_sheet_no_conflict_detection(self):
        """Test conflict detection skipped for single sheet."""
        config = SynthesisConfig(detect_conflicts=True)
        synthesizer = ResultSynthesizer(config)

        outputs = {1: "STATUS: complete"}

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1],
            completed_sheets=[1],
            failed_sheets=[],
            sheet_outputs=outputs,
        )

        # Conflict detection not run for single sheet
        assert result.status == "ready"
        assert result.conflict_detection is None

    def test_conflict_detection_result_in_metadata(self):
        """Test conflict detection enabled flag in metadata."""
        config = SynthesisConfig(detect_conflicts=True)
        synthesizer = ResultSynthesizer(config)

        result = synthesizer.prepare_synthesis(
            batch_sheets=[1, 2],
            completed_sheets=[1, 2],
            failed_sheets=[],
            sheet_outputs={
                1: "STATUS: a",
                2: "STATUS: a",  # Same, no conflict
            },
        )

        assert result.metadata.get("conflict_detection_enabled") is True


class TestSynthesisResultConflictField:
    """Tests for SynthesisResult conflict_detection field."""

    def test_default_none(self) -> None:
        """Test conflict_detection defaults to None."""
        result = SynthesisResult(batch_id="test")
        assert result.conflict_detection is None

    def test_to_dict_includes_conflict(self) -> None:
        """Test to_dict includes conflict_detection."""
        result = SynthesisResult(
            batch_id="test",
            conflict_detection={"has_conflicts": True, "conflicts": []},
        )

        data = result.to_dict()
        assert "conflict_detection" in data
        assert data["conflict_detection"]["has_conflicts"] is True

    def test_from_dict_restores_conflict(self) -> None:
        """Test from_dict restores conflict_detection."""
        data = {
            "batch_id": "test",
            "sheets": [],
            "strategy": "merge",
            "status": "ready",
            "conflict_detection": {"has_conflicts": False},
        }

        result = SynthesisResult.from_dict(data)
        assert result.conflict_detection is not None
        assert result.conflict_detection["has_conflicts"] is False

    def test_roundtrip_with_conflict(self) -> None:
        """Test roundtrip serialization with conflict data."""
        original = SynthesisResult(
            batch_id="roundtrip",
            conflict_detection={
                "sheets_analyzed": [1, 2],
                "conflicts": [{"key": "X", "sheet_a": 1, "value_a": "a"}],
                "has_conflicts": True,
            },
        )

        data = original.to_dict()
        restored = SynthesisResult.from_dict(data)

        assert restored.conflict_detection is not None
        assert restored.conflict_detection["has_conflicts"] is True
        assert len(restored.conflict_detection["conflicts"]) == 1


class TestSynthesisConfigConflictFields:
    """Tests for SynthesisConfig conflict-related fields."""

    def test_default_values(self) -> None:
        """Test default conflict detection values."""
        config = SynthesisConfig()

        assert config.detect_conflicts is False
        assert config.conflict_key_filter is None
        assert config.fail_on_conflict is False

    def test_custom_values(self) -> None:
        """Test custom conflict detection values."""
        config = SynthesisConfig(
            detect_conflicts=True,
            conflict_key_filter=["STATUS", "VERSION"],
            fail_on_conflict=True,
        )

        assert config.detect_conflicts is True
        assert config.conflict_key_filter == ["STATUS", "VERSION"]
        assert config.fail_on_conflict is True
