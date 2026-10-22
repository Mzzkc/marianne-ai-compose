"""``InjectionItem.max_bytes`` is enforced at prompt assembly (GH #474).

A cadenza ledger that grows every tick used to ship as an ever-larger prompt
until the conductor's token warning fired — after assembly, naming no item.
Both resolvers (the musician's and the prompt renderer's) take the same
helper: a required item over its cap fails the sheet before execution with
the measured size; an optional one is dropped with a warning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config.job import InjectionItem
from marianne.daemon.baton.prompt import injection_over_max_bytes


def _dir(tmp_path: Path, *sizes: int) -> tuple[Path, list[Path]]:
    active = tmp_path / "active"
    active.mkdir()
    files = []
    for n, size in enumerate(sizes):
        f = active / f"{n}.md"
        f.write_text("x" * size)
        files.append(f)
    return active, files


def test_no_cap_is_never_over() -> None:
    item = InjectionItem(directory="/x", **{"as": "context"})
    assert injection_over_max_bytes(item, [], Path("/x")) is False


def test_under_cap_injects(tmp_path: Path) -> None:
    active, files = _dir(tmp_path, 300, 300)
    item = InjectionItem(directory=str(active), max_bytes=1000, **{"as": "context"})
    assert injection_over_max_bytes(item, files, active) is False


def test_required_over_cap_fails_with_the_measured_size(tmp_path: Path) -> None:
    active, files = _dir(tmp_path, 600, 600)
    item = InjectionItem(
        directory=str(active), max_bytes=1000, required=True, **{"as": "context"}
    )
    with pytest.raises(ValueError, match=r"1,200 bytes, over its max_bytes 1,000"):
        injection_over_max_bytes(item, files, active)


def test_optional_over_cap_is_dropped_not_shipped(tmp_path: Path) -> None:
    active, files = _dir(tmp_path, 600, 600)
    item = InjectionItem(directory=str(active), max_bytes=1000, **{"as": "context"})
    assert injection_over_max_bytes(item, files, active) is True


def test_single_file_cap_applies_too(tmp_path: Path) -> None:
    memo = tmp_path / "memory.md"
    memo.write_text("m" * 2048)
    item = InjectionItem(file=str(memo), max_bytes=1024, required=True, **{"as": "context"})
    with pytest.raises(ValueError, match="2,048 bytes"):
        injection_over_max_bytes(item, [memo], memo)


def test_max_bytes_must_be_positive() -> None:
    with pytest.raises(ValueError):
        InjectionItem(file="/x", max_bytes=0, **{"as": "context"})
