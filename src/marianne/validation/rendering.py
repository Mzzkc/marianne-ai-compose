"""Rendering preview engine for enhanced validation.

Generates previews of how sheets will be rendered at execution time,
including expanded prompts, validation paths, and fan-out metadata.
Used by ``mzt validate`` to show what each sheet will look like
before actually running the job.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass, field
from pathlib import Path

import jinja2

from marianne.core.config import JobConfig
from marianne.core.config.job import InjectionCategory, InjectionItem
from marianne.prompts.templating import PromptBuilder, SheetContext


@dataclass
class ExpandedValidation:
    """A single validation rule with expanded paths and applicability."""

    index: int
    type: str
    description: str | None
    raw_path: str | None
    expanded_path: str | None
    pattern: str | None
    condition: str | None
    applicable: bool


@dataclass
class SheetPreview:
    """Preview of a single sheet's rendered state."""

    sheet_num: int
    item_range: tuple[int, int]
    rendered_prompt: str | None
    prompt_snippet: str
    expanded_validations: list[ExpandedValidation]
    stage: int | None
    instance: int | None
    fan_count: int | None
    render_error: str | None


@dataclass
class RenderingPreview:
    """Complete rendering preview for all sheets in a job."""

    sheets: list[SheetPreview]
    total_sheets: int
    has_fan_out: bool
    has_dependencies: bool
    render_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Condition evaluation (reimplemented from ValidationEngine._check_condition)
# ---------------------------------------------------------------------------

_CONDITION_OPS: dict[str, object] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}


def _check_condition(condition: str | None, context: dict[str, int]) -> bool:
    """Evaluate a validation condition expression against a context.

    Supports compound conditions joined by `` and ``.  Each part is
    ``var op value`` where *op* is one of ``>=``, ``<=``, ``==``,
    ``!=``, ``>``, ``<``.

    Returns ``True`` when *condition* is ``None`` (unconditional).
    """
    if condition is None:
        return True

    condition = condition.strip()

    if " and " in condition:
        parts = condition.split(" and ")
        return all(_check_single(p.strip(), context) for p in parts)

    return _check_single(condition, context)


def _check_single(condition: str, context: dict[str, int]) -> bool:
    """Evaluate a single ``var op value`` comparison."""
    match = re.match(r"(\w+)\s*(>=|<=|==|!=|>|<)\s*(\d+)", condition)
    if not match:
        return True  # unrecognised → treat as unconditional

    var_name, op_str, value_str = match.groups()
    value = int(value_str)

    var_value = context.get(var_name)
    if var_value is None:
        return True  # variable not in context → unconditional

    op_fn = _CONDITION_OPS.get(op_str)
    if op_fn is None:
        return True
    return bool(op_fn(var_value, value))  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Path expansion
# ---------------------------------------------------------------------------


def _expand_path(template: str, context: dict[str, str]) -> str:
    """Expand single-brace ``{variable}`` placeholders in *template*.

    Uses ``str.replace`` rather than ``str.format`` so that unknown
    placeholders are left intact instead of raising ``KeyError``.
    """
    result = template
    for key, val in context.items():
        placeholder = f"{{{key}}}"
        if placeholder in result:
            result = result.replace(placeholder, str(val))
    return result


# ---------------------------------------------------------------------------
# Snippet builder
# ---------------------------------------------------------------------------


def _build_snippet(text: str, max_lines: int = 15) -> str:
    """Return the first *max_lines* lines of *text*, with ``...`` if truncated."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "\n..."


# ---------------------------------------------------------------------------
# Injection resolver (preview-safe — missing files are skipped gracefully)
# ---------------------------------------------------------------------------


def _resolve_injections_preview(
    context: SheetContext,
    items: list[InjectionItem],
    template_vars: dict[str, object],
) -> list[str]:
    """Resolve prelude/cadenza injections for preview.

    Returns a list of warnings for files that could not be resolved.
    Missing files are skipped gracefully (preview time — files may not
    exist yet).
    """
    warnings: list[str] = []

    env = jinja2.Environment(
        undefined=jinja2.Undefined,  # lenient
        autoescape=False,
    )

    for item in items:
        if item.directory is not None:
            # Directory cadenza — resolve path, glob files
            try:
                tmpl = env.from_string(item.directory)
                expanded_path = tmpl.render(**template_vars)
            except jinja2.TemplateError as exc:
                warnings.append(f"Jinja error expanding '{item.directory}': {exc}")
                continue

            dir_path = Path(expanded_path)
            if not dir_path.is_absolute():
                dir_path = Path(str(template_vars.get("workspace", "."))) / dir_path

            if not dir_path.is_dir():
                # At preview time directory may not exist — skip gracefully
                continue

            for file_path in sorted(f for f in dir_path.glob("*") if f.is_file()):
                _inject_file_preview(item, context, file_path)
        else:
            # Single file injection — item.file is guaranteed non-None by validator
            assert item.file is not None
            try:
                tmpl = env.from_string(item.file)
                expanded_path = tmpl.render(**template_vars)
            except jinja2.TemplateError as exc:
                warnings.append(f"Jinja error expanding '{item.file}': {exc}")
                continue

            path = Path(expanded_path)
            if not path.is_absolute():
                path = Path(str(template_vars.get("workspace", "."))) / path

            if not path.is_file():
                continue

            _inject_file_preview(item, context, path)

    return warnings


def _inject_file_preview(
    item: InjectionItem,
    context: SheetContext,
    path: Path,
) -> None:
    """Inject a single file for preview (graceful — no raises)."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    if item.as_ == InjectionCategory.CONTEXT:
        context.injected_context.append(content)
    elif item.as_ == InjectionCategory.SKILL:
        context.injected_skills.append(content)
    elif item.as_ == InjectionCategory.TOOL:
        context.injected_tools.append(content)


