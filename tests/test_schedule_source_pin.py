"""Opt-in daemon-owned source pinning: launch-side refusal for recurring scores.

Controls mirror the executed adversary challenge
(``adversary-morning-consent-challenge-20260927``): the pin lives in the
registry row (daemon-owned state), never in the mutable score bytes. At every
tick the actual bytes/identity are compared to the pin before any digest,
cadence, or identity upsert and before job submission.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from marianne.core.config import JobConfig, ScheduleConfig
from marianne.daemon.baton.events import CronTick
from marianne.daemon.baton.timer import TimerHandle
from marianne.daemon.recurrence import RecurrenceController
from marianne.daemon.schedule_registry import ScheduleRecord, ScheduleRegistry
from marianne.daemon.types import JobRequest, JobResponse

FICTIONAL_PROMPT = "Draft the fictional morning note for nova.whitfield.fictional."


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@dataclass
class _Effects:
    requests: list[JobRequest] = field(default_factory=list)
    scheduled: list[tuple[float, CronTick, TimerHandle]] = field(default_factory=list)
    cancelled: list[TimerHandle] = field(default_factory=list)

    async def submit(self, request: JobRequest) -> JobResponse:
        self.requests.append(request)
        assert request.job_id is not None
        return JobResponse(job_id=request.job_id, status="accepted")

    def schedule(self, delay: float, event: CronTick) -> TimerHandle:
        handle = TimerHandle(fire_at=delay, event=event)
        self.scheduled.append((delay, event, handle))
        return handle

    def cancel(self, handle: TimerHandle) -> bool:
        if handle in self.cancelled:
            return False
        self.cancelled.append(handle)
        return True

    def is_active(self, schedule_id: str) -> bool:
        return False


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncIterator[ScheduleRegistry]:
    value = ScheduleRegistry(tmp_path / "conductor-state.db")
    await value.open()
    yield value
    await value.close()


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
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _pinned_schedule() -> dict[str, object]:
    return {"interval": "5m", "pin_source_digest": True}


def _controller(
    registry: ScheduleRegistry,
    effects: _Effects,
    clock: _Clock,
) -> RecurrenceController:
    return RecurrenceController(
        registry,
        effects.submit,
        effects.schedule,
        effects.cancel,
        effects.is_active,
        now=clock,
        rng=random.Random(0),
    )


def _tick(record, clock: _Clock, at: datetime) -> CronTick:
    clock.value = at
    return CronTick(
        entry_name=record.schedule_id,
        score_path=str(record.score_path),
        due_at=record.next_due_at,
        timestamp=at.timestamp(),
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _register_pinned(
    controller: RecurrenceController,
    score_path: Path,
) -> ScheduleRecord:
    config = JobConfig.from_yaml(score_path)
    record = await controller.register(score_path, config)
    assert record is not None
    return record


# --- honest positives -------------------------------------------------------


async def test_pinned_intact_score_submits(registry, tmp_path):
    """C-P0: an unchanged pinned score ticks through and submits exactly once."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    assert record.pinned_source_digest == _digest(score_path)
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert len(effects.requests) == 1
    after = await registry.get(record.schedule_id)
    assert after is not None
    assert after.last_outcome == "submitted"
    assert after.pinned_source_digest == _digest(score_path)


