"""Fleet management for the Marianne conductor.

A fleet is a concert-of-concerts: multiple agent scores launched and managed
as a unit. The FleetManager handles fleet-level lifecycle operations:
detection, group dependency resolution, concurrent score launch, and
fleet-level pause/resume/cancel.

Fleets are one level of nesting: fleet → score → sheet. No fleet-of-fleets.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marianne.core.config.fleet import FleetConfig, FleetGroupConfig
from marianne.core.logging import get_logger
from marianne.daemon.registry import DaemonJobStatus
from marianne.daemon.task_utils import log_task_exception
from marianne.daemon.types import JobRequest, JobResponse

if TYPE_CHECKING:
    from marianne.daemon.manager import JobManager

_logger = get_logger("daemon.fleet")

_FLEET_DEPENDENCY_SUCCESS_STATUSES = {
    DaemonJobStatus.COMPLETED,
    DaemonJobStatus.PAUSED_AT_CHAIN,
}
_FLEET_DEPENDENCY_TERMINAL_STATUSES = {
    DaemonJobStatus.COMPLETED,
    DaemonJobStatus.FAILED,
    DaemonJobStatus.CANCELLED,
    DaemonJobStatus.PAUSED,
    DaemonJobStatus.PAUSED_AT_CHAIN,
}


class FleetRecord:
    """Tracks a running fleet's state.

    Stores the mapping from fleet_id → member job_ids, group assignments,
    and dependency ordering for fleet-level operations.
    """

    def __init__(
        self,
        fleet_id: str,
        config: FleetConfig,
        config_path: Path,
        member_jobs: dict[str, str],
        group_order: list[set[str]],
    ) -> None:
        self.fleet_id = fleet_id
        self.config = config
        self.config_path = config_path
        # Maps score path → job_id for submitted scores
        self.member_jobs = member_jobs
        # Topologically sorted groups: each set runs concurrently
        self.group_order = group_order

    @property
    def all_job_ids(self) -> list[str]:
        """All member job IDs in this fleet."""
        return list(self.member_jobs.values())


def is_fleet_config(config_path: Path) -> bool:
    """Check if a YAML file is a fleet config (type: fleet).

    Quick check that reads the YAML without full validation. Returns False
    on any error rather than raising.
    """
    import yaml

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        # Expected when probing arbitrary paths — not a misconfiguration.
        _logger.debug("fleet.config_not_found", path=str(config_path))
        return False
    except (OSError, yaml.YAMLError) as exc:
        # #255: a genuine read/parse error (permission denied, malformed YAML)
        # must be distinguishable from "valid file, just not a fleet config".
        # Still return False (the contract is bool), but surface WHY so the
        # operator isn't left guessing whether the file is wrong or broken.
        _logger.warning(
            "fleet.config_unreadable",
            path=str(config_path),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
    return isinstance(raw, dict) and raw.get("type") == "fleet"


def topological_sort_groups(
    groups: dict[str, FleetGroupConfig],
) -> list[set[str]]:
    """Sort fleet groups into dependency layers.

    Returns a list of sets, where each set contains group names that
    can run concurrently. Earlier sets must complete before later sets start.

    Groups not declared in the groups dict are treated as having no dependencies
    and are placed in the first layer.
    """
    if not groups:
        return [set()]

    # Build adjacency and in-degree
    in_degree: dict[str, int] = {}
    dependents: dict[str, list[str]] = defaultdict(list)

    for name, cfg in groups.items():
        in_degree.setdefault(name, 0)
        for dep in cfg.depends_on:
            dependents[dep].append(name)
            in_degree[name] = in_degree.get(name, 0) + 1
            in_degree.setdefault(dep, 0)

    # Kahn's algorithm with layer tracking
    layers: list[set[str]] = []
    queue = {name for name, deg in in_degree.items() if deg == 0}

    while queue:
        layers.append(queue)
        next_queue: set[str] = set()
        for name in queue:
            for dep in dependents.get(name, []):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.add(dep)
        queue = next_queue

    return layers


async def submit_fleet(
    manager: JobManager,
    config_path: Path,
    fleet_config: FleetConfig,
) -> JobResponse:
    """Submit a fleet config — launch scores in group-dependency order.

    Launches all scores in each group layer concurrently, waiting for
    each layer to be submitted before proceeding to the next.

    Args:
        manager: The JobManager to submit individual scores through.
        config_path: Path to the fleet YAML file (for relative path resolution).
        fleet_config: Parsed fleet configuration.

    Returns:
        JobResponse with fleet_id and status.
    """
    fleet_id = fleet_config.name
    config_dir = config_path.parent

    # Resolve group ordering
    group_order = topological_sort_groups(fleet_config.groups)

    # Map scores to their groups
    scores_by_group: dict[str, list[str]] = defaultdict(list)
    ungrouped: list[str] = []
    for entry in fleet_config.scores:
        if entry.group:
            scores_by_group[entry.group].append(entry.path)
        else:
            ungrouped.append(entry.path)

    # All group names that appear in the order
    ordered_group_names: set[str] = set()
    for layer in group_order:
        ordered_group_names.update(layer)

    member_jobs: dict[str, str] = {}

    _logger.info(
        "fleet.submitting",
        fleet_id=fleet_id,
        total_scores=len(fleet_config.scores),
        groups=len(fleet_config.groups),
        layers=len(group_order),
    )

    record = FleetRecord(
        fleet_id=fleet_id,
        config=fleet_config,
        config_path=config_path,
        member_jobs=member_jobs,
        group_order=group_order,
    )
    manager._fleet_records[fleet_id] = record

    # Submit ungrouped scores first (no dependencies)
    if ungrouped:
        results = await _submit_score_batch(
            manager, config_dir, ungrouped, member_jobs,
        )
        if not all(r.status == "accepted" for r in results):
            return JobResponse(
                job_id=fleet_id,
                status="rejected",
                message="One or more ungrouped scores failed to submit",
            )

    initial_layer = group_order[0] if group_order else set()
    await _submit_group_layer(
        manager,
        config_dir,
        initial_layer,
        scores_by_group,
        member_jobs,
        fleet_id=fleet_id,
    )

    remaining_layers = group_order[1:]
    if remaining_layers:
        task = asyncio.create_task(
            _submit_dependent_group_layers(
                manager,
                config_dir,
                remaining_layers,
                fleet_config.groups,
                scores_by_group,
                record,
            ),
            name=f"fleet-dependencies-{fleet_id}",
        )
        task.add_done_callback(
            lambda t: log_task_exception(
                t,
                _logger,
                "fleet.dependency_coordinator_failed",
            )
        )

    _logger.info(
        "fleet.submitted",
        fleet_id=fleet_id,
        member_count=len(member_jobs),
        job_ids=list(member_jobs.values()),
    )

    return JobResponse(
        job_id=fleet_id,
        status="accepted",
        message=(
            f"Fleet '{fleet_id}' launched with {len(member_jobs)} initial scores"
        ),
    )


async def _submit_group_layer(
    manager: JobManager,
    config_dir: Path,
    layer: set[str],
    scores_by_group: dict[str, list[str]],
    member_jobs: dict[str, str],
    *,
    fleet_id: str,
) -> list[JobResponse]:
    """Submit all scores for a dependency-satisfied group layer."""
    layer_scores: list[str] = []
    for group_name in layer:
        layer_scores.extend(scores_by_group.get(group_name, []))

    if not layer_scores:
        return []

    results = await _submit_score_batch(
        manager, config_dir, layer_scores, member_jobs,
    )
    if not all(r.status == "accepted" for r in results):
        _logger.warning(
            "fleet.layer_partial_failure",
            fleet_id=fleet_id,
            layer=sorted(layer),
            failed=[
                s for s, r in zip(layer_scores, results, strict=False)
                if r.status != "accepted"
            ],
        )
    return results


async def _submit_dependent_group_layers(
    manager: JobManager,
    config_dir: Path,
    remaining_layers: list[set[str]],
    groups: dict[str, FleetGroupConfig],
    scores_by_group: dict[str, list[str]],
    record: FleetRecord,
) -> None:
    """Submit dependent fleet groups after their dependencies complete."""
    for layer in remaining_layers:
        ready_groups: set[str] = set()
        for group_name in layer:
            dependency_groups = set(groups[group_name].depends_on)
            dependencies_ok = await _wait_for_dependency_groups(
                manager,
                record,
                scores_by_group,
                dependency_groups,
            )
            if dependencies_ok:
                ready_groups.add(group_name)
            else:
                _logger.warning(
                    "fleet.group_dependencies_failed",
                    fleet_id=record.fleet_id,
                    group=group_name,
                    dependencies=sorted(dependency_groups),
                )

        await _submit_group_layer(
            manager,
            config_dir,
            ready_groups,
            scores_by_group,
            record.member_jobs,
            fleet_id=record.fleet_id,
        )


async def _wait_for_dependency_groups(
    manager: JobManager,
    record: FleetRecord,
    scores_by_group: dict[str, list[str]],
    dependency_groups: set[str],
) -> bool:
    """Wait until all scores in dependency groups finish successfully."""
    dependency_score_paths = [
        score_path
        for group_name in dependency_groups
        for score_path in scores_by_group.get(group_name, [])
    ]
    if not dependency_score_paths:
        return True

    while True:
        statuses: list[DaemonJobStatus | None] = []
        for score_path in dependency_score_paths:
            job_id = record.member_jobs.get(score_path)
            meta = manager._job_meta.get(job_id) if job_id else None
            statuses.append(meta.status if meta is not None else None)

        if all(status in _FLEET_DEPENDENCY_SUCCESS_STATUSES for status in statuses):
            return True
        if any(
            status in _FLEET_DEPENDENCY_TERMINAL_STATUSES
            and status not in _FLEET_DEPENDENCY_SUCCESS_STATUSES
            for status in statuses
        ):
            return False

        await asyncio.sleep(0.5)


async def _submit_score_batch(
    manager: JobManager,
    config_dir: Path,
    score_paths: list[str],
    member_jobs: dict[str, str],
) -> list[JobResponse]:
    """Submit a batch of scores concurrently.

    Args:
        manager: The JobManager for submission.
        config_dir: Base directory for resolving relative score paths.
        score_paths: List of score YAML paths (relative to config_dir).
        member_jobs: Dict to populate with path → job_id mappings.

    Returns:
        List of JobResponse results.
    """
    tasks: list[asyncio.Task[JobResponse]] = []
    for score_path in score_paths:
        resolved = config_dir / score_path
        if not resolved.exists():
            _logger.error(
                "fleet.score_not_found",
                score_path=str(resolved),
            )
            # Return a rejection response without submitting
            tasks.append(
                asyncio.ensure_future(
                    _rejected_response(str(resolved), f"Score not found: {resolved}")
                )
            )
            continue

        request = JobRequest(config_path=resolved)
        tasks.append(asyncio.create_task(
            manager.submit_job(request),
            name=f"fleet-submit-{resolved.stem}",
        ))

    results = await asyncio.gather(*tasks)
    for score_path, response in zip(score_paths, results, strict=False):
        if response.status == "accepted":
            member_jobs[score_path] = response.job_id

    return list(results)


async def _rejected_response(job_id: str, message: str) -> JobResponse:
    """Create a rejection response (used as a coroutine for gather)."""
    return JobResponse(job_id=job_id, status="rejected", message=message)


async def pause_fleet(manager: JobManager, fleet_id: str) -> dict[str, Any]:
    """Pause all member scores in a fleet."""
    record = manager._fleet_records.get(fleet_id)
    if record is None:
        return {"error": f"Fleet '{fleet_id}' not found"}

    results: dict[str, bool] = {}
    for job_id in record.all_job_ids:
        try:
            ok = await manager.pause_job(job_id)
            results[job_id] = ok
        except Exception as exc:
            _logger.warning("fleet.pause_member_failed", job_id=job_id, error=str(exc))
            results[job_id] = False

    return {"fleet_id": fleet_id, "paused": results}


async def resume_fleet(manager: JobManager, fleet_id: str) -> dict[str, Any]:
    """Resume all paused member scores in a fleet."""
    record = manager._fleet_records.get(fleet_id)
    if record is None:
        return {"error": f"Fleet '{fleet_id}' not found"}

    results: dict[str, str] = {}
    for job_id in record.all_job_ids:
        try:
            response = await manager.resume_job(job_id)
            results[job_id] = response.status
        except Exception as exc:
            _logger.warning("fleet.resume_member_failed", job_id=job_id, error=str(exc))
            results[job_id] = f"error: {exc}"

    return {"fleet_id": fleet_id, "resumed": results}


async def cancel_fleet(manager: JobManager, fleet_id: str) -> dict[str, Any]:
    """Cancel all member scores in a fleet."""
    record = manager._fleet_records.get(fleet_id)
    if record is None:
        return {"error": f"Fleet '{fleet_id}' not found"}

    results: dict[str, bool] = {}
    for job_id in record.all_job_ids:
        try:
            ok = await manager.cancel_job(job_id)
            results[job_id] = ok
        except Exception as exc:
            _logger.warning("fleet.cancel_member_failed", job_id=job_id, error=str(exc))
            results[job_id] = False

    return {"fleet_id": fleet_id, "cancelled": results}


def get_fleet_status(manager: JobManager, fleet_id: str) -> dict[str, Any]:
    """Get status of a fleet and all its member scores."""
    record = manager._fleet_records.get(fleet_id)
    if record is None:
        return {"error": f"Fleet '{fleet_id}' not found"}

    members: list[dict[str, Any]] = []
    for score_path, job_id in record.member_jobs.items():
        meta = manager._job_meta.get(job_id)
        members.append({
            "score_path": score_path,
            "job_id": job_id,
            "status": meta.status.value if meta else "unknown",
            "group": next(
                (e.group for e in record.config.scores if e.path == score_path),
                None,
            ),
        })

    return {
        "fleet_id": fleet_id,
        "name": record.config.name,
        "total_scores": len(record.config.scores),
        "members": members,
        "groups": {
            name: {"depends_on": cfg.depends_on}
            for name, cfg in record.config.groups.items()
        },
    }
