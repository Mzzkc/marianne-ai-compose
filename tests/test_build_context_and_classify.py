"""Tests for _build_sheet_context() and _classify_execution().

FIX-11f: Tests template context assembly and error classification
which drive all retry decisions.

FIX-06: Additional multi-error tests for classify_execution() covering
JSON structured parsing, multi-error root cause selection, exception
analysis, and regex fallback paths.
"""

import json
from unittest.mock import AsyncMock

from marianne.backends.base import ExecutionResult
from marianne.core.checkpoint import CheckpointState
from marianne.core.config import JobConfig
from marianne.core.errors.classifier import ErrorClassifier
from marianne.core.errors.codes import ErrorCategory, ErrorCode
from marianne.core.errors.models import ClassificationResult
from marianne.execution.runner import JobRunner


def _make_config(**overrides: object) -> JobConfig:
    """Build a minimal JobConfig."""
    base = {
        "name": "test-ctx",
        "description": "Test context and classification",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Process sheet {{ sheet_num }} of {{ total_sheets }}."},
        "retry": {"max_retries": 3},
        "validations": [],
        "pause_between_sheets_seconds": 0,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return JobConfig.model_validate(base)


def _make_runner(config: JobConfig | None = None) -> JobRunner:
    """Build a minimal JobRunner."""
    cfg = config or _make_config()
    backend = AsyncMock()
    state_backend = AsyncMock()
    return JobRunner(config=cfg, backend=backend, state_backend=state_backend)


class TestBuildSheetContext:
    """Tests for _build_sheet_context() template context assembly."""

    def test_basic_context_fields(self) -> None:
        """Context contains sheet_num, total_sheets, start/end items, workspace."""
        runner = _make_runner()
        ctx = runner._build_sheet_context(sheet_num=1)

        d = ctx.to_dict()
        assert d["sheet_num"] == 1
        assert d["total_sheets"] == 3  # 30 items / 10 per sheet
        assert "start_item" in d
        assert "end_item" in d
        assert "workspace" in d

    def test_context_item_range_first_sheet(self) -> None:
        """First sheet starts at item 1."""
        runner = _make_runner()
        ctx = runner._build_sheet_context(sheet_num=1)

        d = ctx.to_dict()
        assert d["start_item"] == 1
        assert d["end_item"] == 10

    def test_context_item_range_second_sheet(self) -> None:
        """Second sheet starts where first ended."""
        runner = _make_runner()
        ctx = runner._build_sheet_context(sheet_num=2)

        d = ctx.to_dict()
        assert d["start_item"] == 11
        assert d["end_item"] == 20

    def test_context_item_range_last_sheet(self) -> None:
        """Last sheet ends at total_items."""
        runner = _make_runner()
        ctx = runner._build_sheet_context(sheet_num=3)

        d = ctx.to_dict()
        assert d["start_item"] == 21
        assert d["end_item"] == 30

    def test_context_without_state_has_no_cross_sheet(self) -> None:
        """Without state, no cross-sheet context is populated."""
        runner = _make_runner()
        ctx = runner._build_sheet_context(sheet_num=2)

        assert ctx.previous_outputs == {}
        assert ctx.previous_files == {}

    def test_context_with_cross_sheet_config(self) -> None:
        """With cross_sheet config and state, previous outputs included."""
        config = _make_config(
            cross_sheet={
                "auto_capture_stdout": True,
                "max_output_chars": 1000,
            }
        )
        runner = _make_runner(config)

        # Build a state with sheet 1 having output
        state = CheckpointState(
            job_id="test",
            job_name="test-ctx",
            config_path="/tmp/test.yaml",
            total_sheets=3,
        )
        state.mark_sheet_started(1)
        state.mark_sheet_completed(1, validation_passed=True)
        state.sheets[1].stdout_tail = "Output from sheet 1"

        ctx = runner._build_sheet_context(sheet_num=2, state=state)
        assert ctx.previous_outputs.get(1) == "Output from sheet 1"

    def test_context_with_prelude_cadenza_injections(self, tmp_path) -> None:
        """With prelude/cadenzas config, injection fields are populated."""
        (tmp_path / "shared.md").write_text("Shared prelude content")
        (tmp_path / "sheet2.md").write_text("Sheet 2 cadenza")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 10,
                "total_items": 30,
                "prelude": [
                    {"file": "shared.md", "as": "context"},
                ],
                "cadenzas": {
                    2: [{"file": "sheet2.md", "as": "skill"}],
                },
            },
        )
        runner = _make_runner(config)

        # Sheet 1 gets only prelude
        ctx1 = runner._build_sheet_context(sheet_num=1)
        assert ctx1.injected_context == ["Shared prelude content"]
        assert ctx1.injected_skills == []

        # Sheet 2 gets prelude + cadenza
        ctx2 = runner._build_sheet_context(sheet_num=2)
        assert ctx2.injected_context == ["Shared prelude content"]
        assert ctx2.injected_skills == ["Sheet 2 cadenza"]

    def test_context_with_jinja_paths_in_prelude(self, tmp_path) -> None:
        """Prelude items with Jinja-templated paths expand correctly (GH#53 Phase 2)."""
        notes = tmp_path / "notes-1.md"
        notes.write_text("Sheet 1 notes")

        config = _make_config(
            workspace=str(tmp_path),
            sheet={
                "size": 10,
                "total_items": 30,
                "prelude": [
                    {"file": "{{ workspace }}/notes-{{ sheet_num }}.md", "as": "context"},
                ],
            },
        )
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == ["Sheet 1 notes"]

    def test_context_no_injections_unchanged(self) -> None:
        """Without prelude/cadenzas, injection fields remain empty (GH#53 Phase 2)."""
        config = _make_config()
        runner = _make_runner(config)
        ctx = runner._build_sheet_context(sheet_num=1)

        assert ctx.injected_context == []
        assert ctx.injected_skills == []
        assert ctx.injected_tools == []


