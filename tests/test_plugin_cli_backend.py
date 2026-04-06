"""Tests for PluginCliBackend — config-driven CLI instrument execution.

Covers:
- Command construction from CliProfile
- Output parsing (text, json, jsonl modes)
- Error classification with CliErrorConfig
- Rate limit detection from custom patterns
- Preamble and prompt extension injection
- Health check behavior
- Working directory propagation

TDD: Tests define the contract. Implementation fulfills it.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from marianne.backends.base import ExecutionResult
from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    executable: str = "echo",
    prompt_flag: str | None = "-p",
    output_format: str = "text",
    result_path: str | None = None,
    error_path: str | None = None,
    extra_flags: list[str] | None = None,
    success_exit_codes: list[int] | None = None,
    rate_limit_patterns: list[str] | None = None,
    auth_error_patterns: list[str] | None = None,
    timeout_patterns: list[str] | None = None,
    crash_patterns: list[str] | None = None,
    stale_patterns: list[str] | None = None,
    capacity_patterns: list[str] | None = None,
    auto_approve_flag: str | None = None,
    output_format_flag: str | None = None,
    output_format_value: str | None = None,
    model_flag: str | None = None,
    system_prompt_flag: str | None = None,
    timeout_flag: str | None = None,
    working_dir_flag: str | None = None,
    completion_event_type: str | None = None,
    input_tokens_path: str | None = None,
    output_tokens_path: str | None = None,
) -> InstrumentProfile:
    """Create a minimal InstrumentProfile for testing."""
    return InstrumentProfile(
        name="test-instrument",
        display_name="Test Instrument",
        kind="cli",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(
                executable=executable,
                prompt_flag=prompt_flag,
                auto_approve_flag=auto_approve_flag,
                output_format_flag=output_format_flag,
                output_format_value=output_format_value,
                model_flag=model_flag,
                system_prompt_flag=system_prompt_flag,
                timeout_flag=timeout_flag,
                working_dir_flag=working_dir_flag,
                extra_flags=extra_flags or [],
            ),
            output=CliOutputConfig(
                format=output_format,
                result_path=result_path,
                error_path=error_path,
                completion_event_type=completion_event_type,
                input_tokens_path=input_tokens_path,
                output_tokens_path=output_tokens_path,
            ),
            errors=CliErrorConfig(
                success_exit_codes=success_exit_codes or [0],
                rate_limit_patterns=rate_limit_patterns or [],
                auth_error_patterns=auth_error_patterns or [],
                timeout_patterns=timeout_patterns or [],
                crash_patterns=crash_patterns or [],
                stale_patterns=stale_patterns or [],
                capacity_patterns=capacity_patterns or [],
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Command Construction Tests
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    """Tests for how PluginCliBackend builds CLI commands from profiles."""

    def test_basic_command_with_prompt_flag(self) -> None:
        """Prompt is passed via the configured flag."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(executable="gemini", prompt_flag="-p")
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("Hello world", timeout_seconds=None)

        assert cmd[0] == "gemini"
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "Hello world"

    def test_positional_prompt_when_flag_is_none(self) -> None:
        """When prompt_flag is None, prompt is a positional argument."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(executable="codex", prompt_flag=None)
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("Do something", timeout_seconds=None)

        assert cmd[0] == "codex"
        assert cmd[-1] == "Do something"

    def test_auto_approve_flag_included(self) -> None:
        """Auto-approve flag is included when configured."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            executable="claude", auto_approve_flag="--dangerously-skip-permissions",
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("prompt", timeout_seconds=None)
        assert "--dangerously-skip-permissions" in cmd

    def test_model_flag_uses_default_model(self) -> None:
        """Model flag passes the default model name."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            executable="gemini", model_flag="--model",
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("prompt", timeout_seconds=None)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "test-model"

    def test_output_format_flag_with_value(self) -> None:
        """Output format flag includes both flag and value."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            output_format_flag="--output-format",
            output_format_value="json",
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("prompt", timeout_seconds=None)
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"

    def test_output_format_flag_boolean(self) -> None:
        """Boolean output format flag (no value) is just the flag."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            output_format_flag="--json",
            output_format_value=None,
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("prompt", timeout_seconds=None)
        assert "--json" in cmd

    def test_extra_flags_appended(self) -> None:
        """Extra flags are always appended."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(extra_flags=["--verbose", "--no-cache"])
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("prompt", timeout_seconds=None)
        assert "--verbose" in cmd
        assert "--no-cache" in cmd

    def test_timeout_flag_passed(self) -> None:
        """Timeout flag passes the timeout value."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(timeout_flag="--timeout")
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("prompt", timeout_seconds=300)
        assert "--timeout" in cmd
        idx = cmd.index("--timeout")
        assert cmd[idx + 1] == "300"


# ---------------------------------------------------------------------------
# Output Parsing Tests
# ---------------------------------------------------------------------------


class TestOutputParsing:
    """Tests for parsing CLI output based on CliOutputConfig."""

    def test_text_mode_returns_stdout_as_result(self) -> None:
        """In text mode, stdout IS the result."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(output_format="text")
        backend = PluginCliBackend(profile)
        result = backend._parse_output("Hello from CLI", "", exit_code=0)
        assert result.success is True
        assert result.stdout == "Hello from CLI"

    def test_json_mode_extracts_via_result_path(self) -> None:
        """In JSON mode, result is extracted via dot-path."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            output_format="json",
            result_path="response.text",
        )
        backend = PluginCliBackend(profile)
        stdout = json.dumps({"response": {"text": "Extracted result"}})
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.success is True
        assert result.stdout == "Extracted result"

    def test_json_mode_extracts_tokens(self) -> None:
        """JSON mode extracts token counts via configured paths."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            output_format="json",
            result_path="text",
            input_tokens_path="usage.input_tokens",
            output_tokens_path="usage.output_tokens",
        )
        backend = PluginCliBackend(profile)
        stdout = json.dumps({
            "text": "response",
            "usage": {"input_tokens": 150, "output_tokens": 75},
        })
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.input_tokens == 150
        assert result.output_tokens == 75

    def test_json_mode_error_path(self) -> None:
        """JSON mode extracts error via error_path on failure."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            output_format="json",
            result_path="text",
            error_path="error.message",
        )
        backend = PluginCliBackend(profile)
        stdout = json.dumps({"error": {"message": "API key invalid"}})
        result = backend._parse_output(stdout, "", exit_code=1)
        assert result.success is False
        assert result.error_message == "API key invalid"

    def test_jsonl_mode_finds_completion_event(self) -> None:
        """JSONL mode finds the completion event by type."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            output_format="jsonl",
            completion_event_type="result",
            result_path="text",
        )
        backend = PluginCliBackend(profile)
        lines = [
            json.dumps({"type": "progress", "text": "working..."}),
            json.dumps({"type": "result", "text": "Final answer"}),
        ]
        stdout = "\n".join(lines)
        result = backend._parse_output(stdout, "", exit_code=0)
        assert result.success is True
        assert result.stdout == "Final answer"


