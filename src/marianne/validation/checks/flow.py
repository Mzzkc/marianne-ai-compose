"""S3 advisory checks against Forge's parsed flow configuration."""

from __future__ import annotations

import re
from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.flow import ConcertTrigger, span_range
from marianne.core.config.instruments import InstrumentProfile
from marianne.core.expressions import parse_expression
from marianne.core.sheet import build_sheets
from marianne.utils.run_supervisor import subreaper_available
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks.structure import _IDENTITY_VARIABLES


def _issue(code: str, severity: ValidationSeverity, message: str) -> ValidationIssue:
    return ValidationIssue(check_id=code, severity=severity, message=message)


class RunOrphanReapingScopeCheck:
    check_id = "V326"
    severity = ValidationSeverity.INFO
    description = "Detached run-action orphan cleanup requires Linux subreaper support"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        if subreaper_available():
            return []
        for trigger in (config.sheet.triggers or {}).values():
            for action in (trigger.on_success or []) + (trigger.on_fail or []):
                if action.run is not None:
                    return [_issue(
                        self.check_id, self.severity,
                        "run: detached, closed-stdio orphan reaping needs Linux "
                        "PR_SET_CHILD_SUBREAPER; this host uses three-source cleanup",
                    )]
        return []


class UndefinedFlowVariableCheck:
    check_id = "V220"
    severity = ValidationSeverity.WARNING
    description = "Loop expressions reference variables absent from prompt.variables"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for span, loop in (config.sheet.loops or {}).items():
            if loop.until is None:
                continue
            for path in parse_expression(loop.until).references().variables:
                name = path[0]
                if name not in config.prompt.variables and name not in _IDENTITY_VARIABLES:
                    issues.append(
                        _issue(
                            self.check_id,
                            self.severity,
                            f"loop {span}: var.{name} is not declared; pass it with --var",
                        )
                    )
        return issues


class BypassedRecoveryCheck:
    check_id = "V224"
    severity = ValidationSeverity.WARNING
    description = "on_fail replaces explicitly configured recovery"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        explicit_retry = bool(
            config.retry.model_fields_set & {"max_retries", "max_completion_attempts"}
        )
        issues = []
        for span, trigger in (config.sheet.triggers or {}).items():
            if not trigger.on_fail:
                continue
            mechanisms = []
            if explicit_retry:
                mechanisms.append("explicit retry/completion settings")
            if config.instrument_fallbacks or config.sheet.per_sheet_fallbacks:
                mechanisms.append("fallback chain")
            if mechanisms:
                issues.append(
                    _issue(
                        self.check_id,
                        self.severity,
                        f"trigger {span}: on_fail bypasses {', '.join(mechanisms)}",
                    )
                )
        return issues


class MixedFanOutKeyingCheck:
    check_id = "V225"
    severity = ValidationSeverity.INFO
    description = "Flow spans use stages while other per-sheet maps use concrete sheets"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        if not config.sheet.fan_out_stage_map or not (config.sheet.loops or config.sheet.triggers):
            return []
        names = [
            name
            for name, mapping in (
                ("cadenzas", config.sheet.cadenzas),
                ("prompt_extensions", config.sheet.prompt_extensions),
                ("per_sheet_instruments", config.sheet.per_sheet_instruments),
            )
            if mapping
        ]
        return [
            _issue(
                self.check_id,
                self.severity,
                f"{name} keys are concrete sheets; flow spans were stage keys",
            )
            for name in names
        ]


class ConstantUntilCheck:
    check_id = "V226"
    severity = ValidationSeverity.WARNING
    description = "Until expressions with no changing references"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for span, loop in (config.sheet.loops or {}).items():
            if loop.until is None:
                continue
            refs = parse_expression(loop.until).references()
            if not (refs.files or refs.sheets or refs.loops):
                issues.append(
                    _issue(
                        self.check_id,
                        self.severity,
                        f"loop {span}: until has no changing file, sheet, or loop reference",
                    )
                )
        return issues


