"""Path-related remedies for self-healing.

Provides automatic fixes for common path-related issues:
- CreateMissingWorkspaceRemedy: Creates missing workspace directories
- CreateMissingParentDirsRemedy: Creates missing parent directories
- FixPathSeparatorsRemedy: Fixes Windows path separators on Unix
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from marianne.core.errors.codes import ErrorCode
from marianne.healing.diagnosis import Diagnosis
from marianne.healing.remedies.base import BaseRemedy, RemedyCategory, RemedyResult, RiskLevel

if TYPE_CHECKING:
    from marianne.healing.context import ErrorContext


class CreateMissingWorkspaceRemedy(BaseRemedy):
    """Creates missing workspace directories.

    Triggers when:
    - Error code E601 (PREFLIGHT_PATH_MISSING)
    - Error message mentions workspace directory
    - Parent directory exists (so we're not creating deep trees)

    This is the highest-confidence, lowest-risk remedy - creating
    a single directory is always safe and reversible.
    """

    @property
    def name(self) -> str:
        return "create_missing_workspace"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.AUTOMATIC

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def description(self) -> str:
        return "Creates missing workspace directory if parent exists"

    def diagnose(self, context: "ErrorContext") -> Diagnosis | None:
        """Check if workspace is missing but parent exists."""
        # Check for workspace-related error codes
        if context.error_code not in (
            ErrorCode.PREFLIGHT_PATH_MISSING.value,
            ErrorCode.VALIDATION_FILE_MISSING.value,
        ):
            return None

        # Check for workspace-related message patterns
        workspace_patterns = [
            r"workspace.*does not exist",
            r"workspace.*not found",
            r"missing.*workspace",
            r"directory.*does not exist.*workspace",
        ]

        message_lower = context.error_message.lower()
        if not any(re.search(p, message_lower) for p in workspace_patterns):
            return None

        # Get the workspace path
        workspace = context.workspace
        if workspace is None:
            return None

        # Already exists - no fix needed
        if workspace.exists():
            return None

        # Parent must exist for this simple fix
        if not workspace.parent.exists():
            return None

        return Diagnosis(
            error_code=context.error_code,
            issue=f"Workspace directory does not exist: {workspace}",
            explanation="The configured workspace directory hasn't been created yet. "
            "This is common on first run or when using a new workspace path.",
            suggestion=f"Create directory: {workspace}",
            confidence=0.95,  # Very high confidence for this pattern
            remedy_name=self.name,
            requires_confirmation=False,
            context={"workspace_path": str(workspace)},
        )

    def preview(self, context: "ErrorContext") -> str:
        workspace = context.workspace
        return f"Create workspace directory: {workspace}"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Create the workspace directory."""
        workspace = context.workspace
        if workspace is None:
            return RemedyResult(
                success=False,
                message="No workspace path in context",
                action_taken="nothing",
            )

        try:
            workspace.mkdir(parents=False, exist_ok=False)
            return RemedyResult(
                success=True,
                message=f"Created workspace directory: {workspace}",
                action_taken=f"mkdir {workspace}",
                rollback_command=f"rmdir {workspace}",
                created_paths=[workspace],
            )
        except FileExistsError:
            return RemedyResult(
                success=True,
                message=f"Workspace already exists: {workspace}",
                action_taken="no change needed",
            )
        except OSError as e:
            return RemedyResult(
                success=False,
                message=f"Failed to create workspace: {e}",
                action_taken="mkdir failed",
            )

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        workspace = context.workspace
        return (
            f"The workspace directory does not exist: {workspace}\n\n"
            f"To fix manually, run:\n"
            f"  mkdir -p {workspace}\n\n"
            f"Or update your configuration to use an existing directory."
        )


class CreateMissingParentDirsRemedy(BaseRemedy):
    """Creates missing parent directories for validation paths.

    Triggers when:
    - Error relates to a file path that doesn't exist
    - The missing path is for an output/validation file
    - Multiple directories need to be created

    Slightly lower confidence than workspace remedy since it
    creates potentially multiple directories.
    """

    @property
    def name(self) -> str:
        return "create_missing_parent_dirs"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.AUTOMATIC

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def description(self) -> str:
        return "Creates missing parent directories for output files"

    def diagnose(self, context: "ErrorContext") -> Diagnosis | None:
        """Check for missing parent directories in validation paths."""
        # Look for path-related error codes
        if context.error_code not in (
            ErrorCode.PREFLIGHT_PATH_MISSING.value,
            ErrorCode.VALIDATION_FILE_MISSING.value,
            ErrorCode.CONFIG_MISSING_FIELD.value,
        ):
            return None

        # Try to extract path from error message
        path_patterns = [
            r"directory.*'([^']+)'.*does not exist",
            r"parent.*'([^']+)'.*missing",
            r"cannot create.*'([^']+)'",
            r"path '([^']+)' not found",
        ]

        missing_path = None
        for pattern in path_patterns:
            match = re.search(pattern, context.error_message, re.IGNORECASE)
            if match:
                missing_path = Path(match.group(1))
                break

        if missing_path is None:
            return None

        # Skip if path already exists
        if missing_path.exists():
            return None

        # Find the highest non-existent parent
        dirs_to_create: list[Path] = []
        current = missing_path if missing_path.suffix == "" else missing_path.parent

        while current and not current.exists():
            dirs_to_create.insert(0, current)
            current = current.parent

        if not dirs_to_create:
            return None

        return Diagnosis(
            error_code=context.error_code,
            issue=f"Parent directories missing for: {missing_path}",
            explanation=(
                f"Need to create {len(dirs_to_create)} director(ies): "
                f"{', '.join(str(d) for d in dirs_to_create)}"
            ),
            suggestion=f"Create parent directories: mkdir -p {dirs_to_create[-1]}",
            confidence=0.85,  # Good confidence
            remedy_name=self.name,
            requires_confirmation=False,
            context={"paths_to_create": [str(p) for p in dirs_to_create]},
        )

    def preview(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if diagnosis and diagnosis.context.get("paths_to_create"):
            paths = diagnosis.context["paths_to_create"]
            return f"Create directories: {', '.join(paths)}"
        return "Create missing parent directories"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Create missing parent directories."""
        diagnosis = self.diagnose(context)
        if not diagnosis or not diagnosis.context.get("paths_to_create"):
            return RemedyResult(
                success=False,
                message="Could not determine directories to create",
                action_taken="nothing",
            )

        paths_to_create = [Path(p) for p in diagnosis.context["paths_to_create"]]
        created: list[Path] = []

        try:
            for path in paths_to_create:
                if not path.exists():
                    path.mkdir(parents=True, exist_ok=True)
                    created.append(path)

            return RemedyResult(
                success=True,
                message=f"Created {len(created)} director(ies)",
                action_taken=f"mkdir -p {paths_to_create[-1]}",
                rollback_command=f"rmdir {' '.join(str(p) for p in reversed(created))}",
                created_paths=created,
            )
        except OSError as e:
            return RemedyResult(
                success=False,
                message=f"Failed to create directories: {e}",
                action_taken="mkdir failed",
                created_paths=created,
            )

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if diagnosis and diagnosis.context.get("paths_to_create"):
            paths = diagnosis.context["paths_to_create"]
            return (
                f"Parent directories are missing.\n\n"
                f"To fix manually, run:\n"
                f"  mkdir -p {paths[-1]}\n"
            )
        return "One or more parent directories do not exist."


class FixPathSeparatorsRemedy(BaseRemedy):
    """Fixes Windows path separators on Unix systems.

    Triggers when:
    - Running on Unix (not Windows)
    - Paths in config contain backslashes
    - Error relates to file not found

    This is an automatic fix because it's non-destructive
    (only affects in-memory config, not files on disk).
    """

    @property
    def name(self) -> str:
        return "fix_path_separators"

    @property
    def category(self) -> RemedyCategory:
        return RemedyCategory.AUTOMATIC

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.LOW

    @property
    def description(self) -> str:
        return "Fixes Windows-style path separators (backslashes) on Unix"

    def diagnose(self, context: "ErrorContext") -> Diagnosis | None:
        """Check for Windows path separators on Unix."""
        import sys

        # Only relevant on Unix
        if sys.platform == "win32":
            return None

        # Look for path-related errors
        if context.error_code not in (
            ErrorCode.PREFLIGHT_PATH_MISSING.value,
            ErrorCode.VALIDATION_FILE_MISSING.value,
            ErrorCode.CONFIG_MISSING_FIELD.value,
            ErrorCode.CONFIG_PATH_NOT_FOUND.value,
        ):
            return None

        # Check if error message contains backslashes
        if "\\" not in context.error_message:
            return None

        # Extract the problematic path
        path_match = re.search(r"[A-Za-z]?[:\\][^\s'\"]+", context.error_message)
        if not path_match:
            return None

        bad_path = path_match.group(0)
        fixed_path = bad_path.replace("\\", "/")

        return Diagnosis(
            error_code=context.error_code,
            issue=f"Windows-style path separators detected: {bad_path}",
            explanation="Backslash path separators don't work on Unix systems.",
            suggestion=f"Convert to Unix path: {fixed_path}",
            confidence=0.90,
            remedy_name=self.name,
            requires_confirmation=False,
            context={
                "original_path": bad_path,
                "fixed_path": fixed_path,
            },
        )

    def preview(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if diagnosis:
            orig = diagnosis.context['original_path']
            fixed = diagnosis.context['fixed_path']
            return f"Convert path: {orig} → {fixed}"
        return "Convert Windows-style paths to Unix"

    def apply(self, context: "ErrorContext") -> RemedyResult:
        """Note: This remedy doesn't modify files - it's informational.

        The actual fix needs to be applied in the config loader.
        This remedy serves to diagnose and suggest the fix.
        """
        diagnosis = self.diagnose(context)
        if not diagnosis:
            return RemedyResult(
                success=False,
                message="No Windows paths detected",
                action_taken="nothing",
            )

        # This remedy is informational - the actual fix needs to happen
        # in the config file, not at runtime
        return RemedyResult(
            success=True,
            message=f"Detected Windows path: {diagnosis.context['original_path']}. "
            f"Update config file to use: {diagnosis.context['fixed_path']}",
            action_taken="diagnosis provided",
        )

    def generate_diagnostic(self, context: "ErrorContext") -> str:
        diagnosis = self.diagnose(context)
        if diagnosis:
            original = diagnosis.context["original_path"]
            fixed = diagnosis.context["fixed_path"]
            return (
                f"Windows-style path separators detected.\n\n"
                f"Original: {original}\n"
                f"Fixed:    {fixed}\n\n"
                f"Update your configuration file to use forward slashes (/) "
                f"instead of backslashes (\\) for paths."
            )
        return "Path separators may be incorrect for this platform."
