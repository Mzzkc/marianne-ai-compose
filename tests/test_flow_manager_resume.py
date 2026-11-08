"""Resume and restart preserve the operator's durable pause decision."""

import asyncio
import time
from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.config.flow import SheetTriggerConfig, TriggerAction
from marianne.core.flow_state import LoopRunState
from marianne.daemon import manager as manager_module
from marianne.daemon.baton.core import BatonCore
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.config import DaemonConfig
from marianne.daemon.manager import DaemonJobStatus, JobManager, JobMeta
from marianne.daemon.types import JobRequest


def _failed_loop_checkpoint(reason: str = "range_failed") -> CheckpointState:
    return CheckpointState(
        job_id="j", job_name="failed-loop", total_sheets=4,
        sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, total_cost_usd=8),
            2: SheetState(sheet_num=2, status=SheetStatus.COMPLETED, total_cost_usd=1),
            3: SheetState(sheet_num=3, status=SheetStatus.FAILED, total_cost_usd=2),
            4: SheetState(sheet_num=4, status=SheetStatus.SKIPPED, error_code="E999",
                          total_cost_usd=3),
        },
        flow={"loops": {"2-4": LoopRunState(
            span="2-4", index_name="pass_no", iteration=3, phase="completed",
            completed_reason=reason, cost_baseline_usd=0, iteration_started_at=1,
        )}},
    )


@pytest.mark.parametrize("status", [DaemonJobStatus.FAILED, DaemonJobStatus.CANCELLED])
def test_resume_failed_loop_reopens_run_state(status: DaemonJobStatus) -> None:
    checkpoint = _failed_loop_checkpoint()
    assert manager_module._reset_sheets_for_resume(checkpoint, status) == 2
    before = time.time()
    assert manager_module._reopen_loops_for_resume(checkpoint, status) == 1
    run = checkpoint.flow.loops["2-4"]
    assert checkpoint.sheets[3].status == SheetStatus.PENDING
    assert checkpoint.sheets[4].status == SheetStatus.PENDING
    assert checkpoint.sheets[2].status == SheetStatus.COMPLETED
    assert run.phase == "running"
    assert run.completed_reason is None
    assert run.iteration == 3
    assert run.cost_baseline_usd == 6
    assert run.iteration_started_at is not None
    assert before <= run.iteration_started_at <= time.time()
    assert manager_module._reopen_loops_for_resume(checkpoint, status) == 0


@pytest.mark.parametrize("status", [None, DaemonJobStatus.PAUSED, DaemonJobStatus.PAUSED_AT_CHAIN])
def test_resume_paused_keeps_completed_loop_closed(status: DaemonJobStatus | None) -> None:
    checkpoint = _failed_loop_checkpoint()
    before = checkpoint.model_dump()
    assert manager_module._reset_sheets_for_resume(checkpoint, status) == 0
    assert manager_module._reopen_loops_for_resume(checkpoint, status) == 0
    assert checkpoint.model_dump() == before


@pytest.mark.parametrize("reason", [
    "count_reached", "condition_met", "max_iterations", "skipped", "cost_limit_exceeded",
])
def test_resume_preserves_genuinely_completed_loops(reason: str) -> None:
    checkpoint = _failed_loop_checkpoint(reason)
    before = checkpoint.flow.model_dump()
    manager_module._reset_sheets_for_resume(checkpoint, DaemonJobStatus.FAILED)
    assert manager_module._reopen_loops_for_resume(checkpoint, DaemonJobStatus.FAILED) == 0
    assert checkpoint.flow.model_dump() == before


def test_resume_condition_error_loop_reopens() -> None:
    checkpoint = _failed_loop_checkpoint("condition_error")
    manager_module._reset_sheets_for_resume(checkpoint, DaemonJobStatus.FAILED)
    assert manager_module._reopen_loops_for_resume(checkpoint, DaemonJobStatus.FAILED) == 1
    run = checkpoint.flow.loops["2-4"]
    assert run.phase == "running"
    assert run.completed_reason is None
    assert run.iteration == 3
    assert run.cost_baseline_usd == 6


