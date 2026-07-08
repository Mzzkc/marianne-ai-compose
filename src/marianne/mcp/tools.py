"""Marianne MCP tools for conductor records, controls, and artifacts.

This module implements MCP tools that expose conductor-managed Marianne score
execution to external AI agents. Tools are organized by category:

- JobTools: submitted-score lifecycle management (list, get, start)
- ControlTools: conductor control operations (pause, resume, cancel)
- ArtifactTools: Workspace and artifact management

Each tool follows the MCP specification for parameter schemas and return values.
Tools leverage the existing JobControlService for consistent behavior with the dashboard.
"""

import asyncio
import logging
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.log_sources import LogSource, discover_job_log_sources
from ..daemon.detect import _resolve_socket_path
from ..daemon.exceptions import DaemonNotRunningError
from ..daemon.ipc.client import DaemonClient
from ..dashboard.services.job_control import JobControlService
from ..state.base import StateBackend

logger = logging.getLogger(__name__)

MCP_LOG_STREAM_ONLY_BYTES = 50 * 1024 * 1024


def _make_error_response(error: Exception) -> dict[str, Any]:
    """Create a standardized MCP error response."""
    return {
        "content": [{"type": "text", "text": f"Error: {error}"}],
        "isError": True,
    }


class JobTools:
    """Marianne submitted-score lifecycle management tools.

    Provides MCP tools for running, monitoring, and querying submitted scores.
    Tools require explicit user consent due to file system and process execution.

    Routes through the conductor for all operations.
    """

    def __init__(self, state_backend: StateBackend, workspace_root: Path):
        self.state_backend = state_backend
        self._daemon_client = DaemonClient(_resolve_socket_path(None))
        self.job_control = JobControlService(self._daemon_client)

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all submitted-score management tools."""
        return [
            {
                "name": "list_jobs",
                "description": (
                    "List conductor records for submitted Marianne scores with current status"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "status_filter": {
                            "type": "string",
                            "description": (
                                "Filter submitted scores by status "
                                "(running, paused, completed, failed, cancelled)"
                            ),
                            "enum": ["running", "paused", "completed", "failed", "cancelled"],
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of jobs to return",
                            "default": 50,
                            "minimum": 1,
                            "maximum": 500,
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "get_job",
                "description": (
                    "Get detailed conductor state for a submitted Marianne score"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": (
                                "Conductor runtime identifier for the submitted score"
                            ),
                        }
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "start_job",
                "description": (
                    "Submit a Marianne score YAML file to the conductor. "
                    "Set confirm_fresh=true when fresh=true because fresh clears "
                    "existing score state."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "config_path": {
                            "type": "string",
                            "description": "Path to the Marianne score YAML file (.yaml/.yml)",
                        },
                        "workspace": {
                            "type": "string",
                            "description": "Workspace directory for score execution (optional)",
                        },
                        "client_cwd": {
                            "type": "string",
                            "description": (
                                "Client working directory for resolving relative score paths"
                            ),
                        },
                        "start_sheet": {
                            "type": "integer",
                            "description": "Sheet number to start from (1-indexed)",
                            "default": 1,
                            "minimum": 1,
                        },
                        "self_healing": {
                            "type": "boolean",
                            "description": "Enable self-healing mode for automatic error recovery",
                            "default": False,
                        },
                        "self_healing_auto_confirm": {
                            "type": "boolean",
                            "description": "Auto-confirm self-healing fixes",
                            "default": False,
                        },
                        "escalation": {
                            "type": "boolean",
                            "description": "Pause for composer decision on exhaustion",
                            "default": False,
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Validate without executing when supported",
                            "default": False,
                        },
                        "fresh": {
                            "type": "boolean",
                            "description": "Clear existing score state before submitting",
                            "default": False,
                        },
                        "confirm_fresh": {
                            "type": "boolean",
                            "description": "Required when fresh is true",
                            "default": False,
                        },
                        "chain_depth": {
                            "type": "integer",
                            "description": "Concert chain depth for chained submissions",
                            "minimum": 0,
                        },
                        "runtime_variables": {
                            "type": "object",
                            "description": "Per-invocation template variables",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["config_path"],
                },
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a job management tool."""
        try:
            if name == "list_jobs":
                return await self._list_jobs(arguments)
            elif name == "get_job":
                return await self._get_job(arguments)
            elif name == "start_job":
                return await self._start_job(arguments)
            else:
                raise ValueError(f"Unknown job tool: {name}")

        except (
            KeyError,
            ValueError,
            FileNotFoundError,
            RuntimeError,
            OSError,
            ConnectionError,
        ) as e:
            logger.exception("Error executing tool %s", name)
            return _make_error_response(e)

    async def _list_jobs(self, args: dict[str, Any]) -> dict[str, Any]:
        """List all jobs with status information.

        Routes through daemon when available for a complete job listing.
        Falls back to a limited response when daemon is not running.
        """
        status_filter = args.get("status_filter")
        limit = args.get("limit", 50)

        # Try daemon for comprehensive job listing
        try:
            if await self._daemon_client.is_daemon_running():
                jobs = await self._daemon_client.list_jobs()

                # Apply status filter
                if status_filter:
                    jobs = [j for j in jobs if j.get("status") == status_filter]

                # Apply limit
                jobs = jobs[:limit]

                result = "Marianne MCP Submitted Scores (via conductor)\n"
                result += "=" * 40 + "\n\n"

                if not jobs:
                    result += "No submitted scores found"
                    if status_filter:
                        result += f" with status '{status_filter}'"
                    result += ".\n"
                else:
                    result += f"Showing {len(jobs)} submitted score(s):\n\n"
                    for job in jobs:
                        job_id = job.get("job_id", "unknown")
                        status = job.get("status", "unknown")
                        name = job.get("job_name", job_id)
                        result += f"  [{status}] {name} (id: {job_id})\n"

                return {"content": [{"type": "text", "text": result}]}
        except DaemonNotRunningError:
            logger.info("daemon_not_running for list_jobs")
        except (OSError, ConnectionError, TimeoutError):
            logger.warning("daemon_list_jobs_failed", exc_info=True)

        # Fallback: daemon not available
        result = "Marianne MCP Submitted Scores\n"
        result += "=" * 40 + "\n\n"

        if status_filter:
            result += f"Filter: {status_filter}\n"
        result += f"Limit: {limit}\n\n"

        result += "Note: Full submitted-score listing requires the Marianne conductor.\n"
        result += "Start the conductor for comprehensive job tracking:\n"
        result += "  mzt start\n\n"
        result += "Without conductor, use get_job with a specific job_id,\n"
        result += "or the Marianne CLI: mzt list [--status running]\n"

        return {"content": [{"type": "text", "text": result}]}

    async def _get_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get detailed information about a specific job."""
        job_id = args["job_id"]

        # Load job state
        state = await self.state_backend.load(job_id)
        if not state:
            raise FileNotFoundError(f"Job not found: {job_id}")

        # Get process health information
        health = await self.job_control.verify_process_health(job_id)

        # Format detailed job information
        result = f"Marianne Submitted Score Details: {job_id}\n"
        result += "=" * (23 + len(job_id)) + "\n\n"

        # Basic job information
        result += f"Score Name: {state.job_name}\n"
        result += f"Status: {state.status.value}\n"
        result += f"Started: {state.started_at}\n"

        if state.completed_at:
            result += f"Completed: {state.completed_at}\n"

        if state.error_message:
            result += f"Last Error: {state.error_message}\n"

        # Process information
        result += "\nProcess Information:\n"
        result += f"PID: {health.pid or 'None'}\n"
        result += f"Is Alive: {health.is_alive}\n"
        result += f"Is Zombie: {health.is_zombie_state}\n"

        if health.uptime_seconds:
            result += f"Uptime: {health.uptime_seconds:.1f} seconds\n"
        if health.cpu_percent is not None:
            result += f"CPU: {health.cpu_percent:.1f}%\n"
        if health.memory_mb is not None:
            result += f"Memory: {health.memory_mb:.1f} MB\n"

        # Sheet progress
        total_sheets = len(state.sheets)
        completed_sheets = len([s for s in state.sheets.values() if s.status.value == "completed"])
        result += f"\nProgress: {completed_sheets}/{total_sheets} sheets completed\n"

        # Recent sheets
        if state.sheets:
            result += "\nRecent Sheets:\n"
            recent_sheets = sorted(state.sheets.items(), key=lambda x: int(x[0]), reverse=True)[:5]
            for sheet_num, sheet in recent_sheets:
                result += f"  Sheet {sheet_num}: {sheet.status.value}"
                if sheet.error_message:
                    result += f" (Error: {sheet.error_message[:50]}...)"
                result += "\n"

        return {"content": [{"type": "text", "text": result}]}

    async def _start_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Submit a new Marianne score."""
        config_path = Path(args["config_path"])
        workspace = Path(args["workspace"]) if args.get("workspace") else None
        client_cwd = Path(args["client_cwd"]) if args.get("client_cwd") else None
        start_sheet = args.get("start_sheet", 1)
        self_healing = args.get("self_healing", False)
        self_healing_auto_confirm = args.get("self_healing_auto_confirm", False)
        escalation = args.get("escalation", False)
        dry_run = args.get("dry_run", False)
        fresh = args.get("fresh", False)
        confirm_fresh = args.get("confirm_fresh", False)
        chain_depth = args.get("chain_depth")
        runtime_variables = args.get("runtime_variables") or {}

        if fresh and not confirm_fresh:
            raise ValueError(
                "fresh=true clears existing score state and requires confirm_fresh=true"
            )

        if not config_path.exists():
            raise FileNotFoundError(f"Score file not found: {config_path}")

        # Submit the score using the conductor-backed control service.
        try:
            result = await self.job_control.start_job(
                config_path=config_path,
                workspace=workspace,
                start_sheet=start_sheet,
                fresh=fresh,
                self_healing=self_healing,
                self_healing_auto_confirm=self_healing_auto_confirm,
                escalation=escalation,
                dry_run=dry_run,
                chain_depth=chain_depth,
                client_cwd=client_cwd,
                runtime_variables=runtime_variables,
            )

            via = "conductor IPC" if result.via_daemon else "subprocess"
            response_text = "Marianne score submitted to the conductor.\n\n"
            response_text += f"Job ID: {result.job_id}\n"
            response_text += f"Score Name: {result.job_name}\n"
            response_text += f"Status: {result.status}\n"
            response_text += f"Workspace: {result.workspace}\n"
            response_text += f"Total Sheets: {result.total_sheets}\n"
            response_text += f"Via: {via}\n"

            if result.pid:
                response_text += f"Process ID: {result.pid}\n"

            if start_sheet > 1:
                response_text += f"Starting from sheet: {start_sheet}\n"

            if self_healing:
                response_text += "Self-healing: Enabled\n"
            if dry_run:
                response_text += "Dry run: Requested\n"
            if fresh:
                response_text += "Fresh state: Confirmed\n"

            response_text += f"\nUse get_job tool with job_id '{result.job_id}' to check progress."

            return {"content": [{"type": "text", "text": response_text}]}

        except (FileNotFoundError, ValueError, RuntimeError, OSError) as e:
            logger.exception("Failed to submit score from %s", config_path)
            raise RuntimeError(f"Failed to submit score: {e}") from e

    async def shutdown(self) -> None:
        """Cleanup job tools."""
        pass  # No persistent resources to cleanup


