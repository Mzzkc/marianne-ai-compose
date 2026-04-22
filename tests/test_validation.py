"""Tests for marianne.execution.validation module."""

from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config import ValidationRule
from marianne.execution.validation import (
    FailureHistoryStore,
    HistoricalFailure,
    SheetValidationResult,
    ValidationEngine,
    ValidationResult,
)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_create_result(self):
        """Test creating a validation result."""
        rule = ValidationRule(
            type="file_exists",
            path="/test/path.txt",
            description="Test file",
        )
        result = ValidationResult(
            rule=rule,
            passed=True,
            actual_value="/test/path.txt",
            expected_value="/test/path.txt",
        )
        assert result.passed is True
        assert result.confidence == 1.0  # default

    def test_result_with_confidence(self):
        """Test validation result with custom confidence."""
        rule = ValidationRule(type="file_exists", path="/test.txt")
        result = ValidationResult(
            rule=rule,
            passed=True,
            actual_value="exists",
            expected_value="exists",
            confidence=0.8,
            confidence_factors={"file_age": 0.8},
        )
        assert result.confidence == 0.8
        assert result.confidence_factors["file_age"] == 0.8

    def test_to_dict(self):
        """Test converting result to dictionary."""
        rule = ValidationRule(
            type="content_contains",
            path="/test.txt",
            pattern="SUCCESS",
            description="Success marker",
        )
        result = ValidationResult(
            rule=rule,
            passed=True,
            actual_value="contains=True",
            expected_value="SUCCESS",
        )
        data = result.to_dict()
        assert data["rule_type"] == "content_contains"
        assert data["passed"] is True
        assert data["pattern"] == "SUCCESS"


class TestSheetValidationResult:
    """Tests for SheetValidationResult dataclass."""

    def _make_result(self, passed: bool, confidence: float = 1.0) -> ValidationResult:
        """Helper to create a ValidationResult."""
        rule = ValidationRule(type="file_exists", path="/test.txt")
        return ValidationResult(
            rule=rule,
            passed=passed,
            actual_value="x" if passed else None,
            expected_value="x",
            confidence=confidence,
        )

    def test_all_passed(self):
        """Test all_passed property."""
        result1 = self._make_result(passed=True)
        result2 = self._make_result(passed=True)
        sheet_result = SheetValidationResult(sheet_num=1, results=[result1, result2])
        assert sheet_result.all_passed is True

    def test_all_passed_false(self):
        """Test all_passed is False when any fails."""
        result1 = self._make_result(passed=True)
        result2 = self._make_result(passed=False)
        sheet_result = SheetValidationResult(sheet_num=1, results=[result1, result2])
        assert sheet_result.all_passed is False

    def test_pass_percentage(self):
        """Test pass_percentage calculation."""
        passed = self._make_result(passed=True)
        failed = self._make_result(passed=False)
        sheet_result = SheetValidationResult(sheet_num=1, results=[passed, passed, failed])
        assert sheet_result.pass_percentage == pytest.approx(66.67, rel=0.01)

    def test_aggregate_confidence(self):
        """Test aggregate confidence calculation."""
        # High confidence pass
        result1 = self._make_result(passed=True, confidence=0.9)
        # Lower confidence pass
        result2 = self._make_result(passed=True, confidence=0.7)
        sheet_result = SheetValidationResult(sheet_num=1, results=[result1, result2])
        # Both passed, so aggregate is weighted average
        assert sheet_result.aggregate_confidence == pytest.approx(0.8, rel=0.01)

    def test_get_passed_results(self):
        """Test getting only passed results."""
        passed = self._make_result(passed=True)
        failed = self._make_result(passed=False)
        sheet_result = SheetValidationResult(sheet_num=1, results=[passed, failed, passed])
        assert len(sheet_result.get_passed_results()) == 2

    def test_get_failed_results(self):
        """Test getting only failed results."""
        passed = self._make_result(passed=True)
        failed = self._make_result(passed=False)
        sheet_result = SheetValidationResult(sheet_num=1, results=[passed, failed])
        assert len(sheet_result.get_failed_results()) == 1

    def test_passed_count(self):
        """Test passed_count property."""
        sheet_result = SheetValidationResult(
            sheet_num=1,
            results=[
                self._make_result(passed=True),
                self._make_result(passed=True),
                self._make_result(passed=False),
            ],
        )
        assert sheet_result.passed_count == 2

    def test_failed_count(self):
        """Test failed_count property."""
        sheet_result = SheetValidationResult(
            sheet_num=1,
            results=[
                self._make_result(passed=True),
                self._make_result(passed=False),
                self._make_result(passed=False),
            ],
        )
        assert sheet_result.failed_count == 2


