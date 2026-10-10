"""Property checks for the serialized class-map and job-snapshot contracts."""

from datetime import UTC, datetime
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from marianne.core.config.classes import (
    ClassEntry,
    ClassEntryConfig,
    ClassesFile,
    ClassLayerRecord,
    ClassSnapshot,
    ClassSnapshotEntry,
    GeneratedBy,
    InstrumentResolution,
)


@given(
    name=st.sampled_from(["strong", "fast", "local", "workhorse"]),
    profile=st.sampled_from(["claude-code", "codex-cli", "ollama"]),
    model=st.one_of(st.none(), st.text(alphabet="abc123-", min_size=1, max_size=12)),
    tombstone=st.booleans(),
)
@settings(max_examples=40)
def test_classesfile_entry_and_generatedby_round_trip_and_refuse_repeats(
    name: str, profile: str, model: str | None, tombstone: bool,
) -> None:
    entry = ClassEntry(profile=profile, config=ClassEntryConfig(model=model))
    generated = GeneratedBy(
        tool="mzt-instruments-classes-write",
        at=datetime(2026, 10, 10, tzinfo=UTC),
        probe_sha256="a" * 64,
        body_sha256="b" * 64,
    )
    layer = ClassesFile(version=1, generated=generated, classes={
        name: None if tombstone else [entry],
    })
    restored = ClassesFile.model_validate_json(layer.model_dump_json())
    assert restored == layer
    if tombstone:
        assert restored.classes[name] is None
    else:
        assert restored.classes[name] == [entry]
        with pytest.raises(ValueError, match="same step twice"):
            ClassesFile(version=1, classes={name: [entry, entry]})


@given(
    name=st.sampled_from(["strong", "fast", "local"]),
    profile=st.sampled_from(["claude-code", "codex-cli", "ollama"]),
    source_layer=st.sampled_from(["default", "user", "venue"]),
    phase=st.sampled_from(["submit", "resume"]),
)
@settings(max_examples=35)
def test_classsnapshot_layer_entry_and_resolution_preserve_frozen_chain(
    name: str, profile: str, source_layer: str, phase: str,
) -> None:
    layer = ClassLayerRecord(layer=source_layer, path=Path("/tmp/classes.yaml"), sha256="c" * 64)
    entry = ClassSnapshotEntry(
        chain=[ClassEntry(profile=profile)], source_layer=source_layer, resolved_at=phase,
    )
    snapshot = ClassSnapshot.from_classes({name: entry}, [layer])
    restored = ClassSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot
    assert restored.classes[name].chain[0].profile == profile
    other_profile = "cli" if profile != "cli" else "codex-cli"
    changed = ClassSnapshot.from_classes(
        {name: entry.model_copy(update={"chain": [ClassEntry(profile=other_profile)]})},
        [layer],
    )
    assert changed.digest != snapshot.digest
    resolution = InstrumentResolution(
        requested=name, chain=[profile], snapshot_digest=snapshot.digest,
    )
    assert InstrumentResolution.model_validate_json(resolution.model_dump_json()) == resolution
