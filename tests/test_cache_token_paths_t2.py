"""Prompt-cache token counts flow from the harness to the checkpoint (token-efficiency T2).

Before this, Marianne read only ``input_tokens`` and ``output_tokens``; on
Anthropic and OpenAI those exclude the tokens served from the prompt cache,
which a long agentic sheet re-reads every turn. One 23-turn Claude Code sheet
on 2026-11-09 was 139,198 input and 1,025,216 cache-read tokens, so the number
Marianne recorded was 12% of what the provider processed. The job-level totals
were also never summed, so ``mzt status`` printed 0 for every run.
"""

from __future__ import annotations

import json
from typing import Literal

from marianne.core.checkpoint import CheckpointState, SheetState
from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _profile(fmt: Literal["json", "jsonl"], **paths: str) -> InstrumentProfile:
    output = CliOutputConfig(
        format=fmt,
        result_path="result",
        input_tokens_path="usage.input_tokens",
        output_tokens_path="usage.output_tokens",
        completion_event_type="result" if fmt == "jsonl" else None,
        cache_read_tokens_path=paths.get("cache_read_tokens_path"),
        cache_write_tokens_path=paths.get("cache_write_tokens_path"),
    )
    return InstrumentProfile(
        name="fake",
        display_name="Fake",
        kind="cli",
        description="t",
        cli=CliProfile(
            command=CliCommand(executable="x", prompt_flag="-p"),
            output=output,
            errors=CliErrorConfig(),
        ),
        models=[],
    )


ANTHROPIC = {
    "cache_read_tokens_path": "usage.cache_read_input_tokens",
    "cache_write_tokens_path": "usage.cache_creation_input_tokens",
}


def test_json_output_reads_cache_counts() -> None:
    backend = PluginCliBackend(_profile("json", **ANTHROPIC))
    out = json.dumps({
        "result": "ok",
        "usage": {"input_tokens": 602, "cache_creation_input_tokens": 7,
                  "cache_read_input_tokens": 54848, "output_tokens": 508},
    })
    result = backend._parse_output(out, "", exit_code=0)
    assert (result.input_tokens, result.output_tokens) == (602, 508)
    assert (result.cache_read_tokens, result.cache_write_tokens) == (54848, 7)


def test_jsonl_output_reads_cache_counts_from_any_event() -> None:
    backend = PluginCliBackend(_profile("jsonl", **ANTHROPIC))
    lines = [
        json.dumps({"type": "assistant", "usage": {"cache_read_input_tokens": 100}}),
        json.dumps({"type": "result", "result": "done",
                    "usage": {"input_tokens": 5, "output_tokens": 3,
                              "cache_read_input_tokens": 900,
                              "cache_creation_input_tokens": 0}}),
    ]
    result = backend._parse_output("\n".join(lines), "", exit_code=0)
    assert result.stdout == "done"
    # First event with the counter wins, as for input/output tokens.
    assert result.cache_read_tokens == 100
    assert result.cache_write_tokens == 0


def test_profile_without_cache_paths_reports_none_not_zero() -> None:
    backend = PluginCliBackend(_profile("json"))
    out = json.dumps({"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1,
                                                  "cache_read_input_tokens": 999}})
    result = backend._parse_output(out, "", exit_code=0)
    assert result.cache_read_tokens is None
    assert result.cache_write_tokens is None


def _attempt(num: int, **tokens: int) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j", sheet_num=num, instrument_name="fake", attempt=1,
        execution_success=True, validations_total=0, validations_passed=0,
        validation_pass_rate=100.0, **tokens,
    )


def test_sheet_sums_tokens_over_attempts_and_status_sums_sheets() -> None:
    sheet = SheetState(sheet_num=1, status="pending")
    sheet.record_attempt(_attempt(1, input_tokens=10, output_tokens=2,
                                  cache_read_tokens=100, cache_write_tokens=1))
    sheet.record_attempt(_attempt(1, input_tokens=5, output_tokens=1,
                                  cache_read_tokens=50, cache_write_tokens=0))
    assert (sheet.total_input_tokens, sheet.total_output_tokens) == (15, 3)
    assert (sheet.total_cache_read_tokens, sheet.total_cache_write_tokens) == (150, 1)

    from marianne.cli.commands.status import _job_token_totals

    job = CheckpointState(job_id="j", job_name="t", total_sheets=1)
    job.sheets = {1: sheet}
    totals = _job_token_totals(job)
    assert totals == {"input": 15, "output": 3, "cache_read": 150, "cache_write": 1}


def test_old_checkpoint_without_cache_fields_loads_as_zero() -> None:
    data = {"sheet_num": 3, "status": "completed", "input_tokens": 7}
    sheet = SheetState.model_validate(data)
    assert sheet.total_cache_read_tokens == 0
    assert sheet.total_input_tokens == 0  # attempt totals start at the next attempt
