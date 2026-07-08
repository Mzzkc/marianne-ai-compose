"""Artifacts API endpoints for workspace file access."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from marianne.dashboard.app import get_state_backend
from marianne.dashboard.routes import resolve_job_workspace
from marianne.state.base import StateBackend

router = APIRouter(prefix="/api/jobs", tags=["Artifacts"])


# ============================================================================
# Response Models (Pydantic schemas for API responses)
# ============================================================================


class FileInfo(BaseModel):
    """Information about a workspace file."""
    name: str
    path: str
    type: str  # 'file' or 'directory'
    size: int | None = None  # bytes, None for directories
    modified: float | None = None  # Unix timestamp
    mime_type: str | None = None  # MIME type for files


class ArtifactListResponse(BaseModel):
    """Response for listing workspace artifacts."""
    job_id: str
    workspace: str
    total_files: int
    files: list[FileInfo]
    state: str
    message: str
    freshness_verified: bool = False
    freshness_state: str = "freshness_not_verified"


# ============================================================================
# Helper Functions
# ============================================================================


def _get_file_info(file_path: Path, workspace_root: Path) -> FileInfo:
    """Get FileInfo for a path."""
    relative_path = file_path.relative_to(workspace_root)
    stat = file_path.stat()

    if file_path.is_file():
        mime_type, _ = mimetypes.guess_type(str(file_path))
        return FileInfo(
            name=file_path.name,
            path=str(relative_path),
            type="file",
            size=stat.st_size,
            modified=stat.st_mtime,
            mime_type=mime_type
        )
    else:
        return FileInfo(
            name=file_path.name,
            path=str(relative_path),
            type="directory",
            modified=stat.st_mtime
        )


def _is_safe_path(requested_path: str, workspace_root: Path) -> bool:
    """Check if requested path is safe (no directory traversal or symlink escape).

    Defense-in-depth: rejects symlinks to prevent TOCTOU races where a symlink
    could be swapped to point outside workspace between check and access.
    """
    try:
        full_path = (workspace_root / requested_path).resolve()
        workspace_resolved = workspace_root.resolve()
        if not full_path.is_relative_to(workspace_resolved):
            return False
        # Reject symlinks to prevent TOCTOU: a symlink could be retargeted
        # between this check and the actual file read
        unresolved = workspace_root / requested_path
        return not unresolved.is_symlink()
    except (ValueError, OSError):
        return False


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("/{job_id}/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    job_id: str,
    recursive: bool = True,
    include_hidden: bool = False,
    file_pattern: str | None = None,
    backend: StateBackend = Depends(get_state_backend),
) -> ArtifactListResponse:
    """List files in job workspace.

    Args:
        job_id: Unique job identifier
        recursive: Include files in subdirectories
        include_hidden: Include hidden files (starting with .)
        file_pattern: Glob pattern to filter files (e.g. "*.md", "sheet*")
        backend: State backend (injected)

    Returns:
        List of workspace files and directories

    Raises:
        HTTPException: 404 if job not found, 403 if workspace not accessible
    """
    # Load job state to get workspace
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    workspace = resolve_job_workspace(state, job_id)

    if not workspace.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Workspace directory not found: {workspace}"
        )

    if not workspace.is_dir():
        raise HTTPException(
            status_code=403,
            detail=f"Workspace path is not a directory: {workspace}"
        )

    try:
        files: list[FileInfo] = []

        if recursive:
            # Use rglob for recursive listing
            pattern = file_pattern or "*"
            glob_pattern = f"**/{pattern}" if not pattern.startswith("**/") else pattern

            for item in workspace.glob(glob_pattern):
                # Skip hidden files/dirs unless requested
                workspace_len = len(workspace.parts)
                relative_parts = item.parts[workspace_len:]
                if not include_hidden and any(part.startswith('.') for part in relative_parts):
                    continue
                if item.is_symlink():
                    continue

                try:
                    files.append(_get_file_info(item, workspace))
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue
        else:
            # List only direct children
            for item in workspace.iterdir():
                # Skip hidden files/dirs unless requested
                if not include_hidden and item.name.startswith('.'):
                    continue
                if item.is_symlink():
                    continue

                # Apply pattern filter if provided
                if file_pattern and not item.match(file_pattern):
                    continue

                try:
                    files.append(_get_file_info(item, workspace))
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue

        # Sort files: directories first, then by name
        files.sort(key=lambda f: (f.type == "file", f.name.lower()))

        return ArtifactListResponse(
            job_id=job_id,
            workspace=str(workspace),
            total_files=len(files),
            files=files,
            state="available_artifacts" if files else "empty_workspace",
            message=(
                "Artifacts available; freshness not verified"
                if files
                else "Workspace is accessible but contains no visible artifacts"
            ),
        )

    except PermissionError as e:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied accessing workspace: {workspace}"
        ) from e
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error listing workspace: {e}"
        ) from e


@router.get("/{job_id}/artifacts/{path:path}")
async def get_artifact(
    job_id: str,
    path: str,
    download: bool = False,
    backend: StateBackend = Depends(get_state_backend),
) -> Response:
    """Get content of a specific workspace file.

    Args:
        job_id: Unique job identifier
        path: Relative path to file within workspace
        download: If true, force download instead of inline display
        backend: State backend (injected)

    Returns:
        File content with appropriate content type

    Raises:
        HTTPException: 404 if job/file not found, 403 if not accessible, 400 if path invalid
    """
    # Load job state to get workspace
    state = await backend.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Score not found: {job_id}")

    workspace = resolve_job_workspace(state, job_id)

    if not workspace.exists() or not workspace.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Workspace not accessible: {workspace}"
        )

    # Validate path safety (prevent directory traversal)
    if not _is_safe_path(path, workspace):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: {path}"
        )

    file_path = workspace / path

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {path}"
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Path is not a file: {path}"
        )

    try:
        # Check if file is readable
        if not os.access(file_path, os.R_OK):
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {path}"
            )

        # Get MIME type
        mime_type, _ = mimetypes.guess_type(str(file_path))

        # Determine if file should be served inline or as download
        if download:
            # Force download
            return FileResponse(
                path=file_path,
                filename=file_path.name,
                media_type=mime_type or "application/octet-stream"
            )

        # Serve inline for text files, download for binary
        if mime_type and mime_type.startswith(('text/', 'application/json', 'application/xml')):
            # Read text file content
            try:
                content = file_path.read_text(encoding='utf-8')
                return PlainTextResponse(
                    content=content,
                    media_type=mime_type or "text/plain"
                )
            except UnicodeDecodeError:
                # File claimed to be text but isn't valid UTF-8, serve as binary
                return FileResponse(
                    path=file_path,
                    filename=file_path.name,
                    media_type="application/octet-stream"
                )
        else:
            # Binary file - serve as download
            return FileResponse(
                path=file_path,
                filename=file_path.name,
                media_type=mime_type or "application/octet-stream"
            )

    except PermissionError as e:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied: {path}"
        ) from e
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading file: {e}"
        ) from e
