"""Tests for credential redaction in musician error paths.

Found by Warden M2 safety audit: The musician's exception handler at
musician.py:156 constructs error_msg from exception text WITHOUT credential
redaction. This error_msg is:
1. Logged at ERROR level (persists to log files)
2. Stored in SheetAttemptResult.error_message (persists to state DB)
3. Visible in `mozart diagnose` and `mozart errors` output
4. Indexed by the learning store for pattern matching

The musician DOES redact stdout_tail/stderr_tail (line 573) — this gap
is specifically about error_message from caught exceptions.

Similarly, validation engine exceptions at musician.py:551-555 store
unredacted error text in the validation details dict.

This file tests that credential patterns in exception messages are
redacted before storage and logging.

See: F-003 (original credential scanning), F-023 (pattern expansion)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from marianne.utils.credential_scanner import redact_credentials

# =========================================================================
# Test: error_message field in SheetAttemptResult gets redacted
# =========================================================================


class TestMusicianErrorMessageRedaction:
    """Verify that exception messages containing credentials are redacted
    before being stored in SheetAttemptResult.error_message."""

    async def test_anthropic_key_in_exception_redacted(self) -> None:
        """An exception containing an Anthropic API key gets redacted."""
        # Simulate an exception whose message contains a credential
        error_msg = (
            "ConnectionError: Failed to connect to API with key "
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted
        assert "[REDACTED_ANTHROPIC_KEY]" in redacted
        # The non-credential part is preserved
        assert "ConnectionError: Failed to connect to API with key" in redacted

    async def test_openai_key_in_exception_redacted(self) -> None:
        """An exception containing an OpenAI API key gets redacted."""
        error_msg = (
            "AuthenticationError: Invalid API key: "
            "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890extra"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "sk-proj-" not in redacted
        assert "[REDACTED_OPENAI_KEY]" in redacted

    async def test_google_key_in_exception_redacted(self) -> None:
        """An exception containing a Google API key gets redacted."""
        error_msg = (
            "ValueError: Google API returned 403 for key "
            "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012345"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "AIzaSy" not in redacted
        assert "[REDACTED_GOOGLE_KEY]" in redacted

    async def test_aws_key_in_exception_redacted(self) -> None:
        """An exception containing an AWS access key gets redacted."""
        error_msg = (
            "botocore.exceptions.ClientError: Access denied for "
            "AKIAIOSFODNN7EXAMPLE"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "[REDACTED_AWS_KEY]" in redacted

    async def test_multiple_credentials_in_one_exception(self) -> None:
        """An exception containing multiple credential types gets all redacted."""
        error_msg = (
            "ConfigError: Found API key sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxx "
            "and Google key AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012345 in config"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted
        assert "AIzaSy" not in redacted
        assert "[REDACTED_ANTHROPIC_KEY]" in redacted
        assert "[REDACTED_GOOGLE_KEY]" in redacted
        assert "ConfigError: Found API key" in redacted

    async def test_github_token_in_exception_redacted(self) -> None:
        """A GitHub PAT in an exception gets redacted."""
        error_msg = (
            "git.GitError: Authentication failed for token "
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "ghp_" not in redacted
        assert "[REDACTED_GITHUB_TOKEN]" in redacted

    async def test_slack_token_in_exception_redacted(self) -> None:
        """A Slack bot token in an exception gets redacted."""
        error_msg = (
            "SlackApiError: invalid_auth for xoxb-123456789012-abcdefghijklmn"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "xoxb-" not in redacted
        assert "[REDACTED_SLACK_TOKEN]" in redacted

    async def test_hf_token_in_exception_redacted(self) -> None:
        """A Hugging Face token in an exception gets redacted."""
        error_msg = (
            "HTTPError: 401 for token hf_ABCDEFGHIJKLMNOPQRSTUVWXYZab"
        )
        redacted = redact_credentials(error_msg)
        assert redacted is not None
        assert "hf_" not in redacted
        assert "[REDACTED_HF_TOKEN]" in redacted

    async def test_no_credentials_passes_through(self) -> None:
        """Normal exception messages pass through unchanged."""
        error_msg = "FileNotFoundError: /workspace/output.md not found"
        redacted = redact_credentials(error_msg)
        assert redacted == error_msg

    async def test_empty_error_passes_through(self) -> None:
        """Empty error message passes through."""
        assert redact_credentials("") == ""

    async def test_none_error_passes_through(self) -> None:
        """None error message passes through."""
        assert redact_credentials(None) is None


# =========================================================================
# Test: validation error details get redacted
# =========================================================================


class TestValidationErrorRedaction:
    """Verify that validation engine exception messages are redacted
    before being stored in the validation details dict."""

    async def test_validation_error_with_credential_redacted(self) -> None:
        """Validation engine exception containing a credential is redacted."""
        error_text = (
            "ValidationError: command 'curl -H \"Authorization: Bearer "
            "sk-ant-api03-secretkeysecretkeysecretkeysecretkey\" http://localhost' "
            "failed"
        )
        redacted = redact_credentials(error_text)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted
        assert "[REDACTED_ANTHROPIC_KEY]" in redacted

    async def test_validation_path_with_credential_redacted(self) -> None:
        """File path containing a credential pattern is redacted."""
        error_text = (
            "FileNotFoundError: /workspace/sk-ant-api03-leaked-in-path-name/output.md"
        )
        redacted = redact_credentials(error_text)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted


# =========================================================================
# Test: credential scanner adversarial edge cases
# =========================================================================


class TestCredentialScannerAdversarial:
    """Adversarial edge cases for the credential scanner itself."""

    async def test_credential_at_start_of_string(self) -> None:
        """Credential at the very start of a string."""
        text = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890 was found"
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "[REDACTED_ANTHROPIC_KEY]" in redacted
        assert "was found" in redacted

    async def test_credential_at_end_of_string(self) -> None:
        """Credential at the very end of a string."""
        text = "Key is: sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "[REDACTED_ANTHROPIC_KEY]" in redacted

    async def test_credential_on_its_own_line(self) -> None:
        """Credential on its own line in multi-line output."""
        text = "Error details:\nsk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890\nEnd"
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted

    async def test_bearer_token_in_curl_command(self) -> None:
        """Bearer token inside a curl command in error output."""
        text = (
            'curl: (22) The requested URL returned error: 401\n'
            '> Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.long.token.here'
        )
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted
        assert "[REDACTED_BEARER_TOKEN]" in redacted

    async def test_multiple_same_type_credentials(self) -> None:
        """Multiple credentials of the same type all get redacted."""
        text = (
            "keys: sk-ant-api03-aaaaaaaaaaaaaaaaaaa, "
            "sk-ant-api03-bbbbbbbbbbbbbbbbbbb"
        )
        redacted = redact_credentials(text)
        assert redacted is not None
        assert redacted.count("[REDACTED_ANTHROPIC_KEY]") == 2

    async def test_short_sk_prefix_not_false_positive(self) -> None:
        """Short 'sk-' strings should NOT be redacted (not long enough)."""
        text = "The sk-short string is not a real key"
        redacted = redact_credentials(text)
        assert redacted == text  # No change

    async def test_unicode_around_credential(self) -> None:
        """Credential surrounded by unicode characters."""
        text = "认证失败：sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890 请重试"
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted
        assert "认证失败" in redacted
        assert "请重试" in redacted

    async def test_credential_in_json_value(self) -> None:
        """Credential inside a JSON string value."""
        text = '{"api_key": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890", "status": "error"}'
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "sk-ant-api03" not in redacted
        assert '"status": "error"' in redacted

    async def test_credential_in_traceback(self) -> None:
        """Credential embedded in a Python traceback."""
        text = (
            'Traceback (most recent call last):\n'
            '  File "config.py", line 42, in load\n'
            '    raise ValueError(f"Bad key: {key}")\n'
            'ValueError: Bad key: AKIAIOSFODNN7EXAMPLE\n'
        )
        redacted = redact_credentials(text)
        assert redacted is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "[REDACTED_AWS_KEY]" in redacted
        assert "Traceback" in redacted


# =========================================================================
# Integration: musician sheet_task exception path redacts error_message
# =========================================================================


class TestMusicianSheetTaskErrorRedaction:
    """Integration tests proving the musician's sheet_task function redacts
    credential patterns from error_message when exceptions are caught.

    These tests exercise the ACTUAL code path at musician.py:153-178 where
    exceptions are caught and converted to SheetAttemptResult.error_message.
    """

    def _make_sheet(self, tmp_path: Path) -> Any:
        """Create a minimal Sheet for testing."""
        from marianne.core.sheet import Sheet

        return Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            description="test sheet",
            workspace=tmp_path,
            instrument_name="test-instrument",
            instrument_config={},
            prompt_template="Hello {{ workspace }}",
            template_file=None,
            variables={},
            prelude=[],
            cadenza=[],
            prompt_extensions=[],
            validations=[],
            timeout_seconds=30.0,
        )

    def _make_context(self) -> Any:
        """Create a minimal AttemptContext."""
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        return AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
        )

    async def test_exception_with_anthropic_key_redacted_in_result(
        self, tmp_path: Path
    ) -> None:
        """When a backend raises an exception containing an Anthropic key,
        the SheetAttemptResult.error_message must have the key redacted."""
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        # Create a backend that raises with a credential in the message
        class CredentialLeakingBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> Any:
                raise ConnectionError(
                    "Auth failed with key "
                    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=CredentialLeakingBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        # The credential MUST be redacted
        assert "sk-ant-api03" not in result.error_message
        assert "[REDACTED_ANTHROPIC_KEY]" in result.error_message
        # The non-credential part is preserved
        assert "ConnectionError" in result.error_message

    async def test_exception_with_aws_key_redacted_in_result(
        self, tmp_path: Path
    ) -> None:
        """AWS key in exception message must be redacted."""
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class AwsLeakingBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> Any:
                raise PermissionError(
                    "Access denied for AKIAIOSFODNN7EXAMPLE"
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=AwsLeakingBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in result.error_message
        assert "[REDACTED_AWS_KEY]" in result.error_message

    async def test_exception_without_credentials_unchanged(
        self, tmp_path: Path
    ) -> None:
        """Normal exceptions without credentials preserve the full message."""
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class NormalErrorBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> Any:
                raise TimeoutError("Backend timed out after 30 seconds")

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=NormalErrorBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        assert result.error_message == "TimeoutError: Backend timed out after 30 seconds"

    async def test_exception_with_multiple_credentials_all_redacted(
        self, tmp_path: Path
    ) -> None:
        """Multiple credential types in one exception all get redacted."""
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class MultiCredentialBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> Any:
                raise ValueError(
                    "Config has key=sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaa "
                    "and google=AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012345"
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=MultiCredentialBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        assert "sk-ant-api03" not in result.error_message
        assert "AIzaSy" not in result.error_message
        assert "[REDACTED_ANTHROPIC_KEY]" in result.error_message
        assert "[REDACTED_GOOGLE_KEY]" in result.error_message


# =========================================================================
# Test: _classify_error() path redacts error_message from ExecutionResult
# =========================================================================


class TestClassifyErrorPathRedaction:
    """Verify that error_message from backend ExecutionResult is redacted
    when it flows through the _classify_error() code path.

    Found by Sentinel M2: _classify_error() at musician.py:587 returns
    exec_result.error_message directly at lines 608, 622, 627 without
    calling redact_credentials(). A backend that sets error_message to
    a string containing an API key would store that key in
    SheetAttemptResult.error_message → state DB → dashboard → logs.

    This is a DIFFERENT path from the exception handler (tested above).
    The exception handler catches Python exceptions. This path handles
    backends that return ExecutionResult(success=False, error_message=...).
    """

    def _make_sheet(self, tmp_path: Path) -> Any:
        """Create a minimal Sheet for testing."""
        from marianne.core.sheet import Sheet

        return Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            description="test sheet",
            workspace=tmp_path,
            instrument_name="test-instrument",
            instrument_config={},
            prompt_template="Hello {{ workspace }}",
            template_file=None,
            variables={},
            prelude=[],
            cadenza=[],
            prompt_extensions=[],
            validations=[],
            timeout_seconds=30.0,
        )

    def _make_context(self) -> Any:
        """Create a minimal AttemptContext."""
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        return AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
        )

    async def test_error_message_with_anthropic_key_redacted(
        self, tmp_path: Path
    ) -> None:
        """Backend returning error_message with Anthropic key: key is redacted.

        Exercises the TRANSIENT path at musician.py _classify_error line 608
        (exit_code=None → process killed by signal).
        """
        from marianne.backends.base import ExecutionResult
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class ErrorMessageLeakingBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> ExecutionResult:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="auth error",
                    duration_seconds=1.0,
                    exit_code=None,  # Triggers TRANSIENT path
                    error_message=(
                        "Auth failed with key "
                        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
                    ),
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=ErrorMessageLeakingBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        assert "sk-ant-api03" not in result.error_message
        assert "[REDACTED_ANTHROPIC_KEY]" in result.error_message
        assert result.error_classification == "TRANSIENT"

    async def test_auth_failure_error_message_with_key_redacted(
        self, tmp_path: Path
    ) -> None:
        """Backend returning AUTH_FAILURE with key in error_message: key is redacted.

        Exercises the AUTH_FAILURE path at musician.py _classify_error line 622.
        """
        from marianne.backends.base import ExecutionResult
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class AuthFailureLeakingBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> ExecutionResult:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="401 unauthorized for api key",
                    duration_seconds=1.0,
                    exit_code=1,
                    error_message=(
                        "Unauthorized: invalid API key "
                        "sk-ant-api03-secretsecretsecretsecretsecret1234"
                    ),
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=AuthFailureLeakingBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        assert "sk-ant-api03" not in result.error_message
        assert "[REDACTED_ANTHROPIC_KEY]" in result.error_message
        assert result.error_classification == "AUTH_FAILURE"

    async def test_execution_error_message_with_aws_key_redacted(
        self, tmp_path: Path
    ) -> None:
        """Backend returning EXECUTION_ERROR with AWS key: key is redacted.

        Exercises the default EXECUTION_ERROR path at _classify_error line 627.
        """
        from marianne.backends.base import ExecutionResult
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class AwsErrorBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> ExecutionResult:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="access denied",
                    duration_seconds=2.0,
                    exit_code=1,
                    error_message=(
                        "S3 PutObject failed for AKIAIOSFODNN7EXAMPLE"
                    ),
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=AwsErrorBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in result.error_message
        assert "[REDACTED_AWS_KEY]" in result.error_message
        assert result.error_classification == "EXECUTION_ERROR"

    async def test_none_error_message_passes_through(
        self, tmp_path: Path
    ) -> None:
        """Backend returning error_message=None: passes through unchanged."""
        from marianne.backends.base import ExecutionResult
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class NoneMessageBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> ExecutionResult:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="something failed",
                    duration_seconds=1.0,
                    exit_code=1,
                    error_message=None,
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=NoneMessageBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        # error_message should be the fallback from _classify_error
        assert result.error_message == "Exit code 1"
        assert result.error_classification == "EXECUTION_ERROR"

    async def test_clean_error_message_preserved(
        self, tmp_path: Path
    ) -> None:
        """Backend returning error_message without credentials: preserved intact."""
        from marianne.backends.base import ExecutionResult
        from marianne.daemon.baton.events import SheetAttemptResult
        from marianne.daemon.baton.musician import sheet_task

        sheet = self._make_sheet(tmp_path)
        context = self._make_context()
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()

        class CleanErrorBackend:
            def set_preamble(self, text: str) -> None:
                pass

            async def execute(self, prompt: str, **kw: Any) -> ExecutionResult:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="connection timed out",
                    duration_seconds=30.0,
                    exit_code=None,
                    error_message="Backend timed out after 30 seconds",
                )

        await sheet_task(
            job_id="test-job",
            sheet=sheet,
            backend=CleanErrorBackend(),  # type: ignore[arg-type]
            attempt_context=context,
            inbox=inbox,
        )

        result = await inbox.get()
        assert result.error_message == "Backend timed out after 30 seconds"
