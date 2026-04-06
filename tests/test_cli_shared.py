"""Tests for mozart.cli.commands._shared module.

Covers create_backend, setup_learning, setup_notifications, setup_escalation,
setup_grounding, setup_all, SetupComponents, display_run_summary,
create_progress_bar, and handle_job_completion.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from marianne.core.checkpoint import JobStatus
from marianne.execution.runner.models import RunSummary


# ─── Helpers ─────────────────────────────────────────────────────────


def _make_mock_config() -> MagicMock:
    """Create a mock JobConfig."""
    config = MagicMock()
    config.backend.type = "claude_cli"
    config.backend.recursive_light.endpoint = "http://localhost:8000"
    config.backend.model = "claude-3"
    config.backend.skip_permissions = True
    config.backend.disable_mcp = True
    config.backend.output_format = "text"
    config.backend.cli_model = None
    config.backend.allowed_tools = None
    config.backend.system_prompt_file = None
    config.backend.working_directory = None
    config.backend.timeout_seconds = 1800.0
    config.backend.cli_extra_args = []
    config.learning.escalation_enabled = False
    config.learning.min_confidence_threshold = 0.7
    config.get_outcome_store_path.return_value = Path("/tmp/outcomes.db")
    return config


def _make_run_summary(**kwargs) -> RunSummary:
    """Create a RunSummary with sensible defaults."""
    defaults = {
        "job_id": "test-job",
        "job_name": "Test Job",
        "total_sheets": 5,
        "completed_sheets": 5,
        "failed_sheets": 0,
        "skipped_sheets": 0,
        "total_duration_seconds": 120.0,
        "total_retries": 0,
        "rate_limit_waits": 0,
        "validation_pass_count": 5,
        "validation_fail_count": 0,
        "successes_without_retry": 5,
        "final_status": JobStatus.COMPLETED,
    }
    defaults.update(kwargs)
    return RunSummary(**defaults)


# ─── create_backend ──────────────────────────────────────────────────


class TestCreateBackend:
    """Tests for create_backend()."""

    def test_delegates_to_setup(self):
        from marianne.cli.commands._shared import create_backend

        config = _make_mock_config()
        mock_backend = MagicMock()
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            with patch(
                "marianne.execution.setup.create_backend", return_value=mock_backend,
            ):
                result = create_backend(config)
        assert result is mock_backend

    def test_verbose_recursive_light(self):
        from marianne.cli.commands._shared import create_backend

        config = _make_mock_config()
        config.backend.type = "recursive_light"
        mock_backend = MagicMock()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.create_backend", return_value=mock_backend,
            ):
                create_backend(config, console=console)
        console.print.assert_called_once()
        assert "Recursive Light" in console.print.call_args[0][0]

    def test_verbose_anthropic_api(self):
        from marianne.cli.commands._shared import create_backend

        config = _make_mock_config()
        config.backend.type = "anthropic_api"
        mock_backend = MagicMock()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.create_backend", return_value=mock_backend,
            ):
                create_backend(config, console=console)
        console.print.assert_called_once()
        assert "Anthropic API" in console.print.call_args[0][0]

    def test_quiet_suppresses_verbose(self):
        from marianne.cli.commands._shared import create_backend

        config = _make_mock_config()
        config.backend.type = "anthropic_api"
        mock_backend = MagicMock()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.create_backend", return_value=mock_backend,
            ):
                create_backend(config, quiet=True, console=console)
        console.print.assert_not_called()


# ─── setup_learning ──────────────────────────────────────────────────


class TestSetupLearning:
    """Tests for setup_learning()."""

    def test_returns_stores(self):
        from marianne.cli.commands._shared import setup_learning

        config = _make_mock_config()
        mock_outcome = MagicMock()
        mock_global = MagicMock()
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            with patch(
                "marianne.execution.setup.setup_learning",
                return_value=(mock_outcome, mock_global),
            ):
                outcome, global_store = setup_learning(config)
        assert outcome is mock_outcome
        assert global_store is mock_global

    def test_verbose_output_when_enabled(self):
        from marianne.cli.commands._shared import setup_learning

        config = _make_mock_config()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.setup_learning",
                return_value=(MagicMock(), MagicMock()),
            ):
                setup_learning(config, console=console)
        assert console.print.call_count == 2

    def test_none_stores_no_verbose(self):
        from marianne.cli.commands._shared import setup_learning

        config = _make_mock_config()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.setup_learning",
                return_value=(None, None),
            ):
                setup_learning(config, console=console)
        console.print.assert_not_called()


# ─── setup_notifications ─────────────────────────────────────────────


class TestSetupNotifications:
    """Tests for setup_notifications()."""

    def test_returns_manager(self):
        from marianne.cli.commands._shared import setup_notifications

        config = _make_mock_config()
        mock_mgr = MagicMock()
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            with patch(
                "marianne.execution.setup.setup_notifications",
                return_value=mock_mgr,
            ):
                result = setup_notifications(config)
        assert result is mock_mgr

    def test_returns_none_when_no_config(self):
        from marianne.cli.commands._shared import setup_notifications

        config = _make_mock_config()
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            with patch(
                "marianne.execution.setup.setup_notifications",
                return_value=None,
            ):
                result = setup_notifications(config)
        assert result is None

    def test_verbose_output(self):
        from marianne.cli.commands._shared import setup_notifications

        config = _make_mock_config()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.setup_notifications",
                return_value=MagicMock(),
            ):
                setup_notifications(config, console=console)
        console.print.assert_called_once()
        assert "Notifications enabled" in console.print.call_args[0][0]


# ─── setup_escalation ───────────────────────────────────────────────


class TestSetupEscalation:
    """Tests for setup_escalation()."""

    def test_disabled_returns_none(self):
        from marianne.cli.commands._shared import setup_escalation

        config = _make_mock_config()
        result = setup_escalation(config, enabled=False)
        assert result is None

    def test_enabled_returns_handler(self):
        from marianne.cli.commands._shared import setup_escalation

        config = _make_mock_config()
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            result = setup_escalation(config, enabled=True)
        assert result is not None
        assert config.learning.escalation_enabled is True

    def test_verbose_output(self):
        from marianne.cli.commands._shared import setup_escalation

        config = _make_mock_config()
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            result = setup_escalation(config, enabled=True, console=console)
        assert result is not None
        console.print.assert_called_once()
        assert "Escalation enabled" in console.print.call_args[0][0]


# ─── setup_grounding ────────────────────────────────────────────────


class TestSetupGrounding:
    """Tests for setup_grounding()."""

    def test_returns_engine(self):
        from marianne.cli.commands._shared import setup_grounding

        config = _make_mock_config()
        mock_engine = MagicMock()
        mock_engine.get_hook_count.return_value = 3
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            with patch(
                "marianne.execution.setup.setup_grounding",
                return_value=mock_engine,
            ):
                result = setup_grounding(config)
        assert result is mock_engine

    def test_returns_none(self):
        from marianne.cli.commands._shared import setup_grounding

        config = _make_mock_config()
        with patch("marianne.cli.commands._shared.is_verbose", return_value=False):
            with patch(
                "marianne.execution.setup.setup_grounding",
                return_value=None,
            ):
                result = setup_grounding(config)
        assert result is None

    def test_verbose_output(self):
        from marianne.cli.commands._shared import setup_grounding

        config = _make_mock_config()
        mock_engine = MagicMock()
        mock_engine.get_hook_count.return_value = 2
        console = MagicMock(spec=Console)
        with patch("marianne.cli.commands._shared.is_verbose", return_value=True):
            with patch(
                "marianne.execution.setup.setup_grounding",
                return_value=mock_engine,
            ):
                setup_grounding(config, console=console)
        console.print.assert_called_once()
        assert "2 hook(s)" in console.print.call_args[0][0]


# ─── SetupComponents ────────────────────────────────────────────────


class TestSetupComponents:
    """Tests for SetupComponents dataclass."""

    def test_creation(self):
        from marianne.cli.commands._shared import SetupComponents

        components = SetupComponents(
            backend=MagicMock(),
            outcome_store=None,
            global_learning_store=None,
            notification_manager=None,
            escalation_handler=None,
            grounding_engine=None,
        )
        assert components.backend is not None
        assert components.outcome_store is None


# ─── setup_all ───────────────────────────────────────────────────────


class TestSetupAll:
    """Tests for setup_all()."""

    def test_returns_all_components(self):
        from marianne.cli.commands._shared import setup_all

        config = _make_mock_config()
        mock_backend = MagicMock()
        with patch("marianne.cli.commands._shared.create_backend", return_value=mock_backend), \
             patch("marianne.cli.commands._shared.setup_learning", return_value=(None, None)), \
             patch("marianne.cli.commands._shared.setup_notifications", return_value=None), \
             patch("marianne.cli.commands._shared.setup_escalation", return_value=None), \
             patch("marianne.cli.commands._shared.setup_grounding", return_value=None):
            components = setup_all(config)
        assert components.backend is mock_backend
        assert components.escalation_handler is None

    def test_passes_escalation_flag(self):
        from marianne.cli.commands._shared import setup_all

        config = _make_mock_config()
        with patch("marianne.cli.commands._shared.create_backend", return_value=MagicMock()), \
             patch("marianne.cli.commands._shared.setup_learning", return_value=(None, None)), \
             patch("marianne.cli.commands._shared.setup_notifications", return_value=None), \
             patch("marianne.cli.commands._shared.setup_escalation", return_value=MagicMock()) as mock_esc, \
             patch("marianne.cli.commands._shared.setup_grounding", return_value=None):
            setup_all(config, escalation=True)
        mock_esc.assert_called_once()
        assert mock_esc.call_args[1]["enabled"] is True


# ─── display_run_summary ─────────────────────────────────────────────


class TestDisplayRunSummary:
    """Tests for display_run_summary()."""

    def test_quiet_suppresses(self):
        from marianne.cli.commands._shared import display_run_summary

        summary = _make_run_summary()
        with patch("marianne.cli.commands._shared.is_quiet", return_value=True), \
             patch("marianne.cli.commands._shared.default_console") as mock_console:
            display_run_summary(summary)
        mock_console.print.assert_not_called()

    def test_completed_shows_green(self):
        from marianne.cli.commands._shared import display_run_summary

        summary = _make_run_summary(final_status=JobStatus.COMPLETED)
        with patch("marianne.cli.commands._shared.is_quiet", return_value=False), \
             patch("marianne.cli.commands._shared.is_verbose", return_value=False), \
             patch("marianne.cli.commands._shared.default_console") as mock_console:
            display_run_summary(summary)
        mock_console.print.assert_called_once()

    def test_failed_shows_red(self):
        from marianne.cli.commands._shared import display_run_summary

        summary = _make_run_summary(
            final_status=JobStatus.FAILED,
            completed_sheets=3,
            failed_sheets=2,
        )
        with patch("marianne.cli.commands._shared.is_quiet", return_value=False), \
             patch("marianne.cli.commands._shared.is_verbose", return_value=False), \
             patch("marianne.cli.commands._shared.default_console") as mock_console:
            display_run_summary(summary)
        mock_console.print.assert_called_once()

    def test_retries_shown(self):
        from marianne.cli.commands._shared import display_run_summary

        summary = _make_run_summary(total_retries=3)
        with patch("marianne.cli.commands._shared.is_quiet", return_value=False), \
             patch("marianne.cli.commands._shared.is_verbose", return_value=False), \
             patch("marianne.cli.commands._shared.default_console") as mock_console:
            display_run_summary(summary)
        mock_console.print.assert_called_once()

    def test_rate_limit_waits_shown(self):
        from marianne.cli.commands._shared import display_run_summary

        summary = _make_run_summary(rate_limit_waits=2)
        with patch("marianne.cli.commands._shared.is_quiet", return_value=False), \
             patch("marianne.cli.commands._shared.is_verbose", return_value=False), \
             patch("marianne.cli.commands._shared.default_console") as mock_console:
            display_run_summary(summary)
        mock_console.print.assert_called_once()


# ─── create_progress_bar ─────────────────────────────────────────────


class TestCreateProgressBar:
    """Tests for create_progress_bar()."""

    def test_default_progress_bar(self):
        from marianne.cli.commands._shared import create_progress_bar

        console = Console(force_terminal=True)
        progress = create_progress_bar(console=console)
        assert progress is not None

    def test_with_exec_status(self):
        from marianne.cli.commands._shared import create_progress_bar

        console = Console(force_terminal=True)
        progress = create_progress_bar(console=console, include_exec_status=True)
        assert progress is not None

    def test_without_exec_status(self):
        from marianne.cli.commands._shared import create_progress_bar

        console = Console(force_terminal=True)
        progress = create_progress_bar(console=console, include_exec_status=False)
        assert progress is not None


# ─── handle_job_completion ───────────────────────────────────────────


class TestHandleJobCompletion:
    """Tests for handle_job_completion()."""

    @pytest.mark.asyncio
    async def test_completed_sends_notification(self):
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock()
        state.status = JobStatus.COMPLETED
        summary = _make_run_summary()
        notification_mgr = MagicMock()
        notification_mgr.notify_job_complete = AsyncMock()

        with patch("marianne.cli.commands._shared.is_quiet", return_value=True):
            with patch("marianne.cli.commands._shared.display_run_summary"):
                await handle_job_completion(
                    state=state,
                    summary=summary,
                    notification_manager=notification_mgr,
                    job_id="test-job",
                    job_name="Test Job",
                )
        notification_mgr.notify_job_complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_completed_no_notification_manager(self):
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock()
        state.status = JobStatus.COMPLETED
        summary = _make_run_summary()

        with patch("marianne.cli.commands._shared.is_quiet", return_value=True):
            with patch("marianne.cli.commands._shared.display_run_summary"):
                await handle_job_completion(
                    state=state,
                    summary=summary,
                    notification_manager=None,
                    job_id="test-job",
                    job_name="Test Job",
                )

    @pytest.mark.asyncio
    async def test_failed_sends_failure_notification(self):
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock()
        state.status = JobStatus.FAILED
        state.current_sheet = 3
        summary = _make_run_summary(
            final_status=JobStatus.FAILED,
            completed_sheets=2,
            failed_sheets=3,
        )
        notification_mgr = MagicMock()
        notification_mgr.notify_job_failed = AsyncMock()

        with patch("marianne.cli.commands._shared.is_quiet", return_value=True):
            with patch("marianne.cli.commands._shared.display_run_summary"):
                await handle_job_completion(
                    state=state,
                    summary=summary,
                    notification_manager=notification_mgr,
                    job_id="test-job",
                    job_name="Test Job",
                )
        notification_mgr.notify_job_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_paused_no_failure_notification(self):
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock()
        state.status = JobStatus.PAUSED
        summary = _make_run_summary(
            final_status=JobStatus.PAUSED,
            completed_sheets=2,
        )
        notification_mgr = MagicMock()
        notification_mgr.notify_job_failed = AsyncMock()

        with patch("marianne.cli.commands._shared.is_quiet", return_value=True):
            with patch("marianne.cli.commands._shared.display_run_summary"):
                await handle_job_completion(
                    state=state,
                    summary=summary,
                    notification_manager=notification_mgr,
                    job_id="test-job",
                    job_name="Test Job",
                )
        notification_mgr.notify_job_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_completed_shows_status(self):
        from marianne.cli.commands._shared import handle_job_completion

        state = MagicMock()
        state.status = JobStatus.FAILED
        summary = _make_run_summary(
            final_status=JobStatus.FAILED,
            completed_sheets=3,
            failed_sheets=2,
        )
        console = MagicMock(spec=Console)

        with patch("marianne.cli.commands._shared.is_quiet", return_value=False):
            with patch("marianne.cli.commands._shared.display_run_summary"):
                await handle_job_completion(
                    state=state,
                    summary=summary,
                    notification_manager=None,
                    job_id="test-job",
                    job_name="Test Job",
                    console=console,
                )
        console.print.assert_called_once()
        assert "FAILED" in console.print.call_args[0][0].upper()
