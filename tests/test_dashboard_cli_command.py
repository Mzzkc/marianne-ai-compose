"""Tests for marianne.cli.commands.dashboard CLI command error paths.

Tests the dashboard and mcp CLI entry points, focusing on error handling,
state backend selection, and import fallbacks. This tests the CLI wiring,
not the FastAPI routes (those are in test_dashboard*.py).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from marianne.cli.commands.dashboard import dashboard, mcp

# Build a minimal Typer app that registers the dashboard and mcp commands
_app = typer.Typer()
_app.command("dashboard")(dashboard)
_app.command("mcp")(mcp)

from typer.testing import CliRunner

runner = CliRunner()


class TestDashboardUvicornMissing:
    """Test dashboard command when uvicorn is not installed."""

    def test_missing_uvicorn_exits_1(self) -> None:
        """Dashboard exits with error when uvicorn cannot be imported."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "uvicorn":
                raise ImportError("No module named 'uvicorn'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = runner.invoke(_app, ["dashboard"])

        assert result.exit_code == 1
        assert "uvicorn" in result.output.lower()


class TestDashboardCreateAppInvocation:
    """Test that dashboard calls create_app correctly."""

    def test_create_app_called_with_title_and_version(self, tmp_path: Path) -> None:
        """Dashboard passes title and version to create_app."""
        mock_uvicorn = MagicMock()
        mock_create_app = MagicMock(return_value=MagicMock())

        with (
            pytest.MonkeyPatch.context() as modules,
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            modules.setitem(sys.modules, "uvicorn", mock_uvicorn)
            runner.invoke(_app, ["dashboard", "--workspace", str(tmp_path)])

        mock_create_app.assert_called_once()
        call_kwargs = mock_create_app.call_args
        assert call_kwargs.kwargs.get("title") == "Marianne Dashboard" or (
            call_kwargs[1].get("title") == "Marianne Dashboard"
        )


class TestDashboardKeyboardInterrupt:
    """Test dashboard graceful shutdown on KeyboardInterrupt."""

    def test_keyboard_interrupt_handled_gracefully(self, tmp_path: Path) -> None:
        """Dashboard handles KeyboardInterrupt without traceback."""
        mock_uvicorn = MagicMock()
        mock_uvicorn.run.side_effect = KeyboardInterrupt
        mock_create_app = MagicMock(return_value=MagicMock())

        with (
            pytest.MonkeyPatch.context() as modules,
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            modules.setitem(sys.modules, "uvicorn", mock_uvicorn)
            result = runner.invoke(_app, ["dashboard", "--workspace", str(tmp_path)])

        # Should exit cleanly (0) after catching KeyboardInterrupt
        assert result.exit_code == 0


class TestMcpCommand:
    """Test MCP server command error paths."""

    def test_mcp_keyboard_interrupt_handled(self) -> None:
        """MCP command handles KeyboardInterrupt gracefully."""
        with patch(
            "marianne.cli.commands.dashboard.asyncio.run",
            side_effect=KeyboardInterrupt,
        ):
            result = runner.invoke(_app, ["mcp"])

        # Should exit cleanly after KeyboardInterrupt
        assert result.exit_code == 0


class TestDashboardCustomOptions:
    """Test that dashboard passes custom options to uvicorn."""

    def test_custom_port_and_host(self, tmp_path: Path) -> None:
        """Dashboard passes custom port and host to uvicorn."""
        mock_uvicorn = MagicMock()
        mock_create_app = MagicMock(return_value=MagicMock())

        with (
            pytest.MonkeyPatch.context() as modules,
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            modules.setitem(sys.modules, "uvicorn", mock_uvicorn)
            runner.invoke(
                _app,
                [
                    "dashboard",
                    "--port",
                    "9999",
                    "--host",
                    "0.0.0.0",
                    "--workspace",
                    str(tmp_path),
                ],
            )

        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs.kwargs.get("port") == 9999 or call_kwargs[1].get("port") == 9999


class TestExposedBindWarning:
    """GH #387: `mzt dashboard --host 0.0.0.0` under the default auth mode
    prints a red panel before the server starts; the loopback default does not."""

    def _run(self, tmp_path: Path, host: str) -> str:
        mock_uvicorn = MagicMock()
        with (
            pytest.MonkeyPatch.context() as modules,
            patch("marianne.dashboard.create_app", MagicMock(return_value=MagicMock())),
            patch.dict("os.environ", {"MZT_AUTH_MODE": "localhost_only"}),
        ):
            modules.setitem(sys.modules, "uvicorn", mock_uvicorn)
            result = runner.invoke(
                _app, ["dashboard", "--host", host, "--workspace", str(tmp_path)],
                env={"COLUMNS": "200"},
            )
        assert result.exit_code == 0, result.output
        assert mock_uvicorn.run.call_args.kwargs["host"] == host
        return result.output

    def test_network_bind_prints_the_warning_before_starting(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, "0.0.0.0")
        assert "Exposed bind" in out and "MZT_AUTH_MODE=api_key" in out
        assert out.index("Exposed bind") < out.index("Starting Server")

    def test_loopback_bind_prints_no_warning(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, "127.0.0.1")
        assert "Exposed bind" not in out
