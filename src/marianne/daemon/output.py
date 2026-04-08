"""Output protocol for Marianne daemon — decouples execution from Rich console.

Replaces tight coupling to Rich Console with an abstract OutputProtocol.
Implementations:
- NullOutput: no-op for tests
- StructuredOutput: structlog for daemon mode
- ConsoleOutput: wraps Rich for CLI backwards compatibility
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from marianne.daemon.event_bus import EventBus


class OutputProtocol(ABC):
    """Abstract output for job execution feedback.

    Replaces tight coupling to Rich Console. Implementations:
    - NullOutput: no-op for tests
    - StructuredOutput: structlog for daemon
    - ConsoleOutput: wraps Rich for CLI backwards compat
    """

    @abstractmethod
    def log(self, level: str, message: str, **context: Any) -> None: ...

    @abstractmethod
    def progress(
        self,
        job_id: str,
        completed: int,
        total: int,
        eta_seconds: float | None = None,
    ) -> None: ...

    @abstractmethod
    def sheet_event(
        self,
        job_id: str,
        sheet_num: int,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...

    @abstractmethod
    def job_event(
        self,
        job_id: str,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None: ...


class NullOutput(OutputProtocol):
    """No-op output for testing."""

    def log(self, level: str, message: str, **context: Any) -> None:
        pass

    def progress(
        self,
        job_id: str,
        completed: int,
        total: int,
        eta_seconds: float | None = None,
    ) -> None:
        pass

    def sheet_event(
        self,
        job_id: str,
        sheet_num: int,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        pass

    def job_event(
        self,
        job_id: str,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        pass


class StructuredOutput(OutputProtocol):
    """Structured logging output for daemon mode.

    Routes all output through structlog, producing structured JSON events
    that daemon consumers (SSE, gRPC, log aggregators) can parse.
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        from marianne.core.logging import get_logger

        self._logger = get_logger("daemon.output")
        self._event_bus = event_bus

    def log(self, level: str, message: str, **context: Any) -> None:
        getattr(self._logger, level, self._logger.info)(message, **context)

    def progress(
        self,
        job_id: str,
        completed: int,
        total: int,
        eta_seconds: float | None = None,
    ) -> None:
        self._logger.info(
            "job.progress",
            job_id=job_id,
            completed=completed,
            total=total,
            eta_seconds=eta_seconds,
        )

    def sheet_event(
        self,
        job_id: str,
        sheet_num: int,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._logger.info(
            f"sheet.{event}",
            job_id=job_id,
            sheet_num=sheet_num,
            **(data or {}),
        )

    def job_event(
        self,
        job_id: str,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._logger.info(
            f"job.{event}",
            job_id=job_id,
            **(data or {}),
        )


class ConsoleOutput(OutputProtocol):
    """Rich Console wrapper for CLI backwards compatibility.

    Bridges the OutputProtocol to Rich Console, so the existing CLI
    can adopt the protocol without changing its visual output.
    """

    def __init__(self, console: Any | None = None) -> None:
        if console is None:
            from rich.console import Console

            console = Console()
        self._console = console

    def log(self, level: str, message: str, **context: Any) -> None:
        style_map = {
            "debug": "dim",
            "info": "",
            "warning": "yellow",
            "warn": "yellow",
            "error": "red",
            "critical": "bold red",
        }
        style = style_map.get(level, "")
        ctx_str = ""
        if context:
            ctx_str = " " + " ".join(f"{k}={v}" for k, v in context.items())
        if style:
            self._console.print(f"[{style}]{message}{ctx_str}[/{style}]")
        else:
            self._console.print(f"{message}{ctx_str}")

    def progress(
        self,
        job_id: str,
        completed: int,
        total: int,
        eta_seconds: float | None = None,
    ) -> None:
        eta_str = f" (ETA: {eta_seconds:.0f}s)" if eta_seconds is not None else ""
        self._console.print(
            f"[cyan]{job_id}[/cyan] {completed}/{total}{eta_str}"
        )

    def sheet_event(
        self,
        job_id: str,
        sheet_num: int,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        detail = ""
        if data:
            detail = " " + " ".join(f"{k}={v}" for k, v in data.items())
        self._console.print(
            f"[cyan]{job_id}[/cyan] sheet {sheet_num}: [bold]{event}[/bold]{detail}"
        )

    def job_event(
        self,
        job_id: str,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        detail = ""
        if data:
            detail = " " + " ".join(f"{k}={v}" for k, v in data.items())
        self._console.print(
            f"[cyan]{job_id}[/cyan] [bold]{event}[/bold]{detail}"
        )


__all__ = [
    "ConsoleOutput",
    "NullOutput",
    "OutputProtocol",
    "StructuredOutput",
]