def test_resume_reopens_nested_inner_loop() -> None:
    checkpoint = _failed_loop_checkpoint()
    checkpoint.total_sheets = 6
    checkpoint.sheets.update({
        5: SheetState(sheet_num=5, status=SheetStatus.COMPLETED, total_cost_usd=4),
        6: SheetState(sheet_num=6, status=SheetStatus.COMPLETED, total_cost_usd=5),
    })
    checkpoint.flow.loops = {
        "2-6": LoopRunState(span="2-6", index_name="outer", iteration=3,
                            phase="completed", completed_reason="range_failed"),
        "3-4": LoopRunState(span="3-4", index_name="inner", iteration=2,
                            phase="completed", completed_reason="range_failed"),
    }
    manager_module._reset_sheets_for_resume(checkpoint, DaemonJobStatus.FAILED)
    assert manager_module._reopen_loops_for_resume(checkpoint, DaemonJobStatus.FAILED) == 2
    outer, inner = checkpoint.flow.loops.values()
    assert (outer.phase, outer.iteration, outer.cost_baseline_usd) == ("running", 3, 15)
    assert (inner.phase, inner.iteration, inner.cost_baseline_usd) == ("running", 2, 5)
    assert outer.completed_reason is inner.completed_reason is None


@pytest.mark.parametrize(
    "from_sheet, expected_count, expected_phase",
    [(3, 1, "running"), (5, 0, "completed")],
)
def test_resume_from_sheet_reopens_only_affected_loop(
    from_sheet: int, expected_count: int, expected_phase: str,
) -> None:
    checkpoint = _failed_loop_checkpoint()
    manager_module._reset_sheets_for_resume(checkpoint, DaemonJobStatus.PAUSED, from_sheet)
    assert manager_module._reopen_loops_for_resume(
        checkpoint, DaemonJobStatus.PAUSED, from_sheet,
    ) == expected_count
    assert checkpoint.flow.loops["2-4"].phase == expected_phase


def test_resume_does_not_reopen_loop_without_pending_members() -> None:
    checkpoint = _failed_loop_checkpoint()
    for sheet in checkpoint.sheets.values():
        sheet.status = SheetStatus.COMPLETED
    before = checkpoint.flow.model_dump()
    manager_module._reset_sheets_for_resume(checkpoint, DaemonJobStatus.FAILED)
    assert manager_module._reopen_loops_for_resume(checkpoint, DaemonJobStatus.FAILED) == 0
    assert checkpoint.flow.model_dump() == before


async def test_resume_failed_loop_runs_remaining_passes(tmp_path: Path) -> None:
    """The public manager resume path must restore indices before dispatch."""
    witness = tmp_path / "passes.txt"
    fail_once = tmp_path / "failed-once"
    score = tmp_path / "loop.yaml"
    score.write_text(
        "name: resume-loop\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "retry:\n  max_retries: 0\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "  loops:\n    1:\n      count: 3\n      index: pass_no\n"
        "prompt:\n  template: |\n"
        f"    echo {{{{ pass_no }}}} >> {witness}\n"
        f"    if [ {{{{ pass_no }}}} -eq 2 ] && [ ! -e {fail_once} ]; then\n"
        f"      touch {fail_once}\n"
        "      exit 1\n"
        "    fi\n",
        encoding="utf-8",
    )
    manager = JobManager(DaemonConfig(
        pid_file=tmp_path / "conductor.pid", state_db_path=tmp_path / "jobs.db",
    ))

    async def terminal_status(job_id: str) -> DaemonJobStatus:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = manager._job_meta[job_id].status
            if status in {DaemonJobStatus.FAILED, DaemonJobStatus.COMPLETED}:
                return status
            await asyncio.sleep(0.02)
        pytest.fail("loop job did not reach a terminal state")

    await manager.start()
    try:
        response = await manager.submit_job(JobRequest(config_path=score))
        assert response.status == "accepted"
        assert await terminal_status(response.job_id) == DaemonJobStatus.FAILED
        assert witness.read_text().split() == ["1", "2"]
        resumed = await manager.resume_job(response.job_id)
        assert resumed.status == "accepted"
        assert await terminal_status(response.job_id) == DaemonJobStatus.COMPLETED
        assert witness.read_text().split() == ["1", "2", "2", "3"]
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_resume_rejects_changed_wall_limit_before_transition(tmp_path: Path) -> None:
    score = tmp_path / "changed.yaml"
    score.write_text(
        "name: Changed\n"
        f"workspace: {tmp_path}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: test\n"
        "max_wall_seconds: 120\n",
        encoding="utf-8",
    )
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "jobs.db"))
    manager._job_meta["j"] = JobMeta(
        job_id="j",
        config_path=score,
        workspace=tmp_path,
        status=DaemonJobStatus.PAUSED,
        max_wall_seconds=60.0,
        wall_deadline_at=1234.0,
    )

    with pytest.raises(Exception, match="max_wall_seconds is immutable on resume"):
        await manager._resume_active_job("j", config_path=score)
    assert manager._job_meta["j"].status == DaemonJobStatus.PAUSED
    assert manager._job_meta["j"].wall_deadline_at == 1234.0


