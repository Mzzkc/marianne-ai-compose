"""Dynamic, context-aware preamble building for Mozart-orchestrated agents.

Generates preambles that tell agents who they are, where they are in the
concert, and what success looks like. Replaces the static 5-rule warning
label that was previously hardcoded in ClaudeCliBackend.
"""

from pathlib import Path


def build_preamble(
    sheet_num: int,
    total_sheets: int,
    workspace: Path,
    retry_count: int = 0,
    is_parallel: bool = False,
) -> str:
    """Build a context-aware preamble for a Mozart-orchestrated agent.

    Args:
        sheet_num: Current sheet number (1-indexed).
        total_sheets: Total number of sheets in the concert.
        workspace: Workspace directory path.
        retry_count: Number of previous failed attempts (0 = first run).
        is_parallel: Whether parallel execution is enabled.

    Returns:
        Preamble string wrapped in ``<mozart-preamble>`` tags.
    """
    if retry_count > 0:
        return _build_retry_preamble(
            sheet_num, total_sheets, workspace, retry_count,
        )
    return _build_first_run_preamble(
        sheet_num, total_sheets, workspace, is_parallel,
    )


def _build_first_run_preamble(
    sheet_num: int,
    total_sheets: int,
    workspace: Path,
    is_parallel: bool,
) -> str:
    """Build preamble for first execution attempt."""
    lines = [
        "<mozart-preamble>",
        f"You are sheet {sheet_num} of {total_sheets} in a Mozart concert.",
        f"Workspace: {workspace}",
    ]

    if is_parallel:
        lines.append(
            "Other sheets may execute concurrently "
            "— coordinate via workspace files."
        )

    lines.extend([
        "",
        "Your prompt describes intent, not a prescription. Use your judgment — adapt",
        "the approach if the codebase, context, or evidence demands it. Code samples",
        "in the prompt are illustrations, not copy-paste targets.",
        "",
        "Success: all validation requirements (at the end of your prompt) pass on the",
        "first automated check. Read them before you begin.",
        "",
        "Write all outputs to your workspace. Exit with no background processes.",
        "</mozart-preamble>",
    ])

    return "\n".join(lines)


def _build_retry_preamble(
    sheet_num: int,
    total_sheets: int,
    workspace: Path,
    retry_count: int,
) -> str:
    """Build preamble for a retry attempt."""
    lines = [
        "<mozart-preamble>",
        f"RETRY #{retry_count}",
        f"You are sheet {sheet_num} of {total_sheets} in a Mozart concert.",
        f"Workspace: {workspace}",
        "",
        "The previous attempt failed validation. Study the workspace for evidence",
        "of what went wrong and do not repeat the same approach.",
        "",
        "Your prompt describes intent, not a prescription. Use your judgment — adapt",
        "the approach if the codebase, context, or evidence demands it. Code samples",
        "in the prompt are illustrations, not copy-paste targets.",
        "",
        "Success: all validation requirements (at the end of your prompt) pass on the",
        "first automated check. Read them before you begin.",
        "",
        "Write all outputs to your workspace. Exit with no background processes.",
        "</mozart-preamble>",
    ]

    return "\n".join(lines)
