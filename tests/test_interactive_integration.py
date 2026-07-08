"""Integration tests for interactive mode against a REAL tmux server.

Skipped when tmux >= 3.2 is unavailable. Uses a scripted fake agent (a tiny
Python REPL) — deterministic, no model calls, no quota.

Pins the lab-mandated launch-path property: the pane process is the command
itself (pid == pgid, no shell wrapper), so the baton's PID registration and
group-kill plumbing hold on the implementation's real launch path — not just
the manual spike command.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from marianne.core.config.instruments import InteractiveCliConfig
from marianne.execution.instruments.interactive.driver import (
    InteractiveSessionDriver,
    StaticNudgePolicy,
)
from marianne.execution.instruments.interactive.tmux import TmuxControl, TmuxError


def _tmux_available() -> bool:
    if shutil.which("tmux") is None:
        return False
    try:
        out = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    import re

    match = re.search(r"(\d+)\.(\d+)", out)
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (3, 2)


pytestmark = pytest.mark.skipif(
    not _tmux_available(), reason="tmux >= 3.2 not available",
)


# A minimal scripted agent: prints a ❯ prompt, logs every received line to
# received.txt, and creates the marker file (argv[1]) when it sees a line
# containing DO-TASK or the nudge message.
FAKE_AGENT_SCRIPT = """\
import sys, time

marker = sys.argv[1]
log = open("received.txt", "a", encoding="utf-8")
print("fake-agent booted")
while True:
    print("\\u276f ", flush=True)
    line = sys.stdin.readline()
    if not line:
        time.sleep(0.05)
        continue
    log.write(line)
    log.flush()
    if "DO-TASK" in line or "please continue" in line:
        print("fake-agent completed task", flush=True)
        time.sleep(0.2)
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("done")
"""


@pytest_asyncio.fixture
async def tmux() -> AsyncIterator[TmuxControl]:
    """Isolated per-test tmux server, killed at teardown (test-only API)."""
    control = TmuxControl(socket_name=f"mzt-test-{os.getpid()}")
    yield control
    await control.kill_server()


def _interactive_config() -> InteractiveCliConfig:
    return InteractiveCliConfig(
        ready_pattern=r"(?m)^❯",
        busy_patterns=[],
        quiet_seconds=0.8,
        poll_interval_seconds=0.1,
        startup_timeout_seconds=15.0,
        terminal_width=120,
        terminal_height=40,
        volatile_tail_lines=0,
    )


def _write_fake_agent(tmp_path: Path) -> Path:
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT_SCRIPT, encoding="utf-8")
    return script


class TestLaunchPathProcessIdentity:
    """The lab's P0 gate: argv launch must preserve pid == pgid (no shell)."""

    async def test_pane_pid_is_own_pgid(self, tmux: TmuxControl) -> None:
        await tmux.new_session(
            "identity-probe", ["sleep", "60"],
            cwd=Path.cwd(), width=80, height=24,
        )
        try:
            pid = await tmux.pane_pid("identity-probe")
            assert pid is not None
            pgid = os.getpgid(pid)
            assert pgid == pid, (
                f"pane process {pid} is not its own process-group leader "
                f"(pgid={pgid}) — a shell wrapper crept into the launch path"
            )
            # And it really is the command, not a shell
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            assert comm == "sleep"
        finally:
            await tmux.kill_session("identity-probe")

        # kill_session reaps the process
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    async def test_args_with_spaces_survive(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        """argv boundaries are explicit — no shell splitting/injection."""
        out = tmp_path / "argv.txt"
        await tmux.new_session(
            "argv-probe",
            [
                sys.executable, "-c",
                "import sys, pathlib; "
                "pathlib.Path(sys.argv[1]).write_text(repr(sys.argv[2:]))",
                str(out),
                "two words", "$(injection)", "a;b",
            ],
            cwd=tmp_path, width=80, height=24,
        )
        try:
            deadline = asyncio.get_event_loop().time() + 10.0
            while not out.exists() and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.1)
            assert out.exists(), "probe never wrote its argv"
            assert out.read_text() == repr(["two words", "$(injection)", "a;b"])
        finally:
            await tmux.kill_session("argv-probe")


