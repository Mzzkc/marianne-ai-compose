"""#172: a job whose only non-COMPLETED sheets are *deliberately* skipped must
finish COMPLETED, not FAILED.

`_check_completions` computed `all_success = all(status == COMPLETED)`, so any
SKIPPED sheet flipped the job to FAILED — even when all intended work finished
(e.g. `--start-sheet N` marks earlier sheets SKIPPED, or a `skip_when` skip).
The fix reuses the cascade-vs-deliberate discriminant the baton already relies
on (`_is_dep_satisfied`): a SKIPPED sheet with `error_code is None` was a
deliberate skip → counts as success; a SKIPPED sheet with `error_code` set was
cascade-blocked by a failed upstream → genuine failure. FAILED/CANCELLED never
count as success.
"""

from __future__ import annotations

from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.state import BatonSheetStatus, SheetExecutionState


def _job(statuses: dict[int, tuple[BatonSheetStatus, str | None]]):
    """Build a registered job; statuses maps sheet_num → (status, error_code)."""
    adapter = BatonAdapter()
    sheets = {
        n: SheetExecutionState(sheet_num=n, instrument_name="claude-code")
        for n in statuses
    }
    adapter._baton.register_job("j", sheets, {})
    job = adapter._baton._jobs["j"]
    for n, (status, error_code) in statuses.items():
        job.sheets[n].status = status
        job.sheets[n].error_code = error_code
    return adapter, job


class TestJobSucceeded:
    def test_all_completed_is_success(self) -> None:
        adapter, job = _job(
            {1: (BatonSheetStatus.COMPLETED, None), 2: (BatonSheetStatus.COMPLETED, None)}
        )
        assert adapter._job_succeeded(job) is True

    def test_completed_plus_deliberate_skip_is_success(self) -> None:
        # #172: --start-sheet / skip_when leave SKIPPED with error_code=None.
        adapter, job = _job(
            {
                1: (BatonSheetStatus.SKIPPED, None),  # deliberate (e.g. --start-sheet)
                2: (BatonSheetStatus.COMPLETED, None),
                3: (BatonSheetStatus.COMPLETED, None),
            }
        )
        assert adapter._job_succeeded(job) is True

    def test_cascade_skip_is_failure(self) -> None:
        # A SKIPPED sheet with an error_code was blocked by a failed upstream.
        adapter, job = _job(
            {
                1: (BatonSheetStatus.FAILED, "E001"),
                2: (BatonSheetStatus.SKIPPED, "E999"),  # cascade-blocked
            }
        )
        assert adapter._job_succeeded(job) is False

    def test_completed_plus_cascade_skip_is_failure(self) -> None:
        # Even if the failed sheet were somehow absent, a cascade-skip (error_code
        # set) means intended work did not complete → not success.
        adapter, job = _job(
            {
                1: (BatonSheetStatus.COMPLETED, None),
                2: (BatonSheetStatus.SKIPPED, "E999"),
            }
        )
        assert adapter._job_succeeded(job) is False

    def test_any_failed_is_failure(self) -> None:
        adapter, job = _job(
            {1: (BatonSheetStatus.COMPLETED, None), 2: (BatonSheetStatus.FAILED, "E001")}
        )
        assert adapter._job_succeeded(job) is False

    def test_any_cancelled_is_failure(self) -> None:
        adapter, job = _job(
            {1: (BatonSheetStatus.COMPLETED, None), 2: (BatonSheetStatus.CANCELLED, None)}
        )
        assert adapter._job_succeeded(job) is False
