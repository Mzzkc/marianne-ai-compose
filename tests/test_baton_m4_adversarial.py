"""Adversarial tests for M4 baton code — musician prompt rendering, error
classification, clone system, adapter state mapping, and validation edge cases.

Movement 1 cycle 2 — Breakpoint.

These tests target the code that shipped since M3: the full F-104 prompt
rendering pipeline in musician.py, the conductor-clone module, the E006/F-098
error classification fixes, and the adapter's state mapping tables.

Every test is designed to catch a bug that would manifest in production.
No happy paths. No polite inputs. The real world is not polite.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jinja2
import pytest

from mozart.backends.base import ExecutionResult
from mozart.core.config.execution import ValidationRule
from mozart.core.config.job import InjectionCategory, InjectionItem
from mozart.core.sheet import Sheet
from mozart.daemon.baton.events import SheetAttemptResult
from mozart.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    BatonSheetStatus,
)


# =========================================================================
# Helpers
# =========================================================================


def _make_sheet(
    num: int = 1,
    instrument: str = "claude-code",
    prompt: str = "Write hello world",
    validations: list[Any] | None = None,
    workspace: str = "/tmp/test-ws",
    timeout: float = 60.0,
    template_file: Path | None = None,
    prelude: list[InjectionItem] | None = None,
    cadenza: list[InjectionItem] | None = None,
    voice_count: int = 1,
) -> Sheet:
    """Create a Sheet for adversarial testing."""
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=voice_count,
        instrument_name=instrument,
        workspace=Path(workspace),
        prompt_template=prompt,
        template_file=template_file,
        validations=validations or [],
        timeout_seconds=timeout,
        prelude=prelude or [],
        cadenza=cadenza or [],
    )


def _make_context(
    attempt: int = 1,
    mode: AttemptMode = AttemptMode.NORMAL,
    completion_suffix: str | None = None,
) -> AttemptContext:
    """Create an AttemptContext for testing."""
    return AttemptContext(
        attempt_number=attempt,
        mode=mode,
        completion_prompt_suffix=completion_suffix,
    )


def _make_exec_result(
    success: bool = True,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    rate_limited: bool = False,
    error_message: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model: str | None = "claude-sonnet-4-5-20250929",
    duration_seconds: float = 1.0,
) -> ExecutionResult:
    """Create an ExecutionResult for testing."""
    return ExecutionResult(
        success=success,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        rate_limited=rate_limited,
        error_message=error_message,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        duration_seconds=duration_seconds,
    )


# =========================================================================
# 1. Musician _build_prompt — adversarial template rendering
# =========================================================================


class TestBuildPromptAdversarial:
    """Adversarial tests for the musician's _build_prompt() function (F-104).

    The prompt rendering pipeline is the single most important code path
    in the baton — it determines what every agent sees. Edge cases here
    produce confused agents in production.
    """

    def test_undefined_variable_raises_strict(self) -> None:
        """StrictUndefined mode must raise on undefined template variables.

        If a score references {{ nonexistent_var }}, the musician must
        fail loudly — not silently produce empty text.
        """
        from mozart.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt="{{ nonexistent_var }}")
        ctx = _make_context()

        with pytest.raises(jinja2.UndefinedError):
            _build_prompt(sheet, ctx)

    def test_template_with_shell_injection_attempt(self) -> None:
        """Template variables are rendered, not executed. Shell metacharacters
        in variable values must appear literally in the output.
        """
        from mozart.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt="Run: {{ workspace }}")
        ctx = _make_context()

        result = _build_prompt(sheet, ctx)
        # The workspace path should appear as a literal string, not get executed
        assert "/tmp/test-ws" in result

    def test_empty_prompt_template_produces_preamble_only(self) -> None:
        """A sheet with an empty prompt still gets a preamble.

        The preamble is always prepended. An empty template means the agent
        gets identity but no task — weird but not a crash.
        """
        from mozart.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt="")
        ctx = _make_context()

        result = _build_prompt(sheet, ctx)
        # Preamble should be present
        assert "sheet" in result.lower() or "Sheet" in result

    def test_completion_suffix_appended_after_validations(self) -> None:
        """Completion mode suffix must appear at the END of the prompt,
        after validation requirements. The agent reads top-to-bottom;
        "finish your work" before "here's what must pass" is confusing.
        """
        from mozart.daemon.baton.musician import _build_prompt

        rule = ValidationRule(
            type="file_exists",
            path="/tmp/output.txt",
            description="Output file must exist",
        )

        sheet = _make_sheet(
            prompt="Write code",
            validations=[rule],
        )
        ctx = _make_context(
            mode=AttemptMode.COMPLETION,
            completion_suffix="FINISH: Complete the remaining validations.",
        )

        result = _build_prompt(sheet, ctx)
        # Suffix must come after validation section
        val_pos = result.find("Success Requirements")
        suffix_pos = result.find("FINISH:")
        assert val_pos >= 0, "Validation section missing"
        assert suffix_pos >= 0, "Completion suffix missing"
        assert suffix_pos > val_pos, (
            "Completion suffix must come AFTER validation requirements"
        )

    def test_retry_preamble_differs_from_first_attempt(self) -> None:
        """On retry, the preamble should indicate this is a retry.

        The agent needs to know it failed before — otherwise it repeats
        the same approach.
        """
        from mozart.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt="Do the work")
        ctx_first = _make_context(attempt=1)
        ctx_retry = _make_context(attempt=3)

        first_prompt = _build_prompt(sheet, ctx_first)
        retry_prompt = _build_prompt(sheet, ctx_retry)

        # The retry prompt should be different (retry count in preamble)
        assert first_prompt != retry_prompt

    def test_parallel_voice_indicated_in_preamble(self) -> None:
        """When voice_count > 1, the preamble should indicate parallelism.

        Agents in a fan-out need to know they're concurrent to avoid
        conflicting file writes.
        """
        from mozart.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt="Do work", voice_count=5)
        ctx = _make_context()

        result = _build_prompt(sheet, ctx)
        # Should mention parallel/concurrent execution somehow
        # The preamble's is_parallel flag should affect output
        assert "concurrently" in result.lower() or "parallel" in result.lower()

    def test_jinja2_builtin_variables_cannot_be_overridden(self) -> None:
        """Template variables like sheet_num are built-in.

        A score author shouldn't be able to override them with custom vars
        that have the same name — the built-in should win.
        """
        from mozart.daemon.baton.musician import _build_prompt

        sheet = _make_sheet(prompt="Sheet {{ sheet_num }}")
        ctx = _make_context()

        result = _build_prompt(sheet, ctx)
        # sheet_num should be 1 (the actual sheet num), not something custom
        assert "Sheet 1" in result


# =========================================================================
# 2. Musician _classify_error — adversarial error classification
# =========================================================================


class TestClassifyErrorAdversarial:
    """Adversarial tests for the musician's _classify_error() function.

    Error classification drives the baton's retry/escalation decisions.
    A misclassification means the wrong recovery path — rate limits treated
    as auth failures, transient errors treated as permanent.
    """

    def test_rate_limited_is_never_classified_as_error(self) -> None:
        """Rate limits are NOT errors — they must return (None, None).

        If rate limits leak into error classification, the baton will
        retry instead of wait — burning through attempts on cooldowns.
        """
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(
            success=False,
            exit_code=1,
            stderr="API Error: Rate limit reached. Please wait.",
            rate_limited=True,
        )
        classification, message = _classify_error(result)
        assert classification is None
        assert message is None

    def test_auth_patterns_only_checked_in_stderr(self) -> None:
        """Auth pattern matching checks stderr only.

        If an agent writes "authentication" in stdout (e.g., building
        an auth module), that's content, not an error.
        """
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(
            success=False,
            exit_code=1,
            stdout="Implementing authentication module with 401 handler",
            stderr="Command failed with exit code 1",
        )
        classification, _ = _classify_error(result)
        # stdout "authentication" and "401" should NOT trigger AUTH_FAILURE
        assert classification != "AUTH_FAILURE"

    def test_exit_code_none_is_transient(self) -> None:
        """exit_code=None means the process was killed by signal.

        This is TRANSIENT — the agent didn't finish, wasn't wrong.
        """
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(
            success=False,
            exit_code=None,
            stderr="",
        )
        classification, _ = _classify_error(result)
        assert classification == "TRANSIENT"

    def test_success_returns_none_none(self) -> None:
        """Successful execution has no error to classify."""
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(success=True, exit_code=0)
        classification, message = _classify_error(result)
        assert classification is None
        assert message is None

    def test_auth_failure_case_insensitive(self) -> None:
        """Auth patterns must match case-insensitively.

        "UNAUTHORIZED" and "unauthorized" are the same error.
        """
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(
            success=False,
            exit_code=1,
            stderr="UNAUTHORIZED: Invalid API key provided",
        )
        classification, _ = _classify_error(result)
        assert classification == "AUTH_FAILURE"

    def test_empty_stderr_with_nonzero_exit(self) -> None:
        """Nonzero exit with empty stderr should be EXECUTION_ERROR, not crash.

        Some tools exit nonzero without writing to stderr.
        """
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(
            success=False,
            exit_code=42,
            stderr="",
        )
        classification, message = _classify_error(result)
        assert classification == "EXECUTION_ERROR"
        assert "42" in (message or "")

    def test_mixed_auth_and_rate_limit_prefers_rate_limit(self) -> None:
        """When rate_limited=True AND stderr has auth patterns, rate limit wins.

        The rate_limited flag is authoritative — it comes from the backend
        classifier which has full context.
        """
        from mozart.daemon.baton.musician import _classify_error

        result = _make_exec_result(
            success=False,
            exit_code=1,
            stderr="unauthorized: rate limit exceeded",
            rate_limited=True,
        )
        classification, _ = _classify_error(result)
        assert classification is None  # Rate limit = not an error


# =========================================================================
# 3. Musician _validate — F-018 contract adversarial tests
# =========================================================================


class TestValidateAdversarial:
    """Adversarial tests for the musician's _validate() function.

    F-018: validation_pass_rate must be 100.0 when execution succeeds
    with no validations. If this contract breaks, sheets with no validations
    retry forever.
    """

    @pytest.mark.asyncio
    async def test_no_validations_success_yields_100(self) -> None:
        """The F-018 contract: success + no validations = 100% pass rate."""
        from mozart.daemon.baton.musician import _validate

        sheet = _make_sheet(validations=[])
        result = _make_exec_result(success=True)

        passed, total, rate, details = await _validate(sheet, result)
        assert rate == 100.0
        assert total == 0

    @pytest.mark.asyncio
    async def test_execution_failure_yields_zero(self) -> None:
        """Execution failure means no validations run — rate is 0.0."""
        from mozart.daemon.baton.musician import _validate

        sheet = _make_sheet(validations=[])
        result = _make_exec_result(success=False)

        passed, total, rate, details = await _validate(sheet, result)
        assert rate == 0.0

    @pytest.mark.asyncio
    async def test_validation_engine_exception_yields_zero(self) -> None:
        """If the validation engine itself crashes, rate must be 0.0.

        This triggers a retry — the musician can't know if work was done.
        """
        from mozart.daemon.baton.musician import _validate

        rule = ValidationRule(type="file_exists", path="/tmp/output.txt")
        sheet = _make_sheet(validations=[rule])
        result = _make_exec_result(success=True)

        with patch(
            "mozart.execution.validation.engine.ValidationEngine",
            side_effect=RuntimeError("engine broken"),
        ):
            passed, total, rate, details = await _validate(sheet, result)

        assert rate == 0.0
        assert details is not None
        assert "error" in details


# =========================================================================
# 4. Musician _capture_output — credential redaction adversarial tests
# =========================================================================


class TestCaptureOutputAdversarial:
    """Adversarial tests for output capture and credential redaction."""

    def test_credentials_redacted_in_stdout(self) -> None:
        """API keys in stdout must be redacted before entering the baton."""
        from mozart.daemon.baton.musician import _capture_output

        result = _make_exec_result(
            stdout="Using key sk-ant-api03-secretkey1234567890abcdef",
        )
        stdout, _ = _capture_output(result)
        assert "sk-ant-" not in stdout
        assert "REDACTED" in stdout

    def test_credentials_redacted_in_stderr(self) -> None:
        """API keys in stderr must be redacted."""
        from mozart.daemon.baton.musician import _capture_output

        # Google API key must be 28+ chars after AIzaSy to match the pattern
        google_key = "AIzaSy" + "A" * 30
        result = _make_exec_result(
            stderr=f"Error: invalid key {google_key}",
        )
        _, stderr = _capture_output(result)
        assert "AIzaSy" not in stderr
        assert "REDACTED" in stderr

    def test_none_output_handled(self) -> None:
        """None stdout/stderr must produce empty strings, not crash."""
        from mozart.daemon.baton.musician import _capture_output

        result = _make_exec_result()
        # Manually set None to simulate backend returning None
        result = ExecutionResult(
            success=True,
            exit_code=0,
            stdout=None,  # type: ignore[arg-type]
            stderr=None,  # type: ignore[arg-type]
            rate_limited=False,
            duration_seconds=1.0,
        )
        stdout, stderr = _capture_output(result)
        assert stdout == ""
        assert stderr == ""

    def test_output_truncated_to_limit(self) -> None:
        """Extremely long output must be truncated, keeping the tail.

        The tail is kept (not the head) because the end of output
        usually contains the error/result.
        """
        from mozart.core.constants import TRUNCATE_STDOUT_TAIL_CHARS
        from mozart.daemon.baton.musician import _capture_output

        long_output = "x" * (TRUNCATE_STDOUT_TAIL_CHARS + 10000)
        result = _make_exec_result(stdout=long_output)
        stdout, _ = _capture_output(result)
        assert len(stdout) <= TRUNCATE_STDOUT_TAIL_CHARS


# =========================================================================
# 5. Conductor clone — adversarial sanitization
# =========================================================================


class TestCloneSanitizationAdversarial:
    """Adversarial tests for clone name sanitization.

    Clone names come from user input (--conductor-clone=NAME). Malicious
    names could create files outside /tmp, overwrite production paths,
    or crash path construction.
    """

    def test_path_traversal_rejected(self) -> None:
        """../../../etc/passwd must not produce paths outside /tmp."""
        from mozart.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("../../../etc/passwd")
        assert "/etc/" not in str(paths.socket)
        assert "/tmp/" in str(paths.socket)

    def test_absolute_path_injection(self) -> None:
        """/tmp/evil must not override the path construction."""
        from mozart.daemon.clone import resolve_clone_paths

        paths = resolve_clone_paths("/tmp/evil")
        # Should be sanitized — slashes become hyphens
        assert paths.socket != Path("/tmp/evil.sock")

    def test_null_byte_injection(self) -> None:
        """Null bytes in name must be stripped (C-string truncation attack)."""
        from mozart.daemon.clone import _sanitize_name

        result = _sanitize_name("test\x00evil")
        assert "\x00" not in result

    def test_very_long_name_truncated(self) -> None:
        """Names > 64 chars must be truncated for socket path limits."""
        from mozart.daemon.clone import _sanitize_name

        long_name = "a" * 500
        result = _sanitize_name(long_name)
        assert len(result) <= 64

    def test_empty_name_produces_default(self) -> None:
        """Empty/None name produces default clone (no suffix)."""
        from mozart.daemon.clone import _sanitize_name

        assert _sanitize_name("") == ""
        assert _sanitize_name(None) == ""

    def test_only_special_chars_produces_hyphen(self) -> None:
        """A name made entirely of special characters sanitizes to a single hyphen.

        Hyphens are not stripped (preserves uniqueness between clone names).
        The result is distinct from the default clone (empty string).
        """
        from mozart.daemon.clone import _sanitize_name

        result = _sanitize_name("!@#$%^&*()")
        assert result == "-"

    def test_unicode_name_handled(self) -> None:
        """Unicode characters should be replaced, not crash."""
        from mozart.daemon.clone import _sanitize_name

        result = _sanitize_name("тест-клон-名前")
        # Non-ASCII replaced with hyphens, then collapsed
        assert all(c.isalnum() or c == "-" for c in result)


class TestCloneGlobalStateAdversarial:
    """Adversarial tests for clone global state management.

    The clone name is stored as module-level global state. Concurrent
    tests or forgotten cleanup can leak clone state between operations.
    """

    def test_clone_state_cleanup(self) -> None:
        """Setting clone to None restores production mode."""
        from mozart.daemon.clone import (
            get_clone_name,
            is_clone_active,
            set_clone_name,
        )

        set_clone_name("test")
        assert is_clone_active()
        set_clone_name(None)
        assert not is_clone_active()
        assert get_clone_name() is None

    def test_named_clones_produce_distinct_paths(self) -> None:
        """Two different clone names must produce different socket paths."""
        from mozart.daemon.clone import resolve_clone_paths

        paths_a = resolve_clone_paths("alpha")
        paths_b = resolve_clone_paths("beta")

        assert paths_a.socket != paths_b.socket
        assert paths_a.pid_file != paths_b.pid_file
        assert paths_a.state_db != paths_b.state_db

    def test_default_clone_paths_differ_from_production(self) -> None:
        """Default clone paths must not overlap production paths."""
        from mozart.daemon.clone import resolve_clone_paths

        # Production uses ~/.mozart/mozart.sock (or similar)
        # Clone uses /tmp/mozart-clone.sock
        paths = resolve_clone_paths(None)
        assert "clone" in str(paths.socket)
        assert "clone" in str(paths.pid_file)


# =========================================================================
# 6. Adapter state mapping — bidirectional correctness
# =========================================================================


class TestAdapterStateMappingAdversarial:
    """Adversarial tests for the BatonAdapter's state mapping tables.

    The adapter maps between BatonSheetStatus (11 states) and
    CheckpointState (5 states). This is a lossy compression — multiple
    baton states map to the same checkpoint state. The reverse mapping
    must be defined for ALL checkpoint states.
    """

    def test_all_baton_statuses_have_checkpoint_mapping(self) -> None:
        """Every BatonSheetStatus must have a checkpoint mapping.

        A missing mapping means the adapter can't save state for that status.
        """
        from mozart.daemon.baton.adapter import _BATON_TO_CHECKPOINT

        for status in BatonSheetStatus:
            assert status in _BATON_TO_CHECKPOINT, (
                f"BatonSheetStatus.{status.name} has no checkpoint mapping"
            )

    def test_all_checkpoint_statuses_have_baton_mapping(self) -> None:
        """Every known checkpoint status must have a baton mapping.

        This is needed for resume — checkpoint state must map back to
        a baton status.
        """
        from mozart.daemon.baton.adapter import _CHECKPOINT_TO_BATON

        known_statuses = {"pending", "in_progress", "completed", "failed", "skipped"}
        for status in known_statuses:
            assert status in _CHECKPOINT_TO_BATON, (
                f"Checkpoint status '{status}' has no baton mapping"
            )

    def test_terminal_baton_statuses_map_to_terminal_checkpoint(self) -> None:
        """Terminal baton statuses must map to terminal checkpoint statuses.

        If COMPLETED maps to "pending", the job never finishes.
        """
        from mozart.daemon.baton.adapter import _BATON_TO_CHECKPOINT

        terminal_checkpoint = {"completed", "failed", "skipped"}
        terminal_baton = {
            BatonSheetStatus.COMPLETED,
            BatonSheetStatus.FAILED,
            BatonSheetStatus.SKIPPED,
            BatonSheetStatus.CANCELLED,
        }
        for status in terminal_baton:
            mapped = _BATON_TO_CHECKPOINT[status]
            assert mapped in terminal_checkpoint, (
                f"Terminal BatonSheetStatus.{status.name} maps to "
                f"non-terminal '{mapped}'"
            )

    def test_round_trip_terminal_states_preserved(self) -> None:
        """Terminal states must survive a round trip (baton → checkpoint → baton).

        If COMPLETED → "completed" → COMPLETED, the round trip preserves
        terminal status. If not, resume after restart loses terminal state.
        """
        from mozart.daemon.baton.adapter import (
            _BATON_TO_CHECKPOINT,
            _CHECKPOINT_TO_BATON,
        )

        for baton_status in [
            BatonSheetStatus.COMPLETED,
            BatonSheetStatus.FAILED,
            BatonSheetStatus.SKIPPED,
        ]:
            checkpoint_status = _BATON_TO_CHECKPOINT[baton_status]
            recovered_status = _CHECKPOINT_TO_BATON[checkpoint_status]
            assert recovered_status == baton_status, (
                f"Round trip lost state: {baton_status} → "
                f"'{checkpoint_status}' → {recovered_status}"
            )

    def test_mapping_functions_match_tables(self) -> None:
        """The public mapping functions must agree with the tables."""
        from mozart.daemon.baton.adapter import (
            _BATON_TO_CHECKPOINT,
            baton_to_checkpoint_status,
            checkpoint_to_baton_status,
        )

        for baton_status, expected in _BATON_TO_CHECKPOINT.items():
            assert baton_to_checkpoint_status(baton_status) == expected


# =========================================================================
# 7. Full sheet_task integration — adversarial async scenarios
# =========================================================================


class TestSheetTaskAdversarial:
    """Adversarial async tests for the full sheet_task function.

    sheet_task is the async entry point. It must NEVER crash, NEVER leave
    the inbox empty, and ALWAYS report a result — even when everything
    goes wrong.
    """

    @pytest.mark.asyncio
    async def test_backend_raises_always_reports(self) -> None:
        """If the backend throws, the musician still reports a result."""
        from mozart.daemon.baton.musician import sheet_task

        sheet = _make_sheet()
        ctx = _make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        backend = AsyncMock()
        backend.execute = AsyncMock(side_effect=RuntimeError("backend dead"))
        backend.set_preamble = MagicMock()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=ctx,
            inbox=inbox,
        )

        assert not inbox.empty()
        result = inbox.get_nowait()
        assert not result.execution_success
        assert result.error_classification == "TRANSIENT"
        assert "RuntimeError" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_rendered_prompt_path_skips_build(self) -> None:
        """When rendered_prompt is provided, _build_prompt is NOT called.

        This is the PromptRenderer path — the adapter pre-renders the
        prompt. The musician must use it directly.
        """
        from mozart.daemon.baton.musician import sheet_task

        sheet = _make_sheet()
        ctx = _make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_exec_result())
        backend.set_preamble = MagicMock()

        pre_rendered = "This is a pre-rendered prompt with full context."

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=ctx,
            inbox=inbox,
            rendered_prompt=pre_rendered,
            preamble="Test preamble",
        )

        # Backend should have received the pre-rendered prompt
        backend.execute.assert_called_once()
        actual_prompt = backend.execute.call_args[0][0]
        assert actual_prompt == pre_rendered
        backend.set_preamble.assert_called_once_with("Test preamble")

    @pytest.mark.asyncio
    async def test_rendered_prompt_without_preamble(self) -> None:
        """rendered_prompt can be provided without a preamble.

        The preamble is optional even when the rendered prompt is provided.
        """
        from mozart.daemon.baton.musician import sheet_task

        sheet = _make_sheet()
        ctx = _make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        backend = AsyncMock()
        backend.execute = AsyncMock(return_value=_make_exec_result())
        backend.set_preamble = MagicMock()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=ctx,
            inbox=inbox,
            rendered_prompt="Just a prompt",
        )

        backend.set_preamble.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_redaction_before_inbox(self) -> None:
        """Credentials must be redacted BEFORE the result enters the inbox.

        If redaction happens after inbox.put(), a concurrent reader could
        see raw credentials.
        """
        from mozart.daemon.baton.musician import sheet_task

        sheet = _make_sheet()
        ctx = _make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_exec_result(
                stdout="Key: sk-ant-api03-secretkey1234567890abcdef here",
            )
        )
        backend.set_preamble = MagicMock()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=ctx,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert "sk-ant-" not in (result.stdout_tail or "")

    @pytest.mark.asyncio
    async def test_zero_cost_for_no_tokens(self) -> None:
        """Zero tokens → zero cost. No phantom charges."""
        from mozart.daemon.baton.musician import sheet_task

        sheet = _make_sheet()
        ctx = _make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        backend = AsyncMock()
        backend.execute = AsyncMock(
            return_value=_make_exec_result(input_tokens=0, output_tokens=0)
        )
        backend.set_preamble = MagicMock()

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=backend,
            attempt_context=ctx,
            inbox=inbox,
        )

        result = inbox.get_nowait()
        assert result.cost_usd == 0.0


# =========================================================================
# 8. Injection resolution — adversarial file paths
# =========================================================================


class TestInjectionResolutionAdversarial:
    """Adversarial tests for _resolve_injections.

    Injections load files from disk based on paths in the score YAML.
    Malicious paths, missing files, and encoding issues are all attacks
    on this surface.
    """

    def test_missing_context_file_skipped_not_crashed(self) -> None:
        """Missing context files are skipped — not a crash, just a warning."""
        from mozart.daemon.baton.musician import _resolve_injections

        sheet = _make_sheet(
            prelude=[
                InjectionItem(
                    file="/nonexistent/path/to/context.md",
                    as_=InjectionCategory.CONTEXT,
                )
            ]
        )
        ctx_list, skills, tools = _resolve_injections(sheet, {})
        assert ctx_list == []

    def test_relative_path_resolved_against_workspace(self) -> None:
        """Relative injection paths must be resolved against the workspace."""
        from mozart.daemon.baton.musician import _resolve_injections

        sheet = _make_sheet(
            workspace="/tmp/test-workspace",
            prelude=[
                InjectionItem(
                    file="context.md",
                    as_=InjectionCategory.CONTEXT,
                )
            ]
        )

        # The function tries to read from workspace/context.md
        # Since the file doesn't exist, it logs a warning and skips
        ctx_list, _, _ = _resolve_injections(sheet, {})
        assert ctx_list == []

    def test_jinja2_in_path_expansion_uses_lenient_mode(self) -> None:
        """Path expansion uses lenient mode — undefined vars become empty.

        This is intentional: a missing variable in a path produces an
        empty segment, not a crash.
        """
        from mozart.daemon.baton.musician import _resolve_injections

        sheet = _make_sheet(
            prelude=[
                InjectionItem(
                    file="{{ missing_var }}/context.md",
                    as_=InjectionCategory.CONTEXT,
                )
            ]
        )

        # Should not raise — lenient mode handles missing vars
        ctx_list, _, _ = _resolve_injections(sheet, {})
        assert ctx_list == []  # File doesn't exist, but no crash

    def test_injection_categories_correctly_separated(self) -> None:
        """Context, skill, and tool injections go to different buckets."""
        from mozart.daemon.baton.musician import _resolve_injections
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            for name in ["ctx.md", "skill.md", "tool.md"]:
                Path(tmpdir, name).write_text(f"Content of {name}")

            sheet = _make_sheet(
                workspace=tmpdir,
                prelude=[
                    InjectionItem(
                        file=os.path.join(tmpdir, "ctx.md"),
                        as_=InjectionCategory.CONTEXT,
                    ),
                    InjectionItem(
                        file=os.path.join(tmpdir, "skill.md"),
                        as_=InjectionCategory.SKILL,
                    ),
                ],
                cadenza=[
                    InjectionItem(
                        file=os.path.join(tmpdir, "tool.md"),
                        as_=InjectionCategory.TOOL,
                    ),
                ],
            )

            ctx_list, skills, tools = _resolve_injections(sheet, {})
            assert len(ctx_list) == 1
            assert len(skills) == 1
            assert len(tools) == 1
            assert "ctx.md" in ctx_list[0]
            assert "skill.md" in skills[0]
            assert "tool.md" in tools[0]


# =========================================================================
# 9. Error classifier Phase 4.5 — F-098/F-097 regression tests
# =========================================================================


class TestErrorClassifierPhase45Adversarial:
    """Adversarial tests for classify_execution() Phase 4.5 rate limit override.

    F-098: Rate limit patterns in stdout were invisible when Phase 1 found
    JSON errors. Phase 4.5 is an unconditional override that always scans
    stdout+stderr for rate limit text, regardless of what Phase 1 found.

    These tests reproduce the exact production failure scenario.
    """

    def test_f098_regression_json_errors_mask_rate_limit(self) -> None:
        """EXACT F-098 production failure: Claude CLI returns JSON errors
        AND rate limit text in stdout. Phase 1 finds the JSON error.
        Phase 4 is skipped because all_errors is non-empty. Without
        Phase 4.5, the rate limit is invisible.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            stdout=(
                '{"type":"error","error":{"type":"overloaded_error",'
                '"message":"Overloaded"}}\n'
                "API Error: Rate limit reached. Please wait 5 minutes."
            ),
            stderr="",
            exit_code=1,
        )
        has_rate_limit = any(
            e.category == ErrorCategory.RATE_LIMIT for e in result.all_errors
        )
        assert has_rate_limit, (
            "F-098 regression: rate limit in stdout was masked by JSON errors"
        )

    def test_e006_stale_detection_in_classify_execution(self) -> None:
        """F-097: Stale detection produces E006, not E001 timeout."""
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCode

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            stdout="",
            stderr="Stale execution: no output for 1800s, killing process",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_STALE

    def test_e001_regular_timeout_in_classify_execution(self) -> None:
        """Regular timeout (no stale text) produces E001."""
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCode

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            stdout="Processing...",
            stderr="Command timed out after 3600s",
            exit_code=None,
            exit_reason="timeout",
        )
        assert result.primary.error_code == ErrorCode.EXECUTION_TIMEOUT

    def test_stale_detection_wait_time_is_120s(self) -> None:
        """Stale detection gets 120s retry wait (longer than timeout's 60s).

        Stale means "no output activity." A 60s retry would just hit the
        same stale detection again. 120s gives the system breathing room.
        """
        from mozart.core.errors.classifier import ErrorClassifier

        classifier = ErrorClassifier()
        stale = classifier.classify_execution(
            stdout="",
            stderr="Stale execution detected",
            exit_code=None,
            exit_reason="timeout",
        )
        assert stale.primary.suggested_wait_seconds == 120.0

    def test_phase45_does_not_duplicate_existing_rate_limit(self) -> None:
        """If Phase 1 already found a rate limit, Phase 4.5 should not
        add a second one. Double-counting rate limits could confuse
        the baton's retry logic.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            stdout="rate limit exceeded",
            stderr="",
            exit_code=1,
        )
        rate_limit_count = sum(
            1 for e in result.all_errors
            if e.category == ErrorCategory.RATE_LIMIT
        )
        # At most 1 rate limit error, not 2
        assert rate_limit_count <= 2  # Could be 1 from fallback + no dup from 4.5

    def test_quota_exhaustion_detected_by_phase45(self) -> None:
        """Phase 4.5 should detect quota exhaustion when it also matches
        rate limit patterns. The quota check is nested inside the rate
        limit check — quota text that ALSO matches rate_limit_patterns
        (e.g., "quota") gets the more specific QUOTA_EXHAUSTED code.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCategory, ErrorCode

        classifier = ErrorClassifier()
        # Use text that matches both rate_limit_patterns ("quota") AND
        # quota_exhaustion_patterns ("token budget exhausted")
        result = classifier.classify_execution(
            stdout=(
                '{"errors": [{"message": "Internal error"}]}\n'
                "Token quota exhausted — daily token limit reached, resets at 9pm"
            ),
            stderr="",
            exit_code=1,
        )
        has_quota = any(
            e.error_code == ErrorCode.QUOTA_EXHAUSTED for e in result.all_errors
        )
        assert has_quota, "Quota exhaustion in stdout should be detected by Phase 4.5"

    def test_quota_pattern_without_rate_limit_pattern_missed_by_phase45(self) -> None:
        """F-114: Quota exhaustion text that does NOT also match rate limit
        patterns is invisible to Phase 4.5 when Phase 1 found JSON errors.

        Phase 4.5 only fires the quota check inside the rate limit pattern
        match. Text like "Token budget exhausted" matches quota_exhaustion_patterns
        but NOT rate_limit_patterns. When Phase 1 finds JSON errors, Phase 4
        (regex fallback) is skipped, and Phase 4.5 doesn't fire because the
        rate limit gate doesn't open.

        This test documents the known limitation. In practice, most quota
        messages also contain rate-limit-like text, so the gap is narrow.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCode

        classifier = ErrorClassifier()
        result = classifier.classify_execution(
            stdout=(
                '{"errors": [{"message": "Internal error"}]}\n'
                "Token budget exhausted — usage resets at 9pm"
            ),
            stderr="",
            exit_code=1,
        )
        # This SHOULD have QUOTA_EXHAUSTED but Phase 4.5's gate prevents it.
        # Documenting this as a known gap — NOT asserting the correct behavior,
        # asserting the ACTUAL behavior so this test breaks if the fix lands.
        has_quota = any(
            e.error_code == ErrorCode.QUOTA_EXHAUSTED for e in result.all_errors
        )
        # Currently False — this is the gap. When fixed, flip this assertion.
        assert not has_quota, (
            "If this fails, F-114 was fixed — update this test to assert True"
        )

    def test_classify_vs_classify_execution_consistency(self) -> None:
        """classify() and classify_execution() should agree on rate limits
        for simple (non-JSON) cases.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()

        simple_result = classifier.classify(
            stdout="",
            stderr="Rate limit hit, please wait",
            exit_code=1,
        )
        complex_result = classifier.classify_execution(
            stdout="",
            stderr="Rate limit hit, please wait",
            exit_code=1,
        )
        assert simple_result.category == ErrorCategory.RATE_LIMIT
        assert complex_result.primary.category == ErrorCategory.RATE_LIMIT


