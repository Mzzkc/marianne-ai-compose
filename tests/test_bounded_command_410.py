"""GH #410: skip_when and command_succeeds must not burn their timeout when the
command backgrounds a child that inherits stdout/stderr (the #406 shape).

Derived from Blueprint's integration probes i4/i6 (2026-10-09): control `exit 0`
and stimulus `sleep N & exit 0` — the parent exits 0 immediately; the child
holds the pipes. Before the shared helper, both seams waited for pipe EOF.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import pytest

from marianne.core.config.execution import SkipWhenCommand, ValidationRule
from marianne.daemon.baton.skip import evaluate_skip_command
from marianne.execution.validation.engine import ValidationEngine


def _alive_sleepers(tag: str) -> list[int]:
    out: list[int] = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{name}/cmdline").read_bytes().replace(b"\0", b" ").strip()
            if cmdline == f"sleep {tag}".encode():
                stat = Path(f"/proc/{name}/stat").read_text().rsplit(")", 1)[1].split()[0]
                if stat != "Z":
                    out.append(int(name))
        except OSError:
            continue
    return out


def _kill(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.asyncio
async def test_skip_when_backgrounded_child_does_not_burn_timeout(tmp_path: Path) -> None:
    tag = "41"
    try:
        swc = SkipWhenCommand(command=f"sleep {tag} & exit 0", timeout_seconds=5)
        t0 = time.monotonic()
        should_skip, reason = await evaluate_skip_command(swc, workspace=tmp_path, context={})
        elapsed = time.monotonic() - t0
        assert should_skip is True, reason
        # Quality gate: timing bounds >= 30s. The real proof is passed=True under a
        # 5s command timeout (the old path burned all 5s and failed).
        assert elapsed < 30.0, f"burned the timeout: {elapsed:.2f}s"
        assert _alive_sleepers(tag) == [], "backgrounded child survived the group kill"
    finally:
        _kill(_alive_sleepers(tag))


@pytest.mark.asyncio
async def test_command_succeeds_backgrounded_child_does_not_burn_timeout(tmp_path: Path) -> None:
    tag = "42"
    try:
        engine = ValidationEngine(tmp_path, {"sheet_num": 1})
        rule = ValidationRule(
            type="command_succeeds", command=f"sleep {tag} & exit 0",
            timeout_seconds=5, description="bg child",
        )
        t0 = time.monotonic()
        res = await engine.run_validations([rule])
        elapsed = time.monotonic() - t0
        r = res.results[0]
        assert r.passed is True, r.error_message
        # Quality gate: timing bounds >= 30s. The real proof is passed=True under a
        # 5s command timeout (the old path burned all 5s and failed).
        assert elapsed < 30.0, f"burned the timeout: {elapsed:.2f}s"
        assert _alive_sleepers(tag) == [], "backgrounded child survived the group kill"
    finally:
        _kill(_alive_sleepers(tag))


@pytest.mark.asyncio
async def test_command_succeeds_live_parent_still_times_out(tmp_path: Path) -> None:
    engine = ValidationEngine(tmp_path, {"sheet_num": 1})
    rule = ValidationRule(
        type="command_succeeds", command="sleep 30", timeout_seconds=1, description="slow",
    )
    t0 = time.monotonic()
    res = await engine.run_validations([rule])
    assert res.results[0].passed is False
    assert "timed out" in (res.results[0].error_message or "")
    assert time.monotonic() - t0 < 6.0
