"""#206: baton rate limits mirrored to the daemon RateLimitCoordinator.

Verified state before this change: the coordinator's write path was DEAD —
``JobService`` stored ``rate_limit_callback`` but never invoked it, so
``RateLimitCoordinator.active_limits`` was always empty in production. That
silently starved every coordinator consumer:

- ``BackpressureController`` rate-limit escalation (backpressure.py) never
  fired;
- the submit-time "instrument clears in Xs" warning (manager.py) never
  rendered;
- the (inactive) ``GlobalSheetScheduler`` read path had no data.

Cross-job dispatch backoff itself needs NO new read path: the baton is
shared by all jobs, ``InstrumentState.rate_limited`` is baton-wide, and
``_handle_rate_limit_hit`` already moves every job's sheets on the limited
instrument to WAITING and skips dispatch until ``RateLimitExpired``.

The fix is a write-through mirror: the adapter's run loop reports every
``RateLimitHit`` to an injected reporter (``JobManager._on_rate_limit`` →
``RateLimitCoordinator.report_rate_limit``). The baton stays the single
dispatch authority; the coordinator is the daemon-level observability
mirror. Key space is instrument NAMES end-to-end — the same key
``mzt clear-rate-limits --instrument X`` already passes to both stores.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import RateLimitHit, ShutdownRequested
from marianne.daemon.rate_coordinator import RateLimitCoordinator


def _make_sheet(workspace: Path) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=workspace,
        instrument_name="claude-code",
        prompt_template="test",
        timeout_seconds=600.0,
    )


def _hit(instrument: str = "claude-code", wait: float = 60.0) -> RateLimitHit:
    return RateLimitHit(
        instrument=instrument,
        wait_seconds=wait,
        job_id="j1",
        sheet_num=1,
    )


class _ReporterSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, str, int]] = []

    async def __call__(
        self, instrument: str, wait_seconds: float, job_id: str, sheet_num: int
    ) -> None:
        self.calls.append((instrument, wait_seconds, job_id, sheet_num))


class TestReporterInterception:
    async def test_rate_limit_hit_invokes_reporter(self) -> None:
        spy = _ReporterSpy()
        adapter = BatonAdapter(rate_limit_reporter=spy)

        await adapter._report_rate_limit_cross_job(_hit(wait=42.0))

        assert spy.calls == [("claude-code", 42.0, "j1", 1)]

    async def test_no_reporter_is_noop(self) -> None:
        adapter = BatonAdapter()

        # Must not raise — reporter is optional (backward compat).
        await adapter._report_rate_limit_cross_job(_hit())

    async def test_reporter_exception_is_swallowed(self) -> None:
        async def boom(
            instrument: str, wait_seconds: float, job_id: str, sheet_num: int
        ) -> None:
            raise RuntimeError("coordinator unavailable")

        adapter = BatonAdapter(rate_limit_reporter=boom)

        # Fail-open: a broken mirror must never break the dispatch loop.
        await adapter._report_rate_limit_cross_job(_hit())


class TestRunLoopWiring:
    async def test_run_loop_reports_rate_limit_hit(self, tmp_path: Path) -> None:
        """A RateLimitHit flowing through the real run loop reaches the reporter."""
        spy = _ReporterSpy()
        adapter = BatonAdapter(rate_limit_reporter=spy)
        ws = tmp_path / "ws"
        ws.mkdir()
        adapter.register_job("j1", [_make_sheet(ws)], dependencies={})

        adapter.baton.inbox.put_nowait(_hit(wait=30.0))
        adapter.baton.inbox.put_nowait(ShutdownRequested(graceful=False))

        await asyncio.wait_for(adapter.run(), timeout=10.0)

        assert spy.calls == [("claude-code", 30.0, "j1", 1)]

    async def test_run_loop_survives_raising_reporter(self, tmp_path: Path) -> None:
        """A raising reporter doesn't kill the loop — later events still process."""
        calls: list[str] = []

        async def boom(
            instrument: str, wait_seconds: float, job_id: str, sheet_num: int
        ) -> None:
            calls.append(instrument)
            raise RuntimeError("boom")

        adapter = BatonAdapter(rate_limit_reporter=boom)
        ws = tmp_path / "ws"
        ws.mkdir()
        adapter.register_job("j1", [_make_sheet(ws)], dependencies={})

        adapter.baton.inbox.put_nowait(_hit())
        adapter.baton.inbox.put_nowait(_hit(instrument="gemini-cli"))
        adapter.baton.inbox.put_nowait(ShutdownRequested(graceful=False))

        await asyncio.wait_for(adapter.run(), timeout=10.0)

        # Both events reached the reporter; the loop survived both raises
        # and processed through to shutdown.
        assert calls == ["claude-code", "gemini-cli"]


class TestCoordinatorEndToEnd:
    async def test_hit_populates_coordinator_active_limits(
        self, tmp_path: Path
    ) -> None:
        """Manager-style wiring: RateLimitHit → coordinator.active_limits."""
        coordinator = RateLimitCoordinator()

        async def reporter(
            instrument: str, wait_seconds: float, job_id: str, sheet_num: int
        ) -> None:
            await coordinator.report_rate_limit(
                instrument=instrument,
                wait_seconds=wait_seconds,
                job_id=job_id,
                sheet_num=sheet_num,
            )

        adapter = BatonAdapter(rate_limit_reporter=reporter)
        ws = tmp_path / "ws"
        ws.mkdir()
        adapter.register_job("j1", [_make_sheet(ws)], dependencies={})

        adapter.baton.inbox.put_nowait(_hit(wait=120.0))
        adapter.baton.inbox.put_nowait(ShutdownRequested(graceful=False))
        await asyncio.wait_for(adapter.run(), timeout=10.0)

        active = coordinator.active_limits
        assert "claude-code" in active
        assert 0 < active["claude-code"] <= 120.0
        # The baton's own dispatch-blocking state agrees — one limit, two views.
        assert "claude-code" in adapter.baton.get_rate_limited_instruments()


class TestManagerWiring:
    async def test_manager_injects_reporter_into_adapter(
        self, tmp_path: Path
    ) -> None:
        """JobManager construction threads its coordinator reporter into the baton."""
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.manager import JobManager

        config = DaemonConfig(
            max_concurrent_jobs=1,
            pid_file=tmp_path / "test.pid",
            state_db_path=tmp_path / "test-registry.db",
        )
        mgr = JobManager(config)
        await mgr.start()
        try:
            adapter = mgr._baton_adapter
            assert adapter is not None
            assert adapter._rate_limit_reporter is not None

            # The injected reporter feeds the manager's own coordinator.
            await adapter._rate_limit_reporter("claude-code", 60.0, "j1", 1)
            assert "claude-code" in mgr.rate_coordinator.active_limits
        finally:
            await mgr.shutdown(graceful=False)
