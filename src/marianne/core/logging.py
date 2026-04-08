"""Structured logging infrastructure for Marianne.

Provides structured JSON logging using structlog with Marianne-specific context
such as job_id, sheet_num, and component names. Supports both console and
file output with log rotation.

Example usage:
    from marianne.core.logging import get_logger, configure_logging, with_context

    # Configure once at startup
    configure_logging(LogConfig(level="DEBUG", format="console"))

    # Get a component-specific logger
    logger = get_logger("runner")

    # Log with auto-context
    logger.info("starting_sheet", sheet_num=5)

    # Bind context for a scope
    ctx_logger = logger.bind(job_id="my-job", sheet_num=1)
    ctx_logger.debug("executing_prompt")

    # Use execution context for automatic correlation
    from marianne.core.logging import ExecutionContext, with_context

    ctx = ExecutionContext(job_id="my-job", run_id="abc-123")
    with with_context(ctx):
        logger.info("sheet_started")  # Automatically includes job_id, run_id
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Literal

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

# Sensitive field patterns that should never be logged
SENSITIVE_PATTERNS = frozenset({
    "api_key",
    "apikey",
    "api-key",
    "token",
    "secret",
    "password",
    "credential",
    "auth",
    "bearer",
    "authorization",
})

# Global state for log file path tracking (for CLI `logs` command)
_current_log_path: Path | None = None


def get_current_log_path() -> Path | None:
    """Get the currently configured log file path.

    Returns:
        The Path to the current log file, or None if file logging is not enabled.
    """
    return _current_log_path


def get_default_log_path(workspace: Path) -> Path:
    """Get the default log file path for a workspace.

    The default log location is {workspace}/logs/marianne.log

    Args:
        workspace: The workspace directory.

    Returns:
        Path to the default log file location.
    """
    return workspace / "logs" / "marianne.log"


def find_log_files(workspace: Path, log_path: Path | None = None) -> list[Path]:
    """Find all log files for a workspace.

    Searches for the main log file and any rotated/compressed backups.

    Args:
        workspace: The workspace directory.
        log_path: Optional specific log path. If None, uses default location.

    Returns:
        List of paths to all log files (current + compressed backups),
        sorted from newest to oldest.
    """
    if log_path is None:
        log_path = get_default_log_path(workspace)

    files: list[Path] = []

    # Current log file
    if log_path.exists():
        files.append(log_path)

    # Look for compressed backups (.1.gz, .2.gz, etc.)
    for i in range(1, 100):  # Reasonable upper bound
        gz_path = log_path.with_suffix(f".log.{i}.gz")
        # Handle case where log_path ends with .log
        if log_path.suffix == ".log":
            gz_path = log_path.parent / f"{log_path.stem}.log.{i}.gz"
            plain_path = log_path.parent / f"{log_path.stem}.log.{i}"
        else:
            plain_path = Path(f"{log_path}.{i}")
            gz_path = Path(f"{log_path}.{i}.gz")

        if gz_path.exists():
            files.append(gz_path)
        elif plain_path.exists():
            files.append(plain_path)
        else:
            # Stop when no more backups found
            break

    return files


class CompressingRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that compresses old log files with gzip.

    After rotation, old log files are compressed to .gz format to save disk space.
    For example, marianne.log.1 becomes marianne.log.1.gz.

    This handler extends the standard RotatingFileHandler with:
    - Automatic gzip compression of rotated files
    - Configurable compression level (default: 9 for best compression)
    - Cleanup of temporary files on compression failure

    Example:
        handler = CompressingRotatingFileHandler(
            "logs/marianne.log",
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=5,
            compress_level=9,
        )
    """

    def __init__(
        self,
        filename: str | Path,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        errors: str | None = None,
        compress_level: int = 9,
    ) -> None:
        """Initialize the compressing rotating file handler.

        Args:
            filename: Path to the log file.
            mode: File mode (default 'a' for append).
            maxBytes: Maximum file size before rotation (0 = no rotation).
            backupCount: Number of backup files to keep.
            encoding: File encoding (default None for system default).
            delay: If True, file opening is deferred until first write.
            errors: Error handling mode for encoding errors.
            compress_level: Gzip compression level 1-9 (default 9, best compression).
        """
        self.compress_level = compress_level
        super().__init__(
            filename,
            mode=mode,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            errors=errors,
        )

    def doRollover(self) -> None:
        """Perform log rotation with compression.

        This method:
        1. Closes the current log stream
        2. Rotates existing .gz files (e.g., .2.gz -> .3.gz)
        3. Compresses the current log file to .1.gz
        4. Opens a new log file for writing
        """
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]

        # Rotate existing compressed backups (.2.gz -> .3.gz, etc.)
        # Start from highest and work down to avoid overwrites
        for i in range(self.backupCount - 1, 0, -1):
            src = f"{self.baseFilename}.{i}.gz"
            dst = f"{self.baseFilename}.{i + 1}.gz"
            if os.path.exists(src):
                # Remove destination if it exists (shouldn't normally happen)
                if os.path.exists(dst):
                    os.remove(dst)
                os.rename(src, dst)

        # Compress current log file to .1.gz
        if os.path.exists(self.baseFilename):
            compressed_path = f"{self.baseFilename}.1.gz"
            try:
                with (
                    open(self.baseFilename, "rb") as f_in,
                    gzip.open(
                        compressed_path,
                        "wb",
                        compresslevel=self.compress_level,
                    ) as f_out,
                ):
                    shutil.copyfileobj(f_in, f_out)
                # Only remove original after successful compression
                os.remove(self.baseFilename)
            except OSError:
                # If compression fails, fall back to just renaming
                # (better to have uncompressed backup than lose data)
                if os.path.exists(compressed_path):
                    try:
                        os.remove(compressed_path)
                    except OSError:
                        pass  # Ignore cleanup failures
                try:
                    dst = f"{self.baseFilename}.1"
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(self.baseFilename, dst)
                except OSError:
                    pass  # Ignore if this also fails - just truncate

        # Remove old backups beyond backupCount
        for i in range(self.backupCount + 1, self.backupCount + 10):
            old_gz = f"{self.baseFilename}.{i}.gz"
            old_plain = f"{self.baseFilename}.{i}"
            for old_file in [old_gz, old_plain]:
                if os.path.exists(old_file):
                    try:
                        os.remove(old_file)
                    except OSError:
                        # Can't use logger here (inside log handler = recursion risk)
                        import sys
                        print(
                            f"Warning: failed to remove old log backup {old_file}",
                            file=sys.stderr,
                        )

        # Reopen the base file for writing
        if not self.delay:
            self.stream = self._open()

    def get_log_files(self) -> list[Path]:
        """Get all log files managed by this handler.

        Returns:
            List of paths to all log files (current + compressed backups),
            sorted from newest to oldest.
        """
        files: list[Path] = []

        # Current log file
        base = Path(self.baseFilename)
        if base.exists():
            files.append(base)

        # Compressed backups (sorted by number)
        for i in range(1, self.backupCount + 1):
            gz_path = Path(f"{self.baseFilename}.{i}.gz")
            plain_path = Path(f"{self.baseFilename}.{i}")

            if gz_path.exists():
                files.append(gz_path)
            elif plain_path.exists():
                files.append(plain_path)

        return files


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable context for correlating log entries across an execution.

    Provides tracing/correlation identifiers that are automatically included
    in all log entries when set via `with_context()`.

    Attributes:
        job_id: The job identifier (from config name).
        run_id: Unique execution run ID (UUID), unique per `mzt run` invocation.
        sheet_num: Current sheet number being processed (None if not in sheet).
        component: Component name for the current operation (e.g., "runner", "backend").
        parent_run_id: Optional parent run ID for nested operations (e.g., sub-jobs).
    """

    job_id: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sheet_num: int | None = None
    component: str = "unknown"
    parent_run_id: str | None = None

    def with_sheet(self, sheet_num: int) -> ExecutionContext:
        """Create a new context with the specified sheet number.

        Args:
            sheet_num: The sheet number to set.

        Returns:
            A new ExecutionContext with the sheet_num field updated.
        """
        return ExecutionContext(
            job_id=self.job_id,
            run_id=self.run_id,
            sheet_num=sheet_num,
            component=self.component,
            parent_run_id=self.parent_run_id,
        )

    def with_component(self, component: str) -> ExecutionContext:
        """Create a new context with the specified component.

        Args:
            component: The component name to set.

        Returns:
            A new ExecutionContext with the component field updated.
        """
        return ExecutionContext(
            job_id=self.job_id,
            run_id=self.run_id,
            sheet_num=self.sheet_num,
            component=component,
            parent_run_id=self.parent_run_id,
        )

    def as_child(self, child_run_id: str | None = None) -> ExecutionContext:
        """Create a child context for nested operations.

        Creates a new context where the current run_id becomes the parent_run_id,
        and a new run_id is generated (or uses provided child_run_id).

        Args:
            child_run_id: Optional run ID for the child context.

        Returns:
            A new ExecutionContext as a child of the current context.
        """
        return ExecutionContext(
            job_id=self.job_id,
            run_id=child_run_id or str(uuid.uuid4()),
            sheet_num=self.sheet_num,
            component=self.component,
            parent_run_id=self.run_id,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert context to a dictionary for logging.

        Returns:
            Dictionary with all context fields (excludes None values).
        """
        result: dict[str, Any] = {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "component": self.component,
        }
        if self.sheet_num is not None:
            result["sheet_num"] = self.sheet_num
        if self.parent_run_id is not None:
            result["parent_run_id"] = self.parent_run_id
        return result


