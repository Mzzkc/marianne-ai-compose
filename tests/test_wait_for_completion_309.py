"""Tests for shutdown-responsive wait_for_completion (#309).

A 4-model thinking-lab review unanimously rejected adding a total timeout
(jobs legitimately run for hours) and instead made the wait shutdown-responsive:
it returns the result on real completion, but raises CancelledError when the
baton adapter is shutting down — rather than returning False, which would
collapse "execution interrupted" into "job failed terminally" and corrupt job
semantics. Per-sheet stuck detection stays the job of StaleCheck (#350).
"""

from __future__ import annotations

import asyncio

import pytest

from marianne.daemon.baton.adapter import BatonAdapter


class TestWaitForCompletionShutdownResponsive:
    async def test_returns_result_on_completion(self) -> None:
        adapter = BatonAdapter()
        adapter._completion_events["j"] = asyncio.Event()
        adapter._completion_results["j"] = True
        adapter._completion_events["j"].set()

        result = await asyncio.wait_for(adapter.wait_for_completion("j"), timeout=1.0)
        assert result is True

    async def test_returns_false_result_on_failed_completion(self) -> None:
        adapter = BatonAdapter()
        adapter._completion_events["j"] = asyncio.Event()
        adapter._completion_results["j"] = False
        adapter._completion_events["j"].set()

        result = await asyncio.wait_for(adapter.wait_for_completion("j"), timeout=1.0)
        assert result is False

    async def test_unknown_job_raises_keyerror(self) -> None:
        adapter = BatonAdapter()
        with pytest.raises(KeyError):
            await adapter.wait_for_completion("ghost")

    async def test_shutdown_raises_cancelled_not_false(self) -> None:
        # A waiter on an in-flight (not-yet-terminal) job must NOT silently
        # return False when the adapter shuts down — that would mark a healthy
        # interrupted job as failed. It raises CancelledError so the manager's
        # existing cancellation path runs.
        adapter = BatonAdapter()
        adapter._completion_events["j"] = asyncio.Event()  # never set

        task = asyncio.create_task(adapter.wait_for_completion("j"))
        await asyncio.sleep(0)  # let it begin waiting
        assert not task.done()

        adapter._shutdown_event.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)

    async def test_completion_wins_over_concurrent_shutdown(self) -> None:
        # If the job genuinely completed, a simultaneously-set shutdown flag must
        # not turn the real result into an interruption.
        adapter = BatonAdapter()
        adapter._completion_events["j"] = asyncio.Event()
        adapter._completion_results["j"] = True
        adapter._completion_events["j"].set()
        adapter._shutdown_event.set()

        result = await asyncio.wait_for(adapter.wait_for_completion("j"), timeout=1.0)
        assert result is True