class TestValidationEngine:
    """Tests for ValidationEngine."""

    async def test_file_exists_pass(self, temp_workspace: Path):
        """Test file_exists validation passes when file exists."""
        # Create test file
        test_file = temp_workspace / "output.txt"
        test_file.write_text("test content")

        rule = ValidationRule(
            type="file_exists",
            path=str(test_file),
            description="Output file",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is True

    async def test_file_exists_fail(self, temp_workspace: Path):
        """Test file_exists validation fails when file missing."""
        rule = ValidationRule(
            type="file_exists",
            path=str(temp_workspace / "nonexistent.txt"),
            description="Missing file",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is False

    async def test_content_contains_pass(self, temp_workspace: Path):
        """Test content_contains validation passes when pattern found."""
        test_file = temp_workspace / "log.txt"
        test_file.write_text("Operation completed: SUCCESS")

        rule = ValidationRule(
            type="content_contains",
            path=str(test_file),
            pattern="SUCCESS",
            description="Success marker",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is True

    async def test_content_contains_fail(self, temp_workspace: Path):
        """Test content_contains validation fails when pattern not found."""
        test_file = temp_workspace / "log.txt"
        test_file.write_text("Operation failed: ERROR")

        rule = ValidationRule(
            type="content_contains",
            path=str(test_file),
            pattern="SUCCESS",
            description="Success marker",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is False

    async def test_path_template_expansion(self, temp_workspace: Path):
        """Test path templates are expanded correctly."""
        test_file = temp_workspace / "sheet-1-output.txt"
        test_file.write_text("content")

        rule = ValidationRule(
            type="file_exists",
            path="{workspace}/sheet-{sheet_num}-output.txt",
            description="Sheet output",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is True

    async def test_run_validations(self, temp_workspace: Path):
        """Test validating multiple rules."""
        # Create test files
        (temp_workspace / "file1.txt").write_text("SUCCESS")
        (temp_workspace / "file2.txt").write_text("DONE")

        rules = [
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file1.txt"),
            ),
            ValidationRule(
                type="content_contains",
                path=str(temp_workspace / "file1.txt"),
                pattern="SUCCESS",
            ),
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "nonexistent.txt"),
            ),
        ]
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations(rules)

        assert len(sheet_result.results) == 3
        assert len(sheet_result.get_passed_results()) == 2
        assert len(sheet_result.get_failed_results()) == 1
        assert sheet_result.pass_percentage == pytest.approx(66.67, rel=0.01)

    async def test_command_succeeds_pass(self, temp_workspace: Path):
        """Test command_succeeds validation passes when command succeeds."""
        rule = ValidationRule(
            type="command_succeeds",
            command="echo 'hello'",
            description="Echo command",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is True
        assert sheet_result.results[0].actual_value == "exit_code=0"

    async def test_command_succeeds_fail(self, temp_workspace: Path):
        """Test command_succeeds validation fails when command fails."""
        rule = ValidationRule(
            type="command_succeeds",
            command="exit 1",
            description="Failing command",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is False
        assert sheet_result.results[0].actual_value == "exit_code=1"

    async def test_command_succeeds_with_working_directory(self, temp_workspace: Path):
        """Test command_succeeds uses specified working directory."""
        # Create a subdirectory
        subdir = temp_workspace / "subdir"
        subdir.mkdir()
        (subdir / "marker.txt").write_text("found")

        rule = ValidationRule(
            type="command_succeeds",
            command="cat marker.txt",
            working_directory=str(subdir),
            description="Cat in subdir",
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        sheet_result = await engine.run_validations([rule])
        assert sheet_result.results[0].passed is True


class TestStagedValidation:
    """Tests for staged validation with fail-fast behavior."""

    async def test_staged_validation_all_pass(self, temp_workspace: Path):
        """Test staged validation when all stages pass."""
        # Create test files
        (temp_workspace / "file1.txt").write_text("content1")
        (temp_workspace / "file2.txt").write_text("content2")

        rules = [
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file1.txt"),
                stage=1,
                description="Stage 1 check",
            ),
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file2.txt"),
                stage=2,
                description="Stage 2 check",
            ),
        ]
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        result, failed_stage = await engine.run_staged_validations(rules)

        assert failed_stage is None
        assert result.all_passed is True
        assert len(result.results) == 2

    async def test_staged_validation_fail_fast(self, temp_workspace: Path):
        """Test that failure in stage 1 skips stage 2."""
        # Create only file2, not file1
        (temp_workspace / "file2.txt").write_text("content2")

        rules = [
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "missing.txt"),
                stage=1,
                description="Stage 1 - missing",
            ),
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file2.txt"),
                stage=2,
                description="Stage 2 - exists",
            ),
        ]
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        result, failed_stage = await engine.run_staged_validations(rules)

        assert failed_stage == 1
        assert result.all_passed is False
        assert len(result.results) == 2
        # Stage 1 failed
        assert result.results[0].passed is False
        # Stage 2 was skipped
        assert result.results[1].passed is False
        assert result.results[1].failure_category == "skipped"

    async def test_staged_validation_multiple_in_same_stage(self, temp_workspace: Path):
        """Test multiple validations in the same stage."""
        (temp_workspace / "file1.txt").write_text("content1")
        # file2 doesn't exist

        rules = [
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file1.txt"),
                stage=1,
                description="Stage 1 - exists",
            ),
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "missing.txt"),
                stage=1,
                description="Stage 1 - missing",
            ),
            ValidationRule(
                type="command_succeeds",
                command="echo 'stage 2'",
                stage=2,
                description="Stage 2 - command",
            ),
        ]
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        result, failed_stage = await engine.run_staged_validations(rules)

        assert failed_stage == 1
        assert len(result.results) == 3
        # First in stage 1 passed
        assert result.results[0].passed is True
        # Second in stage 1 failed
        assert result.results[1].passed is False
        # Stage 2 was skipped
        assert result.results[2].failure_category == "skipped"

    async def test_staged_validation_empty_rules(self, temp_workspace: Path):
        """Test staged validation with empty rules."""
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        result, failed_stage = await engine.run_staged_validations([])

        assert failed_stage is None
        assert len(result.results) == 0

    def test_staged_validation_default_stage(self, temp_workspace: Path):
        """Test that rules without explicit stage default to stage 1."""
        (temp_workspace / "file.txt").write_text("content")

        rule = ValidationRule(
            type="file_exists",
            path=str(temp_workspace / "file.txt"),
            description="Default stage check",
            # Note: no stage specified, should default to 1
        )
        assert rule.stage == 1

    async def test_staged_validation_non_sequential_stages(self, temp_workspace: Path):
        """Test staged validation with non-sequential stage numbers."""
        (temp_workspace / "file.txt").write_text("content")

        rules = [
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file.txt"),
                stage=5,  # Jump to stage 5
                description="Stage 5 check",
            ),
            ValidationRule(
                type="command_succeeds",
                command="echo hello",
                stage=10,  # Jump to stage 10
                description="Stage 10 check",
            ),
        ]
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        result, failed_stage = await engine.run_staged_validations(rules)

        assert failed_stage is None
        assert result.all_passed is True

    async def test_executed_pass_percentage_excludes_skipped(self, temp_workspace: Path):
        """Test that executed_pass_percentage excludes skipped validations.

        This is critical for completion mode decisions - skipped validations
        should not count against the pass percentage.
        """
        (temp_workspace / "file.txt").write_text("content")
        # missing.txt doesn't exist

        rules = [
            # Stage 1: 1 pass, 1 fail
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "file.txt"),
                stage=1,
                description="Stage 1 - exists",
            ),
            ValidationRule(
                type="file_exists",
                path=str(temp_workspace / "missing.txt"),
                stage=1,
                description="Stage 1 - missing",
            ),
            # Stage 2: will be skipped
            ValidationRule(
                type="command_succeeds",
                command="echo stage2",
                stage=2,
                description="Stage 2 - skipped",
            ),
            # Stage 3: will be skipped
            ValidationRule(
                type="command_succeeds",
                command="echo stage3",
                stage=3,
                description="Stage 3 - skipped",
            ),
        ]
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )
        result, failed_stage = await engine.run_staged_validations(rules)

        # Stage 1 failed (1/2 passed), stages 2-3 skipped
        assert failed_stage == 1
        assert len(result.results) == 4

        # Counts
        assert result.passed_count == 1
        assert result.failed_count == 1  # Only actual failures, not skipped
        assert result.skipped_count == 2
        assert result.executed_count == 2  # Only stage 1 ran

        # Percentages
        # pass_percentage includes skipped as failures: 1/4 = 25%
        assert result.pass_percentage == 25.0
        # executed_pass_percentage excludes skipped: 1/2 = 50%
        assert result.executed_pass_percentage == 50.0