# Thread/task-safe context variable for ExecutionContext
# Using ContextVar ensures proper isolation in async code
_current_context: ContextVar[ExecutionContext | None] = ContextVar(
    "marianne_context", default=None
)


def get_current_context() -> ExecutionContext | None:
    """Get the current ExecutionContext if set.

    Returns:
        The current ExecutionContext or None if not in a context block.
    """
    return _current_context.get()


def set_context(ctx: ExecutionContext) -> None:
    """Set the current ExecutionContext.

    Generally prefer using `with_context()` for automatic cleanup.

    Args:
        ctx: The ExecutionContext to set as current.
    """
    _current_context.set(ctx)


def clear_context() -> None:
    """Clear the current ExecutionContext."""
    _current_context.set(None)


@contextmanager
def with_context(ctx: ExecutionContext) -> Iterator[ExecutionContext]:
    """Context manager that sets ExecutionContext for the duration of a block.

    All log calls within the block will automatically include the context fields
    (job_id, run_id, sheet_num, etc.) when the _add_context processor is active.

    Args:
        ctx: The ExecutionContext to use for the block.

    Yields:
        The ExecutionContext that was set.

    Example:
        ctx = ExecutionContext(job_id="my-job", run_id="abc-123")
        with with_context(ctx):
            logger.info("processing")  # Includes job_id, run_id automatically
    """
    token = _current_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_context.reset(token)


