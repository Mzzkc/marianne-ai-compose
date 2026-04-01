"""Tests for F-122: IPC callsites must use clone-aware socket resolution.

Five callsites hardcoded production socket paths, bypassing the
``_resolve_socket_path()`` clone-aware resolution pattern:

1. ``execution/hooks.py`` — ``_try_daemon_submit`` used ``SocketConfig().path``
2. ``mcp/tools.py`` — ``JobTools.__init__`` used ``DaemonConfig().socket.path``
3. ``dashboard/routes/jobs.py`` — ``daemon_status`` used ``DaemonConfig().socket.path``
4. ``dashboard/services/job_control.py`` — ``JobControlService.__init__`` used
   ``DaemonConfig().socket.path``
5. ``dashboard/app.py`` — ``_create_daemon_client`` used ``DaemonConfig().socket.path``

All must route through ``_resolve_socket_path(None)`` so ``--conductor-clone``
works everywhere.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozart.daemon.ipc.client import DaemonClient
from mozart.state.base import StateBackend


def _mock_client() -> MagicMock:
    """Create a spec'd DaemonClient mock."""
    return MagicMock(spec=DaemonClient)


def _mock_response(*, status: str = "accepted", job_id: str = "test-123") -> MagicMock:
    """Create a mock daemon submit response."""
    resp = MagicMock(spec=["status", "job_id"])
    resp.status = status
    resp.job_id = job_id
    return resp


def _mock_backend() -> MagicMock:
    """Create a spec'd StateBackend mock."""
    return MagicMock(spec=StateBackend)


# ── hooks.py ──────────────────────────────────────────────────────────


