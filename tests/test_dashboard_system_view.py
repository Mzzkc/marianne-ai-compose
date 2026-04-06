"""Tests for DaemonSystemView and system health API routes."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from marianne.daemon.ipc.client import DaemonClient
from marianne.daemon.types import DaemonStatus
from marianne.dashboard.app import create_app
from marianne.dashboard.routes.system import set_system_view
from marianne.dashboard.services.system_view import DaemonSystemView
from marianne.state.json_backend import JsonStateBackend

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_client() -> DaemonClient:
    """Create a DaemonClient with mocked IPC calls."""
    client = MagicMock(spec=DaemonClient)
    client._socket_path = Path("/tmp/fake.sock")

    # daemon.top → SystemSnapshot
    client.call = AsyncMock(return_value={
        "cpu_percent": 42.5,
        "memory_percent": 65.0,
        "pressure_level": "LOW",
        "active_processes": 3,
    })

    # daemon.status
    status = MagicMock(spec=DaemonStatus)
    status.model_dump.return_value = {
        "pid": 12345,
        "uptime_seconds": 3600.0,
        "running_jobs": 2,
        "memory_mb": 128.5,
    }
    client.status = AsyncMock(return_value=status)

    # daemon.rate_limits
    client.rate_limits = AsyncMock(return_value={
        "backends": {"claude": {"events_count": 5, "active": True}},
        "active_limits": 1,
    })

    # daemon.learning.patterns
    client.learning_patterns = AsyncMock(return_value={
        "patterns": [
            {"pattern_id": "p1", "description": "Rate limit pattern", "confidence": 0.9},
            {"pattern_id": "p2", "description": "Timeout pattern", "confidence": 0.7},
        ],
    })

    return client


@pytest.fixture
def system_view(mock_client: DaemonClient) -> DaemonSystemView:
    return DaemonSystemView(mock_client)


# ============================================================================
# DaemonSystemView Unit Tests
# ============================================================================


class TestGetSnapshot:
    @pytest.mark.asyncio
    async def test_returns_snapshot(self, system_view: DaemonSystemView) -> None:
        snap = await system_view.get_snapshot()
        assert snap is not None
        assert snap["cpu_percent"] == 42.5
        assert snap["pressure_level"] == "LOW"

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, mock_client: DaemonClient) -> None:
        mock_client.call = AsyncMock(side_effect=ConnectionError("down"))
        view = DaemonSystemView(mock_client)
        snap = await view.get_snapshot()
        assert snap is None


class TestGetDaemonStatus:
    @pytest.mark.asyncio
    async def test_returns_status_dict(self, system_view: DaemonSystemView) -> None:
        status = await system_view.get_daemon_status()
        assert status is not None
        assert status["pid"] == 12345
        assert status["uptime_seconds"] == 3600.0
        assert status["running_jobs"] == 2

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, mock_client: DaemonClient) -> None:
        mock_client.status = AsyncMock(side_effect=ConnectionError("down"))
        view = DaemonSystemView(mock_client)
        status = await view.get_daemon_status()
        assert status is None


class TestRateLimitState:
    @pytest.mark.asyncio
    async def test_returns_rate_limits(self, system_view: DaemonSystemView) -> None:
        result = await system_view.rate_limit_state()
        assert result["active_limits"] == 1
        assert "claude" in result["backends"]
        assert result["backends"]["claude"]["active"] is True

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, mock_client: DaemonClient) -> None:
        mock_client.rate_limits = AsyncMock(side_effect=ConnectionError("down"))
        view = DaemonSystemView(mock_client)
        result = await view.rate_limit_state()
        assert result == {"backends": {}, "active_limits": 0}


class TestPressureLevel:
    @pytest.mark.asyncio
    async def test_returns_level_and_color(self, system_view: DaemonSystemView) -> None:
        result = await system_view.pressure_level()
        assert result["level"] == "LOW"
        assert result["color"] == "yellow"

    @pytest.mark.asyncio
    async def test_unknown_when_daemon_down(self, mock_client: DaemonClient) -> None:
        mock_client.call = AsyncMock(side_effect=ConnectionError("down"))
        view = DaemonSystemView(mock_client)
        result = await view.pressure_level()
        assert result["level"] == "unknown"
        assert result["color"] == "gray"

    @pytest.mark.asyncio
    async def test_all_pressure_colors(self, mock_client: DaemonClient) -> None:
        expected = {
            "NONE": "green",
            "LOW": "yellow",
            "MEDIUM": "amber",
            "HIGH": "orange",
            "CRITICAL": "red",
        }
        for level, color in expected.items():
            mock_client.call = AsyncMock(return_value={"pressure_level": level})
            view = DaemonSystemView(mock_client)
            result = await view.pressure_level()
            assert result["level"] == level
            assert result["color"] == color, f"Expected {color} for {level}"

    @pytest.mark.asyncio
    async def test_unknown_pressure_level_gets_gray(
        self, mock_client: DaemonClient,
    ) -> None:
        mock_client.call = AsyncMock(return_value={"pressure_level": "ALIEN"})
        view = DaemonSystemView(mock_client)
        result = await view.pressure_level()
        assert result["level"] == "ALIEN"
        assert result["color"] == "gray"

    @pytest.mark.asyncio
    async def test_missing_pressure_level_defaults_none(
        self, mock_client: DaemonClient,
    ) -> None:
        mock_client.call = AsyncMock(return_value={})
        view = DaemonSystemView(mock_client)
        result = await view.pressure_level()
        assert result["level"] == "NONE"
        assert result["color"] == "green"


class TestLearningPatterns:
    @pytest.mark.asyncio
    async def test_returns_patterns(self, system_view: DaemonSystemView) -> None:
        patterns = await system_view.learning_patterns()
        assert len(patterns) == 2
        assert patterns[0]["pattern_id"] == "p1"
        assert patterns[1]["confidence"] == 0.7

    @pytest.mark.asyncio
    async def test_respects_limit(self, system_view: DaemonSystemView) -> None:
        await system_view.learning_patterns(limit=5)
        system_view._client.learning_patterns.assert_called_with(5)

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self, mock_client: DaemonClient) -> None:
        mock_client.learning_patterns = AsyncMock(
            side_effect=ConnectionError("down"),
        )
        view = DaemonSystemView(mock_client)
        patterns = await view.learning_patterns()
        assert patterns == []


# ============================================================================
# Route Tests
# ============================================================================


@pytest.fixture
def app_with_system_view(
    system_view: DaemonSystemView,
    tmp_path: Path,
) -> TestClient:
    """Create a test app with system routes wired up."""
    from marianne.dashboard.routes.system import router as system_router

    backend = JsonStateBackend(tmp_path / "state")
    app = create_app(state_backend=backend)
    set_system_view(system_view)
    app.include_router(system_router)
    return TestClient(app)


class TestSystemRoutes:
    def test_rate_limits_endpoint(self, app_with_system_view: TestClient) -> None:
        resp = app_with_system_view.get("/api/system/rate-limits")
        assert resp.status_code == 200
        data = resp.json()
        assert "backends" in data
        assert "active_limits" in data
        assert data["active_limits"] == 1

    def test_pressure_endpoint(self, app_with_system_view: TestClient) -> None:
        resp = app_with_system_view.get("/api/system/pressure")
        assert resp.status_code == 200
        data = resp.json()
        assert "level" in data
        assert "color" in data
        assert data["level"] == "LOW"
        assert data["color"] == "yellow"

    def test_learning_endpoint(self, app_with_system_view: TestClient) -> None:
        resp = app_with_system_view.get("/api/system/learning")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["pattern_id"] == "p1"

    def test_learning_endpoint_with_limit(
        self, app_with_system_view: TestClient,
    ) -> None:
        resp = app_with_system_view.get("/api/system/learning?limit=5")
        assert resp.status_code == 200

    def test_learning_endpoint_invalid_limit(
        self, app_with_system_view: TestClient,
    ) -> None:
        resp = app_with_system_view.get("/api/system/learning?limit=0")
        assert resp.status_code == 422  # FastAPI validation error

    def test_learning_endpoint_limit_too_high(
        self, app_with_system_view: TestClient,
    ) -> None:
        resp = app_with_system_view.get("/api/system/learning?limit=101")
        assert resp.status_code == 422
