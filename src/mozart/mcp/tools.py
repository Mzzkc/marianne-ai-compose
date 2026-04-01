"""Mozart MCP Tools - Tool implementations for Mozart job management.

This module implements MCP tools that expose Mozart's job management capabilities
to external AI agents. Tools are organized by category:

- JobTools: Job lifecycle management (list, get, start)
- ControlTools: Job control operations (pause, resume, cancel)
- ArtifactTools: Workspace and artifact management

Each tool follows the MCP specification for parameter schemas and return values.
Tools leverage the existing JobControlService for consistent behavior with the dashboard.
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..daemon.detect import _resolve_socket_path
from ..daemon.exceptions import DaemonNotRunningError
from ..daemon.ipc.client import DaemonClient
from ..dashboard.services.job_control import JobControlService
from ..state.json_backend import JsonStateBackend

logger = logging.getLogger(__name__)


def _make_error_response(error: Exception) -> dict[str, Any]:
    """Create a standardized MCP error response."""
    return {
        "content": [{"type": "text", "text": f"Error: {error}"}],
        "isError": True,
    }


class JobTools:
    """Mozart job lifecycle management tools.

    Provides MCP tools for running, monitoring, and querying Mozart jobs.
    Tools require explicit user consent due to file system and process execution.

    Routes through the conductor when available, falling back to
    subprocess execution. Job start/get operations delegate to JobControlService
    which handles conductor routing. List operations use DaemonClient directly.
    """

    def __init__(self, state_backend: JsonStateBackend, workspace_root: Path):
        self.state_backend = state_backend
        self.job_control = JobControlService(state_backend, workspace_root)
        self._daemon_client = DaemonClient(_resolve_socket_path(None))

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all job management tools."""
        return [
            {
                "name": "list_jobs",
                "description": "List all Mozart jobs with their current status",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "status_filter": {
                            "type": "string",
                            "description": (
                                "Filter jobs by status"
                                " (running, paused, completed, failed, cancelled)"
                            ),
                            "enum": ["running", "paused", "completed", "failed", "cancelled"]
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of jobs to return",
                            "default": 50,
                            "minimum": 1,
                            "maximum": 500
                        }
                    },
                    "required": []
                }
            },
            {
                "name": "get_job",
                "description": "Get detailed information about a specific Mozart job",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Mozart job ID to retrieve"
                        }
                    },
                    "required": ["job_id"]
                }
            },
            {
                "name": "start_job",
                "description": "Start a new Mozart job from a configuration file",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "config_path": {
                            "type": "string",
                            "description": "Path to the Mozart job configuration file (.yaml)"
                        },
                        "workspace": {
                            "type": "string",
                            "description": "Workspace directory for job execution (optional)"
                        },
                        "start_sheet": {
                            "type": "integer",
                            "description": "Sheet number to start from (1-indexed)",
                            "default": 1,
                            "minimum": 1
                        },
                        "self_healing": {
                            "type": "boolean",
                            "description": "Enable self-healing mode for automatic error recovery",
                            "default": False
                        }
                    },
                    "required": ["config_path"]
                }
            }
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
            KeyError, ValueError, FileNotFoundError,
            RuntimeError, OSError, ConnectionError,
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

                result = "Mozart MCP Job Listing (via daemon)\n"
                result += "=" * 40 + "\n\n"

                if not jobs:
                    result += "No jobs found"
                    if status_filter:
                        result += f" with status '{status_filter}'"
                    result += ".\n"
                else:
                    result += f"Showing {len(jobs)} job(s):\n\n"
                    for job in jobs:
                        job_id = job.get("job_id", "unknown")
                        status = job.get("status", "unknown")
                        name = job.get("job_name", job_id)
                        result += f"  [{status}] {name} (id: {job_id})\n"

                return {
                    "content": [{"type": "text", "text": result}]
                }
        except DaemonNotRunningError:
            logger.info("daemon_not_running for list_jobs")
        except (OSError, ConnectionError, TimeoutError):
            logger.warning("daemon_list_jobs_failed", exc_info=True)

        # Fallback: daemon not available
        result = "Mozart MCP Job Listing\n"
        result += "=" * 40 + "\n\n"

        if status_filter:
            result += f"Filter: {status_filter}\n"
        result += f"Limit: {limit}\n\n"

        result += "Note: Full job listing requires the Mozart conductor.\n"
        result += "Start the conductor for comprehensive job tracking:\n"
        result += "  mozart start\n\n"
        result += "Without conductor, use get_job with a specific job ID,\n"
        result += "or the Mozart CLI: mozart list [--status running]\n"

        return {
            "content": [{"type": "text", "text": result}]
        }

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
        result = f"Mozart Job Details: {job_id}\n"
        result += "=" * (23 + len(job_id)) + "\n\n"

        # Basic job information
        result += f"Job Name: {state.job_name}\n"
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
            recent_sheets = sorted(state.sheets.items(),
                                 key=lambda x: int(x[0]), reverse=True)[:5]
            for sheet_num, sheet in recent_sheets:
                result += f"  Sheet {sheet_num}: {sheet.status.value}"
                if sheet.error_message:
                    result += f" (Error: {sheet.error_message[:50]}...)"
                result += "\n"

        return {
            "content": [{"type": "text", "text": result}]
        }

    async def _start_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Start a new Mozart job."""
        config_path = Path(args["config_path"])
        workspace = Path(args["workspace"]) if args.get("workspace") else None
        start_sheet = args.get("start_sheet", 1)
        self_healing = args.get("self_healing", False)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Start job using the control service
        try:
            result = await self.job_control.start_job(
                config_path=config_path,
                workspace=workspace,
                start_sheet=start_sheet,
                self_healing=self_healing
            )

            via = "daemon" if result.via_daemon else "subprocess"
            response_text = "✓ Mozart job started successfully!\n\n"
            response_text += f"Job ID: {result.job_id}\n"
            response_text += f"Job Name: {result.job_name}\n"
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

            response_text += f"\nUse get_job tool with job_id '{result.job_id}' to check progress."

            return {
                "content": [{"type": "text", "text": response_text}]
            }

        except (FileNotFoundError, ValueError, RuntimeError, OSError) as e:
            logger.exception("Failed to start job from %s", config_path)
            raise RuntimeError(f"Failed to start job: {e}") from e

    async def shutdown(self) -> None:
        """Cleanup job tools."""
        pass  # No persistent resources to cleanup


class ControlTools:
    """Mozart job control tools.

    Provides MCP tools for controlling running Mozart jobs (pause, resume, cancel).
    These tools interact with job processes and require user consent.

    Routes through the conductor when available via JobControlService,
    falling back to local signal-based control.
    """

    def __init__(self, state_backend: JsonStateBackend, workspace_root: Path):
        self.state_backend = state_backend
        self.job_control = JobControlService(state_backend, workspace_root)

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all job control tools."""
        return [
            {
                "name": "pause_job",
                "description": "Pause a running Mozart job gracefully at sheet boundary",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Mozart job ID to pause"
                        }
                    },
                    "required": ["job_id"]
                }
            },
            {
                "name": "resume_job",
                "description": "Resume a paused Mozart job",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Mozart job ID to resume"
                        }
                    },
                    "required": ["job_id"]
                }
            },
            {
                "name": "cancel_job",
                "description": "Cancel a running Mozart job permanently",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Mozart job ID to cancel"
                        }
                    },
                    "required": ["job_id"]
                }
            }
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
                response_text = f"✓ Pause request sent to job: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Message: {result.message}\n"
                response_text += f"Via: {via}\n\n"
                response_text += "The job will pause gracefully at the next sheet boundary."
            else:
                response_text = f"✗ Failed to pause job: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Error: {result.message}"

            return {
                "content": [{"type": "text", "text": response_text}]
            }

        except (RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error pausing job %s", job_id)
            raise RuntimeError(f"Failed to pause job: {e}") from e

    async def _resume_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused job."""
        job_id = args["job_id"]

        try:
            result = await self.job_control.resume_job(job_id)
            via = "daemon" if result.via_daemon else "local"

            if result.success:
                response_text = f"✓ Job resumed successfully: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Message: {result.message}\n"
                response_text += f"Via: {via}"
            else:
                response_text = f"✗ Failed to resume job: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Error: {result.message}"

            return {
                "content": [{"type": "text", "text": response_text}]
            }

        except (RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error resuming job %s", job_id)
            raise RuntimeError(f"Failed to resume job: {e}") from e

    async def _cancel_job(self, args: dict[str, Any]) -> dict[str, Any]:
        """Cancel a running job permanently."""
        job_id = args["job_id"]

        try:
            result = await self.job_control.cancel_job(job_id)
            via = "daemon" if result.via_daemon else "local"

            if result.success:
                response_text = f"✓ Job cancelled successfully: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Message: {result.message}\n"
                response_text += f"Via: {via}\n\n"
                response_text += "Note: This action is permanent and cannot be undone."
            else:
                response_text = f"✗ Failed to cancel job: {job_id}\n\n"
                response_text += f"Status: {result.status}\n"
                response_text += f"Error: {result.message}"

            return {
                "content": [{"type": "text", "text": response_text}]
            }

        except (RuntimeError, OSError, ConnectionError) as e:
            logger.exception("Error cancelling job %s", job_id)
            raise RuntimeError(f"Failed to cancel job: {e}") from e

    async def shutdown(self) -> None:
        """Cleanup control tools."""
        pass


# Artifact tool schemas — extracted from ArtifactTools.list_tools() for readability.
# Each constant defines the MCP tool specification (name, description, inputSchema).
_ARTIFACT_LIST_SCHEMA: dict[str, Any] = {
    "name": "mozart_artifact_list",
    "description": "List files in a Mozart workspace",
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
    "name": "mozart_artifact_read",
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
    "name": "mozart_artifact_get_logs",
    "description": "Get logs from a Mozart job execution",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Mozart job ID",
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
    "name": "mozart_artifact_list_artifacts",
    "description": "List all artifacts created by a Mozart job",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Mozart job ID",
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
    "name": "mozart_artifact_get_artifact",
    "description": "Get a specific artifact from a Mozart job",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Mozart job ID",
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
    """Mozart artifact and workspace management tools.

    Provides MCP tools for browsing workspace files and accessing job artifacts.
    File system access is restricted to designated workspace directories.
    """

    _LOG_LEVEL_PATTERNS: dict[str, re.Pattern[str]] = {
        "debug": re.compile(r"DEBUG|debug", re.IGNORECASE),
        "info": re.compile(r"INFO|info", re.IGNORECASE),
        "warning": re.compile(r"WARNING|warning|WARN|warn", re.IGNORECASE),
        "error": re.compile(r"ERROR|error|FAIL|fail", re.IGNORECASE),
    }

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self._custom_level_cache: dict[str, re.Pattern[str]] = {}

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all artifact management tools."""
        return list(_ARTIFACT_TOOL_SCHEMAS)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an artifact management tool."""
        dispatch = {
            "mozart_artifact_list": self._list_files,
            "mozart_artifact_read": self._read_file,
            "mozart_artifact_get_logs": self._get_logs,
            "mozart_artifact_list_artifacts": self._list_artifacts,
            "mozart_artifact_get_artifact": self._get_artifact,
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
            KeyError, ValueError, FileNotFoundError, IsADirectoryError,
            NotADirectoryError, PermissionError, OSError,
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
            if not include_hidden and item.name.startswith('.'):
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

        return {
            "content": [{"type": "text", "text": result}]
        }

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
                for alt_encoding in ['latin-1', 'cp1252']:
                    try:
                        with open(target_file, encoding=alt_encoding) as f:
                            data = f.read()
                            return f"[File read with {alt_encoding} encoding]\n{data}"
                    except UnicodeDecodeError:
                        continue
                # Final fallback to binary representation
                with open(target_file, 'rb') as f:
                    raw_data = f.read()[:1000]  # First 1KB only
                    return f"Binary file: {file_size} bytes\nFirst 1KB (hex): {raw_data.hex()}"

        content = await asyncio.to_thread(_sync_read_file)

        size_str = self._format_size(file_size)

        result = f"📄 File: {target_file.name}\n"
        result += f"Size: {size_str}\n"
        result += f"Encoding: {encoding}\n\n"
        result += f"Content:\n{'-' * 40}\n{content}"

        return {
            "content": [{"type": "text", "text": result}]
        }

    async def _get_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get logs from a Mozart job execution."""
        job_id = args["job_id"]
        workspace = args.get("workspace")
        lines = args.get("lines", 100)
        level = args.get("level", "all")

        # Find the workspace if not provided
        if not workspace:
            workspace = self._find_job_workspace(job_id)

        workspace_path = Path(workspace)

        # Look for log files in common locations
        log_files = []

        # Primary log file (Mozart typically uses job-name.log)
        primary_log = workspace_path / f"{job_id}.log"
        if primary_log.exists():
            log_files.append(("Primary", primary_log))

        # Mozart.log (general log)
        mozart_log = workspace_path / "mozart.log"
        if mozart_log.exists():
            log_files.append(("Mozart", mozart_log))

        # Output logs from runner
        runner_log = workspace_path / "runner.log"
        if runner_log.exists():
            log_files.append(("Runner", runner_log))

        if not log_files:
            # Fallback: scan for any .log files
            for log_file in workspace_path.glob("*.log"):
                log_files.append(("Found", log_file))

        if not log_files:
            raise FileNotFoundError(
                f"No log files found for job {job_id}"
                f" in workspace {workspace_path}"
            )

        parts = [
            f"📋 Logs for Mozart Job: {job_id}\n",
            f"Workspace: {workspace_path}\n",
            f"Lines requested: {lines}, Level filter: {level}\n",
            "=" * 60 + "\n\n",
        ]

        # Use pre-compiled level filter pattern
        level_regex: re.Pattern[str] | None = None
        if level != "all":
            level_regex = self._LOG_LEVEL_PATTERNS.get(level.lower())
            if level_regex is None:
                # Custom level string — compile on demand with caching
                level_key = level.lower()
                level_regex = self._custom_level_cache.get(level_key)
                if level_regex is None:
                    level_regex = re.compile(re.escape(level), re.IGNORECASE)
                    self._custom_level_cache[level_key] = level_regex

        for log_type, log_file in log_files:
            try:
                parts.append(f"📄 {log_type} Log: {log_file.name}\n")
                parts.append("-" * 40 + "\n")

                # Read the log file in a thread to avoid blocking the event loop
                def _sync_read_log(path: Path = log_file) -> list[str]:
                    with open(path, encoding='utf-8', errors='ignore') as f:
                        return f.readlines()

                log_lines = await asyncio.to_thread(_sync_read_log)

                # Filter by log level if specified
                if level_regex is not None:
                    filtered_lines = [line for line in log_lines if level_regex.search(line)]
                else:
                    filtered_lines = log_lines

                # Get last N lines
                recent_lines = filtered_lines[-lines:] if filtered_lines else []

                if recent_lines:
                    parts.append("".join(recent_lines))
                else:
                    parts.append(f"(No {level} level logs found)\n")

                parts.append("\n" + "-" * 40 + "\n\n")

            except (OSError, UnicodeDecodeError) as e:
                parts.append(f"Error reading {log_file}: {e}\n\n")

        return {
            "content": [{"type": "text", "text": "".join(parts)}]
        }

    async def _list_artifacts(self, args: dict[str, Any]) -> dict[str, Any]:
        """List all artifacts created by a Mozart job."""
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

        result = f"🎯 Artifacts for Mozart Job: {job_id}\n"
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
            "other": []
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
            artifacts[category].append({
                "path": str(rel_path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime),
                "category": category,
            })

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

        return {
            "content": [{"type": "text", "text": result}]
        }

    async def _get_artifact(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get a specific artifact from a Mozart job."""
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

        result = f"🎯 Mozart Job Artifact: {job_id}\n"
        result += f"Artifact: {artifact_path}\n"
        result += f"Size: {self._format_size(file_size)}\n"
        result += f"Modified: {modified.strftime('%Y-%m-%d %H:%M:%S')}\n"
        result += f"Created: {created.strftime('%Y-%m-%d %H:%M:%S')}\n"
        result += "=" * 60 + "\n\n"

        # Read content based on file type
        try:
            if target_artifact.suffix.lower() in ['.json', '.yaml', '.yml', '.txt', '.md', '.log']:
                # Text files
                with open(target_artifact, encoding='utf-8') as f:
                    content = f.read()
                result += f"Content:\n{'-' * 40}\n{content}"
            else:
                # Binary files - show hex dump
                with open(target_artifact, 'rb') as f:
                    raw_data = f.read()
                    if len(raw_data) <= 1000:  # Small binary files
                        result += f"Binary Content (hex):\n{'-' * 40}\n{raw_data.hex()}"
                    else:
                        result += f"Large Binary File:\n{'-' * 40}\n"
                        result += f"First 1KB (hex): {raw_data[:1000].hex()}\n"
                        result += f"... ({len(raw_data)} total bytes)"
        except (OSError, UnicodeDecodeError) as e:
            result += f"Error reading artifact content: {e}"

        return {
            "content": [{"type": "text", "text": result}]
        }

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
        """Find workspace directory for a job ID."""
        # Try common workspace locations
        possible_workspaces = [
            self.workspace_root / job_id,
            self.workspace_root / f"{job_id}-workspace",
            self.workspace_root / "workspace" / job_id,
            self.workspace_root,  # Job might be in root workspace
        ]

        for ws in possible_workspaces:
            # Look for state files or other Mozart artifacts
            if (ws / f"{job_id}.json").exists():
                return str(ws)
            if (ws / "mozart.log").exists():
                return str(ws)
            if any(ws.glob("*.json")):  # Any state file
                return str(ws)

        # Default to job_id as workspace name
        return str(self.workspace_root / job_id)

    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes/1024:.1f}KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes/(1024*1024):.1f}MB"
        else:
            return f"{size_bytes/(1024*1024*1024):.1f}GB"

    async def shutdown(self) -> None:
        """Cleanup artifact tools."""
        pass


class ScoreTools:
    """Mozart code quality score tools.

    Provides MCP tools for validating and generating quality scores for code changes
    using Mozart's AI-powered review system. Tools analyze git diffs and provide
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

        # Note: This is a stub implementation
        # Full implementation would require backend integration
        result_text = f"🎯 Quality Score Validation: {workspace.name}\n"
        result_text += f"Workspace: {workspace}\n"
        result_text += f"Min Score: {min_score}/100\n"
        result_text += f"Target Score: {target_score}/100\n"
        if since_commit:
            result_text += f"Since Commit: {since_commit}\n"
        result_text += "=" * 60 + "\n\n"

        result_text += "⚠️  STUB IMPLEMENTATION\n"
        result_text += "This tool requires integration with Mozart's AIReviewer.\n"
        result_text += "The validate_score tool would:\n\n"
        result_text += "1. Initialize AIReviewer with backend configuration\n"
        result_text += "2. Get git diff using GitDiffProvider\n"
        result_text += "3. Execute AI review to generate quality score\n"
        result_text += "4. Evaluate score against min_score and target_score\n"
        result_text += "5. Return validation result with pass/fail status\n\n"
        result_text += "Score components analyzed:\n"
        result_text += "• Code Quality (30%): Complexity, patterns, readability\n"
        result_text += "• Test Coverage (25%): New code tested, edge cases\n"
        result_text += "• Security (25%): No secrets, validation, safe error handling\n"
        result_text += "• Documentation (20%): APIs documented, complex logic explained\n\n"
        result_text += "To enable scoring, configure AI backend in Mozart config."

        return {
            "content": [{"type": "text", "text": result_text}]
        }

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

        # Note: This is a stub implementation
        # Full implementation would require backend integration
        result_text = f"📊 Quality Score Generation: {workspace.name}\n"
        result_text += f"Workspace: {workspace}\n"
        if since_commit:
            result_text += f"Since Commit: {since_commit}\n"
        result_text += f"Detailed Output: {detailed}\n"
        result_text += "=" * 60 + "\n\n"

        result_text += "⚠️  STUB IMPLEMENTATION\n"
        result_text += "This tool requires integration with Mozart's AIReviewer.\n"
        result_text += "The generate_score tool would:\n\n"
        result_text += "1. Initialize AIReviewer with backend configuration\n"
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
        result_text += '  },\n'
        result_text += '  "issues": [\n'
        result_text += '    {\n'
        result_text += '      "severity": "medium",\n'
        result_text += '      "category": "documentation",\n'
        result_text += '      "description": "Complex logic lacks comments",\n'
        result_text += '      "suggestion": "Add docstring explaining algorithm"\n'
        result_text += '    }\n'
        result_text += '  ],\n'
        result_text += '  "summary": "High quality code with minor documentation gaps"\n'
        result_text += "}\n"
        result_text += "```\n\n"
        result_text += "To enable scoring, configure AI backend in Mozart config."

        return {
            "content": [{"type": "text", "text": result_text}]
        }

    async def shutdown(self) -> None:
        """Cleanup score tools."""
        pass
