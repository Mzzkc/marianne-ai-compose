"""Smoke tests for daemon task_utils module."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from marianne.daemon.task_utils import log_task_exception


def _make_logger() -> MagicMock:
    """Create a mock logger with structlog-style interface."""
    return MagicMock()


@pytest.mark.asyncio
async def test_log_task_exception_returns_none_on_success() -> None:
    """Successful task should return None (no exception)."""

    async def _ok() -> str:
        return "done"

    task = asyncio.create_task(_ok())
    await task

    result = log_task_exception(task, _make_logger(), "test.ok")
    assert result is None


@pytest.mark.asyncio
async def test_log_task_exception_returns_exception_on_failure() -> None:
    """Failed task should return the exception."""

    async def _fail() -> None:
        raise ValueError("boom")

    task = asyncio.create_task(_fail())
    with pytest.raises(ValueError):
        await task

    logger = _make_logger()
    result = log_task_exception(task, logger, "test.fail")
    assert isinstance(result, ValueError)
    assert str(result) == "boom"
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_log_task_exception_handles_cancelled() -> None:
    """Cancelled task should return None."""

    async def _hang() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(_hang())
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    result = log_task_exception(task, _make_logger(), "test.cancel")
    assert result is None
