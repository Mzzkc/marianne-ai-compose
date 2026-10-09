"""Real subprocess and file-facts boundaries for flow actions."""

from __future__ import annotations

import asyncio
import re
import signal
import time
from pathlib import Path

import psutil

from marianne.core.config.flow import RunTrigger
from marianne.daemon.baton import flow_actions
from marianne.daemon.baton.flow_actions import execute_run_action, prefetch_file_facts
from marianne.utils.process import safe_killpg


async def test_file_facts_are_bounded_and_compare_iteration_mtime(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    started = time.time()
    marker.write_text("done", encoding="utf-8")
    facts = await prefetch_file_facts(tmp_path, frozenset({"marker.txt"}), {}, started - 1)
    assert facts["marker.txt"].exists
    assert facts["marker.txt"].modified
    assert facts["marker.txt"].text == "done"
    marker.write_bytes(b"x" * 1_048_577)
    oversized = await prefetch_file_facts(tmp_path, frozenset({"marker.txt"}), {}, started - 1)
    assert oversized["marker.txt"].text is None


async def test_background_child_does_not_hold_run_open(tmp_path: Path) -> None:
    started = time.monotonic()
    outcome = await execute_run_action(
        RunTrigger(command="sleep 30 & echo done", timeout_seconds=2),
        workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
        chain_id=1, cursor=0, attempt=1, variables={},
    )
    assert outcome.exit_code == 0
    assert not outcome.timed_out
    assert time.monotonic() - started < 1.5
    assert outcome.log_path is not None
    assert "done" in Path(outcome.log_path).read_text(encoding="utf-8")


async def test_foreground_timeout_is_bounded(tmp_path: Path) -> None:
    started = time.monotonic()
    outcome = await execute_run_action(
        RunTrigger(command="sleep 30", timeout_seconds=0.25),
        workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
        chain_id=2, cursor=0, attempt=1, variables={},
    )
    assert outcome.timed_out
    assert time.monotonic() - started < 5


async def test_detached_log_holder_is_reaped_without_signalling_unrelated_process(
    tmp_path: Path, monkeypatch,
) -> None:
    original_spawn = flow_actions.asyncio.create_subprocess_exec
    redirects: list[object] = []

    async def capture_spawn(*args, **kwargs):
        redirects.append(kwargs["stdout"])
        return await original_spawn(*args, **kwargs)

    monkeypatch.setattr(flow_actions.asyncio, "create_subprocess_exec", capture_spawn)
    unrelated = await original_spawn(
        "sleep", "30", stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid: int | None = None
    try:
        outcome = await execute_run_action(
            RunTrigger(
                command="setsid sh -c 'echo child_pid=$$; exec sleep 30' & echo done",
                timeout_seconds=2,
            ),
            workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
            chain_id=3, cursor=0, attempt=1, variables={},
        )
        assert outcome.exit_code == 0
        assert not outcome.timed_out
        assert outcome.log_path is not None
        log = Path(outcome.log_path).read_text(encoding="utf-8")
        match = re.search(r"child_pid=(\d+)", log)
        assert match is not None
        child_pid = int(match.group(1))
        assert redirects and redirects[0] != asyncio.subprocess.PIPE
        assert unrelated.returncode is None
        assert (
            not psutil.pid_exists(child_pid)
            or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        )
    finally:
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                safe_killpg(child_pid, signal.SIGKILL, context="flow_test_cleanup")
            except (ProcessLookupError, PermissionError):
                pass
        if unrelated.returncode is None:
            try:
                safe_killpg(unrelated.pid, signal.SIGKILL, context="flow_test_cleanup")
            except (ProcessLookupError, PermissionError):
                pass
        await unrelated.wait()
