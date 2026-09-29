"""Opt-in ``--json-schema`` support for the Claude Code CLI seat.

Contracts under test (2026-09-29, runtime-cli-schema-20260929):

- A profile declares schema capability through
  ``cli.command.json_schema_flag`` (None = the instrument cannot enforce a
  schema; the generic flag-mapping idiom of ``CliCommand``).
- A sheet's ``instrument_config.response_format`` (type ``json_schema``)
  rides ``SheetRequestState`` request-locally: the backend appends the
  profile's flag plus the compact JSON schema argument, and the terminal
  JSON's top-level ``structured_output`` object becomes the sheet result.
- Absent request state preserves the EXACT legacy command bytes and the
  ordinary ``result_path`` parsing.
- Refusals are LOUD (ValueError before spawn) — never log-and-ignore:
  profile without ``json_schema_flag``, ``json_object``-type requests
  (a CLI flag cannot enforce a bare json_object), non-JSON output
  profiles, and invalid schema shapes.
- Malformed/nonconforming output (missing or non-object
  ``structured_output``) fails the sheet with a content-free error.
- No leak: a later plain call on the same pooled backend, concurrent
  schema/plain A-B sheets, and release-time reset.

All subprocess I/O is faked: commands are captured through a patched
``asyncio.create_subprocess_exec``; no live CLI is invoked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import provenance self-check: this suite must bind to THIS tree's
# marianne (the repo .venv may carry a non-relocatable editable .pth
# pointing at another checkout — see run report 2026-09-29).
import marianne.core.config.instruments as _prov
from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.base import SheetRequestState
from marianne.execution.instruments.cli_backend import PluginCliBackend

assert Path(_prov.__file__).resolve().is_relative_to(
    Path(__file__).resolve().parents[1] / "src"
), f"import provenance drift: {_prov.__file__} is not this tree"

JSON_SCHEMA_RF = {
    "type": "json_schema",
    "json_schema": {
        "name": "turn_facts",
        "schema": {
            "type": "object",
            "properties": {"acts": {"type": "array"}},
            "required": ["acts"],
        },
    },
}

STRUCTURED = {"acts": [{"id": "a1", "kind": "statement"}]}


def _make_profile(
    *,
    json_schema_flag: str | None = None,
    output_format: str = "json",
) -> InstrumentProfile:
    return InstrumentProfile(
        name="claude-code",
        display_name="Claude Code",
        description="Test CLI instrument",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(
                executable="claude",
                prompt_flag="-p",
                prompt_via_stdin=False,
                output_format_flag="--output-format",
                output_format_value=output_format,
                json_schema_flag=json_schema_flag,
            ),
            output=CliOutputConfig(
                format=output_format,
                result_path="result",
            ),
            errors=CliErrorConfig(),
        ),
    )


def _make_backend(
    *, json_schema_flag: str | None = "--json-schema",
    output_format: str = "json",
) -> PluginCliBackend:
    return PluginCliBackend(
        _make_profile(
            json_schema_flag=json_schema_flag,
            output_format=output_format,
        ),
    )


def _claude_terminal_output(
    structured_output: Any = None,
    *,
    include_structured: bool = True,
) -> bytes:
    payload: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "result": "plain text result",
        "session_id": "test-session",
    }
    if include_structured:
        payload["structured_output"] = structured_output
    return json.dumps(payload).encode("utf-8")


def _fake_proc(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.pid = 4242
    proc.stdin = None
    proc.stdout = AsyncMock()
    proc.stdout.read = AsyncMock(
        side_effect=[stdout, b""] if stdout else [b""]
    )
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(side_effect=[b""])
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    return proc


async def _execute_captured(
    backend: PluginCliBackend,
    stdout: bytes,
    *,
    request: SheetRequestState | None = None,
    returncode: int = 0,
) -> tuple[Any, list[list[str]]]:
    """Run execute() with a fake subprocess; return (result, commands)."""
    commands: list[list[str]] = []

    def _spawn(*args: Any, **kwargs: Any) -> MagicMock:
        commands.append(list(args))
        return _fake_proc(stdout, returncode)

    with patch("asyncio.create_subprocess_exec", side_effect=_spawn):
        result = await backend.execute("do the task", request=request)
    return result, commands


# =========================================================================
# Profile model
# =========================================================================


def test_cli_command_accepts_json_schema_flag() -> None:
    cmd = CliCommand(executable="claude", json_schema_flag="--json-schema")
    assert cmd.json_schema_flag == "--json-schema"
    assert CliCommand(executable="claude").json_schema_flag is None


# =========================================================================
# _build_command boundary (real method)
# =========================================================================


def test_build_command_appends_flag_and_compact_schema() -> None:
    backend = _make_backend()
    cmd = backend._build_command(
        "prompt",
        timeout_seconds=None,
        response_format=JSON_SCHEMA_RF,
    )
    assert "--json-schema" in cmd
    idx = cmd.index("--json-schema")
    schema_arg = cmd[idx + 1]
    assert json.loads(schema_arg) == JSON_SCHEMA_RF["json_schema"]["schema"]
    # Compact encoding — one argument, no embedded spaces.
    assert " " not in schema_arg


def test_build_command_without_schema_is_byte_identical_to_legacy() -> None:
    """No schema request → the exact legacy command bytes."""
    backend = _make_backend()
    legacy = backend._build_command("prompt", timeout_seconds=None)
    no_schema = backend._build_command(
        "prompt", timeout_seconds=None, response_format=None
    )
    assert no_schema == legacy
    assert "--json-schema" not in legacy


# =========================================================================
# Parser boundary: structured_output exposure and refusals
# =========================================================================


async def test_execute_exposes_structured_output_as_result() -> None:
    backend = _make_backend()
    result, commands = await _execute_captured(
        backend,
        _claude_terminal_output(STRUCTURED),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is True
    assert json.loads(result.stdout) == STRUCTURED
    assert "--json-schema" in commands[0]


async def test_missing_structured_output_fails_loudly() -> None:
    backend = _make_backend()
    result, commands = await _execute_captured(
        backend,
        _claude_terminal_output(include_structured=False),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is False
    assert "structured_output" in (result.error_message or "")
    # The sheet result must NOT carry candidate/model content on refusal.
    assert result.stdout != "plain text result"
    assert "plain text result" not in (result.stdout or "")


async def test_non_object_structured_output_fails_loudly() -> None:
    backend = _make_backend()
    result, _ = await _execute_captured(
        backend,
        _claude_terminal_output("a string is not an object"),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is False
    assert "structured_output" in (result.error_message or "")


# =========================================================================
# Parser boundary: schema CONFORMANCE of structured_output (continuation)
# =========================================================================


async def test_nonconforming_structured_output_fails_loudly() -> None:
    """Exit 0 + structurally present object that VIOLATES the schema
    must fail the sheet — not return the nonconforming payload as the
    result (RED 2026-09-29: any dict in structured_output was accepted;
    the requested schema was never applied at the result boundary)."""
    backend = _make_backend()
    result, commands = await _execute_captured(
        backend,
        _claude_terminal_output({"wrong": "shape"}),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert "--json-schema" in commands[0]  # the refusal is post-spawn
    assert result.success is False
    assert "conform" in (result.error_message or "").lower()
    # Content-free refusal: the nonconforming payload never surfaces as
    # the sheet result or in the error text.
    assert result.stdout != json.dumps({"wrong": "shape"})
    assert '"wrong"' not in (result.stdout or "")
    assert "wrong" not in (result.error_message or "")


async def test_type_violation_fails_loudly_and_content_free() -> None:
    """``acts`` present but not an array → refuse on the type keyword,
    with the candidate value absent from the error text."""
    backend = _make_backend()
    result, _ = await _execute_captured(
        backend,
        _claude_terminal_output({"acts": "not-an-array"}),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is False
    assert "type" in (result.error_message or "")
    assert "not-an-array" not in (result.error_message or "")
    assert "not-an-array" not in (result.stdout or "")


async def test_conforming_structured_output_still_succeeds() -> None:
    """Anti-over-refusal control: a payload that satisfies the schema
    must still pass (no false reject from the conformance check)."""
    backend = _make_backend()
    result, _ = await _execute_captured(
        backend,
        _claude_terminal_output(STRUCTURED),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is True
    assert json.loads(result.stdout) == STRUCTURED


async def test_metaschema_invalid_request_schema_refuses() -> None:
    """A requested schema that is not valid JSON Schema cannot prove
    conformance — the sheet must refuse, never silently weaken."""
    backend = _make_backend()
    bad_rf = {
        "type": "json_schema",
        "json_schema": {
            "name": "bad",
            # 'required' must be an array — metaschema-invalid.
            "schema": {"required": "acts"},
        },
    }
    result, _ = await _execute_captured(
        backend,
        _claude_terminal_output(STRUCTURED),
        request=SheetRequestState(response_format=bad_rf),
    )
    assert result.success is False
    assert "schema" in (result.error_message or "").lower()
    # Content-free: even the conforming payload's keys must not surface.
    assert "acts" not in (result.error_message or "")


# =========================================================================
# Loud capability refusals (ValueError before spawn)
# =========================================================================


async def test_unsupported_profile_refuses_loudly() -> None:
    """No json_schema_flag in the profile → ValueError, never a spawn."""
    backend = _make_backend(json_schema_flag=None)
    commands: list[list[str]] = []

    def _spawn(*args: Any, **kwargs: Any) -> MagicMock:
        commands.append(list(args))
        return _fake_proc(b"{}")

    with (
        patch("asyncio.create_subprocess_exec", side_effect=_spawn),
        pytest.raises(ValueError, match="json_schema_flag"),
    ):
        await backend.execute(
            "prompt", request=SheetRequestState(
                response_format=JSON_SCHEMA_RF
            )
        )
    assert commands == []  # refused before any subprocess


async def test_json_object_request_refuses_loudly() -> None:
    """A CLI flag cannot enforce a bare json_object — refuse, don't fake."""
    backend = _make_backend()
    with pytest.raises(ValueError, match="json_object"):
        await backend.execute(
            "prompt",
            request=SheetRequestState(
                response_format={"type": "json_object"}
            ),
        )


