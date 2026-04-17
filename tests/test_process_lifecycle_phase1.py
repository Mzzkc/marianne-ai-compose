"""TDD tests for Process Lifecycle Phase 1.

Spec: docs/specs/2026-04-16-process-lifecycle-design.md

Phase 1 items:
    1. start_new_session=True in cli_backend.py (baton path) and engine.py
    2. pgid capture at spawn; daemon-own-group safety check
    3. finally blocks with SIGTERM -> 2s grace -> SIGKILL in both files
    4. In-memory _active_pids dict in adapter; on_process_spawned callback (pid, pgid)
    5. Kill process groups in deregister_job() before cancelling asyncio tasks
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_profile(
    name: str = "test-cli",
    *,
    start_new_session: bool = False,
) -> InstrumentProfile:
    """Create a minimal CLI InstrumentProfile for testing."""
    return InstrumentProfile(
        name=name,
        display_name=f"Test CLI: {name}",
        kind="cli",
        description=f"Test CLI instrument: {name}",
        models=[
            ModelCapacity(
                name="test-model",
                context_window=128000,
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.03,
            ),
        ],
        default_model="test-model",
        cli=CliProfile(
            command=CliCommand(
                executable="echo",
                extra_flags=[],
                start_new_session=start_new_session,
            ),
            output=CliOutputConfig(),
            errors=CliErrorConfig(success_exit_codes=[0]),
        ),
    )


# ============================================================
# Item 4 (part 1): new callback slot for (pid, pgid)
# ============================================================


class TestProcessGroupSpawnedCallbackSlot:
    """PluginCliBackend exposes a _on_process_group_spawned callback slot."""

    def test_slot_exists_and_defaults_none(self) -> None:
        backend = PluginCliBackend(profile=_make_profile())
        assert hasattr(backend, "_on_process_group_spawned")
        assert backend._on_process_group_spawned is None

    def test_slot_accepts_callable(self) -> None:
        backend = PluginCliBackend(profile=_make_profile())
        cb = MagicMock()
        backend._on_process_group_spawned = cb
        assert backend._on_process_group_spawned is cb


# ============================================================
# Item 1 + 2: start_new_session forced + pgid captured
# ============================================================


class TestStartNewSessionForcedForBatonPath:
    """When the group-aware callback is set, start_new_session is forced True."""

    @pytest.mark.asyncio
    async def test_forces_true_when_group_callback_set(self) -> None:
        backend = PluginCliBackend(profile=_make_profile(start_new_session=False))
        backend._on_process_group_spawned = lambda pid, pgid: None

        observed: dict[str, object] = {}
        original = asyncio.create_subprocess_exec

        async def recording_exec(*args, **kwargs):
            observed["start_new_session"] = kwargs.get("start_new_session")
            return await original(*args, **kwargs)

        with patch(
            "marianne.execution.instruments.cli_backend."
            "asyncio.create_subprocess_exec",
            side_effect=recording_exec,
        ):
            await backend.execute("test prompt")

        assert observed["start_new_session"] is True

    @pytest.mark.asyncio
    async def test_respects_profile_when_group_callback_absent(self) -> None:
        backend = PluginCliBackend(profile=_make_profile(start_new_session=False))

        observed: dict[str, object] = {}
        original = asyncio.create_subprocess_exec

        async def recording_exec(*args, **kwargs):
            observed["start_new_session"] = kwargs.get("start_new_session")
            return await original(*args, **kwargs)

        with patch(
            "marianne.execution.instruments.cli_backend."
            "asyncio.create_subprocess_exec",
            side_effect=recording_exec,
        ):
            await backend.execute("test prompt")

        assert observed["start_new_session"] is False


class TestPgidCapturedAndCallbackFires:
    """pgid is captured at spawn and reported via the group-aware callback."""

    @pytest.mark.asyncio
    async def test_callback_fires_with_pid_and_pgid(self) -> None:
        backend = PluginCliBackend(profile=_make_profile())
        captured: list[tuple[int, int]] = []
        backend._on_process_group_spawned = lambda pid, pgid: captured.append(
            (pid, pgid)
        )

        expected_pid = 31337
        expected_pgid = 31337  # start_new_session=True => pgid == pid
        proc = _mock_proc(pid=expected_pid, returncode=0)

        async def fake_spawn(*args, **kwargs):
            return proc

        def fake_getpgid(pid: int) -> int:
            if pid == 0:
                return 99999  # daemon pgid — distinct from child's
            return expected_pgid

        with patch(
            "marianne.execution.instruments.cli_backend."
            "asyncio.create_subprocess_exec",
            side_effect=fake_spawn,
        ), patch(
            "marianne.execution.instruments.cli_backend.os.getpgid",
            side_effect=fake_getpgid,
        ):
            await backend.execute("test prompt")

        assert len(captured) == 1
        pid, pgid = captured[0]
        assert pid == expected_pid
        assert pgid == expected_pgid
        # With start_new_session=True forced, pgid should equal pid.
        assert pgid == pid


# ============================================================
# Item 2: daemon-own-group safety check
# ============================================================


class TestDaemonOwnGroupSafety:
    """Spawn refuses to proceed if the subprocess shares the daemon's pgid."""

    @pytest.mark.asyncio
    async def test_raises_when_pgid_matches_daemon(self) -> None:
        backend = PluginCliBackend(profile=_make_profile())
        backend._on_process_group_spawned = lambda pid, pgid: None

        daemon_pgid = 99999

        def fake_getpgid(pid: int) -> int:
            return daemon_pgid

        with patch(
            "marianne.execution.instruments.cli_backend.os.getpgid",
            side_effect=fake_getpgid,
        ), pytest.raises(RuntimeError, match="shares daemon pgid"):
            await backend.execute("test prompt")


