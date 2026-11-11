"""The pure resolver: score `memory:` config + sheet phase -> frozen bindings."""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path

import pytest

from marianne.core.config.memory import MemoryConfig
from marianne.memory import ALL_PERMISSIONS, ScopeRefused
from marianne.memory.gateway.paths import MAX_SOCKET_PATH_BYTES
from marianne.memory.resolve import ResolvedBinding, SheetPhase, resolve_memory

RUN = Path("/tmp/mr")


def cfg(**kw: object) -> MemoryConfig:
    return MemoryConfig.model_validate(kw)


def resolve(c: MemoryConfig | None, phase: SheetPhase | None = None) -> list[ResolvedBinding]:
    return resolve_memory(c, phase or SheetPhase("work", 2), user="ada", run_dir=RUN)


def test_absent_memory_is_empty() -> None:
    assert resolve(None) == []


def test_solo_default_when_memory_present_without_backends() -> None:
    (b,) = resolve(cfg())
    assert (b.name, b.system, b.scope) == ("default", "files", "local:default")
    assert b.permissions == tuple(p.value for p in ALL_PERMISSIONS)
    assert b.principal == "person:ada"
    assert b.access == "inherit" and b.recall_cap_bytes == 32768


def test_binding_is_frozen() -> None:
    (b,) = resolve(cfg())
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.name = "x"  # type: ignore[misc]


def test_attachment_id_is_uuid5_of_name_and_canonical_scope() -> None:
    a = resolve(cfg(backends={"notes": {"system": "files", "scope": "Project/Demo"}}))[0]
    b = resolve(cfg(backends={"notes": {"system": "files", "scope": "project:demo"}}))[0]
    c = resolve(cfg(backends={"other": {"system": "files", "scope": "project/demo"}}))[0]
    assert a.scope == "project/demo"
    assert a.attachment_id == b.attachment_id != c.attachment_id
    assert uuid.UUID(a.attachment_id).version == 5
    assert a.attachment_id == resolve(
        cfg(backends={"notes": {"system": "files", "scope": "project/demo"}})
    )[0].attachment_id


def test_missing_scope_defaults_to_local_default() -> None:
    (b,) = resolve(cfg(backends={"mem": {"system": "files"}}))
    assert b.scope == "local:default" and b.permissions


def test_bad_scope_raises_typed() -> None:
    with pytest.raises(ScopeRefused):
        resolve(cfg(backends={"m": {"system": "files", "scope": "nonsense"}}))


def test_phase_filtering() -> None:
    c = cfg(backends={
        "everywhere": {"system": "files"},
        "second": {"system": "files", "phases": [2]},
        "named": {"system": "files", "phases": ["recon"]},
        "never": {"system": "files", "phases": [7, "plan"]},
    })
    names = lambda p: sorted(b.name for b in resolve(c, p))  # noqa: E731
    assert names(SheetPhase("work", 2)) == ["everywhere", "second"]
    assert names(SheetPhase("recon", 1)) == ["everywhere", "named"]
    assert names(SheetPhase("recon")) == ["everywhere", "named"]
    assert names(SheetPhase("plan", 3)) == ["everywhere", "never"]


def test_caps_clamp_to_the_score_default() -> None:
    c = cfg(
        defaults={"recall_cap_bytes": 4096, "provenance": "optional"},
        backends={
            "big": {"system": "files", "caps": {"recall_cap_bytes": 99999}},
            "small": {"system": "files", "caps": {"recall_cap_bytes": 2048}},
            "unset": {"system": "files"},
            "strict": {"system": "files", "caps": {"provenance": "required"}},
        },
    )
    by = {b.name: b for b in resolve(c)}
    assert by["big"].recall_cap_bytes == 4096
    assert by["small"].recall_cap_bytes == 2048
    assert by["unset"].recall_cap_bytes == 4096 and by["unset"].provenance == "optional"
    assert by["strict"].provenance == "required"
    assert by["big"].lock_timeout_seconds == 10.0


def test_grant_access_carries_no_permissions() -> None:
    (b,) = resolve(cfg(backends={"m": {"system": "files", "access": "grant:team-a@2"}}))
    assert b.access == "grant:team-a@2" and b.permissions == ()


def test_socket_path_uses_paths_and_does_no_io() -> None:
    run = Path("/tmp/mr-does-not-exist")
    (b,) = resolve_memory(cfg(), SheetPhase("work"), user="ada", run_dir=run)
    p = b.socket_path("job-1", "d-1")
    assert p.parent == run / "s" and p.suffix == ".sock"
    assert len(str(p).encode()) <= MAX_SOCKET_PATH_BYTES
    assert b.socket_path("job-1", "d-1") == p != b.socket_path("job-1", "d-2")
    assert not run.exists()


def test_order_follows_declaration() -> None:
    c = cfg(backends={"b": {"system": "files"}, "a": {"system": "files"}})
    assert [x.name for x in resolve(c)] == ["b", "a"]
