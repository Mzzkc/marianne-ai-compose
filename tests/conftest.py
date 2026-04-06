"""Pytest fixtures for Mozart tests."""

import logging
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import structlog

from marianne.core.checkpoint import CheckpointState
from marianne.state.base import StateBackend


class MockStateBackend(StateBackend):
    """In-memory state backend for testing.

    Supports optional error simulation via should_fail_* flags
    for testing error handling paths.
    """

    def __init__(self) -> None:
        self.states: dict[str, CheckpointState] = {}
        self.should_fail_load = False
        self.should_fail_save = False
        self.should_fail_delete = False

    async def load(self, job_id: str) -> CheckpointState | None:
        if self.should_fail_load:
            raise RuntimeError("Database connection failed")
        return self.states.get(job_id)

    async def save(self, state: CheckpointState) -> None:
        if self.should_fail_save:
            raise RuntimeError("Database write failed")
        self.states[state.job_id] = state

    async def delete(self, job_id: str) -> bool:
        if self.should_fail_delete:
            raise RuntimeError("Database delete failed")
        if job_id in self.states:
            del self.states[job_id]
            return True
        return False

    async def list_jobs(self) -> list[CheckpointState]:
        return list(self.states.values())

    async def get_next_sheet(self, job_id: str) -> int | None:
        state = await self.load(job_id)
        return state.get_next_sheet() if state else None

    async def mark_sheet_status(
        self,
        job_id: str,
        sheet_num: int,
        status,
        error_message: str | None = None,
    ) -> None:
        state = await self.load(job_id)
        if state and sheet_num in state.sheets:
            state.sheets[sheet_num].status = status
            if error_message:
                state.sheets[sheet_num].error_message = error_message


@pytest.fixture
def mock_state_backend() -> MockStateBackend:
    """In-memory state backend for testing."""
    return MockStateBackend()


@pytest.fixture(autouse=True)
def reset_output_level() -> Generator[None, None, None]:
    """Reset CLI output level before and after each test.

    Prevents quiet/verbose mode from leaking between tests
    when typer callbacks set module-level state.
    """
    from marianne.cli.helpers import OutputLevel, set_output_level

    set_output_level(OutputLevel.NORMAL)
    yield
    set_output_level(OutputLevel.NORMAL)


@pytest.fixture(autouse=True)
def reset_logging_state() -> Generator[None, None, None]:
    """Reset logging state before and after each test.

    This ensures test isolation for logging configuration.
    """
    # Import helpers module directly for access to internal state
    from marianne.cli import helpers as cli_helpers

    # Store original state from the CliLoggingConfig dataclass
    cfg = cli_helpers._log_config
    original_configured = cfg.configured
    original_log_level = cfg.level
    original_log_file = cfg.file
    original_log_format = cfg.format

    # Reset state before test
    cfg.configured = False
    cfg.level = "INFO"
    cfg.file = None
    cfg.format = "console"

    # Reset structlog to default state
    structlog.reset_defaults()

    # Clear all handlers from root logger
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    for handler in original_handlers:
        root_logger.removeHandler(handler)

    yield

    # Restore state after test
    cfg.configured = original_configured
    cfg.level = original_log_level
    cfg.file = original_log_file
    cfg.format = original_log_format

    # Restore original handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    for handler in original_handlers:
        root_logger.addHandler(handler)


@pytest.fixture(autouse=True)
def no_daemon_detection() -> Generator[None, None, None]:
    """Prevent dashboard tests from connecting to a real daemon.

    The dashboard's JobControlService.is_daemon_available() checks for a
    Unix socket, which succeeds if the conductor happens to be running. Patching
    it globally ensures dashboard API tests always take the subprocess path.

    Note: CLI commands that use ``try_daemon_route()`` from
    ``mozart.daemon.detect`` are NOT affected by this fixture -- those
    tests mock ``try_daemon_route`` directly where needed.
    """
    with patch(
        "marianne.dashboard.services.job_control.JobControlService.is_daemon_available",
        new_callable=AsyncMock,
        return_value=False,
    ):
        yield


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def sample_config_dict() -> dict:
    """Return a sample job configuration dictionary."""
    return {
        "name": "test-job",
        "description": "Test job for unit tests",
        "backend": {
            "type": "claude_cli",
            "skip_permissions": True,
        },
        "sheet": {
            "size": 10,
            "total_items": 30,
        },
        "prompt": {
            "template": "Process sheet {{ sheet_num }} of {{ total_sheets }}.",
        },
        "retry": {
            "max_retries": 2,
        },
        "validations": [
            {
                "type": "file_exists",
                "path": "{workspace}/output-{sheet_num}.txt",
                "description": "Output file exists",
            },
        ],
    }


@pytest.fixture
def sample_yaml_config(tmp_path: Path, sample_config_dict: dict) -> Path:
    """Create a sample YAML config file."""
    import yaml

    config_path = tmp_path / "test-config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(sample_config_dict, f)
    return config_path
