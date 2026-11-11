"""GH #533: interactive sheets must report token usage, never a confident zero."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from marianne.core.config.instruments import InstrumentProfile, InteractiveUsageConfig
from marianne.execution.base import ExecutionResult
from marianne.execution.instruments.interactive.backend import InteractiveCliBackend
from marianne.execution.instruments.interactive.usage import (
    apply_usage,
    read_session_usage,
    read_usage_file,
)
from tests.test_interactive_mode import (
    READY_SCREEN,
    FakeTmux,
    make_interactive_config,
    make_profile,
)

BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"


def usage_config(record_path: str = "{session_id}.jsonl") -> InteractiveUsageConfig:
    return InteractiveUsageConfig(
        session_id_flag="--session-id",
        record_path=record_path,
        record_filter={"type": "assistant"},
        dedupe_key_path="message.id",
        model_path="message.model",
        ignore_models=["<synthetic>"],
        input_tokens_path="message.usage.input_tokens",
        output_tokens_path="message.usage.output_tokens",
        cache_read_tokens_path="message.usage.cache_read_input_tokens",
        cache_write_tokens_path="message.usage.cache_creation_input_tokens",
    )


def line(mid: str, i: int, o: int, cr: int, cw: int, model: str = "m-1", **extra: Any) -> str:
    rec: dict[str, Any] = {
        "type": "assistant",
        "message": {
            "id": mid, "model": model,
            "usage": {
                "input_tokens": i, "output_tokens": o,
                "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
            },
        },
    }
    rec.update(extra)
    return json.dumps(rec)


def write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestReader:
    def test_repeated_ids_count_once_using_last_record(self, tmp_path: Path) -> None:
        f = write(tmp_path / "s.jsonl", [
            line("a", 10, 1, 100, 5), line("a", 10, 40, 100, 5),
            line("b", 20, 2, 200, 0),
        ])
        r = read_usage_file(f, usage_config())
        assert (r.responses, r.input_tokens, r.output_tokens) == (2, 30, 42)
        assert (r.cache_read_tokens, r.cache_write_tokens) == (300, 5)
        assert r.measured and r.model == "m-1"

    def test_synthetic_malformed_and_non_assistant_lines_skipped(self, tmp_path: Path) -> None:
        f = write(tmp_path / "s.jsonl", [
            "{not json", '{"type": "user", "message": {"id": "u"}}', "[1,2]",
            line("x", 5, 5, 0, 0, model="<synthetic>"),
            line("a", 7, 3, 0, 0),
        ])
        r = read_usage_file(f, usage_config())
        assert (r.responses, r.input_tokens, r.output_tokens) == (1, 7, 3)

    def test_sidechain_lines_in_same_file_are_counted(self, tmp_path: Path) -> None:
        f = write(tmp_path / "s.jsonl", [
            line("a", 1, 1, 0, 0), line("sub", 2, 2, 0, 0, isSidechain=True),
        ])
        assert read_usage_file(f, usage_config()).input_tokens == 3

    def test_empty_record_is_unmeasured(self, tmp_path: Path) -> None:
        f = write(tmp_path / "s.jsonl", ["{bad"])
        r = read_usage_file(f, usage_config())
        assert not r.measured and r.reason

    def test_missing_file_is_unmeasured(self, tmp_path: Path) -> None:
        r = read_session_usage(usage_config(str(tmp_path / "{session_id}.jsonl")), "nope")
        assert not r.measured and "not found" in (r.reason or "")

    def test_no_declared_block_is_unmeasured(self) -> None:
        assert not read_session_usage(None, None).measured

    def test_apply_marks_unmeasured_without_inventing_zero(self) -> None:
        res = ExecutionResult(success=True, stdout="", stderr="", duration_seconds=1.0)
        apply_usage(res, read_session_usage(None, None))
        assert res.usage_unmeasured is True
        assert res.input_tokens is None and res.output_tokens is None

    def test_model_validation(self) -> None:
        with pytest.raises(ValueError):
            InteractiveUsageConfig(session_id_flag="--s", record_path="x/{session_id}")
        with pytest.raises(ValueError):
            InteractiveUsageConfig(
                session_id_flag="--s", record_path="x", output_tokens_path="a",
            )


def _profile(tmp_path: Path, with_usage: bool) -> InstrumentProfile:
    cfg = make_interactive_config(
        usage=usage_config(str(tmp_path / "rec" / "{session_id}.jsonl")) if with_usage else None,
    )
    return make_profile(interactive=cfg)


class TestBackendWiring:
    async def test_session_id_passed_and_tokens_filled_on_failed_attempt(
        self, tmp_path: Path,
    ) -> None:
        fake = FakeTmux([READY_SCREEN])
        backend = InteractiveCliBackend(
            _profile(tmp_path, True), working_directory=tmp_path,
            tmux=fake,  # type: ignore[arg-type]
        )
        backend.configure_interactive(max_nudges=1)
        orig = fake.new_session

        async def new_session(session: str, command: list[str], **kw: Any) -> None:
            sid = command[command.index("--session-id") + 1]
            (tmp_path / "rec").mkdir(exist_ok=True)
            write(tmp_path / "rec" / f"{sid}.jsonl", [line("a", 4, 6, 8, 2)])
            await orig(session, command, **kw)

        fake.new_session = new_session  # type: ignore[method-assign]
        result = await backend.execute("do it", timeout_seconds=5.0)
        assert result.success is False
        assert (result.input_tokens, result.output_tokens) == (4, 6)
        assert (result.cache_read_tokens, result.cache_write_tokens) == (8, 2)
        assert result.model_observed == "m-1"
        assert result.usage_unmeasured is False

    async def test_undeclared_profile_is_unmeasured_not_zero(self, tmp_path: Path) -> None:
        fake = FakeTmux([READY_SCREEN])
        backend = InteractiveCliBackend(
            _profile(tmp_path, False), working_directory=tmp_path,
            tmux=fake,  # type: ignore[arg-type]
        )
        backend.configure_interactive(max_nudges=1)
        result = await backend.execute("do it", timeout_seconds=5.0)
        assert "--session-id" not in fake.created[0][1]
        assert result.usage_unmeasured is True and result.input_tokens is None

    async def test_declared_but_missing_record_is_unmeasured(self, tmp_path: Path) -> None:
        fake = FakeTmux([READY_SCREEN])
        backend = InteractiveCliBackend(
            _profile(tmp_path, True), working_directory=tmp_path,
            tmux=fake,  # type: ignore[arg-type]
        )
        backend.configure_interactive(max_nudges=1)
        result = await backend.execute("do it", timeout_seconds=5.0)
        assert "--session-id" in fake.created[0][1]
        assert result.usage_unmeasured is True


def test_unmeasured_attempt_is_cost_uncertain_even_with_pricing() -> None:
    from marianne.daemon.baton.musician import _attempt_cost_uncertain

    unmeasured = ExecutionResult(
        success=True, stdout="", stderr="", duration_seconds=1.0, usage_unmeasured=True,
    )
    measured = ExecutionResult(
        success=True, stdout="", stderr="", duration_seconds=1.0, input_tokens=5,
    )
    assert _attempt_cost_uncertain(unmeasured, 0.003, 0.015) is True
    assert _attempt_cost_uncertain(measured, 0.003, 0.015) is False
    assert _attempt_cost_uncertain(measured, None, None) is True


def test_unmeasured_flag_reaches_status_sheet_state() -> None:
    from marianne.core.checkpoint import SheetState
    from marianne.daemon.baton.events import SheetAttemptResult

    sheet = SheetState(sheet_num=1)
    sheet.record_attempt(SheetAttemptResult(
        job_id="j", sheet_num=1, instrument_name="x", attempt=1,
        cost_usd=0.0, cost_uncertain=True,
    ))
    assert sheet.cost_uncertain is True


def test_builtin_claude_code_declares_usage_and_others_do_not() -> None:
    data = yaml.safe_load((BUILTINS / "claude-code.yaml").read_text())
    profile = InstrumentProfile.model_validate(data)
    assert profile.cli is not None and profile.cli.interactive is not None
    usage = profile.cli.interactive.usage
    assert usage is not None and usage.session_id_flag == "--session-id"
    assert usage.dedupe_key_path == "message.id"
    for other in ("antigravity.yaml", "gemini-cli.yaml", "codex-cli.yaml"):
        d = InstrumentProfile.model_validate(yaml.safe_load((BUILTINS / other).read_text()))
        if d.cli is not None and d.cli.interactive is not None:
            assert d.cli.interactive.usage is None
