"""#352 increment 2: ``communicate()`` → concurrent gather-drain.

The swap is behavior-preserving (byte-identical returns with no
streaming consumer), so these tests are characterization armor for the
deadlock-avoidance contract ``communicate()`` used to provide — plus
the lab-mandated battery the new drain must survive:

- simultaneous stdout+stderr blast (THE sequential-read deadlock case)
- large single-pipe burst far beyond any OS pipe buffer
- one pipe closed early while the other keeps streaming
- hang mid-stream → timeout kills the process
- cancel mid-stream → kill-on-exit reaps the child
- invalid UTF-8 → decode ``errors="replace"`` after capture, not per-chunk

All tests run REAL subprocesses (integration over mocks for
infrastructure code). The subprocess body is the prompt itself, passed
to ``python3 -c`` via the profile's prompt flag.

Lab synthesis: ~/lab-archives/2026-06-11-streaming-352 (issue #352).
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _python_profile() -> InstrumentProfile:
    """A profile that runs ``python3 -c "<prompt>"`` — the prompt IS the
    subprocess body, letting each test script the exact pipe behavior."""
    return InstrumentProfile(
        name="drain-test",
        display_name="Drain Test",
        kind="cli",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(executable="python3", prompt_flag="-c"),
            output=CliOutputConfig(format="text"),
            errors=CliErrorConfig(success_exit_codes=[0]),
        ),
    )


async def _wait_pid_gone(pid: int, deadline_seconds: float = 10.0) -> bool:
    """Bounded poll until the pid no longer exists (no fixed sleeps)."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


CHUNK = 65536


class TestDrainDeadlockBattery:
    async def test_simultaneous_stdout_stderr_blast(self) -> None:
        """2MB to stdout interleaved with 2MB to stderr.

        A reader that drains one pipe to EOF before touching the other
        deadlocks here: the child blocks when the unread pipe's OS buffer
        fills, so the read pipe never reaches EOF.
        """
        script = (
            "import sys\n"
            f"o = b'o' * {CHUNK}\n"
            f"e = b'e' * {CHUNK}\n"
            "for _ in range(32):\n"
            "    sys.stdout.buffer.write(o)\n"
            "    sys.stderr.buffer.write(e)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.flush()\n"
        )
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(script, timeout_seconds=60)

        assert result.success is True
        assert len(result.stdout) == 32 * CHUNK
        assert len(result.stderr) == 32 * CHUNK

    async def test_large_stdout_burst(self) -> None:
        """50MB on a single pipe — far beyond any OS buffer — arrives whole."""
        total = 50 * 1024 * 1024
        script = (
            "import sys\n"
            "chunk = b'x' * (1024 * 1024)\n"
            "for _ in range(50):\n"
            "    sys.stdout.buffer.write(chunk)\n"
            "sys.stdout.buffer.flush()\n"
        )
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(script, timeout_seconds=120)

        assert result.success is True
        assert len(result.stdout) == total

    async def test_one_pipe_closed_early(self) -> None:
        """Child closes stderr immediately, then streams stdout."""
        script = (
            "import os, sys\n"
            "os.close(2)\n"
            f"sys.stdout.buffer.write(b'd' * (16 * {CHUNK}))\n"
            "sys.stdout.buffer.flush()\n"
        )
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(script, timeout_seconds=60)

        assert result.success is True
        assert len(result.stdout) == 16 * CHUNK
        assert result.stderr == ""

    async def test_timeout_mid_stream_kills_process(self) -> None:
        """Child emits output then hangs; the timeout must fire and the
        child must actually die (kill-on-exit), not linger."""
        script = (
            "import sys, time\n"
            "sys.stdout.write('partial output before hang\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(600)\n"
        )
        backend = PluginCliBackend(_python_profile())
        pids: list[int] = []
        backend._on_process_spawned = pids.append

        start = time.monotonic()
        result = await backend.execute(script, timeout_seconds=2)
        elapsed = time.monotonic() - start

        assert result.success is False
        assert result.exit_reason == "timeout"
        assert elapsed < 60
        assert len(pids) == 1
        assert await _wait_pid_gone(pids[0])

    async def test_cancel_mid_stream_reaps_child(self) -> None:
        """Cancelling execute() mid-stream must still reap the child via
        the kill-on-exit finally."""
        script = (
            "import sys, time\n"
            "sys.stdout.write('streaming\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(600)\n"
        )
        backend = PluginCliBackend(_python_profile())
        pids: list[int] = []
        backend._on_process_spawned = pids.append

        task = asyncio.create_task(backend.execute(script, timeout_seconds=600))
        deadline = time.monotonic() + 10
        while not pids and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert pids, "subprocess never spawned"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await _wait_pid_gone(pids[0])


