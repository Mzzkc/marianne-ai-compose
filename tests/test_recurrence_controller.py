"""Behavioral coverage for recurring-score lifecycle and tick submission."""

from __future__ import annotations

import hashlib
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from marianne.core.config import JobConfig
from marianne.daemon.baton.events import CronTick
from marianne.daemon.baton.timer import TimerHandle
from marianne.daemon.recurrence import RecurrenceController
from marianne.daemon.schedule_registry import ScheduleRegistry
from marianne.daemon.types import JobRequest, JobResponse


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@dataclass
class _Effects:
    responses: list[JobResponse] = field(default_factory=list)
    requests: list[JobRequest] = field(default_factory=list)
    scheduled: list[tuple[float, CronTick, TimerHandle]] = field(default_factory=list)
    cancelled: list[TimerHandle] = field(default_factory=list)
    active: set[str] = field(default_factory=set)
    submit_error: Exception | None = None

    async def submit(self, request: JobRequest) -> JobResponse:
        self.requests.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        if self.responses:
            return self.responses.pop(0)
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
        return schedule_id in self.active


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncIterator[ScheduleRegistry]:
    value = ScheduleRegistry(tmp_path / "conductor-state.db")
    await value.open()
    yield value
    await value.close()


def _write_score(
    path: Path,
    *,
    name: str = "weekday-report",
    schedule: dict[str, object] | None = None,
) -> JobConfig:
    payload: dict[str, object] = {
        "name": name,
        "workspace": str(path.parent / "workspace"),
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": "Write the report."},
    }
    if schedule is not None:
        payload["schedule"] = schedule
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return JobConfig.from_yaml(path)


def _controller(
    registry: ScheduleRegistry,
    effects: _Effects,
    clock: _Clock,
    *,
    rng: random.Random | None = None,
) -> RecurrenceController:
    return RecurrenceController(
        registry,
        effects.submit,
        effects.schedule,
        effects.cancel,
        effects.is_active,
        now=clock,
        rng=rng,
    )


async def test_register_replaces_projection_and_cancels_prior_timer(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """Changing one declaration replaces its projection and its one live timer."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)

    await controller.register(score_path, config)
    first = await registry.get("weekday-report")
    assert first is not None
    assert first.score_path == score_path.resolve()
    assert first.source_digest == hashlib.sha256(score_path.read_bytes()).hexdigest()
    assert first.next_due_at == datetime(2026, 8, 28, 12, 5, tzinfo=UTC).timestamp()
    assert len(effects.scheduled) == 1
    assert effects.scheduled[0][0] == 300.0

    config = _write_score(score_path, schedule={"interval": "10m"})
    await controller.register(score_path, config)

    replaced = await registry.get("weekday-report")
    assert replaced is not None
    assert replaced.created_at == first.created_at
    assert replaced.source_digest == hashlib.sha256(score_path.read_bytes()).hexdigest()
    assert replaced.next_due_at == datetime(2026, 8, 28, 12, 10, tzinfo=UTC).timestamp()
    assert effects.cancelled == [effects.scheduled[0][2]]
    assert len(effects.scheduled) == 2


async def test_restore_keeps_exactly_one_next_future_timer(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """Restart restoration schedules only the durable next future identity."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "5m"})
    assert config.schedule is not None
    due = datetime(2026, 8, 28, 12, 5, tzinfo=UTC).timestamp()
    await registry.upsert(
        config.name,
        config.name,
        score_path,
        config.schedule,
        hashlib.sha256(score_path.read_bytes()).hexdigest(),
        due,
    )
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)

    await controller.restore()
    await controller.restore()

    assert [item[1].due_at for item in effects.scheduled] == [due, due]
    assert effects.cancelled == [effects.scheduled[0][2]]


