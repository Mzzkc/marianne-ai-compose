"""Live system health from daemon profiler and controllers.

Wraps daemon IPC calls for system snapshots, status, rate limits,
backpressure levels, and learning patterns.
"""
from __future__ import annotations

import asyncio
from typing import Any, cast

from marianne.core.logging import get_logger
from marianne.daemon.ipc.client import DaemonClient

_logger = get_logger("dashboard.system_view")

DASHBOARD_SYSTEM_VIEW_TIMEOUT_SECONDS = 2.0


class DaemonSystemView:
    """Live system health from the daemon profiler and controllers.

    Parameters
    ----------
    client:
        Connected ``DaemonClient`` for IPC calls.
    """

    def __init__(self, client: DaemonClient) -> None:
        self._client = client

    async def get_snapshot(self) -> dict[str, Any] | None:
        """Get latest SystemSnapshot from daemon.

        Returns ``None`` if the daemon is down or the call fails.
        """
        try:
            return cast(
                "dict[str, Any] | None",
                await asyncio.wait_for(
                    self._client.call("daemon.top"),
                    timeout=DASHBOARD_SYSTEM_VIEW_TIMEOUT_SECONDS,
                ),
            )
        except Exception:
            _logger.debug("get_snapshot_failed", exc_info=True)
            return None

    async def get_daemon_status(self) -> dict[str, Any] | None:
        """Get daemon status (pid, uptime, running_jobs, memory).

        Returns ``None`` if the daemon is unreachable.
        """
        try:
            result = await asyncio.wait_for(
                self._client.status(),
                timeout=DASHBOARD_SYSTEM_VIEW_TIMEOUT_SECONDS,
            )
            return result.model_dump()
        except Exception:
            _logger.debug("get_daemon_status_failed", exc_info=True)
            return None

    async def rate_limit_state(self) -> dict[str, Any]:
        """Current rate limit state per backend."""
        try:
            return await asyncio.wait_for(
                self._client.rate_limits(),
                timeout=DASHBOARD_SYSTEM_VIEW_TIMEOUT_SECONDS,
            )
        except Exception:
            _logger.debug("rate_limit_state_failed", exc_info=True)
            return {"backends": {}, "active_limits": 0}

    async def pressure_level(self) -> dict[str, Any]:
        """Backpressure level from latest snapshot."""
        snap = await self.get_snapshot()
        if snap is None:
            return {"level": "unknown", "color": "gray"}
        level = snap.get("pressure_level", "NONE")
        colors = {
            "NONE": "green",
            "LOW": "yellow",
            "MEDIUM": "amber",
            "HIGH": "orange",
            "CRITICAL": "red",
        }
        return {"level": level, "color": colors.get(level, "gray")}

    async def learning_patterns(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent learning insights from the daemon."""
        try:
            result = await asyncio.wait_for(
                self._client.learning_patterns(limit),
                timeout=DASHBOARD_SYSTEM_VIEW_TIMEOUT_SECONDS,
            )
            return cast("list[dict[str, Any]]", result.get("patterns", []))
        except Exception:
            _logger.debug("learning_patterns_failed", exc_info=True)
            return []


__all__ = ["DaemonSystemView"]