class TestClassifyExecution:
    """Tests for _classify_execution() error classification."""

    def test_rate_limit_detected(self) -> None:
        """Rate limit patterns in stderr are classified as E101."""
        runner = _make_runner()
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error: rate limit exceeded, please try again later",
            exit_code=1,
            duration_seconds=1.0,
        )

        classification = runner._classify_execution(result)
        assert classification.primary.error_code.value.startswith("E1")

    def test_successful_execution_classified(self) -> None:
        """Exit code 0 + no error patterns -> no critical error."""
        runner = _make_runner()
        result = ExecutionResult(
            success=True,
            stdout="All done",
            stderr="",
            exit_code=0,
            duration_seconds=1.0,
        )

        classification = runner._classify_execution(result)
        # With exit code 0, the primary error should be generic or success-related
        assert classification.primary is not None

    def test_auth_error_detected(self) -> None:
        """Authentication errors in output are classified appropriately."""
        runner = _make_runner()
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error: Invalid API key provided. Check your API key.",
            exit_code=1,
            duration_seconds=1.0,
        )

        classification = runner._classify_execution(result)
        # Should detect auth-related error
        error_code = classification.primary.error_code.value
        assert error_code.startswith("E")  # Valid error code

    def test_timeout_error_detected(self) -> None:
        """Timeout signal (exit_signal=15) classified correctly."""
        runner = _make_runner()
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Process killed",
            exit_code=None,
            exit_signal=15,  # SIGTERM
            duration_seconds=300.0,
        )

        classification = runner._classify_execution(result)
        assert classification.primary is not None

    def test_classification_returns_classification_result(self) -> None:
        """Return type is ClassificationResult with primary/secondary."""
        runner = _make_runner()
        result = ExecutionResult(
            success=False,
            stdout="Some output",
            stderr="Error: something went wrong",
            exit_code=1,
            duration_seconds=1.0,
        )

        classification = runner._classify_execution(result)
        assert isinstance(classification, ClassificationResult)
        assert classification.primary is not None
        assert isinstance(classification.secondary, list)
        assert isinstance(classification.confidence, float)

    def test_classify_error_backward_compat(self) -> None:
        """_classify_error returns just the primary ClassifiedError."""
        runner = _make_runner()
        result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Unknown error",
            exit_code=1,
            duration_seconds=1.0,
        )

        error = runner._classify_error(result)
        # Should return ClassifiedError, not ClassificationResult
        assert hasattr(error, "error_code")
        assert hasattr(error, "message")


