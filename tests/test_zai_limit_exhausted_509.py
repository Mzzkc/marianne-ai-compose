"""The Z.AI gateway's quota refusal is a rate limit on every route it reaches (GH #509).

On a host whose ``claude-code`` profile points ``ANTHROPIC_BASE_URL`` at Z.AI,
and on ``opencode`` with the Z.AI Coding Plan, an exhausted weekly/monthly
plan answers::

    API Error: Server is temporarily limiting requests (not your usage limit) ·
    [1310][Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-11-12 06:24:25]

    Error: Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-11-12 06:24:25

Neither matched any shipped pattern (8 Nov 2026): eight headless repair
musicians were retried as plain failures and an interactive musician's
first-turn refusal screen was nudged five times, then rescued by the previous
pass's files. The vocabulary is a third one beside Anthropic's and OpenAI's.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from marianne.core.config.instruments import InstrumentProfile
from marianne.core.errors.classifier import ErrorClassifier
from marianne.execution.instruments.cli_backend import PluginCliBackend

BUILTINS = Path(__file__).resolve().parents[1] / "src" / "marianne" / "instruments" / "builtins"

ZAI_CLAUDE = (
    "API Error: Server is temporarily limiting requests (not your usage limit) · "
    "[1310][Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-11-12 06:24:25]"
    "[20261109033458785f816d2296457d]"
)
ZAI_OPENCODE = (
    "Error: Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-11-12 06:24:25"
)


def _profile(name: str) -> InstrumentProfile:
    return InstrumentProfile.model_validate(yaml.safe_load((BUILTINS / f"{name}.yaml").read_text()))


class TestClaudeCodeRoute:
    def test_zai_refusal_in_result_stream_is_rate_limited(self) -> None:
        stream = json.dumps([
            {"type": "system", "subtype": "init"},
            {"type": "result", "is_error": True, "api_error_status": 429, "result": ZAI_CLAUDE},
        ])
        result = PluginCliBackend(_profile("claude-code"))._parse_output(
            stdout=stream, stderr="", exit_code=1,
        )
        assert result.rate_limited and not result.success

    def test_zai_refusal_on_stderr_is_rate_limited(self) -> None:
        result = PluginCliBackend(_profile("claude-code"))._parse_output(
            stdout="", stderr=ZAI_CLAUDE, exit_code=1,
        )
        assert result.rate_limited

    def test_interactive_screen_patterns_recognise_the_zai_screen(self) -> None:
        interactive = _profile("claude-code").cli.interactive  # type: ignore[union-attr]
        assert interactive is not None
        screen = "●  " + ZAI_CLAUDE + "\n\n❯ "
        assert any(
            re.search(p, screen, re.IGNORECASE)
            for p in interactive.rate_limit_screen_patterns
        ), "a first-turn quota screen must be a rate limit, not an idle agent to nudge"

    def test_prose_about_limits_in_a_successful_run_is_not_rate_limited(self) -> None:
        stream = json.dumps([{
            "type": "result", "is_error": False,
            "result": "Note: Z.AI prints 'Limit Exhausted' when the plan is spent.",
        }])
        result = PluginCliBackend(_profile("claude-code"))._parse_output(
            stdout=stream, stderr="", exit_code=0,
        )
        assert result.success and not result.rate_limited


class TestOpencodeRoute:
    def test_zai_plan_refusal_is_rate_limited(self) -> None:
        result = PluginCliBackend(_profile("opencode"))._parse_output(
            stdout="", stderr=ZAI_OPENCODE, exit_code=1,
        )
        assert result.rate_limited and not result.success


class TestResetTime:
    def test_iso_reset_time_is_parsed_and_clamped_to_a_day(self) -> None:
        classifier = ErrorClassifier()
        soon = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        wait = classifier.parse_reset_time(f"Limit Exhausted. Your limit will reset at {soon}")
        assert wait is not None and 7000 <= wait <= 7300
        far = classifier.parse_reset_time("Your limit will reset at 2099-01-01 00:00:00")
        assert far == 86400.0, "a multi-day reset is capped at the maximum wait"
