"""Persisted-job remediation exercises the conductor's actual remedies."""

from pathlib import Path

import pytest

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config import JobConfig
from marianne.healing.context import ErrorContext
from marianne.healing.remediation import RemediationService


def config(workspace: Path) -> JobConfig:
    return JobConfig.model_validate(
        {
            "name": "repair",
            "workspace": str(workspace),
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "work"},
            "retry": {"max_retries": 4},
        }
    )


def test_checkpoint_mapping_preserves_history(tmp_path):
    sheet = SheetState(
        sheet_num=2,
        attempt_count=3,
        exit_code=1,
        exit_signal=9,
        error_message="broken",
        stderr_tail="detail",
        attempt_history=[{"error_code": "E201"}, {"other": 1}],
    )
    ctx = ErrorContext.from_checkpoint_sheet(
        sheet, 2, workspace=str(tmp_path), config=config(tmp_path)
    )
    assert (ctx.error_code, ctx.error_message, ctx.retry_count, ctx.max_retries) == (
        "E009",
        "broken",
        3,
        4,
    )
    assert (ctx.exit_code, ctx.signal, ctx.stdout_tail, ctx.stderr_tail) == (1, 9, "", "detail")
    assert ctx.previous_errors == ["E201"]
    assert ctx.metadata["attempt_history"] == [{"error_code": "E201"}, {"other": 1}]
    assert ctx.workspace == tmp_path


@pytest.mark.asyncio
async def test_selects_failed_sheets_and_repairs_workspace(tmp_path):
    workspace = tmp_path / "missing"
    job = CheckpointState(
        job_id="repair",
        job_name="repair",
        total_sheets=4,
        config_snapshot=config(workspace).model_dump(mode="json"),
        sheets={
            n: SheetState(
                sheet_num=n, status=status, error_code="E601", error_message="missing workspace"
            )
            for n, status in enumerate(
                [
                    SheetStatus.FAILED,
                    SheetStatus.COMPLETED,
                    SheetStatus.PENDING,
                    SheetStatus.FAILED,
                ],
                1,
            )
        },
    )
    result = await RemediationService().run(job, str(workspace))
    assert [r.error_context.sheet_number for r in result.reports] == [1, 4]
    assert workspace.is_dir()
    assert result.reports[0].any_remedies_applied
    assert result.reports[0].error_context.config.retry.max_retries == 4


@pytest.mark.asyncio
async def test_filter_can_diagnose_nonterminal_error_but_not_completed(tmp_path):
    job = CheckpointState(
        job_id="x",
        job_name="x",
        total_sheets=2,
        sheets={
            1: SheetState(sheet_num=1, status=SheetStatus.FERMATA, error_code="E901"),
            2: SheetState(sheet_num=2, status=SheetStatus.COMPLETED, error_code="E901"),
        },
    )
    service = RemediationService()
    assert (await service.run(job, str(tmp_path))).empty
    assert len((await service.run(job, str(tmp_path), sheet_filter=1)).reports) == 1
    assert (await service.run(job, str(tmp_path), sheet_filter=2)).empty


@pytest.mark.asyncio
async def test_dry_run_does_not_create_workspace(tmp_path):
    workspace = tmp_path / "missing"
    job = CheckpointState(
        job_id="x",
        job_name="x",
        total_sheets=1,
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                error_code="E601",
                error_message="missing workspace",
            )
        },
    )
    result = await RemediationService().run(job, str(workspace), dry_run=True)
    assert not workspace.exists()
    assert not result.reports[0].actions_taken
    assert "Dry run" in result.reports[0].actions_skipped[0][1]


@pytest.mark.asyncio
async def test_invalid_snapshot_is_reported_without_losing_diagnosis(tmp_path):
    job = CheckpointState(
        job_id="x",
        job_name="x",
        total_sheets=1,
        config_snapshot={"invalid": True},
        sheets={1: SheetState(sheet_num=1, status=SheetStatus.FAILED)},
    )
    result = await RemediationService().run(job, str(tmp_path))
    assert result.warnings
    assert result.reports[0].error_context.config is None


@pytest.mark.asyncio
async def test_spec_proposal_loads_corpus_without_claiming_a_fix(tmp_path):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "intent.md").write_text("The output must be a CSV.")
    cfg = config(tmp_path).model_copy(
        update={"spec": config(tmp_path).spec.model_copy(update={"spec_dir": "spec"})}
    )
    job = CheckpointState(
        job_id="x",
        job_name="x",
        total_sheets=1,
        config_snapshot=cfg.model_dump(mode="json"),
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.FAILED,
                error_code="E301",
                error_message="invalid spec corpus",
            )
        },
    )
    result = await RemediationService().run(job, str(tmp_path), auto_confirm=True)
    report = result.reports[0]
    assert any(d.remedy_name == "suggest_spec_fix" for d in report.diagnoses)
    assert "intent" in str(report.diagnostic_outputs)
    assert not report.any_remedies_applied
    assert (spec_dir / "intent.md").read_text() == "The output must be a CSV."


@pytest.mark.asyncio
async def test_recompose_only_for_exhausted_validation_failures(tmp_path):
    cfg = config(tmp_path)
    job = CheckpointState(
        job_id="x",
        job_name="x",
        total_sheets=1,
        config_path="/scores/repair.yaml",
        config_snapshot=cfg.model_dump(mode="json"),
        sheets={
            1: SheetState(
                sheet_num=1, status=SheetStatus.FAILED, error_code="E202", attempt_count=4
            )
        },
    )
    result = await RemediationService().run(job, str(tmp_path))
    report = result.reports[0]
    assert "re-compose" in str(report.diagnostic_outputs)
    assert "/scores/repair.yaml" in str(report.diagnostic_outputs)
    assert not report.any_remedies_applied
    job.sheets[1].error_code = "E101"
    result = await RemediationService().run(job, str(tmp_path))
    assert not any(d.remedy_name == "suggest_recompose" for d in result.reports[0].diagnoses)


@pytest.mark.parametrize("code", ["E101", "E601", "E901", "E009"])
def test_proposals_do_not_match_unrelated_baton_errors(code):
    from marianne.healing.remedies.spec_fix import SuggestRecomposeRemedy, SuggestSpecFixRemedy

    ctx = ErrorContext(error_code=code, error_message="unrelated", error_category="execution")
    assert SuggestSpecFixRemedy().diagnose(ctx) is None
    assert SuggestRecomposeRemedy().diagnose(ctx) is None