class TestDrainMockSafety:
    """The mocked-pipe failure mode that crashed WSL must fail LOUDLY.

    An AsyncMock pipe whose read() returns a truthy MagicMock (never
    ``b""``) makes a naive drain loop append forever WITHOUT yielding to
    the event loop — wait_for's timeout can never fire, and the appends
    exhaust system memory (took down the whole WSL2 VM). The drain must
    refuse non-bytes on the FIRST read instead.
    """

    async def test_non_bytes_read_raises_instead_of_looping(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from marianne.execution.instruments.cli_backend import _drain_stream

        stream = AsyncMock()
        stream.read = AsyncMock(return_value=MagicMock())

        with pytest.raises(TypeError, match="expected bytes"):
            await _drain_stream(stream, [])

    async def test_str_read_raises(self) -> None:
        """str is not bytes — decode happens after capture, never inside."""
        from unittest.mock import AsyncMock

        from marianne.execution.instruments.cli_backend import _drain_stream

        stream = AsyncMock()
        stream.read = AsyncMock(return_value="text not bytes")

        with pytest.raises(TypeError, match="expected bytes"):
            await _drain_stream(stream, [])


class TestDrainEquivalence:
    """Byte-identical capture semantics versus the old communicate()."""

    async def test_small_output_exact(self) -> None:
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(
            "print('hello drain')", timeout_seconds=30
        )
        assert result.success is True
        assert result.stdout == "hello drain\n"
        assert result.stderr == ""
        assert result.exit_code == 0

    async def test_nonzero_exit_with_stderr(self) -> None:
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(
            "import sys; sys.stderr.write('boom\\n'); sys.exit(3)",
            timeout_seconds=30,
        )
        assert result.success is False
        assert result.exit_code == 3
        assert "boom" in result.stderr

    async def test_invalid_utf8_replaced_not_raised(self) -> None:
        """Decode happens AFTER capture with errors='replace' — a chunk
        boundary must never split a multibyte sequence into an error."""
        script = (
            "import sys\n"
            "sys.stdout.buffer.write(b'ok \\xff\\xfe bad')\n"
            "sys.stdout.buffer.flush()\n"
        )
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(script, timeout_seconds=30)

        assert result.success is True
        assert "ok " in result.stdout
        assert "�" in result.stdout

    async def test_multibyte_utf8_across_chunk_boundary(self) -> None:
        """A multibyte char straddling the 64KB read boundary must decode
        cleanly because joining precedes decoding."""
        # 65535 ASCII bytes, then a 3-byte char: bytes 65536..65538 span
        # the first read() boundary.
        script = (
            "import sys\n"
            f"sys.stdout.buffer.write(b'a' * ({CHUNK} - 1))\n"
            "sys.stdout.buffer.write('\\u20ac'.encode('utf-8'))\n"
            "sys.stdout.buffer.flush()\n"
        )
        backend = PluginCliBackend(_python_profile())
        result = await backend.execute(script, timeout_seconds=30)

        assert result.success is True
        assert result.stdout.endswith("€")
        assert "�" not in result.stdout