class TestHooksCloneAware:
    """hooks.py _try_daemon_submit must use _resolve_socket_path."""

    @pytest.mark.asyncio
    async def test_try_daemon_submit_uses_resolve_socket_path(self) -> None:
        """_try_daemon_submit creates DaemonClient with resolved clone path."""
        clone_socket = Path("/tmp/mozart-clone-test.sock")

        with (
            patch(
                "mozart.daemon.detect._resolve_socket_path",
                return_value=clone_socket,
            ) as mock_resolve,
            patch(
                "mozart.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("mozart.daemon.ipc.client.DaemonClient") as mock_client_cls,
        ):
            client = _mock_client()
            client.submit_job = AsyncMock(
                return_value=_mock_response(status="accepted", job_id="test-job-123"),
            )
            mock_client_cls.return_value = client

            from mozart.execution.hooks import _try_daemon_submit

            ok, job_id = await _try_daemon_submit(
                job_path=Path("/tmp/test.yaml"),
                workspace=Path("/tmp/ws"),
                fresh=False,
                chain_depth=1,
            )

            assert ok is True
            assert job_id == "test-job-123"
            mock_resolve.assert_called_once_with(None)
            mock_client_cls.assert_called_once_with(clone_socket)

    @pytest.mark.asyncio
    async def test_try_daemon_submit_no_clone_uses_default(self) -> None:
        """When no clone is active, falls back to default socket."""
        default_socket = Path("/home/test/.mozart/daemon.sock")

        with (
            patch(
                "mozart.daemon.detect._resolve_socket_path",
                return_value=default_socket,
            ),
            patch(
                "mozart.daemon.detect.is_daemon_available",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("mozart.daemon.ipc.client.DaemonClient") as mock_client_cls,
        ):
            client = _mock_client()
            client.submit_job = AsyncMock(
                return_value=_mock_response(status="accepted", job_id="job-456"),
            )
            mock_client_cls.return_value = client

            from mozart.execution.hooks import _try_daemon_submit

            await _try_daemon_submit(
                job_path=Path("/tmp/test.yaml"),
                workspace=None,
                fresh=False,
                chain_depth=None,
            )

            mock_client_cls.assert_called_once_with(default_socket)

    def test_hooks_no_longer_imports_socket_config(self) -> None:
        """hooks.py should not import SocketConfig for DaemonClient creation."""
        from mozart.execution import hooks

        source = inspect.getsource(hooks._try_daemon_submit)
        assert "SocketConfig" not in source, (
            "hooks._try_daemon_submit still imports SocketConfig — "
            "should use _resolve_socket_path instead"
        )

    def test_hooks_uses_resolve_socket_path_in_source(self) -> None:
        """hooks.py _try_daemon_submit source calls _resolve_socket_path."""
        from mozart.execution import hooks

        source = inspect.getsource(hooks._try_daemon_submit)
        assert "_resolve_socket_path" in source, (
            "hooks._try_daemon_submit does not call _resolve_socket_path"
        )


# ── mcp/tools.py ──────────────────────────────────────────────────────


class TestMcpToolsCloneAware:
    """MCP JobTools must use clone-aware socket resolution."""

    def test_job_tools_uses_resolve_socket_path(self) -> None:
        """JobTools.__init__ creates DaemonClient with resolved clone path."""
        clone_socket = Path("/tmp/mozart-clone-mcp.sock")

        with (
            patch(
                "mozart.mcp.tools._resolve_socket_path",
                return_value=clone_socket,
            ) as mock_resolve,
            patch("mozart.mcp.tools.DaemonClient") as mock_client_cls,
            patch("mozart.mcp.tools.JobControlService"),
        ):
            mock_client_cls.return_value = _mock_client()

            from mozart.mcp.tools import JobTools

            tools = JobTools(_mock_backend(), Path("/tmp"))

            mock_resolve.assert_called_once_with(None)
            mock_client_cls.assert_called_once_with(clone_socket)
            assert tools._daemon_client is mock_client_cls.return_value

    def test_mcp_tools_no_longer_uses_daemon_config_for_socket(self) -> None:
        """MCP tools should not use DaemonConfig().socket.path directly."""
        from mozart.mcp import tools

        source = inspect.getsource(tools.JobTools.__init__)
        assert "DaemonConfig().socket.path" not in source, (
            "JobTools.__init__ still uses DaemonConfig().socket.path — "
            "should use _resolve_socket_path instead"
        )


# ── dashboard/routes/jobs.py ──────────────────────���──────────────────


class TestDashboardRoutesCloneAware:
    """Dashboard daemon_status route must use clone-aware socket."""

    def test_daemon_status_no_hardcoded_socket(self) -> None:
        """daemon_status should not use DaemonConfig().socket.path."""
        from mozart.dashboard.routes import jobs

        source = inspect.getsource(jobs.daemon_status)
        assert "DaemonConfig().socket.path" not in source, (
            "daemon_status still uses DaemonConfig().socket.path — "
            "should use _resolve_socket_path instead"
        )

    def test_daemon_status_uses_resolve_socket_path_in_source(self) -> None:
        """daemon_status source calls _resolve_socket_path."""
        from mozart.dashboard.routes import jobs

        source = inspect.getsource(jobs.daemon_status)
        assert "_resolve_socket_path" in source, (
            "daemon_status does not call _resolve_socket_path"
        )


# ── dashboard/services/job_control.py ────────────────────────────────


class TestJobControlServiceCloneAware:
    """JobControlService must use clone-aware socket resolution."""

    def test_job_control_uses_resolve_socket_path(self) -> None:
        """JobControlService.__init__ creates DaemonClient with resolved path."""
        clone_socket = Path("/tmp/mozart-clone-dash.sock")

        with (
            patch(
                "mozart.dashboard.services.job_control._resolve_socket_path",
                return_value=clone_socket,
            ) as mock_resolve,
            patch(
                "mozart.dashboard.services.job_control.DaemonClient",
            ) as mock_client_cls,
        ):
            mock_client_cls.return_value = _mock_client()

            from mozart.dashboard.services.job_control import JobControlService

            svc = JobControlService(_mock_backend(), Path("/tmp"))

            mock_resolve.assert_called_once_with(None)
            mock_client_cls.assert_called_once_with(clone_socket)
            assert svc._daemon_client is mock_client_cls.return_value

    def test_job_control_no_hardcoded_fallback(self) -> None:
        """JobControlService should not hardcode daemon.sock path."""
        from mozart.dashboard.services import job_control

        source = inspect.getsource(job_control.JobControlService.__init__)
        assert "daemon.sock" not in source, (
            "JobControlService.__init__ still has hardcoded daemon.sock fallback"
        )

    def test_job_control_no_daemon_config_for_socket(self) -> None:
        """JobControlService should not use DaemonConfig().socket.path."""
        from mozart.dashboard.services import job_control

        source = inspect.getsource(job_control.JobControlService.__init__)
        assert "DaemonConfig().socket.path" not in source, (
            "JobControlService.__init__ still uses DaemonConfig().socket.path"
        )


# ── dashboard/app.py ─────────────────────────────────────────────────


class TestDashboardAppCloneAware:
    """Dashboard _create_daemon_client must use clone-aware resolution."""

    def test_create_daemon_client_uses_resolve_socket_path(self) -> None:
        """_create_daemon_client resolves socket via clone-aware path."""
        clone_socket = Path("/tmp/mozart-clone-app.sock")

        with (
            patch(
                "mozart.daemon.detect._resolve_socket_path",
                return_value=clone_socket,
            ) as mock_resolve,
            patch("mozart.daemon.ipc.client.DaemonClient") as mock_client_cls,
        ):
            mock_client_cls.return_value = _mock_client()

            from mozart.dashboard.app import _create_daemon_client

            client = _create_daemon_client()

            mock_resolve.assert_called_once_with(None)
            mock_client_cls.assert_called_once_with(clone_socket)
            assert client is mock_client_cls.return_value

    def test_app_factory_no_hardcoded_socket(self) -> None:
        """_create_daemon_client should not use DaemonConfig().socket.path."""
        from mozart.dashboard import app

        source = inspect.getsource(app._create_daemon_client)
        assert "DaemonConfig().socket.path" not in source, (
            "_create_daemon_client still uses DaemonConfig().socket.path — "
            "should use _resolve_socket_path instead"
        )

    def test_app_factory_uses_resolve_in_source(self) -> None:
        """_create_daemon_client source calls _resolve_socket_path."""
        from mozart.dashboard import app

        source = inspect.getsource(app._create_daemon_client)
        assert "_resolve_socket_path" in source, (
            "_create_daemon_client does not call _resolve_socket_path"
        )
