"""The musician reports the prompt receipt on the attempt result (GH #546)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.checkpoint import SheetState
from marianne.core.config.job import InjectionCategory, InjectionItem, PromptConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.execution.base import ExecutionResult


@pytest.mark.asyncio
async def test_completed_attempt_checkpoint_shows_the_receipt(tmp_path: Path) -> None:
    prelude = tmp_path / "identity.md"
    prelude.write_text("Canyon identity\n")
    cad = tmp_path / "active"
    cad.mkdir()
    (cad / "one.md").write_text("ledger one\n")
    big = tmp_path / "big"
    big.mkdir()
    (big / "x.md").write_text("y" * 400)
    sheet = Sheet(
        num=1, movement=1, voice=1, voice_count=1, workspace=tmp_path,
        instrument_name="test-cli", prompt_template="Work.", timeout_seconds=60,
        prelude=[InjectionItem(file=str(prelude), **{"as": InjectionCategory.CONTEXT})],
        cadenza=[
            InjectionItem(directory=str(cad), **{"as": InjectionCategory.CONTEXT}),
            InjectionItem(
                directory=str(big), max_bytes=50, **{"as": InjectionCategory.CONTEXT}
            ),
        ],
    )
    adapter = BatonAdapter()
    adapter.register_job("j", [sheet], {1: []}, prompt_config=PromptConfig())
    pool = MagicMock()
    pool.release = AsyncMock()
    pool._registry = None
    adapter.set_backend_pool(pool)
    backend = MagicMock()
    backend.execute = AsyncMock(return_value=ExecutionResult(
        success=True, stdout="done", stderr="", duration_seconds=0.01, exit_code=0,
        model="the-echoed-model",
    ))

    await adapter._musician_wrapper(
        job_id="j", sheet=sheet, backend=backend,
        context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
        effective_instrument="test-cli",
    )
    events = []
    while not adapter.baton.inbox.empty():
        events.append(adapter.baton.inbox.get_nowait())
    result = next(e for e in events if hasattr(e, "prompt_receipt"))
    assert result.prompt_receipt is not None

    # What the backend was actually sent is what the receipt hashes.
    sent = backend.execute.await_args.args[0]
    assert result.prompt_receipt.prompt_sha256 == (
        "sha256:" + hashlib.sha256(sent.encode()).hexdigest()
    )
    assert result.prompt_receipt.total_bytes == len(sent.encode())

    state = SheetState(sheet_num=1, instrument_name="test-cli")
    state.record_attempt(result)
    stored = SheetState.model_validate(state.model_dump(mode="json"))
    receipt = stored.prompt_receipt
    assert receipt is not None
    reasons = {s.path: s.dropped_reason for s in receipt.sources}
    assert reasons[str(prelude)] is None
    assert reasons[str(big)].startswith("over_max_bytes")
    assert [s.kind for s in receipt.sources][-1] == "template"
    assert stored.attempt_history[-1]["model_used"] == "the-echoed-model"
    assert stored.attempt_history[-1]["prompt_sha256"] == receipt.prompt_sha256
    dumped = receipt.model_dump_json()
    assert "Canyon identity" not in dumped and "ledger one" not in dumped
    assert "Work." not in dumped


@pytest.mark.asyncio
async def test_inline_fallback_prompt_still_gets_a_receipt(tmp_path: Path) -> None:
    """With no PromptRenderer the musician builds the prompt itself; it is hashed too."""
    from marianne.daemon.baton.musician import sheet_task

    sheet = Sheet(
        num=1, movement=1, voice=1, voice_count=1, workspace=tmp_path,
        instrument_name="test-cli", prompt_template="Inline.", timeout_seconds=60,
    )
    inbox: asyncio.Queue = asyncio.Queue()
    backend = MagicMock()
    backend.execute = AsyncMock(return_value=ExecutionResult(
        success=True, stdout="ok", stderr="", duration_seconds=0.01, exit_code=0,
    ))
    await sheet_task(
        job_id="j", sheet=sheet, backend=backend,
        attempt_context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
        inbox=inbox, total_sheets=1, total_movements=1,
    )
    result = inbox.get_nowait()
    sent = backend.execute.await_args.args[0]
    assert result.prompt_receipt is not None
    assert result.prompt_receipt.prompt_sha256 == (
        "sha256:" + hashlib.sha256(sent.encode()).hexdigest()
    )
