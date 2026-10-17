"""Real subprocess and file-facts boundaries for flow actions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
from pathlib import Path

import psutil
import pytest

from marianne.core.config.flow import RunTrigger
from marianne.daemon.baton import flow_actions
from marianne.daemon.baton.flow_actions import execute_run_action, prefetch_file_facts
from marianne.utils import process as process_utils
from marianne.utils.process import run_bounded_command, safe_killpg


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
    unrelated = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
    child_pid: int | None = None
    try:
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


async def test_foreground_timeout_is_bounded(tmp_path: Path) -> None:
    unrelated = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
    try:
        started = time.monotonic()
        outcome = await execute_run_action(
            RunTrigger(command="echo $$ > parent.pid; sleep 30", timeout_seconds=2),
            workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
            chain_id=2, cursor=0, attempt=1, variables={},
        )
        assert outcome.timed_out
        assert time.monotonic() - started < 5
        parent_pid = int((tmp_path / "parent.pid").read_text().strip())
        assert not psutil.pid_exists(parent_pid) or (
            psutil.Process(parent_pid).status() == psutil.STATUS_ZOMBIE
        )
        live_group: list[int] = []
        for process in psutil.process_iter(["pid"]):
            try:
                if (os.getpgid(process.pid) == parent_pid
                        and process.status() != psutil.STATUS_ZOMBIE):
                    live_group.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        assert live_group == []
        assert unrelated.returncode is None
    finally:
        if unrelated.returncode is None:
            unrelated.kill()
        await unrelated.wait()


async def test_detached_log_holder_is_reaped_without_signalling_unrelated_process(
    tmp_path: Path, monkeypatch,
) -> None:
    original_spawn = flow_actions.asyncio.create_subprocess_exec
    original_reap = process_utils.reap_descendant_trees
    original_killpg = process_utils.safe_killpg
    redirects: list[object] = []
    signalled: list[int] = []
    signalled_groups: list[int] = []
    supervisor_groups: list[tuple[int, int]] = []

    async def capture_spawn(*args, **kwargs):
        redirects.append(kwargs["stdout"])
        assert kwargs["start_new_session"] is True
        spawned = await original_spawn(*args, **kwargs)
        supervisor_groups.append((spawned.pid, os.getpgid(spawned.pid)))
        return spawned

    def capture_reap(descendants):
        result = original_reap(descendants)
        signalled.append(result.signalled)
        return result

    def capture_killpg(pgid, sig, *, context=""):
        signalled_groups.append(pgid)
        return original_killpg(pgid, sig, context=context)

    monkeypatch.setattr(flow_actions.asyncio, "create_subprocess_exec", capture_spawn)
    monkeypatch.setattr(process_utils, "reap_descendant_trees", capture_reap)
    monkeypatch.setattr(process_utils, "safe_killpg", capture_killpg)
    unrelated = await original_spawn(
        "sleep", "30", stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid: int | None = None
    try:
        started = time.monotonic()
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
        assert time.monotonic() - started < 1
        assert outcome.log_path is not None
        log = Path(outcome.log_path).read_text(encoding="utf-8")
        match = re.search(r"child_pid=(\d+)", log)
        assert match is not None
        child_pid = int(match.group(1))
        assert redirects and redirects[0] != asyncio.subprocess.PIPE
        assert supervisor_groups and supervisor_groups[0][0] == supervisor_groups[0][1]
        assert supervisor_groups[0][1] != os.getpgid(0)
        assert outcome.supervisor_pid not in signalled_groups
        assert sum(signalled) >= 1
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


async def test_detached_closed_stdio_orphan_is_reaped_after_clean_shell_exit(
    tmp_path: Path,
) -> None:
    """GH #421 Shape A: detached and closed-stdio descendants still belong to run."""
    bystander = await asyncio.create_subprocess_exec(
        "sleep", "30", start_new_session=True,
    )
    try:
        for iteration in range(5):
            child_file = tmp_path / f"detached-{iteration}.pid"
            command = (
                "setsid sh -c 'echo $$ > detached-"
                f"{iteration}.pid; exec >/dev/null 2>&1 </dev/null; exec sleep 30' "
                "& echo done"
            )
            outcome = await execute_run_action(
                RunTrigger(command=command, timeout_seconds=3),
                workspace=tmp_path, job_id=f"shape-a-{iteration}", sheet_num=1,
                fired_epoch=0, chain_id=1, cursor=0, attempt=1, variables={},
            )
            assert outcome.exit_code == 0
            assert not outcome.timed_out
            child_pid = int(child_file.read_text().strip())
            assert not psutil.pid_exists(child_pid) or (
                psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
            )
            assert bystander.returncode is None
    finally:
        for child_file in tmp_path.glob("detached-*.pid"):
            pid = int(child_file.read_text().strip())
            if psutil.pid_exists(pid):
                try:
                    psutil.Process(pid).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        if bystander.returncode is None:
            bystander.kill()
        await bystander.wait()


