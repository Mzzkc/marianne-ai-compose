"""Functional review of the local Claude profile through real child processes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from marianne.core.tokens import get_effective_window_size
from marianne.daemon.baton.musician import _preflight_token_warnings
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.instruments.availability import probe_profile
from marianne.instruments.loader import InstrumentProfileLoader

BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"


@pytest.fixture
def local_profile():
    # Loader results are cached: review fixtures must never mutate shared profiles.
    return InstrumentProfileLoader.load_directory(BUILTINS)["claude-code-ollama"].model_copy(
        deep=True,
    )


@pytest.mark.parametrize("model", ["llama3.1:8b", "qwen3:8b"])
async def test_local_child_receives_large_prompt_and_pinned_route(
    tmp_path, monkeypatch, local_profile, model,
) -> None:
    """Launch a deterministic CLI stand-in using the shipped stdin/env contract."""
    child = tmp_path / "claude-stand-in"
    child.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, json, os, sys\n"
        "prompt = sys.stdin.buffer.read()\n"
        "snapshot = {'argv': sys.argv[1:], 'size': len(prompt),\n"
        "    'digest': hashlib.sha256(prompt).hexdigest(),\n"
        "    'base': os.environ.get('ANTHROPIC_BASE_URL'),\n"
        "    'auth': os.environ.get('ANTHROPIC_AUTH_TOKEN'),\n"
        "    'key': os.environ.get('ANTHROPIC_API_KEY'),\n"
        "    'cloud': os.environ.get('CLAUDE_CODE_USE_BEDROCK')}\n"
        "print(json.dumps({'result': json.dumps(snapshot), 'is_error': False,\n"
        "    'usage': {'input_tokens': 11, 'output_tokens': 7}}))\n"
    )
    child.chmod(0o700)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://unused.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-parent-key")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    local_profile.cli.command.executable = str(child)
    backend = PluginCliBackend(local_profile, working_directory=tmp_path)
    backend.apply_overrides({"model": model})
    prompt = "Long local prompt λ\n" * 7000
    try:
        result = await backend.execute(prompt, timeout_seconds=10)
        assert result.success, result.error_message
        assert result.model == model
        assert (result.input_tokens, result.output_tokens) == (11, 7)
        received = json.loads(result.stdout)
        assert received["size"] == len(prompt.encode())
        assert received["digest"] == hashlib.sha256(prompt.encode()).hexdigest()
        argv = received["argv"]
        assert argv[argv.index("--model") + 1] == model
        assert argv[argv.index("-p") + 1] == "-"
        assert argv[argv.index("--output-format") + 1] == "json"
        assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
        assert "--strict-mcp-config" in argv
        assert "--effort" not in argv
        assert prompt not in argv
        assert received["base"] == "http://localhost:11434"
        assert received["auth"] == "local"
        assert received["key"] == ""
        assert received["cloud"] is None
    finally:
        backend.clear_overrides()
    restored = backend._build_command("next", timeout_seconds=None)
    assert restored[restored.index("--model") + 1] == "llama3.1:8b"
    assert InstrumentProfileLoader.load_directory(BUILTINS)[
        "claude-code-ollama"
    ].cli.command.executable == "claude"


@pytest.mark.parametrize(
    ("message", "expected_type", "rate_limited"),
    [
        ("unauthorized", "auth", False),
        ("API Error: Rate limit status 429", None, True),
        ("API service overloaded 503", "capacity", True),
        ("Connection timeout", "timeout", False),
    ],
)
def test_local_endpoint_failures_are_not_success(
    local_profile, message, expected_type, rate_limited,
) -> None:
    backend = PluginCliBackend(local_profile)
    result = backend._parse_output("", message, exit_code=1)
    assert not result.success
    assert result.rate_limited is rate_limited
    assert result.error_type == expected_type


def test_missing_local_model_surfaces_api_refusal(local_profile) -> None:
    backend = PluginCliBackend(local_profile)
    result = backend._parse_output(
        json.dumps({"type": "result", "is_error": True,
                    "result": "model 'llama3.1:8b' not found", "api_error_status": 404}),
        "", exit_code=1,
    )
    assert not result.success
    assert result.error_type == "api_error"
    assert "404" in result.error_message
    assert "not found" in result.error_message
    assert not result.rate_limited


def test_schema_command_and_fail_closed_output(local_profile) -> None:
    backend = PluginCliBackend(local_profile)
    schema = {"type": "json_schema", "json_schema": {
        "name": "local_result", "schema": {
            "type": "object", "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"], "additionalProperties": False,
        },
    }}
    argv = backend._build_command("work", timeout_seconds=None, response_format=schema)
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema["json_schema"]["schema"]
    assert "--effort" not in argv
    terminal = {"type": "result", "result": "ignored", "structured_output": {"ok": True}}
    result = backend._parse_output(
        json.dumps([{"type": "system"}, terminal]), "", exit_code=0, response_format=schema,
    )
    assert result.success
    assert result.structured_output == {"ok": True}
    terminal.pop("structured_output")
    refused = backend._parse_output(
        json.dumps(terminal), "", exit_code=0, response_format=schema,
    )
    assert not refused.success
    assert refused.error_type == "schema_output"
    assert refused.stdout == ""


@pytest.mark.parametrize("binary", [None, "/fake/bin/claude"])
def test_cli_availability_is_binary_only(local_profile, monkeypatch, binary) -> None:
    monkeypatch.setattr("marianne.instruments.availability.shutil.which", lambda _: binary)
    probe = probe_profile(local_profile)
    assert probe.state == ("ready" if binary else "unavailable")
    if not binary:
        assert "claude" in probe.reason


def test_qwen_selected_capacity_limits_context_advice(local_profile) -> None:
    model = next(model for model in local_profile.models if model.name == "qwen3:8b")
    window = get_effective_window_size(model=model.name, instrument=local_profile.name)
    # A per-model ceiling must win over the new 128K per-instrument default.
    assert window <= model.context_window, (window, model.context_window)
    warnings = _preflight_token_warnings("x" * 175_000, local_profile.name, model.name)
    assert warnings, "A ~50K-token prompt exceeds Qwen's declared 40K context"
