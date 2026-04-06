"""TDD tests for rate limit time-remaining UX (F-110).

Tests that the CLI surfaces rate limit time-remaining information to users:
- In rejection messages when backpressure rejects a submission
- In `mozart status` output when rate limits are active
- In formatting helpers that translate seconds into human-readable durations

Red first, then green.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.cli.output import format_rate_limit_info


# =============================================================================
# format_rate_limit_info — pure formatting function
# =============================================================================


class TestFormatRateLimitInfo:
    """Test the rate limit formatting helper."""

    def test_empty_limits_returns_empty(self) -> None:
        """No active limits → no output."""
        assert format_rate_limit_info({}) == []

    def test_single_limit_formats_instrument_and_time(self) -> None:
        """One active limit → one formatted line with instrument and time."""
        result = format_rate_limit_info({"claude-cli": {"seconds_remaining": 150.0}})
        assert len(result) == 1
        assert "claude-cli" in result[0]
        assert "2m" in result[0]
        assert "30s" in result[0]

    def test_multiple_limits_one_line_each(self) -> None:
        """Multiple limits → one line per instrument."""
        limits = {
            "claude-cli": {"seconds_remaining": 60.0},
            "gemini-cli": {"seconds_remaining": 300.0},
        }
        result = format_rate_limit_info(limits)
        assert len(result) == 2
        instruments = " ".join(result)
        assert "claude-cli" in instruments
        assert "gemini-cli" in instruments

    def test_seconds_only_no_minutes(self) -> None:
        """< 60s → shows only seconds."""
        result = format_rate_limit_info({"claude-cli": {"seconds_remaining": 45.0}})
        assert len(result) == 1
        assert "45s" in result[0]
        # Should not contain "0m"
        assert "0m" not in result[0]

    def test_minutes_only_no_seconds(self) -> None:
        """Exact minutes → shows only minutes."""
        result = format_rate_limit_info({"claude-cli": {"seconds_remaining": 120.0}})
        assert len(result) == 1
        assert "2m" in result[0]
        # Should not contain "0s"
        assert "0s" not in result[0]

    def test_zero_seconds_returns_empty(self) -> None:
        """Zero remaining → treated as no active limit."""
        result = format_rate_limit_info({"claude-cli": {"seconds_remaining": 0.0}})
        assert result == []

    def test_negative_seconds_returns_empty(self) -> None:
        """Negative remaining → expired, treated as no active limit."""
        result = format_rate_limit_info({"claude-cli": {"seconds_remaining": -5.0}})
        assert result == []

    def test_large_duration_shows_hours(self) -> None:
        """3600+ seconds → includes hours."""
        result = format_rate_limit_info({"claude-cli": {"seconds_remaining": 3661.0}})
        assert len(result) == 1
        assert "1h" in result[0]


# =============================================================================
# query_rate_limits — IPC helper
# =============================================================================


class TestQueryRateLimits:
    """Test the rate limit IPC query helper."""

    @pytest.mark.asyncio
    async def test_returns_backend_limits(self) -> None:
        """Successful IPC call returns rate limit data."""
        from marianne.cli.helpers import query_rate_limits

        mock_result = {
            "backends": {"claude-cli": {"seconds_remaining": 120.0}},
            "active_limits": 1,
        }
        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(True, mock_result),
        ):
            result = await query_rate_limits()
        assert result is not None
        assert "claude-cli" in result

    @pytest.mark.asyncio
    async def test_returns_none_when_daemon_unavailable(self) -> None:
        """Daemon not running → returns None."""
        from marianne.cli.helpers import query_rate_limits

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            return_value=(False, None),
        ):
            result = await query_rate_limits()
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self) -> None:
        """Connection error → returns None gracefully."""
        from marianne.cli.helpers import query_rate_limits

        with patch(
            "marianne.daemon.detect.try_daemon_route",
            new_callable=AsyncMock,
            side_effect=ConnectionError("socket gone"),
        ):
            result = await query_rate_limits()
        assert result is None


# =============================================================================
# run.py rejection path — rate limit info in rejection message
# =============================================================================


class TestRunRejectionRateLimitInfo:
    """Test that run.py shows rate limit info on rejection."""

    @pytest.mark.asyncio
    async def test_rejection_shows_rate_limit_when_pressure(self) -> None:
        """When submission is rejected due to high pressure, show rate limits."""
        from marianne.cli.commands.run import _try_daemon_submit

        # Mock try_daemon_route to return rejection
        submit_result = {
            "job_id": "",
            "status": "rejected",
            "message": "System under high pressure — try again later",
        }
        rate_limit_result = {
            "backends": {"claude-cli": {"seconds_remaining": 90.0}},
            "active_limits": 1,
        }

        call_count = 0

        async def mock_route(method: str, params: dict, **kw: object) -> tuple[bool, object]:
            nonlocal call_count
            call_count += 1
            if method == "job.submit":
                return (True, submit_result)
            if method == "daemon.rate_limits":
                return (True, rate_limit_result)
            return (False, None)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=mock_route,
            ),
            patch("marianne.cli.commands.run.output_error") as mock_error,
            patch("marianne.cli.commands.run.console") as mock_console,
            pytest.raises((SystemExit, Exception)),
        ):
            await _try_daemon_submit(
                config_file=MagicMock(spec_set=["resolve"], resolve=MagicMock(return_value="/fake.yaml")),
                workspace=None,
                fresh=False,
                self_healing=False,
                auto_confirm=False,
                json_output=False,
            )

        # Verify rate limits IPC was queried
        assert call_count == 2  # job.submit + daemon.rate_limits

        # Verify console output includes rate limit info
        console_output = " ".join(
            str(c) for c in mock_console.print.call_args_list
        )
        assert "claude-cli" in console_output
        assert "1m" in console_output or "90" in console_output

    @pytest.mark.asyncio
    async def test_rejection_no_rate_limits_no_extra_output(self) -> None:
        """When rejected for non-rate-limit reasons, no rate limit info shown."""
        from marianne.cli.commands.run import _try_daemon_submit

        submit_result = {
            "job_id": "test-job",
            "status": "rejected",
            "message": "Config file not found: /missing.yaml",
        }

        async def mock_route(method: str, params: dict, **kw: object) -> tuple[bool, object]:
            if method == "job.submit":
                return (True, submit_result)
            if method == "daemon.rate_limits":
                return (True, {"backends": {}, "active_limits": 0})
            return (False, None)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=mock_route,
            ),
            patch("marianne.cli.commands.run.output_error"),
            patch("marianne.cli.commands.run.console") as mock_console,
            pytest.raises((SystemExit, Exception)),
        ):
            await _try_daemon_submit(
                config_file=MagicMock(spec_set=["resolve"], resolve=MagicMock(return_value="/fake.yaml")),
                workspace=None,
                fresh=False,
                self_healing=False,
                auto_confirm=False,
                json_output=False,
            )

        # No rate limit lines printed
        console_calls = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "clears in" not in console_calls


# =============================================================================
# status.py — rate limit info in status display
# =============================================================================


class TestStatusRateLimitDisplay:
    """Test that status shows active rate limit time remaining."""

    def test_format_rate_limit_for_status(self) -> None:
        """format_rate_limit_info produces lines suitable for status display."""
        limits = {
            "claude-cli": {"seconds_remaining": 45.0},
            "gemini-cli": {"seconds_remaining": 180.0},
        }
        lines = format_rate_limit_info(limits)
        assert len(lines) == 2
        # Each line should mention the instrument and time
        for line in lines:
            assert "clears in" in line.lower() or "—" in line


# =============================================================================
# Stale state feedback (#139) — stale PID detection
# =============================================================================


class TestStalePidFeedback:
    """Test stale PID detection improvements."""

    def test_stale_pid_detected_when_process_dead(self) -> None:
        """When PID file exists but process is dead, report as stale."""
        from marianne.cli.helpers import check_pid_alive

        # PID that doesn't exist
        with patch("os.kill", side_effect=OSError("No such process")):
            assert check_pid_alive(999999) is False

    def test_live_pid_detected(self) -> None:
        """When PID file points to a running process, report as alive."""
        from marianne.cli.helpers import check_pid_alive

        with patch("os.kill", return_value=None):
            assert check_pid_alive(1234) is True

    def test_permission_error_treated_as_alive(self) -> None:
        """PermissionError on kill -0 means process exists (different user)."""
        from marianne.cli.helpers import check_pid_alive

        with patch("os.kill", side_effect=PermissionError("Operation not permitted")):
            assert check_pid_alive(1234) is True


class TestFreshRunFeedback:
    """Test that --fresh runs provide clear feedback about cleared state."""

    @pytest.mark.asyncio
    async def test_fresh_rejection_suggests_clear(self) -> None:
        """When --fresh fails due to stale registry, hint about clearing."""
        from marianne.cli.commands.run import _try_daemon_submit

        submit_result = {
            "job_id": "my-score",
            "status": "rejected",
            "message": "Job 'my-score' is already running.",
        }

        async def mock_route(method: str, params: dict, **kw: object) -> tuple[bool, object]:
            if method == "job.submit":
                return (True, submit_result)
            if method == "daemon.rate_limits":
                return (True, {"backends": {}, "active_limits": 0})
            return (False, None)

        with (
            patch(
                "marianne.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "marianne.daemon.detect.try_daemon_route",
                side_effect=mock_route,
            ),
            patch("marianne.cli.commands.run.output_error") as mock_error,
            patch("marianne.cli.commands.run.console"),
            pytest.raises((SystemExit, Exception)),
        ):
            await _try_daemon_submit(
                config_file=MagicMock(spec_set=["resolve"], resolve=MagicMock(return_value="/fake.yaml")),
                workspace=None,
                fresh=True,
                self_healing=False,
                auto_confirm=False,
                json_output=False,
            )

        # Verify error message includes the rejection
        mock_error.assert_called_once()
        call_args = mock_error.call_args
        msg = call_args[0][0] if call_args[0] else call_args.kwargs.get("message", "")
        assert "already running" in msg.lower()
        # Verify hints include clearing suggestion
        hints = call_args.kwargs.get("hints", call_args[1].get("hints", []) if len(call_args) > 1 else [])
        hint_text = " ".join(hints)
        assert "clear" in hint_text.lower() or "cancel" in hint_text.lower()
