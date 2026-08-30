"""Physical receipts for identity, memory, cadenza, and technique delivery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from marianne.core.config.job import InjectionCategory, InjectionItem, PromptConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.musician import sheet_task as real_sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode
from marianne.execution.base import ExecutionResult


@pytest.mark.asyncio
async def test_receipt_is_written_only_after_sheet_task_boundary(tmp_path: Path) -> None:
    identity = tmp_path / "identity.md"
    identity.write_text("Canyon is a persistent systems thinker.\n")
    sheet = Sheet(
        num=1,
        movement=1,
        voice=1,
        voice_count=1,
        workspace=tmp_path,
        instrument_name="test-cli",
        prompt_template="Use the attached identity.",
        prelude=[
            InjectionItem(
                file=str(identity),
                required=True,
                **{"as": InjectionCategory.CONTEXT},
            )
        ],
        timeout_seconds=60,
    )
    adapter = BatonAdapter()
    adapter.register_job(
        "agent/canyon",
        [sheet],
        {1: []},
        prompt_config=PromptConfig(),
    )
    pool = MagicMock()
    pool.release = AsyncMock()
    pool._registry = None
    adapter.set_backend_pool(pool)
    backend = MagicMock()
    backend.execute = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            stdout="done",
            stderr="",
            duration_seconds=0.01,
            exit_code=0,
        )
    )
    context = AttemptContext(attempt_number=2, mode=AttemptMode.NORMAL)

    with patch(
        "marianne.daemon.baton.adapter.sheet_task",
        new_callable=AsyncMock,
    ) as mocked_sheet_task:
        await adapter._musician_wrapper(
            job_id="agent/canyon",
            sheet=sheet,
            backend=backend,
            context=context,
            effective_instrument="test-cli",
        )

    mocked_sheet_task.assert_awaited_once()
    # Rendering and setup alone are not delivery.
    assert list((tmp_path / ".marianne" / "context-receipts").glob("*/*.yaml")) == []

    # Execute the exact call captured at the real function boundary.
    await real_sheet_task(**mocked_sheet_task.await_args.kwargs)
    receipt_paths = list(
        (tmp_path / ".marianne" / "context-receipts").glob("*/*.yaml")
    )
    assert len(receipt_paths) == 1
    receipt = yaml.safe_load(receipt_paths[0].read_text())
    assert receipt["status"] == "delivered_to_sheet_task"
    assert receipt["job_id"] == "agent/canyon"
    assert receipt["sheet_num"] == 1
    assert receipt["attempt"] == 2
    assert receipt["instrument"] == "test-cli"
    assert receipt["prompt_sha256"].startswith("sha256:")
    assert receipt["context_manifest"][0]["resolved_path"] == str(identity)


@pytest.mark.asyncio
async def test_required_context_failure_becomes_dispatch_failure(tmp_path: Path) -> None:
    """A missing identity fails through the baton instead of stranding a task."""
    sheet = Sheet(
        num=1,
        movement=1,
        voice=1,
        voice_count=1,
        workspace=tmp_path,
        instrument_name="test-cli",
        prompt_template="Use the attached identity.",
        prelude=[
            InjectionItem(
                file=str(tmp_path / "missing-identity.md"),
                required=True,
                **{"as": InjectionCategory.CONTEXT},
            )
        ],
        timeout_seconds=60,
    )
    adapter = BatonAdapter()
    adapter.register_job(
        "agent-canyon",
        [sheet],
        {1: []},
        prompt_config=PromptConfig(),
    )
    pool = MagicMock()
    pool.release = AsyncMock()
    pool._registry = None
    adapter.set_backend_pool(pool)

    with patch(
        "marianne.daemon.baton.adapter.sheet_task",
        new_callable=AsyncMock,
    ) as mocked_sheet_task:
        await adapter._musician_wrapper(
            job_id="agent-canyon",
            sheet=sheet,
            backend=MagicMock(),
            context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            effective_instrument="test-cli",
        )

    mocked_sheet_task.assert_not_awaited()
    events = []
    while not adapter.baton.inbox.empty():
        events.append(adapter.baton.inbox.get_nowait())
    failure = next(event for event in events if hasattr(event, "error_classification"))
    assert failure.error_classification == "E505"
    assert "required injection file not found" in (failure.error_message or "")
