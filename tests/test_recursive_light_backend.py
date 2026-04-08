"""Tests for marianne.backends.recursive_light module.

Covers the RecursiveLightBackend class: initialization, execute() with
various HTTP response scenarios, error handling paths, health_check,
and close.

GH#82 — Recursive Light backend at 58% coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marianne.backends.recursive_light import RecursiveLightBackend


class TestInit:
    """Tests for RecursiveLightBackend initialization."""

    def test_default_values(self) -> None:
        """Defaults for endpoint, user_id, and timeout."""
        backend = RecursiveLightBackend()
        assert backend.rl_endpoint == "http://localhost:8080"
        assert backend.timeout == 30.0
        assert backend.user_id  # auto-generated UUID

    def test_custom_values(self) -> None:
        """Custom endpoint, user_id, and timeout."""
        backend = RecursiveLightBackend(
            rl_endpoint="http://custom:9090/",
            user_id="test-user",
            timeout=60.0,
        )
        assert backend.rl_endpoint == "http://custom:9090"  # trailing slash stripped
        assert backend.user_id == "test-user"
        assert backend.timeout == 60.0

    def test_name_property(self) -> None:
        """Backend name is 'recursive-light'."""
        backend = RecursiveLightBackend()
        assert backend.name == "recursive-light"

    def test_from_config(self) -> None:
        """from_config creates backend from BackendConfig."""
        config = MagicMock()
        config.recursive_light.endpoint = "http://rl:8080"
        config.recursive_light.user_id = "cfg-user"
        config.recursive_light.timeout = 45.0

        backend = RecursiveLightBackend.from_config(config)
        assert backend.rl_endpoint == "http://rl:8080"
        assert backend.user_id == "cfg-user"
        assert backend.timeout == 45.0


class TestExecute:
    """Tests for execute() with mocked HTTP responses."""

    @pytest.fixture()
    def backend(self) -> RecursiveLightBackend:
        return RecursiveLightBackend(rl_endpoint="http://test:8080", user_id="test")

    async def test_successful_execution(self, backend: RecursiveLightBackend) -> None:
        """Successful 200 response -> success=True with response text."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "Hello world"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test prompt")

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "Hello world"
        assert result.duration_seconds > 0

    async def test_api_error_response(self, backend: RecursiveLightBackend) -> None:
        """Non-200 status code -> success=False with error info."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test prompt")

        assert result.success is False
        assert result.exit_code == 500
        assert result.error_type == "api_error"

    async def test_connection_error(self, backend: RecursiveLightBackend) -> None:
        """Connection refused -> success=False, connection_error type."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test prompt")

        assert result.success is False
        assert result.error_type == "connection_error"
        assert "Connection refused" in result.stderr

    async def test_timeout_error(self, backend: RecursiveLightBackend) -> None:
        """Request timeout -> success=False, timeout type, exit_code 124."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Timed out")

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test prompt")

        assert result.success is False
        assert result.exit_code == 124
        assert result.error_type == "timeout"

    async def test_unexpected_exception(self, backend: RecursiveLightBackend) -> None:
        """Unexpected exception is re-raised after logging."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = RuntimeError("Something went wrong")

        with patch.object(backend, "_get_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Something went wrong"):
                await backend.execute("test prompt")

    async def test_timeout_override_logged(self, backend: RecursiveLightBackend) -> None:
        """Per-call timeout_seconds is logged but not enforced."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "ok"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test", timeout_seconds=999)

        assert result.success is True

    async def test_non_string_response(self, backend: RecursiveLightBackend) -> None:
        """Non-string response value is converted to str."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": 12345}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(backend, "_get_client", return_value=mock_client):
            result = await backend.execute("test")

        assert result.success is True
        assert result.stdout == "12345"


class TestHealthCheck:
    """Tests for health_check()."""

    async def test_healthy_server(self) -> None:
        """Server responding 200 on /health -> True."""
        backend = RecursiveLightBackend()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is True

    async def test_connection_error_unhealthy(self) -> None:
        """Connection error -> False."""
        backend = RecursiveLightBackend()

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is False

    async def test_timeout_unhealthy(self) -> None:
        """Timeout -> False."""
        backend = RecursiveLightBackend()

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("Timed out")

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is False

    async def test_unexpected_error_unhealthy(self) -> None:
        """Unexpected error -> False."""
        backend = RecursiveLightBackend()

        mock_client = AsyncMock()
        mock_client.get.side_effect = RuntimeError("Unexpected")

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is False

    async def test_all_endpoints_fail(self) -> None:
        """All health endpoints return non-200 -> False."""
        backend = RecursiveLightBackend()

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch.object(backend, "_get_client", return_value=mock_client):
            assert await backend.health_check() is False


class TestClose:
    """Tests for close()."""

    async def test_close_delegates_to_mixin(self) -> None:
        """close() calls _close_httpx_client."""
        backend = RecursiveLightBackend()

        with patch.object(backend, "_close_httpx_client", new_callable=AsyncMock) as mock_close:
            await backend.close()
            mock_close.assert_called_once()
