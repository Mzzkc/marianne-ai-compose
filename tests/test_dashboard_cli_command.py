"""Tests for mozart.cli.commands.dashboard CLI command error paths.

Tests the dashboard and mcp CLI entry points, focusing on error handling,
state backend selection, and import fallbacks. This tests the CLI wiring,
not the FastAPI routes (those are in test_dashboard*.py).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestDashboardStateBackendSelection:
    """Test that dashboard selects the correct state backend."""

    def test_prefers_sqlite_when_db_exists(self, tmp_path: Path) -> None:
        """Dashboard uses SQLite backend when .mozart-state.db exists."""
        # Create a fake SQLite file
        db_path = tmp_path / ".mozart-state.db"
        db_path.touch()

        mock_uvicorn = MagicMock()
        mock_sqlite_cls = MagicMock()
        mock_json_cls = MagicMock()
        mock_create_app = MagicMock(return_value=MagicMock())

        with (
            patch.dict(sys.modules, {"uvicorn": mock_uvicorn}),
            patch("marianne.state.SQLiteStateBackend", mock_sqlite_cls),
            patch("marianne.state.JsonStateBackend", mock_json_cls),
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            runner.invoke(
                _app, ["dashboard", "--workspace", str(tmp_path)]
            )

        # SQLite backend should have been instantiated
        mock_sqlite_cls.assert_called_once_with(db_path)

    def test_falls_back_to_json_when_no_db(self, tmp_path: Path) -> None:
        """Dashboard uses JSON backend when no SQLite database exists."""
        mock_uvicorn = MagicMock()
        mock_sqlite_cls = MagicMock()
        mock_json_cls = MagicMock()
        mock_create_app = MagicMock(return_value=MagicMock())

        with (
            patch.dict(sys.modules, {"uvicorn": mock_uvicorn}),
            patch("marianne.state.SQLiteStateBackend", mock_sqlite_cls),
            patch("marianne.state.JsonStateBackend", mock_json_cls),
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            runner.invoke(
                _app, ["dashboard", "--workspace", str(tmp_path)]
            )

        # JSON backend should have been instantiated
        mock_json_cls.assert_called_once_with(tmp_path)


class TestDashboardKeyboardInterrupt:
    """Test dashboard graceful shutdown on KeyboardInterrupt."""

    def test_keyboard_interrupt_handled_gracefully(self, tmp_path: Path) -> None:
        """Dashboard handles KeyboardInterrupt without traceback."""
        mock_uvicorn = MagicMock()
        mock_uvicorn.run.side_effect = KeyboardInterrupt
        mock_create_app = MagicMock(return_value=MagicMock())

        with (
            patch.dict(sys.modules, {"uvicorn": mock_uvicorn}),
            patch("marianne.state.SQLiteStateBackend"),
            patch("marianne.state.JsonStateBackend"),
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            result = runner.invoke(
                _app, ["dashboard", "--workspace", str(tmp_path)]
            )

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
            patch.dict(sys.modules, {"uvicorn": mock_uvicorn}),
            patch("marianne.state.SQLiteStateBackend"),
            patch("marianne.state.JsonStateBackend"),
            patch("marianne.dashboard.create_app", mock_create_app),
        ):
            runner.invoke(
                _app,
                [
                    "dashboard",
                    "--port", "9999",
                    "--host", "0.0.0.0",
                    "--workspace", str(tmp_path),
                ],
            )

        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        assert call_kwargs.kwargs.get("port") == 9999 or call_kwargs[1].get("port") == 9999
