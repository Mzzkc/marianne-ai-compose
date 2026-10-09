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
from marianne.utils import process as process_utils
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
    action_a = execute_run_action(
        RunTrigger(command="sleep 30 & echo $! > child.pid; echo done", timeout_seconds=2),
        workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
        chain_id=1, cursor=0, attempt=1, variables={},
    )
    action_b = execute_run_action(
        RunTrigger(command="echo other", timeout_seconds=2),
        workspace=tmp_path, job_id="other", sheet_num=1, fired_epoch=0,
        chain_id=1, cursor=0, attempt=1, variables={},
    )
    outcome, other = await asyncio.gather(action_a, action_b)
    assert outcome.exit_code == 0
    assert other.exit_code == 0
    assert not outcome.timed_out
    assert time.monotonic() - started < 1
    assert outcome.log_path is not None
    assert "done" in Path(outcome.log_path).read_text(encoding="utf-8")
    child_pid = int((tmp_path / "child.pid").read_text().strip())
    assert (
        not psutil.pid_exists(child_pid)
        or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    )


async def test_foreground_timeout_is_bounded(tmp_path: Path) -> None:
    started = time.monotonic()
    outcome = await execute_run_action(
        RunTrigger(command="sleep 30", timeout_seconds=2),
        workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
        chain_id=2, cursor=0, attempt=1, variables={},
    )
    assert outcome.timed_out
    assert time.monotonic() - started < 5


async def test_detached_log_holder_is_reaped_without_signalling_unrelated_process(
    tmp_path: Path, monkeypatch,
) -> None:
    original_spawn = flow_actions.asyncio.create_subprocess_exec
    original_reap = process_utils.reap_descendant_trees
    redirects: list[object] = []
    signalled: list[int] = []

    async def capture_spawn(*args, **kwargs):
        redirects.append(kwargs["stdout"])
        return await original_spawn(*args, **kwargs)

    def capture_reap(descendants):
        result = original_reap(descendants)
        signalled.append(result.signalled)
        return result

    monkeypatch.setattr(flow_actions.asyncio, "create_subprocess_exec", capture_spawn)
    monkeypatch.setattr(process_utils, "reap_descendant_trees", capture_reap)
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
        assert sum(signalled) == 1
        assert unrelated.returncode is None
        assert (
            not psutil.pid_exists(child_pid)
            or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        )
    finally:
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if unrelated.returncode is None:
            try:
                safe_killpg(unrelated.pid, signal.SIGKILL, context="flow_test_cleanup")
            except (ProcessLookupError, PermissionError):
                pass
        await unrelated.wait()


async def test_closed_stdio_group_child_is_reaped_but_bystander_survives(
    tmp_path: Path,
) -> None:
    unrelated = await asyncio.create_subprocess_exec(
        "sleep", "30", start_new_session=True,
    )
    child_pid: int | None = None
    try:
        started = time.monotonic()
        outcome = await execute_run_action(
            RunTrigger(
                command="sleep 30 >/dev/null 2>&1 & echo $! > child.pid; echo done",
                timeout_seconds=2,
            ),
            workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
            chain_id=4, cursor=0, attempt=1, variables={},
        )
        child_pid = int((tmp_path / "child.pid").read_text().strip())
        assert outcome.exit_code == 0
        assert not outcome.timed_out
        assert time.monotonic() - started < 1
        assert (
            not psutil.pid_exists(child_pid)
            or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        )
        assert unrelated.returncode is None
    finally:
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                safe_killpg(child_pid, signal.SIGKILL, context="flow_test_cleanup")
            except (ProcessLookupError, PermissionError):
                pass
        if unrelated.returncode is None:
            unrelated.kill()
        await unrelated.wait()


async def test_run_log_identity_is_unique_across_repeated_and_fresh_jobs(tmp_path: Path) -> None:
    paths: list[Path] = []
    for _ in range(3):
        outcome = await execute_run_action(
            RunTrigger(command="echo done", timeout_seconds=2),
            workspace=tmp_path, job_id="same-score", sheet_num=1,
            fired_epoch=0, chain_id=1, cursor=0, attempt=1, variables={},
        )
        assert outcome.exit_code == 0
        assert outcome.log_path is not None
        paths.append(Path(outcome.log_path))
    assert len(set(paths)) == 3
    assert all(path.read_text().strip() == "done" for path in paths)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