def _sanitize_value(
    key: str, value: str | int | float | bool | None,
) -> str | int | float | bool | None:
    """Sanitize potentially sensitive values.

    Args:
        key: The key/field name being logged.
        value: The value to potentially sanitize.

    Returns:
        Original value if safe, "[REDACTED]" if sensitive.
    """
    key_lower = key.lower()
    for pattern in SENSITIVE_PATTERNS:
        if pattern in key_lower:
            return "[REDACTED]"
    return value


def _sanitize_event_dict(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor that sanitizes sensitive fields.

    Args:
        logger: The wrapped logger object.
        method_name: The name of the method called on the logger.
        event_dict: The event dict containing all bound and event data.

    Returns:
        Sanitized event dict with sensitive values redacted.
    """
    # Create a new dict to avoid mutating the original
    sanitized: EventDict = {}
    for key, value in event_dict.items():
        if isinstance(value, dict):
            # Recursively sanitize nested dicts
            sanitized[key] = {
                k: _sanitize_value(k, v) for k, v in value.items()
            }
        else:
            sanitized[key] = _sanitize_value(key, value)
    return sanitized


def _add_timestamp(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor that adds ISO8601 UTC timestamp.

    Args:
        logger: The wrapped logger object.
        method_name: The name of the method called on the logger.
        event_dict: The event dict containing all bound and event data.

    Returns:
        Event dict with timestamp added.
    """
    event_dict["timestamp"] = datetime.now(UTC).isoformat()
    return event_dict


def _add_context(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor that adds ExecutionContext fields to log entries.

    Retrieves the current ExecutionContext from the ContextVar and adds its
    fields (job_id, run_id, sheet_num, etc.) to the event dict. Fields from
    the context are only added if they are not already present in the event
    dict (explicit bindings take precedence).

    Args:
        logger: The wrapped logger object.
        method_name: The name of the method called on the logger.
        event_dict: The event dict containing all bound and event data.

    Returns:
        Event dict with context fields added.
    """
    ctx = get_current_context()
    if ctx is not None:
        # Add context fields that aren't already set (explicit takes precedence)
        ctx_dict = ctx.to_dict()
        for key, value in ctx_dict.items():
            if key not in event_dict:
                event_dict[key] = value
    return event_dict


class MarianneLogger:
    """Marianne-specific logger wrapper around structlog.

    Provides methods for debug, info, warning, error, and critical logging
    with automatic inclusion of Marianne context (job_id, sheet_num, component).

    The logger is bound to a component name and can have additional context
    bound for a specific scope (e.g., job_id, sheet_num).

    Note: This class uses lazy logger initialization to ensure that loggers
    created at module import time still respect configuration set later via
    configure_logging().
    """

    def __init__(
        self,
        component: str,
        **initial_context: Any,
    ) -> None:
        """Initialize a Marianne logger for a component.

        Args:
            component: The component name (e.g., "runner", "backend", "validator").
            **initial_context: Additional context to bind (e.g., job_id).
        """
        self._component = component
        self._context: dict[str, Any] = {"component": component, **initial_context}

    def _get_logger(self) -> structlog.stdlib.BoundLogger:
        """Get the underlying structlog logger with current context.

        This fetches a fresh logger each time to ensure it respects the
        current logging configuration, even if configure_logging() was
        called after this MarianneLogger was created.
        """
        logger: structlog.stdlib.BoundLogger = structlog.get_logger().bind(**self._context)
        return logger

    def bind(self, **context: Any) -> MarianneLogger:
        """Create a new logger with additional bound context.

        Args:
            **context: Additional context to bind (e.g., job_id, sheet_num).

        Returns:
            A new MarianneLogger with the additional context bound.
        """
        new_logger = MarianneLogger.__new__(MarianneLogger)
        new_logger._component = self._component
        new_logger._context = {**self._context, **context}
        return new_logger

    def unbind(self, *keys: str) -> MarianneLogger:
        """Create a new logger with specified keys removed.

        Args:
            *keys: Keys to remove from the bound context.

        Returns:
            A new MarianneLogger with the specified keys unbound.
        """
        new_logger = MarianneLogger.__new__(MarianneLogger)
        new_logger._component = self._component
        new_logger._context = {k: v for k, v in self._context.items() if k not in keys}
        return new_logger

    def debug(self, event: str, **kw: Any) -> None:
        """Log a debug message.

        Args:
            event: The event name (snake_case recommended).
            **kw: Additional key-value pairs to include.
        """
        self._get_logger().debug(event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        """Log an info message.

        Args:
            event: The event name (snake_case recommended).
            **kw: Additional key-value pairs to include.
        """
        self._get_logger().info(event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        """Log a warning message.

        Args:
            event: The event name (snake_case recommended).
            **kw: Additional key-value pairs to include.
        """
        self._get_logger().warning(event, **kw)

    def error(self, event: str, **kw: Any) -> None:
        """Log an error message.

        Args:
            event: The event name (snake_case recommended).
            **kw: Additional key-value pairs to include.
        """
        self._get_logger().error(event, **kw)

    def critical(self, event: str, **kw: Any) -> None:
        """Log a critical message.

        Args:
            event: The event name (snake_case recommended).
            **kw: Additional key-value pairs to include.
        """
        self._get_logger().critical(event, **kw)

    def exception(self, event: str, **kw: Any) -> None:
        """Log an exception with traceback.

        Should be called from within an exception handler.

        Args:
            event: The event name (snake_case recommended).
            **kw: Additional key-value pairs to include.
        """
        self._get_logger().exception(event, **kw)


def _get_console_processors(
    include_timestamps: bool,
    include_context: bool = True,
) -> list[Processor]:
    """Get structlog processors for console output.

    Args:
        include_timestamps: Whether to add timestamps to log entries.
        include_context: Whether to add ExecutionContext fields to log entries.

    Returns:
        List of processors for console rendering.
    """
    processors: list[Processor] = [
        structlog.stdlib.filter_by_level,  # Filter before processing
        structlog.stdlib.add_log_level,
        _sanitize_event_dict,
    ]

    if include_context:
        processors.append(_add_context)

    if include_timestamps:
        processors.append(_add_timestamp)

    processors.extend([
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(colors=True),
    ])

    return processors


def _get_json_processors(
    include_timestamps: bool,
    include_context: bool = True,
) -> list[Processor]:
    """Get structlog processors for JSON output.

    Args:
        include_timestamps: Whether to add timestamps to log entries.
        include_context: Whether to add ExecutionContext fields to log entries.

    Returns:
        List of processors for JSON rendering.
    """
    processors: list[Processor] = [
        structlog.stdlib.filter_by_level,  # Filter before processing
        structlog.stdlib.add_log_level,
        _sanitize_event_dict,
    ]

    if include_context:
        processors.append(_add_context)

    if include_timestamps:
        processors.append(_add_timestamp)

    processors.extend([
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ])

    return processors


def configure_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    format: Literal["json", "console", "both"] = "console",  # noqa: A002
    file_path: Path | None = None,
    max_file_size_mb: int = 50,
    backup_count: int = 5,
    include_timestamps: bool = True,
    include_context: bool = True,
    compress_logs: bool = True,
) -> None:
    """Configure Marianne structured logging.

    This should be called once at application startup before any logging occurs.

    Args:
        level: Minimum log level to capture.
        format: Output format - "json" for structured, "console" for human-readable,
            "both" for console to stderr and JSON to file (requires file_path).
        file_path: Optional file path for log output. Required if format="both".
        max_file_size_mb: Maximum log file size before rotation (MB).
        backup_count: Number of rotated log files to keep.
        include_timestamps: Whether to include ISO8601 timestamps in log entries.
        include_context: Whether to include ExecutionContext fields (job_id, run_id,
            sheet_num) in log entries when a context is active.
        compress_logs: Whether to compress rotated log files with gzip (default: True).

    Raises:
        ValueError: If format="both" but file_path is not provided.
    """
    global _current_log_path

    # Validate configuration
    if format == "both" and file_path is None:
        raise ValueError("file_path is required when format='both'")

    # Set up stdlib logging level
    log_level = getattr(logging, level)

    # Configure handlers
    handlers: list[logging.Handler] = []

    if format == "console" or format == "both":
        # Console handler with colored output
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(log_level)
        handlers.append(console_handler)

    # File handler - created whenever file_path is set, regardless of format
    if file_path:
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Store current log path for CLI access
        _current_log_path = file_path

        # Use compressing handler or standard rotating handler
        if compress_logs:
            file_handler: logging.Handler = CompressingRotatingFileHandler(
                file_path,
                maxBytes=max_file_size_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding="utf-8",
            )
        else:
            file_handler = RotatingFileHandler(
                file_path,
                maxBytes=max_file_size_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding="utf-8",
            )
        file_handler.setLevel(log_level)
        handlers.append(file_handler)
    elif format == "json":
        # JSON to stdout if no file specified
        json_handler = logging.StreamHandler(sys.stdout)
        json_handler.setLevel(log_level)
        handlers.append(json_handler)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add our handlers
    for handler in handlers:
        root_logger.addHandler(handler)

    # Select processors based on format
    if format == "json":
        shared_processors = _get_json_processors(include_timestamps, include_context)
    else:
        # Console or both - use console processors for main output
        shared_processors = _get_console_processors(include_timestamps, include_context)

    # Configure structlog
    # NOTE: cache_logger_on_first_use=False ensures loggers respect runtime config
    # even when created at module import time before configure_logging() is called
    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(component: str, **initial_context: Any) -> MarianneLogger:
    """Get a Marianne logger for a component.

    The returned logger will automatically include ExecutionContext fields
    (job_id, run_id, sheet_num) when logging inside a `with_context()` block.

    Args:
        component: The component name (e.g., "runner", "backend", "validator").
        **initial_context: Additional context to bind (e.g., job_id).

    Returns:
        A MarianneLogger instance bound to the component.

    Example:
        logger = get_logger("runner")
        ctx = ExecutionContext(job_id="my-job")
        with with_context(ctx):
            logger.info("sheet_started")  # Includes job_id, run_id automatically
    """
    return MarianneLogger(component, **initial_context)


# Re-export for convenience
__all__ = [
    "CompressingRotatingFileHandler",
    "ExecutionContext",
    "MarianneLogger",
    "SENSITIVE_PATTERNS",
    "clear_context",
    "configure_logging",
    "find_log_files",
    "get_current_context",
    "get_current_log_path",
    "get_default_log_path",
    "get_logger",
    "set_context",
    "with_context",
]
