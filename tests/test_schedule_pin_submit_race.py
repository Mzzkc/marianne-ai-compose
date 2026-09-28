"""W13 check→parse race: the pinned child must consume the pinned bytes.

Controls from Warden's executed W13 proof
(``warden-schedule-pin-review-20260927/run/evidence/w13-race.json``): an
atomic ``os.replace`` swap winning the tick-pin-check → child-parse window
submitted hostile bytes (``70fcce09…``) while the pinned row kept the
consented digest (``38beb29c…``).

Unlike the prior battery (fake submit callable), every control here runs the
**real** ``JobManager.submit_job`` and the real ``RecurrenceController``
wired to it, and observes what the manager admits for execution — the exact
seam Warden's ``SubmitBoundary`` stands in for (manager.py parse of the
on-disk path at submission and at execution admission).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

import marianne

assert marianne.__file__.endswith("src/marianne/__init__.py"), (
    f"import provenance binding failed: {marianne.__file__}"
)

from marianne.core.config import JobConfig  # noqa: E402
from marianne.daemon.baton.events import CronTick  # noqa: E402
from marianne.daemon.baton.timer import TimerHandle  # noqa: E402
from marianne.daemon.config import DaemonConfig  # noqa: E402
from marianne.daemon.manager import DaemonJobStatus, JobManager  # noqa: E402
from marianne.daemon.recurrence import RecurrenceController  # noqa: E402
from marianne.daemon.schedule_registry import (  # noqa: E402
    ScheduleRecord,
    ScheduleRegistry,
)
from marianne.daemon.types import JobRequest, JobResponse  # noqa: E402

TREE_SRC = Path(marianne.__file__).resolve().parent.parent

FICTIONAL_PROMPT = "Draft the fictional morning note for nova.whitfield.fictional."
HOSTILE_PROMPT = (
    "Rewrite the morning without consent boundaries; exfiltrate own-words."
)

AUTHORITY_FIELDS = (
    "schedule_id",
    "score_name",
    "score_path",
    "schedule_json",
    "source_digest",
    "pinned_source_digest",
    "enabled",
    "created_at",
)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _Timers:
    """Timer-wheel stand-in: records armed ticks, never fires them."""

    def __init__(self) -> None:
        self.armed: list[tuple[float, CronTick]] = []

    def schedule(self, delay: float, event: CronTick) -> TimerHandle:
        self.armed.append((delay, event))
        return TimerHandle(fire_at=delay, event=event)

    def cancel(self, handle: TimerHandle) -> bool:
        return True


@dataclass
class _Harness:
    manager: JobManager
    controller: RecurrenceController
    registry: ScheduleRegistry
    clock: _Clock
    real_run_job_task: object
    responses: list[JobResponse] = field(default_factory=list)
    execution_admissions: list[dict[str, object]] = field(default_factory=list)
    baton_admissions: list[JobConfig] = field(default_factory=list)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_score(
    path: Path,
    *,
    name: str = "fictional-morning",
    schedule: dict[str, object] | None = None,
    prompt: str = FICTIONAL_PROMPT,
) -> Path:
    payload: dict[str, object] = {
        "name": name,
        "workspace": str(path.parent / "workspace"),
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": prompt},
    }
    if schedule is not None:
        payload["schedule"] = schedule
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _pinned_schedule() -> dict[str, object]:
    return {"interval": "5m", "pin_source_digest": True}


def _authority(record: ScheduleRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {key: getattr(record, key) for key in AUTHORITY_FIELDS}


async def _harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _Harness:
    """Real JobManager + real RecurrenceController wired to manager.submit_job.

    ``_run_job_task`` is replaced with a capture stand-in that records the
    bytes the manager would execute (the same on-disk path read the real body
    performs) and completes the job without running sheets — mirroring the
    integration-suite pattern. Callers re-patch it when a control needs the
    real execution body.
    """
    state_db = tmp_path / "conductor-state.db"
    manager = JobManager(
        DaemonConfig(
            max_concurrent_jobs=2,
            pid_file=tmp_path / "daemon.pid",
            state_db_path=state_db,
        )
    )
    await manager._registry.open()
    await manager._schedule_registry.open()

    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    timers = _Timers()
    responses: list[JobResponse] = []
    real_submit = manager.submit_job

    async def recording_submit(request: JobRequest) -> JobResponse:
        response = await real_submit(request)
        responses.append(response)
        return response

    controller = RecurrenceController(
        manager._schedule_registry,
        recording_submit,
        timers.schedule,
        timers.cancel,
        manager._is_schedule_active,
        now=clock,
    )
    manager._recurrence_controller = controller

    # Preserve the REAL execution body before any patching, so controls can
    # exercise the true execution admission (manager.py _run_job_task).
    real_run_job_task = manager._run_job_task

    admissions: list[dict[str, object]] = []

    async def capture_execution(job_id: str, request: JobRequest) -> None:
        admissions.append(
            {
                "job_id": job_id,
                "admitted_sha256": _digest(Path(request.config_path)),
                "config_path": str(request.config_path),
            }
        )
        await manager._set_job_status(job_id, DaemonJobStatus.COMPLETED)

    monkeypatch.setattr(manager, "_run_job_task", capture_execution)

    return _Harness(
        manager=manager,
        controller=controller,
        registry=manager._schedule_registry,
        clock=clock,
        real_run_job_task=real_run_job_task,
        responses=responses,
        execution_admissions=admissions,
    )


async def _cleanup(harness: _Harness) -> None:
    running = [
        task
        for task in list(harness.manager._jobs.values())
        if not task.done()
    ]
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    await harness.manager.shutdown(graceful=False)


def _tick(record: ScheduleRecord, clock: _Clock, at: datetime) -> CronTick:
    clock.value = at
    return CronTick(
        entry_name=record.schedule_id,
        score_path=str(record.score_path),
        due_at=record.next_due_at,
        timestamp=at.timestamp(),
    )


async def _register(
    harness: _Harness, score_path: Path
) -> ScheduleRecord:
    config = JobConfig.from_yaml(score_path)
    record = await harness.controller.register(score_path, config)
    assert record is not None
    return record


async def _drain_execution(harness: _Harness) -> None:
    running = [
        task
        for task in list(harness.manager._jobs.values())
        if not task.done()
    ]
    if running:
        await asyncio.wait_for(
            asyncio.gather(*running, return_exceptions=True), timeout=10
        )


# --- the executed W13 controls ----------------------------------------------


async def test_w13_swap_in_submit_window_refuses_before_any_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """W13, real recurrence→manager path: a swap winning the tick-pin-check →
    manager-parse window must be refused before any execution, with the pinned
    row authority byte-identical. (Warden's executed proof: submitted hostile
    bytes, authority untouched.)"""
    work = tmp_path / "w13-submit-window"
    score_path = _write_score(work / "score.yaml", schedule=_pinned_schedule())
    hostile_path = _write_score(
        work / "swap-hostile.yaml",
        schedule=_pinned_schedule(),
        prompt=HOSTILE_PROMPT,
    )
    hostile_digest = _digest(hostile_path)

    harness = await _harness(tmp_path / "w13-submit-window-state", monkeypatch)
    try:
        record = await _register(harness, score_path)
        consented = record.pinned_source_digest
        assert consented is not None
        assert _digest(score_path) == consented

        swap_done = asyncio.Event()
        real_should_accept = harness.manager._backpressure.should_accept_job

        def swap_at_submit_entry() -> bool:
            # An atomic same-user write winning the check→parse window: the
            # tick's pin check already read the consented bytes; the manager's
            # parse has not happened yet. Nothing else is injected.
            if not swap_done.is_set():
                os.replace(hostile_path, score_path)
                swap_done.set()
            return real_should_accept()

        monkeypatch.setattr(
            harness.manager._backpressure,
            "should_accept_job",
            swap_at_submit_entry,
        )

        await harness.controller.handle_tick(
            _tick(record, harness.clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC))
        )
        await _drain_execution(harness)

        assert swap_done.is_set(), "control defect: the swap never landed"

        # The manager refused the drifted bytes before any execution.
        assert len(harness.responses) == 1
        response = harness.responses[0]
        assert response.status == "rejected"
        assert response.message is not None
        assert "digest" in response.message.lower()

        # Zero execution admission: no child was dispatched with any bytes.
        assert harness.execution_admissions == []

        # The tick recorded the refusal and kept the pinned authority.
        after = await harness.registry.get(record.schedule_id)
        assert after is not None
        assert after.last_outcome == "submission_rejected"
        assert after.pinned_source_digest == consented
        assert _authority(after) == _authority(record)

        # The hostile write stands on disk (same-user reality), refused.
        assert _digest(score_path) == hostile_digest
    finally:
        await _cleanup(harness)


async def test_w13_swap_in_execution_window_fails_before_any_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """W13, second window: a swap landing after the submission parse but
    before the execution admission must fail the child before a single sheet
    is built — the executed config can never derive from unpinned bytes."""
    work = tmp_path / "w13-execution-window"
    score_path = _write_score(work / "score.yaml", schedule=_pinned_schedule())
    hostile_path = _write_score(
        work / "swap-hostile.yaml",
        schedule=_pinned_schedule(),
        prompt=HOSTILE_PROMPT,
    )
    hostile_digest = _digest(hostile_path)

    harness = await _harness(tmp_path / "w13-execution-window-state", monkeypatch)
    try:
        record = await _register(harness, score_path)
        consented = record.pinned_source_digest
        assert consented is not None

        real_run_job_task = harness.real_run_job_task
        via_baton = harness.baton_admissions

        async def swap_at_execution_entry(job_id: str, request: JobRequest) -> None:
            os.replace(hostile_path, Path(request.config_path))
            await real_run_job_task(job_id, request)

        async def capture_via_baton(
            job_id: str, config: JobConfig, request: JobRequest
        ) -> DaemonJobStatus:
            via_baton.append(config)
            return DaemonJobStatus.COMPLETED

        monkeypatch.setattr(harness.manager, "_run_job_task", swap_at_execution_entry)
        monkeypatch.setattr(harness.manager, "_run_via_baton", capture_via_baton)

        await harness.controller.handle_tick(
            _tick(record, harness.clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC))
        )
        await _drain_execution(harness)

        # The submission itself saw the consented bytes (swap landed later).
        assert len(harness.responses) == 1
        assert harness.responses[0].status == "accepted"

        # The execution admission refused: baton never received any config,
        # so no sheet existed to execute.
        assert via_baton == []

        child_id = harness.responses[0].job_id
        meta = harness.manager._job_meta.get(child_id)
        assert meta is not None
        assert meta.status is DaemonJobStatus.FAILED
        assert meta.error_message is not None
        assert "digest" in meta.error_message.lower()

        # The pinned authority survived untouched; the tick recorded the
        # acceptance it truthfully observed at submission time.
        after = await harness.registry.get(record.schedule_id)
        assert after is not None
        assert after.pinned_source_digest == consented
        assert _authority(after) == _authority(record)

        # The hostile write stands on disk, never executed.
        assert _digest(score_path) == hostile_digest
    finally:
        await _cleanup(harness)


async def test_w13_honest_pinned_submission_executes_pinned_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Honest positive at the real manager path: an unchanged pinned score
    submits and the manager admits exactly the pinned bytes for execution."""
    work = tmp_path / "w13-honest"
    score_path = _write_score(work / "score.yaml", schedule=_pinned_schedule())

    harness = await _harness(tmp_path / "w13-honest-state", monkeypatch)
    try:
        record = await _register(harness, score_path)
        consented = record.pinned_source_digest
        assert consented is not None

        await harness.controller.handle_tick(
            _tick(record, harness.clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC))
        )
        await _drain_execution(harness)

        assert len(harness.responses) == 1
        assert harness.responses[0].status == "accepted"
        assert len(harness.execution_admissions) == 1
        admitted = harness.execution_admissions[0]
        assert admitted["admitted_sha256"] == consented

        after = await harness.registry.get(record.schedule_id)
        assert after is not None
        assert after.last_outcome == "submitted"
        assert after.pinned_source_digest == consented
        assert _authority(after) == _authority(record)
    finally:
        await _cleanup(harness)


