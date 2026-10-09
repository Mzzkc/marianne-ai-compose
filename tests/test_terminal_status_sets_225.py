"""GH #225: terminal-status sets have one enum-owned definition each.

Sheet terminal set lives beside ``SheetStatus``; job terminal set beside
``JobStatus``; the daemon's ``DaemonJobStatus`` mirrors it by member NAME.
Every other module aliases those — none redefines a literal set.
"""

from __future__ import annotations

from marianne.core.checkpoint import (
    TERMINAL_JOB_STATUSES,
    TERMINAL_SHEET_STATUSES,
    JobStatus,
    SheetStatus,
)
from marianne.daemon import manager as manager_mod
from marianne.daemon.baton import state as baton_state
from marianne.daemon.registry import (
    _TERMINAL_STATUSES,
    TERMINAL_DAEMON_JOB_STATUSES,
    DaemonJobStatus,
)
from marianne.dashboard import routes as dashboard_routes


def test_sheet_terminal_set_is_aliased_not_redefined() -> None:
    assert baton_state._TERMINAL_BATON_STATUSES is TERMINAL_SHEET_STATUSES
    for s in SheetStatus:
        assert s.is_terminal == (s in TERMINAL_SHEET_STATUSES)


def test_job_terminal_set_is_aliased_not_redefined() -> None:
    assert manager_mod._TERMINAL_CHECKPOINT_STATUSES is TERMINAL_JOB_STATUSES
    assert dashboard_routes.TERMINAL_JOB_STATUSES is TERMINAL_JOB_STATUSES
    for s in JobStatus:
        assert s.is_terminal == (s in TERMINAL_JOB_STATUSES)
    assert not JobStatus.PAUSED.is_terminal and not JobStatus.PAUSED_AT_CHAIN.is_terminal


def test_daemon_job_terminal_set_mirrors_checkpoint_by_name() -> None:
    assert {s.name for s in TERMINAL_DAEMON_JOB_STATUSES} == {s.name for s in TERMINAL_JOB_STATUSES}
    assert frozenset(s.value for s in TERMINAL_DAEMON_JOB_STATUSES) == _TERMINAL_STATUSES
    for s in DaemonJobStatus:
        assert s.is_terminal == (s in TERMINAL_DAEMON_JOB_STATUSES)
