"""Slice 2 (UDS wave): the per-dispatch socket path helper."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from marianne.memory import SchemaViolation
from marianne.memory.gateway.paths import MAX_SOCKET_PATH_BYTES, socket_path_for


def test_path_shape_and_directory_mode(short_tmp: Path) -> None:
    p = socket_path_for(short_tmp, "job", "d1", "att")
    assert p.parent == short_tmp / "s"
    assert p.name.startswith("s_") and p.name.endswith(".sock") and len(p.name) == 2 + 16 + 5
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert not p.exists()


def test_hash_depends_on_all_three_ids(short_tmp: Path) -> None:
    base = socket_path_for(short_tmp, "job", "d1", "att")
    assert base == socket_path_for(short_tmp, "job", "d1", "att")
    for other in (("job2", "d1", "att"), ("job", "d2", "att"), ("job", "d1", "att2")):
        assert socket_path_for(short_tmp, *other) != base


def test_loose_existing_directory_is_tightened(short_tmp: Path) -> None:
    (short_tmp / "s").mkdir(mode=0o755)
    (short_tmp / "s").chmod(0o755)
    socket_path_for(short_tmp, None, "d1", "att")
    assert stat.S_IMODE((short_tmp / "s").stat().st_mode) == 0o700


def test_too_long_is_a_typed_refusal_naming_the_length(short_tmp: Path) -> None:
    deep = short_tmp / ("x" * 60)
    with pytest.raises(SchemaViolation) as info:
        socket_path_for(deep, "job", "d1", "att")
    assert info.value.subtype == "socket_path_too_long"
    assert str(len(str(deep / "s" / ("s_" + "0" * 16 + ".sock")))) in str(info.value)
    assert not deep.exists()  # refused before anything was created


def test_limit_constant_and_empty_ids(short_tmp: Path) -> None:
    assert MAX_SOCKET_PATH_BYTES == 70
    with pytest.raises(SchemaViolation):
        socket_path_for(short_tmp, "job", "", "att")