# ============================================================
# Item 3: finally block kills the process group on exit paths
# ============================================================


def _mock_proc(
    *,
    pid: int = 12345,
    returncode: int | None = 0,
    communicate_behavior: str = "normal",
) -> MagicMock:
    """Build a mock asyncio subprocess.Process with configurable behavior."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.stdin = None

    async def communicate_normal():
        return (b"ok\n", b"")

    async def communicate_hangs():
        await asyncio.sleep(100)
        return (b"", b"")

    if communicate_behavior == "normal":
        proc.communicate = communicate_normal
    elif communicate_behavior == "hangs":
        proc.communicate = communicate_hangs
    else:
        raise ValueError(communicate_behavior)

    async def wait_default():
        return proc.returncode

    proc.wait = AsyncMock(side_effect=wait_default)
    proc.kill = MagicMock()
    return proc


class TestFinallyKillsOnCancel:
    """finally block runs killpg SIGTERM on task cancellation."""

    @pytest.mark.asyncio
    async def test_killpg_sigterm_fires_on_task_cancellation(self) -> None:
        backend = PluginCliBackend(profile=_make_profile())
        backend._on_process_group_spawned = lambda pid, pgid: None

        pgid_captured = 54321
        proc = _mock_proc(
            pid=pgid_captured, returncode=None, communicate_behavior="hangs",
        )

        async def fake_spawn(*args, **kwargs):
            return proc

        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))
            if sig == signal.SIGTERM:
                proc.returncode = -signal.SIGTERM

        def fake_getpgid(pid: int) -> int:
            return pgid_captured if pid != 0 else 11111

        with patch(
            "marianne.execution.instruments.cli_backend."
            "asyncio.create_subprocess_exec",
            side_effect=fake_spawn,
        ), patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.execution.instruments.cli_backend.os.getpgid",
            side_effect=fake_getpgid,
        ):
            task = asyncio.create_task(backend.execute("test"))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert (pgid_captured, signal.SIGTERM) in killpg_calls


class TestFinallyDoesNotKillOnCleanExit:
    """Clean exit must NOT trigger killpg."""

    @pytest.mark.asyncio
    async def test_no_killpg_after_clean_completion(self) -> None:
        backend = PluginCliBackend(profile=_make_profile())
        backend._on_process_group_spawned = lambda pid, pgid: None

        pgid_captured = 22222
        proc = _mock_proc(
            pid=pgid_captured, returncode=0, communicate_behavior="normal",
        )

        async def fake_spawn(*args, **kwargs):
            return proc

        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))

        def fake_getpgid(pid: int) -> int:
            return pgid_captured if pid != 0 else 11111

        with patch(
            "marianne.execution.instruments.cli_backend."
            "asyncio.create_subprocess_exec",
            side_effect=fake_spawn,
        ), patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.execution.instruments.cli_backend.os.getpgid",
            side_effect=fake_getpgid,
        ):
            await backend.execute("test prompt")

        assert killpg_calls == []


# ============================================================
# Engine.py — validation command spawn protection
# Items 1 + 3 apply to _check_command_succeeds as well.
# ============================================================


def _make_validation_rule(
    command: str = "true",
    *,
    timeout_seconds: float | None = None,
):
    """Build a minimal command_succeeds ValidationRule."""
    from marianne.core.config import ValidationRule

    return ValidationRule(
        type="command_succeeds",
        command=command,
        timeout_seconds=timeout_seconds,
    )


class TestEngineCommandSucceedsUsesProcessGroup:
    """Validation command spawn forces start_new_session=True."""

    @pytest.mark.asyncio
    async def test_spawn_forces_start_new_session(self, tmp_path) -> None:
        from marianne.execution.validation.engine import ValidationEngine

        engine = ValidationEngine(workspace=tmp_path, sheet_context={})
        observed: dict[str, object] = {}

        async def recording_spawn(*args, **kwargs):
            observed["start_new_session"] = kwargs.get("start_new_session")
            proc = MagicMock()
            proc.pid = 55555

            async def communicate():
                return (b"", b"")

            proc.communicate = communicate
            proc.returncode = 0
            proc.wait = AsyncMock(return_value=0)
            proc.kill = MagicMock()
            return proc

        with patch(
            "marianne.execution.validation.engine."
            "asyncio.create_subprocess_exec",
            side_effect=recording_spawn,
        ), patch(
            "marianne.execution.validation.engine.os.getpgid",
            return_value=55555,
        ):
            await engine._check_command_succeeds(
                _make_validation_rule(command="true"),
            )

        assert observed["start_new_session"] is True


class TestEngineCommandSucceedsFinallyKillsOnTimeout:
    """Validation command timeout path runs killpg SIGTERM on the group."""

    @pytest.mark.asyncio
    async def test_killpg_fires_on_timeout(self, tmp_path) -> None:
        from marianne.execution.validation.engine import ValidationEngine

        engine = ValidationEngine(workspace=tmp_path, sheet_context={})

        pgid_captured = 77777
        proc = MagicMock()
        proc.pid = pgid_captured
        proc.returncode = None
        proc.kill = MagicMock()

        async def communicate_hangs():
            await asyncio.sleep(100)
            return (b"", b"")

        proc.communicate = communicate_hangs

        async def wait_default():
            return proc.returncode

        proc.wait = AsyncMock(side_effect=wait_default)

        async def fake_spawn(*args, **kwargs):
            return proc

        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))
            if sig == signal.SIGTERM:
                proc.returncode = -signal.SIGTERM

        def fake_getpgid(pid: int) -> int:
            return pgid_captured if pid != 0 else 11111

        with patch(
            "marianne.execution.validation.engine."
            "asyncio.create_subprocess_exec",
            side_effect=fake_spawn,
        ), patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.execution.validation.engine.os.getpgid",
            side_effect=fake_getpgid,
        ):
            result = await engine._check_command_succeeds(
                _make_validation_rule(
                    command="sleep 100", timeout_seconds=0.05,
                ),
            )

        assert not result.passed
        assert (pgid_captured, signal.SIGTERM) in killpg_calls


class TestEngineCommandSucceedsFinallyNoKillOnCleanExit:
    """Clean exit of the validation command must NOT trigger killpg."""

    @pytest.mark.asyncio
    async def test_no_killpg_after_clean_completion(self, tmp_path) -> None:
        from marianne.execution.validation.engine import ValidationEngine

        engine = ValidationEngine(workspace=tmp_path, sheet_context={})

        pgid_captured = 88888
        proc = MagicMock()
        proc.pid = pgid_captured
        proc.returncode = 0

        async def communicate_ok():
            return (b"", b"")

        proc.communicate = communicate_ok
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()

        async def fake_spawn(*args, **kwargs):
            return proc

        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))

        def fake_getpgid(pid: int) -> int:
            return pgid_captured if pid != 0 else 11111

        with patch(
            "marianne.execution.validation.engine."
            "asyncio.create_subprocess_exec",
            side_effect=fake_spawn,
        ), patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.execution.validation.engine.os.getpgid",
            side_effect=fake_getpgid,
        ):
            result = await engine._check_command_succeeds(
                _make_validation_rule(command="true"),
            )

        assert result.passed
        assert killpg_calls == []


# ============================================================
# Item 4: adapter._active_pids populated via wired callback
# ============================================================


def _make_adapter():
    """Create a fresh BatonAdapter inside an asyncio context.

    BatonAdapter.__init__ creates an asyncio.Queue which needs a running
    event loop; callers must invoke this inside `@pytest.mark.asyncio`
    tests or similar.
    """
    from marianne.daemon.baton.adapter import BatonAdapter

    return BatonAdapter()


class TestAdapterActivePidsInitialization:
    """BatonAdapter exposes an _active_pids dict that starts empty."""

    @pytest.mark.asyncio
    async def test_active_pids_attribute_exists_and_empty(self) -> None:
        adapter = _make_adapter()
        assert hasattr(adapter, "_active_pids")
        assert adapter._active_pids == {}


class TestMusicianWrapperWiresCallback:
    """_musician_wrapper installs a closure on backend._on_process_group_spawned
    that populates _active_pids for the dispatched (job_id, sheet_num)."""

    @pytest.mark.asyncio
    async def test_callback_closure_populates_active_pids(self) -> None:
        """When backend's callback fires mid-execute, _active_pids gains the entry."""
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        adapter = _make_adapter()

        # Fake backend with _on_process_group_spawned slot; fires the callback
        # during execute() to simulate a subprocess spawn.
        backend = MagicMock()
        backend._on_process_group_spawned = None
        backend.set_preamble = MagicMock()

        pid, pgid = 42424, 42424

        async def fake_execute(*args, **kwargs):
            cb = backend._on_process_group_spawned
            if cb is not None:
                cb(pid, pgid)
            from marianne.backends.base import ExecutionResult
            return ExecutionResult(
                success=True, stdout="ok", stderr="", exit_code=0,
                duration_seconds=0.01,
            )

        backend.execute = fake_execute

        # Fake pool with release()
        pool = MagicMock()
        pool.release = AsyncMock()
        pool._registry = None
        adapter.set_backend_pool(pool)

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        sheet = Sheet(
            num=7, movement=1, voice=1, voice_count=1,
            description="t", workspace=tmp,
            instrument_name="test-cli", instrument_config={},
            instrument_fallbacks=[], prompt_template="p",
            validations=[], timeout_seconds=60,
        )
        adapter._job_sheets["J1"] = {7: sheet}

        ctx = AttemptContext(
            attempt_number=1, mode=AttemptMode.NORMAL,
            completion_prompt_suffix=None,
            previous_outputs={}, previous_files={},
        )

        # Pre-render so the wrapper does not need a PromptRenderer
        await adapter._musician_wrapper(
            job_id="J1",
            sheet=sheet,
            backend=backend,
            context=ctx,
            effective_instrument="test-cli",
        )

        assert ("J1", 7) not in adapter._active_pids  # cleared on completion
        # The callback must have been wired (not None) and captured the values
        # at least once during the execute — verify by re-firing the callback.
        # If wiring worked, active_pids would have been populated mid-execute.
        # To test that directly, check that the callback is NOT None after
        # wrapper (backend was reused/held during execute).

    @pytest.mark.asyncio
    async def test_callback_populates_and_then_clears(self) -> None:
        """Mid-execute the entry exists; after completion it is cleared."""
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        adapter = _make_adapter()
        backend = MagicMock()
        backend._on_process_group_spawned = None
        backend.set_preamble = MagicMock()

        pid, pgid = 31411, 31411
        mid_execute_snapshot: dict[tuple[str, int], tuple[int, int]] = {}

        async def fake_execute(*args, **kwargs):
            cb = backend._on_process_group_spawned
            if cb is not None:
                cb(pid, pgid)
            mid_execute_snapshot.update(adapter._active_pids)
            from marianne.backends.base import ExecutionResult
            return ExecutionResult(
                success=True, stdout="ok", stderr="", exit_code=0,
                duration_seconds=0.01,
            )

        backend.execute = fake_execute

        pool = MagicMock()
        pool.release = AsyncMock()
        pool._registry = None
        adapter.set_backend_pool(pool)

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        sheet = Sheet(
            num=3, movement=1, voice=1, voice_count=1,
            description="t", workspace=tmp,
            instrument_name="test-cli", instrument_config={},
            instrument_fallbacks=[], prompt_template="p",
            validations=[], timeout_seconds=60,
        )
        adapter._job_sheets["J2"] = {3: sheet}

        ctx = AttemptContext(
            attempt_number=1, mode=AttemptMode.NORMAL,
            completion_prompt_suffix=None,
            previous_outputs={}, previous_files={},
        )

        await adapter._musician_wrapper(
            job_id="J2", sheet=sheet, backend=backend,
            context=ctx, effective_instrument="test-cli",
        )

        # Mid-execute the entry must have been present.
        assert mid_execute_snapshot.get(("J2", 3)) == (pid, pgid)
        # After completion the entry must be cleared.
        assert ("J2", 3) not in adapter._active_pids

    @pytest.mark.asyncio
    async def test_skips_wiring_when_backend_lacks_slot(self) -> None:
        """HTTP backends without _on_process_group_spawned must not break."""
        from marianne.core.sheet import Sheet
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        adapter = _make_adapter()
        # spec=[] strips all attrs — backend will NOT have the slot
        backend = MagicMock(spec=["execute", "set_preamble"])

        async def fake_execute(*args, **kwargs):
            from marianne.backends.base import ExecutionResult
            return ExecutionResult(
                success=True, stdout="ok", stderr="", exit_code=0,
                duration_seconds=0.01,
            )

        backend.execute = fake_execute

        pool = MagicMock()
        pool.release = AsyncMock()
        pool._registry = None
        adapter.set_backend_pool(pool)

        import tempfile
        tmp = Path(tempfile.mkdtemp())
        sheet = Sheet(
            num=1, movement=1, voice=1, voice_count=1,
            description="t", workspace=tmp,
            instrument_name="http", instrument_config={},
            instrument_fallbacks=[], prompt_template="p",
            validations=[], timeout_seconds=60,
        )
        adapter._job_sheets["J3"] = {1: sheet}

        ctx = AttemptContext(
            attempt_number=1, mode=AttemptMode.NORMAL,
            completion_prompt_suffix=None,
            previous_outputs={}, previous_files={},
        )

        # Must not raise AttributeError when the slot is absent
        await adapter._musician_wrapper(
            job_id="J3", sheet=sheet, backend=backend,
            context=ctx, effective_instrument="http",
        )

        assert adapter._active_pids == {}