async def test_non_json_output_profile_refuses_loudly() -> None:
    """Schema parsing needs JSON terminal output — text profiles refuse."""
    backend = _make_backend(output_format="text")
    with pytest.raises(ValueError, match="json"):
        await backend.execute(
            "prompt", request=SheetRequestState(
                response_format=JSON_SCHEMA_RF
            )
        )


def test_setter_rejects_invalid_shapes() -> None:
    backend = _make_backend()
    with pytest.raises(ValueError):
        backend.set_response_format({"type": "bogus"})
    with pytest.raises(ValueError):
        backend.set_response_format("not-a-mapping")


# =========================================================================
# No-leak controls
# =========================================================================


async def test_later_plain_call_is_ordinary() -> None:
    """After a schema sheet, a plain sheet parses result_path as before."""
    backend = _make_backend()
    schema_result, _ = await _execute_captured(
        backend,
        _claude_terminal_output(STRUCTURED),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert schema_result.success is True

    plain_result, plain_cmds = await _execute_captured(
        backend,
        _claude_terminal_output(include_structured=False),
        request=SheetRequestState(),
    )
    assert plain_result.success is True
    assert plain_result.stdout == "plain text result"  # legacy result_path
    assert not any("--json-schema" in c for c in plain_cmds)


async def test_release_resets_setter_slot() -> None:
    """clear_overrides() drops a direct-use schema for the next sheet."""
    backend = _make_backend()
    backend.set_response_format(JSON_SCHEMA_RF)
    backend.clear_overrides()
    result, cmds = await _execute_captured(
        backend, _claude_terminal_output(include_structured=False)
    )
    assert result.success is True
    assert result.stdout == "plain text result"
    assert not any("--json-schema" in c for c in cmds)


async def test_concurrent_schema_a_b_no_cross_leak() -> None:
    """Interleaved schema/plain sheets on one backend stay independent."""
    backend = _make_backend()
    commands: list[list[str]] = []
    outputs = {
        "schema": _claude_terminal_output(STRUCTURED),
        "plain": _claude_terminal_output(include_structured=False),
    }
    queue: list[bytes] = []

    def _spawn(*args: Any, **kwargs: Any) -> MagicMock:
        commands.append(list(args))
        return _fake_proc(queue.pop(0))

    async def _run(prompt: str, request: SheetRequestState) -> Any:
        with patch("asyncio.create_subprocess_exec", side_effect=_spawn):
            return await backend.execute(prompt, request=request)

    schema_req = SheetRequestState(response_format=JSON_SCHEMA_RF)
    plain_req = SheetRequestState()
    # Plain dispatches FIRST but resolves LAST (flight overlap).
    queue.extend([outputs["plain"], outputs["schema"]])
    plain_task = asyncio.create_task(_run("plain", plain_req))
    await asyncio.sleep(0)  # let the plain sheet start and read its slot
    schema_result = await _run("schema", schema_req)
    plain_result = await plain_task

    assert json.loads(schema_result.stdout) == STRUCTURED
    assert plain_result.stdout == "plain text result"
    schema_flags = [c for c in commands if "--json-schema" in c]
    plain_flags = [
        c for c in commands if "--json-schema" in c and c != schema_flags[0]
    ]
    assert len(schema_flags) == 1
    assert len(plain_flags) == 0
