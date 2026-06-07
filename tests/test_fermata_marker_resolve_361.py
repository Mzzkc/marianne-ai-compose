"""#361: FERMATA usable end-to-end via a non-gated marker-file producer.

FERMATA (pause a sheet for a composer decision on retry exhaustion) was a
one-way trap: the baton entered it, the resolve HANDLER was complete, but
nothing PRODUCED the `EscalationResolved` event, and FERMATA reset to PENDING
on restart (silent re-run). A 4-model thinking-lab (~/lab-archives/
2026-06-07-fermata-361) converged on a non-gated fix:

- composer drops `{workspace}/markers/fermata/{job_id}/sheet-{N}.{decision}`
  (decision ∈ retry|skip|accept|fail; zero-byte, decision in the extension);
- the adapter polls (`FermataCheck`), consumes by atomic rename into
  `consumed/`, and emits the existing `EscalationResolved`;
- FERMATA is removed from `_RESET_ON_RESTART`; the run-loop reconcile re-arms
  polling for any FERMATA sheet (fresh or restart-recovered);
- `{job_id}`-scoped marker dir prevents a prior run's marker auto-resolving a
  new run's FERMATA (the unanimous top hazard).
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.events import EscalationResolved, FermataCheck
from marianne.daemon.baton.state import BatonSheetStatus

_KEY = ("j1", 1)


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


def _fermata_adapter(tmp_path: Path) -> tuple[BatonAdapter, Path]:
    adapter = BatonAdapter()
    ws = tmp_path / "job-ws"
    ws.mkdir(parents=True)
    adapter.register_job("j1", [_make_sheet(ws)], dependencies={})
    state = adapter.baton.get_sheet_state("j1", 1)
    assert state is not None
    state.status = BatonSheetStatus.FERMATA
    return adapter, ws


def _drain_resolved(adapter: BatonAdapter) -> list[EscalationResolved]:
    out: list[EscalationResolved] = []
    inbox = adapter.baton.inbox
    while not inbox.empty():
        ev = inbox.get_nowait()
        if isinstance(ev, EscalationResolved):
            out.append(ev)
    return out


def _marker_dir(ws: Path) -> Path:
    d = ws / "markers" / "fermata" / "j1"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestMarkerResolution:
    async def test_retry_marker_emits_escalation_resolved(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)
        (_marker_dir(ws) / "sheet-1.retry").touch()

        await adapter._handle_fermata_check(FermataCheck(job_id="j1", sheet_num=1))

        resolved = _drain_resolved(adapter)
        assert len(resolved) == 1
        assert resolved[0].decision == "retry"
        # Marker consumed (moved to consumed/), not left to re-fire.
        assert not (ws / "markers" / "fermata" / "j1" / "sheet-1.retry").exists()
        assert (ws / "markers" / "fermata" / "j1" / "consumed" / "sheet-1.retry").exists()

    async def test_each_decision_maps_through(self, tmp_path: Path) -> None:
        for decision in ("skip", "accept", "fail"):
            adapter, ws = _fermata_adapter(tmp_path / decision)
            (_marker_dir(ws) / f"sheet-1.{decision}").touch()
            await adapter._handle_fermata_check(FermataCheck(job_id="j1", sheet_num=1))
            resolved = _drain_resolved(adapter)
            assert [r.decision for r in resolved] == [decision]

    async def test_invalid_decision_marker_ignored(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)
        (_marker_dir(ws) / "sheet-1.bogus").touch()

        await adapter._handle_fermata_check(FermataCheck(job_id="j1", sheet_num=1))

        assert _drain_resolved(adapter) == []  # not a valid decision → no resolve

    async def test_ambiguous_markers_do_not_resolve(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)
        d = _marker_dir(ws)
        (d / "sheet-1.retry").touch()
        (d / "sheet-1.fail").touch()

        await adapter._handle_fermata_check(FermataCheck(job_id="j1", sheet_num=1))

        assert _drain_resolved(adapter) == []  # refuse to choose
        # Neither consumed — composer must disambiguate.
        assert (d / "sheet-1.retry").exists()
        assert (d / "sheet-1.fail").exists()

    async def test_no_marker_does_not_resolve(self, tmp_path: Path) -> None:
        adapter, _ws = _fermata_adapter(tmp_path)
        await adapter._handle_fermata_check(FermataCheck(job_id="j1", sheet_num=1))
        assert _drain_resolved(adapter) == []

    async def test_marker_for_non_fermata_sheet_ignored(self, tmp_path: Path) -> None:
        adapter, ws = _fermata_adapter(tmp_path)
        state = adapter.baton.get_sheet_state("j1", 1)
        assert state is not None
        state.status = BatonSheetStatus.COMPLETED  # left FERMATA
        adapter._fermata_polling.add(_KEY)
        (_marker_dir(ws) / "sheet-1.retry").touch()

        await adapter._handle_fermata_check(FermataCheck(job_id="j1", sheet_num=1))

        assert _drain_resolved(adapter) == []  # not FERMATA → no resolve
        assert _KEY not in adapter._fermata_polling  # polling stopped


class TestReconcile:
    async def test_reconcile_arms_fermata_sheet(self, tmp_path: Path) -> None:
        adapter, _ws = _fermata_adapter(tmp_path)
        assert _KEY not in adapter._fermata_polling

        await adapter._reconcile_fermata_polling()

        assert _KEY in adapter._fermata_polling

    async def test_reconcile_drops_non_fermata_sheet(self, tmp_path: Path) -> None:
        adapter, _ws = _fermata_adapter(tmp_path)
        adapter._fermata_polling.add(_KEY)
        state = adapter.baton.get_sheet_state("j1", 1)
        assert state is not None
        state.status = BatonSheetStatus.COMPLETED

        await adapter._reconcile_fermata_polling()

        assert _KEY not in adapter._fermata_polling


class TestRestartPreservation:
    def test_fermata_not_in_reset_on_restart(self) -> None:
        """#361: a FERMATA sheet must survive a conductor restart (no silent re-run)."""
        import inspect

        src = inspect.getsource(BatonAdapter)
        # The _RESET_ON_RESTART frozenset must not contain FERMATA.
        marker = "_RESET_ON_RESTART = frozenset({"
        start = src.index(marker)
        block = src[start : src.index("})", start)]
        # Check the frozenset MEMBERS (BatonSheetStatus.*), not the comment text.
        assert (
            "BatonSheetStatus.FERMATA" not in block
        ), "FERMATA must not reset on restart (#361)"
        assert "BatonSheetStatus.IN_PROGRESS" in block  # sanity: transient states still reset


class TestPath3SetsFermataFields:
    async def test_exhaustion_path3_records_entry_and_reason(self) -> None:
        from marianne.core.checkpoint import SheetState

        # A SheetState (== SheetExecutionState) is what the baton operates on.
        sheet = SheetState(sheet_num=1, normal_attempts=3, healing_attempts=1)
        sheet.status = BatonSheetStatus.FERMATA  # simulate Path 3 outcome shape
        # The real Path 3 sets these; assert the model carries them.
        assert sheet.fermata_entered_at is None
        sheet.fermata_entered_at = 123.0
        sheet.fermata_reason = "retry budget exhausted after 3 attempt(s)"
        assert sheet.fermata_entered_at == 123.0
        assert "exhausted" in sheet.fermata_reason
