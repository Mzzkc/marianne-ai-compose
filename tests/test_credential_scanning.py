"""Tests for credential scanning in output capture (F-003).

Verifies that API keys and tokens in stdout/stderr are redacted before
being stored in CheckpointState, preventing credential leaks into:
- Learning store patterns
- Dashboard display
- Diagnostic output
- MCP resources
"""

import pytest

from mozart.core.checkpoint import SheetState
from mozart.utils.credential_scanner import redact_credentials, scan_for_credentials


class TestRedactCredentials:
    """Tests for the redact_credentials utility."""

    def test_no_credentials_returns_unchanged(self) -> None:
        """Normal text passes through without modification."""
        text = "Sheet 5 completed successfully. All validations passed."
        result = redact_credentials(text)
        assert result == text

    def test_none_passes_through(self) -> None:
        """None input returns None."""
        assert redact_credentials(None) is None

    def test_anthropic_key_redacted(self) -> None:
        """Anthropic API keys (sk-ant-api...) are redacted."""
        text = "Error: Invalid key sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456"
        result = redact_credentials(text)
        assert "sk-ant-api" not in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result

    def test_openai_project_key_redacted(self) -> None:
        """OpenAI project API keys (sk-proj-...) are redacted."""
        text = "Using API key: sk-proj-1234567890abcdefghij1234567890"
        result = redact_credentials(text)
        assert "sk-proj-" not in result
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_google_key_redacted(self) -> None:
        """Google API keys (AIzaSy...) are redacted."""
        text = "GOOGLE_API_KEY=AIzaSyA1234567890abcdefghijklmnopqr"
        result = redact_credentials(text)
        assert "AIzaSy" not in result
        assert "[REDACTED_GOOGLE_KEY]" in result

    def test_aws_key_redacted(self) -> None:
        """AWS access keys (AKIA...) are redacted."""
        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        result = redact_credentials(text)
        assert "AKIA" not in result
        assert "[REDACTED_AWS_KEY]" in result

    def test_bearer_token_redacted(self) -> None:
        """Bearer tokens in Authorization headers are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload"
        result = redact_credentials(text)
        assert "eyJhbGci" not in result
        assert "[REDACTED_BEARER_TOKEN]" in result

    def test_multiple_credentials_all_redacted(self) -> None:
        """Multiple credentials of different types are all redacted."""
        text = (
            "ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxx "
            "AWS_KEY=AKIAIOSFODNN7EXAMPLE"
        )
        result = redact_credentials(text)
        assert "sk-ant-api" not in result
        assert "AKIA" not in result

    def test_empty_string(self) -> None:
        """Empty string returns empty string."""
        result = redact_credentials("")
        assert result == ""

    def test_short_sk_prefix_not_false_positive(self) -> None:
        """Short 'sk-' strings below the min length are not redacted."""
        text = "The task sk-ip was completed"
        result = redact_credentials(text)
        assert result == text


class TestScanForCredentials:
    """Tests for scan_for_credentials (detection without redaction)."""

    def test_clean_text_returns_empty(self) -> None:
        """Clean text returns empty list."""
        result = scan_for_credentials("Hello world")
        assert result == []

    def test_detects_anthropic_key(self) -> None:
        """Detects Anthropic API key pattern."""
        result = scan_for_credentials("sk-ant-api03-abcdefghijklmnop")
        assert any("Anthropic" in d for d in result)

    def test_detects_aws_key(self) -> None:
        """Detects AWS access key pattern."""
        result = scan_for_credentials("AKIAIOSFODNN7EXAMPLE")
        assert any("AWS" in d for d in result)

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty list."""
        assert scan_for_credentials("") == []


class TestCaptureOutputRedaction:
    """Tests for credential redaction in SheetState.capture_output."""

    def test_stdout_credential_redacted(self) -> None:
        """Credential in stdout is redacted before storage."""
        state = SheetState(sheet_num=1)
        state.capture_output(
            stdout="Result: sk-ant-api03-secretkeyabcdefghijklmnop",
            stderr="",
        )
        assert state.stdout_tail is not None
        assert "sk-ant-api" not in state.stdout_tail
        assert "[REDACTED" in state.stdout_tail

    def test_stderr_credential_redacted(self) -> None:
        """Credential in stderr is redacted before storage."""
        state = SheetState(sheet_num=2)
        state.capture_output(
            stdout="",
            stderr="Error: AKIAIOSFODNN7EXAMPLE leaked",
        )
        assert state.stderr_tail is not None
        assert "AKIA" not in state.stderr_tail
        assert "[REDACTED" in state.stderr_tail

    def test_clean_output_unchanged(self) -> None:
        """Output without credentials passes through unchanged."""
        state = SheetState(sheet_num=3)
        original = "Build completed successfully in 42s"
        state.capture_output(stdout=original, stderr="")
        assert state.stdout_tail == original

    def test_large_output_with_credential_still_redacted(self) -> None:
        """Even truncated output has credentials redacted."""
        state = SheetState(sheet_num=4)
        # Create output larger than default max_bytes
        large_output = "x" * 60000 + " sk-ant-api03-secretkeyabcdefghijklmnop"
        state.capture_output(stdout=large_output, stderr="")
        assert state.stdout_tail is not None
        assert "sk-ant-api" not in state.stdout_tail

    @pytest.mark.adversarial()
    def test_credential_in_json_output(self) -> None:
        """Credential embedded in JSON output is still caught."""
        state = SheetState(sheet_num=5)
        state.capture_output(
            stdout='{"api_key": "sk-ant-api03-xxxxxxxxxxxxxxxxxxxx", "result": "ok"}',
            stderr="",
        )
        assert state.stdout_tail is not None
        assert "sk-ant-api" not in state.stdout_tail

    @pytest.mark.adversarial()
    def test_credential_on_separate_line(self) -> None:
        """Credential on a separate line is caught."""
        state = SheetState(sheet_num=6)
        state.capture_output(
            stdout="line1\nAKIAIOSFODNN7EXAMPLE\nline3",
            stderr="",
        )
        assert state.stdout_tail is not None
        assert "AKIA" not in state.stdout_tail
