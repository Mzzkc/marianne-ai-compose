"""#527: `mzt status` must not report a confident cost when nothing was measured.

A sheet's ``cost_confidence`` defaults to 1.0, and both the JSON and human
status paths took ``min(..., default=1.0)`` over the sheets with a measured
``estimated_cost``. With no measured sheet the job therefore reported
``cost_confidence: 1.0`` and ``Cost: $0.00``, which reads as "we measured it,
and it was free". The honest answer is "unknown": ``null`` in JSON and an
explicit phrase in the human Cost Summary.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from marianne.cli.commands.status import _output_status_json, _render_cost_summary
from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.checkpoint import JobStatus as CPJobStatus

UNKNOWN_PHRASE = "cost unknown (no sheet reported a measured cost)"


def _job(sheets: dict[int, SheetState], **kwargs: Any) -> CheckpointState:
    return CheckpointState(
        job_id="j527",
        job_name="demo-527",
        total_sheets=max(len(sheets), 1),
        status=CPJobStatus.RUNNING,
        sheets=sheets,
        **kwargs,
    )


def _unmeasured_job() -> CheckpointState:
    # cost_confidence=1.0 is the field default: the trap that looked like certainty.
    return _job(
        {
            1: SheetState(sheet_num=1, estimated_cost=None, cost_confidence=1.0),
            2: SheetState(sheet_num=2, estimated_cost=0.0, cost_confidence=1.0),
        }
    )


def _json(job: CheckpointState, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    _output_status_json(job)
    out = capsys.readouterr().out
    # Unconfigured structlog may print a debug line before the JSON document.
    start = out.index("\n{") + 1 if not out.startswith("{") else 0
    data: dict[str, Any] = json.loads(out[start:])
    return data


def _human(job: CheckpointState, capsys: pytest.CaptureFixture[str]) -> str:
    _render_cost_summary(job)
    return capsys.readouterr().out


def _cost_line(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("Cost:")]
    assert len(lines) == 1, out
    return lines[0]


class TestNoMeasuredCost:
    def test_json_confidence_is_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = _json(_unmeasured_job(), capsys)
        assert data["cost"]["cost_confidence"] is None

    def test_json_confidence_null_with_no_sheets(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _json(_job({}), capsys)
        assert data["cost"]["cost_confidence"] is None

    def test_human_says_cost_unknown_not_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        line = _cost_line(_human(_unmeasured_job(), capsys))
        assert UNKNOWN_PHRASE in line
        assert "$0.00" not in line

    def test_human_keeps_limit_context(self, capsys: pytest.CaptureFixture[str]) -> None:
        job = _unmeasured_job()
        job.config_snapshot = {"cost_limits": {"enabled": True, "max_cost_per_job": 5.0}}
        out = _human(job, capsys)
        assert UNKNOWN_PHRASE in _cost_line(out)
        assert "(limit: $5.00)" in out

    def test_human_unknown_line_fits_80_columns(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Rich wraps at 80 columns off-TTY; a wrapped line split "not enforced".
        job = _unmeasured_job()
        job.config_snapshot = {"cost_limits": {"enabled": False, "max_cost_per_job": 5.0}}
        out = _human(job, capsys)
        assert UNKNOWN_PHRASE in _cost_line(out)
        assert "(limit: $5.00, not enforced)" in out

    def test_job_level_total_without_measured_sheet_is_not_confident(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        job = _job(
            {1: SheetState(sheet_num=1, estimated_cost=None, cost_confidence=1.0)},
            total_estimated_cost=2.0,
        )
        assert _json(job, capsys)["cost"]["cost_confidence"] is None
        line = _cost_line(_human(job, capsys))
        assert "~$2.00 (est.)" in line

    def test_accumulated_sheet_total_without_measured_sheet_is_not_confident(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sheet = SheetState(sheet_num=1, estimated_cost=None, cost_confidence=1.0)
        sheet.total_cost_usd = 3.0
        job = _job({1: sheet})
        assert _json(job, capsys)["cost"]["cost_confidence"] is None
        line = _cost_line(_human(job, capsys))
        assert "~$3.00 (est.)" in line


class TestMeasuredCostUnchanged:
    def test_measured_low_confidence(self, capsys: pytest.CaptureFixture[str]) -> None:
        job = _job(
            {
                1: SheetState(sheet_num=1, estimated_cost=0.5, cost_confidence=0.7),
                2: SheetState(sheet_num=2, estimated_cost=None, cost_confidence=1.0),
            }
        )
        assert _json(job, capsys)["cost"]["cost_confidence"] == pytest.approx(0.7)
        line = _cost_line(_human(job, capsys))
        assert "~$0.50 (est.)" in line
        assert UNKNOWN_PHRASE not in line

    def test_measured_high_confidence_is_exact(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        job = _job({1: SheetState(sheet_num=1, estimated_cost=1.25, cost_confidence=1.0)})
        assert _json(job, capsys)["cost"]["cost_confidence"] == pytest.approx(1.0)
        line = _cost_line(_human(job, capsys))
        assert "$1.25" in line
        assert "(est.)" not in line
