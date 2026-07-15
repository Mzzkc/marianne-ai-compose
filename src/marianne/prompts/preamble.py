"""Dynamic, context-aware preamble building for Marianne-orchestrated agents.

Generates preambles that tell agents who they are, where they are in the
concert, and what success looks like. Replaces the static 5-rule warning
label that was previously hardcoded in the retired native CLI executor.
"""

from pathlib import Path
from typing import Any

from marianne import __version__
from marianne.prompts.failure_evidence import format_failure_evidence


def build_preamble(
    sheet_num: int,
    total_sheets: int,
    workspace: Path,
    retry_count: int = 0,
    is_parallel: bool = False,
    healing_context: dict[str, Any] | None = None,
) -> str:
    """Build a context-aware preamble for a Marianne-orchestrated agent.

    Args:
        sheet_num: Current sheet number (1-indexed).
        total_sheets: Total number of sheets in the concert.
        workspace: Workspace directory path.
        retry_count: Number of previous failed attempts (0 = first run).
        is_parallel: Whether parallel execution is enabled.
        healing_context: Failure signals from the prior attempt (#201). When
            present on a retry, rendered into a bounded UNTRUSTED-EVIDENCE block
            so the agent knows specifically what failed instead of retrying
            blind. Carries the raw ``SheetState`` fields (error_code,
            error_message, validation_details, grounding_guidance, stderr_tail,
            error_history); ``None`` keys degrade gracefully.

    Returns:
        Preamble string wrapped in ``<marianne-preamble>`` tags.
    """
    if retry_count > 0:
        evidence = _render_failure_evidence(healing_context)
        return _build_retry_preamble(
            sheet_num, total_sheets, workspace, retry_count,
            failure_evidence=evidence,
        )
    return _build_first_run_preamble(
        sheet_num, total_sheets, workspace, is_parallel,
    )


def _render_failure_evidence(healing_context: dict[str, Any] | None) -> str | None:
    """Render the healing_context dict into a failure-evidence block, or None."""
    if not healing_context:
        return None
    return format_failure_evidence(
        error_code=healing_context.get("error_code"),
        error_message=healing_context.get("error_message"),
        validation_details=healing_context.get("validation_details"),
        grounding_guidance=healing_context.get("grounding_guidance"),
        stderr_tail=healing_context.get("stderr_tail"),
        error_history=healing_context.get("error_history"),
        # #133: runtime diagnostics attached by the adapter when the daemon
        # injected a provider; absent keys degrade gracefully.
        observer_events=healing_context.get("observer_events"),
        resources=healing_context.get("resources"),
    )


def _build_first_run_preamble(
    sheet_num: int,
    total_sheets: int,
    workspace: Path,
    is_parallel: bool,
) -> str:
    """Build preamble for first execution attempt."""
    lines = [
        "<marianne-preamble>",
        f"You are sheet {sheet_num} of {total_sheets} in a Marianne concert.",
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
        "",
        "If you commit to a git repository, end your commit message with:",
        f"Co-Authored-By: Marianne AI Compose v{__version__} <noreply@marianne.ai>",
        "</marianne-preamble>",
    ])

    return "\n".join(lines)


def _build_retry_preamble(
    sheet_num: int,
    total_sheets: int,
    workspace: Path,
    retry_count: int,
    failure_evidence: str | None = None,
) -> str:
    """Build preamble for a retry attempt.

    When ``failure_evidence`` is present (#201), the generic "study the
    workspace" guidance is replaced with the specific, bounded evidence block
    from the prior attempt so the agent self-corrects against concrete signals.
    """
    lines = [
        "<marianne-preamble>",
        f"RETRY #{retry_count}",
        f"You are sheet {sheet_num} of {total_sheets} in a Marianne concert.",
        f"Workspace: {workspace}",
        "",
    ]

    if failure_evidence:
        lines.extend([
            "The previous attempt failed. The diagnostic evidence below shows what",
            "specifically went wrong — address it, and do not repeat the same approach.",
            "",
            failure_evidence,
            "",
        ])
    else:
        lines.extend([
            "The previous attempt failed validation. Study the workspace for evidence",
            "of what went wrong and do not repeat the same approach.",
            "",
        ])

    lines.extend([
        "Your prompt describes intent, not a prescription. Use your judgment — adapt",
        "the approach if the codebase, context, or evidence demands it. Code samples",
        "in the prompt are illustrations, not copy-paste targets.",
        "",
        "Success: all validation requirements (at the end of your prompt) pass on the",
        "first automated check. Read them before you begin.",
        "",
        "Write all outputs to your workspace. Exit with no background processes.",
        "",
        "If you commit to a git repository, end your commit message with:",
        f"Co-Authored-By: Marianne AI Compose v{__version__} <noreply@marianne.ai>",
        "</marianne-preamble>",
    ])

    return "\n".join(lines)
