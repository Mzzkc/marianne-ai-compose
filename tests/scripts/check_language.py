#!/usr/bin/env python3
"""Check that Marianne's score language agrees with itself across four layers.

The four layers are one type system (GH #436):

* schema      -- ``JobConfig`` key names, placement and self-descriptions;
* validator   -- the V-codes ``mzt validate`` can emit and their documentation;
* docs        -- ``docs/configuration-reference.md`` and ``docs/cli-reference.md``;
* skills      -- what an LLM has loaded while it writes a score
                 (score-authoring and composing skills plus their ``docs/ref``).

Every finding names one author-visible key path (``retry.max_retries``), never a
bare word, because one word means different things at different levels
(``on_success`` at the root is a hook list; under ``sheet.triggers`` it is a
trigger). A finding has a stable id ``CHECK:subject`` so a baseline fixture can
hold today's known gaps and fail only on regression, the way
``tests/fixtures/validation_corpus_baseline.json`` holds the validator corpus.

Checks (``L`` codes, so they never collide with validator ``V`` codes):

L01 undescribed      schema key with no description on its own field (a
                      parent model's docstring is not a field description)
L02 divergent        root key whose description shares no word stem with its name
L03 misplaced-sched  key described as scheduled/recurring that does not live
                      under ``schedule``
L04 phantom-key      validator suggestion/typo table names a key path that does
                      not exist
L05 orphan           schema key no author surface mentions (score-authoring,
                      composing, configuration-reference.md)
L06 stale            a score-shaped YAML example in a skill, ``docs/ref`` or
                      ``docs/*.md`` whose keys the loader's own tolerant walk
                      drops (the example teaches a key that does nothing)
L07 vcode-undocumented  a V-code ``mzt validate`` can emit that the
                      ``docs/cli-reference.md`` validate table does not list
L08 unread           a score field no runtime code reads (advisory; heuristic)
L09 silent-switch    a model gated by ``enabled: false`` whose other fields a
                      score can set without turning it on, and the validator
                      says nothing (advisory)
L10 dead-end-hint    a validator suggestion points at a key that does nothing
                      on its own (it sits in an ``enabled: false`` section with
                      no auto-enable): following the fix yields a silent no-op
L11 orphan-check     a V-code check class defined in ``src/marianne/
                      validation`` that the runner never registers and no
                      doc row marks retired (GH #439)

L12 glossary-unresolved  a glossary row whose code name is not a schema key
                      path, ``mzt`` command, or status value, and not a
                      ``(concept)`` row with an existing doc anchor; a broken
                      link target is the same defect (PL-10)

Exit 0 when no finding is outside the baseline, 1 on regression, 2 on usage or
environment error. ``--json`` prints machine-readable output.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import subprocess
import sys
import typing
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import UnionType
from typing import Any

import yaml
from pydantic import BaseModel

CHECKS = {
    "L01": "undescribed",
    "L02": "divergent",
    "L03": "misplaced-sched",
    "L04": "phantom-key",
    "L05": "orphan",
    "L06": "stale",
    "L07": "vcode-undocumented",
    "L08": "unread",
    "L09": "silent-switch",
    "L10": "dead-end-hint",
    "L11": "orphan-check",
    "L12": "glossary-unresolved",
}
GATING = {"L01", "L02", "L03", "L04", "L05", "L06", "L07", "L10", "L11", "L12"}

# Author surfaces, relative to the repository root. The plugin tree is the
# ``plugins`` submodule.
SKILL_FILES = (
    "plugins/marianne/skills/score-authoring/SKILL.md",
    "plugins/marianne/skills/composing/SKILL.md",
)
SKILL_REFS = (
    "plugins/marianne/docs/ref/essentials.md",
    "plugins/marianne/docs/ref/patterns.md",
    "plugins/marianne/docs/ref/advanced.md",
    "plugins/marianne/docs/ref/modern-agents.md",
)
CONFIG_REFERENCE = "docs/configuration-reference.md"
CLI_REFERENCE = "docs/cli-reference.md"
GLOSSARY = "docs/glossary.md"

# Words that carry no meaning for L02: a description that only says "the" or
# "configuration" shares nothing with its name.
_STOP = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "with",
    "when",
    "whether",
    "configuration",
    "config",
    "settings",
    "optional",
    "set",
    "use",
    "used",
}
_SCHEDULED = re.compile(r"\b(schedul\w*|recurr\w*|cron\b|tick\b)", re.IGNORECASE)
_VCODE = re.compile(r"\bV[0-9]{3}\b")
_VRANGE = re.compile(r"\bV([0-9]{3})\s*[–-]\s*V([0-9]{3})\b")


@dataclass(frozen=True)
class KeyInfo:
    """One author-visible key path in the score schema."""

    path: str
    key: str
    model: str
    field: str
    description: str
    model_description: str
    annotation: str
    source: str


@dataclass
class Finding:
    check: str
    subject: str
    detail: str
    gating: bool = True
    where: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.check}:{self.subject}"


# --------------------------------------------------------------------------
# schema inventory
# --------------------------------------------------------------------------


def _models_in(annotation: Any) -> list[type[BaseModel]]:
    out: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        out.append(annotation)
    for arg in typing.get_args(annotation):
        out.extend(_models_in(arg))
    return out


def _container_suffix(annotation: Any) -> str:
    """How a nested model is reached: ``[]`` for lists, ``.<k>`` for dict values."""
    origin = typing.get_origin(annotation)
    if origin in (UnionType, typing.Union):
        for arg in typing.get_args(annotation):
            suffix = _container_suffix(arg)
            if suffix:
                return suffix
        return ""
    if origin in (list, tuple):
        return "[]"
    if origin is dict:
        return ".<k>"
    return ""


def _source(model: type[BaseModel], repo: Path) -> str:
    try:
        file = Path(inspect.getsourcefile(model) or "")
        line = inspect.getsourcelines(model)[1]
        return f"{file.resolve().relative_to(repo.resolve())}:{line}"
    except (OSError, TypeError, ValueError):
        return "?"


def schema_inventory(root: type[BaseModel], repo: Path) -> list[KeyInfo]:
    """Every author-visible key path, depth first, each model expanded once.

    A model reachable from two places (``TriggerAction`` under ``on_success``
    and ``on_fail``) is expanded at its first path only; its keys keep one
    identity, as an author's mental model does.
    """
    keys: list[KeyInfo] = []
    expanded: set[type[BaseModel]] = set()

    def walk(model: type[BaseModel], prefix: str) -> None:
        if model in expanded:
            return
        expanded.add(model)
        for name, info in model.model_fields.items():
            key = info.alias if isinstance(info.alias, str) else name
            path = f"{prefix}.{key}" if prefix else key
            keys.append(
                KeyInfo(
                    path=path,
                    key=key,
                    model=model.__name__,
                    field=name,
                    description=(info.description or "").strip(),
                    model_description="",
                    annotation=repr(info.annotation).replace("typing.", ""),
                    source=_source(model, repo),
                )
            )
            for child in _models_in(info.annotation):
                if child in expanded:
                    continue
                child_doc = (child.__doc__ or "").strip()
                # Record the reached model's docstring on the key that reaches it.
                keys[-1] = KeyInfo(**{**asdict(keys[-1]), "model_description": child_doc})
                walk(child, path + _container_suffix(info.annotation))

    walk(root, "")
    return keys


# --------------------------------------------------------------------------
# surfaces
# --------------------------------------------------------------------------


class SurfaceMissing(RuntimeError):
    """An author surface the gate must read is not on disk.

    The skill files live in the ``plugins`` submodule. A bare checkout (fresh
    worktree, CI without submodules) has none of them; scoring a missing
    surface as "" would turn every skill-taught key into an L05 orphan and
    report the wrong gaps with a straight face. Fail loud instead.
    """


def _read(repo: Path, rel: str) -> str:
    path = repo / rel
    if not path.exists():
        raise SurfaceMissing(
            f"{rel} is missing under {repo}; if it lives in the plugins submodule, "
            "run `git submodule update --init plugins` (the gate cannot judge the "
            "language without the skill layer)"
        )
    return path.read_text(encoding="utf-8")


def _mentions(text: str, key: KeyInfo, homonyms: frozenset[str] = frozenset()) -> bool:
    """Whether ``text`` names this key path, not just the bare word.

    A dotted path (``stale_detection.idle_timeout_seconds``), a YAML line
    (``  idle_timeout_seconds:``) or a backticked name all count. A bare root
    key counts only as backticked text or a YAML line, so ``workspace`` in prose
    does not teach the ``workspace:`` key.

    ``homonyms`` lists leaf names that mean something else at another level
    (``source_path`` is a root loader field and a ``field_match`` rule field).
    A root key with a homonym counts only as a YAML line at column 0: the bare
    word, backticked or not, is what every field table uses for the nested
    meaning, so it cannot teach the root key. A nested homonym keeps the usual
    credit, because field tables are where nested keys are taught.
    """
    leaf = re.escape(key.key)
    dotted = re.escape(key.path.replace(".<k>", "").replace("[]", ""))
    is_root = "." not in key.path and "[]" not in key.path
    if key.key in homonyms and is_root:
        return re.search(rf"(?m)^{leaf}\s*:", text) is not None
    patterns = [
        rf"`{dotted}`",
        rf"\b{dotted}\b" if "." in key.path else None,
        rf"(?m)^\s*-?\s*{leaf}\s*:",
        rf"`{leaf}`",
        rf"`{leaf}:",
    ]
    return any(re.search(p, text) for p in patterns if p)


def homonym_leaves(keys: list[KeyInfo]) -> frozenset[str]:
    """Leaf names that appear at more than one level with different models.

    ``on_success`` (root hook list vs. sheet trigger), ``concert`` (root
    ConcertConfig vs. trigger action), ``source_path`` (root loader field vs.
    ``field_match`` reference) are one word with several meanings. A reader
    cannot tell them apart from the bare word, so neither can the orphan check.
    """
    owners: dict[str, set[str]] = {}
    for key in keys:
        owners.setdefault(key.key, set()).add(key.model)
    return frozenset(name for name, models in owners.items() if len(models) > 1)


# A block marked as a deliberately wrong, removed, or proposed example teaches
# nothing about the current language. Kept explicit: widening it is a reviewed
# edit, never a regex tweak to make a finding disappear.
_NOT_TEACHING = re.compile(
    r"\b(proposed|does not exist|now rejected|removed|legacy|wrong|not yet exposed|"
    r"sidecar|agent-card|instrument profile|classes\.yaml|composition-design)\b",
    re.IGNORECASE,
)
# A block that says it belongs in the conductor's config file is judged against
# that file's schema (DaemonConfig), not skipped: a wrong conductor example is
# as much a language defect as a wrong score example.
_CONDUCTOR_FILE = re.compile(r"\b(conductor\.yaml|conductor config|daemon config)\b", re.IGNORECASE)


def _block_context(text: str, start: int, block: str) -> str:
    first = block.splitlines()[0] if block.strip() else ""
    lead_in = "\n".join(text[:start].rstrip().splitlines()[-2:])
    return (first if first.lstrip().startswith("#") else "") + "\n" + lead_in


def _conductor_unknown_keys(data: dict[Any, Any]) -> list[str]:
    """Dotted paths a ``conductor.yaml`` example sets that DaemonConfig forbids."""
    from pydantic import ValidationError

    from marianne.daemon.config import DaemonConfig

    try:
        DaemonConfig.model_validate(data)
    except ValidationError as exc:
        return [
            ".".join(str(part) for part in error["loc"])
            for error in exc.errors()
            if error["type"] == "extra_forbidden"
        ]
    return []


def stale_example_keys(repo: Path, root: type[BaseModel]) -> list[tuple[str, int, str]]:
    """Keys in doc/skill YAML examples that the file they target would drop.

    Each block is judged in the language it claims: a block introduced as
    ``conductor.yaml`` runs through ``DaemonConfig``; a score-shaped block (at
    least 60% of its top-level keys are score root keys) runs through the score
    loader's own tolerant walk. Returns ``(file, block_index, key_path)``; a
    conductor path is prefixed ``conductor:`` so it cannot collide with a score
    path of the same spelling (``learning`` means a different model in each).
    """
    from marianne.core.config.schema_walk import strip_unknown_score_fields

    roots = {(f.alias if isinstance(f.alias, str) else n) for n, f in root.model_fields.items()}
    rels = list(SKILL_FILES + SKILL_REFS) + sorted(
        str(p.relative_to(repo)) for p in (repo / "docs").glob("*.md")
    )
    out: list[tuple[str, int, str]] = []
    for rel in rels:
        text = _read(repo, rel)
        for index, match in enumerate(re.finditer(r"```ya?ml\n(.*?)```", text, flags=re.DOTALL)):
            block = match.group(1)
            context = _block_context(text, match.start(), block)
            if _NOT_TEACHING.search(context):
                continue
            try:
                data = yaml.safe_load(block)
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict) or not data:
                continue
            if _CONDUCTOR_FILE.search(context):
                for location in _conductor_unknown_keys(data):
                    out.append((rel, index, "conductor:" + _normalize_path(location)))
                continue
            top = {str(k) for k in data}
            if len(top & roots) < 0.6 * len(top):
                continue
            _, unknown = strip_unknown_score_fields(data, root)
            for item in unknown:
                location = f"{item.path}.{item.key}" if item.path else item.key
                out.append((rel, index, _normalize_path(location)))
    return out


def _normalize_path(location: str) -> str:
    """``movements.2.description`` -> ``movements.<k>.description``."""
    return re.sub(r"\.(\d+)(?=\.|$)", ".<k>", re.sub(r"\[\d+\]", "[]", location))


# --------------------------------------------------------------------------
# validator
# --------------------------------------------------------------------------


def emittable_vcodes() -> set[str]:
    """Codes ``mzt validate`` can emit: the suppression-known registry."""
    from marianne.validation.output_contract import _known_suppression_severities

    return set(_known_suppression_severities())


def documented_vcodes(text: str) -> set[str]:
    codes = set(_VCODE.findall(text))
    for start, end in _VRANGE.findall(text):
        for number in range(int(start), int(end) + 1):
            codes.add(f"V{number:03d}")
    return codes


def validate_section(text: str) -> str:
    start = text.find("### `mzt validate`")
    if start < 0:
        start = text.find("mzt validate [OPTIONS]")
    end = text.find("\n### ", start + 10) if start >= 0 else -1
    return text[start:end] if start >= 0 else ""


def validator_key_references(repo: Path, roots: set[str]) -> list[tuple[str, str]]:
    """Key paths the validator names to authors, with where it names them.

    Two sources: the curated typo tables (``_KNOWN_TYPOS`` and the validate
    command's hint table), and every string literal in ``src/marianne/
    validation`` or the validate command that contains a dotted path starting
    with a score root key (``instrument_config.model``). Literal text is what
    reaches the author, so a path named only in prose still counts.
    """
    import ast

    refs: list[tuple[str, str]] = []
    from marianne.validation.checks import schema as schema_check

    for typo, target in schema_check._KNOWN_TYPOS.items():  # noqa: SLF001
        refs.append((f"validation/checks/schema.py:_KNOWN_TYPOS[{typo}]", target))
    sources = sorted((repo / "src" / "marianne" / "validation").rglob("*.py"))
    sources.append(repo / "src" / "marianne" / "cli" / "commands" / "validate.py")
    dotted = re.compile(r"(?<![\w./])([a-z_]+(?:\.[a-z_]+)+)(?![\w(])")
    for path in sources:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(repo / "src" / "marianne").as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for candidate in dotted.findall(node.value):
                    if candidate.split(".")[0] in roots:
                        refs.append((f"{rel}:{node.lineno}", candidate))
    return refs


def _untyped_dict_paths(keys: list[KeyInfo]) -> set[str]:
    """Key paths whose value is a free-form dict (no model behind it)."""
    return {k.path for k in keys if k.annotation.startswith("dict[str, Any]")}


def _suggestion_path(suggestion: str) -> str:
    """The primary key path a suggestion names.

    A suggestion may carry guidance after the key (``parallel.stagger_delay_ms
    (requires parallel.enabled: true)``); the path the author writes is the
    first dotted path in the text.
    """
    match = re.search(r"[a-z_]+(?:\.[a-z_]+)+", suggestion)
    return match.group(0) if match else suggestion


def _names_the_switch(suggestion: str, section: str) -> bool:
    """Whether a suggestion itself names the section's ``enabled`` switch.

    A hint that says ``requires parallel.enabled: true`` cannot strand the
    author: following it turns the section on. That is D7's contract for
    hints that land inside an opt-in section.
    """
    return f"{section}.enabled" in suggestion


def orphan_check_definitions(repo: Path) -> list[tuple[str, str]]:
    """V-codes a check class defines but the registry never registers.

    A class with a ``check_id`` property returning a literal V-code, or a
    ``ValidationIssue(check_id="Vxxx")`` literal, defines an emittable code.
    When the suppression-known registry does not know it, the check is dead:
    written, exported, never run (GH #439). Deleting a retired check with a
    doc note, or registering it, clears the finding.
    """
    import ast

    known = emittable_vcodes()
    out: list[tuple[str, str]] = []
    base = repo / "src" / "marianne" / "validation"
    for path in sorted(base.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(repo).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if not isinstance(item, ast.FunctionDef) or item.name != "check_id":
                        continue
                    for ret in ast.walk(item):
                        value = getattr(ret, "value", None)
                        if (
                            isinstance(value, ast.Constant)
                            and isinstance(value.value, str)
                            and re.fullmatch(r"V[0-9]{3}", value.value)
                            and value.value not in known
                        ):
                            out.append((f"{rel}:{node.lineno}", value.value))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id != "ValidationIssue":
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "check_id":
                        continue
                    value = keyword.value
                    if (
                        isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        and re.fullmatch(r"V[0-9]{3}", value.value)
                        and value.value not in known
                    ):
                        out.append((f"{rel}:{node.lineno}", value.value))
    return sorted(set(out))


# --------------------------------------------------------------------------
# runtime (advisory)
# --------------------------------------------------------------------------


# Fields whose only runtime reader is a model method the name-scan cannot
# see through. Each entry names its verified reader; removing an entry
# requires re-verifying by grep.
_VERIFIED_READERS = {
    "isolation.worktree_base": "IsolationConfig.get_worktree_base, called at daemon/manager.py:399",
}

# These score sections have no receiver. The name-only L08 heuristic misses
# common leaves such as ``enabled`` and ``level``; keep their contract explicit
# until a receiver-aware census replaces it. A future runtime reader must
# remove its row and prove that reader in the same change.
_UNREAD_ROOT_SECTIONS = frozenset(
    {
        "ai_review", "bridge", "checkpoints", "circuit_breaker",
        "conductor", "feedback", "grounding", "logging", "rate_limit",
    }
)


def unread_fields(repo: Path, keys: list[KeyInfo]) -> dict[str, int]:
    """Read counts for every model field outside ``core/config``.

    Heuristic: an attribute read ``.<field>`` or a string key ``"<field>"`` in
    ``src/marianne`` excluding the config package itself. Zero means no code
    reads the value a score sets. Counts are per field name, so a common name
    (``enabled``) is never reported unread; the check is conservative.
    """
    src = repo / "src" / "marianne"
    texts = [
        p.read_text(encoding="utf-8")
        for p in src.rglob("*.py")
        if "core/config" not in p.as_posix()
    ]
    blob = "\n".join(texts)
    counts: dict[str, int] = {}
    for name in sorted({k.field for k in keys}):
        pattern = re.compile(rf"\.{re.escape(name)}\b|[\"']{re.escape(name)}[\"']")
        counts[name] = len(pattern.findall(blob))
    return counts


# --------------------------------------------------------------------------
# glossary (L12, PL-10)
# --------------------------------------------------------------------------

# Each definition is a bullet or table row beginning with **term** (`code`)
# or **term** (concept). Malformed rows remain visible to the gate.
_GLOSSARY_ROW = re.compile(
    r"^\*\*(?P<term>.+?)\*\*\s+\((?:`(?P<code>[^`]+)`|(?P<concept>concept))\)"
    r"(?:\s*:)?(?:\s|$)"
)
_LINK = re.compile(r"\[[^]]*\]\((?P<target>[^)\s]+)\)")


def mzt_commands() -> set[str]:
    """Every command the Typer app registers, including subcommand paths."""
    import click
    import typer

    from marianne.cli import app

    names: set[str] = set()

    def walk(group: click.Group, prefix: str) -> None:
        for name, sub in group.commands.items():
            path = f"{prefix} {name}"
            names.add(path)
            if isinstance(sub, click.Group):
                walk(sub, path)

    command = typer.main.get_command(app)
    if isinstance(command, click.Group):
        walk(command, "mzt")
    return names


def status_values() -> set[str]:
    """Every run- and sheet-status value the runtime can report."""
    from marianne.core.checkpoint import JobStatus, SheetStatus

    return {status.value for status in (*JobStatus, *SheetStatus)}


def _doc_anchors(repo: Path) -> dict[str, set[str]]:
    """Markdown heading ids, including duplicate suffixes and explicit anchors."""
    anchors: dict[str, set[str]] = {}
    for path in sorted((repo / "docs").rglob("*.md")):
        ids: set[str] = set()
        counts: Counter[str] = Counter()
        fence: str | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                if fence is None:
                    fence = marker[1][0]
                elif marker[1][0] == fence:
                    fence = None
                continue
            if fence is not None:
                continue
            ids.update(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)["\']', line))
            heading = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
            if heading:
                slug = re.sub(r"[^\w\s-]", "", heading[1].lower())
                slug = re.sub(r"\s", "-", slug.strip())
                count = counts[slug]
                counts[slug] += 1
                ids.add(f"{slug}-{count}" if count else slug)
        anchors[path.relative_to(repo).as_posix()] = ids
    return anchors


_LIST_MARKER = re.compile(r"(?:[-*+]|\d+[.)])\s+")


def glossary_rows(text: str) -> list[tuple[int, str, str, bool, list[str]]]:
    """Parse every definition, retaining malformed rows so they cannot pass silently."""
    rows: list[tuple[int, str, str, bool, list[str]]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if line.startswith("|"):
            cells = line.strip("|").split("|")
            first = cells[0].strip()
            if first in {"Term", "Timer", "Term (code name)"} or all(
                re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in cells
            ):
                continue
        else:
            # Any ordinary Markdown list marker introduces a definition a
            # reader can see; the gate must see it too (Guide Inspect P1:
            # a `* ` row with a bad code name produced no L12 finding).
            marker = _LIST_MARKER.match(line)
            if marker is None:
                continue
            first = line[marker.end():]
        match = _GLOSSARY_ROW.match(first)
        targets = [m["target"] for m in _LINK.finditer(line)]
        if match:
            rows.append(
                (number, match["term"], match["code"] or "", match["concept"] is not None, targets)
            )
        else:
            rows.append((number, first, "", False, targets))
    return rows


def _glossary_schema_paths(keys: list[KeyInfo], root: type[BaseModel]) -> set[str]:
    """Inventory paths plus repeated model locations and field-scoped aliases.

    The reporting inventory expands each model once. A glossary must also
    resolve that model under other parents (failure hooks, trigger actions,
    per-sheet instrument settings), preserving its container path and aliases.
    """
    paths = {key.path for key in keys}

    def walk(model: type[BaseModel], prefix: str, ancestors: set[type[BaseModel]]) -> None:
        if model in ancestors:
            return
        for name, info in model.model_fields.items():
            spellings = {info.alias if isinstance(info.alias, str) else name}
            choices = getattr(info.validation_alias, "choices", ())
            spellings.update(choice for choice in choices if isinstance(choice, str))
            for spelling in spellings:
                path = f"{prefix}.{spelling}" if prefix else spelling
                paths.add(path)
                for child in _models_in(info.annotation):
                    walk(child, path + _container_suffix(info.annotation), ancestors | {model})

    walk(root, "", set())
    return paths


def glossary_findings(repo: Path, keys: list[KeyInfo], root: type[BaseModel]) -> list[Finding]:
    """L12: each row resolves to a key, registered command, status or linked concept.

    Definitions require a configuration/CLI reference anchor. Concept rows may
    use any existing docs anchor. Links may be repo-relative (docs/...) or
    document-relative (../docs/... or configuration-reference.md).
    """
    page = repo / GLOSSARY
    rows = glossary_rows(page.read_text(encoding="utf-8")) if page.exists() else []
    if not rows:
        return [
            Finding(
                "L12",
                "glossary",
                "glossary is missing or contains no definitions",
                where=[GLOSSARY],
            )
        ]
    known_paths = _glossary_schema_paths(keys, root)
    commands = mzt_commands()
    statuses = status_values()
    anchors = _doc_anchors(repo)
    findings: list[Finding] = []
    for number, term, code, is_concept, targets in rows:
        reasons: list[str] = []
        if not is_concept and code not in known_paths | commands | statuses:
            reasons.append(f"`{code or term}` names no schema key, mzt command, or status value")
        if not targets:
            reasons.append("row has no documentation anchor")
        for target in targets:
            file_part, separator, anchor = target.partition("#")
            if file_part.startswith("docs/"):
                path = repo / file_part
            else:
                path = repo / "docs" / file_part
            try:
                relative = path.resolve().relative_to(repo.resolve()).as_posix()
            except ValueError:
                relative = ""
            allowed = is_concept or relative in {CONFIG_REFERENCE, CLI_REFERENCE}
            if (
                not separator
                or not anchor
                or not allowed
                or anchor not in anchors.get(relative, set())
            ):
                reasons.append(f"unresolved documentation anchor {target}")
        if reasons:
            findings.append(
                Finding(
                    "L12",
                    code or term,
                    f"glossary.md:{number} ({term}): " + "; ".join(reasons),
                    where=[f"{GLOSSARY}:{number}"],
                )
            )
    return findings


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w[:5] for w in words if w not in _STOP and len(w) > 2}


def run_checks(
    repo: Path, root: type[BaseModel]
) -> tuple[list[KeyInfo], list[Finding], dict[str, Any]]:
    from marianne.validation.checks.reserved import SWITCH_WARNING_PATHS

    # Synthetic control schemas have no V329 registration: they must still
    # demonstrate that L09 detects a silent switch. Only the shipped score
    # model may claim the validator's audited warning paths.
    warned_switches = (
        SWITCH_WARNING_PATHS
        if root.__module__ == "marianne.core.config.job"
        else frozenset()
    )
    keys = schema_inventory(root, repo)
    findings: list[Finding] = []
    by_path = {k.path: k for k in keys}
    root_keys = [k for k in keys if "." not in k.path and "[]" not in k.path]

    skills = "\n".join(_read(repo, rel) for rel in SKILL_FILES + SKILL_REFS)
    config_ref = _read(repo, CONFIG_REFERENCE)
    cli_ref = _read(repo, CLI_REFERENCE)

    # L01 undescribed: a field description is required on the key's own field;
    # a parent model's docstring describes the model, not the key (D5).
    for key in keys:
        if not key.description:
            findings.append(
                Finding(
                    "L01", key.path, "no description on the field", where=[key.source]
                )
            )

    # L02 divergent (root keys only: nested descriptions inherit their parent's context)
    for key in root_keys:
        text = key.description or key.model_description.split("\n")[0]
        if text and not (_stems(key.key.replace("_", " ")) & _stems(text)):
            findings.append(
                Finding(
                    "L02",
                    key.path,
                    f"name shares no word stem with {text[:70]!r}",
                    where=[key.source],
                )
            )

    # L03 misplaced scheduled semantics
    for key in keys:
        match = _SCHEDULED.search(key.description or "")
        if (
            match
            and not key.path.startswith("schedule")
            and not key.description.startswith("Deprecated:")
        ):
            findings.append(
                Finding(
                    "L03",
                    key.path,
                    f"described as {match.group(0)!r} outside `schedule`",
                    where=[key.source],
                )
            )

    # L04 validator names a key that does not exist. A path under a free-form
    # dict (``instrument_config.model``) names a key the schema cannot see at
    # all: that is the #438 defect (untyped instrument_config), reported here
    # because the validator relies on a key the language never declares.
    leaf_paths = {k.path.replace(".<k>", "").replace("[]", "") for k in keys}
    untyped = _untyped_dict_paths(keys)
    references = validator_key_references(repo, {k.key for k in root_keys})
    for origin, target in references:
        primary = _suggestion_path(target)
        if primary in leaf_paths:
            continue
        parent = primary.rsplit(".", 1)[0]
        why = (
            "inside an untyped dict the schema cannot describe"
            if parent in untyped
            else "no such key"
        )
        findings.append(Finding("L04", primary, f"named by {origin}; {why}", where=[origin]))

    # L10 a validator hint that lands on a key inert without `enabled: true`
    switched_off = {
        k.path.rsplit(".", 1)[0]
        for k in keys
        if k.key == "enabled" and _defaults_off_without_auto(root, k.model)
    }
    # Only the typo tables redirect an author who has NOT yet opted in; a
    # message emitted from inside an enabled section (V304 names
    # ``code_execution.require_sandbox`` only when ``code_execution.enabled``)
    # cannot strand anyone, so prose references are not judged here.
    for origin, target in references:
        if "_KNOWN_TYPOS" not in origin:
            continue
        primary = _suggestion_path(target)
        section = primary.rsplit(".", 1)[0] if "." in primary else ""
        if (
            section in switched_off
            and not primary.endswith(".enabled")
            and not _names_the_switch(target, section)
        ):
            findings.append(
                Finding(
                    "L10",
                    primary,
                    f"{origin} suggests it, but `{section}` defaults off; "
                    "following the hint is a silent no-op",
                    where=[origin],
                )
            )

    # L05 orphan: no author surface names the key path. A leaf spelled the same
    # at two levels must be pinned to its level to count (see _mentions).
    # A reserved or deprecated key is deliberately untaught (P1/D4): its
    # description carries the contract, so the orphan check spares it.
    reserved_mark = ("Reserved:", "Reserved.", "Loader-owned", "Deprecated:")
    surfaces = skills + "\n" + config_ref
    homonyms = homonym_leaves(keys)
    for key in keys:
        if key.description.startswith(reserved_mark):
            continue
        if not _mentions(surfaces, key, homonyms):
            findings.append(
                Finding("L05", key.path, "no skill, ref, or configuration-reference.md mention")
            )

    # L06 stale: a score-shaped example whose keys the loader drops. The walk
    # is the loader's own (schema_walk.strip_unknown_score_fields), so the
    # checker cannot disagree with what a score author would actually get.
    for rel, block_index, location in stale_example_keys(repo, root):
        findings.append(
            Finding(
                "L06",
                location,
                f"taught in {rel} YAML block {block_index}; the loader drops it",
                where=[rel],
            )
        )

    # L07 V-codes the validate table does not document
    documented = documented_vcodes(validate_section(cli_ref))
    for code in sorted(emittable_vcodes() - documented):
        findings.append(
            Finding(
                "L07",
                code,
                "emittable by mzt validate; absent from the cli-reference validate table",
            )
        )

    # L08's name-only read heuristic remains advisory. It counts unrelated
    # ``enabled`` and ``level`` reads as score readers; it cannot be a gate.
    # The traced root sections below are gated until a receiver-aware census
    # replaces this explicit list.
    counts = unread_fields(repo, keys)
    for section in sorted(_UNREAD_ROOT_SECTIONS):
        root_key = by_path.get(section)
        if root_key is not None and not root_key.description.startswith(reserved_mark):
            findings.append(Finding(
                "L08", section,
                "unread score section is not marked Reserved or Deprecated",
                where=[root_key.source],
            ))
    for key in keys:
        if counts.get(key.field, 1) == 0:
            if key.path in _VERIFIED_READERS:
                continue
            marked = key.description.startswith(reserved_mark)
            findings.append(
                Finding(
                    "L08",
                    key.path,
                    "no read outside core/config"
                    + ("" if marked else " and not marked reserved"),
                    gating=False,
                    where=[key.source],
                )
            )

    # L09 silent switches (advisory): models gated by enabled=False with other fields
    for key in keys:
        if key.key != "enabled":
            continue
        parent = key.path.rsplit(".", 1)[0]
        parent_key = by_path.get(parent)
        if parent in warned_switches or (
            parent_key is not None
            and parent_key.description.startswith("Reserved:")
        ):
            continue  # V329 or V327 already gives the author a precise warning
        model = _model_by_name(root, key.model)
        if model is None:
            continue
        default = model.model_fields["enabled"].default
        auto = any("auto_enable" in name for name in dir(model))
        siblings = [
            k
            for k in keys
            if k.path.startswith(parent + ".")
            and k.path.count(".") == key.path.count(".")
            and k.key != "enabled"
        ]
        if default is False and siblings and not auto:
            findings.append(
                Finding(
                    "L09",
                    parent,
                    f"defaults off; any of {len(siblings)} sibling keys set without "
                    "`enabled: true` is inert and silent",
                    gating=False,
                )
            )

    # L11 orphan check class: defined, exported, never registered (GH #439)
    for origin, code in orphan_check_definitions(repo):
        findings.append(
            Finding(
                "L11",
                code,
                f"check class at {origin} is never registered by the runner "
                "and no doc row marks it retired; register it or delete it "
                "with a retirement note",
                where=[origin],
            )
        )

    # L12 ships without baseline debt: missing/malformed rows fail as well.
    findings.extend(glossary_findings(repo, keys, root))

    # dedupe by id, keep first
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.id not in seen:
            seen.add(finding.id)
            unique.append(finding)
    meta = {
        "schema_keys": len(keys),
        "root_keys": len(root_keys),
        "emittable_vcodes": len(emittable_vcodes()),
        "documented_vcodes": len(documented),
        "known_paths": len(by_path),
    }
    return keys, unique, meta


def _defaults_off_without_auto(root: type[BaseModel], model_name: str) -> bool:
    model = _model_by_name(root, model_name)
    if model is None or "enabled" not in model.model_fields:
        return False
    auto = any("auto_enable" in name for name in dir(model))
    return model.model_fields["enabled"].default is False and not auto


def _model_by_name(root: type[BaseModel], name: str) -> type[BaseModel] | None:
    stack = [root]
    seen: set[type[BaseModel]] = set()
    while stack:
        model = stack.pop()
        if model in seen:
            continue
        seen.add(model)
        if model.__name__ == name:
            return model
        for info in model.model_fields.values():
            stack.extend(_models_in(info.annotation))
    return None


# --------------------------------------------------------------------------
# baseline + output
# --------------------------------------------------------------------------


def load_baseline(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("findings", []))


def write_baseline(path: Path, findings: list[Finding], commit: str) -> None:
    payload = {
        "description": "Known language gaps (GH #436). A finding not listed here fails the gate. "
        "Remove an entry in the same commit that closes the gap.",
        "commit": commit,
        "findings": sorted(f.id for f in findings if f.gating),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _commit(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Marianne repository root")
    parser.add_argument(
        "--baseline", type=Path, help="baseline fixture; findings listed there do not fail"
    )
    parser.add_argument(
        "--write-baseline", type=Path, help="write today's gating findings as a baseline"
    )
    parser.add_argument("--inventory", type=Path, help="write the schema key inventory (JSON)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--check", action="append", choices=sorted(CHECKS), help="run only these checks"
    )
    parser.add_argument(
        "--allow-foreign-import",
        action="store_true",
        help="accept a marianne import from outside --repo/src (normally exit 2)",
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    try:
        import marianne
        from marianne.core.config.job import JobConfig
    except ImportError as exc:
        print(f"check_language: cannot import marianne: {exc}", file=sys.stderr)
        return 2
    imported = Path(marianne.__file__).resolve()
    if not args.allow_foreign_import and not imported.is_relative_to(repo / "src"):
        print(
            f"check_language: imported marianne from {imported}, which is not under "
            f"{repo / 'src'}; the schema and validator registry would belong to a "
            "different tree than the docs and skills (pass --allow-foreign-import "
            "to override)",
            file=sys.stderr,
        )
        return 2
    try:
        keys, findings, meta = run_checks(repo, JobConfig)
    except SurfaceMissing as exc:
        print(f"check_language: {exc}", file=sys.stderr)
        return 2
    if args.check:
        findings = [f for f in findings if f.check in args.check]
    commit = _commit(repo)
    meta.update({"commit": commit, "marianne_file": marianne.__file__})

    if args.inventory:
        args.inventory.write_text(
            json.dumps([asdict(k) for k in keys], indent=1) + "\n", encoding="utf-8"
        )
    if args.write_baseline:
        write_baseline(args.write_baseline, findings, commit)

    baseline = load_baseline(args.baseline)
    regressions = [f for f in findings if f.gating and f.id not in baseline]
    closed = sorted(baseline - {f.id for f in findings})
    counts = Counter(f.check for f in findings)

    if args.json:
        print(
            json.dumps(
                {
                    "meta": meta,
                    "counts": {f"{c} {CHECKS[c]}": counts.get(c, 0) for c in CHECKS},
                    "regressions": [asdict(f) | {"id": f.id} for f in regressions],
                    "closed_since_baseline": closed,
                    "findings": [asdict(f) | {"id": f.id} for f in findings],
                },
                indent=1,
            )
        )
    else:
        print(
            f"Score language check at {commit[:12]} "
            f"({meta['schema_keys']} keys, {meta['emittable_vcodes']} V-codes)"
        )
        for code in CHECKS:
            tier = "gate" if code in GATING else "advisory"
            print(f"  {code} {CHECKS[code]:<20} {counts.get(code, 0):4}  ({tier})")
        for finding in findings:
            mark = "NEW " if finding in regressions else "    "
            print(f"{mark}{finding.id}  {finding.detail}")
        if closed:
            print(f"Closed since baseline ({len(closed)}): remove them from the fixture:")
            for item in closed:
                print(f"  {item}")
        print(f"Regressions: {len(regressions)}")
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
