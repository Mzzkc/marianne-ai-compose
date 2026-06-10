"""#360: runtime wiring of ``skip_when`` command predicates into baton execution.

Before this, ``skip_when`` (then named skip_when_command) was parsed, validated, and accepted by the
CLI but never evaluated at runtime — every sheet dispatched regardless. These
tests pin three properties of the wiring:

- **correctness**: exit 0 → skip; non-zero → proceed; ``{workspace}`` expands.
- **fail-safety**: a broken predicate (timeout, spawn error) must NOT skip —
  it must fall open to PROCEED (silently dropping real work is worse than a
  phantom run; matches the SkipWhenCommand model's documented semantics).
- **liveness**: a deliberately-skipped sheet (SKIPPED, error_code=None) releases
  its dependents — verified against the existing baton mechanism the wiring
  relies on.

The Jinja-form ``skip_when`` is intentionally NOT covered here: its documented
semantics (a Python ``eval`` expression over a ``sheets`` dict + ``job``) are an
unresolved product decision (#360), escalated separately.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config.execution import SkipWhenCommand
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetSkipped
from marianne.daemon.baton.skip import evaluate_skip_command
from marianne.daemon.baton.state import SheetExecutionState


def _make_sheet(num: int = 1, workspace: str = "/tmp/test-ws") -> Sheet:
    return Sheet(
        num=num,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=Path(workspace),
        instrument_name="claude-code",
        prompt_template="test prompt",
        timeout_seconds=60.0,
    )


# ──────────────────────────────────────────────────────────────────────────
# Unit: evaluate_skip_command — correctness + fail-safety
# ──────────────────────────────────────────────────────────────────────────


class TestEvaluateSkipCommand:
    @pytest.mark.asyncio
    async def test_exit_zero_skips(self, tmp_path: Path) -> None:
        swc = SkipWhenCommand(command="exit 0")
        should_skip, reason = await evaluate_skip_command(
            swc, workspace=tmp_path, context={}
        )
        assert should_skip is True
        assert "exited 0" in reason

    @pytest.mark.asyncio
    async def test_exit_nonzero_proceeds(self, tmp_path: Path) -> None:
        swc = SkipWhenCommand(command="exit 1")
        should_skip, reason = await evaluate_skip_command(
            swc, workspace=tmp_path, context={}
        )
        assert should_skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_workspace_substitution_file_present_skips(self, tmp_path: Path) -> None:
        (tmp_path / "flag").write_text("x")
        swc = SkipWhenCommand(command="test -f {workspace}/flag")
        should_skip, _ = await evaluate_skip_command(swc, workspace=tmp_path, context={})
        assert should_skip is True

    @pytest.mark.asyncio
    async def test_workspace_substitution_file_absent_proceeds(self, tmp_path: Path) -> None:
        swc = SkipWhenCommand(command="test -f {workspace}/flag")
        should_skip, _ = await evaluate_skip_command(swc, workspace=tmp_path, context={})
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_command_not_found_proceeds(self, tmp_path: Path) -> None:
        # bash -c on a missing binary exits 127 (non-zero) → proceed (fail-open).
        swc = SkipWhenCommand(command="this-binary-does-not-exist-xyz")
        should_skip, _ = await evaluate_skip_command(swc, workspace=tmp_path, context={})
        assert should_skip is False

    @pytest.mark.asyncio
    async def test_timeout_proceeds(self, tmp_path: Path) -> None:
        swc = SkipWhenCommand(command="sleep 30", timeout_seconds=0.2)
        should_skip, _ = await evaluate_skip_command(swc, workspace=tmp_path, context={})
        assert should_skip is False  # fail-open on timeout, NOT skip

    @pytest.mark.asyncio
    async def test_custom_context_variable_expands(self, tmp_path: Path) -> None:
        swc = SkipWhenCommand(command='test "{voice}" = "alpha"')
        skip_match, _ = await evaluate_skip_command(
            swc, workspace=tmp_path, context={"voice": "alpha"}
        )
        skip_nomatch, _ = await evaluate_skip_command(
            swc, workspace=tmp_path, context={"voice": "beta"}
        )
        assert skip_match is True
        assert skip_nomatch is False


# ──────────────────────────────────────────────────────────────────────────
# Integration: BatonAdapter._musician_wrapper wiring
# ──────────────────────────────────────────────────────────────────────────


def _mock_backend() -> AsyncMock:
    backend = AsyncMock()
    backend.execute = AsyncMock(
        return_value=MagicMock(
            success=True, exit_code=0, stdout="ok", stderr="",
            rate_limited=False, duration_seconds=0.1,
            input_tokens=1, output_tokens=1, model="test", error_message=None,
        )
    )
    return backend


def _drain(inbox: asyncio.Queue) -> list:
    events = []
    while not inbox.empty():
        events.append(inbox.get_nowait())
    return events


class TestSkipCommandWiring:
    @pytest.mark.asyncio
    async def test_exit_zero_skips_without_executing(self, tmp_path: Path) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, workspace=str(tmp_path))]
        adapter.register_job(
            "job", sheets, dependencies={},
            skip_when={1: SkipWhenCommand(command="exit 0")},
        )
        backend = _mock_backend()
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(return_value=backend)
        adapter._backend_pool.release = AsyncMock()

        await adapter._dispatch_callback("job", 1, _make_execution_state())
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

        backend.execute.assert_not_called()
        events = _drain(adapter._baton.inbox)
        assert any(isinstance(e, SheetSkipped) and e.sheet_num == 1 for e in events)

    @pytest.mark.asyncio
    async def test_exit_nonzero_proceeds_to_execution(self, tmp_path: Path) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, workspace=str(tmp_path))]
        adapter.register_job(
            "job", sheets, dependencies={},
            skip_when={1: SkipWhenCommand(command="exit 1")},
        )
        backend = _mock_backend()
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(return_value=backend)
        adapter._backend_pool.release = AsyncMock()

        await adapter._dispatch_callback("job", 1, _make_execution_state())
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

        backend.execute.assert_called()
        events = _drain(adapter._baton.inbox)
        assert not any(isinstance(e, SheetSkipped) for e in events)

    @pytest.mark.asyncio
    async def test_timeout_proceeds_to_execution(self, tmp_path: Path) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, workspace=str(tmp_path))]
        adapter.register_job(
            "job", sheets, dependencies={},
            skip_when={1: SkipWhenCommand(command="sleep 30", timeout_seconds=0.2)},
        )
        backend = _mock_backend()
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(return_value=backend)
        adapter._backend_pool.release = AsyncMock()

        await adapter._dispatch_callback("job", 1, _make_execution_state())
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

        backend.execute.assert_called()  # fail-open: timeout proceeds

    @pytest.mark.asyncio
    async def test_no_skip_command_proceeds(self, tmp_path: Path) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1, workspace=str(tmp_path))]
        adapter.register_job("job", sheets, dependencies={})
        backend = _mock_backend()
        adapter._backend_pool = MagicMock()
        adapter._backend_pool.acquire = AsyncMock(return_value=backend)
        adapter._backend_pool.release = AsyncMock()

        await adapter._dispatch_callback("job", 1, _make_execution_state())
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

        backend.execute.assert_called()


def _make_execution_state(sheet_num: int = 1) -> SheetExecutionState:
    return SheetExecutionState(sheet_num=sheet_num, instrument_name="claude-code", max_retries=3)


# ──────────────────────────────────────────────────────────────────────────
# Liveness: a deliberate skip releases dependents (existing mechanism)
# ──────────────────────────────────────────────────────────────────────────


class TestSkipReleasesDependents:
    def test_skipped_sheet_satisfies_dependents(self) -> None:
        """A SKIPPED sheet with error_code=None must release its dependents.

        This pins the existing baton mechanism the skip_when wiring
        relies on: skip → SheetSkipped → SKIPPED(error_code=None) → dependents
        become ready on the next dispatch cycle.
        """
        from marianne.daemon.baton.core import BatonCore
        from marianne.daemon.baton.events import SheetSkipped as _SheetSkipped
        from marianne.daemon.baton.state import BatonSheetStatus

        baton = BatonCore()
        states = {
            1: SheetExecutionState(sheet_num=1, instrument_name="x", max_retries=3),
            2: SheetExecutionState(sheet_num=2, instrument_name="x", max_retries=3),
        }
        baton.register_job("job", states, dependencies={2: [1]})

        # Sheet 2 is initially blocked on sheet 1.
        ready_before = {s.sheet_num for s in baton.get_ready_sheets("job")}
        assert 2 not in ready_before

        # Deliberately skip sheet 1.
        baton._handle_sheet_skipped(_SheetSkipped(job_id="job", sheet_num=1, reason="test"))
        assert states[1].status == BatonSheetStatus.SKIPPED
        assert states[1].error_code is None

        # Sheet 2's dependency is now satisfied → it becomes ready.
        ready_after = {s.sheet_num for s in baton.get_ready_sheets("job")}
        assert 2 in ready_after
