"""#184: `mzt pause` must prevent the next dispatch, even when a sheet
completion is queued ahead of the PauseJob.

Root race: ``dispatch_ready`` gates on ``job.paused`` (dispatch.py), but that
flag is set only when the ``PauseJob`` event is *processed* (``_handle_pause_job``).
The loop dispatches after every event, so [SheetAttemptResult, PauseJob] in the
inbox → the completion dispatches the next sheet before the pause is seen.

Fix (4-model lab, 3:1 for B): a synchronous ``BatonCore.request_pause(job_id)``
that sets the dispatch gate (``job.paused`` AND ``job.user_paused``) at the
producer/enqueue site — so ``dispatch_ready`` observes the gate regardless of
event ordering. Single-threaded asyncio ⇒ no interleaving between the
synchronous set and the next dispatch. ``user_paused`` must be set too, or an
escalation-timeout (which clears ``paused`` only ``if not user_paused``) could
unpause a user-paused job before the PauseJob event lands (#326).
"""

from __future__ import annotations

from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import PauseJob
from marianne.daemon.baton.state import SheetExecutionState


def _baton_with_ready_job(job_id: str = "j1") -> BatonCore:
    baton = BatonCore()
    sheets = {1: SheetExecutionState(sheet_num=1, instrument_name="claude-code")}
    baton.register_job(job_id, sheets, {})
    return baton


class TestRequestPause:
    async def test_request_pause_closes_dispatch_gate_synchronously(self) -> None:
        """The whole point of #184: the gate closes BEFORE any PauseJob event is
        processed, so a completion processed first cannot dispatch the next sheet.
        """
        baton = _baton_with_ready_job()
        # Sheet 1 is ready to dispatch before the pause.
        assert len(baton.get_ready_sheets("j1")) == 1

        # Synchronous, producer-side pause — no event processed yet.
        assert baton.request_pause("j1") is True

        # Gate is already closed: dispatch_ready (which calls get_ready_sheets)
        # now sees the job paused, without any PauseJob event having been handled.
        assert baton.is_job_paused("j1") is True
        assert len(baton.get_ready_sheets("j1")) == 0

    async def test_request_pause_sets_user_paused(self) -> None:
        # Must set user_paused too — else an escalation timeout could clear
        # paused (it only respects user_paused) before the event lands (#326).
        baton = _baton_with_ready_job()
        baton.request_pause("j1")
        assert baton._jobs["j1"].user_paused is True

    async def test_request_pause_unknown_job_returns_false(self) -> None:
        baton = BatonCore()
        assert baton.request_pause("nope") is False

    async def test_pausejob_event_after_request_pause_is_idempotent(self) -> None:
        # The PauseJob event still arrives later; processing it must be a no-op
        # re-set (both flags already True), not an error or a state flip.
        baton = _baton_with_ready_job()
        baton.request_pause("j1")
        await baton.handle_event(PauseJob(job_id="j1"))
        assert baton.is_job_paused("j1") is True
        assert baton._jobs["j1"].user_paused is True
        assert len(baton.get_ready_sheets("j1")) == 0
