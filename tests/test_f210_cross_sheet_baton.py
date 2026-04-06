"""TDD tests for F-210: Cross-sheet context in the baton path.

The baton path was built to replace the legacy runner but does not replicate
its cross-sheet context pipeline. Templates referencing {{ previous_outputs }}
or {{ previous_files }} render with empty dicts under the baton. This test
suite drives the fix: populate cross-sheet context from completed sheets'
stdout_tail and workspace file patterns.

Architecture:
    1. AttemptContext gains previous_files field (previous_outputs already exists)
    2. BatonAdapter stores CrossSheetConfig per job
    3. BatonAdapter._collect_cross_sheet_context() reads baton state for completed
       sheet outputs and workspace files
    4. _dispatch_callback populates AttemptContext before dispatch
    5. PromptRenderer copies cross-sheet data to SheetContext
    6. Manager passes config.cross_sheet to adapter.register_job()

Found by: Weaver, Movement 3. Fixed by: Canyon, Movement 4.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from marianne.core.config.job import PromptConfig
from marianne.core.config.workspace import CrossSheetConfig
from marianne.core.sheet import Sheet
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.prompt import PromptRenderer
from marianne.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    BatonJobState,
    BatonSheetStatus,
    SheetExecutionState,
)


# =========================================================================
# Fixtures
# =========================================================================


def _make_sheet(
    num: int = 1,
    *,
    prompt_template: str | None = "Sheet {{ sheet_num }}: {{ workspace }}",
    workspace: Path | None = None,
    instrument_name: str = "claude-code",
    movement: int = 1,
    voice: int | None = None,
    voice_count: int = 1,
) -> Sheet:
    """Create a minimal Sheet for testing."""
    return Sheet(
        num=num,
        movement=movement,
        voice=voice,
        voice_count=voice_count,
        workspace=workspace or Path("/tmp/test-workspace"),
        instrument_name=instrument_name,
        prompt_template=prompt_template,
        variables={},
        prelude=[],
        cadenza=[],
        validations=[],
        timeout_seconds=300.0,
    )


def _make_attempt_result(
    *,
    sheet_num: int = 1,
    execution_success: bool = True,
    stdout_tail: str = "",
    cost_usd: float = 0.01,
    duration_seconds: float = 10.0,
) -> SheetAttemptResult:
    """Create a SheetAttemptResult with stdout capture."""
    return SheetAttemptResult(
        job_id="test-job",
        sheet_num=sheet_num,
        instrument_name="claude-code",
        attempt=1,
        execution_success=execution_success,
        validation_pass_rate=100.0 if execution_success else 0.0,
        stdout_tail=stdout_tail,
        cost_usd=cost_usd,
        duration_seconds=duration_seconds,
    )


def _make_execution_state(
    sheet_num: int,
    *,
    status: BatonSheetStatus = BatonSheetStatus.COMPLETED,
    instrument_name: str = "claude-code",
    stdout_tail: str = "",
) -> SheetExecutionState:
    """Create a SheetExecutionState with an attempt result that has stdout."""
    state = SheetExecutionState(
        sheet_num=sheet_num,
        instrument_name=instrument_name,
    )
    state.status = status
    if stdout_tail:
        result = _make_attempt_result(
            sheet_num=sheet_num,
            execution_success=(status == BatonSheetStatus.COMPLETED),
            stdout_tail=stdout_tail,
        )
        state.record_attempt(result)
    return state


# =========================================================================
# 1. AttemptContext — previous_files field
# =========================================================================


class TestAttemptContextPreviousFiles:
    """AttemptContext must carry previous_files for capture_files support."""

    def test_has_previous_files_field(self) -> None:
        """AttemptContext has a previous_files dict field."""
        ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        assert hasattr(ctx, "previous_files")
        assert isinstance(ctx.previous_files, dict)
        assert ctx.previous_files == {}

    def test_previous_files_populated(self) -> None:
        """previous_files can be populated with file path -> content."""
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_files={"/tmp/ws/output.md": "file content here"},
        )
        assert ctx.previous_files == {"/tmp/ws/output.md": "file content here"}

    def test_previous_outputs_still_works(self) -> None:
        """Existing previous_outputs field is unaffected."""
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_outputs={1: "sheet 1 output", 2: "sheet 2 output"},
        )
        assert ctx.previous_outputs == {1: "sheet 1 output", 2: "sheet 2 output"}


# =========================================================================
# 2. BatonAdapter — CrossSheetConfig storage
# =========================================================================


class TestAdapterCrossSheetStorage:
    """Adapter stores and retrieves CrossSheetConfig per job."""

    def test_register_job_stores_cross_sheet_config(self) -> None:
        """register_job accepts cross_sheet parameter and stores it."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1), _make_sheet(num=2)]
        cross_sheet = CrossSheetConfig(auto_capture_stdout=True, lookback_sheets=5)

        adapter.register_job(
            "job-1",
            sheets,
            {2: [1]},
            cross_sheet=cross_sheet,
        )

        # The adapter should store the config (access via internal dict)
        assert "job-1" in adapter._job_cross_sheet
        assert adapter._job_cross_sheet["job-1"].auto_capture_stdout is True
        assert adapter._job_cross_sheet["job-1"].lookback_sheets == 5

    def test_register_job_without_cross_sheet(self) -> None:
        """register_job without cross_sheet stores None."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]

        adapter.register_job("job-1", sheets, {})

        assert adapter._job_cross_sheet.get("job-1") is None

    def test_recover_job_stores_cross_sheet_config(self) -> None:
        """recover_job also accepts and stores cross_sheet."""
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.core.checkpoint import CheckpointState

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        cross_sheet = CrossSheetConfig(auto_capture_stdout=True)
        checkpoint = CheckpointState(
            job_id="test-job", job_name="test", total_sheets=1,
        )

        adapter.recover_job(
            "job-1",
            sheets,
            {},
            checkpoint,
            cross_sheet=cross_sheet,
        )

        assert adapter._job_cross_sheet["job-1"].auto_capture_stdout is True

    def test_deregister_job_cleans_cross_sheet(self) -> None:
        """deregister_job removes cross_sheet config."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=1)]
        cross_sheet = CrossSheetConfig(auto_capture_stdout=True)

        adapter.register_job("job-1", sheets, {}, cross_sheet=cross_sheet)
        assert "job-1" in adapter._job_cross_sheet

        adapter.deregister_job("job-1")
        assert "job-1" not in adapter._job_cross_sheet