class ControlTools:
    """Marianne conductor control tools.

    Provides MCP tools for controlling conductor records for submitted scores.
    These tools interact with running score processes and require user consent.

    Routes through the conductor for all operations.
    """

    def __init__(self, state_backend: StateBackend, workspace_root: Path):
        self.state_backend = state_backend
        daemon_client = DaemonClient(_resolve_socket_path(None))
        self.job_control = JobControlService(daemon_client)

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all job control tools."""
        return [
            {
                "name": "pause_job",
                "description": (
                    "Pause a running Marianne score gracefully at a sheet boundary"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": (
                                "Conductor runtime identifier for the submitted score"
                            ),
                        }
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "resume_job",
                "description": "Resume a paused Marianne score",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": (
                                "Conductor runtime identifier for the submitted score"
                            ),
                        }
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "cancel_job",
                "description": "Cancel a running Marianne score permanently",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": (
                                "Conductor runtime identifier for the submitted score"
                            ),
                        }
                    },
                    "required": ["job_id"],
                },
            },
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a job control tool."""
        try:
            if name == "pause_job":
                return await self._pause_job(arguments)
            elif name == "resume_job":
                return await self._resume_job(arguments)
            elif name == "cancel_job":
                return await self._cancel_job(arguments)
            else:
                raise ValueError(f"Unknown control tool: {name}")

        except (KeyError, ValueError, RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error executing control tool %s", name)
            return _make_error_response(e)

    async def _pause_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Pause a running job using graceful signal-based mechanism."""
        job_id = args["job_id"]

        try:
            result = await self.job_control.pause_job(job_id)
            via = "daemon" if result.via_daemon else "local"

            if result.success:
                response_text = f"✓ Pause request sent for submitted score: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Message: {result.message}\n"
                response_text += f"Via: {via}\n\n"
                response_text += (
                    "The score will pause gracefully at the next sheet boundary."
                )
            else:
                response_text = f"✗ Failed to pause submitted score: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Error: {result.message}"

            return {"content": [{"type": "text", "text": response_text}]}

        except (RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error pausing job %s", job_id)
            raise RuntimeError(f"Failed to pause submitted score: {e}") from e

    async def _resume_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused job."""
        job_id = args["job_id"]

        try:
            result = await self.job_control.resume_job(job_id)
            via = "daemon" if result.via_daemon else "local"

            if result.success:
                response_text = f"✓ Submitted score resumed successfully: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Message: {result.message}\n"
                response_text += f"Via: {via}"
            else:
                response_text = f"✗ Failed to resume submitted score: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Error: {result.message}"

            return {"content": [{"type": "text", "text": response_text}]}

        except (RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error resuming job %s", job_id)
            raise RuntimeError(f"Failed to resume submitted score: {e}") from e

    async def _cancel_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Cancel a running job permanently."""
        job_id = args["job_id"]

        try:
            result = await self.job_control.cancel_job(job_id)
            via = "daemon" if result.via_daemon else "local"

            if result.success:
                response_text = f"✓ Submitted score cancelled successfully: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Message: {result.message}\n"
                response_text += f"Via: {via}\n\n"
                response_text += "Note: This action is permanent and cannot be undone."
            else:
                response_text = f"✗ Failed to cancel submitted score: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Error: {result.message}"

            return {"content": [{"type": "text", "text": response_text}]}

        except (RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error cancelling job %s", job_id)
            raise RuntimeError(f"Failed to cancel submitted score: {e}") from e

    async def shutdown(self) -> None:
        """Cleanup control tools."""
        pass


# Artifact tool schemas — extracted from ArtifactTools.list_tools() for readability.
# Each constant defines the MCP tool specification (name, description, inputSchema).
_ARTIFACT_LIST_SCHEMA: dict[str, Any] = {
    "name": "marianne_artifact_list",
    "description": "List files in a Marianne workspace",
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace directory to browse",
            },
            "path": {
                "type": "string",
                "description": "Subdirectory path within workspace",
                "default": ".",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden files and directories",
                "default": False,
            },
        },
        "required": ["workspace"],
    },
}

_ARTIFACT_READ_SCHEMA: dict[str, Any] = {
    "name": "marianne_artifact_read",
    "description": "Read content of a file in the workspace",
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace directory",
            },
            "file_path": {
                "type": "string",
                "description": "Path to the file within workspace",
            },
            "max_size": {
                "type": "integer",
                "description": "Maximum file size to read in bytes",
                "default": 50000,
                "maximum": 100000,
            },
            "encoding": {
                "type": "string",
                "description": "Text encoding to use",
                "default": "utf-8",
            },
        },
        "required": ["workspace", "file_path"],
    },
}

_ARTIFACT_GET_LOGS_SCHEMA: dict[str, Any] = {
    "name": "marianne_artifact_get_logs",
    "description": "Get logs from a submitted Marianne score",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Conductor runtime identifier for the submitted score",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace directory (optional, will auto-detect if not provided)",
            },
            "lines": {
                "type": "integer",
                "description": "Number of recent lines to return",
                "default": 100,
                "minimum": 1,
                "maximum": 10000,
            },
            "level": {
                "type": "string",
                "description": "Log level filter",
                "enum": ["debug", "info", "warning", "error", "all"],
                "default": "all",
            },
        },
        "required": ["job_id"],
    },
}

_ARTIFACT_LIST_ARTIFACTS_SCHEMA: dict[str, Any] = {
    "name": "marianne_artifact_list_artifacts",
    "description": "List all artifacts created by a submitted Marianne score",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Conductor runtime identifier for the submitted score",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace directory (optional, will auto-detect if not provided)",
            },
            "sheet_filter": {
                "type": "integer",
                "description": "Filter artifacts by sheet number",
                "minimum": 1,
            },
            "artifact_type": {
                "type": "string",
                "description": "Filter by artifact type",
                "enum": ["output", "error", "log", "state", "all"],
                "default": "all",
            },
        },
        "required": ["job_id"],
    },
}

_ARTIFACT_GET_ARTIFACT_SCHEMA: dict[str, Any] = {
    "name": "marianne_artifact_get_artifact",
    "description": "Get a specific artifact from a submitted Marianne score",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Conductor runtime identifier for the submitted score",
            },
            "artifact_path": {
                "type": "string",
                "description": "Relative path to the artifact within the job workspace",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace directory (optional, will auto-detect if not provided)",
            },
            "max_size": {
                "type": "integer",
                "description": "Maximum artifact size to read in bytes",
                "default": 100000,
                "maximum": 1000000,
            },
        },
        "required": ["job_id", "artifact_path"],
    },
}

_ARTIFACT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _ARTIFACT_LIST_SCHEMA,
    _ARTIFACT_READ_SCHEMA,
    _ARTIFACT_GET_LOGS_SCHEMA,
    _ARTIFACT_LIST_ARTIFACTS_SCHEMA,
    _ARTIFACT_GET_ARTIFACT_SCHEMA,
]


class ArtifactTools:
    """Marianne artifact and workspace management tools.

    Provides MCP tools for browsing workspace files and accessing job artifacts.
    File system access is restricted to designated workspace directories.
    """

    _LOG_LEVEL_PATTERNS: dict[str, re.Pattern[str]] = {
        "debug": re.compile(r"DEBUG|debug", re.IGNORECASE),
        "info": re.compile(r"INFO|info", re.IGNORECASE),
        "warning": re.compile(r"WARNING|warning|WARN|warn", re.IGNORECASE),
        "error": re.compile(r"ERROR|error|FAIL|fail", re.IGNORECASE),
    }

    def __init__(self, workspace_root: Path, state_backend: StateBackend | None = None):
        self.workspace_root = workspace_root
        self.state_backend = state_backend
        self._custom_level_cache: dict[str, re.Pattern[str]] = {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all artifact management tools."""
        return list(_ARTIFACT_TOOL_SCHEMAS)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an artifact management tool."""
        dispatch = {
            "marianne_artifact_list": self._list_files,
            "marianne_artifact_read": self._read_file,
            "marianne_artifact_get_logs": self._get_logs,
            "marianne_artifact_list_artifacts": self._list_artifacts,
            "marianne_artifact_get_artifact": self._get_artifact,
        }
        handler = dispatch.get(name)
        if handler is None:
            return {
                "content": [{"type": "text", "text": f"Error: Unknown artifact tool: {name}"}],
                "isError": True,
            }
        try:
            return await handler(arguments)
        except (
            KeyError,
            ValueError,
            FileNotFoundError,
            IsADirectoryError,
            NotADirectoryError,
            PermissionError,
            OSError,
        ) as e:
            logger.exception("Error executing artifact tool %s", name)
            return _make_error_response(e)

    def _validate_workspace_path(self, workspace: Path, target: Path) -> tuple[Path, Path]:
        """Validate that target is within workspace and workspace is within workspace_root.

        Returns resolved (workspace, target) paths.
        Raises PermissionError if path escapes allowed boundaries.
        """
        target = target.resolve()
        workspace = workspace.resolve()
        workspace_root = self.workspace_root.resolve()
        try:
            workspace.relative_to(workspace_root)
        except ValueError:
            raise PermissionError("Access denied: Workspace outside allowed root") from None
        try:
            target.relative_to(workspace)
        except ValueError:
            raise PermissionError("Access denied: Path outside workspace") from None
        return workspace, target

    async def _list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        """List files in workspace."""
        workspace = Path(args["workspace"])
        subpath = args.get("path", ".")
        include_hidden = args.get("include_hidden", False)

        target_dir = workspace / subpath

        # Security: Ensure we stay within workspace and workspace_root
        workspace, target_dir = self._validate_workspace_path(workspace, target_dir)

        if not target_dir.exists():
            raise FileNotFoundError(f"Directory not found: {target_dir}")

        if not target_dir.is_dir():
            raise NotADirectoryError(f"Not a directory: {target_dir}")

        # List directory contents
        entries = []
        total_files = 0
        total_dirs = 0
        total_size = 0

        for item in sorted(target_dir.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            # Skip hidden files unless requested
            if not include_hidden and item.name.startswith("."):
                continue

            if item.is_dir():
                total_dirs += 1
                entries.append(f"📁 {item.name}/")
            else:
                total_files += 1
                size = item.stat().st_size
                total_size += size
                size_str = self._format_size(size)
                entries.append(f"📄 {item.name} ({size_str})")

        total_size_str = self._format_size(total_size)

        result = f"Contents of {target_dir}:\n"
        result += f"Summary: {total_files} files, {total_dirs} directories, {total_size_str}\n\n"
        if entries:
            result += "\n".join(entries)
        else:
            result += "(empty directory)"

        return {"content": [{"type": "text", "text": result}]}

    async def _read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        """Read file content."""
        workspace = Path(args["workspace"])
        file_path = args["file_path"]
        max_size = args.get("max_size", 50000)
        encoding = args.get("encoding", "utf-8")

        target_file = workspace / file_path

        # Security: Ensure we stay within workspace and workspace_root
        workspace, target_file = self._validate_workspace_path(workspace, target_file)

        if not target_file.exists():
            raise FileNotFoundError(f"File not found: {target_file}")

        if not target_file.is_file():
            raise IsADirectoryError(f"Not a file: {target_file}")

        # Check file size
        file_size = target_file.stat().st_size
        if file_size > max_size:
            raise ValueError(f"File too large: {file_size} bytes (max {max_size})")

        # Read file content in a thread to avoid blocking the event loop
        def _sync_read_file() -> str:
            try:
                with open(target_file, encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                for alt_encoding in ["latin-1", "cp1252"]:
                    try:
                        with open(target_file, encoding=alt_encoding) as f:
                            data = f.read()
                            return f"[File read with {alt_encoding} encoding]\n{data}"
                    except UnicodeDecodeError:
                        continue
                # Final fallback to binary representation
                with open(target_file, "rb") as f:
                    raw_data = f.read()[:1000]  # First 1KB only
                    return f"Binary file: {file_size} bytes\nFirst 1KB (hex): {raw_data.hex()}"

        content = await asyncio.to_thread(_sync_read_file)

        size_str = self._format_size(file_size)

        result = f"📄 File: {target_file.name}\n"
        result += f"Size: {size_str}\n"
        result += f"Encoding: {encoding}\n\n"
        result += f"Content:\n{'-' * 40}\n{content}"

        return {"content": [{"type": "text", "text": result}]}

    async def _get_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get bounded logs from a submitted score's workspace."""
        job_id = args["job_id"]
        workspace = args.get("workspace")
        lines = max(1, min(int(args.get("lines", 100)), 10000))
        level = args.get("level", "all")

        state = await self.state_backend.load(job_id) if self.state_backend is not None else None
        log_sources: list[LogSource] = []
        if state is not None:
            log_sources = discover_job_log_sources(job_id, state)

        if not workspace and not log_sources:
            workspace = self._find_job_workspace(job_id)

        workspace_path = Path(workspace) if workspace else None
        if workspace_path is not None and not workspace_path.exists() and not log_sources:
            text = (
                f"Logs for submitted score: {job_id}\n"
                f"Workspace: {workspace_path}\n"
                "State: unavailable\n"
                "Workspace was not found, so no log source could be inspected.\n"
            )
            return {"content": [{"type": "text", "text": text}]}

        if not log_sources and workspace_path is not None:
            workspace_path, _ = self._validate_workspace_path(workspace_path, workspace_path)
            log_sources = [
                LogSource(
                    path=path,
                    label=label,
                    kind="workspace",
                    alias_state="compatibility",
                )
                for label, path in self._discover_log_sources(workspace_path, job_id)
            ]

        total_size = sum(source.path.stat().st_size for source in log_sources)
        stream_only = total_size > MCP_LOG_STREAM_ONLY_BYTES

        parts = [
            f"Logs for submitted score: {job_id}\n",
            f"Workspace: {workspace_path or 'resolved from conductor state'}\n",
            f"Lines requested: {lines}, Level filter: {level}\n",
            f"Sources found: {len(log_sources)}\n",
        ]
        if stream_only:
            parts.append(
                "State: stream-only for full download; returning bounded tails only "
                f"because sources total {self._format_size(total_size)}.\n"
            )
        elif log_sources:
            parts.append("State: available\n")
        else:
            parts.append(
                "State: no-sources\n"
                "No log source was found. This is different from an empty log file.\n"
            )
            return {"content": [{"type": "text", "text": "".join(parts)}]}
        parts.append("=" * 60 + "\n\n")

        level_regex: re.Pattern[str] | None = None
        if level != "all":
            level_regex = self._LOG_LEVEL_PATTERNS.get(level.lower())
            if level_regex is None:
                level_key = level.lower()
                level_regex = self._custom_level_cache.get(level_key)
                if level_regex is None:
                    level_regex = re.compile(re.escape(level), re.IGNORECASE)
                    self._custom_level_cache[level_key] = level_regex

        for source in log_sources:
            try:
                log_file = source.path
                file_size = log_file.stat().st_size
                parts.append(f"Source: {source.label}\n")
                parts.append(f"Kind: {source.kind}\n")
                parts.append(f"Alias state: {source.alias_state}\n")
                parts.append(f"Raw state: {source.raw_state}\n")
                parts.append(f"Path: {log_file}\n")
                parts.append(f"Size: {self._format_size(file_size)}\n")

                recent_lines, matched_lines = await asyncio.to_thread(
                    self._read_bounded_log_tail,
                    log_file,
                    lines,
                    level_regex,
                    source.job_filter,
                )

                if recent_lines:
                    parts.append(
                        f"State: showing last {len(recent_lines)} of "
                        f"{matched_lines} matching line(s)\n"
                    )
                    parts.append("-" * 40 + "\n")
                    parts.append("".join(recent_lines))
                elif file_size == 0:
                    parts.append("State: no-lines\n")
                else:
                    parts.append(f"State: no matching {level} lines\n")

                parts.append("\n" + "-" * 40 + "\n\n")

            except (OSError, UnicodeDecodeError) as e:
                parts.append(f"Error reading {log_file}: {e}\n\n")

        return {"content": [{"type": "text", "text": "".join(parts)}]}

    def _discover_log_sources(self, workspace_path: Path, job_id: str) -> list[tuple[str, Path]]:
        """Find candidate log sources without reading their contents."""
        candidates: list[tuple[str, Path]] = [
            ("job log", workspace_path / f"{job_id}.log"),
            ("workspace/marianne.log", workspace_path / "marianne.log"),
            ("workspace/logs/marianne.log", workspace_path / "logs" / "marianne.log"),
            ("runner.log", workspace_path / "runner.log"),
            ("workspace observer events", workspace_path / ".marianne-observer.jsonl"),
        ]
        for pattern, label in (("*.log", "workspace log"), ("logs/*.log", "workspace logs")):
            for log_file in sorted(workspace_path.glob(pattern)):
                candidates.append((f"{label}: {log_file.name}", log_file))

        seen: set[Path] = set()
        sources: list[tuple[str, Path]] = []
        for label, path in candidates:
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            sources.append((label, resolved))
        return sources

    @staticmethod
    def _read_bounded_log_tail(
        path: Path,
        lines: int,
        level_regex: re.Pattern[str] | None,
        job_filter: str | None = None,
    ) -> tuple[list[str], int]:
        """Read a filtered log tail without loading the whole file into memory."""
        recent: deque[str] = deque(maxlen=lines)
        matched = 0
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if job_filter is not None and job_filter not in line:
                    continue
                if level_regex is not None and not level_regex.search(line):
                    continue
                matched += 1
                recent.append(line)
        return list(recent), matched

    async def _list_artifacts(self, args: dict[str, Any]) -> dict[str, Any]:
        """List all artifacts created by a Marianne job."""
        job_id = args["job_id"]
        workspace = args.get("workspace")
        sheet_filter = args.get("sheet_filter")
        artifact_type = args.get("artifact_type", "all")

        # Find the workspace if not provided
        if not workspace:
            workspace = self._find_job_workspace(job_id)

        workspace_path = Path(workspace)

        if not workspace_path.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace_path}")

        # Security: Ensure workspace is within allowed root
        workspace_path, _ = self._validate_workspace_path(workspace_path, workspace_path)

        result = f"🎯 Artifacts for Marianne Job: {job_id}\n"
        result += f"Workspace: {workspace_path}\n"
        if sheet_filter:
            result += f"Sheet filter: {sheet_filter}\n"
        result += f"Type filter: {artifact_type}\n"
        result += "=" * 60 + "\n\n"

        # Categorize artifacts
        artifacts: dict[str, list[Any]] = {
            "output": [],
            "error": [],
            "log": [],
            "state": [],
            "other": [],
        }

        # Scan workspace for files
        for item in workspace_path.rglob("*"):
            if not item.is_file():
                continue

            rel_path = item.relative_to(workspace_path)

            # Apply sheet filter early to skip non-matching files
            if sheet_filter:
                escaped = re.escape(str(sheet_filter))
                pattern = rf"sheet[_-]?{escaped}|{escaped}[_-]sheet"
                if not re.search(pattern, str(rel_path), re.IGNORECASE):
                    continue

            stat = item.stat()
            category = self._categorize_artifact(item)
            artifacts[category].append(
                {
                    "path": str(rel_path),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime),
                    "category": category,
                }
            )

        # Format results by category
        if artifact_type == "all":
            categories_to_show = list(artifacts.keys())
        else:
            categories_to_show = [artifact_type] if artifact_type in artifacts else []

        total_artifacts = 0
        for category in categories_to_show:
            items = artifacts[category]
            if items:
                result += f"📂 {category.upper()} Artifacts ({len(items)} items):\n"
                result += "-" * 40 + "\n"

                # Sort by modification time (newest first)
                items.sort(key=lambda x: x["modified"], reverse=True)

                for artifact in items:
                    size_str = self._format_size(artifact["size"])
                    mod_time = artifact["modified"].strftime("%Y-%m-%d %H:%M:%S")
                    result += f"  📄 {artifact['path']} ({size_str}, {mod_time})\n"
                    total_artifacts += 1

                result += "\n"

        if total_artifacts == 0:
            result += "No artifacts found matching the specified criteria.\n"
        else:
            result += f"\n📊 Total artifacts found: {total_artifacts}"

        return {"content": [{"type": "text", "text": result}]}

    async def _get_artifact(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get a specific artifact from a Marianne job."""
        job_id = args["job_id"]
        artifact_path = args["artifact_path"]
        workspace = args.get("workspace")
        max_size = args.get("max_size", 100000)

        # Find the workspace if not provided
        if not workspace:
            workspace = self._find_job_workspace(job_id)

        workspace_path = Path(workspace)
        target_artifact = workspace_path / artifact_path

        # Security: Ensure we stay within workspace and workspace_root
        workspace_path, target_artifact = self._validate_workspace_path(
            workspace_path, target_artifact
        )

        if not target_artifact.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_path}")

        if not target_artifact.is_file():
            raise IsADirectoryError(f"Not a file: {artifact_path}")

        # Check file size
        file_size = target_artifact.stat().st_size
        if file_size > max_size:
            raise ValueError(f"Artifact too large: {file_size} bytes (max {max_size})")

        # Get file metadata
        stat = target_artifact.stat()
        modified = datetime.fromtimestamp(stat.st_mtime)
        created = datetime.fromtimestamp(stat.st_ctime)

        result = f"🎯 Marianne Job Artifact: {job_id}\n"
        result += f"Artifact: {artifact_path}\n"
        result += f"Size: {self._format_size(file_size)}\n"
        result += f"Modified: {modified.strftime('%Y-%m-%d %H:%M:%S')}\n"
        result += f"Created: {created.strftime('%Y-%m-%d %H:%M:%S')}\n"
        result += "=" * 60 + "\n\n"

        # Read content based on file type
        try:
            if target_artifact.suffix.lower() in [".json", ".yaml", ".yml", ".txt", ".md", ".log"]:
                # Text files
                with open(target_artifact, encoding="utf-8") as f:
                    content = f.read()
                result += f"Content:\n{'-' * 40}\n{content}"
            else:
                # Binary files - show hex dump
                with open(target_artifact, "rb") as f:
                    raw_data = f.read()
                    if len(raw_data) <= 1000:  # Small binary files
                        result += f"Binary Content (hex):\n{'-' * 40}\n{raw_data.hex()}"
                    else:
                        result += f"Large Binary File:\n{'-' * 40}\n"
                        result += f"First 1KB (hex): {raw_data[:1000].hex()}\n"
                        result += f"... ({len(raw_data)} total bytes)"
        except (OSError, UnicodeDecodeError) as e:
            result += f"Error reading artifact content: {e}"

        return {"content": [{"type": "text", "text": result}]}

    @staticmethod
    def _categorize_artifact(item: Path) -> str:
        """Categorize an artifact file by its name and extension."""
        if item.suffix == ".log":
            return "log"
        if item.suffix == ".json" and ("state" in item.name or "checkpoint" in item.name):
            return "state"
        name_lower = item.name.lower()
        if "error" in name_lower or "stderr" in name_lower:
            return "error"
        if "output" in name_lower or "stdout" in name_lower:
            return "output"
        return "other"

    def _find_job_workspace(self, job_id: str) -> str:
        """Find workspace directory for a job ID.

        Post-#50 the conductor registry is the source of truth for job
        state; workspaces hold WORK only. This finder serves artifact
        browsing, so it probes for the job's directory — never for state
        files (the old ``{job_id}.json`` / ``*.json`` probes matched a
        state format nothing writes anymore; #50 residual, audit P2).
        """
        # Job-specific directory candidates, most specific first.
        candidates = [
            self.workspace_root / job_id,
            self.workspace_root / f"{job_id}-workspace",
            self.workspace_root / "workspace" / job_id,
        ]
        for ws in candidates:
            if ws.is_dir():
                return str(ws)

        # The job may run directly in the root workspace — accept it only
        # when a Marianne artifact confirms work happened there.
        if (self.workspace_root / "marianne.log").exists():
            return str(self.workspace_root)

        # Default to job_id as workspace name
        return str(self.workspace_root / job_id)

    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"

    async def shutdown(self) -> None:
        """Cleanup artifact tools."""
        pass


class ScoreTools:
    """Marianne code quality score tools.

    Provides MCP tools for validating and generating quality scores for code changes
    using Marianne's AI-powered review system. Tools analyze git diffs and provide
    detailed feedback on code quality, test coverage, security, and documentation.
    """

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all score management tools.

        Returns empty list because validate_score and generate_score are stub
        implementations. Registering stubs misleads MCP clients into expecting
        working functionality. Re-enable when backend integration is complete.
        """
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a score tool."""
        try:
            if name == "validate_score":
                return await self._validate_score(arguments)
            elif name == "generate_score":
                return await self._generate_score(arguments)
            else:
                raise ValueError(f"Unknown score tool: {name}")

        except (KeyError, ValueError, FileNotFoundError, PermissionError, OSError) as e:
            logger.exception("Error executing score tool %s", name)
            return _make_error_response(e)

    async def _validate_score(self, args: dict[str, Any]) -> dict[str, Any]:
        """Validate code changes meet quality score thresholds."""
        workspace = Path(args["workspace"])
        min_score = args.get("min_score", 60)
        target_score = args.get("target_score", 80)
        since_commit = args.get("since_commit")

        if not workspace.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace}")

        # Security: Ensure workspace is within allowed root
        try:
            workspace = workspace.resolve()
            workspace_root = self.workspace_root.resolve()
            workspace.relative_to(workspace_root)
        except ValueError:
            raise PermissionError("Access denied: Workspace outside allowed root") from None

        # Note: This is a stub implementation.
        # Full implementation would require quality-score integration.
        result_text = f"🎯 Quality Score Validation: {workspace.name}\n"
        result_text += f"Workspace: {workspace}\n"
        result_text += f"Min Score: {min_score}/100\n"
        result_text += f"Target Score: {target_score}/100\n"
        if since_commit:
            result_text += f"Since Commit: {since_commit}\n"
        result_text += "=" * 60 + "\n\n"

        result_text += "⚠️  STUB IMPLEMENTATION\n"
        result_text += (
            "This compatibility endpoint is hidden from MCP discovery until "
            "quality-score integration is implemented.\n"
        )
        result_text += "The validate_score tool would:\n\n"
        result_text += "1. Initialize the quality reviewer with instrument settings\n"
        result_text += "2. Get git diff using GitDiffProvider\n"
        result_text += "3. Execute AI review to generate quality score\n"
        result_text += "4. Evaluate score against min_score and target_score\n"
        result_text += "5. Return validation result with pass/fail status\n\n"
        result_text += "Score components analyzed:\n"
        result_text += "• Code Quality (30%): Complexity, patterns, readability\n"
        result_text += "• Test Coverage (25%): New code tested, edge cases\n"
        result_text += "• Security (25%): No secrets, validation, safe error handling\n"
        result_text += "• Documentation (20%): APIs documented, complex logic explained\n\n"
        result_text += "To enable scoring, implement and advertise the quality-score tool."

        return {"content": [{"type": "text", "text": result_text}]}

    async def _generate_score(self, args: dict[str, Any]) -> dict[str, Any]:
        """Generate quality score for code changes."""
        workspace = Path(args["workspace"])
        since_commit = args.get("since_commit")
        detailed = args.get("detailed", False)

        if not workspace.exists():
            raise FileNotFoundError(f"Workspace not found: {workspace}")

        # Security: Ensure workspace is within allowed root
        try:
            workspace = workspace.resolve()
            workspace_root = self.workspace_root.resolve()
            workspace.relative_to(workspace_root)
        except ValueError:
            raise PermissionError("Access denied: Workspace outside allowed root") from None

        # Note: This is a stub implementation.
        # Full implementation would require quality-score integration.
        result_text = f"📊 Quality Score Generation: {workspace.name}\n"
        result_text += f"Workspace: {workspace}\n"
        if since_commit:
            result_text += f"Since Commit: {since_commit}\n"
        result_text += f"Detailed Output: {detailed}\n"
        result_text += "=" * 60 + "\n\n"

        result_text += "⚠️  STUB IMPLEMENTATION\n"
        result_text += (
            "This compatibility endpoint is hidden from MCP discovery until "
            "quality-score integration is implemented.\n"
        )
        result_text += "The generate_score tool would:\n\n"
        result_text += "1. Initialize the quality reviewer with instrument settings\n"
        result_text += "2. Get git diff using GitDiffProvider\n"
        result_text += "3. Execute AI review to analyze code changes\n"
        result_text += "4. Return detailed scoring breakdown\n\n"

        result_text += "Example output format:\n"
        result_text += "```json\n"
        result_text += "{\n"
        result_text += '  "score": 85,\n'
        result_text += '  "components": {\n'
        result_text += '    "code_quality": 26,\n'
        result_text += '    "test_coverage": 20,\n'
        result_text += '    "security": 23,\n'
        result_text += '    "documentation": 16\n'
        result_text += "  },\n"
        result_text += '  "issues": [\n'
        result_text += "    {\n"
        result_text += '      "severity": "medium",\n'
        result_text += '      "category": "documentation",\n'
        result_text += '      "description": "Complex logic lacks comments",\n'
        result_text += '      "suggestion": "Add docstring explaining algorithm"\n'
        result_text += "    }\n"
        result_text += "  ],\n"
        result_text += '  "summary": "High quality code with minor documentation gaps"\n'
        result_text += "}\n"
        result_text += "```\n\n"
        result_text += "To enable scoring, implement and advertise the quality-score tool."

        return {"content": [{"type": "text", "text": result_text}]}

    async def shutdown(self) -> None:
        """Cleanup score tools."""
        pass