class TestDriverAgainstRealTmux:
    async def test_happy_path_completes_without_nudge(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        script = _write_fake_agent(tmp_path)
        marker = tmp_path / "attempt.complete"
        driver = InteractiveSessionDriver(tmux, _interactive_config())

        result = await driver.run(
            session="it-happy",
            command=[sys.executable, str(script), str(marker)],
            cwd=tmp_path,
            prompt="hello agent\nDO-TASK now\nwith a unicode flourish: ✨🎼",
            marker_path=marker,
            timeout_seconds=25.0,
            policy=StaticNudgePolicy("please continue"),
            max_nudges=3,
        )

        assert result.outcome == "completed", result.detail
        assert result.nudges_sent == 0
        assert marker.read_text() == "done"
        # Unicode survived the paste path byte-exact
        received = (tmp_path / "received.txt").read_text(encoding="utf-8")
        assert "DO-TASK now" in received
        assert "✨🎼" in received
        # Session is gone after cleanup
        assert not await tmux.has_session("it-happy")

    async def test_idle_agent_gets_nudged_to_completion(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        script = _write_fake_agent(tmp_path)
        marker = tmp_path / "attempt.complete"
        driver = InteractiveSessionDriver(tmux, _interactive_config())

        # The prompt does NOT contain the trigger — the agent sits idle at
        # its prompt until the nudge ("please continue") arrives.
        result = await driver.run(
            session="it-nudge",
            command=[sys.executable, str(script), str(marker)],
            cwd=tmp_path,
            prompt="this prompt asks for nothing",
            marker_path=marker,
            timeout_seconds=25.0,
            policy=StaticNudgePolicy("please continue"),
            max_nudges=3,
        )

        assert result.outcome == "completed", result.detail
        assert result.nudges_sent >= 1
        assert marker.exists()
        assert not await tmux.has_session("it-nudge")

    async def test_dead_agent_reports_session_lost(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        marker = tmp_path / "attempt.complete"
        driver = InteractiveSessionDriver(tmux, _interactive_config())

        # An "agent" that prints a prompt then exits immediately.
        result = await driver.run(
            session="it-dead",
            command=[
                sys.executable, "-c", "print('\\u276f '); import time; time.sleep(1.5)",
            ],
            cwd=tmp_path,
            prompt="anything",
            marker_path=marker,
            timeout_seconds=25.0,
            policy=StaticNudgePolicy("please continue"),
            max_nudges=2,
        )

        # The pane dies (tmux destroys the session) before completion.
        assert result.outcome in ("session_lost", "nudges_exhausted")
        assert not marker.exists()
        assert not await tmux.has_session("it-dead")

    async def test_transcript_pipe_captures_output(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        script = _write_fake_agent(tmp_path)
        marker = tmp_path / "attempt.complete"
        transcript = tmp_path / "logs" / "session.log"
        driver = InteractiveSessionDriver(tmux, _interactive_config())

        result = await driver.run(
            session="it-pipe",
            command=[sys.executable, str(script), str(marker)],
            cwd=tmp_path,
            prompt="DO-TASK",
            marker_path=marker,
            timeout_seconds=25.0,
            policy=StaticNudgePolicy("please continue"),
            max_nudges=2,
            transcript_path=transcript,
        )

        assert result.outcome == "completed", result.detail
        assert transcript.exists()
        assert "fake-agent completed task" in transcript.read_text(
            encoding="utf-8", errors="replace",
        )

    async def test_concurrent_sessions_are_isolated(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        """Two sheets on one server: distinct markers, no cross-talk."""
        ws_a = tmp_path / "a"
        ws_b = tmp_path / "b"
        ws_a.mkdir()
        ws_b.mkdir()
        script_a = _write_fake_agent(ws_a)
        script_b = _write_fake_agent(ws_b)
        marker_a = ws_a / "a.complete"
        marker_b = ws_b / "b.complete"
        driver = InteractiveSessionDriver(tmux, _interactive_config())

        result_a, result_b = await asyncio.gather(
            driver.run(
                session="it-conc-a",
                command=[sys.executable, str(script_a), str(marker_a)],
                cwd=ws_a,
                prompt="A: DO-TASK alpha-payload",
                marker_path=marker_a,
                timeout_seconds=25.0,
                policy=StaticNudgePolicy("please continue"),
                max_nudges=2,
            ),
            driver.run(
                session="it-conc-b",
                command=[sys.executable, str(script_b), str(marker_b)],
                cwd=ws_b,
                prompt="B: DO-TASK beta-payload",
                marker_path=marker_b,
                timeout_seconds=25.0,
                policy=StaticNudgePolicy("please continue"),
                max_nudges=2,
            ),
        )

        assert result_a.outcome == "completed", result_a.detail
        assert result_b.outcome == "completed", result_b.detail
        # No cross-contamination of pasted prompts (per-session buffers)
        received_a = (ws_a / "received.txt").read_text(encoding="utf-8")
        received_b = (ws_b / "received.txt").read_text(encoding="utf-8")
        assert "alpha-payload" in received_a and "beta-payload" not in received_a
        assert "beta-payload" in received_b and "alpha-payload" not in received_b


class TestTmuxControlHardening:
    async def test_kill_session_idempotent(self, tmux: TmuxControl) -> None:
        # Killing a nonexistent session must not raise.
        await tmux.kill_session("never-existed")

    async def test_capture_on_missing_session_raises(
        self, tmux: TmuxControl,
    ) -> None:
        with pytest.raises(TmuxError):
            await tmux.capture_screen("never-existed")

    async def test_list_sessions_prefix_filter(
        self, tmux: TmuxControl, tmp_path: Path,
    ) -> None:
        await tmux.new_session(
            "mzt-sweep-probe", ["sleep", "30"],
            cwd=tmp_path, width=80, height=24,
        )
        try:
            names = await tmux.list_sessions(prefix="mzt-")
            assert "mzt-sweep-probe" in names
            assert await tmux.list_sessions(prefix="zzz-") == []
        finally:
            await tmux.kill_session("mzt-sweep-probe")

    async def test_version_parses(self, tmux: TmuxControl) -> None:
        version = await tmux.version()
        assert version is not None and version >= (3, 2)
