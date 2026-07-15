"""Error context for self-healing diagnosis.

Captures all diagnostic information needed to analyze failures
and determine appropriate remediation.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marianne.core.checkpoint import ValidationDetailDict
from marianne.core.constants import HEALING_CONTEXT_TAIL_CHARS

if TYPE_CHECKING:
    from marianne.execution.base import ExecutionResult
    from marianne.core.checkpoint import SheetState
    from marianne.core.config import JobConfig


@dataclass
class ErrorContext:
    """Rich context gathered when an error occurs.

    Provides all information needed by the diagnosis engine and
    remedies to understand and potentially fix the error.

    Attributes:
        error_code: Structured error code (e.g., E601, E304)
        error_message: Human-readable error description
        error_category: High-level category (preflight, configuration, etc.)
        exception: Original exception if available
        exit_code: Process exit code (if applicable)
        signal: Termination signal (if applicable)
        stdout_tail: Last portion of stdout output
        stderr_tail: Last portion of stderr output
        config_path: Path to the job configuration file
        config: Parsed JobConfig object
        workspace: Workspace directory path
        sheet_number: Current sheet number
        working_directory: Backend working directory
        environment: Relevant environment variables
        retry_count: Number of retries attempted
        max_retries: Maximum retries configured
        previous_errors: Error codes from previous attempts
    """

    # Error classification
    error_code: str
    error_message: str
    error_category: str
    exception: Exception | None = None

    # Process state
    exit_code: int | None = None
    signal: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    # Execution context
    config_path: Path | None = None
    config: "JobConfig | None" = None
    workspace: Path | None = None
    sheet_number: int = 0
    working_directory: Path | None = None

    # Environment
    environment: dict[str, str] = field(default_factory=dict)

    # History
    retry_count: int = 0
    max_retries: int = 0
    previous_errors: list[str] = field(default_factory=list)

    # Additional metadata
    raw_config_yaml: str | None = None
    validation_details: list[ValidationDetailDict] = field(default_factory=list)

    @classmethod
    def from_execution_result(
        cls,
        result: "ExecutionResult",
        config: "JobConfig",
        config_path: Path | None,
        sheet_number: int,
        error_code: str,
        error_message: str,
        error_category: str,
        retry_count: int = 0,
        max_retries: int = 0,
        previous_errors: list[str] | None = None,
    ) -> "ErrorContext":
        """Create context from an execution result.

        Args:
            result: The failed execution result.
            config: Job configuration.
            config_path: Path to config file.
            sheet_number: Current sheet number.
            error_code: Classified error code.
            error_message: Error message.
            error_category: Error category.
            retry_count: Current retry count.
            max_retries: Maximum retries allowed.
            previous_errors: Error codes from previous attempts.

        Returns:
            ErrorContext with all diagnostic information.
        """
        import os

        # Capture relevant environment variables
        env_vars = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "ANTHROPIC_API_KEY": "***" if os.environ.get("ANTHROPIC_API_KEY") else "",
        }

        return cls(
            error_code=error_code,
            error_message=error_message,
            error_category=error_category,
            exit_code=result.exit_code,
            signal=result.exit_signal,
            stdout_tail=result.stdout[-HEALING_CONTEXT_TAIL_CHARS:] if result.stdout else "",
            stderr_tail=result.stderr[-HEALING_CONTEXT_TAIL_CHARS:] if result.stderr else "",
            config_path=config_path,
            config=config,
            workspace=config.workspace,
            sheet_number=sheet_number,
            working_directory=config.workspace,
            environment=env_vars,
            retry_count=retry_count,
            max_retries=max_retries,
            previous_errors=previous_errors or [],
        )

    @classmethod
    def from_preflight_error(
        cls,
        config: "JobConfig",
        config_path: Path | None,
        error_code: str,
        error_message: str,
        sheet_number: int = 0,
        raw_yaml: str | None = None,
    ) -> "ErrorContext":
        """Create context from a preflight check failure.

        Preflight errors occur before execution starts, so there's
        no ExecutionResult. This is common for validation errors
        like missing workspace directories or invalid templates.

        Args:
            config: Job configuration.
            config_path: Path to config file.
            error_code: Preflight error code.
            error_message: Error description.
            sheet_number: Sheet number (0 if global).
            raw_yaml: Raw YAML content for template analysis.

        Returns:
            ErrorContext for preflight failures.
        """
        import os

        env_vars = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }

        return cls(
            error_code=error_code,
            error_message=error_message,
            error_category="preflight",
            config_path=config_path,
            config=config,
            workspace=config.workspace,
            sheet_number=sheet_number,
            working_directory=config.workspace,
            environment=env_vars,
            raw_config_yaml=raw_yaml,
        )

    @classmethod
    def from_sheet_state(
        cls,
        sheet: "SheetState",
        sheet_number: int,
        error_category: str = "execution",
        *,
        workspace: Path | None = None,
        config_path: Path | None = None,
        config: "JobConfig | None" = None,
    ) -> "ErrorContext":
        """Create context from the baton's per-sheet execution state (#201).

        The baton drives healing from a ``SheetState`` (the unified
        SheetExecutionState), not a full ``ExecutionResult``/``JobConfig`` — so
        this reads what the baton has (error code/message, exit code/signal,
        output tails, attempt counts) and accepts ``workspace``/``config_path``
        explicitly (the baton stores them on its job record).

        Forward-compatible for #133: it can later accept observer/resource data
        as additional optional fields without reshaping callers.
        """
        import os

        env_vars = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "ANTHROPIC_API_KEY": "***" if os.environ.get("ANTHROPIC_API_KEY") else "",
        }

        return cls(
            error_code=sheet.error_code or "E009",
            error_message=sheet.error_message or "",
            error_category=error_category,
            exit_code=sheet.exit_code,
            signal=sheet.exit_signal,
            stdout_tail=sheet.stdout_tail or "",
            stderr_tail=sheet.stderr_tail or "",
            config_path=config_path,
            config=config,
            workspace=workspace,
            sheet_number=sheet_number,
            working_directory=workspace,
            environment=env_vars,
            retry_count=sheet.normal_attempts,
            max_retries=sheet.max_retries,
        )

    def get_context_summary(self) -> dict[str, Any]:
        """Get a summary of context for logging/display.

        Returns:
            Dictionary with key context information.
        """
        return {
            "error_code": self.error_code,
            "error_category": self.error_category,
            "sheet_number": self.sheet_number,
            "retry_count": self.retry_count,
            "exit_code": self.exit_code,
            "workspace": str(self.workspace) if self.workspace else None,
            "has_stdout": bool(self.stdout_tail),
            "has_stderr": bool(self.stderr_tail),
        }
