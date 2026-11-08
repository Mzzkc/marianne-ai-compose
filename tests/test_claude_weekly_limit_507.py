"""Claude Code's weekly-limit 429 on stdout is a rate limit (GH #507).

Claude Code `--output-format json` prints an array of stream events whose
terminal ``type: result`` object carries ``is_error: true``,
``api_error_status: 429`` and the message under ``result``; stderr is empty
and the exit code is 1. The Candidate Advantage repair job (7 Nov 2026) got
"You've hit your weekly limit · resets Nov 9, 3am (UTC)" three times and each
attempt was retried as a transient E009: the structured error extractor read
only ``error``/``message`` keys and never the terminal ``result``, and the
profile's patterns needed "hit your limit" or an "(Area/City)" zone.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from marianne.core.config.instruments import InstrumentProfile
from marianne.execution.instruments.cli_backend import (
    PluginCliBackend,
    _structured_error_text,
)

BUILTIN = (
    Path(__file__).resolve().parents[1]
    / "src" / "marianne" / "instruments" / "builtins" / "claude-code.yaml"
)

WEEKLY = "You've hit your weekly limit · resets Nov 9, 3am (UTC)"
WEEKLY_STREAM = json.dumps([
    {"type": "system", "subtype": "init"},
    {"type": "result", "is_error": True, "api_error_status": 429, "result": WEEKLY},
])


def _claude_backend() -> PluginCliBackend:
    profile = InstrumentProfile.model_validate(yaml.safe_load(BUILTIN.read_text()))
    return PluginCliBackend(profile)


class TestTerminalResultIsErrorText:
    def test_array_stream_reduces_to_the_error_result(self) -> None:
        assert WEEKLY in _structured_error_text(WEEKLY_STREAM)

    def test_single_object_error_result(self) -> None:
        obj = json.dumps({"type": "result", "is_error": True, "result": WEEKLY})
        assert WEEKLY in _structured_error_text(obj)

    def test_successful_result_text_is_agent_prose_not_error(self) -> None:
        obj = json.dumps({"type": "result", "is_error": False, "result": WEEKLY})
        assert _structured_error_text(obj) == ""


class TestWeeklyLimitIsRateLimited:
    def test_weekly_limit_stream_is_rate_limited(self) -> None:
        result = _claude_backend()._parse_output(stdout=WEEKLY_STREAM, stderr="", exit_code=1)
        assert result.rate_limited, "the weekly limit on stdout is a rate limit, not E009"
        assert not result.success

    def test_five_hour_limit_with_city_zone_still_matches(self) -> None:
        stream = json.dumps([{
            "type": "result", "is_error": True, "api_error_status": 429,
            "result": "You've hit your limit · resets 9pm (America/Chicago)",
        }])
        result = _claude_backend()._parse_output(stdout=stream, stderr="", exit_code=1)
        assert result.rate_limited

    def test_successful_run_mentioning_limits_is_not_rate_limited(self) -> None:
        stream = json.dumps([{
            "type": "result", "is_error": False,
            "result": "Docs: when you've hit your weekly limit the CLI resets Nov 9, 3am (UTC).",
        }])
        result = _claude_backend()._parse_output(stdout=stream, stderr="", exit_code=0)
        assert result.success and not result.rate_limited

    def test_other_api_refusal_is_not_rate_limited(self) -> None:
        stream = json.dumps([{
            "type": "result", "is_error": True, "api_error_status": 400,
            "result": "tools.0.input_schema: top-level oneOf is not supported",
        }])
        result = _claude_backend()._parse_output(stdout=stream, stderr="", exit_code=1)
        assert not result.rate_limited and result.error_type == "api_error"
