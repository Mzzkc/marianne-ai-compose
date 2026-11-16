"""Shared temporary workspace MCP configuration for CLI execution modes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
from contextlib import suppress
from pathlib import Path

from marianne.core.config.instruments import CliProfile, InstrumentProfile
from marianne.core.logging import get_logger

_logger = get_logger("execution.workspace_mcp_config")

# Headless and interactive attempts must serialize writes to the same target.
MCP_WORKSPACE_CONFIG_LOCKS: dict[Path, asyncio.Lock] = {}


def _write_private(target: Path, data: bytes, mode: int = 0o600) -> None:
    """Write ``data`` to ``target``, owner-only by default (an MCP config can name sockets).

    ``mode`` is for restoring a file the user owned: it gets back the mode it had.
    """
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


class WorkspaceMcpConfigMixin:
    """Materialize, merge, and restore a profile's workspace-local MCP file."""

    _cli: CliProfile
    _profile: InstrumentProfile
    _working_directory: Path | None
    _mcp_config_path: Path | None

    def _workspace_mcp_config_target(self) -> Path | None:
        """Return the workspace-local MCP config target, if this profile needs one."""
        rel_path = self._cli.command.mcp_config_workspace_path
        if self._mcp_config_path is None or not rel_path:
            return None
        workspace = self._working_directory or Path.cwd()
        return workspace / rel_path

    def _workspace_mcp_config_bytes(self, target: Path) -> bytes:
        """Return bytes to write to a CLI-discovered workspace config path."""
        if self._mcp_config_path is None:
            return b""

        merge_key = self._cli.command.mcp_config_workspace_merge_key
        if not merge_key:
            return self._mcp_config_path.read_bytes()

        try:
            source_data = json.loads(self._mcp_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Generated MCP config is not valid JSON: {self._mcp_config_path}"
            ) from exc

        if not isinstance(source_data, dict):
            raise RuntimeError(
                f"Generated MCP config must be a JSON object: {self._mcp_config_path}"
            )

        try:
            target_data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        except json.JSONDecodeError:
            target_data = {}
        if not isinstance(target_data, dict):
            target_data = {}

        target_data[merge_key] = source_data.get(merge_key, {})
        return (json.dumps(target_data, indent=2) + "\n").encode("utf-8")

    def _materialize_workspace_mcp_config(
        self,
        target: Path,
    ) -> tuple[Path, bytes | None, bool, int | None] | None:
        """Materialize generated MCP config to a CLI-discovered workspace path.

        Returns a restore tuple of ``(target, previous_bytes, parent_existed,
        previous_mode)``. ``previous_bytes`` is ``None`` when no file existed and
        the file should be deleted after execution; ``previous_mode`` is the
        permission bits the user's file had, put back on restore.
        """
        if self._mcp_config_path is None:
            return None

        source = self._mcp_config_path
        if source.resolve() == target.resolve():
            return None

        parent_existed = target.parent.exists()
        previous = target.read_bytes() if target.exists() else None
        previous_mode = stat.S_IMODE(target.stat().st_mode) if previous is not None else None
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_private(target, self._workspace_mcp_config_bytes(target))

        _logger.debug(
            "plugin_cli_workspace_mcp_config_materialized",
            instrument=self._profile.name,
            source=str(source),
            target=str(target),
            merge_key=self._cli.command.mcp_config_workspace_merge_key,
            restored_existing=previous is not None,
        )
        return target, previous, parent_existed, previous_mode

    def _restore_workspace_mcp_config(
        self,
        restore: tuple[Path, bytes | None, bool, int | None],
    ) -> None:
        """Restore or remove a temporary workspace MCP config file."""
        target, previous, parent_existed, previous_mode = restore
        try:
            if previous is None:
                target.unlink(missing_ok=True)
                if not parent_existed:
                    with suppress(OSError):
                        target.parent.rmdir()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_private(target, previous, previous_mode if previous_mode is not None else 0o600)
            _logger.debug(
                "plugin_cli_workspace_mcp_config_restored",
                instrument=self._profile.name,
                target=str(target),
                restored_existing=previous is not None,
            )
        except OSError:
            _logger.warning(
                "plugin_cli_workspace_mcp_config_restore_failed",
                instrument=self._profile.name,
                target=str(target),
                exc_info=True,
            )
