"""Interactive instrument execution — tmux-driven live agent sessions.

Instead of single-pass headless execution (``claude -p``), interactive mode
launches the agent CLI as a live TUI inside an isolated tmux server, submits
the sheet prompt, and a driver state machine pushes the agent to completion.

See docs/specs/2026-06-10-interactive-mode-design.md.
"""

from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.execution.instruments.interactive.driver import (
    ContinuationContext,
    ContinuationPolicy,
    DriverResult,
    InteractiveSessionDriver,
    StaticNudgePolicy,
)
from marianne.execution.instruments.interactive.tmux import TmuxControl, TmuxError

__all__ = [
    "ContinuationContext",
    "ContinuationPolicy",
    "DriverResult",
    "InteractiveCliBackend",
    "InteractiveSessionDriver",
    "StaticNudgePolicy",
    "TmuxControl",
    "TmuxError",
]
