"""mzt status --json and mzt diagnose surface the prompt receipt (GH #546)."""

from __future__ import annotations

import json

from rich.console import Console

from marianne.cli.commands.diagnose import _build_diagnostic_report
from marianne.cli.commands.status import _output_status_json
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.core.prompt_receipt import PromptReceiptSource, build_receipt


def _source(kind: str, name: str, nbytes: int, **kw) -> PromptReceiptSource:
    return PromptReceiptSource(
        kind=kind, path=f"~/{name}", resolved_path=f"/h/{name}",
        sha256="sha256:" + "cd" * 32, bytes=nbytes, **kw,
    )


def _job() -> CheckpointState:
    receipt = build_receipt(
        prompt="p" * 3000, preamble="",
        sources=[
            _source("cadenza", "small.md", 100),
            _source("cadenza", "huge.md", 1500),
            _source("prelude", "mid.md", 700),
            _source("memory", "tiny.md", 10),
            _source("cadenza", "dropped.md", 0, dropped_reason="over_max_bytes: x"),
        ],
    )
    sheet = SheetState(
        sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1,
        prompt_receipt=receipt,
    )
    return CheckpointState(
        job_id="j", job_name="j", total_sheets=1, status=JobStatus.COMPLETED,
        sheets={1: sheet, 2: SheetState(sheet_num=2, status=SheetStatus.PENDING)},
    )


def test_status_json_carries_a_receipt_summary_per_sheet(capsys) -> None:
    _output_status_json(_job())
    sheets = json.loads(capsys.readouterr().out)["sheets"]
    summary = sheets["1"]["prompt_receipt"]
    assert summary["total_bytes"] == 3000
    assert summary["source_count"] == 5
    assert summary["dropped_count"] == 1
    assert summary["prompt_sha256"].startswith("sha256:")
    assert summary["estimated_tokens"] > 0
    assert "prompt_receipt" not in sheets["2"]


def test_diagnose_reports_top_three_sources_by_bytes() -> None:
    report = _build_diagnostic_report(_job())
    entry = report["prompt_receipts"][0]
    assert entry["sheet_num"] == 1
    assert [s["path"] for s in entry["top_sources"]] == [
        "~/huge.md", "~/mid.md", "~/small.md",
    ]
    assert entry["dropped"][0]["dropped_reason"] == "over_max_bytes: x"


def test_diagnose_human_output_names_them() -> None:
    from marianne.cli.commands.diagnose import _print_prompt_receipts

    console = Console(record=True, width=120)
    _print_prompt_receipts(console, _build_diagnostic_report(_job()))
    text = console.export_text()
    assert "huge.md" in text and "1,500" in text
    assert text.index("huge.md") < text.index("mid.md") < text.index("small.md")
    assert "tiny.md" not in text
    assert "dropped.md" in text and "over_max_bytes" in text
