"""Orchestra view: IPC reads for the public fleet page (GH #468)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

from marianne.core.logging import get_logger
from marianne.daemon.exceptions import DaemonError
from marianne.daemon.ipc.client import DaemonClient

_logger = get_logger("dashboard.orchestra_view")

_READ_FAILURES: tuple[type[BaseException], ...] = (
    DaemonError, OSError, TimeoutError, ValueError, TypeError, KeyError,
)
_READ_TIMEOUT_SECONDS = 2.0


class OrchestraView:
    """Snapshot, replay and live beats from the conductor's orchestra recorder."""

    def __init__(self, client: DaemonClient) -> None:
        self._client = client

    async def snapshot(self, limit: int = 200) -> dict[str, Any] | None:
        try:
            return cast(
                "dict[str, Any] | None",
                await asyncio.wait_for(
                    self._client.call("daemon.orchestra.snapshot", {"limit": limit}),
                    timeout=_READ_TIMEOUT_SECONDS,
                ),
            )
        except _READ_FAILURES as exc:
            _logger.warning("orchestra_snapshot_failed", error_type=type(exc).__name__)
            return None

    async def replay(self, day: str | None, limit: int | None = None) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if day:
            params["day"] = day
        if limit:
            params["limit"] = limit
        try:
            return cast(
                "dict[str, Any] | None",
                await asyncio.wait_for(
                    self._client.call("daemon.orchestra.replay", params),
                    timeout=_READ_TIMEOUT_SECONDS * 5,
                ),
            )
        except _READ_FAILURES as exc:
            _logger.warning("orchestra_replay_failed", error_type=type(exc).__name__)
            return None

    async def beats(self) -> AsyncIterator[dict[str, Any]]:
        """Live projected events; ends when the conductor closes the stream."""
        async for params in self._client.stream("daemon.orchestra.stream"):
            yield params
