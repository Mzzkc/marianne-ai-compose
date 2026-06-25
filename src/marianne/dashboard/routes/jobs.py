"""Job control API endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from marianne.core.constants import SHEET_NUM_KEY
from marianne.daemon.detect import _resolve_socket_path
from marianne.daemon.exceptions import DaemonNotRunningError
from marianne.daemon.ipc.client import DaemonClient
from marianne.dashboard.app import get_daemon_client, get_state_backend
from marianne.dashboard.services.job_control import (
    JobActionResult,
    JobControlService,
    JobStartResult,
)

router = APIRouter(prefix="/api/jobs", tags=["Job Control"])


# ============================================================================
# Request Models (Pydantic schemas for API requests)
# ============================================================================


class StartJobRequest(BaseModel):
    """Request to start a new job."""

    config_content: str | None = Field(None, description="YAML config content as string")
    config_path: str | None = Field(None, description="Path to YAML config file")
    workspace: str | None = Field(None, description="Override workspace directory")
    start_sheet: int = Field(1, ge=1, description="Starting sheet number")
    fresh: bool = Field(False, description="Start with clean state")
    self_healing: bool = Field(False, description="Enable self-healing mode")
    self_healing_auto_confirm: bool = Field(
        False, description="Auto-confirm self-healing fixes"
    )
    escalation: bool = Field(False, description="Pause for composer decision on exhaustion")
    dry_run: bool = Field(False, description="Validate without executing when supported")
    chain_depth: int | None = Field(None, ge=0, description="Concert chain depth")
    client_cwd: str | None = Field(
        None, description="Client working directory for relative path resolution"
    )
    runtime_variables: dict[str, str] = Field(
        default_factory=dict,
        description="Per-invocation template variables",
    )

    def validate_config_source(self) -> None:
        """Validate that exactly one config source is provided."""
        if not self.config_content and not self.config_path:
            raise ValueError("Must provide either config_content or config_path")
        if self.config_content and self.config_path:
            raise ValueError("Cannot provide both config_content and config_path")


class JobActionResponse(BaseModel):
    """Response from job actions (pause/resume/cancel)."""

    success: bool
    job_id: str
    status: str
    message: str
    via_daemon: bool = True

    @classmethod
    def from_action_result(cls, result: JobActionResult) -> JobActionResponse:
        """Create from JobActionResult."""
        return cls(
            success=result.success,
            job_id=result.job_id,
            status=result.status,
            message=result.message,
            via_daemon=result.via_daemon,
        )


class StartJobResponse(BaseModel):
    """Response from starting a job."""

    success: bool
    job_id: str
    job_name: str
    status: str
    workspace: str
    total_sheets: int
    pid: int | None
    message: str
    via_daemon: bool = True

    @classmethod
    def from_start_result(cls, result: JobStartResult) -> StartJobResponse:
        """Create from JobStartResult."""
        return cls(
            success=True,
            job_id=result.job_id,
            job_name=result.job_name,
            status=result.status,
            workspace=str(result.workspace),
            total_sheets=result.total_sheets,
            pid=result.pid,
            message=f"Job {result.job_name} started successfully",
            via_daemon=result.via_daemon,
        )


# ============================================================================
# Dependency injection
# ============================================================================


def _get_job_control_service() -> JobControlService:
    """Get a JobControlService backed by the conductor."""
    return JobControlService(get_daemon_client())


# ============================================================================
# API Endpoints
# ============================================================================


@router.post("", response_model=StartJobResponse)
async def start_job(
    request: StartJobRequest,
) -> StartJobResponse:
    """Start a new Marianne job execution via the conductor.

    Supports both inline YAML config content or path to config file.
    """
    try:
        request.validate_config_source()

        config_path = Path(request.config_path) if request.config_path else None
        workspace = Path(request.workspace) if request.workspace else None
        client_cwd = Path(request.client_cwd) if request.client_cwd else None

        service = _get_job_control_service()
        result = await service.start_job(
            config_path=config_path,
            config_content=request.config_content,
            workspace=workspace,
            start_sheet=request.start_sheet,
            fresh=request.fresh,
            self_healing=request.self_healing,
            self_healing_auto_confirm=request.self_healing_auto_confirm,
            escalation=request.escalation,
            dry_run=request.dry_run,
            chain_depth=request.chain_depth,
            client_cwd=client_cwd,
            runtime_variables=request.runtime_variables,
        )

        return StartJobResponse.from_start_result(result)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "Invalid job configuration",
        ) from None
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc) or "Configuration file not found",
        ) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc) or "Conductor unavailable") from None


@router.post("/{job_id}/pause", response_model=JobActionResponse)
async def pause_job(job_id: str) -> JobActionResponse:
    """Pause a running job via the conductor."""
    try:
        service = _get_job_control_service()
        result = await service.pause_job(job_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Conductor unavailable") from None

    if not result.success:
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=409, detail=result.message)

    return JobActionResponse.from_action_result(result)


@router.post("/{job_id}/resume", response_model=JobActionResponse)
async def resume_job(job_id: str) -> JobActionResponse:
    """Resume a paused job via the conductor."""
    try:
        service = _get_job_control_service()
        result = await service.resume_job(job_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Conductor unavailable") from None

    if not result.success:
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=409, detail=result.message)

    return JobActionResponse.from_action_result(result)


@router.post("/{job_id}/cancel", response_model=JobActionResponse)
async def cancel_job(job_id: str) -> JobActionResponse:
    """Cancel a running or paused job via the conductor."""
    try:
        service = _get_job_control_service()
        result = await service.cancel_job(job_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Conductor unavailable") from None

    if not result.success:
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=409, detail=result.message)

    return JobActionResponse.from_action_result(result)


@router.delete("/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    """Delete a terminal job record from the conductor registry."""
    try:
        service = _get_job_control_service()
        deleted = await service.delete_job(job_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Conductor unavailable") from None

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Job {job_id} deleted successfully",
    }


@router.get("/{job_id}/sheets/{sheet_num}")
async def get_sheet_details(
    job_id: str,
    sheet_num: int,
) -> dict[str, Any]:
    """Get detailed sheet information for a specific job and sheet."""
    backend = get_state_backend()
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    sheet_state = state.sheets.get(sheet_num)
    if sheet_state is None:
        raise HTTPException(status_code=404, detail=f"Sheet {sheet_num} not found in job {job_id}")

    return {
        SHEET_NUM_KEY: sheet_state.sheet_num,
        "status": sheet_state.status.value,
        "started_at": sheet_state.started_at.isoformat() if sheet_state.started_at else None,
        "completed_at": sheet_state.completed_at.isoformat() if sheet_state.completed_at else None,
        "attempt_count": sheet_state.attempt_count,
        "exit_code": sheet_state.exit_code,
        "error_message": sheet_state.error_message,
        "error_category": sheet_state.error_category,
        "validation_passed": sheet_state.validation_passed,
        "validation_details": sheet_state.validation_details or [],
        "execution_duration_seconds": sheet_state.execution_duration_seconds,
        "exit_signal": sheet_state.exit_signal,
        "exit_reason": sheet_state.exit_reason,
        "completion_attempts": sheet_state.completion_attempts,
        "passed_validations": sheet_state.passed_validations,
        "failed_validations": sheet_state.failed_validations,
        "last_pass_percentage": sheet_state.last_pass_percentage,
        "execution_mode": sheet_state.execution_mode,
        "confidence_score": sheet_state.confidence_score,
        "outcome_category": sheet_state.outcome_category,
        "success_without_retry": sheet_state.success_without_retry,
        "stdout_tail": sheet_state.stdout_tail,
        "stderr_tail": sheet_state.stderr_tail,
        "output_truncated": sheet_state.output_truncated,
        "preflight_warnings": sheet_state.preflight_warnings,
        "applied_pattern_descriptions": sheet_state.applied_pattern_descriptions,
        "grounding_passed": sheet_state.grounding_passed,
        "grounding_confidence": sheet_state.grounding_confidence,
        "grounding_guidance": sheet_state.grounding_guidance,
        "input_tokens": sheet_state.input_tokens,
        "output_tokens": sheet_state.output_tokens,
        "estimated_cost": sheet_state.estimated_cost,
        "cost_confidence": sheet_state.cost_confidence,
        # #373: True when the instrument had no per-token pricing — the
        # $0 cost means "unknown", not "free". Orthogonal to
        # cost_confidence (estimate quality when pricing DOES exist).
        "cost_uncertain": sheet_state.cost_uncertain,
    }


# ============================================================================
# Daemon status endpoint
# ============================================================================


@router.get("/daemon/status", tags=["Daemon"])
async def daemon_status() -> dict[str, Any]:
    """Check if the Marianne conductor is running and get its status."""
    client = DaemonClient(_resolve_socket_path(None))
    try:
        status = await asyncio.wait_for(client.status(), timeout=2.0)
        return {
            "connected": True,
            "pid": status.pid,
            "uptime_seconds": status.uptime_seconds,
            "running_jobs": status.running_jobs,
            "total_jobs_active": status.total_jobs_active,
            "memory_usage_mb": status.memory_usage_mb,
            "version": status.version,
        }
    except DaemonNotRunningError:
        return {
            "connected": False,
            "message": "Daemon not running",
        }
    except TimeoutError:
        return {
            "connected": False,
            "message": "Daemon status probe timed out",
        }
    except Exception as exc:
        return {
            "connected": False,
            "message": f"Daemon status unavailable: {exc}",
        }
