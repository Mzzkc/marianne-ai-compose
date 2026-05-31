"""#263: structural guard for the three-store status-consistency invariant.

``JobManager._set_job_status`` is the sole method that keeps the three status
stores (``_job_meta``, ``_live_states``, and the registry) in sync. Its docstring
says "Every job status change MUST go through this method", but that was enforced
only by the docstring — a direct ``self._registry.update_status(...)`` call would
silently diverge ``mzt list`` (registry) from ``mzt status`` (live state).

This test is the structural guard: it asserts that every
``self._registry.update_status`` call in ``manager.py`` lives inside
``_set_job_status`` or one of the explicitly-allowed exception sites (paths where
no in-memory meta/live state exists, or which deliberately reconcile the registry
TO the live authority). A new direct caller anywhere else fails this test with a
pointer to ``_set_job_status``.

The allow-list is the audited result (2026-05-31):
- ``_set_job_status``  — the canonical three-store update.
- ``start``            — orphan recovery at daemon startup; prior-session jobs
                         have no in-memory meta/live yet, only the registry.
- ``submit_job``       — task-creation-failure cleanup; meta is popped first, so
                         only the persistent FAILED record remains to write.
- ``_cancel_cleanup``  — background re-assert after ``cancel_job`` already routed
                         meta/live/registry through ``_set_job_status``.
- ``shutdown``         — final flush; writes registry FROM ``live.status`` (the
                         authority) — reconciliation, never divergence.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ALLOWED_CALLERS = frozenset(
    {"_set_job_status", "start", "submit_job", "_cancel_cleanup", "shutdown"}
)


def _enclosing_funcs_calling_registry_update_status(tree: ast.AST) -> set[str]:
    """Names of functions that contain a ``self._registry.update_status(...)`` call."""
    callers: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._stack: list[str] = []

        def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        visit_FunctionDef = _visit_func  # noqa: N815
        visit_AsyncFunctionDef = _visit_func  # noqa: N815

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            func = node.func
            # match: self._registry.update_status(...)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "update_status"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "_registry"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
            ):
                if self._stack:
                    callers.add(self._stack[-1])
            self.generic_visit(node)

    _Visitor().visit(tree)
    return callers


def test_registry_update_status_only_called_from_allowed_sites() -> None:
    manager_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "marianne" / "daemon" / "manager.py"
    )
    tree = ast.parse(manager_src.read_text(encoding="utf-8"))
    callers = _enclosing_funcs_calling_registry_update_status(tree)

    assert callers, "expected to find self._registry.update_status calls in manager.py"
    # _set_job_status must remain the canonical path.
    assert "_set_job_status" in callers

    rogue = callers - _ALLOWED_CALLERS
    assert not rogue, (
        "self._registry.update_status() called directly from "
        f"{sorted(rogue)} — this bypasses _set_job_status and diverges the "
        "in-memory meta/live state from the registry (mzt status vs mzt list, "
        "#263). Route status changes through _set_job_status, or — if this is a "
        "genuine no-meta/reconciliation path — add the method to _ALLOWED_CALLERS "
        "with a justification comment."
    )
