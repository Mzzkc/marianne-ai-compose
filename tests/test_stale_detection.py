"""Tests for stale execution detection (GH#25).

Tests the StaleDetectionConfig validation and the idle watchdog
that detects hung sheet executions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mozart.backends.base import Backend, ExecutionResult
from mozart.core.config import JobConfig
from mozart.core.config.execution import StaleDetectionConfig
from mozart.execution.runner.sheet import SheetExecutionMixin, _StaleExecutionError


# ---------------------------------------------------------------------------
# StaleDetectionConfig unit tests
# ---------------------------------------------------------------------------


class TestStaleDetectionConfig:
    """Tests for StaleDetectionConfig Pydantic model."""

    def test_defaults_disabled(self) -> None:
        cfg = StaleDetectionConfig()
        assert cfg.enabled is False
        assert cfg.idle_timeout_seconds == 300.0
        assert cfg.check_interval_seconds == 30.0

    def test_enabled_with_custom_values(self) -> None:
        cfg = StaleDetectionConfig(
            enabled=True,
            idle_timeout_seconds=120.0,
            check_interval_seconds=10.0,
        )
        assert cfg.enabled is True
        assert cfg.idle_timeout_seconds == 120.0
        assert cfg.check_interval_seconds == 10.0

    def test_interval_must_be_less_than_timeout(self) -> None:
        with pytest.raises(ValueError, match="check_interval_seconds.*must be less than"):
            StaleDetectionConfig(
                enabled=True,
                idle_timeout_seconds=30.0,
                check_interval_seconds=30.0,
            )

    def test_interval_greater_than_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="check_interval_seconds.*must be less than"):
            StaleDetectionConfig(
                enabled=True,
                idle_timeout_seconds=60.0,
                check_interval_seconds=120.0,
            )

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValueError):
            StaleDetectionConfig(idle_timeout_seconds=0)

    def test_negative_timeout_rejected(self) -> None:
        with pytest.raises(ValueError):
            StaleDetectionConfig(idle_timeout_seconds=-1)

    def test_zero_interval_rejected(self) -> None:
        with pytest.raises(ValueError):
            StaleDetectionConfig(check_interval_seconds=0)

    def test_serialization_round_trip(self) -> None:
        cfg = StaleDetectionConfig(
            enabled=True,
            idle_timeout_seconds=180.0,
            check_interval_seconds=15.0,
        )
        data = cfg.model_dump()
        restored = StaleDetectionConfig.model_validate(data)
        assert restored == cfg


# ---------------------------------------------------------------------------
# JobConfig integration tests
# ---------------------------------------------------------------------------


class TestStaleDetectionInJobConfig:
    """Tests that stale_detection integrates into JobConfig correctly."""

    def test_default_stale_detection_in_job(self) -> None:
        from mozart.core.config import JobConfig

        config = JobConfig(
            name="test-job",
            sheet={"size": 1, "total_items": 3},
            prompt={"template": "test"},
        )
        assert config.stale_detection.enabled is False
        assert config.stale_detection.idle_timeout_seconds == 300.0

    def test_stale_detection_from_yaml(self) -> None:
        from mozart.core.config import JobConfig

        config = JobConfig.from_yaml_string("""
name: test-stale
sheet:
  size: 1
  total_items: 3
prompt:
  template: test
stale_detection:
  enabled: true
  idle_timeout_seconds: 120
  check_interval_seconds: 15
""")
        assert config.stale_detection.enabled is True
        assert config.stale_detection.idle_timeout_seconds == 120.0
        assert config.stale_detection.check_interval_seconds == 15.0

    def test_invalid_stale_detection_in_yaml(self) -> None:
        from mozart.core.config import JobConfig

        with pytest.raises(ValueError):
            JobConfig.from_yaml_string("""
name: test-bad
sheet:
  size: 1
  total_items: 3
prompt:
  template: test
stale_detection:
  enabled: true
  idle_timeout_seconds: 10
  check_interval_seconds: 20
