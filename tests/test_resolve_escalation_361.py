"""#361 (resolve): `mzt resolve` CLI + `job.resolve_escalation` IPC.

Ergonomic layer over the shipped marker-file fermata resolution. The
conductor — not the client — writes the decision marker (conductor-only
principle: the CLI never touches workspace paths directly), then enqueues
an immediate ``FermataCheck`` so the EXISTING poll handler consumes it.
One consumption path, restart-safe (the marker survives a crash between
write and consumption), full ``consumed/`` audit trail preserved.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import EscalationResolved, FermataCheck
from marianne.daemon.baton.state import BatonSheetStatus


def _make_sheet(workspace: Path, num: int = 1) -> Sheet:
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=workspace,
        instrument_name="claude-code",
        prompt_template="test",
        timeout_seconds=600.0,
    )


def _fermata_adapter(tmp_path: Path) -> tuple[BatonAdapter, Path]:
    adapter = BatonAdapter()
    ws = tmp_path / "job-ws"
    ws.mkdir(parents=True)
    adapter.register_job("j1", [_make_sheet(ws)], dependencies={})
    state = adapter.baton.get_sheet_state("j1", 1)
    assert state is not None
    state.status = BatonSheetStatus.FERMATA
    # Drain the registration DispatchRetry so inbox assertions are clean.
    while not adapter.baton.inbox.empty():
        adapter.baton.inbox.get_nowait()
    return adapter, ws


class TestResolveFermata:
    def test_writes_marker_and_enqueues_check(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)

        ok, msg = adapter.resolve_fermata("j1", 1, "retry")

        assert ok, msg
        marker = ws / "markers" / "fermata" / "j1" / "sheet-1.retry"
        assert marker.is_file()
        event = adapter.baton.inbox.get_nowait()
        assert isinstance(event, FermataCheck)
        assert (event.job_id, event.sheet_num) == ("j1", 1)

    async def test_end_to_end_resolution_via_existing_handler(
        self, tmp_path: Path
    ) -> None:
        """resolve_fermata + the existing poll handler → EscalationResolved."""
        adapter, _ws = _fermata_adapter(tmp_path)

        ok, _ = adapter.resolve_fermata("j1", 1, "accept")
        assert ok
        check = adapter.baton.inbox.get_nowait()
        assert isinstance(check, FermataCheck)

        await adapter._handle_fermata_check(check)

        resolved: list[EscalationResolved] = []
        while not adapter.baton.inbox.empty():
            e = adapter.baton.inbox.get_nowait()
            if isinstance(e, EscalationResolved):
                resolved.append(e)
        assert len(resolved) == 1
        assert resolved[0].decision == "accept"

    def test_invalid_decision_rejected(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)

        ok, msg = adapter.resolve_fermata("j1", 1, "abandon")

        assert not ok
        assert "decision" in msg.lower()
        assert not (ws / "markers").exists()
        assert adapter.baton.inbox.empty()

    def test_non_fermata_sheet_rejected(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)
        state = adapter.baton.get_sheet_state("j1", 1)
        assert state is not None
        state.status = BatonSheetStatus.IN_PROGRESS

        ok, msg = adapter.resolve_fermata("j1", 1, "retry")

        assert not ok
        assert "fermata" in msg.lower()
        assert not (ws / "markers").exists()

    def test_unknown_job_rejected(self, tmp_path: Path) -> None:
        adapter, _ws = _fermata_adapter(tmp_path)

        ok, msg = adapter.resolve_fermata("nope", 1, "retry")

        assert not ok

    def test_unknown_sheet_rejected(self, tmp_path: Path) -> None:
        adapter, _ws = _fermata_adapter(tmp_path)

        ok, msg = adapter.resolve_fermata("j1", 99, "retry")

        assert not ok


class TestManagerDelegation:
    async def test_resolve_escalation_delegates_to_adapter(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock

        from marianne.daemon.manager import JobManager

        mgr = MagicMock()
        mgr._baton_adapter = MagicMock()
        mgr._baton_adapter.resolve_fermata = MagicMock(
            return_value=(True, "resolution recorded")
        )
        result = await JobManager.resolve_escalation.__get__(mgr)(
            "j1", 1, "skip"
        )

        assert result == {"resolved": True, "message": "resolution recorded"}
        mgr._baton_adapter.resolve_fermata.assert_called_once_with("j1", 1, "skip")