async def test_tick_rereads_changed_yaml_and_submits_file_safe_child(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """A due tick executes the current declaration, not a startup snapshot."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(
        score_path,
        name="Weekday Report",
        schedule={"interval": "5m"},
    )
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    await controller.register(score_path, config)
    _, event, _ = effects.scheduled[-1]

    _write_score(
        score_path,
        name="Weekday Report",
        schedule={"interval": "10m"},
    )
    clock.value = datetime(2026, 8, 28, 12, 5, tzinfo=UTC)
    await controller.handle_tick(event)

    assert len(effects.requests) == 1
    request = effects.requests[0]
    assert request.job_id == "Weekday-Report--scheduled--20260828T120500000000Z"
    assert request.schedule_id == "Weekday Report"
    assert request.scheduled_due_at == event.due_at
    assert request.fresh is True
    record = await registry.get("Weekday Report")
    assert record is not None
    assert record.source_digest == hashlib.sha256(score_path.read_bytes()).hexdigest()
    assert '"interval":"10m"' in record.schedule_json
    assert record.last_run_id == request.job_id
    assert record.last_outcome == "submitted"
    assert record.next_due_at == datetime(2026, 8, 28, 12, 15, tzinfo=UTC).timestamp()


@pytest.mark.parametrize("missing_source", [True, False])
async def test_tick_removes_missing_or_unscheduled_source(
    registry: ScheduleRegistry,
    tmp_path: Path,
    missing_source: bool,
) -> None:
    """A source that disappears or loses schedule authority is deregistered."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    await controller.register(score_path, config)
    _, event, _ = effects.scheduled[-1]

    if missing_source:
        score_path.unlink()
    else:
        _write_score(score_path, schedule=None)
    clock.value += timedelta(minutes=5)
    await controller.handle_tick(event)

    assert await registry.get("weekday-report") is None
    assert effects.requests == []


async def test_overlap_skip_advances_and_escalates_repeated_drops(
    registry: ScheduleRegistry,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Active lineage skips never pile up and repeat drops become loud errors."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects(active={"weekday-report"})
    controller = _controller(registry, effects, clock)
    await controller.register(score_path, config)

    _, first_event, _ = effects.scheduled[-1]
    clock.value += timedelta(minutes=5)
    await controller.handle_tick(first_event)
    first = await registry.get("weekday-report")
    assert first is not None
    assert first.last_outcome == "overlap_skipped"
    assert first.consecutive_drops == 1

    _, second_event, _ = effects.scheduled[-1]
    clock.value += timedelta(minutes=5)
    await controller.handle_tick(second_event)
    second = await registry.get("weekday-report")
    assert second is not None
    assert second.consecutive_drops == 2
    assert effects.requests == []
    captured = capsys.readouterr().out
    assert "schedule.tick_dropped" in captured
    assert "[error" in captured


@pytest.mark.parametrize(
    ("misfire", "expected_requests", "expected_outcome"),
    [
        ("skip", 0, "misfire_skipped"),
        ("latest", 1, "submitted"),
    ],
)
async def test_restore_collapses_misfires_without_replay_storm(
    registry: ScheduleRegistry,
    tmp_path: Path,
    misfire: str,
    expected_requests: int,
    expected_outcome: str,
) -> None:
    """Downtime either skips or submits one latest catch-up, never every miss."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(
        score_path,
        schedule={"interval": "5m", "misfire": misfire},
    )
    assert config.schedule is not None
    due = datetime(2026, 8, 28, 10, 0, tzinfo=UTC).timestamp()
    await registry.upsert(
        config.name,
        config.name,
        score_path,
        config.schedule,
        hashlib.sha256(score_path.read_bytes()).hexdigest(),
        due,
    )
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)

    await controller.restore()

    assert len(effects.requests) == expected_requests
    record = await registry.get("weekday-report")
    assert record is not None
    assert record.last_outcome == expected_outcome
    assert record.next_due_at == datetime(2026, 8, 28, 12, 5, tzinfo=UTC).timestamp()
    assert len(effects.scheduled) == 1


async def test_submission_rejection_advances_without_recording_child(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """A rejected child is a recorded drop and recurrence still moves forward."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects(
        responses=[
            JobResponse(job_id="rejected-child", status="rejected", message="busy")
        ]
    )
    controller = _controller(registry, effects, clock)
    await controller.register(score_path, config)
    _, event, _ = effects.scheduled[-1]

    clock.value += timedelta(minutes=5)
    await controller.handle_tick(event)

    record = await registry.get("weekday-report")
    assert record is not None
    assert record.last_run_id is None
    assert record.last_outcome == "submission_rejected"
    assert record.consecutive_drops == 1
    assert record.next_due_at == datetime(2026, 8, 28, 12, 10, tzinfo=UTC).timestamp()


async def test_submission_error_is_recorded_and_re_raised(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """A submission fault advances durably before remaining loud to the baton."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "5m"})
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects(submit_error=RuntimeError("submission transport failed"))
    controller = _controller(registry, effects, clock)
    await controller.register(score_path, config)
    _, event, _ = effects.scheduled[-1]

    clock.value += timedelta(minutes=5)
    with pytest.raises(RuntimeError, match="submission transport failed"):
        await controller.handle_tick(event)

    record = await registry.get("weekday-report")
    assert record is not None
    assert record.last_outcome == "submission_error"
    assert record.consecutive_drops == 1
    assert record.next_due_at == datetime(2026, 8, 28, 12, 10, tzinfo=UTC).timestamp()
    assert effects.scheduled[-1][1].due_at == record.next_due_at


async def test_subsecond_due_identities_produce_distinct_exact_child_ids(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """Six fractional UTC digits preserve distinct sub-second due identities."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(score_path, schedule={"interval": "0.25s"})
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock)
    await controller.register(score_path, config)

    _, first_event, _ = effects.scheduled[-1]
    clock.value += timedelta(seconds=0.25)
    await controller.handle_tick(first_event)
    _, second_event, _ = effects.scheduled[-1]
    clock.value += timedelta(seconds=0.25)
    await controller.handle_tick(second_event)

    assert [request.job_id for request in effects.requests] == [
        "weekday-report--scheduled--20260828T120000250000Z",
        "weekday-report--scheduled--20260828T120000500000Z",
    ]


async def test_jitter_is_bounded_and_lifecycle_cancels_handles(
    registry: ScheduleRegistry,
    tmp_path: Path,
) -> None:
    """Jitter stays inside its declaration and lifecycle controls revoke timers."""
    score_path = tmp_path / "report.yaml"
    config = _write_score(
        score_path,
        schedule={"interval": "5m", "jitter_seconds": 7},
    )
    clock = _Clock(datetime(2026, 8, 28, 12, 0, tzinfo=UTC))
    effects = _Effects()
    controller = _controller(registry, effects, clock, rng=random.Random(7))

    await controller.register(score_path, config)
    first_delay, _, first_handle = effects.scheduled[-1]
    assert 300.0 <= first_delay <= 307.0

    await controller.pause("weekday-report")
    assert first_handle in effects.cancelled
    paused = await registry.get("weekday-report")
    assert paused is not None and paused.enabled is False

    await controller.resume("weekday-report")
    resumed_handle = effects.scheduled[-1][2]
    assert resumed_handle is not first_handle

    await controller.remove("weekday-report")
    assert resumed_handle in effects.cancelled
    assert await controller.describe("weekday-report") == []

    config = _write_score(score_path, schedule={"interval": "5m"})
    await controller.register(score_path, config)
    shutdown_handle = effects.scheduled[-1][2]
    await controller.shutdown()
    assert shutdown_handle in effects.cancelled
