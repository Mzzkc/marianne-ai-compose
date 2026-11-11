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


# ---------------------------------------------------------------- hardening (review finding)

import os  # noqa: E402
import socket as _socket  # noqa: E402

from marianne.memory.gateway.paths import prepare_socket_dir  # noqa: E402


def test_symlinked_s_is_refused_and_target_untouched(short_tmp: Path) -> None:
    target = short_tmp / "elsewhere"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    (short_tmp / "s").symlink_to(target)
    with pytest.raises(SchemaViolation) as info:
        socket_path_for(short_tmp, "job", "d1", "att")
    assert info.value.subtype == "socket_dir_unsafe"
    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_regular_file_at_s_is_refused(short_tmp: Path) -> None:
    (short_tmp / "s").write_text("x")
    with pytest.raises(SchemaViolation) as info:
        socket_path_for(short_tmp, "job", "d1", "att")
    assert info.value.subtype == "socket_dir_unsafe"
    assert (short_tmp / "s").read_text() == "x"


def test_group_writable_run_dir_is_refused(short_tmp: Path) -> None:
    short_tmp.chmod(0o770)
    with pytest.raises(SchemaViolation) as info:
        socket_path_for(short_tmp, "job", "d1", "att")
    assert info.value.subtype == "run_dir_unsafe"
    assert not (short_tmp / "s").exists()


def test_missing_or_symlinked_run_dir_is_refused(short_tmp: Path) -> None:
    with pytest.raises(SchemaViolation) as info:
        socket_path_for(short_tmp / "nope", "job", "d1", "att")
    assert info.value.subtype == "run_dir_unsafe"
    real = short_tmp / "real"
    real.mkdir(mode=0o700)
    (short_tmp / "link").symlink_to(real)
    with pytest.raises(SchemaViolation) as info2:
        socket_path_for(short_tmp / "link", "job", "d1", "att")
    assert info2.value.subtype == "run_dir_unsafe"


def _bind_stale(path: Path) -> None:
    s = _socket.socket(_socket.AF_UNIX)
    s.bind(os.fspath(path))
    s.close()  # leaves the socket file behind


def test_stale_socket_is_replaced_and_foreign_entry_is_not(short_tmp: Path) -> None:
    with prepare_socket_dir(short_tmp, "job", "d1", "att") as d:
        _bind_stale(d.socket_path)
        assert d.clear_stale_socket() is True
        assert not d.socket_path.exists()
        d.socket_path.write_text("not a socket")
        with pytest.raises(SchemaViolation) as info:
            d.clear_stale_socket()
        assert info.value.subtype == "socket_name_occupied"
        assert d.socket_path.read_text() == "not a socket"


def test_bind_through_the_descriptor_gives_an_owned_0600_socket(short_tmp: Path) -> None:
    with prepare_socket_dir(short_tmp, "job", "d1", "att") as d:
        sock = d.bind_socket()
        try:
            st = os.lstat(d.socket_path)
            assert stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
            assert st.st_uid == os.getuid()
        finally:
            sock.close()
            d.unlink_socket()
        assert not d.socket_path.exists()


def test_directory_swapped_for_a_symlink_after_open_is_caught(short_tmp: Path) -> None:
    with prepare_socket_dir(short_tmp, "job", "d1", "att") as d:
        other = short_tmp / "other"
        other.mkdir(mode=0o700)
        (short_tmp / "s").rename(short_tmp / "s.moved")
        (short_tmp / "s").symlink_to(other)
        with pytest.raises(SchemaViolation) as info:
            d.bind_socket()
        assert info.value.subtype == "socket_dir_swapped"
        assert list(other.iterdir()) == []  # nothing was bound in the other directory
        assert not (short_tmp / "s.moved" / d.name).exists()  # and the socket was unlinked