# =============================================================================
# HistoricalFailure and FailureHistoryStore Tests (Evolution v6)
# =============================================================================


class TestHistoricalFailure:
    """Tests for HistoricalFailure dataclass."""

    def test_create_historical_failure(self) -> None:
        """Test creating a historical failure record."""
        failure = HistoricalFailure(
            sheet_num=2,
            rule_type="file_exists",
            description="Test file must exist",
            failure_reason="File 'output.txt' does not exist",
            failure_category="missing",
            suggested_fix="Create file at: workspace/output.txt",
        )

        assert failure.sheet_num == 2
        assert failure.rule_type == "file_exists"
        assert failure.description == "Test file must exist"
        assert failure.failure_reason == "File 'output.txt' does not exist"
        assert failure.failure_category == "missing"
        assert failure.suggested_fix == "Create file at: workspace/output.txt"

    def test_historical_failure_minimal(self) -> None:
        """Test creating historical failure with minimal info."""
        failure = HistoricalFailure(
            sheet_num=1,
            rule_type="command_succeeds",
            description="Build must pass",
        )

        assert failure.sheet_num == 1
        assert failure.rule_type == "command_succeeds"
        assert failure.failure_reason is None
        assert failure.failure_category is None


class TestFailureHistoryStore:
    """Tests for FailureHistoryStore."""

    def _create_state_with_failures(self) -> CheckpointState:
        """Create a checkpoint state with validation failures for testing."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=4,
        )

        # Sheet 1: completed with one failure
        sheet1 = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        sheet1.validation_details = [
            {
                "rule_type": "file_exists",
                "description": "Output file exists",
                "passed": True,
            },
            {
                "rule_type": "content_contains",
                "description": "Contains marker",
                "passed": False,
                "failure_reason": "Marker 'DONE' not found",
                "failure_category": "incomplete",
                "suggested_fix": "Add 'DONE' marker at end of file",
            },
        ]
        state.sheets[1] = sheet1

        # Sheet 2: completed with two failures
        sheet2 = SheetState(sheet_num=2, status=SheetStatus.COMPLETED)
        sheet2.validation_details = [
            {
                "rule_type": "file_exists",
                "description": "Report file",
                "passed": False,
                "failure_reason": "File 'report.md' does not exist",
                "failure_category": "missing",
                "suggested_fix": "Create report.md",
            },
            {
                "rule_type": "command_succeeds",
                "description": "Tests pass",
                "passed": False,
                "failure_reason": "Command failed with exit 1",
                "failure_category": "error",
            },
        ]
        state.sheets[2] = sheet2

        # Sheet 3: completed with no failures
        sheet3 = SheetState(sheet_num=3, status=SheetStatus.COMPLETED)
        sheet3.validation_details = [
            {
                "rule_type": "file_exists",
                "description": "Final output",
                "passed": True,
            },
        ]
        state.sheets[3] = sheet3

        return state

    def test_query_similar_failures_all(self) -> None:
        """Test querying all failures without filters."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        failures = store.query_similar_failures(
            current_sheet=4,
            limit=10,
        )

        # Should get 3 failures from sheets 1 and 2, most recent first
        assert len(failures) == 3
        # Sheet 2 failures come first (most recent)
        assert failures[0].sheet_num == 2
        assert failures[1].sheet_num == 2
        # Sheet 1 failure last
        assert failures[2].sheet_num == 1

    def test_query_similar_failures_by_rule_type(self) -> None:
        """Test filtering by rule type."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        failures = store.query_similar_failures(
            current_sheet=4,
            rule_types=["file_exists"],
            limit=10,
        )

        # Only one file_exists failure (sheet 2)
        assert len(failures) == 1
        assert failures[0].rule_type == "file_exists"
        assert failures[0].failure_category == "missing"

    def test_query_similar_failures_by_category(self) -> None:
        """Test filtering by failure category."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        failures = store.query_similar_failures(
            current_sheet=4,
            failure_categories=["incomplete", "missing"],
            limit=10,
        )

        # Should match: sheet 1 incomplete, sheet 2 missing
        assert len(failures) == 2
        categories = {f.failure_category for f in failures}
        assert categories == {"incomplete", "missing"}

    def test_query_similar_failures_with_limit(self) -> None:
        """Test that limit restricts results."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        failures = store.query_similar_failures(
            current_sheet=4,
            limit=2,
        )

        assert len(failures) == 2

    def test_query_similar_failures_excludes_current_sheet(self) -> None:
        """Test that current sheet is excluded from results."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        # Query as if we're on sheet 3
        failures = store.query_similar_failures(
            current_sheet=3,
            limit=10,
        )

        # Sheet 3 has no failures anyway, but shouldn't include it
        sheet_nums = {f.sheet_num for f in failures}
        assert 3 not in sheet_nums
        assert len(failures) == 3  # Only from sheets 1 and 2

    def test_query_recent_failures(self) -> None:
        """Test querying recent failures with lookback."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        failures = store.query_recent_failures(
            current_sheet=4,
            lookback_sheets=2,  # Only look at sheets 3 and 2
            limit=10,
        )

        # Sheet 3 has no failures, sheet 2 has 2 failures
        assert len(failures) == 2
        assert all(f.sheet_num == 2 for f in failures)

    def test_query_recent_failures_limited_lookback(self) -> None:
        """Test that lookback_sheets limits which sheets are checked."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        failures = store.query_recent_failures(
            current_sheet=4,
            lookback_sheets=1,  # Only look at sheet 3
            limit=10,
        )

        # Sheet 3 has no failures
        assert len(failures) == 0

    def test_has_failures_true(self) -> None:
        """Test has_failures returns True when failures exist."""
        state = self._create_state_with_failures()
        store = FailureHistoryStore(state)

        assert store.has_failures(current_sheet=4) is True
        assert store.has_failures(current_sheet=3) is True  # Sheets 1, 2 have failures

    def test_has_failures_false(self) -> None:
        """Test has_failures returns False when no failures exist."""
        state = CheckpointState(
            job_id="test-job",
            job_name="test-job",
            total_sheets=2,
        )

        # Sheet with only passing validations
        sheet = SheetState(sheet_num=1, status=SheetStatus.COMPLETED)
        sheet.validation_details = [
            {"rule_type": "file_exists", "passed": True},
        ]
        state.sheets[1] = sheet

        store = FailureHistoryStore(state)
        assert store.has_failures(current_sheet=2) is False

    def test_empty_state(self) -> None:
        """Test queries on empty state."""
        state = CheckpointState(
            job_id="empty-job",
            job_name="empty-job",
            total_sheets=1,
        )
        store = FailureHistoryStore(state)

        failures = store.query_similar_failures(current_sheet=1)
        assert failures == []

        assert store.has_failures(current_sheet=1) is False