# ---------------------------------------------------------------------------
# Error Classification Tests
# ---------------------------------------------------------------------------


class TestErrorClassification:
    """Tests for error detection from CliErrorConfig."""

    def test_success_exit_code_is_success(self) -> None:
        """Exit code in success_exit_codes means success.

        Non-zero success codes are normalized to 0 in the ExecutionResult
        because the result invariant requires success=True → exit_code in (0, None).
        """
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(success_exit_codes=[0, 42])
        backend = PluginCliBackend(profile)
        result = backend._parse_output("output", "", exit_code=42)
        assert result.success is True
        # Non-zero success code normalized to 0
        assert result.exit_code == 0

    def test_non_success_exit_code_is_failure(self) -> None:
        """Exit code NOT in success_exit_codes means failure."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(success_exit_codes=[0])
        backend = PluginCliBackend(profile)
        result = backend._parse_output("output", "error msg", exit_code=1)
        assert result.success is False

    def test_rate_limit_pattern_detected_in_stderr(self) -> None:
        """Rate limit patterns in stderr are detected."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            rate_limit_patterns=[r"rate.?limit", r"429"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "Error: rate limit exceeded", exit_code=1,
        )
        assert result.rate_limited is True

    def test_rate_limit_pattern_detected_in_stdout(self) -> None:
        """Rate limit patterns in stdout are also detected."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            rate_limit_patterns=[r"429 Too Many Requests"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "429 Too Many Requests", "", exit_code=1,
        )
        assert result.rate_limited is True

    def test_auth_error_pattern_sets_error_type(self) -> None:
        """Auth error patterns from the profile should set error_type='auth'."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            auth_error_patterns=[r"invalid.?api.?key", r"authentication failed"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "Error: invalid api key", exit_code=1,
        )
        assert result.error_type == "auth"

    def test_timeout_pattern_sets_error_type(self) -> None:
        """Timeout patterns from the profile should set error_type='timeout'."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            timeout_patterns=[r"request timed out", r"deadline exceeded"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "Error: request timed out after 300s", exit_code=1,
        )
        assert result.error_type == "timeout"

    def test_crash_pattern_sets_error_type(self) -> None:
        """Crash patterns from the profile should set error_type='crash'."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            crash_patterns=[r"segmentation fault", r"bus error"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "segmentation fault (core dumped)", exit_code=139,
        )
        assert result.error_type == "crash"

    def test_stale_pattern_sets_error_type(self) -> None:
        """Stale patterns from the profile should set error_type='stale'."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            stale_patterns=[r"no output for \d+s", r"idle timeout"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "Stale: no output for 1800s", exit_code=1,
        )
        assert result.error_type == "stale"

    def test_capacity_pattern_sets_error_type(self) -> None:
        """Capacity patterns from the profile should set error_type='capacity'."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            capacity_patterns=[r"overloaded", r"capacity exceeded"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "Error: server overloaded, try again later", exit_code=1,
        )
        assert result.error_type == "capacity"

    def test_no_pattern_match_leaves_error_type_none(self) -> None:
        """When no patterns match, error_type should be None."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            auth_error_patterns=[r"invalid key"],
            timeout_patterns=[r"timed out"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "Some other error", exit_code=1,
        )
        assert result.error_type is None

    def test_success_does_not_classify_error_type(self) -> None:
        """Successful executions should not have error_type set."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            auth_error_patterns=[r"auth"],
        )
        backend = PluginCliBackend(profile)
        # Even if "auth" appears in output, success means no error classification
        result = backend._parse_output(
            "auth check passed", "", exit_code=0,
        )
        assert result.error_type is None

    def test_rate_limit_takes_priority_over_other_patterns(self) -> None:
        """Rate limiting should be detected even when other patterns match."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(
            rate_limit_patterns=[r"rate.?limit"],
            auth_error_patterns=[r"error"],
        )
        backend = PluginCliBackend(profile)
        result = backend._parse_output(
            "", "rate limit error", exit_code=1,
        )
        assert result.rate_limited is True


# ---------------------------------------------------------------------------
# Backend Properties Tests
# ---------------------------------------------------------------------------


class TestBackendProperties:
    """Tests for Backend ABC compliance."""

    def test_name_returns_display_name(self) -> None:
        """The name property returns the profile's display_name."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile()
        backend = PluginCliBackend(profile)
        assert backend.name == "Test Instrument"

    def test_working_directory_propagates(self) -> None:
        """Working directory can be set and is used in command execution."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile()
        backend = PluginCliBackend(profile)
        wd = Path("/tmp/test-workspace")
        backend.working_directory = wd
        assert backend.working_directory == wd

    def test_preamble_prepended_to_prompt(self) -> None:
        """Preamble text is prepended to the prompt."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile()
        backend = PluginCliBackend(profile)
        backend.set_preamble("You are sheet 5 of 10.")

        # Build a command — the prompt should have the preamble prepended
        cmd = backend._build_command("Do the task", timeout_seconds=None)
        # Find the prompt in the command
        idx = cmd.index("-p")
        full_prompt = cmd[idx + 1]
        assert "You are sheet 5 of 10." in full_prompt
        assert "Do the task" in full_prompt

    def test_prompt_extensions_appended(self) -> None:
        """Prompt extensions are appended to the prompt."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile()
        backend = PluginCliBackend(profile)
        backend.set_prompt_extensions(["Extension 1", "Extension 2"])

        cmd = backend._build_command("Main prompt", timeout_seconds=None)
        idx = cmd.index("-p")
        full_prompt = cmd[idx + 1]
        assert "Extension 1" in full_prompt
        assert "Extension 2" in full_prompt
        assert "Main prompt" in full_prompt

    async def test_health_check_uses_executable(self) -> None:
        """Health check verifies the executable exists and runs."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        # Use 'echo' which always exists
        profile = _make_profile(executable="echo")
        backend = PluginCliBackend(profile)

        with patch("shutil.which", return_value="/usr/bin/echo"):
            result = await backend.health_check()
        assert result is True

    async def test_health_check_fails_for_missing_executable(self) -> None:
        """Health check returns False for missing executable."""
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        profile = _make_profile(executable="nonexistent-tool-xyz")
        backend = PluginCliBackend(profile)

        with patch("shutil.which", return_value=None):
            result = await backend.health_check()
        assert result is False
