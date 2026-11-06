"""Codex `--json` reports its quota refusal on stdout, not stderr (6 Nov 2026).

The clamonacc proof's characterize loop ran eight no-op passes in six seconds:
each `codex exec --json` ended with one JSONL record carrying
`codex_error_info: usage_limit_exceeded`, exit 1, stderr empty. The stderr-only
rate-limit scan (GH #189) saw nothing, the plain exit was rescued through the
sheet's validations (which an inherited branch satisfied), and the loop burned
its iteration cap. For a structured-output profile the terminal error record
is the error channel.
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
    / "src" / "marianne" / "instruments" / "builtins" / "codex-cli.yaml"
)

QUOTA_RECORD = json.dumps({
    "type": "task_complete",
    "last_agent_message": None,
    "error": {
        "message": (
            "You’ve hit your usage limit. Visit https://chatgpt.com/settings/usage "
            "to purchase more credits or try again at Nov 11th, 2026 4:24 PM."
        ),
        "codex_error_info": "usage_limit_exceeded",
    },
})


def _codex_backend() -> PluginCliBackend:
    profile = InstrumentProfile.model_validate(yaml.safe_load(BUILTIN.read_text()))
    return PluginCliBackend(profile)


class TestStructuredErrorText:
    def test_reads_the_last_jsonl_record(self) -> None:
        prose = json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "rate limit talk"}}
        )
        stdout = prose + "\n" + QUOTA_RECORD
        text = _structured_error_text(stdout)
        assert "usage_limit_exceeded" in text and "hit your usage limit" in text
        assert "rate limit talk" not in text

    def test_agent_prose_is_not_error_text(self) -> None:
        stdout = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "hit your usage limit"},
            }
        )
        assert _structured_error_text(stdout) == ""

    def test_non_json_is_empty(self) -> None:
        assert _structured_error_text("plain text\nmore") == ""
        assert _structured_error_text("") == ""


class TestCodexQuotaOnStdout:
    def test_quota_record_on_stdout_is_rate_limited(self) -> None:
        result = _codex_backend()._parse_output(stdout=QUOTA_RECORD, stderr="", exit_code=1)
        assert result.rate_limited, "the --json quota record is a rate limit"
        assert not result.success

    def test_success_with_quota_prose_in_agent_output_is_not(self) -> None:
        stdout = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "docs: when you hit your usage limit"},
        })
        result = _codex_backend()._parse_output(stdout=stdout, stderr="", exit_code=0)
        assert result.success and not result.rate_limited

    def test_stderr_quota_message_still_detected(self) -> None:
        result = _codex_backend()._parse_output(
            stdout="",
            stderr="■ You've hit your usage limit. Upgrade to Pro, purchase more credits "
            "or try again at 6:15 AM.",
            exit_code=1,
        )
        assert result.rate_limited
