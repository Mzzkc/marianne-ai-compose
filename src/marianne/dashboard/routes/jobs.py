"""Job control API endpoints."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from marianne.daemon.detect import _resolve_socket_path
from marianne.daemon.exceptions import DaemonNotRunningError
from marianne.daemon.ipc.client import DaemonClient
from marianne.dashboard.app import get_state_backend
from marianne.dashboard.services.job_control import (
    JobActionResult,
    JobControlService,
    JobStartResult,
)
from marianne.state.base import StateBackend

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
    self_healing: bool = Field(False, description="Enable self-healing mode")

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
    via_daemon: bool = False

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
    via_daemon: bool = False

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


async def get_job_control_service(
    backend: StateBackend = Depends(get_state_backend),
) -> JobControlService:
    """Get job control service instance."""
    return JobControlService(backend)


# ============================================================================
# API Endpoints
# ============================================================================


@router.post("", response_model=StartJobResponse)
async def start_job(
    request: StartJobRequest,
    job_service: JobControlService = Depends(get_job_control_service),
) -> StartJobResponse:
    """Start a new Marianne job execution.

    Supports both inline YAML config content or path to config file.

    Args:
        request: Job start request with config and options
        job_service: Job control service (injected)

    Returns:
        Job start result with job ID and details

    Raises:
        HTTPException: 400 if validation fails, 500 if start fails
    """
    try:
        # Validate request
        request.validate_config_source()

        # Convert paths to Path objects if provided
        config_path = Path(request.config_path) if request.config_path else None
        workspace = Path(request.workspace) if request.workspace else None

        # Start the job
        result = await job_service.start_job(
            config_path=config_path,
            config_content=request.config_content,
            workspace=workspace,
            start_sheet=request.start_sheet,
            self_healing=request.self_healing,
        )

        return StartJobResponse.from_start_result(result)

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job configuration") from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Configuration file not found") from None
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Failed to start job") from None



@router.post("/{job_id}/pause", response_model=JobActionResponse)
async def pause_job(
    job_id: str,
    job_service: JobControlService = Depends(get_job_control_service),
) -> JobActionResponse:
    """Pause a running job by sending SIGSTOP.

    The job process will be suspended but can be resumed later.

    Args:
        job_id: Unique job identifier
        job_service: Job control service (injected)

    Returns:
        Operation result and updated job status

    Raises:
        HTTPException: 404 if job not found, 400 if not pausable
    """
    result = await job_service.pause_job(job_id)

    if not result.success:
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=409, detail=result.message)

    return JobActionResponse.from_action_result(result)


@router.post("/{job_id}/resume", response_model=JobActionResponse)
async def resume_job(
    job_id: str,
    job_service: JobControlService = Depends(get_job_control_service),
) -> JobActionResponse:
    """Resume a paused job by sending SIGCONT.

    If the process died while paused, attempts to restart execution.

    Args:
        job_id: Unique job identifier
        job_service: Job control service (injected)

    Returns:
        Operation result and updated job status

    Raises:
        HTTPException: 404 if job not found, 409 if not resumable
    """
    result = await job_service.resume_job(job_id)

    if not result.success:
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=409, detail=result.message)

    return JobActionResponse.from_action_result(result)


@router.post("/{job_id}/cancel", response_model=JobActionResponse)
async def cancel_job(
    job_id: str,
    job_service: JobControlService = Depends(get_job_control_service),
) -> JobActionResponse:
    """Cancel a running job by sending SIGTERM.

    The job will be terminated gracefully (or forcefully if needed).

    Args:
        job_id: Unique job identifier
        job_service: Job control service (injected)

    Returns:
        Operation result and updated job status

    Raises:
        HTTPException: 404 if job not found, 409 if not cancellable
    """
    result = await job_service.cancel_job(job_id)

    if not result.success:
        if "not found" in result.message:
            raise HTTPException(status_code=404, detail=result.message)
        raise HTTPException(status_code=409, detail=result.message)

    return JobActionResponse.from_action_result(result)


@router.delete("/{job_id}")
async def delete_job(
    job_id: str,
    job_service: JobControlService = Depends(get_job_control_service),
) -> dict[str, Any]:
    """Delete a job record from state.

    Cannot delete running jobs - they must be cancelled first.

    Args:
        job_id: Unique job identifier
        job_service: Job control service (injected)

    Returns:
        Operation result

    Raises:
        HTTPException: 404 if job not found, 409 if still running
    """
    deleted = await job_service.delete_job(job_id)

    if not deleted:
        # Check if job exists but couldn't be deleted (running) or doesn't exist
        backend = job_service._state_backend
        state = await backend.load(job_id)

        if state is None:
            raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")
        else:
            # Job exists but couldn't be deleted (likely running)
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete running job: {job_id}. Cancel the job first."
            )

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Job {job_id} deleted successfully"
    }


@router.get("/{job_id}/sheets/{sheet_num}")
async def get_sheet_details(
    job_id: str,
    sheet_num: int,
    backend: StateBackend = Depends(get_state_backend),
) -> dict[str, Any]:
    """Get detailed sheet information for a specific job and sheet.

    Args:
        job_id: Unique job identifier
        sheet_num: Sheet number to get details for
        backend: State backend (injected)

    Returns:
        Detailed sheet information including execution logs

    Raises:
        HTTPException: 404 if job or sheet not found
    """
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    sheet_state = state.sheets.get(sheet_num)
    if sheet_state is None:
        raise HTTPException(status_code=404, detail=f"Sheet {sheet_num} not found in job {job_id}")

    # Build comprehensive sheet details
    sheet_details = {
        "sheet_num": sheet_state.sheet_num,
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
    }

    return sheet_details


# ============================================================================
# Daemon status endpoint
# ============================================================================


@router.get("/daemon/status", tags=["Daemon"])
async def daemon_status() -> dict[str, Any]:
    """Check if the Marianne conductor is running and get its status.

    Returns a "Daemon Connected" indicator and status details when the conductor
    is available, or a disconnected status when it's not.
    """
    client = DaemonClient(_resolve_socket_path(None))
    try:
        status = await client.status()
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