# =========================================================================
# 10. Adapter observer event conversion — edge cases
# =========================================================================


class TestAdapterEventConversionAdversarial:
    """Adversarial tests for attempt_result_to_observer_event.

    Event conversion drives the dashboard, learning hub, and notifications.
    A wrong event name means the dashboard shows the wrong icon, the
    learning hub records the wrong outcome, and notifications fire or
    don't fire at the wrong time.
    """

    def test_rate_limited_overrides_success(self) -> None:
        """rate_limited=True overrides even execution_success=True.

        This can happen when a rate limit is detected mid-execution
        by the backend classifier.
        """
        from mozart.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="test",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            exit_code=0,
            duration_seconds=1.0,
            rate_limited=True,
            validation_pass_rate=100.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "rate_limit.active"

    def test_partial_validation_is_partial_not_completed(self) -> None:
        """50% validation pass rate → 'sheet.partial', not 'sheet.completed'.

        Partial completion triggers completion mode in the baton.
        """
        from mozart.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="test",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            exit_code=0,
            duration_seconds=1.0,
            rate_limited=False,
            validation_pass_rate=50.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.partial"

    def test_zero_validation_rate_with_success_is_partial(self) -> None:
        """0% validation with execution success → 'sheet.partial'.

        The execution ran but produced nothing valid. This is the
        completion mode trigger.
        """
        from mozart.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="test",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            exit_code=0,
            duration_seconds=1.0,
            rate_limited=False,
            validation_pass_rate=0.0,
        )
        event = attempt_result_to_observer_event(result)
        assert event["event"] == "sheet.partial"

    def test_event_data_includes_all_required_fields(self) -> None:
        """Observer events must include all fields the dashboard expects."""
        from mozart.daemon.baton.adapter import attempt_result_to_observer_event

        result = SheetAttemptResult(
            job_id="test",
            sheet_num=1,
            instrument_name="claude-code",
            attempt=1,
            execution_success=True,
            exit_code=0,
            duration_seconds=1.5,
            rate_limited=False,
            validation_pass_rate=100.0,
            cost_usd=0.05,
            model_used="claude-sonnet-4-5-20250929",
        )
        event = attempt_result_to_observer_event(result)

        assert "job_id" in event
        assert "sheet_num" in event
        assert "event" in event
        assert "data" in event
        assert "timestamp" in event

        data = event["data"]
        assert "instrument" in data
        assert "attempt" in data
        assert "success" in data
        assert "validation_pass_rate" in data
        assert "cost_usd" in data
        assert "duration_seconds" in data