async def test_late_detached_fork_during_cleanup_is_reaped(tmp_path: Path) -> None:
    """GH #406 Shape B: a real fork after the first census remains owned."""
    leader = tmp_path / "late_leader.py"
    leader.write_text(
        "import os, signal, subprocess, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(0.15)\n"
        "child = subprocess.Popen(['sleep', '30'], start_new_session=True, "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL)\n"
        "Path('late.pid').write_text(str(child.pid))\n"
        "time.sleep(0.2)\n"
    )
    try:
        outcome = await execute_run_action(
            RunTrigger(
                command=f"'{os.sys.executable}' '{leader}' & echo done",
                timeout_seconds=3,
            ),
            workspace=tmp_path, job_id="shape-b", sheet_num=1,
            fired_epoch=0, chain_id=1, cursor=0, attempt=1, variables={},
        )
        assert (tmp_path / "late.pid").exists()
        child_pid = int((tmp_path / "late.pid").read_text())
        assert outcome.exit_code == 0
        assert not psutil.pid_exists(child_pid) or (
            psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        )
    finally:
        if (tmp_path / "late.pid").exists():
            child_pid = int((tmp_path / "late.pid").read_text())
            if psutil.pid_exists(child_pid):
                try:
                    psutil.Process(child_pid).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass


async def test_parent_waits_for_detached_closed_stdio_child_then_reaps_it(
    tmp_path: Path,
) -> None:
    """GH #421 Shape C keeps the direct shell's clean exit and child cleanup."""
    try:
        outcome = await execute_run_action(
            RunTrigger(
                command=(
                    "setsid sh -c 'echo $$ > waited.pid; "
                    "exec >/dev/null 2>&1 </dev/null; exec sleep 30' "
                    "& sleep 0.3; echo done"
                ),
                timeout_seconds=3,
            ),
            workspace=tmp_path, job_id="shape-c", sheet_num=1,
            fired_epoch=0, chain_id=1, cursor=0, attempt=1, variables={},
        )
        child_pid = int((tmp_path / "waited.pid").read_text())
        assert outcome.exit_code == 0
        assert outcome.supervisor_pid is not None
        assert outcome.reparented_orphans >= 1
        assert not psutil.pid_exists(child_pid) or (
            psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        )
    finally:
        if (tmp_path / "waited.pid").exists():
            child_pid = int((tmp_path / "waited.pid").read_text())
            if psutil.pid_exists(child_pid):
                try:
                    psutil.Process(child_pid).kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass


async def test_unavailable_subreaper_uses_existing_cleanup(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(flow_actions, "subreaper_available", lambda: False)
    outcome = await execute_run_action(
        RunTrigger(command="echo done", timeout_seconds=2),
        workspace=tmp_path, job_id="fallback", sheet_num=1,
        fired_epoch=0, chain_id=1, cursor=0, attempt=1, variables={},
    )
    assert outcome.exit_code == 0
    assert outcome.supervisor_pid is None


async def test_malformed_supervisor_state_refuses_and_stops_process(
    tmp_path: Path,
) -> None:
    state = tmp_path / "control.state"
    log_path = tmp_path / "control.log"
    with log_path.open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            os.sys.executable, "-c",
            "import pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text('[]'); "
            "time.sleep(30)",
            str(state), stdout=log, stderr=log, start_new_session=True,
        )
    created = psutil.Process(proc.pid).create_time()
    with pytest.raises(ValueError, match="malformed state"):
        await run_bounded_command(
            proc, timeout_seconds=3, pgid=proc.pid, context="malformed_state",
            file_output_path=log_path, parent_create_time=created,
            supervisor_state_path=state,
        )
    assert proc.returncode is not None


