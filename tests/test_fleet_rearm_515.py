"""A fleet re-arms a dependent group it dropped once its dependencies are green (GH #515).

`_wait_for_dependency_groups` returns False the moment a dependency member
fails, and the dependent group was never submitted again: the operator had to
submit it by hand, outside the fleet's custody (candidate-advantage-orchestra,
2026-11-09). Now the dropped group is recorded on the fleet, named in its
status, and `resume_fleet` submits it when every dependency is success.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from marianne.core.config.fleet import FleetConfig, FleetGroupConfig, FleetScoreEntry
from marianne.daemon.fleet import get_fleet_status, resume_fleet, submit_fleet
from marianne.daemon.registry import DaemonJobStatus
from marianne.daemon.types import JobResponse


async def test_dropped_group_is_recorded_and_rearmed_on_resume(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("name: a\nworkspace: ./ws\n")
    (tmp_path / "b.yaml").write_text("name: b\nworkspace: ./ws\n")
    fleet = FleetConfig(
        name="f",
        scores=[FleetScoreEntry(path="a.yaml", group="root"),
                FleetScoreEntry(path="b.yaml", group="workers")],
        groups={"root": FleetGroupConfig(depends_on=[]),
                "workers": FleetGroupConfig(depends_on=["root"])},
    )
    manager = MagicMock()
    manager._fleet_records = {}
    manager._job_meta = {}
    submitted: list[str] = []

    async def submit_job(request) -> JobResponse:  # type: ignore[no-untyped-def]
        job_id = request.job_id
        submitted.append(job_id)
        manager._job_meta[job_id] = SimpleNamespace(status=DaemonJobStatus.RUNNING)
        if job_id == "f--a":
            async def fail_a() -> None:
                # Yield so the dependency coordinator observes RUNNING first.
                for _ in range(3):
                    await asyncio.sleep(0)
                manager._job_meta[job_id].status = DaemonJobStatus.FAILED
            asyncio.create_task(fail_a())
        return JobResponse(job_id=job_id, status="accepted", message="ok")

    manager.submit_job = AsyncMock(side_effect=submit_job)
    manager.resume_job = AsyncMock(
        return_value=JobResponse(job_id="x", status="accepted", message="ok")
    )

    assert (await submit_fleet(manager, tmp_path / "fleet.yaml", fleet)).status == "accepted"
    record = manager._fleet_records["f"]
    while "workers" not in record.dropped_groups:
        await asyncio.sleep(0.02)
    assert record.dropped_groups == {"workers"}
    assert submitted == ["f--a"], "the dependent group must not have been submitted"
    assert get_fleet_status(manager, "f")["dropped_groups"] == ["workers"]

    # Still red: resume re-arms nothing.
    result = await resume_fleet(manager, "f")
    assert result["rearmed"] == [] and submitted == ["f--a"]

    # The operator resubmitted the member under its fleet ID and it succeeded.
    manager._job_meta["f--a"].status = DaemonJobStatus.COMPLETED
    result = await resume_fleet(manager, "f")
    assert result["rearmed"] == ["workers"]
    assert submitted == ["f--a", "f--b"]
    assert record.dropped_groups == set()
    assert get_fleet_status(manager, "f")["dropped_groups"] == []


async def test_manager_resume_of_a_fleet_id_rearms(tmp_path: Path) -> None:
    """`mzt resume <fleet>` reaches the re-arm: resume_job routes a fleet ID to resume_fleet."""
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.fleet import FleetRecord
    from marianne.daemon.manager import JobManager

    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    (tmp_path / "a.yaml").write_text("name: a\nworkspace: ./ws\n")
    (tmp_path / "b.yaml").write_text("name: b\nworkspace: ./ws\n")
    fleet = FleetConfig(
        name="g",
        scores=[FleetScoreEntry(path="a.yaml", group="root"),
                FleetScoreEntry(path="b.yaml", group="workers")],
        groups={"root": FleetGroupConfig(depends_on=[]),
                "workers": FleetGroupConfig(depends_on=["root"])},
    )
    record = FleetRecord(
        fleet_id="g", config=fleet, config_path=tmp_path / "fleet.yaml",
        member_jobs={"a.yaml": "g--a"}, group_order=[{"root"}, {"workers"}],
    )
    record.scores_by_group = {"root": ["a.yaml"], "workers": ["b.yaml"]}
    record.dropped_groups = {"workers"}
    manager._fleet_records["g"] = record
    manager._job_meta["g--a"] = SimpleNamespace(status=DaemonJobStatus.COMPLETED)
    submitted: list[str] = []

    async def submit_job(request) -> JobResponse:  # type: ignore[no-untyped-def]
        submitted.append(request.job_id)
        return JobResponse(job_id=request.job_id, status="accepted", message="ok")

    manager.submit_job = submit_job  # type: ignore[method-assign]
    manager.resume_job_member = manager.resume_job  # keep a handle; members are not paused here

    # A member resume on a COMPLETED job is rejected by the real manager; the
    # fleet path tolerates that and still re-arms.
    response = await manager.resume_job("g")
    assert response.status == "accepted"
    assert "re-armed dropped group(s) workers" in response.message
    assert submitted == ["g--b"]
    assert record.dropped_groups == set()