@pytest.mark.asyncio
@pytest.mark.parametrize("flow_pause", [False, True])
async def test_paused_job_survives_real_manager_restart(
    tmp_path: Path,
    flow_pause: bool,
) -> None:
    score = tmp_path / "paused.yaml"
    score.write_text(
        "name: paused-flow\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: echo ok\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
    )
    seed = JobManager(config)
    await seed._registry.open()
    try:
        await seed._registry.register_job("paused-flow", score, tmp_path / "workspace")
        checkpoint = CheckpointState(
            job_id="paused-flow",
            job_name="paused-flow",
            total_sheets=1,
            status=JobStatus.PAUSED,
            sheets={1: SheetState(sheet_num=1)},
        )
        if flow_pause:
            checkpoint.flow.pause_reason = "trigger on sheet 1"
        await seed._registry.save_checkpoint("paused-flow", checkpoint.model_dump_json())
        await seed._registry.update_status("paused-flow", DaemonJobStatus.PAUSED.value)
    finally:
        await seed._registry.close()

    manager = JobManager(config)
    await manager.start()
    try:
        assert manager._job_meta["paused-flow"].status == DaemonJobStatus.PAUSED
        assert "paused-flow" not in manager._jobs
        persisted = await manager._registry.load_checkpoint("paused-flow")
        assert persisted is not None
        restored = CheckpointState.model_validate_json(persisted)
        assert restored.status == JobStatus.PAUSED
        assert restored.flow.pause_reason == ("trigger on sheet 1" if flow_pause else None)
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_operator_pause_survives_restart_after_real_submission(tmp_path: Path) -> None:
    score = tmp_path / "operator-paused.yaml"
    score.write_text(
        "name: operator-paused\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 1\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
    )
    first = JobManager(config)
    await first.start()
    try:
        response = await first.submit_job(JobRequest(config_path=score))
        assert response.status == "accepted"

        def registered() -> bool:
            return (
                first._job_meta[response.job_id].status == DaemonJobStatus.RUNNING
                and first._baton_adapter is not None
                and response.job_id in first._baton_adapter._baton._jobs
            )

        deadline = time.monotonic() + 3
        while not registered() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert registered()
        assert await first.pause_job(response.job_id)
        assert first._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
    finally:
        await first.shutdown(graceful=False)

    second = JobManager(config)
    await second.start()
    try:
        assert second._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
        assert response.job_id not in second._jobs
        checkpoint_json = await second._registry.load_checkpoint(response.job_id)
        assert checkpoint_json is not None
        assert CheckpointState.model_validate_json(checkpoint_json).status == JobStatus.PAUSED
    finally:
        await second.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_operator_pause_during_inflight_sheet_survives_graceful_restart(
    tmp_path: Path,
) -> None:
    score = tmp_path / "inflight-paused.yaml"
    score.write_text(
        "name: inflight-paused\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 2\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
        shutdown_timeout_seconds=10,
    )
    first = JobManager(config)
    await first.start()
    try:
        response = await first.submit_job(JobRequest(config_path=score))
        assert response.status == "accepted"

        def inflight() -> bool:
            adapter = first._baton_adapter
            return (
                adapter is not None
                and response.job_id in adapter._baton._jobs
                and any(
                    sheet.status in {SheetStatus.DISPATCHED, SheetStatus.IN_PROGRESS}
                    for sheet in adapter._baton._jobs[response.job_id].sheets.values()
                )
            )

        deadline = time.monotonic() + 5
        while not inflight() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert inflight()
        assert await first.pause_job(response.job_id)
        assert first._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
    finally:
        await first.shutdown(graceful=True)

    second = JobManager(config)
    await second.start()
    try:
        assert second._job_meta[response.job_id].status == DaemonJobStatus.PAUSED
        assert response.job_id not in second._jobs
        checkpoint_json = await second._registry.load_checkpoint(response.job_id)
        assert checkpoint_json is not None
        assert CheckpointState.model_validate_json(checkpoint_json).status == JobStatus.PAUSED
    finally:
        await second.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_trigger_pause_survives_restart_until_explicit_operator_resume(
    tmp_path: Path,
) -> None:
    score = tmp_path / "trigger-paused.yaml"
    score.write_text(
        "name: trigger-paused\n"
        f"workspace: {tmp_path / 'workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 2\n  total_items: 2\n"
        "  triggers:\n    1:\n      on_success:\n        - pause: true\n"
        "prompt:\n  template: echo done\n",
        encoding="utf-8",
    )
    config = DaemonConfig(
        pid_file=tmp_path / "conductor.pid",
        state_db_path=tmp_path / "jobs.db",
    )
    seed = JobManager(config)
    await seed._registry.open()
    try:
        await seed._registry.register_job("j", score, tmp_path / "workspace")
        checkpoint = CheckpointState(
            job_id="j",
            job_name="trigger-paused",
            total_sheets=2,
            sheets={
                1: SheetState(sheet_num=1, instrument_name="cli"),
                2: SheetState(sheet_num=2, instrument_name="cli"),
            },
        )
        baton = BatonCore()
        baton.register_job(
            "j",
            checkpoint.sheets,
            {2: [1]},
            flow_state=checkpoint.flow,
            triggers={"1": SheetTriggerConfig(on_success=[TriggerAction(pause=True)])},
        )
        await baton.handle_event(
            SheetAttemptResult(
                job_id="j",
                sheet_num=1,
                instrument_name="cli",
                attempt=1,
                execution_success=True,
                validation_pass_rate=100.0,
            )
        )
        assert checkpoint.sheets[1].status == SheetStatus.COMPLETED
        assert checkpoint.sheets[2].status == SheetStatus.PENDING
        assert checkpoint.flow.pause_reason == "trigger on sheet 1"
        checkpoint.status = JobStatus.PAUSED
        await seed._registry.save_checkpoint("j", checkpoint.model_dump_json())
        await seed._registry.update_status("j", DaemonJobStatus.PAUSED.value)
    finally:
        await seed._registry.close()

    manager = JobManager(config)
    await manager.start()
    try:
        assert manager._job_meta["j"].status == DaemonJobStatus.PAUSED
        assert "j" not in manager._jobs
        before_json = await manager._registry.load_checkpoint("j")
        assert before_json is not None
        assert CheckpointState.model_validate_json(before_json).flow.pause_reason == (
            "trigger on sheet 1"
        )
        response = await manager.resume_job("j")
        assert response.status == "accepted"
        deadline = time.monotonic() + 3
        while (
            manager._job_meta["j"].status
            not in {
                DaemonJobStatus.COMPLETED,
                DaemonJobStatus.FAILED,
            }
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.01)
        after_json = await manager._registry.load_checkpoint("j")
        assert after_json is not None
        assert CheckpointState.model_validate_json(after_json).flow.pause_reason is None
        assert manager._job_meta["j"].status == DaemonJobStatus.COMPLETED
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_concert_submission_is_fire_and_forget_and_refuses_active_duplicate(
    tmp_path: Path,
) -> None:
    parent_score = tmp_path / "parent.yaml"
    parent_score.write_text("name: parent\n", encoding="utf-8")
    child_score = tmp_path / "child.yaml"
    child_score.write_text(
        "name: child\n"
        f"workspace: {tmp_path / 'child-workspace'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 30\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(
            pid_file=tmp_path / "conductor.pid",
            state_db_path=tmp_path / "jobs.db",
        )
    )
    await manager.start()
    try:
        manager._job_meta["parent"] = JobMeta(
            job_id="parent",
            config_path=parent_score,
            workspace=tmp_path,
            status=DaemonJobStatus.RUNNING,
        )
        accepted, child_id, message = await manager._submit_flow_concert(
            "parent",
            "child.yaml",
        )
        assert accepted, message
        assert child_id == "child"
        assert manager._job_meta["child"].chain_depth == 1
        duplicate, duplicate_id, reason = await manager._submit_flow_concert(
            "parent",
            "child.yaml",
        )
        assert not duplicate
        assert duplicate_id == "child"
        assert reason is not None and "already" in reason
    finally:
        await manager.shutdown(graceful=False)