# =========================================================================
# 3. Cross-sheet context collection
# =========================================================================


class TestCollectCrossSheetContext:
    """Tests for the adapter's cross-sheet context collection logic."""

    def _setup_adapter_with_job(
        self,
        *,
        cross_sheet: CrossSheetConfig | None = None,
        sheet_states: dict[int, SheetExecutionState] | None = None,
        total_sheets: int = 5,
    ):
        """Set up adapter with a registered job and baton state."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        sheets = [_make_sheet(num=i) for i in range(1, total_sheets + 1)]
        adapter.register_job("job-1", sheets, {}, cross_sheet=cross_sheet)

        # Manually set up baton job state for testing
        if sheet_states:
            job_state = adapter._baton._jobs.get("job-1")
            if job_state:
                for num, state in sheet_states.items():
                    job_state.sheets[num] = state

        return adapter

    def test_no_cross_sheet_config_returns_empty(self) -> None:
        """No CrossSheetConfig → empty previous_outputs and previous_files."""
        adapter = self._setup_adapter_with_job(cross_sheet=None)
        outputs, files = adapter._collect_cross_sheet_context("job-1", 3)
        assert outputs == {}
        assert files == {}

    def test_auto_capture_disabled_returns_empty_outputs(self) -> None:
        """auto_capture_stdout=False → empty previous_outputs."""
        config = CrossSheetConfig(auto_capture_stdout=False)
        adapter = self._setup_adapter_with_job(cross_sheet=config)
        outputs, files = adapter._collect_cross_sheet_context("job-1", 3)
        assert outputs == {}

    def test_auto_capture_collects_completed_sheets(self) -> None:
        """auto_capture_stdout=True → collects stdout from completed sheets."""
        config = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=0
        )
        states = {
            1: _make_execution_state(1, stdout_tail="Output from sheet 1"),
            2: _make_execution_state(2, stdout_tail="Output from sheet 2"),
            3: _make_execution_state(
                3, status=BatonSheetStatus.PENDING, stdout_tail=""
            ),
        }
        adapter = self._setup_adapter_with_job(
            cross_sheet=config, sheet_states=states
        )

        outputs, _ = adapter._collect_cross_sheet_context("job-1", 3)
        assert outputs == {1: "Output from sheet 1", 2: "Output from sheet 2"}

    def test_lookback_limits_sheets(self) -> None:
        """lookback_sheets=2 → only include sheets within the lookback window."""
        config = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=2
        )
        states = {
            1: _make_execution_state(1, stdout_tail="Output 1"),
            2: _make_execution_state(2, stdout_tail="Output 2"),
            3: _make_execution_state(3, stdout_tail="Output 3"),
            4: _make_execution_state(4, stdout_tail="Output 4"),
        }
        adapter = self._setup_adapter_with_job(
            cross_sheet=config, sheet_states=states, total_sheets=5
        )

        # Dispatching sheet 5. lookback=2 means sheets 3 and 4 only.
        outputs, _ = adapter._collect_cross_sheet_context("job-1", 5)
        assert 1 not in outputs
        assert 2 not in outputs
        assert outputs[3] == "Output 3"
        assert outputs[4] == "Output 4"

    def test_lookback_zero_means_all(self) -> None:
        """lookback_sheets=0 → all completed sheets before current."""
        config = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=0
        )
        states = {
            i: _make_execution_state(i, stdout_tail=f"Output {i}")
            for i in range(1, 10)
        }
        adapter = self._setup_adapter_with_job(
            cross_sheet=config, sheet_states=states, total_sheets=10
        )

        outputs, _ = adapter._collect_cross_sheet_context("job-1", 10)
        assert len(outputs) == 9  # sheets 1-9

    def test_max_output_chars_truncates(self) -> None:
        """max_output_chars truncates long outputs."""
        config = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=0, max_output_chars=20
        )
        states = {
            1: _make_execution_state(1, stdout_tail="A" * 100),
        }
        adapter = self._setup_adapter_with_job(
            cross_sheet=config, sheet_states=states
        )

        outputs, _ = adapter._collect_cross_sheet_context("job-1", 2)
        assert len(outputs[1]) < 100
        assert outputs[1].startswith("A" * 20)
        assert "truncated" in outputs[1]

    def test_skipped_sheets_get_placeholder(self) -> None:
        """Skipped sheets inject [SKIPPED] placeholder (#120 parity, F-251)."""
        config = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=0
        )
        states = {
            1: _make_execution_state(1, stdout_tail="Output 1"),
            2: _make_execution_state(
                2, status=BatonSheetStatus.SKIPPED, stdout_tail=""
            ),
        }
        adapter = self._setup_adapter_with_job(
            cross_sheet=config, sheet_states=states
        )

        outputs, _ = adapter._collect_cross_sheet_context("job-1", 3)
        assert 1 in outputs
        assert outputs[2] == "[SKIPPED]"  # F-251: explicit placeholder

    def test_failed_sheets_excluded(self) -> None:
        """Failed sheets without successful stdout are excluded."""
        config = CrossSheetConfig(
            auto_capture_stdout=True, lookback_sheets=0
        )
        states = {
            1: _make_execution_state(1, stdout_tail="Output 1"),
            2: _make_execution_state(
                2, status=BatonSheetStatus.FAILED, stdout_tail=""
            ),
        }
        adapter = self._setup_adapter_with_job(
            cross_sheet=config, sheet_states=states
        )

        outputs, _ = adapter._collect_cross_sheet_context("job-1", 3)
        assert 1 in outputs
        assert 2 not in outputs

    def test_capture_files_reads_workspace_files(self) -> None:
        """capture_files patterns are read from the workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            # Create files matching pattern
            (ws / "sheet-1-output.md").write_text("File output from sheet 1")
            (ws / "sheet-2-output.md").write_text("File output from sheet 2")

            config = CrossSheetConfig(
                capture_files=["{{ workspace }}/sheet-*-output.md"],
            )
            sheets = [
                _make_sheet(num=i, workspace=ws) for i in range(1, 4)
            ]

            from marianne.daemon.baton.adapter import BatonAdapter

            adapter = BatonAdapter()
            adapter.register_job("job-1", sheets, {}, cross_sheet=config)

            _, files = adapter._collect_cross_sheet_context("job-1", 3)
            assert len(files) >= 2
            # Files should contain the workspace file contents
            file_values = list(files.values())
            assert "File output from sheet 1" in file_values
            assert "File output from sheet 2" in file_values

    def test_nonexistent_job_returns_empty(self) -> None:
        """Unknown job_id → graceful empty return."""
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        outputs, files = adapter._collect_cross_sheet_context("nonexistent", 1)
        assert outputs == {}
        assert files == {}


# =========================================================================
# 4. PromptRenderer uses cross-sheet context from AttemptContext
# =========================================================================


class TestPromptRendererCrossSheetContext:
    """PromptRenderer must pass cross-sheet data from AttemptContext to SheetContext."""

    def test_previous_outputs_in_rendered_prompt(self) -> None:
        """previous_outputs from AttemptContext appear in rendered templates."""
        sheet = _make_sheet(
            num=3,
            prompt_template=(
                "Previous: {% for k, v in previous_outputs.items() %}"
                "Sheet {{ k }}: {{ v }}; "
                "{% endfor %}"
            ),
        )
        renderer = PromptRenderer(
            prompt_config=PromptConfig(),
            total_sheets=5,
            total_stages=5,
            parallel_enabled=False,
        )
        context = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_outputs={1: "Result A", 2: "Result B"},
        )

        result = renderer.render(sheet, context)
        assert "Sheet 1: Result A" in result.prompt
        assert "Sheet 2: Result B" in result.prompt

    def test_previous_files_in_rendered_prompt(self) -> None:
        """previous_files from AttemptContext appear in rendered templates."""
        sheet = _make_sheet(
            num=3,
            prompt_template=(
                "Files: {% for path, content in previous_files.items() %}"
                "{{ path }}: {{ content }}; "
                "{% endfor %}"
            ),
        )
        renderer = PromptRenderer(
            prompt_config=PromptConfig(),
            total_sheets=5,
            total_stages=5,
            parallel_enabled=False,
        )
        context = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_files={"/tmp/ws/out.md": "Hello world"},
        )

        result = renderer.render(sheet, context)
        assert "/tmp/ws/out.md: Hello world" in result.prompt

    def test_empty_cross_sheet_context_renders_clean(self) -> None:
        """Empty cross-sheet context renders without errors."""
        sheet = _make_sheet(
            num=1,
            prompt_template="Work on {{ workspace }}. Outputs: {{ previous_outputs }}",
        )
        renderer = PromptRenderer(
            prompt_config=PromptConfig(),
            total_sheets=1,
            total_stages=1,
            parallel_enabled=False,
        )
        context = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)

        result = renderer.render(sheet, context)
        assert "Work on" in result.prompt
        assert "{}" in result.prompt  # empty dict renders as {}

    def test_build_context_populates_cross_sheet_fields(self) -> None:
        """_build_context transfers cross-sheet data from AttemptContext to SheetContext."""
        sheet = _make_sheet(num=3)
        renderer = PromptRenderer(
            prompt_config=PromptConfig(),
            total_sheets=5,
            total_stages=5,
            parallel_enabled=False,
        )
        context = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_outputs={1: "out1", 2: "out2"},
            previous_files={"/tmp/f.md": "content"},
        )

        sheet_context = renderer._build_context(sheet, context)
        assert sheet_context.previous_outputs == {1: "out1", 2: "out2"}
        assert sheet_context.previous_files == {"/tmp/f.md": "content"}
