"""Dashboard API routes.

All routes are prefixed with /api for clear API namespace separation.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetStatus
from marianne.dashboard.app import get_state_backend
from marianne.state.base import StateBackend

router = APIRouter(prefix="/api", tags=["Jobs"])


ACTIVE_OR_BLOCKED_SHEET_STATUSES = frozenset(
    {
        SheetStatus.DISPATCHED,
        SheetStatus.IN_PROGRESS,
        SheetStatus.WAITING,
        SheetStatus.RETRY_SCHEDULED,
        SheetStatus.FERMATA,
    }
)
QUEUED_SHEET_STATUSES = frozenset({SheetStatus.PENDING, SheetStatus.READY})
TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)


class ActiveWorkSelection(BaseModel):
    """Selected active or blocked-in-place sheet for cockpit surfaces."""

    sheet_num: int | None = None
    status: SheetStatus | None = None
    reason: str
    selected_by: str
    dispatch_blocked_reason: str | None = None
    dispatch_blocked_details: dict[str, Any] = Field(default_factory=dict)


class ArtifactState(BaseModel):
    """Artifact/workspace state derived from concrete workspace files."""

    state: str
    workspace: str | None = None
    total_files: int | None = None
    message: str
    freshness_verified: bool = False
    freshness_state: str = "freshness_not_verified"


class LogState(BaseModel):
    """Compact log availability state for cockpit rows."""

    state: str
    message: str
    sources: list[str] = Field(default_factory=list)


def _backend_dashboard_metadata(
    backend: StateBackend | None,
    job_id: str,
) -> dict[str, Any]:
    if backend is None:
        return {}
    metadata_getter = getattr(backend, "dashboard_metadata", None)
    if not callable(metadata_getter):
        return {}
    metadata = metadata_getter(job_id)
    return metadata if isinstance(metadata, dict) else {}


def _sheet_status_label(status: SheetStatus | None) -> str:
    if status is None:
        return "unknown"
    return status.value.replace("_", " ")


def _active_reason_for_sheet(sheet: Any, *, selected_by: str) -> str:
    status = getattr(sheet, "status", None)
    sheet_num = getattr(sheet, "sheet_num", None)
    status_label = _sheet_status_label(status)

    dispatch_reason = getattr(sheet, "dispatch_blocked_reason", None)
    if dispatch_reason:
        return f"Sheet {sheet_num} dispatch blocked: {dispatch_reason}"
    if status == SheetStatus.WAITING:
        return f"Sheet {sheet_num} waiting"
    if status == SheetStatus.RETRY_SCHEDULED:
        return f"Sheet {sheet_num} retry scheduled"
    if status == SheetStatus.FERMATA:
        fermata_reason = getattr(sheet, "fermata_reason", None)
        return f"Sheet {sheet_num} fermata: {fermata_reason or 'awaiting decision'}"
    if selected_by == "fallback_status":
        return f"Sheet {sheet_num} selected from {status_label} state"
    return f"Sheet {sheet_num} {status_label}"


def resolve_active_work(state: CheckpointState) -> ActiveWorkSelection:
    """Resolve active or blocked-in-place work for a job.

    The resolver is intentionally narrower than ``SheetStatus.is_active``:
    it prefers ``CheckpointState.current_sheet`` for non-terminal jobs, then
    falls back only to executing or blocked-in-place statuses. Ordinary
    ``pending`` and ``ready`` sheets remain queued future work.
    """

    if state.status in TERMINAL_JOB_STATUSES:
        return ActiveWorkSelection(
            reason="Terminal job has no active sheet",
            selected_by="terminal_job",
        )

    if state.current_sheet is not None:
        sheet = state.sheets.get(state.current_sheet)
        if sheet is None:
            return ActiveWorkSelection(
                sheet_num=state.current_sheet,
                reason=f"Sheet {state.current_sheet} reported as current without sheet state",
                selected_by="current_sheet_missing_state",
            )
        return ActiveWorkSelection(
            sheet_num=sheet.sheet_num,
            status=sheet.status,
            reason=_active_reason_for_sheet(sheet, selected_by="current_sheet"),
            selected_by="current_sheet",
            dispatch_blocked_reason=sheet.dispatch_blocked_reason,
            dispatch_blocked_details=sheet.dispatch_blocked_details,
        )

    for sheet in sorted(state.sheets.values(), key=lambda s: s.sheet_num):
        if sheet.status in ACTIVE_OR_BLOCKED_SHEET_STATUSES:
            return ActiveWorkSelection(
                sheet_num=sheet.sheet_num,
                status=sheet.status,
                reason=_active_reason_for_sheet(sheet, selected_by="fallback_status"),
                selected_by="fallback_status",
                dispatch_blocked_reason=sheet.dispatch_blocked_reason,
                dispatch_blocked_details=sheet.dispatch_blocked_details,
            )

    queued = [
        sheet.sheet_num
        for sheet in state.sheets.values()
        if sheet.status in QUEUED_SHEET_STATUSES
    ]
    if queued:
        noun = "sheets" if len(queued) != 1 else "sheet"
        return ActiveWorkSelection(
            reason=f"{len(queued)} queued {noun}; no active sheet reported",
            selected_by="queued_only",
        )

    return ActiveWorkSelection(
        reason="No current sheet reported",
        selected_by="no_current_sheet_reported",
    )


def _validation_summary(state: CheckpointState) -> tuple[str, int, list[str], float | None]:
    failed: list[str] = []
    total_details = 0
    passed_details = 0
    any_passed = False
    latest_pass_percentage: float | None = None
    failed_pass_percentages: list[float] = []

    for sheet in sorted(state.sheets.values(), key=lambda s: s.sheet_num):
        if sheet.validation_passed is True:
            any_passed = True
        if sheet.last_pass_percentage is not None:
            latest_pass_percentage = sheet.last_pass_percentage

        for name in sheet.failed_validations:
            if name not in failed:
                failed.append(name)

        for detail in sheet.validation_details or []:
            total_details += 1
            passed = bool(detail.get("passed", False))
            if passed:
                passed_details += 1
            else:
                raw_name = (
                    detail.get("description")
                    or detail.get("rule_type")
                    or detail.get("type")
                    or "validation"
                )
                name = str(raw_name)
                if name not in failed:
                    failed.append(name)

        if sheet.validation_passed is False and not sheet.failed_validations:
            fallback_name = f"Sheet {sheet.sheet_num} validation failed"
            if fallback_name not in failed:
                failed.append(fallback_name)

        sheet_has_failure = (
            sheet.validation_passed is False
            or bool(sheet.failed_validations)
            or any(
                not bool(detail.get("passed", False))
                for detail in (sheet.validation_details or [])
            )
        )
        if sheet_has_failure and sheet.last_pass_percentage is not None:
            failed_pass_percentages.append(sheet.last_pass_percentage)

    detail_pass_percentage = (
        round(passed_details / total_details * 100.0, 1) if total_details else None
    )
    if latest_pass_percentage is None and detail_pass_percentage is not None:
        latest_pass_percentage = detail_pass_percentage

    if failed:
        failed_percentage = (
            round(sum(failed_pass_percentages) / len(failed_pass_percentages), 1)
            if failed_pass_percentages
            else detail_pass_percentage
        )
        return "validation_failed", len(failed), failed, failed_percentage
    if any_passed or total_details:
        return "passed", 0, [], latest_pass_percentage
    return "unavailable", 0, [], None


def _retry_resume_metadata(
    state: CheckpointState,
    active: ActiveWorkSelection,
) -> dict[str, Any]:
    sheet = state.sheets.get(active.sheet_num) if active.sheet_num is not None else None
    return {
        "total_retry_count": state.total_retry_count,
        "rate_limit_waits": state.rate_limit_waits,
        "quota_waits": state.quota_waits,
        "resume_at": state.resume_at,
        "active_sheet_fire_at": getattr(sheet, "fire_at", None),
        "active_sheet_rate_limit_expires_at": getattr(
            sheet, "rate_limit_expires_at", None
        ),
        "active_sheet_execution_mode": getattr(sheet, "execution_mode", None),
        "active_sheet_attempt_count": getattr(sheet, "attempt_count", None),
        "active_sheet_normal_attempts": getattr(sheet, "normal_attempts", None),
        "active_sheet_max_retries": getattr(sheet, "max_retries", None),
        "fermata_reason": getattr(sheet, "fermata_reason", None),
    }


def _artifact_state_from_checkpoint(state: CheckpointState) -> ArtifactState:
    if not state.worktree_path:
        return ArtifactState(
            state="no_accessible_workspace",
            message="No accessible workspace recorded for this job",
        )

    workspace = Path(state.worktree_path).expanduser()
    workspace_label = str(workspace)
    try:
        if not workspace.exists():
            return ArtifactState(
                state="missing_workspace",
                workspace=workspace_label,
                message=f"Workspace directory not found: {workspace_label}",
            )
        if not workspace.is_dir():
            return ArtifactState(
                state="unavailable",
                workspace=workspace_label,
                message=f"Workspace path is not a directory: {workspace_label}",
            )

        total_files = 0
        for item in workspace.rglob("*"):
            if any(part.startswith(".") for part in item.relative_to(workspace).parts):
                continue
            if item.is_symlink():
                continue
            total_files += 1
            if total_files >= 1000:
                break

        if total_files == 0:
            return ArtifactState(
                state="empty_workspace",
                workspace=workspace_label,
                total_files=0,
                message="Workspace is accessible but contains no visible artifacts",
            )

        return ArtifactState(
            state="available_artifacts",
            workspace=workspace_label,
            total_files=total_files,
            message="Artifacts available; freshness not verified",
        )
    except PermissionError:
        return ArtifactState(
            state="unavailable",
            workspace=workspace_label,
            message=f"Permission denied accessing workspace: {workspace_label}",
        )
    except OSError as exc:
        return ArtifactState(
            state="unavailable",
            workspace=workspace_label,
            message=f"Workspace unavailable: {exc}",
        )


def _log_state_from_checkpoint(state: CheckpointState) -> LogState:
    if not state.worktree_path:
        return LogState(
            state="no_log_source",
            message="No workspace log source recorded for this job",
        )

    workspace = Path(state.worktree_path).expanduser()
    if not workspace.exists() or not workspace.is_dir():
        return LogState(
            state="unavailable",
            message="Workspace log source unavailable",
        )

    candidates = [
        workspace / "marianne.log",
        workspace / "logs" / "marianne.log",
        workspace / ".marianne-observer.jsonl",
    ]
    try:
        candidates.extend(sorted((workspace / "logs").glob("*.log")))
    except OSError:
        pass

    existing: list[Path] = []
    non_empty: list[Path] = []
    for candidate in candidates:
        try:
            if candidate.is_file():
                existing.append(candidate)
                if candidate.stat().st_size > 0:
                    non_empty.append(candidate)
        except OSError:
            continue

    if non_empty:
        return LogState(
            state="available",
            message=f"{len(non_empty)} log source{'s' if len(non_empty) != 1 else ''} available",
            sources=[str(path) for path in non_empty],
        )
    if existing:
        return LogState(
            state="no_log_lines",
            message="Log sources exist but contain no lines",
            sources=[str(path) for path in existing],
        )
    return LogState(
        state="no_log_source",
        message="No workspace log source found",
    )


def resolve_job_workspace(state: CheckpointState, job_id: str) -> Path:
    """Resolve workspace path from job state's worktree path.

    Only works for worktree-isolated jobs. Non-isolated jobs do not store
    a workspace path in state, so this function will raise 404 for them.

    Args:
        state: Loaded checkpoint state (must have worktree_path set).
        job_id: Job identifier (for error messages).

    Returns:
        Resolved workspace Path.

    Raises:
        HTTPException: 404 if no worktree_path is set on the state.
    """
    if state.worktree_path:
        return Path(state.worktree_path)
    raise HTTPException(
        status_code=404,
        detail=f"No accessible workspace found for job {job_id}. "
               f"Job may not be using worktree isolation.",
    )


async def get_job_or_404(
    backend: StateBackend, job_id: str
) -> CheckpointState:
    """Load job state or raise 404 if not found.

    Consolidates the repeated load→check→raise pattern used
    across multiple route handlers.

    Args:
        backend: State backend to load from.
        job_id: Job identifier.

    Returns:
        Loaded CheckpointState.

    Raises:
        HTTPException: 404 if job not found.
    """
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")
    return state


# ============================================================================
# Response Models (Pydantic schemas for API responses)
# ============================================================================


class SheetSummary(BaseModel):
    """Summarized sheet information for list views."""

    sheet_num: int
    status: SheetStatus
    attempt_count: int = 0
    validation_passed: bool | None = None
    failed_validations: list[str] = Field(default_factory=list)
    last_pass_percentage: float | None = None
    execution_mode: str | None = None
    dispatch_blocked_reason: str | None = None


class JobSummary(BaseModel):
    """Summarized job information for list views."""

    job_id: str
    job_name: str
    status: JobStatus
    total_sheets: int
    completed_sheets: int
    progress_percent: float
    created_at: datetime
    updated_at: datetime
    current_sheet: int | None = None
    active_sheet: int | None = None
    active_sheet_status: SheetStatus | None = None
    active_reason: str
    active_selected_by: str
    dispatch_blocked_reason: str | None = None
    dispatch_blocked_details: dict[str, Any] = Field(default_factory=dict)
    retry_resume_metadata: dict[str, Any] = Field(default_factory=dict)
    validation_state: str
    validation_failed_count: int = 0
    failed_validations: list[str] = Field(default_factory=list)
    validation_pass_percent: float | None = None
    artifact_state: ArtifactState
    log_state: LogState
    total_estimated_cost: float = 0.0
    cost_uncertain: bool = False
    data_source: str = "checkpoint"
    last_updated: datetime
    is_partial: bool = False

    @classmethod
    def from_checkpoint(
        cls,
        state: CheckpointState,
        *,
        data_source: str = "checkpoint",
        is_partial: bool = False,
    ) -> "JobSummary":
        """Create from CheckpointState."""
        completed, total = state.get_progress()
        active = resolve_active_work(state)
        validation_state, failed_count, failed_validations, pass_percent = (
            _validation_summary(state)
        )
        return cls(
            job_id=state.job_id,
            job_name=state.job_name,
            status=state.status,
            total_sheets=total,
            completed_sheets=completed,
            progress_percent=state.get_progress_percent(),
            created_at=state.created_at,
            updated_at=state.updated_at,
            current_sheet=state.current_sheet,
            active_sheet=active.sheet_num,
            active_sheet_status=active.status,
            active_reason=active.reason,
            active_selected_by=active.selected_by,
            dispatch_blocked_reason=active.dispatch_blocked_reason,
            dispatch_blocked_details=active.dispatch_blocked_details,
            retry_resume_metadata=_retry_resume_metadata(state, active),
            validation_state=validation_state,
            validation_failed_count=failed_count,
            failed_validations=failed_validations[:5],
            validation_pass_percent=pass_percent,
            artifact_state=_artifact_state_from_checkpoint(state),
            log_state=_log_state_from_checkpoint(state),
            total_estimated_cost=round(state.total_estimated_cost or 0.0, 4),
            cost_uncertain=any(sheet.cost_uncertain for sheet in state.sheets.values()),
            data_source=data_source,
            last_updated=state.updated_at,
            is_partial=is_partial,
        )


class JobDetail(BaseModel):
    """Full job details including sheet information."""

    job_id: str
    job_name: str
    status: JobStatus
    total_sheets: int
    last_completed_sheet: int
    current_sheet: int | None
    progress_percent: float
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    total_retry_count: int
    rate_limit_waits: int
    sheets: list[SheetSummary]

    @classmethod
    def from_checkpoint(cls, state: CheckpointState) -> "JobDetail":
        """Create from CheckpointState."""
        sheets = [
            SheetSummary(
                sheet_num=s.sheet_num,
                status=s.status,
                attempt_count=s.attempt_count,
                validation_passed=s.validation_passed,
                failed_validations=s.failed_validations,
                last_pass_percentage=s.last_pass_percentage,
                execution_mode=s.execution_mode,
                dispatch_blocked_reason=s.dispatch_blocked_reason,
            )
            for s in sorted(state.sheets.values(), key=lambda x: x.sheet_num)
        ]
        return cls(
            job_id=state.job_id,
            job_name=state.job_name,
            status=state.status,
            total_sheets=state.total_sheets,
            last_completed_sheet=state.last_completed_sheet,
            current_sheet=state.current_sheet,
            progress_percent=state.get_progress_percent(),
            created_at=state.created_at,
            updated_at=state.updated_at,
            started_at=state.started_at,
            completed_at=state.completed_at,
            error_message=state.error_message,
            total_retry_count=state.total_retry_count,
            rate_limit_waits=state.rate_limit_waits,
            sheets=sheets,
        )


class JobStatusResponse(BaseModel):
    """Focused status information for job monitoring."""

    job_id: str
    status: JobStatus
    progress_percent: float
    completed_sheets: int
    total_sheets: int
    current_sheet: int | None
    error_message: str | None
    updated_at: datetime

    @classmethod
    def from_checkpoint(cls, state: CheckpointState) -> "JobStatusResponse":
        """Create from CheckpointState."""
        completed, total = state.get_progress()
        return cls(
            job_id=state.job_id,
            status=state.status,
            progress_percent=state.get_progress_percent(),
            completed_sheets=completed,
            total_sheets=total,
            current_sheet=state.current_sheet,
            error_message=state.error_message,
            updated_at=state.updated_at,
        )


class JobListResponse(BaseModel):
    """Response for job list endpoint."""

    jobs: list[JobSummary]
    total: int
    is_partial: bool = False
    data_source: str = "checkpoint"


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: JobStatus | None = None,
    limit: int = 50,
    backend: StateBackend = Depends(get_state_backend),
) -> JobListResponse:
    """List all jobs with optional status filter.

    Args:
        status: Filter by job status (optional)
        limit: Maximum number of jobs to return
        backend: State backend (injected)

    Returns:
        List of job summaries
    """
    all_jobs = await backend.list_jobs()

    # Apply status filter
    if status is not None:
        all_jobs = [j for j in all_jobs if j.status == status]

    # Apply limit
    limited_jobs = all_jobs[:limit]
    summaries: list[JobSummary] = []
    is_partial = False
    data_sources: set[str] = set()
    for job in limited_jobs:
        metadata = _backend_dashboard_metadata(backend, job.job_id)
        job_is_partial = bool(metadata.get("is_partial", False))
        data_source = str(metadata.get("data_source") or "checkpoint")
        summaries.append(
            JobSummary.from_checkpoint(
                job,
                data_source=data_source,
                is_partial=job_is_partial,
            )
        )
        is_partial = is_partial or job_is_partial
        data_sources.add(data_source)

    return JobListResponse(
        jobs=summaries,
        total=len(all_jobs),
        is_partial=is_partial,
        data_source="mixed" if len(data_sources) > 1 else next(iter(data_sources), "checkpoint"),
    )


@router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: str,
    backend: StateBackend = Depends(get_state_backend),
) -> JobDetail:
    """Get detailed information about a specific job.

    Args:
        job_id: Unique job identifier
        backend: State backend (injected)

    Returns:
        Full job details

    Raises:
        HTTPException: 404 if job not found
    """
    state = await get_job_or_404(backend, job_id)
    return JobDetail.from_checkpoint(state)


@router.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    backend: StateBackend = Depends(get_state_backend),
) -> JobStatusResponse:
    """Get focused status information for a job.

    Lightweight endpoint for polling job progress.

    Args:
        job_id: Unique job identifier
        backend: State backend (injected)

    Returns:
        Job status summary

    Raises:
        HTTPException: 404 if job not found
    """
    state = await get_job_or_404(backend, job_id)
    return JobStatusResponse.from_checkpoint(state)