async def test_supervisor_killed_after_shell_reports_is_an_error_not_success(
    tmp_path: Path,
) -> None:
    """GH #444 (Circuit V1d): the subreaper is the #421 guarantee only while it
    lives. SIGKILL it after the shell has recorded its exit and an adopted
    orphan reparents to init where no capture source can see it. The settle
    must refuse to report the shell's clean exit as the action's outcome."""
    from marianne.utils import run_supervisor

    state = tmp_path / "killed.state"
    log_path = tmp_path / "killed.log"
    marker = tmp_path / "orphan.pid"
    # The shell exits at once; its backgrounded child outlives it and is
    # adopted by the supervisor (the orphan #421 exists to settle).
    command = (
        f"(sleep 30 & echo $! > {marker}) ; exit 0"
    )
    with log_path.open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            os.sys.executable, "-I", str(run_supervisor.__file__), str(state), command,
            stdout=log, stderr=log, start_new_session=True,
        )
    created = psutil.Process(proc.pid).create_time()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if json.loads(state.read_text()).get("exit_code") is not None:
                break
        except (OSError, ValueError):
            pass
        await asyncio.sleep(0.01)
    else:
        pytest.fail("supervisor never recorded the shell exit")
    orphan_pid = int(marker.read_text().strip())
    try:
        os.kill(proc.pid, signal.SIGKILL)  # the operator / OOM killer, not the settle
        with pytest.raises(ValueError, match="run supervisor died by signal 9"):
            await run_bounded_command(
                proc, timeout_seconds=3, pgid=proc.pid, context="killed_supervisor",
                file_output_path=log_path, parent_create_time=created,
                supervisor_state_path=state, kill_grace_seconds=0.2,
            )
    finally:
        try:
            os.kill(orphan_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


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


async def test_run_log_cap_ends_the_command_while_it_runs(tmp_path: Path) -> None:
    """GH #415: a child that writes without bound is ended when the log passes the
    cap, within the poll loop, instead of running to its full timeout and only
    being truncated afterwards. Peak disk use is bounded by the cap plus one poll."""
    from marianne.daemon.baton import flow_actions

    cap = 64 * 1024
    original = flow_actions.FLOW_RUN_LOG_CAP_BYTES
    flow_actions.FLOW_RUN_LOG_CAP_BYTES = cap
    try:
        outcome = await execute_run_action(
            RunTrigger(command="yes 0123456789abcdef; sleep 30", timeout_seconds=20),
            workspace=tmp_path, job_id="j", sheet_num=1, fired_epoch=0,
            chain_id=7, cursor=0, attempt=1, variables={},
        )
    finally:
        flow_actions.FLOW_RUN_LOG_CAP_BYTES = original
    # Ended by the cap: the 20 s command timeout did not fire, and the log
    # stopped at the cap instead of growing for the command's lifetime.
    assert not outcome.timed_out
    assert outcome.error and "exceeded" in outcome.error
    assert outcome.log_path is not None
    assert Path(outcome.log_path).stat().st_size <= cap
    assert not any(
        p.info["name"] == "yes" and os.getpgid(p.pid) != os.getpgid(0)
        for p in psutil.process_iter(["name"])
        if _alive(p)
    )


def _alive(process: psutil.Process) -> bool:
    try:
        return process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


async def test_workspace_package_named_marianne_cannot_shadow_the_supervisor(
    tmp_path: Path,
) -> None:
    """Forge Inspect P2 (2026-10-14): a score workspace holding a ``marianne``
    package shadowed ``python -m marianne.utils.run_supervisor`` because the
    action cwd led sys.path. The action must still run its shell there, write
    its output, exit 0, and leave no survivor."""
    shadow = tmp_path / "marianne"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('shadowed by workspace')\n")
    (shadow / "utils").mkdir()
    (shadow / "utils" / "__init__.py").write_text("")
    (shadow / "utils" / "run_supervisor.py").write_text(
        "raise RuntimeError('workspace run_supervisor must never run')\n"
    )
    outcome = await execute_run_action(
        RunTrigger(command="echo $$ > shell.pid; echo written > out.txt", timeout_seconds=5),
        workspace=tmp_path, job_id="shadow", sheet_num=1, fired_epoch=0,
        chain_id=1, cursor=0, attempt=1, variables={},
    )
    assert outcome.error is None, outcome.error
    assert outcome.exit_code == 0
    assert (tmp_path / "out.txt").read_text() == "written\n"
    shell_pid = int((tmp_path / "shell.pid").read_text().strip())
    assert not psutil.pid_exists(shell_pid) or (
        psutil.Process(shell_pid).status() == psutil.STATUS_ZOMBIE
    )
    assert outcome.log_path is not None
    assert "shadowed by workspace" not in Path(outcome.log_path).read_text()


@pytest.mark.parametrize("pythonpath", [".", ":/nonexistent", "/nonexistent:"])
async def test_pythonpath_cannot_reopen_the_supervisor_shadow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pythonpath: str,
) -> None:
    """Circuit V3 (GH #445): ``-P -m`` was still shadowed when PYTHONPATH was
    "." or carried an empty entry (the shape ``export PYTHONPATH=$PYTHONPATH:/x``
    produces when the variable was unset). The supervisor is now launched with
    ``-I`` by its absolute file path, which ignores PYTHONPATH entirely."""
    shadow = tmp_path / "marianne"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('shadowed by workspace')\n")
    (shadow / "utils").mkdir()
    (shadow / "utils" / "__init__.py").write_text("")
    (shadow / "utils" / "run_supervisor.py").write_text(
        "raise RuntimeError('workspace run_supervisor must never run')\n"
    )
    monkeypatch.setenv("PYTHONPATH", pythonpath)
    outcome = await execute_run_action(
        RunTrigger(command="echo $$ > shell.pid; echo written > out.txt", timeout_seconds=5),
        workspace=tmp_path, job_id="shadow-pp", sheet_num=1, fired_epoch=0,
        chain_id=1, cursor=0, attempt=1, variables={},
    )
    assert outcome.error is None, outcome.error
    assert outcome.exit_code == 0
    assert (tmp_path / "out.txt").read_text() == "written\n"
    shell_pid = int((tmp_path / "shell.pid").read_text().strip())
    assert not psutil.pid_exists(shell_pid) or (
        psutil.Process(shell_pid).status() == psutil.STATUS_ZOMBIE
    )