class JinjaFilePathCheck:
    check_id = "V233"
    severity = ValidationSeverity.ERROR
    description = "Jinja braces inside a flow file() path never expand"

    _JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for span, loop in (config.sheet.loops or {}).items():
            if loop.until is None:
                continue
            for path in sorted(parse_expression(loop.until).references().files):
                if self._JINJA.search(path):
                    issues.append(
                        _issue(
                            self.check_id,
                            self.severity,
                            f"loop {span}: file({path!r}) keeps its Jinja braces verbatim and "
                            "never exists; flow file paths are workspace-relative and use "
                            "the validation form, e.g. file(\"outer-done.txt\") or "
                            "file(\"{workspace}/outer-done.txt\")",
                        )
                    )
        return issues


class JinjaLoopCollisionCheck:
    check_id = "V227"
    severity = ValidationSeverity.ERROR
    description = "Jinja loop.X hides the flow index inside for blocks"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        template = config.prompt.template or ""
        return [
            _issue(
                self.check_id,
                self.severity,
                f"Use {{{{ loops.{loop.index} }}}}; Jinja's own loop hides this index",
            )
            for loop in (config.sheet.loops or {}).values()
            if re.search(rf"\bloop\.{re.escape(loop.index)}\b", template)
        ]


def _zero_cost_reason(
    profile: InstrumentProfile | None, name: str, model_name: str | None = None
) -> str | None:
    """Why this route can never report a cost, or ``None`` when it can.

    ``model_name`` is the sheet's pinned model when it has one; the musician
    prices the model that actually runs, so the check reads that model's
    price and falls back to the profile default only when nothing is pinned.
    """
    if profile is None or profile.kind != "cli":
        return None  # unknown or HTTP: not a zero-cost claim this check can make
    if profile.raw_prompt:
        return f"`{name}` is a raw shell profile"
    output = profile.cli.output if profile.cli is not None else None
    if output is None or not (output.input_tokens_path or output.output_tokens_path):
        return f"`{name}` declares no token paths in its output config"
    if not profile.models:
        return f"`{name}` declares no models (no pricing)"
    wanted = model_name or profile.default_model
    model = next((m for m in profile.models if m.name == wanted), None)
    if model is None:
        if model_name is not None:
            return f"`{name}` has no price for pinned model `{model_name}`"
        model = profile.models[0]
    if model.cost_per_1k_input <= 0 and model.cost_per_1k_output <= 0:
        return f"`{name}` prices `{model.name}` at $0"
    return None


class ZeroCostLoopLimitCheck:
    check_id = "V228"
    severity = ValidationSeverity.WARNING
    description = "A loop cost limit cannot stop a route that reports no cost"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        loops = config.sheet.loops or {}
        if not any(loop.cost_limit_usd is not None for loop in loops.values()):
            return []
        try:
            from marianne.instruments.loader import load_all_profiles

            profiles = load_all_profiles()
        except Exception:
            return []
        sheets = build_sheets(config)
        issues = []
        for span, loop in loops.items():
            if loop.cost_limit_usd is None:
                continue
            span_sheets = set(span_range(span))
            routes: dict[str, str | None] = {}
            for sheet in sheets:
                if sheet.num in span_sheets:
                    model = sheet.instrument_config.get("model")
                    routes.setdefault(sheet.instrument_name, str(model) if model else None)
            # A CLI route reports cost when its profile declares token paths
            # and a priced model: the musician prices those tokens and the
            # baton enforces the span cap on them (GH #453). Only a route that
            # cannot produce a cost makes the cap inert; the warning names
            # that route and why.
            reasons = [
                reason
                for name, model in routes.items()
                if (reason := _zero_cost_reason(profiles.get(name), name, model)) is not None
            ]
            if routes and len(reasons) == len(routes):
                issues.append(
                    _issue(
                        self.check_id,
                        self.severity,
                        f"loop {span}: cost_limit_usd cannot stop a route that reports no "
                        f"cost: {'; '.join(reasons)}",
                    )
                )
        return issues


