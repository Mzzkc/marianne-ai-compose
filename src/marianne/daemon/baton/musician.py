"""Baton musician — single-attempt sheet execution.

The musician is the bridge between the baton's dispatch decision and the
backend's execution. It plays ONE attempt, runs validations, records
outcomes, and reports back via SheetAttemptResult into the baton's inbox.

The musician NEVER retries. It NEVER decides completion mode, escalation,
or rate limit recovery. It reports; the conductor (baton) decides.

The 6-step flow:
    1. Build prompt (render template + inject context)
    2. Configure backend (timeout, working directory)
    3. Play: ``backend.execute(prompt, timeout_seconds=sheet.timeout)``
    4. Listen: run validations on output (if execution succeeded)
    5. Record: capture output with credential redaction
    6. Report: put ``SheetAttemptResult`` into baton inbox

Design decisions:
    - The musician is a free function, not a class. No state to manage.
    - Exceptions from the backend are caught and reported, never re-raised.
      The baton must never crash because a backend threw.
    - Credential redaction happens before the result is put in the inbox.
      No unscanned output ever reaches the baton.
    - F-018 contract: when execution succeeds with no validations,
      ``validation_pass_rate`` is set to 100.0. The default 0.0 would
      cause unnecessary retries.
    - F-104: Full Jinja2 prompt rendering with preamble, variable expansion,
      prelude/cadenza injection, and validation requirements. The musician
      renders the template at execution time because cross-sheet context
      only exists after earlier sheets complete.

See: ``docs/plans/2026-03-26-baton-design.md`` — Single-Attempt Musician
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import jinja2

from marianne.backends.base import Backend, ExecutionResult
from marianne.core.config.job import InjectionCategory, InjectionItem
from marianne.core.constants import SHEET_NUM_KEY, TRUNCATE_STDOUT_TAIL_CHARS
from marianne.core.logging import get_logger
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.state import AttemptContext
from marianne.daemon.technique_router import ClassifiedOutput, OutputKind, TechniqueRouter
from marianne.execution.code_mode import (
    CodeExecutionResult,
    CodeExecutionStatus,
    CodeModeExecutor,
    render_code_mode_error,
)
from marianne.prompts.preamble import build_preamble
from marianne.utils.credential_scanner import redact_credentials

_logger = get_logger("daemon.baton.musician")


async def sheet_task(
    *,
    job_id: str,
    sheet: Sheet,
    backend: Backend,
    attempt_context: AttemptContext,
    inbox: asyncio.Queue[SheetAttemptResult],
    total_sheets: int = 1,
    total_movements: int = 1,
    rendered_prompt: str | None = None,
    preamble: str | None = None,
    cost_per_1k_input: float | None = None,
    cost_per_1k_output: float | None = None,
    instrument_override: str | None = None,
    technique_router: TechniqueRouter | None = None,
    code_executor: CodeModeExecutor | None = None,
) -> None:
    """Execute a single sheet attempt and report the result.

    This is the musician's entire job. Play once, report in full detail.
    The conductor (baton) decides what happens next.

    Args:
        job_id: The job this sheet belongs to.
        sheet: The sheet to execute (prompt, validations, timeout, etc.).
        backend: The backend to execute through.
        attempt_context: Context from the conductor (attempt number, mode, etc.).
        inbox: The baton's event inbox to report results to.
        total_sheets: Total concrete sheets in the job (for template variables).
        total_movements: Total movements in the job (for template variables).
        rendered_prompt: Optional pre-rendered prompt from PromptRenderer.
            When provided, the musician uses this directly instead of
            calling _build_prompt(). This enables the full 9-layer
            prompt assembly pipeline including spec fragments, learned
            patterns, and failure history.
        preamble: Optional pre-built preamble. Set on the backend via
            set_preamble() before execution. Only used when rendered_prompt
            is also provided (the PromptRenderer separates them).
        cost_per_1k_input: Cost per 1000 input tokens (USD) from the
            instrument profile's ModelCapacity. None uses hardcoded fallback.
        cost_per_1k_output: Cost per 1000 output tokens (USD) from the
            instrument profile's ModelCapacity. None uses hardcoded fallback.
        instrument_override: When set, used as the instrument_name in the
            SheetAttemptResult instead of sheet.instrument_name. Required
            after instrument fallback — the Sheet entity keeps the original
            instrument but the baton's SheetExecutionState tracks the
            fallback instrument. Without this, attempt results credit
            the wrong instrument for success/failure tracking.

    Never raises — all exceptions are caught and reported via the inbox.
    """
    start_time = time.monotonic()
    # Resolve the effective instrument name — fallback may have changed it
    effective_instrument = instrument_override or sheet.instrument_name

    try:
        # Step 1: Build prompt
        if rendered_prompt is not None:
            # F-104 via PromptRenderer — pre-rendered with all 9 layers.
            # Preamble is separated and set on the backend directly.
            prompt = rendered_prompt
            if preamble is not None:
                backend.set_preamble(preamble)
        else:
            # Fallback: inline rendering (covers basic cases)
            prompt = _build_prompt(
                sheet, attempt_context,
                total_sheets=total_sheets,
                total_movements=total_movements,
            )

        # Step 2-3: Execute through backend
        exec_result = await _execute(backend, prompt, sheet.timeout_seconds)

        # Step 3a: Classify output via TechniqueRouter (Stage 2a).
        # Only runs when a router is provided AND execution succeeded —
        # classification of a failed run's output is not informative, and
        # the field stays None to signal "no classification performed".
        # Downstream stages (Stage 3 code-mode executor, Stage 5 A2A
        # routing) consume classified.kind to decide further action.
        classified = _classify_output(
            technique_router, exec_result, job_id=job_id, sheet_num=sheet.num,
        )
        output_kind_value = classified.kind.value if classified is not None else None

        # Step 3b: Code mode execution (Stage 3).
        # When the classified output contains code blocks AND a code
        # executor is wired into this sheet, run each block in the
        # sandbox. The sandbox output (stdout, stderr, exit code,
        # artifacts summary) is appended to exec_result.stdout so
        # validations can see both the agent's reasoning and the
        # real execution result. On failure, a diagnostic block is
        # appended — the baton's normal retry logic handles the
        # retry path via the standard validation/error flow.
        if (
            code_executor is not None
            and classified is not None
            and classified.kind == OutputKind.CODE_BLOCK
            and classified.code_blocks
            and exec_result.success
        ):
            sandbox_section = await _execute_code_blocks(
                classified.code_blocks,
                code_executor,
                job_id=job_id,
                sheet_num=sheet.num,
            )
            # Append sandbox output to stdout so validations and
            # capture_output see it. ExecutionResult is a mutable
            # dataclass — append in place.
            separator = "" if exec_result.stdout.endswith("\n") else "\n"
            exec_result.stdout = (
                f"{exec_result.stdout}{separator}\n{sandbox_section}"
            )

        # Step 4: Run validations (only if execution succeeded)
        # F-118: pass total_sheets/total_movements for rich context
        val_passed, val_total, val_rate, val_details = await _validate(
            sheet, exec_result,
            total_sheets=total_sheets,
            total_movements=total_movements,
        )

        # Step 5: Record output with credential redaction
        stdout_tail, stderr_tail = _capture_output(exec_result)

        # Step 6: Classify errors (redact credentials from error messages —
        # backend error_message can contain API keys from auth failures,
        # config errors, or URL parameters)
        error_class, raw_error_msg = _classify_error(exec_result)
        error_msg = redact_credentials(raw_error_msg) if raw_error_msg else raw_error_msg

        # Build and report result
        duration = exec_result.duration_seconds
        result = SheetAttemptResult(
            job_id=job_id,
            sheet_num=sheet.num,
            instrument_name=effective_instrument,
            attempt=attempt_context.attempt_number,
            execution_success=exec_result.success,
            exit_code=exec_result.exit_code,
            duration_seconds=duration,
            validations_passed=val_passed,
            validations_total=val_total,
            validation_pass_rate=val_rate,
            validation_details=val_details,
            error_classification=error_class,
            error_message=error_msg,
            rate_limited=exec_result.rate_limited,
            rate_limit_wait_seconds=exec_result.rate_limit_wait_seconds,
            cost_usd=_estimate_cost(
                exec_result,
                cost_per_1k_input=cost_per_1k_input,
                cost_per_1k_output=cost_per_1k_output,
                instrument_name=effective_instrument,
            ),
            input_tokens=exec_result.input_tokens or 0,
            output_tokens=exec_result.output_tokens or 0,
            model_used=exec_result.model,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            output_kind=output_kind_value,
        )

    except Exception as exc:
        # Step 6 (exception path): Never crash the baton
        duration = time.monotonic() - start_time
        raw_error_msg = f"{type(exc).__name__}: {exc}"
        # Redact credentials from exception messages before logging/storing.
        # Exception text can contain API keys (e.g., auth failures that echo
        # the key, config loading errors with key values in paths). Without
        # redaction, credentials propagate to logs, state DB, dashboard,
        # learning store, and diagnostic output — 6+ storage locations.
        error_msg = redact_credentials(raw_error_msg) or raw_error_msg
        _logger.error(
            "musician.sheet_task.exception",
            extra={
                "job_id": job_id,
                SHEET_NUM_KEY: sheet.num,
                "error": error_msg,
            },
            exc_info=True,
        )

        result = SheetAttemptResult(
            job_id=job_id,
            sheet_num=sheet.num,
            instrument_name=effective_instrument,
            attempt=attempt_context.attempt_number,
            execution_success=False,
            exit_code=None,
            duration_seconds=duration,
            error_classification="TRANSIENT",
            error_message=error_msg,
            rate_limited=False,
        )

    # Always report — the baton must know what happened
    await inbox.put(result)
    _logger.info(
        "musician.sheet_task.reported",
        extra={
            "job_id": job_id,
            SHEET_NUM_KEY: sheet.num,
            "success": result.execution_success,
            "pass_rate": result.validation_pass_rate,
            "duration": result.duration_seconds,
        },
    )


# =========================================================================
# Internal helpers
# =========================================================================


def _build_prompt(
    sheet: Sheet,
    context: AttemptContext,
    *,
    total_sheets: int = 1,
    total_movements: int = 1,
) -> str:
    """Build the full execution prompt from the sheet template.

    Performs the complete prompt assembly pipeline (F-104):

    1. **Preamble** — positional identity (sheet N of M, workspace, retry status)
    2. **Template rendering** — Jinja2 with sheet variables (identity, custom vars)
    3. **Prelude/cadenza injection** — resolved file content by category
    4. **Validation requirements** — formatted as success checklist
    5. **Completion suffix** — appended in completion mode

    Args:
        sheet: The sheet to render (carries template, variables, injections).
        context: Attempt context (attempt number, mode, completion suffix).
        total_sheets: Total concrete sheets in the job.
        total_movements: Total movements in the job.

    Returns:
        Fully rendered prompt string ready for backend execution.

    Raises:
        jinja2.UndefinedError: If the template references undefined variables.
    """
    # Step 1: Build preamble
    retry_count = max(0, context.attempt_number - 1)
    preamble = build_preamble(
        sheet_num=sheet.num,
        total_sheets=total_sheets,
        workspace=sheet.workspace,
        retry_count=retry_count,
        is_parallel=sheet.voice_count > 1,
    )

    # Step 2: Build template variables
    template_vars = sheet.template_variables(
        total_sheets=total_sheets,
        total_movements=total_movements,
    )

    # Step 3: Render the Jinja2 template
    rendered_template = _render_template(sheet, template_vars)

    # Step 4: Resolve prelude/cadenza injections
    injected_context, injected_skills, injected_tools = _resolve_injections(
        sheet, template_vars
    )

    # Step 5: Assemble the prompt in the correct order
    # Order: preamble → template → skills/tools → context → validations
    sections: list[str] = [preamble]

    if rendered_template:
        sections.append(rendered_template)

    # Skills/tools injections (early — before context)
    skills_tools = _format_injection_section(injected_skills, injected_tools)
    if skills_tools:
        sections.append(skills_tools)

    # Context injections
    if injected_context:
        context_parts = "\n\n".join(injected_context)
        sections.append(f"## Injected Context\n\n{context_parts}")

    # Validation requirements (last — success criteria)
    if sheet.validations:
        val_section = _format_validation_requirements(sheet.validations, template_vars)
        if val_section:
            sections.append(val_section)

    # Step 6: Completion mode suffix
    if context.completion_prompt_suffix:
        sections.append(context.completion_prompt_suffix)

    return "\n\n".join(sections)


def _render_template(
    sheet: Sheet,
    template_vars: dict[str, Any],
) -> str:
    """Render the sheet's Jinja2 template with variables.

    Loads from template_file if set (takes precedence), otherwise uses
    the inline prompt_template. Returns empty string if neither is set.

    Args:
        sheet: The sheet carrying the template.
        template_vars: Variables for Jinja2 rendering.

    Returns:
        Rendered template string.
    """
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )

    # Template file takes precedence over inline template
    if sheet.template_file and sheet.template_file.exists():
        template_content = sheet.template_file.read_text(encoding="utf-8")
        template = env.from_string(template_content)
        return template.render(**template_vars)

    if sheet.prompt_template:
        template = env.from_string(sheet.prompt_template)
        return template.render(**template_vars)

    return ""


# Binary file extensions for directory cadenza classification
_BINARY_EXTENSIONS: dict[str, str] = {
    ".pdf": "PDF document",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".docx": "Word document",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
}


def _resolve_injections(
    sheet: Sheet,
    template_vars: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    """Resolve prelude and cadenza injection items.

    Reads file content for each injection item, expanding Jinja2 variables
    in file/directory paths. Supports both single-file injection and
    directory cadenzas (glob all files in a directory).

    Missing context files are skipped with a warning. Missing skill/tool
    files are logged as errors but do not crash the musician.

    Args:
        sheet: The sheet carrying prelude/cadenza items.
        template_vars: Variables for Jinja2 path expansion.

    Returns:
        Tuple of (context_contents, skill_contents, tool_contents).
    """
    items: list[InjectionItem] = list(sheet.prelude) + list(sheet.cadenza)

    injected_context: list[str] = []
    injected_skills: list[str] = []
    injected_tools: list[str] = []

    if not items:
        return injected_context, injected_skills, injected_tools

    path_env = jinja2.Environment(
        undefined=jinja2.Undefined,
        autoescape=False,
    )

    for item in items:
        if item.directory is not None:
            _resolve_directory_cadenza(
                item, sheet, path_env, template_vars,
                injected_context, injected_skills, injected_tools,
            )
        else:
            _resolve_file_injection(
                item, sheet, path_env, template_vars,
                injected_context, injected_skills, injected_tools,
            )

    return injected_context, injected_skills, injected_tools


def _resolve_directory_cadenza(
    item: InjectionItem,
    sheet: Sheet,
    env: jinja2.Environment,
    template_vars: dict[str, Any],
    injected_context: list[str],
    injected_skills: list[str],
    injected_tools: list[str],
) -> None:
    """Resolve a directory cadenza — glob files, classify, inject."""
    assert item.directory is not None  # guaranteed by caller
    try:
        tmpl = env.from_string(item.directory)
        expanded_path = tmpl.render(**template_vars)
    except jinja2.TemplateError as e:
        _logger.warning(
            "musician.injection.path_expansion_error",
            extra={"directory": item.directory, "error": str(e)},
        )
        return

    dir_path = Path(expanded_path)
    if not dir_path.is_absolute():
        dir_path = sheet.workspace / dir_path

    if not dir_path.is_dir():
        if item.as_ == InjectionCategory.CONTEXT:
            _logger.info(
                "musician.injection.directory_not_found",
                extra={"directory": str(dir_path), "category": item.as_.value},
            )
        else:
            _logger.error(
                "musician.injection.required_directory_not_found",
                extra={"directory": str(dir_path), "category": item.as_.value},
            )
        return

    files = sorted(f for f in dir_path.glob("*") if f.is_file())
    if not files:
        _logger.info(
            "musician.injection.directory_empty",
            extra={"directory": str(dir_path)},
        )
        return

    for file_path in files:
        _inject_single_file(
            item, file_path, injected_context, injected_skills, injected_tools,
            from_directory=True,
        )


def _resolve_file_injection(
    item: InjectionItem,
    sheet: Sheet,
    env: jinja2.Environment,
    template_vars: dict[str, Any],
    injected_context: list[str],
    injected_skills: list[str],
    injected_tools: list[str],
) -> None:
    """Resolve a single-file injection."""
    assert item.file is not None  # guaranteed by caller
    try:
        tmpl = env.from_string(item.file)
        expanded_path = tmpl.render(**template_vars)
    except jinja2.TemplateError as e:
        _logger.warning(
            "musician.injection.path_expansion_error",
            extra={"file": item.file, "error": str(e)},
        )
        return

    path = Path(expanded_path)
    if not path.is_absolute():
        path = sheet.workspace / path

    if not path.is_file():
        if item.as_ == InjectionCategory.CONTEXT:
            _logger.warning(
                "musician.injection.file_not_found",
                extra={"file": str(path), "category": item.as_.value},
            )
        else:
            _logger.error(
                "musician.injection.required_file_not_found",
                extra={"file": str(path), "category": item.as_.value},
            )
        return

    _inject_single_file(
        item, path, injected_context, injected_skills, injected_tools,
    )


def _inject_single_file(
    item: InjectionItem,
    path: Path,
    injected_context: list[str],
    injected_skills: list[str],
    injected_tools: list[str],
    *,
    from_directory: bool = False,
) -> None:
    """Inject a single file, classifying as text or binary."""
    try:
        content = path.read_text(encoding="utf-8")
        if from_directory:
            header = f"--- Input: {path.name} ---\n\n"
            full_content = header + content
        else:
            full_content = content
    except UnicodeDecodeError:
        ext = path.suffix.lower()
        file_kind = _BINARY_EXTENSIONS.get(ext, "file")
        full_content = (
            f"--- Input: {path.name} (binary — {file_kind}, read with your tools) ---\n\n"
            f"This file cannot be displayed inline. Read it using your file reading tools.\n"
            f"Path: {path.resolve()}\n"
        )
    except OSError as e:
        _logger.warning(
            "musician.injection.file_read_error",
            extra={"file": str(path), "error": str(e)},
        )
        return

    if item.as_ == InjectionCategory.CONTEXT:
        injected_context.append(full_content)
    elif item.as_ == InjectionCategory.SKILL:
        injected_skills.append(full_content)
    elif item.as_ == InjectionCategory.TOOL:
        injected_tools.append(full_content)


def _format_injection_section(
    skills: list[str],
    tools: list[str],
) -> str:
    """Format skills and tools injections into a prompt section.

    Args:
        skills: Skill injection contents.
        tools: Tool injection contents.

    Returns:
        Formatted section string, or empty string if nothing to inject.
    """
    parts: list[str] = []
    if skills:
        parts.append("## Skills\n\n" + "\n\n".join(skills))
    if tools:
        parts.append("## Tools\n\n" + "\n\n".join(tools))
    return "\n\n".join(parts)


def _format_validation_requirements(
    rules: list[Any],
    template_vars: dict[str, Any],
) -> str:
    """Format validation rules as success requirements for the musician.

    Expands template variables in paths/patterns to show actual expected
    values, making the success criteria concrete and actionable.

    Args:
        rules: List of ValidationRule objects.
        template_vars: Template variables for path expansion.

    Returns:
        Formatted markdown section.
    """
    if not rules:
        return ""

    lines = ["---", "## Success Requirements (Validated Automatically)", ""]
    lines.append(
        "Your output will be validated against these requirements. "
        "All must pass for success:"
    )
    lines.append("")

    for i, rule in enumerate(rules, 1):
        description = getattr(rule, "description", None) or getattr(rule, "type", "check")

        # Expand template variables in path
        path = getattr(rule, "path", None) or ""
        if path:
            try:
                path = path.format(**{
                    k: v for k, v in template_vars.items()
                    if isinstance(v, (str, int, float))
                })
            except (KeyError, ValueError):
                pass  # Use unexpanded path

        rule_type = getattr(rule, "type", "unknown")
        if path:
            lines.append(f"  {i}. **{description}** (`{rule_type}`: `{path}`)")
        else:
            lines.append(f"  {i}. **{description}** (`{rule_type}`)")

    lines.append("")
    lines.append(
        "**Important**: These validations run automatically. "
        "Ensure your output matches exactly."
    )
    lines.append("")

    return "\n".join(lines)


def _classify_output(
    router: TechniqueRouter | None,
    exec_result: ExecutionResult,
    *,
    job_id: str,
    sheet_num: int,
) -> ClassifiedOutput | None:
    """Classify agent output via the technique router (Stage 2a/3).

    Returns the ``ClassifiedOutput`` object when classification runs
    successfully, or None when the router is absent or execution failed.
    Callers extract ``.kind.value`` for ``output_kind`` reporting and
    ``.code_blocks`` for Stage 3 code-mode execution.

    Classification is cheap (pure regex). Exceptions are swallowed and
    logged — classification must never crash the musician. A classifier
    bug should degrade to "no classification" rather than failing the
    sheet.

    Args:
        router: Per-job TechniqueRouter instance, or None if no techniques
            are declared for the job.
        exec_result: The backend execution result to classify.
        job_id: Job identifier, for logging context.
        sheet_num: Sheet number, for logging context.

    Returns:
        The ClassifiedOutput object, or None to indicate "not classified".
    """
    if router is None:
        return None
    if not exec_result.success:
        return None

    stdout = exec_result.stdout or ""
    try:
        classified = router.classify(stdout)
    except Exception as exc:
        _logger.warning(
            "musician.technique_router.classify_error",
            extra={
                "job_id": job_id,
                SHEET_NUM_KEY: sheet_num,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return None

    _logger.debug(
        "musician.technique_router.classified",
        extra={
            "job_id": job_id,
            SHEET_NUM_KEY: sheet_num,
            "kind": classified.kind.value,
            "code_block_count": len(classified.code_blocks),
        },
    )
    return classified


async def _execute_code_blocks(
    blocks: list[Any],
    executor: CodeModeExecutor,
    *,
    job_id: str,
    sheet_num: int,
) -> str:
    """Run classified code blocks through the sandboxed executor (Stage 3).

    Delegates to ``CodeModeExecutor.execute_all()`` and formats the
    results into a markdown section suitable for appending to the
    agent's stdout. Each block's stdout, stderr, and exit code are
    captured so downstream validations and the stdout_tail diagnostic
    see the real execution output.

    Failures are NOT fatal — spec section 10.3 says the sheet's retry
    loop sees the error context and tries again. Each failed block
    renders a ``render_code_mode_error()`` block. Success blocks render
    a compact status + stdout/stderr summary.

    Args:
        blocks: The ``CodeBlock`` objects extracted by the router.
        executor: The sandboxed code mode executor.
        job_id: Job identifier, for logging.
        sheet_num: Sheet number, for logging.

    Returns:
        Markdown-formatted sandbox output section. Safe to append
        directly to ``exec_result.stdout``.
    """
    try:
        results = await executor.execute_all(blocks)
    except Exception as exc:
        # A bug in the executor itself must not crash the musician —
        # fall through to a diagnostic block so the agent can retry.
        _logger.error(
            "musician.code_mode.executor_error",
            extra={
                "job_id": job_id,
                SHEET_NUM_KEY: sheet_num,
                "error": f"{type(exc).__name__}: {exc}",
            },
            exc_info=True,
        )
        return (
            "## Sandbox Output\n\n"
            f"**Status:** executor_error\n"
            f"**Error:** {type(exc).__name__}: {exc}\n"
        )

    _logger.info(
        "musician.code_mode.executed",
        extra={
            "job_id": job_id,
            SHEET_NUM_KEY: sheet_num,
            "block_count": len(results),
            "success_count": sum(
                1 for r in results if r.status == CodeExecutionStatus.SUCCESS
            ),
        },
    )
    return _format_sandbox_results(results)


def _format_sandbox_results(results: list[CodeExecutionResult]) -> str:
    """Format code mode results as a markdown stdout section.

    Success blocks render stdout. Failure blocks render the full error
    diagnostic so the agent has actionable context on retry.
    """
    sections: list[str] = ["## Sandbox Output"]
    for idx, result in enumerate(results, start=1):
        sections.append("")
        sections.append(f"### Block {idx}")
        sections.append("")
        if result.status == CodeExecutionStatus.SUCCESS:
            sections.append(f"**Status:** {result.status.value}")
            sections.append(f"**Exit code:** {result.exit_code}")
            if result.stdout:
                sections.append("")
                sections.append("**stdout:**")
                sections.append("```")
                sections.append(result.stdout.rstrip())
                sections.append("```")
            if result.stderr:
                sections.append("")
                sections.append("**stderr:**")
                sections.append("```")
                sections.append(result.stderr.rstrip())
                sections.append("```")
        else:
            sections.append(render_code_mode_error(result))
    return "\n".join(sections)


async def _execute(
    backend: Backend,
    prompt: str,
    timeout_seconds: float,
) -> ExecutionResult:
    """Execute the prompt through the backend.

    This is a thin wrapper that passes the timeout and returns the result.
    All exception handling is done by the caller (sheet_task).
    """
    return await backend.execute(prompt, timeout_seconds=timeout_seconds)


async def _validate(
    sheet: Sheet,
    exec_result: ExecutionResult,
    *,
    total_sheets: int = 1,
    total_movements: int = 1,
) -> tuple[int, int, float, dict[str, Any] | None]:
    """Run validations on the execution output.

    Returns:
        (passed_count, total_count, pass_rate, details_dict)

    F-018 contract: when execution succeeds with no validations,
    pass_rate is 100.0. The default 0.0 would cause unnecessary retries.

    F-118 fix: passes rich sheet context to ValidationEngine, matching
    the runner's behavior. Validations using {workspace}, {movement},
    {job_name} etc. in paths now resolve correctly under the baton path.
    """
    # Don't run validations if execution failed
    if not exec_result.success:
        return 0, 0, 0.0, None

    # No validation rules → 100% pass rate (F-018 contract)
    if not sheet.validations:
        return 0, 0, 100.0, None

    # Build rich context matching the runner's ValidationEngine contract.
    # Uses Sheet.template_variables() which provides all built-in vars
    # (workspace, movement, voice, stage, instance, etc.) plus custom
    # score-level variables. This closes the F-118 gap where the baton
    # musician only passed {SHEET_NUM_KEY: N}.
    sheet_context = sheet.template_variables(
        total_sheets=total_sheets,
        total_movements=total_movements,
    )

    # Run validations through the validation engine
    try:
        from marianne.execution.validation.engine import ValidationEngine

        engine = ValidationEngine(
            workspace=sheet.workspace,
            sheet_context=sheet_context,
        )
        result = await engine.run_validations(sheet.validations)

        passed = result.passed_count
        total = len(result.results)
        rate = result.pass_percentage
        details = {
            "passed": passed,
            "failed": result.failed_count,
            "skipped": result.skipped_count,
            "pass_percentage": rate,
        }
        return passed, total, rate, details

    except Exception as exc:
        # Redact credentials from validation error messages before storing
        error_text = redact_credentials(str(exc)) or str(exc)
        _logger.warning(
            "musician.validation.error",
            extra={
                SHEET_NUM_KEY: sheet.num,
                "error": error_text,
            },
        )
        # Validation engine failure — report as 0% to trigger retry
        return 0, 0, 0.0, {"error": error_text}


def _capture_output(exec_result: ExecutionResult) -> tuple[str, str]:
    """Capture and sanitize output for the result.

    Truncates to TRUNCATE_STDOUT_TAIL_CHARS and redacts credentials.
    """
    stdout = exec_result.stdout or ""
    stderr = exec_result.stderr or ""

    # Truncate
    if len(stdout) > TRUNCATE_STDOUT_TAIL_CHARS:
        stdout = stdout[-TRUNCATE_STDOUT_TAIL_CHARS:]
    if len(stderr) > TRUNCATE_STDOUT_TAIL_CHARS:
        stderr = stderr[-TRUNCATE_STDOUT_TAIL_CHARS:]

    # Credential redaction
    stdout = redact_credentials(stdout) or ""
    stderr = redact_credentials(stderr) or ""

    return stdout, stderr


def _classify_error(
    exec_result: ExecutionResult,
) -> tuple[str | None, str | None]:
    """Classify execution errors for the baton's decision tree.

    Returns:
        (classification, message) — both None for successful executions.

    Classifications:
        AUTH_FAILURE — API key invalid, permission denied
        TRANSIENT — timeout, signal kill, exit_code=None
        EXECUTION_ERROR — other failures
    """
    if exec_result.success:
        return None, None

    if exec_result.rate_limited:
        return None, None  # Rate limits are NOT errors

    # exit_code=None → process killed by signal → TRANSIENT
    if exec_result.exit_code is None:
        return "TRANSIENT", exec_result.error_message or "Process killed by signal"

    # Check for auth failures in error output
    error_text = (exec_result.stderr or "").lower()
    auth_patterns = [
        "authentication",
        "unauthorized",
        "api key",
        "permission denied",
        "invalid_api_key",
        "401",
        "403",
    ]
    if any(pattern in error_text for pattern in auth_patterns):
        return "AUTH_FAILURE", exec_result.error_message or "Authentication failure"

    # Default: EXECUTION_ERROR
    return (
        "EXECUTION_ERROR",
        exec_result.error_message or f"Exit code {exec_result.exit_code}",
    )


def _estimate_cost(
    exec_result: ExecutionResult,
    cost_per_1k_input: float | None = None,
    cost_per_1k_output: float | None = None,
    instrument_name: str | None = None,
) -> float:
    """Estimate cost from token counts and instrument pricing.

    When profile pricing is provided (from InstrumentProfile.ModelCapacity),
    uses that for accurate per-instrument cost tracking. Falls back to
    conservative Claude Sonnet estimates when no pricing is available —
    the fallback is doctrine-flagged (RULE "cost tracking must use
    instrument profile pricing") so the fallback branch now emits a
    warning that names the instrument whose pricing is missing. Phase 5
    wires the warning; follow-up phases must wire pricing into every
    instrument profile so the fallback stops firing in production.

    Args:
        exec_result: The execution result with token counts.
        cost_per_1k_input: Cost per 1000 input tokens (USD) from instrument
            profile. None falls back to hardcoded estimate.
        cost_per_1k_output: Cost per 1000 output tokens (USD) from instrument
            profile. None falls back to hardcoded estimate.
        instrument_name: Name of the instrument whose pricing is being
            estimated. Used only to enrich the fallback warning — the
            numeric fallback is independent of the name today.
    """
    input_tokens = exec_result.input_tokens or 0
    output_tokens = exec_result.output_tokens or 0

    if cost_per_1k_input is not None and cost_per_1k_output is not None:
        # Profile pricing: cost_per_1k means USD per 1000 tokens
        cost = (input_tokens * cost_per_1k_input / 1_000) + (
            output_tokens * cost_per_1k_output / 1_000
        )
    else:
        # Fallback: conservative Claude Sonnet pricing ($3/1M input,
        # $15/1M output). Doctrine-flagged — emit a warning that names
        # the instrument whose pricing is missing so the gap is visible
        # in logs. This satisfies the xfail guard in
        # tests/test_instrument_cost_tracking.py and Doctrine RULE
        # "Cost tracking must use instrument profile pricing".
        cost = (input_tokens * 3.0 / 1_000_000) + (
            output_tokens * 15.0 / 1_000_000
        )
        _logger.warning(
            "musician.cost_pricing_fallback",
            extra={
                "instrument": instrument_name or "unknown",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": cost,
                "note": (
                    "Instrument profile pricing missing — falling back "
                    "to hardcoded Claude Sonnet rates. Add ModelCapacity "
                    "pricing to the instrument profile to fix."
                ),
            },
        )
    return cost
