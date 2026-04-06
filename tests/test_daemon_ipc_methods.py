"""Tests for daemon.rate_limits and daemon.learning.patterns IPC methods.

Covers handler registration, response shapes, and edge cases (no coordinator,
learning hub not running, empty stores).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, PropertyMock

import pytest

from marianne.daemon.config import DaemonConfig
from marianne.daemon.process import DaemonProcess


def _build_daemon_and_register(
    manager: MagicMock | None = None,
) -> tuple[DaemonProcess, MagicMock, MagicMock]:
    """Create a DaemonProcess, register methods, return (dp, handler, manager)."""
    config = DaemonConfig()
    dp = DaemonProcess(config)

    handler = MagicMock()
    mgr = manager or MagicMock()

    dp._register_methods(handler, mgr, health=None)
    return dp, handler, mgr


def _get_handler(mock_handler: MagicMock, method_name: str):
    """Extract a registered handler function by method name."""
    for call in mock_handler.register.call_args_list:
        if call.args[0] == method_name:
            return call.args[1]
    raise KeyError(f"Handler {method_name!r} not registered")


# ─── Registration ─────────────────────────────────────────────────────


class TestMethodRegistration:
    """Verify both new methods are registered."""

    def test_rate_limits_registered(self):
        _, handler, _ = _build_daemon_and_register()
        registered = {c.args[0] for c in handler.register.call_args_list}
        assert "daemon.rate_limits" in registered

    def test_learning_patterns_registered(self):
        _, handler, _ = _build_daemon_and_register()
        registered = {c.args[0] for c in handler.register.call_args_list}
        assert "daemon.learning.patterns" in registered


# ─── daemon.rate_limits ───────────────────────────────────────────────


class TestRateLimitsHandler:
    """Tests for the daemon.rate_limits IPC handler."""

    @pytest.mark.asyncio
    async def test_empty_rate_limits(self):
        """Returns empty state when no rate limits are active."""
        mgr = MagicMock()
        coordinator = MagicMock()
        type(coordinator).active_limits = PropertyMock(return_value={})
        type(coordinator).recent_events = PropertyMock(return_value=[])
        type(mgr).rate_coordinator = PropertyMock(return_value=coordinator)

        _, handler, _ = _build_daemon_and_register(mgr)
        fn = _get_handler(handler, "daemon.rate_limits")

        result = await fn({}, None)
        assert result == {
            "backends": {},
            "active_limits": 0,
            "recent_events_count": 0,
        }

    @pytest.mark.asyncio
    async def test_active_rate_limits(self):
        """Returns backend data when rate limits are active."""
        mgr = MagicMock()
        coordinator = MagicMock()
        type(coordinator).active_limits = PropertyMock(
            return_value={"claude_cli": 42.5, "openai": 10.0},
        )
        # Two mock events
        type(coordinator).recent_events = PropertyMock(
            return_value=[MagicMock(), MagicMock(), MagicMock()],
        )
        type(mgr).rate_coordinator = PropertyMock(return_value=coordinator)

        _, handler, _ = _build_daemon_and_register(mgr)
        fn = _get_handler(handler, "daemon.rate_limits")

        result = await fn({}, None)
        assert result["active_limits"] == 2
        assert result["recent_events_count"] == 3
        assert "claude_cli" in result["backends"]
        assert result["backends"]["claude_cli"] == {"seconds_remaining": 42.5}
        assert result["backends"]["openai"] == {"seconds_remaining": 10.0}


# ─── daemon.learning.patterns ────────────────────────────────────────


class TestLearningPatternsHandler:
    """Tests for the daemon.learning.patterns IPC handler."""

    @pytest.mark.asyncio
    async def test_hub_not_running(self):
        """Returns empty patterns when learning hub is not running."""
        mgr = MagicMock()
        hub = MagicMock()
        hub.is_running = False
        type(mgr).learning_hub = PropertyMock(return_value=hub)

        _, handler, _ = _build_daemon_and_register(mgr)
        fn = _get_handler(handler, "daemon.learning.patterns")

        result = await fn({}, None)
        assert result == {"patterns": []}

    @pytest.mark.asyncio
    async def test_empty_store(self):
        """Returns empty patterns when store has no data."""
        mgr = MagicMock()
        hub = MagicMock()
        hub.is_running = True
        store = MagicMock()
        store.get_patterns.return_value = []
        hub.store = store
        type(mgr).learning_hub = PropertyMock(return_value=hub)

        _, handler, _ = _build_daemon_and_register(mgr)
        fn = _get_handler(handler, "daemon.learning.patterns")

        result = await fn({}, None)
        assert result == {"patterns": []}
        store.get_patterns.assert_called_once_with(min_priority=0.0, limit=20)

    @pytest.mark.asyncio
    async def test_returns_pattern_data(self):
        """Returns serialized pattern records from the store."""
        from marianne.learning.store.models import PatternRecord, QuarantineStatus

        record = PatternRecord(
            id="pat-001",
            pattern_type="ERROR_RECOVERY",
            pattern_name="retry-on-timeout",
            description="Retry with backoff on timeout errors",
            occurrence_count=5,
            first_seen=datetime(2026, 1, 1),
            last_seen=datetime(2026, 2, 20),
            last_confirmed=datetime(2026, 2, 20),
            led_to_success_count=3,
            led_to_failure_count=1,
            effectiveness_score=0.75,
            variance=0.1,
            suggested_action="increase timeout",
            context_tags=["timeout", "retry"],
            priority_score=0.8,
            quarantine_status=QuarantineStatus.VALIDATED,
            trust_score=0.9,
        )

        mgr = MagicMock()
        hub = MagicMock()
        hub.is_running = True
        store = MagicMock()
        store.get_patterns.return_value = [record]
        hub.store = store
        type(mgr).learning_hub = PropertyMock(return_value=hub)

        _, handler, _ = _build_daemon_and_register(mgr)
        fn = _get_handler(handler, "daemon.learning.patterns")

        result = await fn({"limit": 5}, None)
        assert len(result["patterns"]) == 1

        pat = result["patterns"][0]
        assert pat["id"] == "pat-001"
        assert pat["pattern_type"] == "ERROR_RECOVERY"
        assert pat["pattern_name"] == "retry-on-timeout"
        assert pat["effectiveness_score"] == 0.75
        assert pat["priority_score"] == 0.8
        assert pat["occurrence_count"] == 5
        assert pat["quarantine_status"] == "validated"
        assert pat["trust_score"] == 0.9
        assert pat["last_seen"] == "2026-02-20T00:00:00"

        store.get_patterns.assert_called_once_with(min_priority=0.0, limit=5)

    @pytest.mark.asyncio
    async def test_default_limit(self):
        """Uses default limit=20 when not specified."""
        mgr = MagicMock()
        hub = MagicMock()
        hub.is_running = True
        store = MagicMock()
        store.get_patterns.return_value = []
        hub.store = store
        type(mgr).learning_hub = PropertyMock(return_value=hub)

        _, handler, _ = _build_daemon_and_register(mgr)
        fn = _get_handler(handler, "daemon.learning.patterns")

        await fn({}, None)
        store.get_patterns.assert_called_once_with(min_priority=0.0, limit=20)