class FlowConcertTargetCheck:
    check_id = "V229"
    severity = ValidationSeverity.ERROR
    description = "Static trigger concert targets exist and parse"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        issues = []
        for span, trigger in (config.sheet.triggers or {}).items():
            for action in (trigger.on_success or []) + (trigger.on_fail or []):
                if action.concert is None:
                    continue
                source = (
                    action.concert.score
                    if isinstance(action.concert, ConcertTrigger)
                    else action.concert
                )
                if "{" in source or "}" in source:
                    continue
                target = Path(source)
                if not target.is_absolute():
                    target = config_path.resolve().parent / target
                if not target.is_file():
                    issues.append(
                        _issue(
                            self.check_id,
                            self.severity,
                            f"trigger {span}: concert score not found: {target}",
                        )
                    )
                    continue
                try:
                    JobConfig.from_yaml(target)
                except (ValueError, OSError) as exc:
                    issues.append(
                        _issue(
                            self.check_id,
                            self.severity,
                            f"trigger {span}: concert score invalid: {target}: {exc}",
                        )
                    )
        return issues


class ForwardGotoFanOutCheck:
    check_id = "V231"
    severity = ValidationSeverity.WARNING
    description = "Forward goto from a fanned instance may skip sibling instances"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        stage_map = config.sheet.fan_out_stage_map or {}
        issues = []
        for span, trigger in (config.sheet.triggers or {}).items():
            for number in span_range(span):
                if stage_map.get(number, {}).get("fan_count", 1) <= 1:
                    continue
                actions = (trigger.on_success or []) + (trigger.on_fail or [])
                if any(action.goto is not None and action.goto > number for action in actions):
                    issues.append(
                        _issue(
                            self.check_id,
                            self.severity,
                            f"sheet {number}: forward goto may skip unfinished fan-out siblings",
                        )
                    )
        return issues


class GotoCycleCheck:
    check_id = "V315"
    severity = ValidationSeverity.WARNING
    description = "Unconditional goto edges form a possible cycle"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        edges: dict[int, set[int]] = {}
        for span, trigger in (config.sheet.triggers or {}).items():
            targets = {
                action.goto
                for action in (trigger.on_success or []) + (trigger.on_fail or [])
                if action.goto is not None
            }
            for number in span_range(span):
                edges.setdefault(number, set()).update(targets)
        seen: set[int] = set()
        active: set[int] = set()

        def walk(node: int, path: list[int]) -> list[int] | None:
            if node in active:
                return path[path.index(node) :] + [node]
            if node in seen:
                return None
            active.add(node)
            for target in sorted(edges.get(node, ())):
                cycle = walk(target, path + [target])
                if cycle:
                    return cycle
            active.remove(node)
            seen.add(node)
            return None

        for node in sorted(edges):
            cycle = walk(node, [node])
            if cycle:
                return [
                    _issue(
                        self.check_id,
                        self.severity,
                        f"goto cycle {' -> '.join(map(str, cycle))} may be unbounded",
                    )
                ]
        return []


_CONDITION = re.compile(
    r"\s*\w+\s*(?:>=|<=|==|!=|>|<)\s*-?\d+"
    r"(?:\s+and\s+\w+\s*(?:>=|<=|==|!=|>|<)\s*-?\d+)*\s*\Z"
)


class LegacyConditionCheck:
    check_id = "V232"
    severity = ValidationSeverity.WARNING
    description = "Legacy condition syntax that evaluates fail-open"

    def check(self, config: JobConfig, config_path: Path, raw_yaml: str) -> list[ValidationIssue]:
        return [
            _issue(
                self.check_id,
                self.severity,
                f"condition {rule.condition!r} uses unsupported syntax",
            )
            for rule in config.validations
            if rule.condition and not _CONDITION.fullmatch(rule.condition)
        ]
