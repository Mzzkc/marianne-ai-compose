"""Capability-class checks for the V-CLS frame (design §8).

Every check in this module reaches class state through the ONE loader
(``marianne.instruments.classes``): layer resolution, the shipped
vocabulary, and ``ClassesFile`` validation are never re-implemented here.
A name that is not an alias, not a registered profile, and not class-shaped
stays the existing V210/V211 typo domain (already class-aware through the
same loader); V310 owns the class-shaped refusals, so one bad name is
reported once, under the code that names the fix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marianne.core.config import JobConfig
from marianne.core.config.instruments import InstrumentProfile
from marianne.instruments.classes import ClassMap
from marianne.validation.base import ValidationIssue, ValidationSeverity
from marianne.validation.checks._helpers import find_line_in_yaml

_VOCABULARY_HINT = "run 'mzt instruments classes write' or define it in ~/.marianne/classes.yaml"


@dataclass(frozen=True)
class _ClassContext:
    """One loader view shared by the class checks (loaded per check call).

    Deliberately not cached process-wide: the dashboard and any long-lived
    caller must see layer edits without a restart (the #408 lesson).
    """

    profiles: dict[str, InstrumentProfile]
    class_map: ClassMap

    def resolvable_class_names(self) -> set[str]:
        return set(self.class_map.classes)

    def class_shaped_names(self) -> set[str]:
        """Names that mean a class on this machine: configured, vocabulary, tombstoned."""
        from marianne.instruments.classes import CLASS_VOCABULARY

        return set(self.class_map.classes) | set(CLASS_VOCABULARY) | set(self.class_map.tombstones)

    def used_classes(self, config: JobConfig) -> dict[str, str]:
        """Class name → first instrument position that resolves to it (§3.1 order)."""
        from marianne.instruments.classes import _instrument_positions

        used: dict[str, str] = {}
        for location, name in _instrument_positions(config):
            if name in config.instruments or name in self.profiles:
                continue  # alias > profile > class
            if name in self.class_map.classes:
                used.setdefault(name, location)
        return used

    def names_class_usage(self, config: JobConfig) -> bool:
        """True when any instrument position names a class-shaped name.

        Also true when a layer file failed AND some position name resolves to
        nothing: a class defined only in a refused layer stops looking
        class-shaped, exactly when its author most needs the refusal rendered.
        """
        from marianne.instruments.classes import _instrument_positions

        class_shaped = self.class_shaped_names()
        unresolved = False
        for _, name in _instrument_positions(config):
            if name in config.instruments or name in self.profiles:
                continue
            if name in class_shaped:
                return True
            unresolved = True
        return bool(self.class_map.failures) and unresolved


def _load_context() -> _ClassContext | None:
    """Load profiles + class map through the one loader; degrade silently."""
    try:
        from marianne.instruments.classes import load_class_map
        from marianne.instruments.loader import load_all_profiles

        profiles = load_all_profiles()
        class_map = load_class_map(profile_names=set(profiles))
    except Exception:
        return None
    if not profiles:
        return None
    return _ClassContext(profiles=profiles, class_map=class_map)


def _entry_unavailable_reason(profile_name: str, ctx: _ClassContext) -> str | None:
    """Why one chain entry cannot run here, or None when it can (V-CLS-03 arms 1-3).

    Availability comes from the one shared probe (``check_profile_available``):
    binary-not-on-PATH and ``execution_status: unsupported`` both live there
    since Forge's 12f913fc, so this never re-derives either.
    """
    profile = ctx.profiles.get(profile_name)
    if profile is None:
        return "profile not registered"
    from marianne.instruments.availability import check_profile_available

    available, reason = check_profile_available(profile)
    if not available:
        return reason or "unavailable"
    return None


def _response_format_unenforceable(
    profile_name: str,
    ctx: _ClassContext,
    request: Any,
) -> bool:
    """V-CLS-03 arm 4: the sheet's response_format and this entry's backend.

    Mirrors the dispatch refusal shape (``adapter.py`` "cannot enforce
    instrument_config.response_format"): HTTP backends and CLI backends carry
    ``set_response_format``, but a ``json_schema`` request on a CLI profile
    additionally requires the profile's ``json_schema_flag``.
    """
    if not isinstance(request, dict) or not request:
        return False
    profile = ctx.profiles.get(profile_name)
    if profile is None:
        return False  # arm 1 already reports unregistered entries
    if request.get("type") != "json_schema":
        return False
    if profile.kind != "cli":
        return False
    command = profile.cli.command if profile.cli is not None else None
    return command is None or getattr(command, "json_schema_flag", None) is None


def _layer_record(ctx: _ClassContext, layer: str) -> Path | None:
    for record in ctx.class_map.layers:
        if record.layer == layer:
            return record.path
    return None


def _entry_label(profile: str, model: str | None) -> str:
    return f"{profile}/{model}" if model else profile


class ClassNameResolutionCheck:
    """V310 (ERROR): a class-shaped name that cannot resolve on this machine (V-CLS-02).

    Vocabulary-only classes (shipped names never configured here), tombstoned
    classes, and configured classes whose every entry names a profile not
    registered here are submit refusals (§4.3); this reports them at validate
    time with the fix command. Entirely unknown names stay V210/V211 — the
    loader already folds configured classes into their valid set.
    """

    @property
    def check_id(self) -> str:
        return "V310"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Reports class names that cannot resolve on this machine"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        issues: list[ValidationIssue] = []
        reported: set[str] = set()

        from marianne.instruments.classes import _instrument_positions

        for location, name in _instrument_positions(config):
            if name in config.instruments or name in ctx.profiles:
                continue
            if name in ctx.class_map.classes or name in reported:
                continue
            if name in ctx.class_shaped_names():
                reported.add(name)
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=ValidationSeverity.ERROR,
                        message=(
                            f"{location}: class '{name}' has no instruments configured "
                            f"on this machine; {_VOCABULARY_HINT}"
                        ),
                        line=find_line_in_yaml(raw_yaml, name),
                        context=name,
                        suggestion=_VOCABULARY_HINT,
                        metadata={"class_name": name, "location": location},
                    )
                )

        for name, location in sorted(ctx.used_classes(config).items()):
            if name in reported:
                continue
            entry = ctx.class_map.classes[name]
            if any(step.profile in ctx.profiles for step in entry.chain):
                continue  # at least one registered entry — availability is V311's domain
            reported.add(name)
            entries = ", ".join(step.profile for step in entry.chain)
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=ValidationSeverity.ERROR,
                    message=(
                        f"{location}: class '{name}' has no instruments configured "
                        f"on this machine; {_VOCABULARY_HINT}"
                    ),
                    line=find_line_in_yaml(raw_yaml, name),
                    context=f"chain: {entries}",
                    suggestion=(
                        f"class '{name}' names profiles not registered here ({entries}); "
                        "fix the class map or register the profiles"
                    ),
                    metadata={"class_name": name, "location": location},
                ),
            )
        return issues


class ClassChainAvailabilityCheck:
    """V311 (WARNING): part of a used class's chain cannot run here (V-CLS-03, V-CLS-08).

    Per used class: k of n entries unresolvable or unavailable (binary not on
    PATH, execution_status unsupported, or unable to enforce a sheet's
    response_format) — the job still starts at the first available entry. When
    no entry can run here the row stays a WARNING that says so: availability
    is a fact about the machine, the conductor still admits the score (A-C5),
    and V310 is reserved for a chain with no REGISTERED entry (V-CLS-02).

    Also warns when a name the score uses resolves to a profile that shadows a
    packaged-default class of the same name (V-CLS-08 WARN arm): the profile
    wins (§3.1) and the class is unreachable.
    """

    @property
    def check_id(self) -> str:
        return "V311"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns when class chain entries cannot run on this machine"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        used = ctx.used_classes(config)
        issues: list[ValidationIssue] = []

        sheet_rf_requests = self._sheet_rf_requests(config, ctx, set(used))

        for name, location in sorted(used.items()):
            entry = ctx.class_map.classes[name]
            n = len(entry.chain)
            unavailable: list[str] = []
            unavailable_profiles: set[str] = set()
            for step in entry.chain:
                reason = _entry_unavailable_reason(step.profile, ctx)
                if reason is None and any(
                    _response_format_unenforceable(step.profile, ctx, request)
                    for request in sheet_rf_requests.get(name, [])
                ):
                    reason = "cannot enforce the sheet's response_format"
                if reason is not None:
                    unavailable.append(f"{step.profile} ({reason})")
                    unavailable_profiles.add(step.profile)
            if not unavailable:
                continue
            entries_text = "; ".join(unavailable)
            if len(unavailable) == n:
                # Availability is a fact about this machine, not the score
                # (V-CLS-03; A-C5): the conductor admits a chain whose entries
                # are all registered, and dispatch reports the missing binary.
                # So a chain nobody can run HERE is a WARNING that says so and
                # points at the install, never an ERROR that refuses a score
                # `mzt run` would accept. (The escalation that used to live
                # here failed `mzt init` → `mzt validate` on every fresh
                # install and every CI runner.)
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=ValidationSeverity.WARNING,
                        message=(
                            f"class '{name}': none of its {n} entries can run here "
                            f"({entries_text}); the job will be admitted and every "
                            "dispatch will fail until one of them is installed"
                        ),
                        line=find_line_in_yaml(raw_yaml, name),
                        context=name,
                        suggestion=(
                            "install one of the class's instruments (mzt doctor lists "
                            "what is on PATH), or extend the class in "
                            "~/.marianne/classes.yaml with an instrument you have"
                        ),
                        metadata={"class_name": name, "location": location},
                    )
                )
                continue
            first_available = next(
                _entry_label(step.profile, step.config.model)
                for step in entry.chain
                if step.profile not in unavailable_profiles
            )
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"class '{name}': {len(unavailable)} of {n} entries cannot run "
                        f"here ({entries_text}); the job will start at '{first_available}'"
                    ),
                    line=find_line_in_yaml(raw_yaml, name),
                    context=name,
                    suggestion=(
                        "the chain advances past an unavailable entry after one dispatch "
                        "attempt; install the missing instruments to use the full chain"
                    ),
                    metadata={"class_name": name, "location": location},
                ),
            )

        issues.extend(self._plain_binding_rf_issues(config, ctx, raw_yaml))
        issues.extend(self._shadowed_default_classes(config, ctx, raw_yaml))
        return issues

    def _plain_binding_rf_issues(
        self,
        config: JobConfig,
        ctx: _ClassContext,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """Arm 4 for a sheet bound to a plain profile, not a class.

        Dispatch refuses a ``json_schema`` response_format on a CLI profile
        with no ``cli.command.json_schema_flag`` (``cli_backend`` raises
        before spawning). A class-bound sheet is covered by the chain loop
        above; a sheet bound straight to a profile (``instrument:``, a
        movement or per-sheet binding, or a score alias) was not, so the
        score validated and then failed every dispatch. One WARNING per
        profile, naming the sheets.
        """
        try:
            from marianne.core.sheet import build_sheets

            sheets = build_sheets(
                config, classes=ctx.class_map.snapshot(set(ctx.used_classes(config))),
            )
        except Exception:  # noqa: BLE001 — unbuildable sheets are reported by other checks
            return []
        by_profile: dict[str, list[int]] = {}
        for sheet in sheets:
            if sheet.instrument_resolution is not None:
                continue  # class-bound: the chain loop owns it
            request = sheet.instrument_config.get("response_format")
            if _response_format_unenforceable(sheet.instrument_name, ctx, request):
                by_profile.setdefault(sheet.instrument_name, []).append(sheet.num)
        issues: list[ValidationIssue] = []
        for profile_name, nums in sorted(by_profile.items()):
            shown = ", ".join(str(n) for n in nums[:8]) + (", ..." if len(nums) > 8 else "")
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"instrument '{profile_name}' cannot enforce the json_schema "
                        f"response_format set for sheet(s) {shown}: the profile declares "
                        "no cli.command.json_schema_flag, so every dispatch of those "
                        "sheets is refused"
                    ),
                    line=find_line_in_yaml(raw_yaml, "response_format"),
                    context=profile_name,
                    suggestion=(
                        "bind those sheets to a profile that declares json_schema_flag "
                        "(the claude-code builtin does) or an HTTP profile, or to a class "
                        "whose chain contains one; or remove response_format"
                    ),
                    metadata={"instrument": profile_name, "sheets": ",".join(map(str, nums))},
                )
            )
        return issues

    def _sheet_rf_requests(
        self,
        config: JobConfig,
        ctx: _ClassContext,
        used: set[str],
    ) -> dict[str, list[Any]]:
        """response_format requests of sheets whose primary resolves to a used class."""
        if not used:
            return {}
        try:
            from marianne.core.sheet import build_sheets

            snapshot = ctx.class_map.snapshot(used)
            requests: dict[str, list[Any]] = {}
            for sheet in build_sheets(config, classes=snapshot):
                resolution = sheet.instrument_resolution
                if resolution is None:
                    continue
                request = sheet.instrument_config.get("response_format")
                if request is not None:
                    requests.setdefault(resolution.requested, []).append(request)
            return requests
        except Exception:
            return {}

    def _shadowed_default_classes(
        self,
        config: JobConfig,
        ctx: _ClassContext,
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        """V-CLS-08 WARN arm: a used name resolves to a profile that shadows a default class."""
        from marianne.instruments.classes import _instrument_positions

        issues: list[ValidationIssue] = []
        reported: set[str] = set()
        for location, name in _instrument_positions(config):
            if name in reported or name not in ctx.profiles:
                continue
            entry = ctx.class_map.classes.get(name)
            if entry is None or entry.source_layer != "default":
                continue
            reported.add(name)
            path = _layer_record(ctx, entry.source_layer)
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"class '{name}' in {path} has the same name as instrument "
                        f"profile '{name}'; the profile wins and the class is unreachable"
                    ),
                    line=find_line_in_yaml(raw_yaml, name),
                    context=name,
                    suggestion=(
                        "rename the class in your classes file or use the profile "
                        "deliberately; the §3.1 order (alias > profile > class) is fixed"
                    ),
                    metadata={"class_name": name, "location": location, "layer": "default"},
                ),
            )
        return issues


class ClassAliasProfileCheck:
    """V321 (ERROR): an instrument alias names a class as its profile (V-CLS-04).

    An alias must name a profile; a class there would nest a second chain
    inside one alias slot (§3.2). The conductor refuses the same condition at
    submit through ``resolve_job_classes``.
    """

    @property
    def check_id(self) -> str:
        return "V321"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Reports instrument aliases whose profile names a capability class"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None or not config.instruments:
            return []
        issues: list[ValidationIssue] = []
        for alias, definition in config.instruments.items():
            for entry in definition.entries:
                if entry.profile not in ctx.class_shaped_names():
                    continue
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=ValidationSeverity.ERROR,
                        message=(
                            f"instrument alias '{alias}' names class '{entry.profile}' "
                            "as a profile; an alias entry must name a profile "
                            "(use the class directly in instrument:)"
                        ),
                        line=find_line_in_yaml(raw_yaml, f"{alias}:"),
                        context=entry.profile,
                        suggestion=(
                            f"set instruments.{alias} to a profile name, or name "
                            f"the class '{entry.profile}' in instrument: directly"
                        ),
                        metadata={"alias": alias, "class_name": entry.profile},
                    ),
                )
        return issues


class AliasShadowsClassCheck:
    """V322 (WARNING): an instruments key equals a capability-class name (V-CLS-05).

    The alias wins under the §3.1 resolution order; the author should know
    their alias is masking the class of the same name.
    """

    @property
    def check_id(self) -> str:
        return "V322"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns when a score alias shadows a capability class of the same name"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None or not config.instruments:
            return []
        class_shaped = ctx.class_shaped_names()
        issues: list[ValidationIssue] = []
        for alias in config.instruments:
            if alias not in class_shaped:
                continue
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"alias '{alias}' has the same name as capability class "
                        f"'{alias}'; the alias wins in this score"
                    ),
                    line=find_line_in_yaml(raw_yaml, f"{alias}:"),
                    context=alias,
                    suggestion=(
                        "rename the alias, or rely on the class and delete the alias — "
                        "the §3.1 order (alias > profile > class) is fixed"
                    ),
                    metadata={"alias": alias},
                ),
            )
        return issues


class AliasSelectCheck:
    """V330 (WARNING): an alias `select` modifier that cannot act (#473).

    `cycle`/`spread` rotate across an alias's entries; with one entry there
    is nothing to rotate. `cheapest`/`widest` order by the profile's model
    table; an entry whose profile has no row for its model keeps its authored
    place, so an alias where NO entry has a known metric is unordered. In
    both cases the score runs, on the authored order, and this says so.
    """

    @property
    def check_id(self) -> str:
        return "V330"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns when an alias select modifier has nothing to choose between"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        if not config.instruments:
            return []
        from marianne.core.sheet import _entry_metric

        try:
            from marianne.instruments.loader import load_all_profiles

            profiles = dict(load_all_profiles())
        except Exception:  # noqa: BLE001 — without profiles only the arity arm can judge
            profiles = {}
        issues: list[ValidationIssue] = []
        for alias, definition in config.instruments.items():
            if not definition.select:
                continue
            entries = definition.entries
            problems: list[str] = []
            if len(entries) < 2:
                problems.append(
                    f"{', '.join(definition.select)} need at least two chain entries; "
                    f"this alias has {len(entries)}"
                )
            metrics = (("cheapest", "cost_per_1k_input"), ("widest", "context_window"))
            for metric, field in metrics:
                if metric in definition.select and profiles and not any(
                    _entry_metric(e, profiles, field) is not None for e in entries
                ):
                    problems.append(
                        f"`{metric}` found no {field} for any entry's model in the "
                        "registered profiles, so the authored order stands"
                    )
            for problem in problems:
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=ValidationSeverity.WARNING,
                        message=f"alias '{alias}' select: {problem}",
                        line=find_line_in_yaml(raw_yaml, f"{alias}:"),
                        context=alias,
                        suggestion=(
                            "add chain entries the modifier can choose between, give each "
                            "entry a model the profile's `models:` table lists, or drop the "
                            "modifier"
                        ),
                        metadata={"alias": alias, "select": ",".join(definition.select)},
                    ),
                )
        return issues


class SchemaSheetInteractiveCheck:
    """V331 (WARNING): a schema sheet would run interactively (GH #478).

    A sheet with ``instrument_config.response_format`` needs a headless
    backend (HTTP, or a CLI with ``json_schema_flag``); the interactive tmux
    backend cannot enforce a schema and dispatch refuses it (adapter
    "cannot enforce instrument_config.response_format"). Score silence on
    ``interactive`` resolves to the profile's ``cli.interactive.
    enabled_by_default`` — true for claude-code and antigravity — so a
    schema sheet that does not say ``interactive: false`` passes V311 (the
    profile CAN enforce, headless) and fails at sheet 1. This names the
    sheet before the run.
    """

    @property
    def check_id(self) -> str:
        return "V331"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns when a response_format sheet resolves to interactive execution"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        try:
            from marianne.core.sheet import build_sheets

            used = ctx.used_classes(config)
            snapshot = ctx.class_map.snapshot(set(used)) if used else None
            sheets = build_sheets(config, classes=snapshot)
        except Exception:  # noqa: BLE001 — an unbuildable score is another check's finding
            return []
        issues: list[ValidationIssue] = []
        seen: set[str] = set()
        for sheet in sheets:
            cfg = sheet.instrument_config
            if cfg.get("response_format") is None or cfg.get("interactive") is not None:
                continue
            profile = ctx.profiles.get(sheet.instrument_name)
            if (
                profile is None or profile.kind != "cli" or profile.cli is None
                or profile.cli.interactive is None
                or not profile.cli.interactive.enabled_by_default
            ):
                continue
            key = f"{sheet.instrument_name}:{sheet.movement}"
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"sheet {sheet.num} (movement {sheet.movement}) sets "
                        f"response_format on '{sheet.instrument_name}', whose "
                        "profile runs interactively by default; the interactive "
                        "backend cannot enforce a schema and dispatch refuses the "
                        "sheet"
                    ),
                    line=find_line_in_yaml(raw_yaml, "response_format:"),
                    context=sheet.instrument_name,
                    suggestion=(
                        "set `interactive: false` in that sheet's instrument_config "
                        "(score, movement, per-sheet or alias scope), or drop "
                        "response_format"
                    ),
                    metadata={
                        "sheet_num": str(sheet.num),
                        "instrument": sheet.instrument_name,
                    },
                ),
            )
        return issues


_CLI_SCHEMA_TOP_LEVEL_COMBINATORS = ("oneOf", "allOf", "anyOf")


def _cli_schema_refusal(schema: Any) -> str | None:
    """Why a CLI's tool ``input_schema`` would refuse this schema (GH #486).

    Claude Code forwards ``--json-schema`` to the API as a tool input_schema,
    which rejects a top-level ``oneOf``/``allOf``/``anyOf`` and a top level
    without ``type`` (400, after a two-second round trip). Nested combinators
    and a discriminated union wrapped under one required property are fine.
    """
    if not isinstance(schema, dict):
        return None
    present = [k for k in _CLI_SCHEMA_TOP_LEVEL_COMBINATORS if k in schema]
    if present:
        return f"top-level {'/'.join(present)} (wrap the branches under one required property)"
    if "type" not in schema:
        return "no top-level `type` (the API requires one; `object` for a tool schema)"
    return None


class CliSchemaShapeCheck:
    """V332 (WARNING): a CLI schema sheet carries a shape the API refuses (GH #486).

    V311 proves the profile CAN enforce a schema; V331 proves the sheet runs
    headless; this proves the schema's top level is one the CLI's tool
    input_schema accepts. All three must hold or the sheet fails at its
    first attempt with a 400 the job could not previously even quote.
    """

    @property
    def check_id(self) -> str:
        return "V332"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.WARNING

    @property
    def description(self) -> str:
        return "Warns when a CLI schema sheet's top level is one the API refuses"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        try:
            from marianne.core.sheet import build_sheets

            used = ctx.used_classes(config)
            snapshot = ctx.class_map.snapshot(set(used)) if used else None
            sheets = build_sheets(config, classes=snapshot)
        except Exception:  # noqa: BLE001 — an unbuildable score is another check's finding
            return []
        issues: list[ValidationIssue] = []
        seen: set[str] = set()
        for sheet in sheets:
            request = sheet.instrument_config.get("response_format")
            if not isinstance(request, dict) or request.get("type") != "json_schema":
                continue
            schema = (request.get("json_schema") or {}).get("schema")
            profile = ctx.profiles.get(sheet.instrument_name)
            if profile is None or profile.kind != "cli":
                continue
            reason = _cli_schema_refusal(schema)
            if reason is None:
                continue
            key = f"{sheet.instrument_name}:{sheet.movement}"
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=self.severity,
                    message=(
                        f"sheet {sheet.num} (movement {sheet.movement}) sends a "
                        f"response_format schema to '{sheet.instrument_name}' with "
                        f"{reason}; the API refuses it as a tool input_schema"
                    ),
                    line=find_line_in_yaml(raw_yaml, "response_format:"),
                    context=sheet.instrument_name,
                    suggestion=(
                        "give the schema a top-level `type: object` and move any "
                        "oneOf/allOf/anyOf under a property"
                    ),
                    metadata={
                        "sheet_num": str(sheet.num),
                        "instrument": sheet.instrument_name,
                    },
                ),
            )
        return issues


class ClassModelConfigCheck:
    """V323 (ERROR): a model set in instrument_config against a class primary (V-CLS-06).

    A model id cannot be honoured across heterogeneous profiles (§2.4); the
    conductor refuses the same condition at submit. Applies at score, movement
    and per-sheet scope, exactly as ``resolve_job_classes`` does.
    """

    @property
    def check_id(self) -> str:
        return "V323"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Reports instrument_config.model applied to a class-resolved primary"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        used = ctx.used_classes(config)
        if not used:
            return []
        try:
            from marianne.core.sheet import build_sheets

            snapshot = ctx.class_map.snapshot(set(used))
        except Exception:
            return []  # unresolvable names already carry V310 rows
        issues: list[ValidationIssue] = []
        reported: set[tuple[str, str]] = set()
        for sheet in build_sheets(config, classes=snapshot):
            resolution = sheet.instrument_resolution
            if resolution is None:
                continue
            movement = config.movements.get(sheet.movement)
            scopes: tuple[tuple[str, Mapping[str, Any]], ...] = (
                ("score", config.instrument_config),
                ("movement", movement.instrument_config if movement is not None else {}),
                ("sheet", config.sheet.per_sheet_instrument_config.get(sheet.num, {})),
            )
            for scope_name, scope in scopes:
                model = scope.get("model")
                if model is None or (str(model), resolution.requested) in reported:
                    continue
                reported.add((str(model), resolution.requested))
                issues.append(
                    ValidationIssue(
                        check_id=self.check_id,
                        severity=ValidationSeverity.ERROR,
                        message=(
                            f"sheet {sheet.num}: model '{model}' cannot apply to class "
                            f"'{resolution.requested}' (its entries are different "
                            "instruments); use a score alias for a fixed model, or set "
                            "the model in the class map"
                        ),
                        line=find_line_in_yaml(raw_yaml, "model:"),
                        context=f"{scope_name} instrument_config.model",
                        suggestion=(
                            "write a score alias (instruments: {name: {profile: …, "
                            "config: {model: …}}}) or set the model on the class entry"
                        ),
                        metadata={
                            "sheet": str(sheet.num),
                            "scope": scope_name,
                            "model": str(model),
                            "class_name": resolution.requested,
                        },
                    ),
                )
        return issues


class ClassesFileValidCheck:
    """V324 (ERROR): a classes layer file failed validation while the score uses a class (V-CLS-07).

    Renders ``ClassMap.failures`` exactly as the one loader recorded them
    (pydantic message included) — the way A4 renders ``FlowConfigError``,
    never re-validating the file. File-level, gated on class usage so a broken
    user file stays silent for scores that never name a class.
    """

    @property
    def check_id(self) -> str:
        return "V324"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.ERROR

    @property
    def description(self) -> str:
        return "Reports invalid classes layer files when the score uses a class"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,  # noqa: ARG002 (layer files are outside the score)
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        if not ctx.names_class_usage(config):
            return []
        return [
            ValidationIssue(
                check_id=self.check_id,
                severity=ValidationSeverity.ERROR,
                message=f"{failure.path}: {failure.reason}",
                context=f"layer: {failure.layer}",
                suggestion=(
                    "fix the layer file; the layer is refused and lower layers stand — "
                    "classes it defines are unavailable to this score"
                ),
                metadata={"layer": failure.layer, "path": str(failure.path)},
            )
            for failure in ctx.class_map.failures
        ]


class ClassChainSummaryCheck:
    """V325 (INFO): what each used class resolved to and from which layer (V-CLS-11).

    One row per used class; the ``--json`` summary block carries the per-sheet
    resolution and every layer path + sha256 the validator read (§2.1).
    """

    @property
    def check_id(self) -> str:
        return "V325"

    @property
    def severity(self) -> ValidationSeverity:
        return ValidationSeverity.INFO

    @property
    def description(self) -> str:
        return "Summarises class → instrument-chain resolution for used classes"

    def check(
        self,
        config: JobConfig,
        config_path: Path,  # noqa: ARG002 (protocol shape)
        raw_yaml: str,  # noqa: ARG002 (summary reads layers, not the score text)
    ) -> list[ValidationIssue]:
        ctx = _load_context()
        if ctx is None:
            return []
        used = ctx.used_classes(config)
        if not used:
            return []
        issues: list[ValidationIssue] = []
        for name in sorted(used):
            entry = ctx.class_map.classes[name]
            chain = ", ".join(
                _entry_label(step.profile, step.config.model) for step in entry.chain
            )
            layer = next(
                (r for r in ctx.class_map.layers if r.layer == entry.source_layer), None
            )
            digest = (layer.sha256 or "")[:16] if layer is not None else ""
            issues.append(
                ValidationIssue(
                    check_id=self.check_id,
                    severity=ValidationSeverity.INFO,
                    message=(
                        f"Instruments: {name} → {chain} "
                        f"({entry.source_layer} layer, sha256 {digest})"
                    ),
                    context=name,
                    metadata={
                        "class_name": name,
                        "chain": ",".join(
                            _entry_label(step.profile, step.config.model)
                            for step in entry.chain
                        ),
                        "layer": entry.source_layer,
                        "layer_path": str(layer.path) if layer is not None else "",
                        "layer_sha256": (layer.sha256 or "") if layer is not None else "",
                    },
                )
            )
        return issues
