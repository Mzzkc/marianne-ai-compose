"""The orchestra view: a public-safe, replayable timeline of the running fleet.

The conductor already publishes every lifecycle event on its EventBus. This
package projects those events into a small, stable vocabulary a viewer can
draw without knowing Marianne internals, keeps the current fleet snapshot,
appends one JSONL line per event to a daily file so any day can be replayed,
and serves both over IPC. Nothing here carries a path, a prompt, a workspace
or an error message: the projection is what a stranger may watch.
"""

from marianne.daemon.orchestra.recorder import (
    ORCHESTRA_EVENTS,
    OrchestraEvent,
    OrchestraRecorder,
    project_event,
)

__all__ = ["ORCHESTRA_EVENTS", "OrchestraEvent", "OrchestraRecorder", "project_event"]
