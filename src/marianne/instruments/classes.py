"""Load the three capability-class layers without changing existing profile loading."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from marianne.core.config.classes import (
    ClassesFile,
    ClassLayerRecord,
    ClassSnapshot,
    ClassSnapshotEntry,
    LayerName,
    canonical_json,
)
from marianne.core.config.job import JobConfig

CLASS_VOCABULARY = frozenset({
    "strong", "workhorse", "fast", "cheap", "writing", "review", "vision",
    "local", "image", "video",
})


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_classes_file(source: str) -> ClassesFile:
    return ClassesFile.model_validate(yaml.load(source, Loader=_UniqueKeyLoader))


def class_source_paths(
    *,
    user_path: Path | None = None,
    venue_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    """One path owner for CLI, conductor, validator, and reload watcher."""
    default = Path(__file__).parent / "classes" / "default.yaml"
    user = user_path if user_path is not None else Path.home() / ".marianne" / "classes.yaml"
    venue = venue_path if venue_path is not None else Path(".marianne") / "classes.yaml"
    return default.resolve(), user.resolve(), venue.resolve()


@dataclass(frozen=True)
class ClassLoadFailure:
    layer: LayerName
    path: Path
    reason: str


@dataclass(frozen=True)
class ClassMap:
    classes: dict[str, ClassSnapshotEntry]
    layers: list[ClassLayerRecord]
    failures: list[ClassLoadFailure]
    tombstones: frozenset[str]

    def snapshot(self, names: set[str]) -> ClassSnapshot:
        return ClassSnapshot.from_classes(
            {name: self.classes[name].model_copy(deep=True) for name in names},
            list(self.layers),
        )


def load_class_map(
    *,
    user_path: Path | None = None,
    venue_path: Path | None = None,
    profile_names: set[str] | None = None,
) -> ClassMap:
    """Replace whole chains per layer; preserve lower entries on a broken layer."""
    names: tuple[LayerName, ...] = ("default", "user", "venue")
    paths = class_source_paths(user_path=user_path, venue_path=venue_path)
    merged: dict[str, ClassSnapshotEntry] = {}
    layers: list[ClassLayerRecord] = []
    failures: list[ClassLoadFailure] = []
    tombstones: set[str] = set()
    for layer, path in zip(names, paths, strict=True):
        if not path.exists():
            layers.append(ClassLayerRecord(layer=layer, path=path))
            continue
        try:
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            data = parse_classes_file(content.decode("utf-8"))
            if profile_names is not None and layer != "default":
                collisions = set(data.classes) & profile_names
                if collisions:
                    raise ValueError(
                        "class name collides with a registered profile: "
                        + ", ".join(sorted(collisions))
                    )
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            layers.append(ClassLayerRecord(layer=layer, path=path))
            failures.append(ClassLoadFailure(layer=layer, path=path, reason=str(exc)))
            continue
        layers.append(ClassLayerRecord(layer=layer, path=path, sha256=digest))
        for name, chain in data.classes.items():
            if chain is None:
                merged.pop(name, None)
                tombstones.add(name)
            else:
                merged[name] = ClassSnapshotEntry(chain=chain, source_layer=layer)
                tombstones.discard(name)
    return ClassMap(merged, layers, failures, frozenset(tombstones))


def _instrument_positions(config: JobConfig) -> list[tuple[str, str]]:
    """All score locations where a profile, alias, or class may be named."""
    positions = [("instrument", config.effective_instrument_name)]
    positions.extend(
        (f"instrument_fallbacks[{index}]", name)
        for index, name in enumerate(config.instrument_fallbacks)
    )
    positions.extend(
        (f"movements.{number}.instrument", movement.instrument)
        for number, movement in config.movements.items() if movement.instrument is not None
    )
    for number, movement in config.movements.items():
        positions.extend(
            (f"movements.{number}.instrument_fallbacks[{index}]", name)
            for index, name in enumerate(movement.instrument_fallbacks)
        )
    positions.extend(
        (f"sheet.per_sheet_instruments.{number}", name)
        for number, name in config.sheet.per_sheet_instruments.items()
    )
    positions.extend(
        (f"sheet.instrument_map.{name}", name)
        for name in config.sheet.instrument_map
    )
    for number, fallbacks in config.sheet.per_sheet_fallbacks.items():
        positions.extend(
            (f"sheet.per_sheet_fallbacks.{number}[{index}]", name)
            for index, name in enumerate(fallbacks)
        )
    return positions


def resolve_job_classes(
    config: JobConfig,
    profile_names: set[str],
    class_map: ClassMap,
    *,
    previous: ClassSnapshot | None = None,
    phase: str = "submit",
    expected_route: bool = False,
) -> ClassSnapshot | None:
    """Resolve only this job's class names, preserving its prior chains on resume.

    The daemon owns registry membership and supplies it here. This function has
    no backend availability probe: a registered but unreachable entry remains
    in the frozen chain for the baton's existing fallback path.
    """
    for alias, definition in config.instruments.items():
        if definition.profile in class_map.classes or definition.profile in CLASS_VOCABULARY:
            raise ValueError(
                f"instrument alias '{alias}' names class '{definition.profile}' as its profile; "
                "an alias must name a profile",
            )
        if definition.profile not in profile_names:
            raise ValueError(f"instruments.{alias}.profile: unknown profile '{definition.profile}'")

    requested: set[str] = set()
    for location, name in _instrument_positions(config):
        if name in config.instruments or name in profile_names:
            continue
        if (previous is not None and name in previous.classes) or name in class_map.classes:
            requested.add(name)
        elif name in CLASS_VOCABULARY or name in class_map.tombstones:
            raise ValueError(
                f"{location}: class '{name}' has no instruments configured on this machine; "
                "run 'mzt instruments classes write' or define it in ~/.marianne/classes.yaml",
            )
        else:
            raise ValueError(
                f"{location}: '{name}' is not an instrument profile, score alias, "
                "or capability class",
            )

    if expected_route and requested:
        raise ValueError("classes cannot be used with a reviewed route (expected_route)")

    if not requested and previous is None:
        return None
    selected = dict(previous.classes) if previous is not None else {}
    for name in requested - selected.keys():
        entry = class_map.classes[name]
        for step in entry.chain:
            if step.profile not in profile_names:
                raise ValueError(
                    f"class '{name}' names unregistered instrument profile '{step.profile}'",
                )
        selected[name] = entry.model_copy(update={"resolved_at": phase}, deep=True)
    layers = list(previous.layers) if previous is not None else list(class_map.layers)
    snapshot = ClassSnapshot.from_classes(selected, layers)

    # A score-level model would silently override the class's choice for only
    # one entry. Reject it at the same admission seam that freezes the chain.
    from marianne.core.sheet import build_sheets

    for sheet in build_sheets(config, classes=snapshot):
        resolution = sheet.instrument_resolution
        if resolution is None:
            continue
        movement = config.movements.get(sheet.movement)
        for scope in (
            config.instrument_config,
            movement.instrument_config if movement is not None else {},
            config.sheet.per_sheet_instrument_config.get(sheet.num, {}),
        ):
            if "model" in scope:
                raise ValueError(
                    f"sheet {sheet.num}: model '{scope['model']}' cannot apply to "
                    f"class '{resolution.requested}' (its entries are different instruments)",
                )
    return snapshot


def write_user_classes(
    *,
    path: Path | None = None,
    force: bool = False,
    if_absent: bool = False,
    dry_run: bool = False,
) -> tuple[Path, str, bool]:
    """Generate a policy map and replace the user layer with provenance custody.

    Existing handwritten or modified maps require ``force``. A real replacement
    is backed up first and written through fsync + atomic rename.
    """
    from marianne.instruments.class_policy import (
        canonical_probe,
        probe_builtin_profiles,
        select_class_chains,
    )
    from marianne.instruments.loader import InstrumentProfileLoader, profile_source_dirs

    destination = path or class_source_paths()[1]
    if if_absent and destination.exists():
        return destination, destination.read_text(encoding="utf-8"), False
    builtins_dir = profile_source_dirs()[0]
    profiles = InstrumentProfileLoader.load_directories([builtins_dir])
    probe = probe_builtin_profiles(profiles)
    classes = select_class_chains(probe)
    body_digest = hashlib.sha256(canonical_json(classes)).hexdigest()
    probe_digest = hashlib.sha256(canonical_probe(probe)).hexdigest()
    data = {
        "version": 1,
        "generated": {
            "tool": "mzt-instruments-classes-write",
            "at": datetime.now(UTC).isoformat(),
            "probe_sha256": probe_digest,
            "body_sha256": body_digest,
        },
        "classes": classes,
    }
    content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    parse_classes_file(content)
    if destination.exists() and not force:
        old = parse_classes_file(destination.read_text(encoding="utf-8"))
        old_raw = yaml.load(destination.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
        actual_digest = hashlib.sha256(canonical_json(old_raw["classes"])).hexdigest()
        if old.generated is None or old.generated.body_sha256 != actual_digest:
            raise ValueError(
                f"{destination} has hand edits; use --force to replace it after reviewing the diff"
            )
    if dry_run:
        return destination, content, False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        backup_dir = destination.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(destination, backup_dir / f"classes-{timestamp}.yaml")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".classes-", suffix=".yaml", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination, content, True