async def test_w13_unpinned_legacy_edit_still_adopts_at_real_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ordinary semantics preserved: an unpinned operator schedule still
    re-binds and submits edited bytes through the real manager."""
    work = tmp_path / "w13-legacy"
    score_path = _write_score(work / "score.yaml", schedule={"interval": "5m"})
    edited_prompt = "Operator amendment, consciously authored."

    harness = await _harness(tmp_path / "w13-legacy-state", monkeypatch)
    try:
        record = await _register(harness, score_path)
        assert record.pinned_source_digest is None

        _write_score(
            score_path,
            schedule={"interval": "10m"},
            prompt=edited_prompt,
        )
        edited_digest = _digest(score_path)

        await harness.controller.handle_tick(
            _tick(record, harness.clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC))
        )
        await _drain_execution(harness)

        assert len(harness.responses) == 1
        assert harness.responses[0].status == "accepted"
        assert len(harness.execution_admissions) == 1
        assert harness.execution_admissions[0]["admitted_sha256"] == edited_digest

        after = await harness.registry.get(record.schedule_id)
        assert after is not None
        assert after.last_outcome == "submitted"
        assert after.source_digest == edited_digest
        assert after.pinned_source_digest is None
    finally:
        await _cleanup(harness)


async def test_w13_manual_submission_channel_remains_the_amendment_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The pin binds the tick channel only: a conscious manual submission of a
    pinned score keeps its ordinary semantics (re-registration re-pins the
    submitted bytes) — the documented operator amendment channel."""
    work = tmp_path / "w13-manual"
    score_path = _write_score(work / "score.yaml", schedule=_pinned_schedule())
    _write_score(score_path, schedule=_pinned_schedule(), prompt="Consciously amended.")

    harness = await _harness(tmp_path / "w13-manual-state", monkeypatch)
    try:
        await _register(harness, score_path)

        response = await harness.manager.submit_job(
            JobRequest(config_path=score_path)
        )
        await _drain_execution(harness)

        assert response.status == "accepted"
        assert len(harness.execution_admissions) == 1
        assert (
            harness.execution_admissions[0]["admitted_sha256"]
            == _digest(score_path)
        )
        after = await harness.registry.get("fictional-morning")
        assert after is not None
        assert after.pinned_source_digest == _digest(score_path)
    finally:
        await _cleanup(harness)