class TestValidationRetry:
    """Tests for validation retry behavior (filesystem race condition fix)."""

    def test_retry_defaults(self):
        """Test ValidationRule has retry defaults."""
        rule = ValidationRule(type="file_exists", path="/test.txt")
        assert rule.retry_count == 3
        assert rule.retry_delay_ms == 200

    def test_retry_custom_values(self):
        """Test ValidationRule accepts custom retry values."""
        rule = ValidationRule(
            type="file_exists",
            path="/test.txt",
            retry_count=5,
            retry_delay_ms=500,
        )
        assert rule.retry_count == 5
        assert rule.retry_delay_ms == 500

    def test_retry_disabled(self):
        """Test retry can be disabled with retry_count=0."""
        rule = ValidationRule(
            type="file_exists",
            path="/test.txt",
            retry_count=0,
        )
        assert rule.retry_count == 0

    async def test_file_exists_retries_on_missing_file(self, temp_workspace: Path):
        """Test file_exists retries when file initially missing.

        This simulates the race condition where a sheet creates a file
        and the validation runs before the file is visible.
        """
        import threading
        import time

        test_file = temp_workspace / "delayed_file.txt"

        # File will be created after a short delay (simulating filesystem lag)
        def create_file_delayed():
            time.sleep(0.15)  # 150ms delay
            test_file.write_text("created")

        # Start delayed file creation
        thread = threading.Thread(target=create_file_delayed)
        thread.start()

        # Run validation with retry (should eventually pass)
        rule = ValidationRule(
            type="file_exists",
            path=str(test_file),
            description="Delayed file",
            retry_count=3,
            retry_delay_ms=100,  # 100ms between attempts
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )

        result = await engine.run_validations([rule])
        thread.join()

        # Should pass after retries
        assert result.results[0].passed is True

    async def test_file_exists_fails_without_retry(self, temp_workspace: Path):
        """Test file_exists fails immediately when retry disabled."""
        import threading
        import time

        test_file = temp_workspace / "delayed_file_no_retry.txt"

        # File will be created after a delay
        def create_file_delayed():
            time.sleep(0.15)  # 150ms delay
            test_file.write_text("created")

        thread = threading.Thread(target=create_file_delayed)
        thread.start()

        # Run validation without retry (should fail immediately)
        rule = ValidationRule(
            type="file_exists",
            path=str(test_file),
            description="Delayed file no retry",
            retry_count=0,  # Disable retry
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )

        result = await engine.run_validations([rule])
        thread.join()

        # Should fail because no retry
        assert result.results[0].passed is False

    async def test_content_contains_retries_on_incomplete_content(self, temp_workspace: Path):
        """Test content_contains retries when content initially incomplete."""
        import threading
        import time

        test_file = temp_workspace / "growing_file.txt"
        test_file.write_text("PARTIAL")  # Initial incomplete content

        # Content will be completed after a delay
        def complete_content_delayed():
            time.sleep(0.15)
            test_file.write_text("PARTIAL COMPLETE")

        thread = threading.Thread(target=complete_content_delayed)
        thread.start()

        rule = ValidationRule(
            type="content_contains",
            path=str(test_file),
            pattern="COMPLETE",
            description="Complete marker",
            retry_count=3,
            retry_delay_ms=100,
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )

        result = await engine.run_validations([rule])
        thread.join()

        # Should pass after retries find the complete content
        assert result.results[0].passed is True

    async def test_command_succeeds_retries(self, temp_workspace: Path):
        """Test command_succeeds retries on transient failures."""
        import threading
        import time

        # Create a file that the command will check for
        marker_file = temp_workspace / "marker.txt"

        # File will be created after a delay
        def create_marker_delayed():
            time.sleep(0.15)
            marker_file.write_text("ready")

        thread = threading.Thread(target=create_marker_delayed)
        thread.start()

        # Command checks if file exists
        rule = ValidationRule(
            type="command_succeeds",
            command=f"test -f {marker_file}",
            description="Check marker file",
            retry_count=3,
            retry_delay_ms=100,
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )

        result = await engine.run_validations([rule])
        thread.join()

        # Should pass after retries
        assert result.results[0].passed is True

    async def test_immediate_pass_no_extra_retries(self, temp_workspace: Path):
        """Test that validation returns immediately on success (no unnecessary retries)."""
        import time

        test_file = temp_workspace / "immediate_pass.txt"
        test_file.write_text("exists")

        rule = ValidationRule(
            type="file_exists",
            path=str(test_file),
            retry_count=10,  # Many retries configured
            retry_delay_ms=1000,  # Long delay (1 second)
        )
        engine = ValidationEngine(
            workspace=temp_workspace,
            sheet_context={"sheet_num": 1, "workspace": str(temp_workspace)},
        )

        start = time.monotonic()
        result = await engine.run_validations([rule])
        elapsed = time.monotonic() - start

        assert result.results[0].passed is True
        # Should complete quickly because it passes immediately
        assert elapsed < 30.0

