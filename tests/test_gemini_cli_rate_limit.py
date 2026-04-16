"""Tests for gemini-cli rate limit and error classification.

Verifies that PluginCliBackend correctly detects rate limiting and
classifies error types using the gemini-cli instrument profile's
error patterns. Uses the actual patterns from gemini-cli.yaml.

TDD: Tests define the contract. Implementation fulfills it.
"""

from __future__ import annotations

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend

# ---------------------------------------------------------------------------
# Fixtures — mirrors gemini-cli.yaml error patterns
# ---------------------------------------------------------------------------


def _gemini_profile() -> InstrumentProfile:
    """Create an InstrumentProfile matching gemini-cli.yaml error config."""
    return InstrumentProfile(
        name="gemini-cli",
        display_name="Gemini CLI",
        kind="cli",
        models=[
            ModelCapacity(
                name="gemini-2.5-pro",
                context_window=1000000,
                cost_per_1k_input=0.00125,
                cost_per_1k_output=0.005,
            ),
        ],
        default_model="gemini-2.5-pro",
        cli=CliProfile(
            command=CliCommand(
                executable="gemini",
                prompt_flag="-p",
                model_flag="-m",
                auto_approve_flag="--yolo",
                output_format_flag="-o",
                output_format_value="json",
            ),
            output=CliOutputConfig(
                format="json",
                result_path="response",
                error_path="error.message",
                input_tokens_path="stats.models.*.tokens.prompt",
                output_tokens_path="stats.models.*.tokens.candidates",
                aggregate_tokens=True,
            ),
            errors=CliErrorConfig(
                success_exit_codes=[0],
                rate_limit_patterns=[
                    "rate.?limit",
                    "quota.?exceeded",
                    "429",
                    "RESOURCE_EXHAUSTED",
                    "Too Many Requests",
                ],
                auth_error_patterns=[
                    "authenticat",
                    "unauthorized",
                    "API key",
                    "PERMISSION_DENIED",
                    "PermissionDeniedError",
                ],
                capacity_patterns=[
                    "503",
                    "overloaded",
                    "UNAVAILABLE",
                    "ServerError",
                ],
                timeout_patterns=[
                    "deadline.?exceeded",
                    "DEADLINE_EXCEEDED",
                    "timed?.?out",
                ],
            ),
        ),
    )


def _make_backend() -> PluginCliBackend:
    """Create a PluginCliBackend with gemini-cli profile."""
    from marianne.execution.instruments.cli_backend import PluginCliBackend

    return PluginCliBackend(_gemini_profile())


# ---------------------------------------------------------------------------
# Rate Limit Detection Tests
# ---------------------------------------------------------------------------