class TestClassifyExecutionMultiError:
    """FIX-06: Tests for classify_execution() multi-error scenarios.

    These test the ErrorClassifier directly for scenarios that produce
    multiple errors and exercise root cause selection logic.
    """

    def _classifier(self) -> ErrorClassifier:
        return ErrorClassifier()

    def test_json_structured_multi_error_selects_root_cause(self) -> None:
        """Multiple JSON errors: root cause is selected by priority."""
        classifier = self._classifier()
        # CLI output with both rate limit and ENOENT errors
        cli_output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
                {"type": "user", "message": "spawn claude ENOENT"},
            ],
        })
        result = classifier.classify_execution(
            stdout=cli_output, stderr="", exit_code=1
        )
        assert isinstance(result, ClassificationResult)
        assert len(result.all_errors) == 2
        assert len(result.secondary) == 1
        # ENOENT (BACKEND_NOT_FOUND) should be root cause over rate limit
        assert result.primary.error_code == ErrorCode.BACKEND_NOT_FOUND
        assert result.classification_method == "structured"

    def test_json_structured_single_error(self) -> None:
        """Single JSON error returns it as primary with no secondary."""
        classifier = self._classifier()
        cli_output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
            ],
        })
        result = classifier.classify_execution(
            stdout=cli_output, stderr="", exit_code=1
        )
        assert result.primary.error_code == ErrorCode.RATE_LIMIT_API
        assert result.secondary == []
        assert result.confidence > 0.0

    def test_json_plus_signal_combines_errors(self) -> None:
        """JSON errors + exit signal produces combined error list."""
        classifier = self._classifier()
        cli_output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
            ],
        })
        result = classifier.classify_execution(
            stdout=cli_output,
            stderr="",
            exit_code=None,
            exit_signal=9,  # SIGKILL
        )
        # Should have rate limit from JSON + signal error
        assert len(result.all_errors) >= 2
        codes = [e.error_code for e in result.all_errors]
        assert ErrorCode.RATE_LIMIT_API in codes

    def test_exception_timeout_classified(self) -> None:
        """Exception with 'timeout' in message → TIMEOUT category."""
        classifier = self._classifier()
        result = classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exception=TimeoutError("Connection timeout after 30s"),
        )
        assert result.primary.category == ErrorCategory.TIMEOUT
        assert result.primary.error_code == ErrorCode.EXECUTION_TIMEOUT
        assert result.primary.retriable is True

    def test_exception_network_classified(self) -> None:
        """Exception with 'connection' in message → NETWORK category."""
        classifier = self._classifier()
        result = classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exception=ConnectionError("connection refused"),
        )
        assert result.primary.category == ErrorCategory.NETWORK
        assert result.primary.error_code == ErrorCode.NETWORK_CONNECTION_FAILED

    def test_exception_generic_classified_as_transient(self) -> None:
        """Generic exception → TRANSIENT category (retriable)."""
        classifier = self._classifier()
        result = classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exception=RuntimeError("something unexpected happened"),
        )
        assert result.primary.category == ErrorCategory.TRANSIENT
        assert result.primary.retriable is True

    def test_regex_fallback_when_no_json_no_signal(self) -> None:
        """No JSON, no signal, no exception → uses regex fallback."""
        classifier = self._classifier()
        result = classifier.classify_execution(
            stdout="",
            stderr="Error: invalid API key provided",
            exit_code=1,
        )
        assert result.classification_method == "regex_fallback"
        assert result.primary is not None

    def test_confidence_reported(self) -> None:
        """Classification result includes confidence score."""
        classifier = self._classifier()
        cli_output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
                {"type": "user", "message": "spawn claude ENOENT"},
            ],
        })
        result = classifier.classify_execution(
            stdout=cli_output, stderr="", exit_code=1
        )
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0

    def test_all_errors_property(self) -> None:
        """all_errors includes primary + secondary."""
        classifier = self._classifier()
        cli_output = json.dumps({
            "result": "",
            "errors": [
                {"type": "system", "message": "Rate limit exceeded"},
                {"type": "system", "message": "Unauthorized access"},
            ],
        })
        result = classifier.classify_execution(
            stdout=cli_output, stderr="", exit_code=1
        )
        assert len(result.all_errors) == len(result.secondary) + 1
        assert result.primary in result.all_errors

    def test_backward_compat_properties(self) -> None:
        """ClassificationResult backward-compat properties work."""
        classifier = self._classifier()
        result = classifier.classify_execution(
            stdout="", stderr="rate limit hit", exit_code=1
        )
        # These properties delegate to primary
        assert result.category == result.primary.category
        assert result.message == result.primary.message
        assert result.error_code == result.primary.error_code
        assert result.retriable == result.primary.retriable

    def test_timeout_exit_reason_no_signal(self) -> None:
        """exit_reason='timeout' without signal → TIMEOUT classification."""
        classifier = self._classifier()
        result = classifier.classify_execution(
            stdout="partial output",
            stderr="",
            exit_code=124,
            exit_reason="timeout",
        )
        timeout_codes = [e.error_code for e in result.all_errors]
        assert ErrorCode.EXECUTION_TIMEOUT in timeout_codes

    def test_duplicate_error_codes_deduplicated(self) -> None:
        """Same error code from JSON + signal not duplicated."""
        classifier = self._classifier()
        # If JSON already has a timeout error, and exit_reason is also timeout,
        # the timeout should not be added twice
        result = classifier.classify_execution(
            stdout="",
            stderr="",
            exit_code=None,
            exit_reason="timeout",
        )
        timeout_count = sum(
            1 for e in result.all_errors
            if e.error_code == ErrorCode.EXECUTION_TIMEOUT
        )
        assert timeout_count == 1
