"""Tests for mozart.utils.credential_scanner — output credential redaction.

TDD: Tests written BEFORE implementation. The scanner detects and redacts
API key patterns in agent output before storage in CheckpointState,
learning store, dashboard, and diagnostics.

Addresses F-003: stdout_tail/stderr_tail stored in 6+ locations without
scanning for credential patterns.
"""

from __future__ import annotations

import pytest


class TestRedactCredentials:
    """Tests for the redact_credentials utility function."""

    def test_no_credentials_unchanged(self) -> None:
        """Text without credentials passes through unchanged."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "Hello, this is normal output with no secrets."
        assert redact_credentials(text) == text

    def test_anthropic_api_key_redacted(self) -> None:
        """Anthropic API keys (sk-ant-...) are redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "Using key sk-ant-api03-abcdefghijklmnop1234567890 for auth"
        result = redact_credentials(text)
        assert "sk-ant-api03-abcdefghijklmnop1234567890" not in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result
        assert "Using key" in result  # surrounding text preserved
        assert "for auth" in result

    def test_openai_api_key_redacted(self) -> None:
        """OpenAI API keys (sk-...) are redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "export OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345"
        result = redact_credentials(text)
        assert "sk-proj-abc123" not in result
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_google_api_key_redacted(self) -> None:
        """Google API keys (AIza...) are redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "GOOGLE_API_KEY=AIzaSyA1B2C3D4E5F6G7H8I9J0KlMnOpQrSt"
        result = redact_credentials(text)
        assert "AIzaSyA1B2C3" not in result
        assert "[REDACTED_GOOGLE_KEY]" in result

    def test_aws_access_key_redacted(self) -> None:
        """AWS access keys (AKIA...) are redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE"
        result = redact_credentials(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED_AWS_KEY]" in result

    def test_generic_bearer_token_redacted(self) -> None:
        """Bearer tokens in Authorization headers are redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWI'
        result = redact_credentials(text)
        assert "eyJhbGciOiJ" not in result
        assert "[REDACTED_BEARER_TOKEN]" in result

    def test_multiple_credentials_all_redacted(self) -> None:
        """Multiple different credential types in one text are all caught."""
        from marianne.utils.credential_scanner import redact_credentials

        text = (
            "Keys: sk-ant-api03-abcdefghijk1234567890 and AKIAIOSFODNN7EXAMPLE "
            "and AIzaSyAbcdefghijklmnopqrstuvwxyz12"
        )
        result = redact_credentials(text)
        assert "sk-ant-api03-abcdefghijk" not in result
        assert "AKIAIOSFODNN7" not in result
        assert "AIzaSyAbcdef" not in result

    def test_none_input_returns_none(self) -> None:
        """None input returns None (for optional stdout_tail)."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials(None) is None  # type: ignore[arg-type]

    def test_empty_string_returns_empty(self) -> None:
        """Empty string returns empty string."""
        from marianne.utils.credential_scanner import redact_credentials

        assert redact_credentials("") == ""

    @pytest.mark.adversarial
    def test_partial_key_not_false_positive(self) -> None:
        """Short strings that look like key prefixes but aren't full keys."""
        from marianne.utils.credential_scanner import redact_credentials

        # "sk-" alone is too short to be a key
        text = "Use sk-something for the field"
        result = redact_credentials(text)
        # Short sk- prefixes without sufficient length shouldn't be redacted
        assert "sk-something" in result

    @pytest.mark.adversarial
    def test_base64_in_code_not_false_positive(self) -> None:
        """Base64 in code that isn't a bearer token shouldn't be redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = 'data = base64.b64encode(b"hello")'
        result = redact_credentials(text)
        # Normal base64 operations shouldn't trigger
        assert result == text

    @pytest.mark.adversarial
    def test_large_text_performance(self) -> None:
        """Scanner handles large outputs (100KB) without excessive time."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "Normal output line\n" * 5000  # ~100KB
        text += "Hidden key: sk-ant-api03-secret123456789012345678\n"
        text += "More output\n" * 5000
        result = redact_credentials(text)
        assert "secret12345" not in result


