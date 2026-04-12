"""Tests verifying every output_error() call in the CLI includes actionable hints.

Layer 2 of error quality: every error message should tell the user what to do
about it, not just what went wrong. These tests target the 8 remaining hintless
output_error() calls discovered in movement 4.

TDD: Written by Lens, Movement 4.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from typer.testing import CliRunner

from marianne.cli import app

runner = CliRunner()


def _make_config(tmp_path: Path) -> Path:
    """Create a minimal valid config file for testing."""
    config = {
        "name": "test-job",
        "instrument": "claude-code",
        "sheet": {"size": 10, "total_items": 10},
        "prompt": {"template": "test"},
    }
    config_path = tmp_path / "test.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return config_path


def _assert_hints_present(captured_calls: list[dict[str, object]], context: str) -> None:
    """Assert that at least one captured output_error call includes non-empty hints."""
    assert len(captured_calls) > 0, f"output_error not called: {context}"
    call = captured_calls[0]
    assert "hints" in call, f"hints= kwarg missing: {context}"
    hints = call["hints"]
    assert hints is not None, f"hints=None: {context}"
    assert isinstance(hints, list) and len(hints) > 0, f"hints empty: {context}"


def _make_spy() -> tuple[list[dict[str, object]], object]:
    """Create a spy function that captures output_error kwargs."""
    captured: list[dict[str, object]] = []

    def spy(message: str, **kwargs: object) -> None:
        captured.append({"message": message, **kwargs})

    return captured, spy


def _get_module_source(module_path: str) -> str:
    """Get full module source using importlib to avoid function vs module ambiguity."""
    mod = importlib.import_module(module_path)
    return inspect.getsource(mod)


def _assert_output_error_has_hints(source: str, search_string: str, context: str) -> None:
    """Assert that the output_error() call near search_string includes hints=."""
    idx = source.find(search_string)
    assert idx != -1, f"Cannot find '{search_string}' in source: {context}"
    # Look for the enclosing output_error call (search backward and forward)
    call_start = source.rfind("output_error(", max(0, idx - 300), idx + len(search_string))
    if call_start == -1:
        # The output_error might be after the search string
        call_start = source.find("output_error(", idx)
    assert call_start != -1, f"output_error( not found near '{search_string}': {context}"
    # Read the full call (up to 500 chars should be enough)
    snippet = source[call_start:call_start + 500]
    assert "hints=" in snippet, f"output_error lacks hints= near '{search_string}': {context}"


# =============================================================================
# helpers.py:159 — logging configuration error
# =============================================================================


class TestLoggingConfigErrorHints:
    """When logging configuration fails, tell the user what to check."""

    def test_logging_error_includes_hint(self) -> None:
        """output_error for logging config error should include hints."""
        source = _get_module_source("marianne.cli.helpers")
        _assert_output_error_has_hints(
            source, "Logging configuration error", "helpers.py logging error",
        )


# =============================================================================
# run.py:160 — escalation not supported error
# =============================================================================


class TestEscalationNotSupportedHints:
    """When --escalation is used, tell the user why and what to do instead."""

    def test_escalation_error_has_hints(self, tmp_path: Path) -> None:
        """The escalation not-supported error should include hints."""
        config_path = _make_config(tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()

        captured, spy = _make_spy()

        with patch("marianne.cli.commands.run.output_error", side_effect=spy):
            runner.invoke(
                app,
                ["run", str(config_path), "--workspace", str(ws), "--escalation"],
                catch_exceptions=True,
            )

        _assert_hints_present(captured, "run.py escalation error")


# =============================================================================
# pause.py — daemon communication errors (lines 321, 348, 525, 616, 628)
# =============================================================================


class TestPauseDaemonErrorHints:
    """All daemon communication errors in pause commands should have hints."""

    def test_pause_daemon_oserror_has_hints(self) -> None:
        """When pause fails due to conductor communication, hints guide recovery."""
        captured, spy = _make_spy()

        with patch("marianne.daemon.detect.try_daemon_route",
                   new_callable=AsyncMock,
                   side_effect=OSError("Connection refused")), \
             patch("marianne.cli.commands.pause.output_error", side_effect=spy):
            runner.invoke(
                app,
                ["pause", "test-job"],
                catch_exceptions=True,
            )

        _assert_hints_present(captured, "pause.py daemon OSError")

    def test_pause_failed_response_has_hints(self) -> None:
        """When conductor says pause failed, hints explain possible reasons."""
        captured, spy = _make_spy()

        with patch("marianne.daemon.detect.try_daemon_route",
                   new_callable=AsyncMock,
                   return_value=(True, {"paused": False, "error": "Job not found"})), \
             patch("marianne.cli.commands.pause.output_error", side_effect=spy):
            runner.invoke(
                app,
                ["pause", "test-job"],
                catch_exceptions=True,
            )

        _assert_hints_present(captured, "pause.py pause failed")

    def test_pause_status_check_error_has_hints(self) -> None:
        """When pause's internal status-check IPC call fails, hints guide recovery."""
        source = _get_module_source("marianne.cli.commands.pause")
        # The _check_pause_state function makes a try_daemon_route call
        # and has an output_error on failure — verify it has hints=
        func_idx = source.find("_check_pause_state")
        if func_idx != -1:
            func_source = source[func_idx:func_idx + 2000]
            err_idx = func_source.find("output_error(")
            if err_idx != -1:
                snippet = func_source[err_idx:err_idx + 300]
                assert "hints=" in snippet, \
                    "pause.py _check_pause_state output_error lacks hints="

    def test_modify_daemon_error_has_hints(self) -> None:
        """When modify command's IPC call fails, hints guide recovery."""
        source = _get_module_source("marianne.cli.commands.pause")
        _assert_output_error_has_hints(
            source, "job.modify", "pause.py modify daemon error (line 616)",
        )

    def test_modify_rejected_has_hints(self) -> None:
        """When modify is rejected by conductor, hints explain what to try."""
        source = _get_module_source("marianne.cli.commands.pause")
        # Find the rejected handler
        idx = source.find('"rejected"')
        assert idx != -1, "rejected handler not found in pause.py"
        # The next output_error after this should have hints=
        snippet = source[idx:idx + 400]
        err_idx = snippet.find("output_error(")
        assert err_idx != -1, "output_error not found after rejected check"
        err_snippet = snippet[err_idx:err_idx + 300]
        assert "hints=" in err_snippet, \
            "pause.py modify rejected output_error lacks hints= (line 628)"


