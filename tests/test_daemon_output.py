"""Tests for marianne.daemon.output module."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marianne.daemon.output import (
    ConsoleOutput,
    NullOutput,
    OutputProtocol,
    StructuredOutput,
)


class TestOutputProtocolAbstract:
    """Tests for OutputProtocol ABC — cannot be instantiated directly."""

    def test_cannot_instantiate_directly(self):
        """Test that OutputProtocol raises TypeError when instantiated."""
        with pytest.raises(TypeError):
            OutputProtocol()  # type: ignore[abstract]

    def test_has_required_methods(self):
        """Test that OutputProtocol defines the expected abstract methods."""
        abstract_methods = OutputProtocol.__abstractmethods__
        assert "log" in abstract_methods
        assert "progress" in abstract_methods
        assert "sheet_event" in abstract_methods
        assert "job_event" in abstract_methods

    def test_subclass_must_implement_all_methods(self):
        """Test that partial implementations cannot be instantiated."""

        class PartialOutput(OutputProtocol):
            def log(self, level: str, message: str, **context: Any) -> None:
                pass

        with pytest.raises(TypeError):
            PartialOutput()  # type: ignore[abstract]


class TestNullOutput:
    """Tests for NullOutput — no-op implementation for testing."""

    def test_instantiation(self):
        """Test NullOutput can be created."""
        output = NullOutput()
        assert isinstance(output, OutputProtocol)

    def test_log_does_not_raise(self):
        """Test log() silently does nothing."""
        output = NullOutput()
        output.log("info", "test message")
        output.log("error", "failure", detail="something", code=42)

    def test_progress_does_not_raise(self):
        """Test progress() silently does nothing."""
        output = NullOutput()
        output.progress("job-1", 5, 10)
        output.progress("job-1", 5, 10, eta_seconds=30.0)

    def test_sheet_event_does_not_raise(self):
        """Test sheet_event() silently does nothing."""
        output = NullOutput()
        output.sheet_event("job-1", 3, "started")
        output.sheet_event("job-1", 3, "completed", data={"duration": 10.5})

    def test_job_event_does_not_raise(self):
        """Test job_event() silently does nothing."""
        output = NullOutput()
        output.job_event("job-1", "starting")
        output.job_event("job-1", "failed", data={"error": "timeout"})

    def test_is_subclass_of_protocol(self):
        """Test NullOutput properly implements OutputProtocol."""
        output = NullOutput()
        assert isinstance(output, OutputProtocol)


class TestStructuredOutput:
    """Tests for StructuredOutput — structlog-based daemon output."""

    @patch("marianne.core.logging.get_logger")
    def test_instantiation_gets_logger(self, mock_get_logger: MagicMock):
        """Test StructuredOutput creates a structlog logger on init."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        mock_get_logger.assert_called_with("daemon.output")
        assert output._logger is mock_logger

    @patch("marianne.core.logging.get_logger")
    def test_log_delegates_to_structlog(self, mock_get_logger: MagicMock):
        """Test log() calls the appropriate structlog level method."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.log("info", "test message", key="value")
        mock_logger.info.assert_called_once_with("test message", key="value")

    @patch("marianne.core.logging.get_logger")
    def test_log_warning_level(self, mock_get_logger: MagicMock):
        """Test log() with warning level."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.log("warning", "something bad")
        mock_logger.warning.assert_called_once_with("something bad")

    @patch("marianne.core.logging.get_logger")
    def test_log_error_level(self, mock_get_logger: MagicMock):
        """Test log() with error level."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.log("error", "failure occurred", code=500)
        mock_logger.error.assert_called_once_with("failure occurred", code=500)

    @patch("marianne.core.logging.get_logger")
    def test_log_unknown_level_falls_back_to_info(self, mock_get_logger: MagicMock):
        """Test log() with unknown level falls back to info.

        Uses spec=structlog.stdlib.BoundLogger so unknown attributes
        (e.g. 'exotic_level') raise AttributeError, triggering the
        getattr fallback to self._logger.info.
        """
        import structlog

        mock_logger = MagicMock(spec=structlog.stdlib.BoundLogger)
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.log("exotic_level", "fallback test")
        mock_logger.info.assert_called_with("fallback test")

    @patch("marianne.core.logging.get_logger")
    def test_progress_logs_structured_event(self, mock_get_logger: MagicMock):
        """Test progress() logs a structured job.progress event."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.progress("job-1", 5, 10, eta_seconds=30.0)
        mock_logger.info.assert_called_once_with(
            "job.progress",
            job_id="job-1",
            completed=5,
            total=10,
            eta_seconds=30.0,
        )

    @patch("marianne.core.logging.get_logger")
    def test_progress_without_eta(self, mock_get_logger: MagicMock):
        """Test progress() works without ETA."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.progress("job-1", 3, 8)
        mock_logger.info.assert_called_once_with(
            "job.progress",
            job_id="job-1",
            completed=3,
            total=8,
            eta_seconds=None,
        )

    @patch("marianne.core.logging.get_logger")
    def test_sheet_event_logs_structured(self, mock_get_logger: MagicMock):
        """Test sheet_event() logs a structured sheet.* event."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.sheet_event("job-1", 3, "started", data={"attempt": 1})
        mock_logger.info.assert_called_once_with(
            "sheet.started",
            job_id="job-1",
            sheet_num=3,
            attempt=1,
        )

    @patch("marianne.core.logging.get_logger")
    def test_sheet_event_without_data(self, mock_get_logger: MagicMock):
        """Test sheet_event() works without data dict."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.sheet_event("job-1", 1, "completed")
        mock_logger.info.assert_called_once_with(
            "sheet.completed",
            job_id="job-1",
            sheet_num=1,
        )

    @patch("marianne.core.logging.get_logger")
    def test_job_event_logs_structured(self, mock_get_logger: MagicMock):
        """Test job_event() logs a structured job.* event."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.job_event("job-1", "starting", data={"total_sheets": 10})
        mock_logger.info.assert_called_once_with(
            "job.starting",
            job_id="job-1",
            total_sheets=10,
        )

    @patch("marianne.core.logging.get_logger")
    def test_job_event_without_data(self, mock_get_logger: MagicMock):
        """Test job_event() works without data dict."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        output = StructuredOutput()
        output.job_event("job-1", "paused")
        mock_logger.info.assert_called_once_with(
            "job.paused",
            job_id="job-1",
        )

    def test_is_subclass_of_protocol(self):
        """Test StructuredOutput implements OutputProtocol."""
        output = StructuredOutput()
        assert isinstance(output, OutputProtocol)


class TestConsoleOutput:
    """Tests for ConsoleOutput — Rich Console wrapper."""

    def test_instantiation_creates_console(self):
        """Test ConsoleOutput creates a Rich Console if none provided."""
        output = ConsoleOutput()
        assert output._console is not None

    def test_instantiation_with_custom_console(self):
        """Test ConsoleOutput accepts a custom console."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)
        assert output._console is mock_console

    def test_log_info_prints(self):
        """Test log() with info level prints plain text."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("info", "hello world")
        mock_console.print.assert_called_once_with("hello world")

    def test_log_error_prints_with_red_style(self):
        """Test log() with error level prints in red."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("error", "something broke")
        mock_console.print.assert_called_once_with(
            "[red]something broke[/red]"
        )

    def test_log_warning_prints_with_yellow_style(self):
        """Test log() with warning level prints in yellow."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("warning", "careful now")
        mock_console.print.assert_called_once_with(
            "[yellow]careful now[/yellow]"
        )

    def test_log_debug_prints_with_dim_style(self):
        """Test log() with debug level prints dimmed."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("debug", "trace info")
        mock_console.print.assert_called_once_with(
            "[dim]trace info[/dim]"
        )

    def test_log_critical_prints_with_bold_red_style(self):
        """Test log() with critical level prints in bold red."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("critical", "system down")
        mock_console.print.assert_called_once_with(
            "[bold red]system down[/bold red]"
        )

    def test_log_with_context(self):
        """Test log() appends context key=value pairs."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("info", "message", key="val", count=42)
        mock_console.print.assert_called_once_with("message key=val count=42")

    def test_log_unknown_level_no_style(self):
        """Test log() with unknown level applies no style."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.log("trace", "low level")
        mock_console.print.assert_called_once_with("low level")

    def test_progress_prints_job_progress(self):
        """Test progress() prints formatted progress."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.progress("job-1", 5, 10)
        mock_console.print.assert_called_once_with(
            "[cyan]job-1[/cyan] 5/10"
        )

    def test_progress_with_eta(self):
        """Test progress() includes ETA when provided."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.progress("job-1", 5, 10, eta_seconds=120.0)
        mock_console.print.assert_called_once_with(
            "[cyan]job-1[/cyan] 5/10 (ETA: 120s)"
        )

    def test_sheet_event_prints_formatted(self):
        """Test sheet_event() prints formatted sheet event."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.sheet_event("job-1", 3, "started")
        mock_console.print.assert_called_once_with(
            "[cyan]job-1[/cyan] sheet 3: [bold]started[/bold]"
        )

    def test_sheet_event_with_data(self):
        """Test sheet_event() includes data key=value pairs."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.sheet_event("job-1", 3, "completed", data={"duration": 10.5})
        mock_console.print.assert_called_once_with(
            "[cyan]job-1[/cyan] sheet 3: [bold]completed[/bold] duration=10.5"
        )

    def test_job_event_prints_formatted(self):
        """Test job_event() prints formatted job event."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.job_event("job-1", "starting")
        mock_console.print.assert_called_once_with(
            "[cyan]job-1[/cyan] [bold]starting[/bold]"
        )

    def test_job_event_with_data(self):
        """Test job_event() includes data key=value pairs."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)

        output.job_event("job-1", "failed", data={"error": "timeout"})
        mock_console.print.assert_called_once_with(
            "[cyan]job-1[/cyan] [bold]failed[/bold] error=timeout"
        )

    def test_is_subclass_of_protocol(self):
        """Test ConsoleOutput implements OutputProtocol."""
        mock_console = MagicMock()
        output = ConsoleOutput(console=mock_console)
        assert isinstance(output, OutputProtocol)