class TestGitHubTokenRedaction:
    """Tests for GitHub PAT token redaction (F-023 fix)."""

    def test_github_pat_classic_redacted(self) -> None:
        """Classic GitHub PAT (ghp_) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "git clone https://ghp_ABCDEFghijklmnop1234567890abcdefghijklmn@github.com/repo"
        result = redact_credentials(text)
        assert "ghp_ABCDEF" not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result

    def test_github_oauth_token_redacted(self) -> None:
        """GitHub OAuth token (gho_) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "GITHUB_TOKEN=gho_ABCDEFghijklmnop1234567890abcdefghijklmn"
        result = redact_credentials(text)
        assert "gho_ABCDEF" not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result

    def test_github_fine_grained_pat_redacted(self) -> None:
        """Fine-grained GitHub PAT (github_pat_) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "export GH_TOKEN=github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz1234567890"
        result = redact_credentials(text)
        assert "github_pat_11ABCDEFG0" not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result

    @pytest.mark.adversarial
    def test_short_ghp_not_false_positive(self) -> None:
        """Short ghp_ prefix without sufficient chars is not redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "ghp_short is not a token"
        result = redact_credentials(text)
        assert "ghp_short" in result  # too short to be a real token


class TestSlackTokenRedaction:
    """Tests for Slack token redaction (F-023 fix)."""

    def test_slack_bot_token_redacted(self) -> None:
        """Slack bot token (xoxb-) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "SLACK_TOKEN=xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx"
        result = redact_credentials(text)
        assert "xoxb-123456789012" not in result
        assert "[REDACTED_SLACK_TOKEN]" in result

    def test_slack_user_token_redacted(self) -> None:
        """Slack user token (xoxp-) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "token: xoxp-123456789012-1234567890123-1234567890123-abcdefghijklmnopqrstuvwxyz123456"
        result = redact_credentials(text)
        assert "xoxp-123456789012" not in result
        assert "[REDACTED_SLACK_TOKEN]" in result

    def test_slack_app_token_redacted(self) -> None:
        """Slack app-level token (xapp-) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "SLACK_APP=xapp-1-A1234567890-1234567890123-abcdefghijklmnopqrstuvwx"
        result = redact_credentials(text)
        assert "xapp-1-A123456" not in result
        assert "[REDACTED_SLACK_TOKEN]" in result


class TestHuggingFaceTokenRedaction:
    """Tests for Hugging Face token redaction (F-023 fix)."""

    def test_hf_token_redacted(self) -> None:
        """Hugging Face token (hf_) is redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "HF_TOKEN=hf_ABCDEFghijklmnopQRSTUV"
        result = redact_credentials(text)
        assert "hf_ABCDEFghijklmnop" not in result
        assert "[REDACTED_HF_TOKEN]" in result

    @pytest.mark.adversarial
    def test_short_hf_not_false_positive(self) -> None:
        """Short hf_ prefix is not redacted."""
        from marianne.utils.credential_scanner import redact_credentials

        text = "hf_short is not enough"
        result = redact_credentials(text)
        assert "hf_short" in result  # too short


class TestScanForCredentials:
    """Tests for scan_for_credentials — detection without redaction."""

    def test_detects_anthropic_key(self) -> None:
        """Detects Anthropic API key presence."""
        from marianne.utils.credential_scanner import scan_for_credentials

        text = "Key: sk-ant-api03-abcdefghijk1234567890123456"
        found = scan_for_credentials(text)
        assert len(found) > 0
        assert any("anthropic" in f.lower() for f in found)

    def test_clean_text_empty_result(self) -> None:
        """Clean text returns empty list."""
        from marianne.utils.credential_scanner import scan_for_credentials

        found = scan_for_credentials("No credentials here")
        assert found == []

    def test_detects_github_token(self) -> None:
        """Detects GitHub PAT presence."""
        from marianne.utils.credential_scanner import scan_for_credentials

        text = "ghp_ABCDEFghijklmnop1234567890abcdefghijklmn"
        found = scan_for_credentials(text)
        assert len(found) > 0
        assert any("github" in f.lower() for f in found)

    def test_detects_slack_token(self) -> None:
        """Detects Slack token presence."""
        from marianne.utils.credential_scanner import scan_for_credentials

        text = "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx"
        found = scan_for_credentials(text)
        assert len(found) > 0
        assert any("slack" in f.lower() for f in found)

    def test_detects_hf_token(self) -> None:
        """Detects Hugging Face token presence."""
        from marianne.utils.credential_scanner import scan_for_credentials

        text = "hf_ABCDEFghijklmnopQRSTUV"
        found = scan_for_credentials(text)
        assert len(found) > 0
        assert any("hugging" in f.lower() for f in found)