async def test_self_chain_concert_gets_a_lineage_id_instead_of_colliding(
    tmp_path: Path,
) -> None:
    """A trigger fires while its parent still runs, so a child derived from the
    same score stem is refused as an active duplicate and a self-chaining score
    stops after one iteration (GH #454). The child gets a lineage id; the depth
    guard stays the only bound."""
    score = tmp_path / "solver.yaml"
    score.write_text(
        "name: solver\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 30\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(pid_file=tmp_path / "conductor.pid", state_db_path=tmp_path / "jobs.db")
    )
    await manager.start()
    try:
        manager._job_meta["solver"] = JobMeta(
            job_id="solver",
            config_path=score,
            workspace=tmp_path,
            status=DaemonJobStatus.RUNNING,
            concert_config={"enabled": True, "max_chain_depth": 2},
        )
        accepted, child_id, message = await manager._submit_flow_concert("solver", "solver.yaml")
        assert accepted, message
        assert child_id is not None and child_id.startswith("solver--chain1--")
        assert manager._job_meta[child_id].chain_depth == 1

        manager._job_meta[child_id].status = DaemonJobStatus.RUNNING
        manager._job_meta[child_id].concert_config = {"enabled": True, "max_chain_depth": 2}
        again, grandchild, _ = await manager._submit_flow_concert(child_id, "solver.yaml")
        assert again and grandchild is not None and grandchild.startswith("solver--chain2--")
        manager._job_meta[grandchild].status = DaemonJobStatus.RUNNING
        manager._job_meta[grandchild].concert_config = {"enabled": True, "max_chain_depth": 2}
        stopped, _, reason = await manager._submit_flow_concert(grandchild, "solver.yaml")
        assert not stopped and reason is not None and "depth limit" in reason
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_self_chain_survives_resume_with_a_config_copy(tmp_path: Path) -> None:
    """`mzt resume --config <copy>` points the parent's config_path at the copy.
    The score's trigger names the ORIGINAL file, so the child resolved to a
    sibling path, derived the parent's own id, and was refused as an active
    duplicate — the 13-sheet issue-solver chained nothing on 2026-10-22.
    Identity is the job id the child would get, not the parent's path."""
    body = (
        "name: solver\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  template: sleep 30\n"
    )
    original = tmp_path / "solver.yaml"
    original.write_text(body, encoding="utf-8")
    copy = tmp_path / "resume" / "solver-resume.yaml"
    copy.parent.mkdir()
    copy.write_text(body, encoding="utf-8")
    (tmp_path / "resume" / "solver.yaml").write_text(body, encoding="utf-8")
    manager = JobManager(
        DaemonConfig(pid_file=tmp_path / "conductor.pid", state_db_path=tmp_path / "jobs.db")
    )
    await manager.start()
    try:
        manager._job_meta["solver"] = JobMeta(
            job_id="solver",
            config_path=copy,  # what resume --config left behind
            workspace=tmp_path,
            status=DaemonJobStatus.RUNNING,
            concert_config={"enabled": True, "max_chain_depth": 2},
        )
        accepted, child_id, message = await manager._submit_flow_concert("solver", "solver.yaml")
        assert accepted, message
        assert child_id is not None and child_id.startswith("solver--chain1--")
        assert manager._job_meta[child_id].chain_depth == 1
    finally:
        await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_resume_no_reload_runs_the_admitted_score_not_the_edited_disk(
    tmp_path: Path,
) -> None:
    """GH #417: ``mzt resume --no-reload`` promised the admitted config and was a
    silent no-op because nothing wrote ``config_snapshot``. Sheet 2's prompt is a
    bash command (the ``cli`` profile) that records which template ran: the YAML is
    edited between pause and resume; with ``--no-reload`` the ORIGINAL runs, without
    it the EDITED one does."""
    for no_reload, expected in ((True, "original"), (False, "edited")):
        root = tmp_path / ("no-reload" if no_reload else "reload")
        root.mkdir()
        workspace = root / "workspace"
        witness = root / "witness.txt"
        score = root / "score.yaml"

        def body(word: str, workspace: Path = workspace, witness: Path = witness) -> str:
            return (
                "name: snapshot-417\n"
                f"workspace: {workspace}\n"
                "instrument: cli\n"
                "sheet:\n  size: 1\n  total_items: 2\n"
                "  triggers:\n    1:\n      on_success:\n        - pause: true\n"
                f"prompt:\n  template: echo {word} >> {witness}\n"
            )

        score.write_text(body("original"), encoding="utf-8")
        config = DaemonConfig(
            pid_file=root / "conductor.pid", state_db_path=root / "jobs.db"
        )
        manager = JobManager(config)
        await manager.start()
        try:
            response = await manager.submit_job(JobRequest(config_path=score))
            assert response.status == "accepted"
            job_id = response.job_id
            deadline = time.monotonic() + 10
            while (
                manager._job_meta[job_id].status != DaemonJobStatus.PAUSED
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.02)
            assert manager._job_meta[job_id].status == DaemonJobStatus.PAUSED
            paused_json = await manager._registry.load_checkpoint(job_id)
            assert paused_json is not None
            paused = CheckpointState.model_validate_json(paused_json)
            assert paused.config_snapshot is not None, "the admitted config was never persisted"
            assert paused.config_snapshot["prompt"]["template"] == f"echo original >> {witness}"
            assert witness.read_text().split() == ["original"]

            score.write_text(body("edited"), encoding="utf-8")
            resumed = await manager.resume_job(job_id, no_reload=no_reload)
            assert resumed.status == "accepted"
            deadline = time.monotonic() + 10
            while (
                manager._job_meta[job_id].status
                not in {DaemonJobStatus.COMPLETED, DaemonJobStatus.FAILED}
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.02)
            assert manager._job_meta[job_id].status == DaemonJobStatus.COMPLETED
            final_json = await manager._registry.load_checkpoint(job_id)
            assert final_json is not None
            final = CheckpointState.model_validate_json(final_json)
            assert witness.read_text().split() == ["original", expected], {
                n: (s.status.value, s.attempt_count, s.error_message, s.stdout_tail)
                for n, s in final.sheets.items()
            }
        finally:
            await manager.shutdown(graceful=False)


