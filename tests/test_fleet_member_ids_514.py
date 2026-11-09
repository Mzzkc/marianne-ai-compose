"""Fleet members are named for their fleet, never by bare file stem (GH #514).

A member `integration.yaml` used to become job `integration`, colliding with
any earlier job of that name; auto-fresh then replaced that job's record and
`mzt status integration` showed the stale job as the member
(candidate-advantage-orchestra, 2026-11-09). Two fleets with a member of the
same stem would also collide with each other.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from marianne.core.config.fleet import FleetConfig, FleetScoreEntry
from marianne.daemon.fleet import submit_fleet
from marianne.daemon.types import JobRequest, JobResponse


def _manager() -> MagicMock:
    manager = MagicMock()
    manager._fleet_records = {}

    async def submit(request: JobRequest) -> JobResponse:
        return JobResponse(job_id=request.job_id or request.config_path.stem,
                           status="accepted", message="ok")

    manager.submit_job = AsyncMock(side_effect=submit)
    return manager


async def test_member_job_ids_are_fleet_scoped(tmp_path: Path) -> None:
    (tmp_path / "integration.yaml").write_text("name: integration\nworkspace: ./ws\n")
    (tmp_path / "01-build.yaml").write_text("name: build\nworkspace: ./ws\n")
    fleet = FleetConfig(name="memory-orchestra", scores=[
        FleetScoreEntry(path="01-build.yaml"), FleetScoreEntry(path="integration.yaml"),
    ])
    manager = _manager()
    response = await submit_fleet(manager, tmp_path / "fleet.yaml", fleet)
    assert response.status == "accepted"
    requests = [c.args[0] for c in manager.submit_job.await_args_list]
    assert {r.job_id for r in requests} == {
        "memory-orchestra--01-build", "memory-orchestra--integration",
    }
    record = manager._fleet_records["memory-orchestra"]
    assert record.member_jobs == {
        "01-build.yaml": "memory-orchestra--01-build",
        "integration.yaml": "memory-orchestra--integration",
    }


async def test_two_fleets_with_the_same_member_stem_do_not_collide(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        d = tmp_path / name
        d.mkdir()
        (d / "integration.yaml").write_text("name: integration\nworkspace: ./ws\n")
    manager = _manager()
    for name in ("alpha", "beta"):
        fleet = FleetConfig(name=f"{name}-fleet", scores=[FleetScoreEntry(path="integration.yaml")])
        await submit_fleet(manager, tmp_path / name / "fleet.yaml", fleet)
    ids = [c.args[0].job_id for c in manager.submit_job.await_args_list]
    assert ids == ["alpha-fleet--integration", "beta-fleet--integration"]
    assert len(set(ids)) == 2