class TestGeminiRateLimitDetection:
    """Verify rate limit detection with gemini-cli's patterns."""

    def test_resource_exhausted_detected(self) -> None:
        """RESOURCE_EXHAUSTED from Google API triggers rate limit."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: RESOURCE_EXHAUSTED - Quota exceeded",
            exit_code=1,
        )
        assert result.rate_limited, "RESOURCE_EXHAUSTED should trigger rate limit"

    def test_429_status_code_detected(self) -> None:
        """HTTP 429 in error output triggers rate limit."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: Request failed with status 429",
            exit_code=1,
        )
        assert result.rate_limited, "429 in output should trigger rate limit"

    def test_too_many_requests_detected(self) -> None:
        """'Too Many Requests' message triggers rate limit."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: Too Many Requests - please retry after 30 seconds",
            exit_code=1,
        )
        assert result.rate_limited, "'Too Many Requests' should trigger rate limit"

    def test_quota_exceeded_detected(self) -> None:
        """'quota exceeded' message triggers rate limit."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: quota exceeded for project",
            exit_code=1,
        )
        assert result.rate_limited, "'quota exceeded' should trigger rate limit"

    def test_rate_limit_phrase_detected(self) -> None:
        """'rate limit' phrase triggers detection."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="rate limit reached, retrying in 60s",
            exit_code=1,
        )
        assert result.rate_limited, "'rate limit' should trigger detection"

    def test_no_rate_limit_on_success(self) -> None:
        """Successful output should not be flagged as rate limited."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"response": "Hello!", "stats": {}}',
            stderr="",
            exit_code=0,
        )
        assert not result.rate_limited, "Successful responses should not be rate limited"

    def test_no_rate_limit_on_auth_error(self) -> None:
        """Auth errors should not be flagged as rate limited."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"error": {"message": "API key not valid"}}',
            stderr="",
            exit_code=1,
        )
        assert not result.rate_limited, "Auth errors should not trigger rate limit"


# ---------------------------------------------------------------------------
# Error Classification Tests
# ---------------------------------------------------------------------------


class TestGeminiErrorClassification:
    """Verify error classification with gemini-cli's patterns."""

    def test_auth_error_classified(self) -> None:
        """PERMISSION_DENIED classified as auth error."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"error": {"type": "PERMISSION_DENIED", "message": "Invalid credentials"}}',
            stderr="",
            exit_code=1,
        )
        assert result.error_type == "auth", "PERMISSION_DENIED should classify as auth"

    def test_api_key_error_classified(self) -> None:
        """'API key' in error classified as auth error."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"error": {"message": "API key not valid. Please check your API key."}}',
            stderr="",
            exit_code=1,
        )
        assert result.error_type == "auth", "'API key' error should classify as auth"

    def test_capacity_503_classified(self) -> None:
        """503 error classified as capacity."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: 503 Service Temporarily Unavailable",
            exit_code=1,
        )
        assert result.error_type == "capacity", "503 should classify as capacity"

    def test_unavailable_classified(self) -> None:
        """UNAVAILABLE classified as capacity."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout=(
                '{"error": {"type": "UNAVAILABLE", '
                '"message": "The service is currently unavailable"}}'
            ),
            stderr="",
            exit_code=1,
        )
        assert result.error_type == "capacity", "UNAVAILABLE should classify as capacity"

    def test_timeout_classified(self) -> None:
        """DEADLINE_EXCEEDED classified as timeout."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"error": {"type": "DEADLINE_EXCEEDED", "message": "Operation timed out"}}',
            stderr="",
            exit_code=1,
        )
        assert result.error_type == "timeout", "DEADLINE_EXCEEDED should classify as timeout"

    def test_timed_out_classified(self) -> None:
        """'timed out' phrase classified as timeout."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: Request timed out after 300s",
            exit_code=1,
        )
        assert result.error_type == "timeout", "'timed out' should classify as timeout"

    def test_no_error_on_success(self) -> None:
        """Successful output should have no error type."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"response": "All good"}',
            stderr="",
            exit_code=0,
        )
        assert result.error_type is None, "Success should have no error type"

    def test_unknown_error_returns_none(self) -> None:
        """Unrecognized error returns None error_type."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: Something weird happened",
            exit_code=1,
        )
        assert result.error_type is None, "Unknown errors should return None"


# ---------------------------------------------------------------------------
# Combined Rate Limit + Classification Tests
# ---------------------------------------------------------------------------


class TestGeminiRateLimitWithClassification:
    """Verify that rate limiting and error classification interact correctly."""

    def test_rate_limit_and_capacity_can_coexist(self) -> None:
        """RESOURCE_EXHAUSTED triggers rate limit; capacity also applicable."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: RESOURCE_EXHAUSTED",
            exit_code=1,
        )
        assert result.rate_limited, "Should be rate limited"
        # error_type is independent — RESOURCE_EXHAUSTED doesn't match any
        # error classification pattern (it's only in rate_limit_patterns)
        # unless it also matches another pattern group

    def test_rate_limit_success_field_false(self) -> None:
        """Rate-limited responses should be marked as not successful."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout="",
            stderr="Error: RESOURCE_EXHAUSTED",
            exit_code=1,
        )
        assert not result.success, "Rate limited should not be successful"

    def test_json_error_extraction_on_rate_limit(self) -> None:
        """Error message is extracted from JSON even when rate limited."""
        backend = _make_backend()
        result = backend._parse_output(
            stdout='{"error": {"message": "Quota exceeded for model gemini-2.5-pro"}}',
            stderr="Quota exceeded for model gemini-2.5-pro",
            exit_code=1,
        )
        assert result.rate_limited, "Should detect rate limit from 'quota exceeded'"
        assert result.error_message is not None, "Should extract error message from JSON"
        assert "Quota exceeded" in result.error_message
