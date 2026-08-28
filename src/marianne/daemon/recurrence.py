"""Recurring-score controller joining durable schedules to normal submission."""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml

from marianne.core.config import JobConfig, MisfirePolicy, ScheduleConfig
from marianne.core.config.workspace import sanitize_workspace_name
from marianne.core.logging import get_logger
from marianne.core.scheduling import next_due_at
from marianne.daemon.baton.events import CronTick
from marianne.daemon.baton.timer import TimerHandle
from marianne.daemon.schedule_registry import ScheduleRecord, ScheduleRegistry
from marianne.daemon.types import JobRequest, JobResponse
from marianne.utils.time import utc_now

_logger = get_logger("daemon.recurrence")

SubmitJob = Callable[[JobRequest], Awaitable[JobResponse]]
ScheduleTick = Callable[[float, CronTick], TimerHandle]
CancelTick = Callable[[TimerHandle], bool]
IsScheduleActive = Callable[[str], bool]
LifecycleAdmission = Callable[[tuple[str, ...]], None]
LifecycleProbe = Callable[[tuple[str, ...]], bool]


class RecurrenceController:
    """Own recurrence calculation, timer handles, and scheduled submissions."""

    def __init__(
        self,
        registry: ScheduleRegistry,
        submit: SubmitJob,
        schedule_tick: ScheduleTick,
        cancel_tick: CancelTick,
        is_active: IsScheduleActive,
        *,
        now: Callable[[], datetime] = utc_now,
        rng: random.Random | None = None,
    ) -> None:
        self._registry = registry
        self._submit = submit
        self._schedule_tick = schedule_tick
        self._cancel_tick = cancel_tick
        self._is_active = is_active
        self._now = now
        self._rng = rng or random.Random()
        self._timers: dict[str, tuple[float, TimerHandle]] = {}
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}
        self._registration_lock = asyncio.Lock()
        self._shutting_down = False

    async def restore(self) -> None:
        """Restore one live timer or one collapsed misfire per enabled schedule."""
        current = self._current_time()
        for record in await self._registry.list():
            if record.next_due_at <= current.timestamp():
                await self.handle_tick(
                    CronTick(
                        entry_name=record.schedule_id,
                        score_path=str(record.score_path),
                        due_at=record.next_due_at,
                        timestamp=current.timestamp(),
                    )
                )
                continue
            async with self._lock_schedules(record.schedule_id):
                refreshed = await self._registry.get(record.schedule_id)
                self._cancel_timer(record.schedule_id)
                if refreshed is not None and refreshed.enabled:
                    self._arm_record(refreshed, current)

    async def register(
        self,
        score_path: Path,
        config: JobConfig | None = None,
        *,
        before_wait: LifecycleProbe | None = None,
        before_mutation: LifecycleAdmission | None = None,
    ) -> ScheduleRecord | None:
        """Register or replace the current schedule declaration for one score."""
        resolved_path = score_path.resolve(strict=False)
        async with self._registration_lock:
            source_config, source_digest = await self._load_score(resolved_path)
            if config is not None and config != source_config:
                _logger.info(
                    "schedule.source_changed_during_registration",
                    schedule_id=source_config.name,
                    score_path=str(resolved_path),
                )
            config = source_config
            priors = await self._registry.list()
            prior_ids = {
                prior.schedule_id
                for prior in priors
                if prior.score_path == resolved_path
                and prior.schedule_id != config.name
            }
            mutation_ids = tuple(sorted({config.name, *prior_ids}))
            if before_wait is not None and not before_wait(mutation_ids):
                raise RuntimeError(
                    "Schedule lifecycle is already owned by another task"
                )
            async with self._lock_schedules(config.name, *prior_ids):
                if before_mutation is not None:
                    before_mutation(mutation_ids)
                for prior_id in prior_ids:
                    await self._remove_locked(prior_id)

                if config.schedule is None:
                    if await self._registry.get(config.name) is not None:
                        await self._remove_locked(config.name)
                    return None

                current = self._current_time()
                due = next_due_at(config.schedule, current)
                await self._registry.upsert(
                    config.name,
                    config.name,
                    resolved_path,
                    config.schedule,
                    source_digest,
                    due.timestamp(),
                )
                self._cancel_timer(config.name)
                record = await self._registry.get(config.name)
                if record is None:
                    raise RuntimeError(
                        f"Schedule {config.name!r} disappeared after registration"
                    )
                if record.enabled:
                    self._arm_record(record, current)
                return record

    async def handle_tick(self, event: CronTick) -> None:
        """Claim, resolve, record, and advance one exact recurring due identity."""
        if event.due_at is None:
            _logger.error(
                "schedule.tick_missing_due_at",
                schedule_id=event.entry_name,
                score_path=event.score_path,
            )
            return

        async with self._lock_schedules(event.entry_name):
            replacement = await self._handle_tick_locked(event)
        if replacement is not None:
            await self.register(*replacement)

    async def _handle_tick_locked(
        self,
        event: CronTick,
    ) -> tuple[Path, JobConfig] | None:
        """Handle one tick while its schedule lifecycle lock is held."""
        assert event.due_at is not None

        due_at = event.due_at
        active_timer = self._timers.get(event.entry_name)
        if active_timer is not None and active_timer[0] == due_at:
            self._cancel_timer(event.entry_name)

        record = await self._registry.get(event.entry_name)
        if record is None or not record.enabled:
            return None
        if record.next_due_at != due_at:
            self._arm_record(record, self._current_time())
            return None

        current = self._current_time()
        if due_at > current.timestamp():
            self._arm_record(record, current)
            return None

        stored_schedule = ScheduleConfig.model_validate_json(record.schedule_json)
        claim = await self._registry.claim_due(
            record.schedule_id,
            due_at,
            now=current.timestamp(),
        )
        if claim is None:
            await self._advance_stale_claim(record, stored_schedule, due_at, current)
            return None

        next_due = self._calculate_next(stored_schedule, due_at, current)
        score_path = record.score_path
        try:
            config, source_digest = await self._load_score(score_path)
        except FileNotFoundError:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "source_missing",
                next_due,
                dropped=True,
            )
            await self._remove_locked(record.schedule_id)
            _logger.warning(
                "schedule.source_removed",
                schedule_id=record.schedule_id,
                score_path=str(score_path),
            )
            return None
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "source_invalid",
                next_due,
                dropped=True,
            )
            self._arm_next(record.schedule_id, score_path, stored_schedule, next_due, current)
            _logger.error(
                "schedule.source_invalid",
                schedule_id=record.schedule_id,
                score_path=str(score_path),
                error_type=type(exc).__name__,
            )
            return None

        if config.schedule is None:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "schedule_removed",
                next_due,
                dropped=False,
            )
            await self._remove_locked(record.schedule_id)
            return None

        if config.name != record.schedule_id:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "schedule_identity_changed",
                next_due,
                dropped=False,
            )
            await self._remove_locked(record.schedule_id)
            return score_path, config

        schedule = config.schedule
        next_due = self._calculate_next(schedule, due_at, current)
        if source_digest != record.source_digest:
            await self._registry.upsert(
                record.schedule_id,
                config.name,
                score_path,
                schedule,
                source_digest,
                next_due,
            )

        if not schedule.enabled:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "schedule_disabled",
                next_due,
                dropped=False,
            )
            return None

        is_misfire = event.timestamp > due_at
        if is_misfire and schedule.misfire is MisfirePolicy.SKIP:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "misfire_skipped",
                next_due,
                dropped=True,
            )
            self._arm_next(record.schedule_id, score_path, schedule, next_due, current)
            return None

        if self._is_active(record.schedule_id):
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "overlap_skipped",
                next_due,
                dropped=True,
            )
            self._arm_next(record.schedule_id, score_path, schedule, next_due, current)
            return None

        child_id = self._child_job_id(config.name, due_at)
        try:
            response = await self._submit(
                JobRequest(
                    config_path=score_path,
                    job_id=child_id,
                    schedule_id=record.schedule_id,
                    scheduled_due_at=due_at,
                    fresh=True,
                )
            )
        except Exception:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "submission_error",
                next_due,
                dropped=True,
            )
            self._arm_next(record.schedule_id, score_path, schedule, next_due, current)
            raise
        if response.status in {"accepted", "pending"}:
            await self._registry.record_submission(
                record.schedule_id,
                due_at,
                response.job_id,
            )
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "submitted",
                next_due,
                dropped=False,
            )
        else:
            await self._record_outcome(
                record.schedule_id,
                due_at,
                "submission_rejected",
                next_due,
                dropped=True,
            )
        self._arm_next(record.schedule_id, score_path, schedule, next_due, current)
        return None

    async def pause(
        self,
        schedule_id: str,
        *,
        before_mutation: LifecycleAdmission | None = None,
    ) -> None:
        """Pause one schedule and cancel its pending timer."""
        async with self._lock_schedules(schedule_id):
            if before_mutation is not None:
                before_mutation((schedule_id,))
            await self._registry.pause(schedule_id)
            self._cancel_timer(schedule_id)

    async def resume(
        self,
        schedule_id: str,
        *,
        before_mutation: LifecycleAdmission | None = None,
    ) -> None:
        """Resume one schedule and restore its one pending due identity."""
        replacement: tuple[Path, JobConfig] | None = None
        async with self._lock_schedules(schedule_id):
            if before_mutation is not None:
                before_mutation((schedule_id,))
            await self._registry.resume(schedule_id)
            record = await self._registry.get(schedule_id)
            if record is None:
                return
            current = self._current_time()
            if record.next_due_at <= current.timestamp():
                replacement = await self._handle_tick_locked(
                    CronTick(
                        entry_name=record.schedule_id,
                        score_path=str(record.score_path),
                        due_at=record.next_due_at,
                        timestamp=current.timestamp(),
                    )
                )
            else:
                self._arm_record(record, current)
        if replacement is not None:
            await self.register(*replacement, before_mutation=before_mutation)

    async def remove(
        self,
        schedule_id: str,
        *,
        before_mutation: LifecycleAdmission | None = None,
    ) -> None:
        """Remove one schedule and revoke its pending timer."""
        async with self._lock_schedules(schedule_id):
            if before_mutation is not None:
                before_mutation((schedule_id,))
            await self._remove_locked(schedule_id)

    async def describe(self, schedule_id: str | None = None) -> list[ScheduleRecord]:
        """Return durable schedule projections for later presentation layers."""
        if schedule_id is None:
            return await self._registry.list()
        record = await self._registry.get(schedule_id)
        return [] if record is None else [record]

    async def shutdown(self) -> None:
        """Cancel every controller-owned handle before timer-wheel shutdown."""
        self._shutting_down = True
        schedule_ids = list(self._timers)
        async with self._lock_schedules(*schedule_ids):
            for schedule_id in schedule_ids:
                self._cancel_timer(schedule_id)

    async def _remove_locked(self, schedule_id: str) -> None:
        """Remove one schedule while its lifecycle lock is held."""
        self._cancel_timer(schedule_id)
        await self._registry.remove(schedule_id)

    async def _advance_stale_claim(
        self,
        record: ScheduleRecord,
        schedule: ScheduleConfig,
        due_at: float,
        current: datetime,
    ) -> None:
        """Advance a durable prior-process claim without replaying its submission."""
        refreshed = await self._registry.get(record.schedule_id)
        if refreshed is None or not refreshed.enabled:
            return
        if refreshed.next_due_at != due_at:
            self._arm_record(refreshed, current)
            return
        next_due = self._calculate_next(schedule, due_at, current)
        await self._record_outcome(
            record.schedule_id,
            due_at,
            "stale_claim_skipped",
            next_due,
            dropped=True,
        )
        self._arm_next(record.schedule_id, record.score_path, schedule, next_due, current)

    async def _record_outcome(
        self,
        schedule_id: str,
        due_at: float,
        outcome: str,
        next_due: float,
        *,
        dropped: bool,
    ) -> None:
        """Record one claimed outcome and escalate repeated tick drops."""
        await self._registry.record_tick_outcome(
            schedule_id,
            due_at,
            outcome,
            next_due_at=next_due,
            dropped=dropped,
        )
        if not dropped:
            return
        record = await self._registry.get(schedule_id)
        if record is None:
            raise RuntimeError(f"Schedule {schedule_id!r} disappeared after tick outcome")
        log = _logger.error if record.consecutive_drops > 1 else _logger.warning
        log(
            "schedule.tick_dropped",
            schedule_id=schedule_id,
            due_at=due_at,
            outcome=outcome,
            consecutive_drops=record.consecutive_drops,
        )

    def _arm_record(self, record: ScheduleRecord, current: datetime) -> None:
        schedule = ScheduleConfig.model_validate_json(record.schedule_json)
        self._arm_next(
            record.schedule_id,
            record.score_path,
            schedule,
            record.next_due_at,
            current,
        )

    def _arm_next(
        self,
        schedule_id: str,
        score_path: Path,
        schedule: ScheduleConfig,
        due_at: float,
        current: datetime,
    ) -> None:
        self._cancel_timer(schedule_id)
        if self._shutting_down:
            return
        delay = max(0.0, due_at - current.timestamp())
        if schedule.jitter_seconds:
            delay += self._rng.uniform(0.0, float(schedule.jitter_seconds))
        event = CronTick(
            entry_name=schedule_id,
            score_path=str(score_path),
            due_at=due_at,
            timestamp=current.timestamp(),
        )
        handle = self._schedule_tick(delay, event)
        self._timers[schedule_id] = (due_at, handle)

    def _cancel_timer(self, schedule_id: str) -> None:
        existing = self._timers.pop(schedule_id, None)
        if existing is not None:
            self._cancel_tick(existing[1])

    def _lifecycle_lock(self, schedule_id: str) -> asyncio.Lock:
        """Return the stable lock serializing one schedule's lifecycle."""
        return self._lifecycle_locks.setdefault(schedule_id, asyncio.Lock())

    @asynccontextmanager
    async def _lock_schedules(
        self,
        *schedule_ids: str,
    ) -> AsyncIterator[None]:
        """Acquire lifecycle locks in deterministic ID order."""
        async with AsyncExitStack() as stack:
            for schedule_id in sorted(set(schedule_ids)):
                await stack.enter_async_context(self._lifecycle_lock(schedule_id))
            yield

    @staticmethod
    def _calculate_next(
        schedule: ScheduleConfig,
        due_at: float,
        current: datetime,
    ) -> float:
        due = datetime.fromtimestamp(due_at, tz=UTC)
        calculated = next_due_at(schedule, current, interval_anchor=due)
        if calculated.timestamp() <= due_at:
            raise ValueError("Calculated next due time must be later than the claimed due time")
        return calculated.timestamp()

    @staticmethod
    def _child_job_id(score_name: str, due_at: float) -> str:
        safe_name = sanitize_workspace_name(score_name)
        timestamp = datetime.fromtimestamp(due_at, tz=UTC).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        return f"{safe_name}--scheduled--{timestamp}"

    def _current_time(self) -> datetime:
        current = self._now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("RecurrenceController now() must return an aware datetime")
        return current.astimezone(UTC)

    @staticmethod
    async def _load_score(score_path: Path) -> tuple[JobConfig, str]:
        """Read a stable YAML source and return its parsed config and byte digest."""

        def _read() -> tuple[JobConfig, str]:
            for _attempt in range(2):
                before = score_path.read_bytes()
                after = score_path.read_bytes()
                if before == after:
                    data = yaml.safe_load(before)
                    if not isinstance(data, dict):
                        raise ValueError(
                            "The score file is empty or invalid. "
                            "A Marianne score requires at minimum: name, sheet, "
                            "and prompt sections. See 'mzt validate --help' or "
                            "the score writing guide for examples."
                        )
                    workspace = data.get("workspace")
                    if workspace:
                        workspace_path = Path(str(workspace))
                        if not workspace_path.is_absolute():
                            data["workspace"] = str(
                                (score_path.resolve().parent / workspace_path).resolve()
                            )
                    config = JobConfig.from_yaml_string(
                        yaml.safe_dump(data, sort_keys=False)
                    )
                    return config, hashlib.sha256(before).hexdigest()
            raise ValueError(f"Schedule source changed while being read: {score_path}")

        return await asyncio.to_thread(_read)


__all__ = ["RecurrenceController"]