# ---------------------------------------------------------------------------
# Main preview generator
# ---------------------------------------------------------------------------


def generate_preview(
    config: JobConfig,
    config_path: Path,
    *,
    max_sheets: int | None = None,
) -> RenderingPreview:
    """Generate a rendering preview for all sheets in a job.

    Builds each sheet's context, renders its prompt, expands validation
    paths, and evaluates condition applicability — all without executing
    anything.

    Args:
        config: Parsed job configuration.
        config_path: Path to the YAML config file (for relative path resolution).
        max_sheets: Cap the number of sheets to preview. ``None`` = all.

    Returns:
        A :class:`RenderingPreview` with per-sheet details.
    """
    _ = config_path  # available for future relative-path resolution

    total_sheets = config.sheet.total_sheets
    preview_count = min(total_sheets, max_sheets) if max_sheets else total_sheets

    has_fan_out = bool(config.sheet.fan_out_stage_map)
    has_dependencies = bool(config.sheet.dependencies)

    builder = PromptBuilder(config.prompt)
    render_errors: list[str] = []
    sheet_previews: list[SheetPreview] = []

    for sheet_num in range(1, preview_count + 1):
        # 1. Build SheetContext
        context = builder.build_sheet_context(
            sheet_num=sheet_num,
            total_sheets=total_sheets,
            sheet_size=config.sheet.size,
            total_items=config.sheet.total_items,
            start_item=config.sheet.start_item,
            workspace=config.workspace,
        )

        # Populate fan-out metadata
        fan_meta = config.sheet.get_fan_out_metadata(sheet_num)
        context.stage = fan_meta.stage
        context.instance = fan_meta.instance
        context.fan_count = fan_meta.fan_count
        context.total_stages = config.sheet.total_stages

        # 2. Resolve prelude/cadenza files
        injection_items: list[InjectionItem] = list(config.sheet.prelude)
        cadenza_items = config.sheet.cadenzas.get(sheet_num, [])
        injection_items.extend(cadenza_items)

        if injection_items:
            template_vars = context.to_dict()
            inj_warnings = _resolve_injections_preview(
                context, injection_items, template_vars
            )
            render_errors.extend(inj_warnings)

        # 3. Render template
        rendered_prompt: str | None = None
        render_error: str | None = None
        try:
            rendered_prompt = builder.build_sheet_prompt(context)
        except jinja2.TemplateError as exc:
            render_error = str(exc)
            render_errors.append(
                f"Sheet {sheet_num}: template render failed — {exc}"
            )

        # 4. Build snippet
        snippet = ""
        if rendered_prompt:
            snippet = _build_snippet(rendered_prompt)
        elif render_error:
            snippet = f"[render error: {render_error}]"

        # 5. Expand validation paths and check applicability
        # Start with user-defined prompt.variables (lowest precedence),
        # then overlay built-in variables so they always win.
        path_context: dict[str, str] = {
            str(k): str(v) for k, v in config.prompt.variables.items()
        }
        path_context.update({
            "workspace": str(config.workspace),
            "sheet_num": str(sheet_num),
            "start_item": str(context.start_item),
            "end_item": str(context.end_item),
            "stage": str(context.stage if context.stage > 0 else sheet_num),
            "instance": str(context.instance),
            "fan_count": str(context.fan_count),
            "total_sheets": str(total_sheets),
            "total_stages": str(
                context.total_stages if context.total_stages > 0 else total_sheets
            ),
        })

        condition_context: dict[str, int] = {
            "sheet_num": sheet_num,
            "start_item": context.start_item,
            "end_item": context.end_item,
            "stage": context.stage if context.stage > 0 else sheet_num,
            "instance": context.instance,
            "fan_count": context.fan_count,
            "total_sheets": total_sheets,
            "total_stages": (
                context.total_stages if context.total_stages > 0 else total_sheets
            ),
        }

        expanded_validations: list[ExpandedValidation] = []
        for idx, rule in enumerate(config.validations):
            expanded_path: str | None = None
            if rule.path:
                try:
                    expanded_path = _expand_path(rule.path, path_context)
                except (KeyError, ValueError):
                    expanded_path = rule.path  # leave raw on error

            applicable = _check_condition(rule.condition, condition_context)

            expanded_validations.append(
                ExpandedValidation(
                    index=idx,
                    type=rule.type,
                    description=rule.description,
                    raw_path=rule.path,
                    expanded_path=expanded_path,
                    pattern=rule.pattern,
                    condition=rule.condition,
                    applicable=applicable,
                )
            )

        sheet_previews.append(
            SheetPreview(
                sheet_num=sheet_num,
                item_range=(context.start_item, context.end_item),
                rendered_prompt=rendered_prompt,
                prompt_snippet=snippet,
                expanded_validations=expanded_validations,
                stage=fan_meta.stage if has_fan_out else None,
                instance=fan_meta.instance if has_fan_out else None,
                fan_count=fan_meta.fan_count if has_fan_out else None,
                render_error=render_error,
            )
        )

    return RenderingPreview(
        sheets=sheet_previews,
        total_sheets=total_sheets,
        has_fan_out=has_fan_out,
        has_dependencies=has_dependencies,
        render_errors=render_errors,
    )
