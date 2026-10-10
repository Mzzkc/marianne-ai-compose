"""Load the three capability-class layers without changing existing profile loading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from marianne.core.config.classes import (
    ClassesFile,
    ClassLayerRecord,
    ClassSnapshot,
    ClassSnapshotEntry,
    LayerName,
)


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
