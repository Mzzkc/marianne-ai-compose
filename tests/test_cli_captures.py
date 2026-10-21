"""Replay CLI output captured from the installed CLIs through the real parser.

GH #472: the CLI JSON-schema parser assumed one JSON object for months
because its test fixture (`_claude_terminal_output`) was written from the
docs, not captured from the CLI. These fixtures were recorded with
`mzt instruments check claude-code --live --record tests/fixtures/cli_captures`
and reduced to the events the parser reads (each `.json` sidecar names the
CLI version and what was dropped). Re-record after a CLI upgrade; a test
here failing then means the real output shape moved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marianne.daemon.baton.musician import _classify_error
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.instruments.loader import InstrumentProfileLoader, profile_source_dirs

CAPTURES = Path(__file__).resolve().parent / "fixtures" / "cli_captures"


def _backend() -> PluginCliBackend:
    profile = InstrumentProfileLoader.load_directory(profile_source_dirs()[0])["claude-code"]
    return PluginCliBackend(profile)


def _replay(capture: str, mode: str):
    meta = json.loads((CAPTURES / capture / f"{mode}.json").read_text())
    stdout = (CAPTURES / capture / f"{mode}.stdout").read_text()
    stderr = (CAPTURES / capture / f"{mode}.stderr").read_text()
    return meta, _backend()._parse_output(
        stdout, stderr, exit_code=meta["exit_code"],
        response_format=meta["response_format"],
    )


def test_captures_name_their_cli_version() -> None:
    metas = [json.loads(p.read_text()) for p in CAPTURES.rglob("*.json")]
    assert metas, "no captures recorded"
    assert all(m.get("cli_version") not in (None, "", "unknown") for m in metas)


@pytest.mark.parametrize("capture", ["claude-code-sonnet", "claude-code-glm53-no-tool"])
def test_real_plain_output_yields_the_result_text(capture: str) -> None:
    _meta, result = _replay(capture, "plain")
    assert result.success is True
    assert result.stdout.strip().lower().rstrip(".") == "ok"


def test_real_schema_output_yields_the_validated_object() -> None:
    _meta, result = _replay("claude-code-sonnet", "schema")
    assert result.success is True, result.error_message
    assert json.loads(result.stdout) == {"n": 7}


def test_real_schema_miss_is_a_refusal_not_unschematized_text() -> None:
    """Recorded 2026-10-21 on the GLM-5.3 route: the model could not find the
    StructuredOutput tool and answered in prose with a JSON code block. The
    parser must refuse (never pass the prose off as the schema result), and the
    baton must retry it on the same entry, not abandon the instrument."""
    _meta, result = _replay("claude-code-glm53-no-tool", "schema")
    assert result.success is False
    assert result.stdout == ""
    assert "structured_output" in (result.error_message or "")
    assert _classify_error(result).classification == "EXECUTION_ERROR"


def test_real_api_refusal_keeps_its_witness_and_is_not_transient() -> None:
    """Recorded 2026-10-21 (GH #486): the API refused a tool input_schema with a
    top-level oneOf (400). The CLI's terminal result carries is_error, the
    status and the message; the attempt must keep that message as its error
    and its stderr tail, and the baton must not retry it as weather."""
    _meta, result = _replay("claude-code-api-400-oneof", "schema")
    assert result.success is False
    assert result.stdout == ""
    assert result.error_type == "api_error"
    assert "input_schema does not support oneOf" in (result.error_message or "")
    assert "input_schema does not support oneOf" in result.stderr
    classified = _classify_error(result)
    assert classified.classification == "EXECUTION_ERROR"
    assert classified.error_code == "E301"