""")


# ---------------------------------------------------------------------------
# Idle watchdog integration tests (uses actual async)
# ---------------------------------------------------------------------------

def _make_success_result() -> ExecutionResult:
    return ExecutionResult(
        success=True, exit_code=0, stdout="done", stderr="",
        duration_seconds=1.0,
    )


def _make_stale_config(
    idle_timeout: float = 0.3,
    check_interval: float = 0.1,
) -> StaleDetectionConfig:
    return StaleDetectionConfig(
        enabled=True,
        idle_timeout_seconds=idle_timeout,
        check_interval_seconds=check_interval,
    )


class _MinimalRunner(SheetExecutionMixin):
    """Minimal mock of the runner for testing _execute_with_stale_detection.

    Inherits from SheetExecutionMixin so that _idle_watchdog and
    _execute_with_stale_detection are bound to self.
    """

    def __init__(
        self,
        stale_config: StaleDetectionConfig | None = None,
    ) -> None:
        from mozart.core.logging import get_logger

        self.config = MagicMock(spec=JobConfig)
        self.config.stale_detection = stale_config or StaleDetectionConfig()
        self.backend = MagicMock(spec=Backend)
        self.backend.execute = AsyncMock(return_value=_make_success_result())
        self._logger = get_logger("test")
        self._current_sheet_num = 1
        self._last_progress_monotonic = time.monotonic()


class TestIdleWatchdog:
    """Tests for the _idle_watchdog and _execute_with_stale_detection methods."""

    @pytest.mark.asyncio
    async def test_fast_execution_completes_normally(self) -> None:
        """When execution completes quickly, no stale detection triggers."""
        runner = _MinimalRunner(stale_config=_make_stale_config())
        runner.backend.execute = AsyncMock(return_value=_make_success_result())

        result = await runner._execute_with_stale_detection(
            "test prompt",
            timeout_seconds=None,
        )
        assert result.success is True
        assert result.stdout == "done"

    @pytest.mark.asyncio
    async def test_disabled_stale_detection_bypasses_watchdog(self) -> None:
        """When stale_detection.enabled=False, direct execution is used."""
        runner = _MinimalRunner()  # disabled by default
        runner.backend.execute = AsyncMock(return_value=_make_success_result())

        result = await runner._execute_with_stale_detection(
            "test prompt",
            timeout_seconds=10.0,
        )
        assert result.success is True
        runner.backend.execute.assert_called_once_with(
            "test prompt", timeout_seconds=10.0,
        )

    @pytest.mark.asyncio
    async def test_stale_execution_detected(self) -> None:
        """When no progress arrives, stale detection triggers."""
        runner = _MinimalRunner(
            stale_config=_make_stale_config(
                idle_timeout=0.2,
                check_interval=0.05,
            ),
        )
        # Simulate a slow/hung execution that takes forever
        async def _slow_execute(*_args: Any, **_kwargs: Any) -> ExecutionResult:
            await asyncio.sleep(10.0)  # Will be cancelled
            return _make_success_result()

        runner.backend.execute = AsyncMock(side_effect=_slow_execute)

        result = await runner._execute_with_stale_detection(
            "test prompt",
            timeout_seconds=None,
        )
        assert result.success is False
        assert result.error_type == "stale"
        assert "no output" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_progress_updates_prevent_stale_detection(self) -> None:
        """Regular progress updates keep the watchdog happy."""
        runner = _MinimalRunner(
            stale_config=_make_stale_config(
                idle_timeout=0.3,
                check_interval=0.05,
            ),
        )

        async def _slow_but_active(*_args: Any, **_kwargs: Any) -> ExecutionResult:
            # Simulates execution that takes 0.5s but sends progress every 0.1s
            for _ in range(5):
                await asyncio.sleep(0.1)
                runner._last_progress_monotonic = time.monotonic()
            return _make_success_result()

        runner.backend.execute = AsyncMock(side_effect=_slow_but_active)

        result = await runner._execute_with_stale_detection(
            "test prompt",
            timeout_seconds=None,
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_stale_detection_returns_proper_error_type(self) -> None:
        """Stale detection returns error_type='stale' and exit_reason='timeout'."""
        runner = _MinimalRunner(
            stale_config=_make_stale_config(
                idle_timeout=0.15,
                check_interval=0.05,
            ),
        )

        async def _hang(*_args: Any, **_kwargs: Any) -> ExecutionResult:
            await asyncio.sleep(10.0)
            return _make_success_result()

        runner.backend.execute = AsyncMock(side_effect=_hang)

        result = await runner._execute_with_stale_detection(
            "test prompt",
            timeout_seconds=None,
        )
        assert result.error_type == "stale"
        assert result.exit_reason == "timeout"
        assert result.exit_code is None
        assert result.success is False

    @pytest.mark.asyncio
    async def test_timeout_seconds_passed_through(self) -> None:
        """timeout_seconds is forwarded to backend.execute."""
        runner = _MinimalRunner(stale_config=_make_stale_config())

        await runner._execute_with_stale_detection(
            "test prompt",
            timeout_seconds=42.0,
        )
        runner.backend.execute.assert_called_once_with(
            "test prompt", timeout_seconds=42.0,
        )


class TestStaleExecutionError:
    """Tests for _StaleExecutionError exception."""

    def test_attributes(self) -> None:
        err = _StaleExecutionError(idle_seconds=120.5, timeout=60.0)
        assert err.idle_seconds == 120.5
        assert err.timeout == 60.0

    def test_message(self) -> None:
        err = _StaleExecutionError(idle_seconds=300.0, timeout=120.0)
        assert "300.0s" in str(err)
        assert "120.0s" in str(err)


class TestIdleWatchdogDirect:
    """Direct tests for _idle_watchdog method."""

    @pytest.mark.asyncio
    async def test_watchdog_returns_when_task_completes(self) -> None:
        """Watchdog exits cleanly when the execution task completes."""
        runner = _MinimalRunner(stale_config=_make_stale_config())

        async def _quick() -> ExecutionResult:
            return _make_success_result()

        task = asyncio.create_task(_quick())
        # Give it a moment to complete
        await asyncio.sleep(0.05)

        # Watchdog should exit without raising
        await runner._idle_watchdog(
            idle_timeout=1.0,
            check_interval=0.05,
            execution_task=task,
        )

    @pytest.mark.asyncio
    async def test_watchdog_raises_on_idle(self) -> None:
        """Watchdog raises _StaleExecutionError when idle too long."""
        runner = _MinimalRunner(stale_config=_make_stale_config())
        # Set last progress to distant past
        runner._last_progress_monotonic = time.monotonic() - 100

        async def _hang() -> ExecutionResult:
            await asyncio.sleep(10.0)
            return _make_success_result()

        task = asyncio.create_task(_hang())

        with pytest.raises(_StaleExecutionError) as exc_info:
            await runner._idle_watchdog(
                idle_timeout=0.1,
                check_interval=0.05,
                execution_task=task,
            )

        assert exc_info.value.timeout == 0.1
        # Task was cancelled by the watchdog; may be in cancelling state
        assert task.cancelled() or task.cancelling()