# =============================================================================
# status.py:310 — conductor watch error
# =============================================================================


class TestStatusWatchErrorHints:
    """The watch-mode conductor error should include recovery hints."""

    def test_watch_conductor_error_has_hints(self) -> None:
        """Verify the watch mode conductor error output_error call includes hints=."""
        source = _get_module_source("marianne.cli.commands.status")
        _assert_output_error_has_hints(
            source, "Conductor error:", "status.py watch error (line 310)",
        )


# =============================================================================
# status.py:1845 — clear command error
# =============================================================================


class TestClearCommandErrorHints:
    """The clear command error should include hints."""

    def test_clear_daemon_error_has_hints(self) -> None:
        """When clear command fails, tell the user what to check."""
        captured, spy = _make_spy()

        with patch("marianne.daemon.detect.try_daemon_route",
                   new_callable=AsyncMock,
                   side_effect=Exception("Connection refused")), \
             patch("marianne.cli.commands.status.output_error", side_effect=spy):
            runner.invoke(
                app,
                ["clear", "--yes"],
                catch_exceptions=True,
            )

        _assert_hints_present(captured, "status.py clear command error (line 1845)")


# =============================================================================
# Bonus: status.py:1819-1822 — raw console.print error in clear validation
# =============================================================================


class TestClearInvalidStatusUsesOutputError:
    """Invalid status in clear should use output_error, not raw console.print."""

    def test_invalid_status_uses_output_error(self) -> None:
        """Verify invalid status validation uses output_error with hints."""
        source = _get_module_source("marianne.cli.commands.status")
        idx = source.find("Invalid status(es):")
        assert idx != -1, "Cannot find invalid status validation"
        # Look backward for the containing call — should be output_error, not console.print
        call_region = source[max(0, idx - 100):idx + 10]
        assert "output_error(" in call_region, \
            "Invalid status validation should use output_error(), not console.print()"
