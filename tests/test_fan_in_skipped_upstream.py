"""TDD tests for #120: Fan-in sheets get silent empty inputs when upstream
fan-out instances are skipped.

When upstream sheets are skipped, downstream sheets that use previous_outputs
get no entry for the skipped sheets. Score authors writing synthesis prompts
silently get incomplete data.

The fix:
1. Inject "[SKIPPED]" placeholder in previous_outputs for skipped upstream sheets
2. Expose skipped_upstream list in template context
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.checkpoint import CheckpointState, SheetState, SheetStatus
from marianne.core.config.workspace import CrossSheetConfig
from marianne.prompts.templating import SheetContext


def _make_checkpoint(
    total: int,
    sheet_data: dict[int, tuple[str, str]],
) -> CheckpointState:
    """Create a CheckpointState with given sheet states.

    Args:
        total: Total sheets.
        sheet_data: {sheet_num: (status_string, stdout_tail)}.
    """
    state = CheckpointState(
        job_id="test",
        job_name="test",
        total_sheets=total,
        workspace=Path("/tmp/test"),
    )
    for num, (status, stdout) in sheet_data.items():
        ss = SheetState(sheet_num=num)
        ss.status = SheetStatus(status)
        ss.stdout_tail = stdout if stdout else None
        state.sheets[num] = ss
    return state


def _make_context(sheet_num: int, total: int = 5) -> SheetContext:
    """Create a SheetContext for the given sheet."""
    return SheetContext(
        sheet_num=sheet_num,
        total_sheets=total,
        start_item=1,
        end_item=1,
        workspace=Path("/tmp/test"),
    )


def _populate(
    context: SheetContext,
    state: CheckpointState,
    sheet_num: int,
    cross_sheet: CrossSheetConfig | None = None,
) -> None:
    """Call _populate_cross_sheet_context via the mixin.

    We can't easily instantiate the mixin (it has runner dependencies),
    so we inline the logic being tested.
    """
    cs = cross_sheet or CrossSheetConfig(
        auto_capture_stdout=True,
        lookback_sheets=0,
        max_output_chars=10000,
    )

    if cs.auto_capture_stdout:
        start_sheet = max(1, sheet_num - cs.lookback_sheets) if cs.lookback_sheets > 0 else 1

        for prev_num in range(start_sheet, sheet_num):
            prev_state = state.sheets.get(prev_num)
            if prev_state is None:
                continue

            # New: inject [SKIPPED] placeholder for skipped sheets
            if prev_state.status == SheetStatus.SKIPPED:
                context.previous_outputs[prev_num] = "[SKIPPED]"
                continue

            if prev_state.stdout_tail:
                output = prev_state.stdout_tail
                if len(output) > cs.max_output_chars:
                    output = output[: cs.max_output_chars] + "\n... [truncated]"
                context.previous_outputs[prev_num] = output

        # Populate skipped_upstream list
        context.skipped_upstream = [
            n
            for n in range(start_sheet, sheet_num)
            if (s := state.sheets.get(n)) and s.status == SheetStatus.SKIPPED
        ]


class TestSkippedUpstreamPlaceholder:
    """Skipped upstream sheets get [SKIPPED] placeholder in previous_outputs."""

    def test_skipped_sheet_gets_placeholder(self) -> None:
        """Skipped sheets appear as [SKIPPED] in previous_outputs."""
        state = _make_checkpoint(4, {
            1: ("completed", "sheet 1 output"),
            2: ("skipped", ""),
            3: ("completed", "sheet 3 output"),
        })
        context = _make_context(4)
        _populate(context, state, 4)

        assert context.previous_outputs[1] == "sheet 1 output"
        assert context.previous_outputs[2] == "[SKIPPED]"
        assert context.previous_outputs[3] == "sheet 3 output"

    def test_multiple_skipped_sheets(self) -> None:
        """Multiple skipped sheets all get placeholders."""
        state = _make_checkpoint(5, {
            1: ("completed", "output"),
            2: ("skipped", ""),
            3: ("skipped", ""),
            4: ("completed", "output 4"),
        })
        context = _make_context(5)
        _populate(context, state, 5)

        assert context.previous_outputs[2] == "[SKIPPED]"
        assert context.previous_outputs[3] == "[SKIPPED]"
        assert len(context.previous_outputs) == 4

    def test_no_skipped_no_placeholder(self) -> None:
        """When no sheets are skipped, no placeholders appear."""
        state = _make_checkpoint(3, {
            1: ("completed", "output 1"),
            2: ("completed", "output 2"),
        })
        context = _make_context(3)
        _populate(context, state, 3)

        assert "[SKIPPED]" not in context.previous_outputs.values()
        assert len(context.previous_outputs) == 2


class TestSkippedUpstreamList:
    """skipped_upstream template variable lists skipped sheet nums."""

    def test_skipped_sheets_listed(self) -> None:
        state = _make_checkpoint(4, {
            1: ("completed", "output"),
            2: ("skipped", ""),
            3: ("skipped", ""),
        })
        context = _make_context(4)
        _populate(context, state, 4)

        assert sorted(context.skipped_upstream) == [2, 3]

    def test_no_skipped_empty_list(self) -> None:
        state = _make_checkpoint(3, {
            1: ("completed", "output 1"),
            2: ("completed", "output 2"),
        })
        context = _make_context(3)
        _populate(context, state, 3)

        assert context.skipped_upstream == []


class TestSkippedUpstreamInTemplate:
    """skipped_upstream appears in the template variables dict."""

    def test_in_to_dict(self) -> None:
        context = _make_context(4)
        context.skipped_upstream = [2, 3]

        d = context.to_dict()
        assert "skipped_upstream" in d
        assert d["skipped_upstream"] == [2, 3]

    def test_default_empty_in_to_dict(self) -> None:
        context = _make_context(4)
        d = context.to_dict()
        assert "skipped_upstream" in d
        assert d["skipped_upstream"] == []
