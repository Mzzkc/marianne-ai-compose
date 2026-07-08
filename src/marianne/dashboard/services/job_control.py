"""Job lifecycle control service — conductor-only proxy.

Every operation routes through the conductor via ``DaemonClient`` IPC.
If the conductor is not running, all operations fail with clear errors.
There is no subprocess fallback.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.core.logging import get_logger
from marianne.daemon.exceptions import DaemonNotRunningError
from marianne.daemon.ipc.client import DaemonClient
from marianne.daemon.types import JobRequest

logger = get_logger("job_control")

DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass
class JobStartResult:
    """Result of starting a job."""

    job_id: str
    job_name: str
    status: str
    workspace: Path
    total_sheets: int
    pid: int | None = None
    via_daemon: bool = True


@dataclass
class JobActionResult:
    """Result of a job action (pause/resume/cancel)."""

    success: bool
    job_id: str
    status: str
    message: str
    via_daemon: bool = True


@dataclass
class ProcessHealth:
    """Process health check result (derived from conductor state)."""

    pid: int | None
    is_alive: bool
    is_zombie_state: bool
    process_exists: bool
    cpu_percent: float | None = None
    memory_mb: float | None = None
    uptime_seconds: float | None = None


def _action_error_message(response: Any, fallback: str) -> str:
    if isinstance(response, dict):
        raw = response.get("error") or response.get("message")
        if raw:
            return str(raw)
    return fallback


class JobControlService:
    """Conductor-only proxy for job lifecycle control.

    Parameters
    ----------
    daemon_client:
        A ``DaemonClient`` connected to the conductor's Unix socket.
    """

    def __init__(self, daemon_client: DaemonClient) -> None:
        if daemon_client is None:
            raise ValueError(
                "DaemonClient is required — the dashboard requires a running conductor."
            )
        self._client = daemon_client

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    async def start_job(
        self,
        config_path: Path | None = None,
        config_content: str | None = None,
        workspace: Path | None = None,
        start_sheet: int = 1,
        self_healing: bool = False,
        fresh: bool = False,
        self_healing_auto_confirm: bool = False,
        escalation: bool = False,
        dry_run: bool = False,
        chain_depth: int | None = None,
        client_cwd: Path | None = None,
        runtime_variables: dict[str, str] | None = None,
    ) -> JobStartResult:
        """Submit a new job to the conductor.

        The conductor contract is path-based and asynchronous: accepted
        jobs keep the submitted path in the registry and may read it after
        this request returns. Inline ``config_content`` is therefore written
        to a durable dashboard submissions directory, not a throwaway temp file.

        Args:
            config_path: Path to YAML config file.
            config_content: Inline YAML config content (written to durable file).
            workspace: Override workspace directory.
            start_sheet: Starting sheet number.
            self_healing: Enable self-healing mode.
            fresh: Start with clean state, ignoring existing checkpoints.
            self_healing_auto_confirm: Auto-confirm self-healing fixes.
            escalation: Pause for composer decision on retry exhaustion.
            dry_run: Forward dry-run intent to daemon clients that honor it.
            chain_depth: Concert chain depth for chained submissions.
            client_cwd: Client working directory for relative path resolution.
            runtime_variables: Per-invocation template variables.

        Returns:
            JobStartResult with job details.

        Raises:
            ValueError: If neither config_path nor config_content provided.
            FileNotFoundError: If config_path doesn't exist.
            RuntimeError: If the conductor rejects the submission.
        """
        if not config_path and not config_content:
            raise ValueError("Must provide either config_path or config_content")

        resolved_path = config_path
        config: JobConfig

        if config_content:
            try:
                config = JobConfig.from_yaml_string(config_content)
            except Exception as exc:
                raise ValueError(f"Invalid job configuration: {exc}") from exc
            resolved_path = _write_dashboard_submission(config_content, config.name)

        assert resolved_path is not None

        if config_path and not config_content:
            resolved = config_path.resolve()
            if resolved.suffix not in (".yaml", ".yml"):
                raise ValueError(f"Config path must be a YAML file (.yaml/.yml): {config_path}")
            if ".." in config_path.parts:
                raise ValueError(f"Config path must not contain '..' traversal: {config_path}")
            if not resolved.exists():
                raise FileNotFoundError(f"Config file not found: {config_path}")
            resolved_path = resolved
            try:
                config = JobConfig.from_yaml(resolved_path)
            except Exception as exc:
                raise ValueError(f"Invalid job configuration: {exc}") from exc

        client_cwd_path = client_cwd.resolve() if client_cwd else Path.cwd().resolve()
        runtime_vars = runtime_variables or {}

        try:
            request = JobRequest(
                config_path=resolved_path.resolve(),
                workspace=workspace.resolve() if workspace else None,
                fresh=fresh,
                self_healing=self_healing,
                self_healing_auto_confirm=self_healing_auto_confirm,
                escalation=escalation,
                start_sheet=start_sheet if start_sheet > 1 else None,
                dry_run=dry_run,
                chain_depth=chain_depth,
                client_cwd=client_cwd_path,
                runtime_variables=runtime_vars,
            )
            response = await asyncio.wait_for(
                self._client.submit_job(request),
                timeout=DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS,
            )

            if response.status not in ("accepted", "pending"):
                detail = response.message or f"status={response.status}"
                raise RuntimeError(f"Conductor rejected job: {detail}")

            ws = workspace or (Path(config.workspace) if config.workspace else client_cwd_path)
            if client_cwd_path and not ws.is_absolute():
                ws = (client_cwd_path / ws).resolve()

            logger.info(
                "job_submitted_to_conductor",
                job_id=response.job_id,
                job_name=config.name,
                status=response.status,
            )

            return JobStartResult(
                job_id=response.job_id,
                job_name=config.name,
                status=response.status,
                workspace=ws,
                total_sheets=config.sheet.total_sheets,
            )

        except DaemonNotRunningError:
            raise RuntimeError("Conductor not running. Start it with: mzt start") from None
        except TimeoutError:
            raise RuntimeError("Conductor request timed out.") from None
        except Exception as e:
            if isinstance(e, (ValueError, FileNotFoundError, RuntimeError)):
                raise
            raise RuntimeError(f"Failed to submit job to conductor: {e}") from e

    async def pause_job(self, job_id: str) -> JobActionResult:
        """Pause a running job via the conductor."""
        try:
            response = await asyncio.wait_for(
                self._client.pause_job(job_id, ""),
                timeout=DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS,
            )
            paused = bool(response.get("paused")) if isinstance(response, dict) else False
            if not paused:
                message = _action_error_message(response, "Pause request was rejected")
                return JobActionResult(
                    success=False,
                    job_id=job_id,
                    status="pause_rejected",
                    message=message,
                )
            return JobActionResult(
                success=True,
                job_id=job_id,
                status="pause_requested",
                message=f"Pause request sent to conductor for job {job_id}",
            )
        except DaemonNotRunningError:
            raise RuntimeError("Conductor not running.") from None
        except TimeoutError:
            raise RuntimeError("Conductor pause request timed out.") from None

    async def resume_job(self, job_id: str) -> JobActionResult:
        """Resume a paused job via the conductor."""
        try:
            response = await asyncio.wait_for(
                self._client.resume_job(job_id, ""),
                timeout=DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS,
            )
            status = str(response.get("status", "")) if isinstance(response, dict) else ""
            if status not in ("accepted", "pending"):
                message = _action_error_message(response, "Resume request was rejected")
                return JobActionResult(
                    success=False,
                    job_id=job_id,
                    status="resume_rejected",
                    message=message,
                )
            return JobActionResult(
                success=True,
                job_id=job_id,
                status="resume_requested",
                message=f"Resume request sent to conductor for job {job_id}",
            )
        except DaemonNotRunningError:
            raise RuntimeError("Conductor not running.") from None
        except TimeoutError:
            raise RuntimeError("Conductor resume request timed out.") from None

    async def cancel_job(self, job_id: str) -> JobActionResult:
        """Cancel a running or paused job via the conductor."""
        try:
            response = await asyncio.wait_for(
                self._client.cancel_job(job_id, ""),
                timeout=DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS,
            )
            cancelled = (
                bool(response.get("cancelled")) if isinstance(response, dict) else False
            )
            if not cancelled:
                message = _action_error_message(response, "Cancel request was rejected")
                return JobActionResult(
                    success=False,
                    job_id=job_id,
                    status="cancel_rejected",
                    message=message,
                )
            return JobActionResult(
                success=True,
                job_id=job_id,
                status="cancel_requested",
                message=f"Cancel request sent to conductor for job {job_id}",
            )
        except DaemonNotRunningError:
            raise RuntimeError("Conductor not running.") from None
        except TimeoutError:
            raise RuntimeError("Conductor cancel request timed out.") from None

    async def delete_job(self, job_id: str) -> bool:
        """Delete a terminal job from the conductor registry."""
        try:
            result = await asyncio.wait_for(
                self._client.clear_jobs(job_ids=[job_id]),
                timeout=DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS,
            )
            deleted: bool = bool(result.get("deleted", 0))
            if deleted:
                logger.info("job_deleted", job_id=job_id)
            return deleted
        except DaemonNotRunningError:
            raise RuntimeError("Conductor not running.") from None
        except TimeoutError:
            raise RuntimeError("Conductor delete request timed out.") from None

    # ------------------------------------------------------------------
    # Process health (for MCP compatibility)
    # ------------------------------------------------------------------

    async def verify_process_health(self, job_id: str) -> ProcessHealth:
        """Check job health by querying conductor state.

        Returns a ``ProcessHealth`` derived from the job's ``CheckpointState``
        as reported by the conductor.
        """
        try:
            status_data = await asyncio.wait_for(
                self._client.get_job_status(job_id, ""),
                timeout=DASHBOARD_DAEMON_REQUEST_TIMEOUT_SECONDS,
            )
            from marianne.core.checkpoint import CheckpointState, JobStatus

            state = CheckpointState(**status_data)

            is_terminal = state.status in (
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            )

            return ProcessHealth(
                pid=state.pid,
                is_alive=not is_terminal,
                is_zombie_state=state.is_zombie() if not is_terminal else False,
                process_exists=not is_terminal,
                uptime_seconds=None,
                cpu_percent=None,
                memory_mb=None,
            )
        except DaemonNotRunningError:
            return ProcessHealth(
                pid=None,
                is_alive=False,
                is_zombie_state=False,
                process_exists=False,
            )
        except TimeoutError:
            return ProcessHealth(
                pid=None,
                is_alive=False,
                is_zombie_state=False,
                process_exists=False,
            )


def _dashboard_submission_dir() -> Path:
    """Directory for durable inline dashboard submissions."""
    override = os.environ.get("MARIANNE_DASHBOARD_SUBMISSIONS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".marianne" / "dashboard-submissions"


def _slugify_score_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-._")
    return slug or "score"


def _write_dashboard_submission(content: str, config_name: str) -> Path:
    """Persist inline score content long enough for conductor execution."""
    directory = _dashboard_submission_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_slugify_score_name(config_name)}-{uuid.uuid4().hex[:10]}.yaml"
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("dashboard_submission_chmod_failed", path=str(path), exc_info=True)
    return path