async def test_settle_leaves_a_reader_of_the_action_log_alone(tmp_path: Path) -> None:
    """GH #449: a same-UID process that only READS the action log (an
    operator's ``tail -f``) was captured as a file holder and SIGTERMed at
    settle. Only a writer is the escapee that source exists to catch."""
    import asyncio
    import subprocess

    async def start_reader_when_log_appears() -> subprocess.Popen[bytes]:
        flow_dir = tmp_path / ".marianne-flow"
        for _ in range(200):
            logs = sorted(flow_dir.rglob("*.log")) if flow_dir.exists() else []
            if logs:
                return subprocess.Popen(
                    ["tail", "-f", str(logs[0])],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            await asyncio.sleep(0.01)
        raise AssertionError("action log never appeared")

    reader_task = asyncio.create_task(start_reader_when_log_appears())
    outcome = await execute_run_action(
        RunTrigger(
            command="for i in 1 2 3 4 5 6; do echo tick $i; sleep 0.2; done",
            timeout_seconds=10,
        ),
        workspace=tmp_path, job_id="bystander", sheet_num=1, fired_epoch=0,
        chain_id=1, cursor=0, attempt=1, variables={},
    )
    reader = await reader_task
    try:
        assert outcome.error is None, outcome.error
        assert outcome.exit_code == 0
        await asyncio.sleep(0.3)
        assert reader.poll() is None, f"reader was ended: returncode={reader.returncode}"
    finally:
        reader.kill()
        reader.wait()


def test_fd_open_for_writing_reads_proc_fdinfo_access_mode(tmp_path: Path) -> None:
    import os

    from marianne.utils.process import _fd_open_for_writing

    target = tmp_path / "log"
    target.write_text("")
    reader = os.open(target, os.O_RDONLY)
    writer = os.open(target, os.O_WRONLY | os.O_APPEND)
    both = os.open(target, os.O_RDWR)
    try:
        assert _fd_open_for_writing(os.getpid(), str(reader)) is False
        assert _fd_open_for_writing(os.getpid(), str(writer)) is True
        assert _fd_open_for_writing(os.getpid(), str(both)) is True
    finally:
        for fd in (reader, writer, both):
            os.close(fd)
    # An unreadable fdinfo errs toward capture (the pre-#449 behaviour).
    assert _fd_open_for_writing(os.getpid(), "999999") is True
