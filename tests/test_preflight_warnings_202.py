"""#202: warn-only token preflight on the rendered prompt.

The baton dispatched sheets with no preflight; the token-preflight scaffolding
(PreflightConfig thresholds, SheetState.preflight_warnings, E601) existed but had
NO producer. A 4-model thinking-lab (~/lab-archives 2026-06-07) converged on a
warn-only V1: estimate the rendered prompt vs the instrument/model context
window and annotate (never block — the estimate is a char-heuristic, so a wrong
over-estimate must not false-reject a runnable sheet; an actually-oversized
prompt fails loudly at the backend). A configurable hard-fail threshold is a
deliberate, non-retryable follow-on (the #201 healing-enrichment would otherwise
grow an over-limit prompt on every retry).
"""

from __future__ import annotations

from marianne.core.checkpoint import SheetState
from marianne.core.tokens import get_effective_window_size
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _preflight_token_warnings


class TestPreflightHelper:
    def test_small_prompt_no_warning(self) -> None:
        assert _preflight_token_warnings("hello world", "claude-code", None) == []

    def test_oversized_prompt_warns(self) -> None:
        window = get_effective_window_size(instrument="claude-code")
        # ~2x the window in characters (estimate_tokens is char-based) → over.
        big = "x " * window * 4
        warnings = _preflight_token_warnings(big, "claude-code", None)
        assert len(warnings) == 1
        assert "preflight" in warnings[0]
        assert "window" in warnings[0]

    def test_helper_never_raises_or_blocks(self) -> None:
        # Unknown instrument + None model → window falls back; never raises.
        assert isinstance(_preflight_token_warnings("x", "totally-unknown", None), list)
        assert isinstance(_preflight_token_warnings("", None, None), list)


class TestResultCarriesWarnings:
    def test_default_empty(self) -> None:
        r = SheetAttemptResult(job_id="j", sheet_num=1, instrument_name="x", attempt=1)
        assert r.preflight_warnings == []

    def test_carries_warnings(self) -> None:
        r = SheetAttemptResult(
            job_id="j", sheet_num=1, instrument_name="x", attempt=1,
            preflight_warnings=["preflight: big"],
        )
        assert r.preflight_warnings == ["preflight: big"]


class TestSheetStatePropagation:
    def test_record_attempt_folds_warnings(self) -> None:
        sheet = SheetState(sheet_num=1)
        assert sheet.preflight_warnings == []
        sheet.record_attempt(
            SheetAttemptResult(
                job_id="j", sheet_num=1, instrument_name="x", attempt=1,
                preflight_warnings=["preflight: ~99% of window"],
            )
        )
        assert sheet.preflight_warnings == ["preflight: ~99% of window"]

    def test_record_attempt_dedupes_repeated_warning(self) -> None:
        sheet = SheetState(sheet_num=1)
        for attempt in (1, 2):
            sheet.record_attempt(
                SheetAttemptResult(
                    job_id="j", sheet_num=1, instrument_name="x", attempt=attempt,
                    preflight_warnings=["preflight: same warning"],
                )
            )
        assert sheet.preflight_warnings == ["preflight: same warning"]  # not duplicated

    def test_no_warnings_leaves_empty(self) -> None:
        sheet = SheetState(sheet_num=1)
        sheet.record_attempt(
            SheetAttemptResult(job_id="j", sheet_num=1, instrument_name="x", attempt=1)
        )
        assert sheet.preflight_warnings == []


class TestStatusSurfacing:
    def test_status_renders_preflight_warning(self, capsys: object) -> None:
        from marianne.cli.commands.status import _render_preflight_warnings
        from marianne.core.checkpoint import CheckpointState
        from marianne.core.checkpoint import JobStatus as CPJobStatus

        job = CheckpointState(
            job_id="j", job_name="demo", total_sheets=1, status=CPJobStatus.RUNNING,
            sheets={1: SheetState(sheet_num=1, preflight_warnings=["preflight: ~120% of window"])},
        )
        _render_preflight_warnings(job)
        out = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "Preflight Warnings" in out and "120% of window" in out

    def test_status_silent_when_none(self, capsys: object) -> None:
        from marianne.cli.commands.status import _render_preflight_warnings
        from marianne.core.checkpoint import CheckpointState
        from marianne.core.checkpoint import JobStatus as CPJobStatus

        job = CheckpointState(
            job_id="j", job_name="demo", total_sheets=1, status=CPJobStatus.RUNNING,
            sheets={1: SheetState(sheet_num=1)},
        )
        _render_preflight_warnings(job)
        assert "Preflight" not in capsys.readouterr().out  # type: ignore[attr-defined]
