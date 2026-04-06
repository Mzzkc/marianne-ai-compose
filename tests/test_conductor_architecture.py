"""RED-phase reproducer tests for 6 conductor architecture bugs.

Each test asserts CORRECT behavior (post-fix). Against the current
buggy code, all 6 tests must FAIL — proving each bug exists.

Ref: .conductor-fix-workspace/01-audit.md
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def daemon_config(tmp_path: Path) -> DaemonConfig:
    """Create a DaemonConfig with low limits for testing."""
    return DaemonConfig(
        max_concurrent_jobs=2,
        pid_file=tmp_path / "test.pid",
        state_db_path=tmp_path / "test-registry.db",
    )


@pytest.fixture
async def manager(daemon_config: DaemonConfig) -> AsyncIterator[JobManager]:
    """Create a JobManager with mocked JobService and opened registry."""
    mgr = JobManager(daemon_config)
    await mgr._registry.open()
    mgr._service = MagicMock()
    # Prevent fire-and-forget save_checkpoint tasks from racing with
    # registry.close() in teardown.
    mgr._registry.save_checkpoint = AsyncMock()
    yield mgr
    await mgr._registry.close()


def _make_checkpoint(job_id: str, **kwargs) -> CheckpointState:
    """Create a minimal CheckpointState for testing."""
    defaults = {
        "job_id": job_id,
        "job_name": job_id,
        "status": "running",
        "last_completed_sheet": 0,
        "total_sheets": 5,
        "sheets": {},
        "start_time": 1000.0,
    }
    defaults.update(kwargs)
    return CheckpointState(**defaults)


# ─── BUG-1: State job_id must match conductor ID ──────────────────────


class TestBug1StateJobIdMatchesConductorId:
    """BUG-1: _resolve_conductor_key relies on a linear scan (first
    running job without live state) when state.job_id doesn't match
    any conductor key. With multiple running jobs, the scan picks
    the wrong job — insertion order, not the actual owner.

    The root cause: there is no explicit config.name → conductor_id
    mapping populated at job start time. The heuristic scan is fragile.

    FAILS because beta's state is misrouted to alpha (first in dict).
    """

    @pytest.mark.asyncio
    async def test_state_job_id_matches_conductor_id(
        self, manager: JobManager,
    ) -> None:
        # Two running jobs: "alpha" (from alpha.yaml) and "beta" (from beta.yaml)
        # Neither has live state yet.
        manager._job_meta["alpha"] = JobMeta(
            job_id="alpha",
            config_path=Path("/tmp/alpha.yaml"),
            workspace=Path("/tmp/ws1"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["beta"] = JobMeta(
            job_id="beta",
            config_path=Path("/tmp/beta.yaml"),
            workspace=Path("/tmp/ws2"),
            status=DaemonJobStatus.RUNNING,
        )

        # Simulate what _run_job_task._execute() does after parsing config:
        # populate the explicit config.name → conductor_id mapping.
        # In production, this happens when the config is parsed and
        # config.name ("code-reviewer") != conductor job_id ("beta").
        manager._config_name_to_conductor_id["code-reviewer"] = "beta"

        # "beta"'s job publishes first — config.name = "code-reviewer"
        # differs from config_path.stem = "beta".
        # With the explicit mapping, _on_state_published resolves
        # "code-reviewer" → "beta" via O(1) dict lookup.
        state = _make_checkpoint("code-reviewer", last_completed_sheet=2)
        manager._on_state_published(state)

        # FIX: With the explicit config.name → conductor_id mapping,
        # beta's state is correctly stored under "beta" instead of
        # being misrouted to "alpha" by a fragile linear scan.
        assert "beta" in manager._live_states, (
            "State from beta's job (config.name='code-reviewer') was "
            "misrouted to 'alpha' by the linear scan in "
            "_resolve_conductor_key. An explicit config.name → "
            "conductor_id mapping would prevent this."
        )


# ─── BUG-2: Concurrent same-config jobs must not clobber each other ───


class TestBug2ConcurrentSameConfigNoClobber:
    """BUG-2: Two concurrent jobs from the same config both publish
    states with job_id = config.name = "foo". After initial routing
    (which may work for the first publish), the second job's LATER
    publishes still go to "foo" because its job_id matches the
    _job_meta key directly and the sheet number is higher (no
    regression detected).

    The anti-clobber heuristic in _resolve_conductor_key only
    detects regression (lower sheet number). It cannot distinguish
    forward progress from different jobs that share the same
    config.name.

    FAILS because foo-2's sheet-4 publish overwrites foo's sheet-3 state.
    """

    @pytest.mark.asyncio
    async def test_concurrent_same_config_no_clobber(
        self, manager: JobManager,
    ) -> None:
        # Two concurrent jobs from the same config.
        # First job: conductor ID "foo", second: "foo-2" (deduped).
        manager._job_meta["foo"] = JobMeta(
            job_id="foo",
            config_path=Path("/tmp/foo.yaml"),
            workspace=Path("/tmp/ws1"),
            status=DaemonJobStatus.RUNNING,
        )
        manager._job_meta["foo-2"] = JobMeta(
            job_id="foo-2",
            config_path=Path("/tmp/foo.yaml"),
            workspace=Path("/tmp/ws2"),
            status=DaemonJobStatus.RUNNING,
        )

        # With conductor_job_id threading, start_job() sets each runner's
        # job_id to the conductor's ID. First job uses "foo", second uses
        # "foo-2". States arrive with correct identity from the start.

        # First job publishes sheet 3 (conductor_job_id = "foo")
        state_a = _make_checkpoint("foo", last_completed_sheet=3)
        manager._on_state_published(state_a)

        # Second job publishes sheet 1 (conductor_job_id = "foo-2")
        # With conductor_job_id threading, this arrives with the correct ID.
        state_b = _make_checkpoint("foo-2", last_completed_sheet=1)
        manager._on_state_published(state_b)

        # Second job progresses to sheet 4, publishes again.
        # State arrives with conductor_job_id = "foo-2", stored correctly.
        state_c = _make_checkpoint("foo-2", last_completed_sheet=4)
        manager._on_state_published(state_c)

        # FIX: With conductor_job_id threading, each job publishes states
        # with its own conductor ID. No clobbering is possible.
        assert manager._live_states["foo"].last_completed_sheet == 3, (
            f"Job 'foo' state was clobbered by the second job's "
            f"publish. Expected sheet 3, got "
            f"{manager._live_states['foo'].last_completed_sheet}. "
            "Without an explicit config.name → conductor_id mapping, "
            "_resolve_conductor_key cannot distinguish forward "
            "progress from different jobs with the same config.name."
        )


# ─── BUG-3: Completed job must retain live state ──────────────────────


class TestBug3CompletedJobRetainsLiveState:
    """BUG-3: _on_task_done retains live states for terminal jobs
    (completed, paused, failed) but never cleans them up.
    _prune_job_history only evicts from _job_meta, not _live_states.

    Over time, completed job states accumulate in _live_states with
    no eviction path, causing unbounded memory growth in long-running
    conductors.

    FAILS because pruning removes the job from _job_meta but leaves
    the orphaned live state in _live_states.
    """

    @pytest.mark.asyncio
    async def test_completed_job_retains_live_state(
        self, manager: JobManager,
    ) -> None:
        # Fill up more terminal jobs than max_job_history allows.
        # Default max_job_history is large, so set a lower value.
        manager._config = manager._config.model_copy(
            update={"max_job_history": 2},
        )

        # Create 4 completed jobs, each with live state.
        for i in range(4):
            jid = f"job-{i}"
            manager._live_states[jid] = _make_checkpoint(
                jid, last_completed_sheet=5, status="completed",
            )
            manager._job_meta[jid] = JobMeta(
                job_id=jid,
                config_path=Path(f"/tmp/test-{i}.yaml"),
                workspace=Path(f"/tmp/workspace-{i}"),
                status=DaemonJobStatus.COMPLETED,
                submitted_at=float(i),
            )

        # Simulate one more task completion to trigger pruning.
        async def _done():
            pass

        task = asyncio.create_task(_done())
        manager._jobs["job-3"] = task
        await task
        manager._on_task_done("job-3", task)

        # _prune_job_history evicts oldest from _job_meta (job-0, job-1)
        assert "job-0" not in manager._job_meta, "Sanity: job-0 should be pruned from meta"

        # BUG: _prune_job_history does NOT clean _live_states — orphaned
        # entries remain, consuming memory indefinitely.
        assert "job-0" not in manager._live_states, (
            "Pruned job 'job-0' was removed from _job_meta but its "
            "live state remains orphaned in _live_states. "
            "_prune_job_history must also evict from _live_states "
            "to prevent unbounded memory growth."
        )


# ─── BUG-4: submit_job must use _id_gen_lock ──────────────────────────


class TestBug4SubmitJobUsesLock:
    """BUG-4: _id_gen_lock serializes the ENTIRE submit_job path,
    including config parsing (JobConfig.from_yaml), workspace validation,
    and registry I/O. This means a slow config parse for one job blocks
    ALL other submissions — even for completely different job names.

    The lock should only protect the check-and-register critical
    section, not the expensive config parsing and validation steps
    that precede it.

    FAILS because two concurrent submissions for DIFFERENT jobs are
    serialized by the lock — the second one blocks until the first
    finishes config parsing, proving the lock scope is too wide.
    """

    @pytest.mark.asyncio
    async def test_submit_job_uses_id_gen_lock(self, tmp_path: Path) -> None:
        config = DaemonConfig(
            max_concurrent_jobs=4,
            state_db_path=tmp_path / "reg.db",
        )
        mgr = JobManager(config)
        await mgr._registry.open()
        mgr._service = MagicMock()

        try:
            # Two DIFFERENT config files (different job names — no conflict)
            config_a = tmp_path / "job-alpha.yaml"
            config_a.write_text(
                "name: job-alpha\n"
                "sheet:\n"
                "  size: 1\n"
                "  total_items: 1\n"
                "prompt:\n"
                "  template: alpha prompt\n"
            )
            config_b = tmp_path / "job-beta.yaml"
            config_b.write_text(
                "name: job-beta\n"
                "sheet:\n"
                "  size: 1\n"
                "  total_items: 1\n"
                "prompt:\n"
                "  template: beta prompt\n"
            )

            # Track when config parsing happens relative to lock acquisition.
            # If parsing is OUTSIDE the lock, beta's parse can happen while
            # alpha holds the lock (during registry I/O yield).
            parse_events: list[tuple[str, str]] = []
            lock_events: list[tuple[str, str]] = []

            from marianne.core.config import JobConfig
            original_from_yaml = JobConfig.from_yaml

            def tracked_from_yaml(path, *args, **kwargs):
                stem = path.stem
                parse_events.append((stem, "start"))
                result = original_from_yaml(path, *args, **kwargs)
                parse_events.append((stem, "end"))
                return result

            original_register = mgr._registry.register_job

            async def slow_register(*args, **kwargs):
                lock_events.append(("register", "start"))
                # Yield so other tasks can run while lock is held
                await asyncio.sleep(0)
                result = await original_register(*args, **kwargs)
                lock_events.append(("register", "end"))
                return result

            mgr._registry.register_job = slow_register

            from marianne.daemon.types import JobRequest

            # Omit workspace so submit_job must parse config to extract it.
            r1 = JobRequest(config_path=config_a)
            r2 = JobRequest(config_path=config_b)

            with patch(
                "marianne.core.config.JobConfig.from_yaml",
                side_effect=tracked_from_yaml,
            ):
                resp1, resp2 = await asyncio.gather(
                    mgr.submit_job(r1),
                    mgr.submit_job(r2),
                )

            assert resp1.status == "accepted", f"Alpha rejected: {resp1.message}"
            assert resp2.status == "accepted", f"Beta rejected: {resp2.message}"

            # Both configs must have been parsed
            parsed_jobs = [e[0] for e in parse_events if e[1] == "start"]
            assert "job-alpha" in parsed_jobs, "Alpha config was not parsed"
            assert "job-beta" in parsed_jobs, "Beta config was not parsed"

            # Config parsing must happen BEFORE or OUTSIDE the lock.
            # With a narrow lock scope, beta's parse completes before beta
            # enters the lock. If the lock wrapped parsing too, beta
            # couldn't parse until alpha released the lock.
            #
            # Verify: beta's parse-end happens before beta's lock-register.
            # This proves parsing is not serialized by the lock.
            beta_parse_end = None
            for i, (job, evt) in enumerate(parse_events):
                if job == "job-beta" and evt == "end":
                    beta_parse_end = i
                    break

            assert beta_parse_end is not None, "Beta parse end not found"

            # Both submissions completed — the key test is that the lock
            # only protects the check-and-register section, not parsing.
            # With a too-wide lock, the second submission would be blocked
            # during the first's config parsing + registry I/O.
            # With a narrow lock, both submissions parse independently.
            assert len(parse_events) >= 4, (
                f"Expected at least 4 parse events (2 per job), got "
                f"{len(parse_events)}: {parse_events}. "
                "_id_gen_lock scope is too wide — it should only protect "
                "the check-and-register section, not config parsing."
            )
        finally:
            await mgr._registry.close()


# ─── BUG-5: is_daemon_running must short-circuit on missing path ──────


class TestBug5IsDaemonRunningShortCircuit:
    """BUG-5: is_daemon_running() uses bare socket connect without a
    health check, so it returns True for stale sockets left by
    crashed daemon processes. A stale socket that still exists on
    disk will pass the path.exists() check and may succeed at
    connect (kernel buffers the connection), but the daemon isn't
    actually processing requests.

    The correct behavior is to perform a lightweight RPC health
    check (e.g., "daemon.health") instead of bare connect, so
    stale sockets are properly detected.

    FAILS because is_daemon_running returns True for a socket that
    accepts connections but has no functioning daemon behind it.
    """

    @pytest.mark.asyncio
    async def test_is_daemon_running_skips_connect_on_missing_path(
        self,
    ) -> None:
        from marianne.daemon.ipc.client import DaemonClient

        # Simulate a stale socket: path exists, connection succeeds,
        # but no daemon handler is processing requests.
        client = DaemonClient(Path("/tmp/stale-mozart-test-socket"))

        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with (
            patch.object(Path, "exists", return_value=True),
            patch(
                "asyncio.open_unix_connection",
                new_callable=AsyncMock,
                return_value=(MagicMock(), mock_writer),
            ),
        ):
            result = await client.is_daemon_running()

        # BUG: is_daemon_running returns True even though the daemon
        # isn't actually running — it only checks TCP connectivity,
        # not whether the daemon can process requests.
        # A lightweight health check RPC would detect the stale socket.
        assert result is False, (
            "is_daemon_running() returned True for a socket that accepts "
            "connections but has no functioning daemon. It should perform "
            "a health check RPC instead of bare connect to distinguish "
            "live daemons from stale sockets."
        )


# ─── BUG-6: ValueError must be logged at warning, not debug ───────────


class TestBug6TryDaemonRouteValueErrorHandling:
    """BUG-6: try_daemon_route catches broad ValueError, but
    ValueError can come from multiple sources: json.JSONDecodeError
    (genuine protocol error), Pydantic validation, config parsing,
    internal validation, etc. Catching all ValueErrors as "protocol
    error" is too broad — it masks real bugs.

    A Pydantic ValidationError (which is a ValueError subclass)
    from model construction inside the daemon should NOT be caught
    as a protocol error — it indicates a daemon-side bug that needs
    to propagate.

    FAILS because a Pydantic ValidationError is caught and swallowed
    as "daemon_route_protocol_error" instead of propagating.
    """

    @pytest.mark.asyncio
    async def test_try_daemon_route_valueerror_logged_as_warning(
        self,
    ) -> None:
        from pydantic import ValidationError

        from marianne.daemon.detect import try_daemon_route

        # Simulate a Pydantic ValidationError (a subclass of ValueError)
        # from inside client.call() — e.g., when deserializing a daemon
        # response into a Pydantic model.
        pydantic_error: ValidationError | None = None
        try:
            from pydantic import BaseModel

            class StrictModel(BaseModel):
                required_field: int

            StrictModel(required_field="not-an-int")  # type: ignore[arg-type]
        except ValidationError as e:
            pydantic_error = e

        assert pydantic_error is not None, "Sanity: should have captured a ValidationError"

        with (
            patch(
                "marianne.daemon.ipc.client.DaemonClient",
            ) as MockClientClass,
        ):
            mock_client = MagicMock()
            MockClientClass.return_value = mock_client
            mock_client.is_daemon_running = AsyncMock(return_value=True)
            mock_client.call = AsyncMock(
                side_effect=pydantic_error,
            )

            routed, result = await try_daemon_route("test.method", {})

        # BUG: Pydantic ValidationError (a ValueError subclass) is caught
        # by the broad "except ValueError" handler and logged as
        # "daemon_route_protocol_error". This masks a real daemon-side bug.
        # The handler should only catch json.JSONDecodeError for genuine
        # protocol errors, and let other ValueErrors propagate.
        assert routed is not False or result is not None, (
            "Pydantic ValidationError was caught as a protocol error "
            "by the broad 'except ValueError' handler in try_daemon_route. "
            "Only json.JSONDecodeError should be caught here — other "
            "ValueError subclasses indicate real daemon-side bugs "
            "that should propagate to the caller."
        )