@pytest.mark.asyncio
async def test_concert_child_inherits_the_parent_runtime_variables(tmp_path: Path) -> None:
    """`mzt run --var k=v` on the root iteration binds the whole chain: a
    self-chained child is the same engagement, so it is submitted with the
    parent's runtime variables and they persist on its checkpoint (#359 shape)."""
    score = tmp_path / "solver.yaml"
    score.write_text(
        "name: solver\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "instrument: cli\n"
        "sheet:\n  size: 1\n  total_items: 1\n"
        "prompt:\n  variables:\n    project_root: default\n  template: sleep 30\n",
        encoding="utf-8",
    )
    manager = JobManager(
        DaemonConfig(pid_file=tmp_path / "conductor.pid", state_db_path=tmp_path / "jobs.db")
    )
    await manager.start()
    try:
        manager._job_meta["solver"] = JobMeta(
            job_id="solver",
            config_path=score,
            workspace=tmp_path,
            status=DaemonJobStatus.RUNNING,
            concert_config={"enabled": True, "max_chain_depth": 3},
        )
        manager._live_states["solver"] = CheckpointState(
            job_id="solver",
            job_name="solver",
            total_sheets=1,
            sheets={1: SheetState(sheet_num=1)},
            runtime_variables={"project_root": "/srv/checkout", "repo": "o/r"},
        )
        accepted, child_id, message = await manager._submit_flow_concert("solver", "solver.yaml")
        assert accepted, message
        assert child_id is not None
        # The live checkpoint is built when the run task starts, not at accept.
        deadline = time.monotonic() + 10.0
        while child_id not in manager._live_states and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        child = manager._live_states[child_id]
        assert child.runtime_variables == {"project_root": "/srv/checkout", "repo": "o/r"}
    finally:
        await manager.shutdown(graceful=False)
