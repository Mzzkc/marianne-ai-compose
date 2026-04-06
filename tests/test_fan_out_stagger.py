"""Tests for F-099: Fan-out launch staggering.

Adding a configurable delay between parallel sheet launches reduces
rate limit surge when many sheets hit the same API simultaneously.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.config.execution import ParallelConfig
from marianne.execution.parallel import ParallelExecutionConfig, ParallelExecutor


# ---------------------------------------------------------------------------
# 1. ParallelConfig accepts stagger_delay_ms
# ---------------------------------------------------------------------------


class TestParallelConfigStagger:
    """ParallelConfig must accept stagger_delay_ms field."""

    def test_default_stagger_delay(self) -> None:
        """Default stagger_delay_ms is 0 (no stagger)."""
        config = ParallelConfig()
        assert config.stagger_delay_ms == 0

    def test_custom_stagger_delay(self) -> None:
        """stagger_delay_ms can be set to a custom value."""
        config = ParallelConfig(stagger_delay_ms=100)
        assert config.stagger_delay_ms == 100

    def test_stagger_delay_in_yaml(self) -> None:
        """stagger_delay_ms is accepted from YAML config."""
        from marianne.core.config import JobConfig

        config = JobConfig.model_validate({
            "name": "test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 3, "total_items": 9},
            "prompt": {"template": "Test {{ sheet_num }}"},
            "parallel": {
                "enabled": True,
                "max_concurrent": 5,
                "stagger_delay_ms": 150,
            },
        })
        assert config.parallel.stagger_delay_ms == 150

    def test_stagger_delay_validation_non_negative(self) -> None:
        """stagger_delay_ms must be non-negative."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParallelConfig(stagger_delay_ms=-1)

    def test_stagger_delay_max_cap(self) -> None:
        """stagger_delay_ms is capped at 5000ms."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ParallelConfig(stagger_delay_ms=6000)


# ---------------------------------------------------------------------------
# 2. ParallelExecutionConfig accepts stagger_delay_ms
# ---------------------------------------------------------------------------


class TestParallelExecConfigStagger:
    """ParallelExecutionConfig dataclass must carry stagger_delay_ms."""

    def test_default_value(self) -> None:
        """Default stagger_delay_ms is 0."""
        config = ParallelExecutionConfig()
        assert config.stagger_delay_ms == 0

    def test_custom_value(self) -> None:
        """stagger_delay_ms can be set."""
        config = ParallelExecutionConfig(stagger_delay_ms=100)
        assert config.stagger_delay_ms == 100


# ---------------------------------------------------------------------------
# 3. ParallelExecutor uses stagger delay between launches
# ---------------------------------------------------------------------------


class TestParallelExecutorStagger:
    """ParallelExecutor must delay between sheet launches when stagger > 0."""

    @pytest.mark.asyncio
    async def test_no_stagger_when_zero(self) -> None:
        """When stagger_delay_ms=0, no delay between launches."""
        runner = MagicMock()
        runner.dependency_dag = None
        runner._state_lock = asyncio.Lock()

        config = ParallelExecutionConfig(
            enabled=True,
            max_concurrent=3,
            stagger_delay_ms=0,
        )
        executor = ParallelExecutor(runner, config)

        # Mock sheet execution to complete immediately
        async def fake_execute(sheet_num: int, state: Any) -> None:
            pass

        executor._execute_single_sheet = fake_execute  # type: ignore[assignment]

        state = MagicMock()
        start = time.monotonic()
        await executor.execute_batch([1, 2, 3], state)
        duration = time.monotonic() - start

        # Without stagger, should complete in under 0.5s
        assert duration < 0.5

    @pytest.mark.asyncio
    async def test_stagger_adds_delay(self) -> None:
        """When stagger_delay_ms=100, there's a delay between launches."""
        runner = MagicMock()
        runner.dependency_dag = None
        runner._state_lock = asyncio.Lock()

        launch_times: list[float] = []

        config = ParallelExecutionConfig(
            enabled=True,
            max_concurrent=5,
            stagger_delay_ms=100,
        )
        executor = ParallelExecutor(runner, config)

        async def fake_execute(sheet_num: int, state: Any) -> None:
            launch_times.append(time.monotonic())
            await asyncio.sleep(0.01)  # Simulate minimal work

        executor._execute_single_sheet = fake_execute  # type: ignore[assignment]

        state = MagicMock()
        await executor.execute_batch([1, 2, 3], state)

        # Should have 3 launches
        assert len(launch_times) == 3

        # With 100ms stagger between 3 sheets, total stagger should be >= 200ms
        # Use generous tolerance (50ms) for CI
        if len(launch_times) >= 2:
            gap_1_2 = launch_times[1] - launch_times[0]
            assert gap_1_2 >= 0.05, \
                f"Gap between sheet 1 and 2 was {gap_1_2:.3f}s, expected >= 0.05s"

    @pytest.mark.asyncio
    async def test_single_sheet_no_stagger(self) -> None:
        """With a single sheet, no stagger delay is needed."""
        runner = MagicMock()
        runner.dependency_dag = None
        runner._state_lock = asyncio.Lock()

        config = ParallelExecutionConfig(
            enabled=True,
            max_concurrent=5,
            stagger_delay_ms=500,  # Large delay, but should not apply
        )
        executor = ParallelExecutor(runner, config)

        async def fake_execute(sheet_num: int, state: Any) -> None:
            pass

        executor._execute_single_sheet = fake_execute  # type: ignore[assignment]

        state = MagicMock()
        start = time.monotonic()
        await executor.execute_batch([1], state)
        duration = time.monotonic() - start

        # Single sheet should complete instantly
        assert duration < 0.5