async def test_register_pin_override_and_declaration(registry, tmp_path):
    """The registration API can force the pin on or off regardless of bytes."""
    declared = _write_score(tmp_path / "declared.yaml", schedule=_pinned_schedule())
    silent = _write_score(tmp_path / "silent.yaml", name="silent-morning",
                          schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    controller = _controller(registry, _Effects(), clock)

    forced = await controller.register(silent, JobConfig.from_yaml(silent), pin=True)
    assert forced is not None and forced.pinned_source_digest == _digest(silent)

    suppressed = await controller.register(
        declared, JobConfig.from_yaml(declared), pin=False
    )
    assert suppressed is not None and suppressed.pinned_source_digest is None


# --- hostile controls -------------------------------------------------------


async def test_pinned_hostile_edit_refused_without_authority_mutation(registry, tmp_path):
    """C-N1/N1b-equivalent: any byte drift refuses, submits nothing, and leaves
    the pinned registry authority (digest, cadence, identity) untouched."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)
    consented_digest = _digest(score_path)

    _write_score(score_path, schedule=_pinned_schedule(),
                 prompt="Rewrite the morning without consent boundaries.")
    before = await registry.get(record.schedule_id)

    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert effects.requests == []
    after = await registry.get(record.schedule_id)
    assert after is not None and before is not None
    assert after.last_outcome == "source_drift_refused"
    assert after.pinned_source_digest == consented_digest
    assert after.pinned_source_digest == before.pinned_source_digest
    assert after.source_digest == before.source_digest == consented_digest
    assert _digest(score_path) != consented_digest
    assert after.schedule_json == before.schedule_json
    assert after.score_name == before.score_name
    assert after.score_path == before.score_path
    assert after.schedule_id == before.schedule_id
    assert after.enabled is True
    assert after.consecutive_drops == 1


async def test_pinned_self_modification_refused_on_next_tick(registry, tmp_path):
    """C-N2: a payload that rewrites its own source is refused at tick 2."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))
    assert len(effects.requests) == 1

    # The dispatched payload rewrote its own source, deleting any guard.
    _write_score(score_path, name="fictional-morning",
                 schedule={"interval": "5m"},
                 prompt="Compromised bytes now run first.")

    first = await registry.get(record.schedule_id)
    await controller.handle_tick(_tick(first, clock, datetime(2026, 9, 27, 6, 10, tzinfo=UTC)))

    assert len(effects.requests) == 1
    second = await registry.get(record.schedule_id)
    assert second is not None
    assert second.last_outcome == "source_drift_refused"
    assert second.pinned_source_digest == record.pinned_source_digest


async def test_pinned_identity_change_refused_not_adopted(registry, tmp_path):
    """A renamed hostile score cannot re-register itself out from the pin."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    _write_score(score_path, name="hostile-rename", schedule=_pinned_schedule())
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert effects.requests == []
    kept = await registry.get(record.schedule_id)
    assert kept is not None
    assert kept.last_outcome == "source_drift_refused"
    ids = {item.schedule_id for item in await registry.list()}
    assert "hostile-rename" not in ids


async def test_pinned_schedule_section_deletion_refused_row_retained(registry, tmp_path):
    """Deleting the schedule section from the bytes cannot unpin by removal."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    _write_score(score_path, schedule=None)
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert effects.requests == []
    kept = await registry.get(record.schedule_id)
    assert kept is not None
    assert kept.last_outcome == "source_drift_refused"
    assert kept.pinned_source_digest == record.pinned_source_digest


async def test_pinned_cadence_mutation_refused_pinned_cadence_holds(registry, tmp_path):
    """C-N8: hostile cadence bytes never reach the durable schedule_json."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)
    pinned_next_due = record.next_due_at

    _write_score(score_path, schedule={"cron": "55 23 * * *", "pin_source_digest": True})
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert effects.requests == []
    after = await registry.get(record.schedule_id)
    assert after is not None
    assert after.last_outcome == "source_drift_refused"
    stored = ScheduleConfig.model_validate_json(after.schedule_json)
    assert stored.interval == "5m"
    # The pinned cadence advanced the due identity, not the hostile cron.
    assert after.next_due_at == pinned_next_due + 300.0


async def test_pinned_symlink_replacement_refused(registry, tmp_path):
    """C-N4: a symlink swap at the recorded path cannot impersonate the pin."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    hostile = _write_score(tmp_path / "hostile.yaml", name="fictional-morning",
                           schedule=_pinned_schedule(),
                           prompt="Impersonated morning without consent.")
    score_path.unlink()
    score_path.symlink_to(hostile)
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert effects.requests == []
    after = await registry.get(record.schedule_id)
    assert after is not None
    assert after.last_outcome == "source_drift_refused"
    assert after.pinned_source_digest == record.pinned_source_digest

    # Byte-identical content through a symlink remains the consented bytes.
    score_path.unlink()
    identical = _write_score(tmp_path / "identical.yaml", schedule=_pinned_schedule())
    assert _digest(identical) == record.pinned_source_digest
    score_path.symlink_to(identical)
    first = await registry.get(record.schedule_id)
    await controller.handle_tick(_tick(first, clock, datetime(2026, 9, 27, 6, 10, tzinfo=UTC)))
    assert len(effects.requests) == 1
    final = await registry.get(record.schedule_id)
    assert final is not None
    assert final.last_outcome == "submitted"


