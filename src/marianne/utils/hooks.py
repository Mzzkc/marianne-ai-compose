"""Shared hook utilities.

Contains small pure helpers used by the daemon's inline hook executor
in ``daemon/manager.py``. The former ``execution/hooks.py`` runner-side
hook executor has been removed (Phase 6b of the Pre-Instrument Execution
Atlas) — this module preserves the one utility that was still live:
``expand_hook_variables()``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from marianne.core.logging import get_logger

_logger = get_logger("hooks")


_KNOWN_HOOK_VARS = frozenset({"workspace", "job_id", "sheet_count"})


def expand_hook_variables(
    template: str,
    *,
    workspace: str | Path,
    job_id: str,
    sheet_count: int | None = None,
    for_shell: bool = False,
) -> str:
    """Expand template variables in hook paths/commands.

    Known variables: ``{workspace}``, ``{job_id}``, ``{sheet_count}``.
    Warns on unrecognized ``{var}`` patterns that remain after expansion.

    Args:
        template: Template string with ``{variable}`` placeholders.
        workspace: Workspace path to substitute.
        job_id: Job identifier to substitute.
        sheet_count: Optional sheet count to substitute.
        for_shell: When True, apply ``shlex.quote()`` to variable values
            before substitution. Use this when the expanded result will
            be passed to ``create_subprocess_shell``. Do NOT use when the
            result will be used as a filesystem path (e.g., a run_job
            hook's job_path).
    """
    ws_str = str(workspace)
    jid_str = job_id
    if for_shell:
        ws_str = shlex.quote(ws_str)
        jid_str = shlex.quote(jid_str)

    result = template.replace("{workspace}", ws_str).replace("{job_id}", jid_str)
    if sheet_count is not None:
        sc_str = str(sheet_count)
        if for_shell:
            sc_str = shlex.quote(sc_str)
        result = result.replace("{sheet_count}", sc_str)
    # Warn about unrecognized template variables
    for match in re.finditer(r"\{(\w+)\}", result):
        var_name = match.group(1)
        if var_name not in _KNOWN_HOOK_VARS:
            _logger.warning(
                "unknown_template_variable",
                variable=var_name,
                template=template,
                known_vars=sorted(_KNOWN_HOOK_VARS),
            )
    return result