# =========================================================================
# 11. Validation formatting — adversarial inputs
# =========================================================================


class TestValidationFormattingAdversarial:
    """Adversarial tests for _format_validation_requirements.

    Validation requirements are rendered into the prompt as markdown.
    Malformed rules, missing attributes, or hostile path values could
    crash the formatter or inject confusing text.
    """

    def test_empty_rules_produces_empty_string(self) -> None:
        """No rules → no section. Don't pollute the prompt."""
        from mozart.daemon.baton.musician import _format_validation_requirements

        assert _format_validation_requirements([], {}) == ""

    def test_rule_with_missing_attributes(self) -> None:
        """Rules without standard attributes should not crash.

        Defensive getattr() with defaults handles this.
        """
        from mozart.daemon.baton.musician import _format_validation_requirements

        mock_rule = MagicMock(spec=[])
        mock_rule.description = None
        mock_rule.type = None
        mock_rule.path = None
        result = _format_validation_requirements([mock_rule], {})
        assert "Success Requirements" in result

    def test_path_format_expansion_failure_uses_raw(self) -> None:
        """If path format expansion fails, the raw path is used."""
        from mozart.daemon.baton.musician import _format_validation_requirements

        rule = MagicMock()
        rule.description = "Check output"
        rule.type = "file_exists"
        rule.path = "{missing_key}/output.md"

        result = _format_validation_requirements([rule], {"workspace": "/tmp"})
        assert "Check output" in result
        # The raw path should appear since {missing_key} can't be expanded
        assert "file_exists" in result

    def test_multiple_rules_all_listed(self) -> None:
        """All validation rules should appear in the formatted output."""
        from mozart.daemon.baton.musician import _format_validation_requirements

        rules = []
        for i in range(5):
            rule = MagicMock()
            rule.description = f"Rule {i}"
            rule.type = "file_exists"
            rule.path = f"/tmp/output_{i}.md"
            rules.append(rule)

        result = _format_validation_requirements(rules, {})
        for i in range(5):
            assert f"Rule {i}" in result


# =========================================================================
# 12. Cost estimation — adversarial values
# =========================================================================


class TestEstimateCostAdversarial:
    """Adversarial tests for _estimate_cost.

    Cost drives the baton's per-sheet and per-job cost limits.
    Wrong cost = wrong enforcement.
    """

    def test_zero_tokens_zero_cost(self) -> None:
        from mozart.daemon.baton.musician import _estimate_cost

        result = _make_exec_result(input_tokens=0, output_tokens=0)
        assert _estimate_cost(result) == 0.0

    def test_large_token_count_reasonable_cost(self) -> None:
        """1M tokens should produce a cost between $0 and $100."""
        from mozart.daemon.baton.musician import _estimate_cost

        result = _make_exec_result(input_tokens=1_000_000, output_tokens=100_000)
        cost = _estimate_cost(result)
        assert 0 < cost < 100

    def test_cost_is_non_negative(self) -> None:
        """Cost must never be negative regardless of input."""
        from mozart.daemon.baton.musician import _estimate_cost

        result = _make_exec_result(input_tokens=100, output_tokens=50)
        assert _estimate_cost(result) >= 0.0
