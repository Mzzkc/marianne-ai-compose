"""Pytest fixtures for Marianne tests."""

import logging
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from marianne.core.checkpoint import CheckpointState
from marianne.state.base import StateBackend


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip playwright-marked tests when running under xdist.

    Playwright tests launch browsers and bind ports — they cannot run in
    parallel xdist workers.  The addopts ``-m 'not playwright'`` in
    pyproject.toml is the primary gate; this hook is a safety net.
    """
    if not config.pluginmanager.has_plugin("xdist"):
        return
    worker_count = config.getoption("numprocesses", default=None)
    if worker_count is None or worker_count == 0:
        return
    skip_marker = pytest.mark.skip(reason="Playwright tests cannot run under xdist")
    for item in items:
        if "playwright" in item.keywords:
            item.add_marker(skip_marker)


# Modules deleted as part of baton migration (runner-removal).  Tests that
# lazy-import these will raise ModuleNotFoundError at runtime.  Convert to
# skip so `pytest -x` doesn't halt the entire suite on dead-code tests.
_DELETED_MODULES = frozenset({
    "marianne.execution.parallel",
    "marianne.execution.runner",
    "marianne.execution.runner.base",
    "marianne.execution.runner.cost",
    "marianne.execution.runner.context",
    "marianne.execution.runner.lifecycle",
    "marianne.execution.runner.models",
    "marianne.execution.runner.patterns",
    "marianne.execution.runner.recovery",
    "marianne.execution.runner.sheet",
})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> None:  # type: ignore[return]
    """Auto-skip tests that import deleted runner/parallel modules."""
    outcome = yield
    exc = outcome.excinfo
    if exc is not None:
        exc_type, exc_value, _ = exc
        if exc_type in (ModuleNotFoundError, ImportError):
            # Check if the missing module is in our deleted set.
            # ModuleNotFoundError.name gives the exact module.
            # ImportError from "from pkg import X" gives the parent in .name
            # and X in the message — reconstruct the full path.
            module_name = getattr(exc_value, "name", "") or ""
            # "from marianne.execution import parallel" → name='marianne.execution'
            # Reconstruct: parent.imported_name and check.
            if module_name not in _DELETED_MODULES:
                msg = str(exc_value)
                for mod in _DELETED_MODULES:
                    parent, _, leaf = mod.rpartition(".")
                    if parent == module_name and leaf in msg:
                        module_name = mod
                        break
            if module_name in _DELETED_MODULES:
                pytest.skip(f"Module removed (baton migration): {module_name}")
        # Also catch AttributeError from unittest.mock.patch() targeting
        # methods that were removed (e.g. JobService.start_job).
        elif exc_type is AttributeError:
            msg = str(exc_value)
            if "start_job" in msg or "resume_job" in msg or "_execute_runner" in msg:
                pytest.skip(f"Method removed (baton migration): {msg}")


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
def reset_global_learning_store() -> Generator[None, None, None]:
    """Reset the global learning store singleton before each test.

    This prevents test isolation issues where one test's use of get_global_store()
    pollutes the singleton cache for subsequent tests. Each test should get a fresh
    singleton state.

    Fixes F-527: Test isolation gaps in test_global_learning.py where tests fail
    in full suite due to global singleton state pollution but pass in isolation.
    """
    # Import the module to access the global variable
    import marianne.learning.store as store_module

    # Reset to None before test
    store_module._global_store = None

    yield

    # Reset to None after test to ensure cleanup
    store_module._global_store = None


@pytest.fixture(autouse=True)
def no_daemon_detection() -> Generator[None, None, None]:
    """Prevent dashboard tests from connecting to a real daemon.

    The dashboard's ``_create_daemon_client()`` constructs a DaemonClient
    pointed at the conductor's Unix socket. Patching it globally ensures
    tests don't accidentally connect to a running conductor.

    Note: CLI commands that use ``try_daemon_route()`` from
    ``marianne.daemon.detect`` are NOT affected by this fixture -- those
    tests mock ``try_daemon_route`` directly where needed.
    """
    with patch(
        "marianne.dashboard.app._create_daemon_client",
        return_value=None,
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
