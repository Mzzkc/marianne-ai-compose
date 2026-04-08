"""Tests for ``mzt top`` error message standardization.

The ``top`` command was added in M1 after error standardization (step 35)
completed. Six error paths used raw console.print("[red]...") instead of
output_error(). This creates inconsistency with the rest of the CLI:
- No error codes
- No hints list
- No JSON output support
- No consistent formatting

Lens M2: Migrate top.py error paths to output_error().
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

from marianne.cli.commands import top as top_module


class TestTopTuiMissingTextual:
    """TUI mode when textual package is not installed."""

    def test_missing_textual_uses_output_error(self) -> None:
        """When textual is not installed, error should go through
        output_error() rather than raw console.print."""
        import builtins

        real_import = builtins.__import__

        def fake_import(
            name: str,
            globals: object = None,
            locals: object = None,
            fromlist: object = (),
            level: int = 0,
        ) -> object:
            if name == "marianne.tui.app":
                raise ImportError("No module named 'textual'")
            return real_import(name, globals, locals, fromlist, level)  # type: ignore[arg-type]

        with (
            patch("marianne.cli.commands.top.output_error") as mock_error,
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(typer.Exit),
        ):
            top_module._tui_mode(filter_job=None, interval=2.0)

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][0]
        assert "textual" in msg.lower()
        assert "hints" in mock_error.call_args[1]
        hints = mock_error.call_args[1]["hints"]
        assert any("pip install" in h for h in hints)


class TestTopHistoryTuiMissingTextual:
    """History TUI mode when textual package is not installed."""

    def test_history_tui_missing_textual_uses_output_error(self) -> None:
        """History TUI mode also uses output_error() for missing textual."""
        import builtins

        real_import = builtins.__import__

        def fake_import(
            name: str,
            globals: object = None,
            locals: object = None,
            fromlist: object = (),
            level: int = 0,
        ) -> object:
            if name == "marianne.tui.app":
                raise ImportError("No module named 'textual'")
            return real_import(name, globals, locals, fromlist, level)  # type: ignore[arg-type]

        with (
            patch("marianne.cli.commands.top.output_error") as mock_error,
            patch("builtins.__import__", side_effect=fake_import),
            pytest.raises(typer.Exit),
        ):
            import asyncio

            asyncio.run(
                top_module._history_tui(3600.0, filter_job=None)
            )

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][0]
        assert "textual" in msg.lower()


class TestTopNoMonitorDatabase:
    """_get_storage() when no monitor database exists."""

    def test_missing_db_uses_output_error(self, tmp_path: Path) -> None:
        """When the monitor database is missing, _get_storage() should
        use output_error() with a hint about running the conductor."""
        from marianne.daemon.profiler.models import ProfilerConfig

        fake_config = MagicMock(spec=ProfilerConfig())
        fake_config.storage_path = MagicMock(spec=Path)
        fake_config.storage_path.expanduser.return_value = (
            tmp_path / "nonexistent.db"
        )

        with (
            patch("marianne.cli.commands.top.output_error") as mock_error,
            patch(
                "marianne.daemon.profiler.models.ProfilerConfig",
                return_value=fake_config,
            ),
            pytest.raises(typer.Exit),
        ):
            top_module._get_storage()

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][0]
        assert "monitor" in msg.lower() or "database" in msg.lower()
        assert "hints" in mock_error.call_args[1]
        assert mock_error.call_args[1].get("severity") == "warning"


class TestTopNoMonitorData:
    """JSON fallback mode when no monitor data is available."""

    def test_no_jsonl_file_uses_output_error(self, tmp_path: Path) -> None:
        """When the JSONL file doesn't exist, should use output_error()."""
        from marianne.daemon.profiler.models import ProfilerConfig

        fake_config = MagicMock(spec=ProfilerConfig())
        fake_config.jsonl_path = MagicMock(spec=Path)
        fake_config.jsonl_path.expanduser.return_value = (
            tmp_path / "nonexistent.jsonl"
        )

        with (
            patch("marianne.cli.commands.top.output_error") as mock_error,
            patch(
                "marianne.daemon.profiler.models.ProfilerConfig",
                return_value=fake_config,
            ),
            pytest.raises(typer.Exit),
        ):
            import asyncio

            asyncio.run(
                top_module._json_from_jsonl(
                    filter_job=None, interval=2.0
                )
            )

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][0]
        assert "monitor" in msg.lower() or "data" in msg.lower()
        assert mock_error.call_args[1].get("severity") == "warning"


class TestTopStraceNotAvailable:
    """Trace mode when strace is not available."""

    def test_strace_missing_uses_output_error(self) -> None:
        """When strace is not installed, error should use output_error()."""
        with (
            patch("marianne.cli.commands.top.output_error") as mock_error,
            patch(
                "marianne.daemon.profiler.strace_manager.StraceManager.is_available",
                return_value=False,
            ),
            pytest.raises(typer.Exit),
        ):
            import asyncio

            asyncio.run(top_module._trace_mode(12345))

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][0]
        assert "strace" in msg.lower()
        assert "hints" in mock_error.call_args[1]


class TestTopStraceAttachFailure:
    """Trace mode when strace attach fails."""

    def test_strace_attach_failure_uses_output_error(self) -> None:
        """When strace attach fails, error should use output_error()
        with diagnostic hints."""
        with (
            patch("marianne.cli.commands.top.output_error") as mock_error,
            patch(
                "marianne.daemon.profiler.strace_manager.StraceManager.is_available",
                return_value=True,
            ),
            patch(
                "marianne.daemon.profiler.strace_manager.StraceManager.__init__",
                return_value=None,
            ),
            patch(
                "marianne.daemon.profiler.strace_manager.StraceManager.attach_full_trace",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("marianne.cli.commands.top.console"),
            pytest.raises(typer.Exit),
        ):
            import asyncio

            asyncio.run(top_module._trace_mode(99999))

        mock_error.assert_called_once()
        msg = mock_error.call_args[0][0]
        assert "failed" in msg.lower() or "attach" in msg.lower()
        assert "hints" in mock_error.call_args[1]
        hints = mock_error.call_args[1]["hints"]
        assert any("permission" in h.lower() or "not found" in h.lower() for h in hints)
