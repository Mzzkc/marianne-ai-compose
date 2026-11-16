"""PromptReceipt model and its place on SheetState (GH #546)."""

from __future__ import annotations

from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.prompt_receipt import (
    MAX_RECEIPT_SOURCES,
    PromptReceipt,
    PromptReceiptSource,
    build_receipt,
)
from marianne.daemon.baton.events import SheetAttemptResult


def _src(kind: str, nbytes: int, name: str = "a.md", **kw) -> PromptReceiptSource:
    return PromptReceiptSource(
        kind=kind, path=f"~/{name}", resolved_path=f"/home/x/{name}",
        sha256="sha256:" + "ab" * 32, bytes=nbytes, **kw,
    )


def test_every_field_is_described() -> None:
    for model in (PromptReceipt, PromptReceiptSource):
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name} has no description"


def test_build_receipt_hashes_prompt_and_never_keeps_text() -> None:
    prompt = "SECRET-PROMPT-TEXT " * 10
    receipt = build_receipt(prompt=prompt, preamble="pre", sources=[_src("prelude", 40)])
    dumped = receipt.model_dump_json()
    assert "SECRET-PROMPT-TEXT" not in dumped
    assert receipt.total_bytes == len(prompt.encode())
    assert receipt.prompt_sha256.startswith("sha256:") and len(receipt.prompt_sha256) == 71
    assert receipt.preamble_bytes == 3
    assert receipt.template_bytes == receipt.total_bytes - 40
    assert receipt.estimated_tokens > 0


def test_dropped_sources_do_not_count_as_delivered() -> None:
    dropped = _src("cadenza", 0, dropped_reason="over_max_bytes: measured 9 > max_bytes 5")
    receipt = build_receipt(prompt="x" * 100, preamble="", sources=[dropped])
    assert receipt.template_bytes == 100
    assert receipt.sources[0].dropped_reason.startswith("over_max_bytes")


def test_sources_are_capped_and_the_overflow_counted() -> None:
    many = [_src("cadenza", 1, f"f{i}.md") for i in range(MAX_RECEIPT_SOURCES + 7)]
    receipt = build_receipt(prompt="x" * 500, preamble="", sources=many)
    assert len(receipt.sources) == MAX_RECEIPT_SOURCES
    assert receipt.sources_omitted == 7


def test_sheet_state_records_latest_receipt_and_round_trips() -> None:
    receipt = build_receipt(prompt="hello", preamble="", sources=[_src("prelude", 5)])
    sheet = SheetState(sheet_num=1, instrument_name="claude-code")
    sheet.record_attempt(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="claude-code", attempt=1,
        execution_success=True, duration_seconds=1.0, model_used="sonnet",
        prompt_receipt=receipt,
    ))
    assert sheet.prompt_receipt == receipt
    assert sheet.prompt_metrics == {"estimated_tokens": receipt.estimated_tokens}
    entry = sheet.attempt_history[-1]
    assert entry["prompt_sha256"] == receipt.prompt_sha256
    assert entry["prompt_bytes"] == 5
    assert entry["model_used"] == "sonnet"
    again = SheetState.model_validate(sheet.model_dump(mode="json"))
    assert again.prompt_receipt == receipt


def test_old_checkpoint_without_the_field_loads() -> None:
    data = SheetState(sheet_num=1).model_dump(mode="json")
    data.pop("prompt_receipt", None)
    assert SheetState.model_validate(data).prompt_receipt is None
    state = CheckpointState(job_id="j", job_name="j", total_sheets=1)
    raw = state.model_dump(mode="json")
    assert CheckpointState.model_validate(raw).sheets == {}


def test_a_later_attempt_without_a_receipt_keeps_the_last_one() -> None:
    receipt = build_receipt(prompt="hello", preamble="", sources=[])
    sheet = SheetState(sheet_num=1, instrument_name="claude-code")
    for rcpt in (receipt, None):
        sheet.record_attempt(SheetAttemptResult(
            job_id="j", sheet_num=1, instrument_name="claude-code", attempt=1,
            execution_success=False, duration_seconds=1.0, prompt_receipt=rcpt,
        ))
    assert sheet.prompt_receipt == receipt