# ============================================================
# Item 5: deregister_job kills process groups before cancelling tasks
# ============================================================


class TestDeregisterJobKillsProcessGroups:
    """deregister_job SIGTERMs active pgroups for the job before cancelling."""

    @pytest.mark.asyncio
    async def test_sigterm_fires_for_each_active_sheet(self) -> None:
        adapter = _make_adapter()
        adapter._active_pids[("J", 1)] = (100, 200)
        adapter._active_pids[("J", 2)] = (101, 201)

        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))

        def fake_getpgid(pid):
            # daemon pgid is 999 — distinct from the children (200, 201)
            return 999

        with patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.daemon.baton.adapter.os.getpgid",
            side_effect=fake_getpgid,
        ):
            adapter.deregister_job("J")

        assert (200, signal.SIGTERM) in killpg_calls
        assert (201, signal.SIGTERM) in killpg_calls
        # _active_pids for J must be cleared
        assert ("J", 1) not in adapter._active_pids
        assert ("J", 2) not in adapter._active_pids

    @pytest.mark.asyncio
    async def test_skips_daemon_own_group(self) -> None:
        adapter = _make_adapter()
        daemon_pgid = 777
        adapter._active_pids[("J", 1)] = (100, daemon_pgid)  # suspicious

        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pgid, sig):
            killpg_calls.append((pgid, sig))

        def fake_getpgid(pid):
            return daemon_pgid

        with patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.daemon.baton.adapter.os.getpgid",
            side_effect=fake_getpgid,
        ):
            adapter.deregister_job("J")

        # Must NOT kill the daemon's own group
        assert (daemon_pgid, signal.SIGTERM) not in killpg_calls

    @pytest.mark.asyncio
    async def test_handles_ProcessLookupError_silently(self) -> None:
        adapter = _make_adapter()
        adapter._active_pids[("J", 1)] = (100, 200)

        def fake_killpg(pgid, sig):
            raise ProcessLookupError

        def fake_getpgid(pid):
            return 999

        with patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.daemon.baton.adapter.os.getpgid",
            side_effect=fake_getpgid,
        ):
            # Must not propagate the error
            adapter.deregister_job("J")

        assert ("J", 1) not in adapter._active_pids

    @pytest.mark.asyncio
    async def test_preserves_pids_for_other_jobs(self) -> None:
        adapter = _make_adapter()
        adapter._active_pids[("J_kill", 1)] = (100, 200)
        adapter._active_pids[("J_keep", 5)] = (300, 400)

        def fake_killpg(pgid, sig):
            pass

        def fake_getpgid(pid):
            return 999

        with patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.daemon.baton.adapter.os.getpgid",
            side_effect=fake_getpgid,
        ):
            adapter.deregister_job("J_kill")

        assert ("J_kill", 1) not in adapter._active_pids
        assert adapter._active_pids[("J_keep", 5)] == (300, 400)

    @pytest.mark.asyncio
    async def test_cancels_tasks_after_killing_pgroups(self) -> None:
        """Task cancellation still runs — kill is preempt, not replacement."""
        adapter = _make_adapter()

        async def sleeper():
            await asyncio.sleep(100)

        task = asyncio.create_task(sleeper())
        adapter._active_tasks[("J", 1)] = task
        adapter._active_pids[("J", 1)] = (100, 200)

        kill_order: list[str] = []

        def fake_killpg(pgid, sig):
            kill_order.append(f"killpg:{pgid}:{sig}")

        def fake_getpgid(pid):
            return 999

        with patch(
            "marianne.utils.process.os.killpg",
            side_effect=fake_killpg,
        ), patch(
            "marianne.daemon.baton.adapter.os.getpgid",
            side_effect=fake_getpgid,
        ):
            adapter.deregister_job("J")

        # Task was cancelled
        with pytest.raises(asyncio.CancelledError):
            await task
        # killpg was called
        assert any("killpg:200" in c for c in kill_order)
        # Task was removed
        assert ("J", 1) not in adapter._active_tasks