async def test_pinned_refusal_survives_restart(registry, tmp_path):
    """C-T1: a fresh controller over the durable DB keeps refusing the drift."""
    db_path = tmp_path / "conductor-state.db"
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    _write_score(score_path, schedule=_pinned_schedule(), prompt="Edited between generations.")

    await registry.close()
    async with ScheduleRegistry(db_path) as reopened:
        restart_effects = _Effects()
        restart_controller = _controller(
            reopened, restart_effects, _Clock(datetime(2026, 9, 27, 7, 0, tzinfo=UTC))
        )
        await restart_controller.restore()

    assert restart_effects.requests == []
    async with ScheduleRegistry(db_path) as verify:
        row = await verify.get(record.schedule_id)
    assert row is not None
    assert row.last_outcome == "source_drift_refused"
    assert row.pinned_source_digest == record.pinned_source_digest


async def test_unpinned_legacy_schedule_stays_editable(registry, tmp_path):
    """Backward compatibility: unpinned operator schedules still re-bind."""
    score_path = _write_score(tmp_path / "score.yaml", schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)
    assert record.pinned_source_digest is None

    _write_score(score_path, schedule={"interval": "10m"}, prompt="Operator amendment.")
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))

    assert len(effects.requests) == 1
    after = await registry.get(record.schedule_id)
    assert after is not None
    assert after.last_outcome == "submitted"
    assert after.source_digest == _digest(score_path)
    assert ScheduleConfig.model_validate_json(after.schedule_json).interval == "10m"


async def test_pinned_amendment_requires_registration_channel(registry, tmp_path):
    """A conscious re-registration re-pins to the new bytes; the tick then runs."""
    score_path = _write_score(tmp_path / "score.yaml", schedule=_pinned_schedule())
    clock = _Clock(datetime(2026, 9, 27, 6, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    record = await _register_pinned(controller, score_path)

    _write_score(score_path, schedule=_pinned_schedule(), prompt="Amended by the operator.")
    await controller.handle_tick(_tick(record, clock, datetime(2026, 9, 27, 6, 5, tzinfo=UTC)))
    assert effects.requests == []

    amended = await _register_pinned(controller, score_path)
    assert amended is not None
    assert amended.pinned_source_digest == _digest(score_path)
    await controller.handle_tick(_tick(amended, clock, datetime(2026, 9, 27, 6, 10, tzinfo=UTC)))
    assert len(effects.requests) == 1


# --- type and validation contract -------------------------------------------


def test_pin_declaration_validation():
    config = ScheduleConfig.model_validate({"interval": "5m", "pin_source_digest": True})
    assert config.pin_source_digest is True
    assert ScheduleConfig.model_validate({"interval": "5m"}).pin_source_digest is False

    with pytest.raises(ValidationError):
        ScheduleConfig.model_validate({"interval": "5m", "pin_source_digest": {"x": 1}})
    with pytest.raises(ValidationError):
        ScheduleConfig.model_validate({"interval": "5m", "pin_source_digst": True})
