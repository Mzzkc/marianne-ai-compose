"""Opt-in response_format forwarding for the generic OpenAI-compatible backend.

Contracts under test (2026-09-27, marianne-response-format-20260927):

- A score (``instrument_config.response_format``) or an instrument profile
  (``http.response_format``) may supply an OpenAI-compatible structured-output
  object. It is validated loudly and forwarded UNCHANGED into that sheet's
  HTTP payload.
- Absent configuration must preserve the EXACT legacy payload — same four
  keys, same order, byte-identical body.
- Invalid shapes raise (backend setter) / fail validation (profile load) —
  never silently dropped.
- A provider 400 answering the constrained request surfaces as a failure
  with the provider body — not swallowed, not retried away.
- Per-sheet values never carry across backend release (F-150 carryover class).

All HTTP is offline: requests are captured through httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from marianne.core.config.instruments import HttpProfile, InstrumentProfile
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.backend_pool import _create_backend_for_profile
from marianne.daemon.baton.state import SheetExecutionState
from marianne.execution.base import RESPONSE_FORMAT_UNSET, SheetRequestState
from marianne.execution.instruments.openai_compat_backend import (
    OpenAICompatibleBackend,
)

# =========================================================================
# Fixtures — offline exact-payload capture
# =========================================================================


_OK_RESPONSE = {
    "model": "test-model",
    "choices": [{"message": {"content": '{"answer": 1}'}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 5},
}


def _capturing_backend(
    *,
    status: int = 200,
    response_body: dict[str, Any] | None = None,
    **backend_kwargs: Any,
) -> tuple[OpenAICompatibleBackend, dict[str, Any]]:
    """Build an OpenAICompatibleBackend whose requests hit a MockTransport.

    Captures the raw request bytes and the decoded JSON payload.
    """
    captured: dict[str, Any] = {"raw": None, "payload": None, "count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["count"] += 1
        captured["raw"] = request.read()
        captured["payload"] = json.loads(captured["raw"])
        if status != 200:
            return httpx.Response(
                status, json=response_body or {}, request=request
            )
        return httpx.Response(200, json=_OK_RESPONSE, request=request)

    backend = OpenAICompatibleBackend(
        model="test-model",
        base_url="http://offline.local/v1",
        api_key_env=None,
        **backend_kwargs,
    )
    # Pre-seed the lazy client slot (HttpxClientMixin reuses a live client),
    # so no real connection is ever attempted.
    backend._client = httpx.AsyncClient(
        base_url="http://offline.local/v1",
        transport=httpx.MockTransport(handler),
    )
    return backend, captured


def _legacy_body_bytes() -> bytes:
    """Byte-exact oracle for the legacy payload, built by httpx itself."""
    legacy = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16384,
        "temperature": 0.7,
    }
    return httpx.Request(
        "POST", "http://offline.local/v1/chat/completions", json=legacy
    ).read()


# =========================================================================
# Exact payload contracts
# =========================================================================


async def test_absent_config_sends_exact_legacy_payload() -> None:
    """No response_format anywhere → payload is byte-identical to legacy."""
    backend, captured = _capturing_backend()
    result = await backend.execute("hi")

    assert result.success is True
    assert captured["count"] == 1
    assert "response_format" not in captured["payload"]
    assert list(captured["payload"].keys()) == [
        "model", "messages", "max_tokens", "temperature",
    ]
    assert captured["raw"] == _legacy_body_bytes()


async def test_profile_json_object_forwarded_unchanged() -> None:
    """A profile-level json_object lands in the payload verbatim."""
    backend, captured = _capturing_backend(
        response_format={"type": "json_object"}
    )
    result = await backend.execute("hi")

    assert result.success is True
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    # The legacy keys are untouched alongside the new one.
    assert list(captured["payload"].keys()) == [
        "model", "messages", "max_tokens", "temperature", "response_format",
    ]


async def test_json_schema_with_extras_forwarded_unchanged() -> None:
    """A full json_schema object (strict + provider extras) is not rewritten."""
    schema_obj = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer_schema",
            "schema": schema_obj,
            "strict": True,
            "provider_extra": {"kept": True},
        },
    }
    backend, captured = _capturing_backend(response_format=response_format)
    result = await backend.execute("hi")

    assert result.success is True
    assert captured["payload"]["response_format"] == response_format


async def test_set_response_format_none_opts_out_of_profile_default() -> None:
    """An explicit per-sheet null clears a profile default (opt-out)."""
    backend, captured = _capturing_backend(
        response_format={"type": "json_object"}
    )
    backend.set_response_format(None)
    result = await backend.execute("hi")

    assert result.success is True
    assert "response_format" not in captured["payload"]


async def test_clear_overrides_restores_profile_default() -> None:
    """Release-time clear_overrides restores the profile default (no carryover)."""
    backend, captured = _capturing_backend(
        response_format={"type": "json_object"}
    )
    backend.set_response_format(
        {
            "type": "json_schema",
            "json_schema": {
                "name": "per_sheet",
                "schema": {"type": "object"},
            },
        }
    )
    backend.clear_overrides()
    result = await backend.execute("hi")

    assert result.success is True
    assert captured["payload"]["response_format"] == {"type": "json_object"}


# =========================================================================
# Loud rejection of invalid shapes
# =========================================================================


_INVALID_RESPONSE_FORMATS: list[Any] = [
    # The successor score's symbolic value is NOT a valid object — a string
    # must never silently pass through to the wire.
    "per_turn_json_schema",
    ["json_object"],
    {},
    {"type": "text"},
    {"type": "json"},
    # Note: unknown keys on a json_object (e.g. a stray json_schema) are a
    # provider judgment, not a structural violation — they are forwarded
    # unchanged, and a refusing provider surfaces as the tested 400.
    {"type": "json_schema"},
    {"type": "json_schema", "json_schema": "not-a-mapping"},
    {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}},
    {"type": "json_schema", "json_schema": {"name": ""}},
    {
        "type": "json_schema",
        "json_schema": {"name": "s", "schema": "not-a-mapping"},
    },
    {
        "type": "json_schema",
        "json_schema": {
            "name": "s",
            "schema": {"type": "object"},
            "strict": "yes",
        },
    },
]


@pytest.mark.parametrize("bad", _INVALID_RESPONSE_FORMATS)
def test_backend_setter_rejects_invalid_shapes(bad: Any) -> None:
    """Invalid response_format raises ValueError in the backend setter."""
    backend = OpenAICompatibleBackend(
        model="m", base_url="http://offline.local/v1", api_key_env=None
    )
    with pytest.raises(ValueError, match="response_format"):
        backend.set_response_format(bad)
    # A rejected value must not leave partial state behind.
    assert backend._response_format is None


@pytest.mark.parametrize("bad", _INVALID_RESPONSE_FORMATS)
def test_profile_load_rejects_invalid_shapes(bad: Any) -> None:
    """Invalid response_format fails HttpProfile validation at config load."""
    with pytest.raises(ValidationError, match="response_format"):
        HttpProfile(
            base_url="http://offline.local/v1",
            schema_family="openai",
            response_format=bad,
        )


def test_constructor_rejects_invalid_default() -> None:
    """The backend constructor validates the profile default too."""
    with pytest.raises(ValueError, match="response_format"):
        OpenAICompatibleBackend(
            model="m",
            base_url="http://offline.local/v1",
            api_key_env=None,
            response_format={"type": "yaml"},
        )


# =========================================================================
# Provider failure surfaces — never swallowed
# =========================================================================


async def test_provider_400_surfaces_failure_with_body() -> None:
    """A provider 400 answering the constrained request fails loudly."""
    error_body = {
        "error": {
            "type": "invalid_request_error",
            "message": "response_format only supports schema model x",
        }
    }
    backend, captured = _capturing_backend(
        status=400,
        response_body=error_body,
        response_format={"type": "json_object"},
    )
    result = await backend.execute("hi")

    assert result.success is False
    assert result.exit_code == 400
    assert result.error_type == "bad_request"
    assert "response_format only supports schema model x" in result.stderr
    # The constrained payload really was sent before the provider refused.
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["count"] == 1  # no silent retry that strips the field


# =========================================================================
# Wiring: profile default through the pool, sheet config through the adapter
# =========================================================================


def test_pool_builds_backend_with_profile_default() -> None:
    """_build_openai_family_backend forwards the profile default."""
    profile = InstrumentProfile(
        name="offline-json",
        display_name="Offline JSON",
        kind="http",
        default_model="test-model",
        http=HttpProfile(
            base_url="http://offline.local/v1",
            endpoint="/chat/completions",
            schema_family="openai",
            response_format={"type": "json_object"},
        ),
    )
    backend = _create_backend_for_profile(profile)
    assert isinstance(backend, OpenAICompatibleBackend)
    assert backend._response_format == {"type": "json_object"}


def _adapter_with_registered_sheet(instrument_config: dict[str, Any]) -> tuple[
    BatonAdapter, MagicMock
]:
    adapter = BatonAdapter()
    sheet = Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=Path("/tmp/test-ws"),
        instrument_name="offline-json",
        prompt_template="hi",
        timeout_seconds=60.0,
        instrument_config=instrument_config,
    )
    adapter.register_job("test-job", [sheet], dependencies={})
    pool = MagicMock()
    pool.acquire = AsyncMock()
    pool.release = AsyncMock()
    adapter._backend_pool = pool
    return adapter, pool


def _success_result() -> MagicMock:
    return MagicMock(
        success=True,
        exit_code=0,
        stdout="ok",
        stderr="",
        rate_limited=False,
        duration_seconds=1.0,
        input_tokens=1,
        output_tokens=1,
        model="test-model",
        error_message=None,
    )


async def test_sheet_response_format_reaches_backend_at_dispatch() -> None:
    """instrument_config.response_format reaches execute() request-locally.

    W-F1/W-F2: dispatch resolves and validates the value once and threads it
    on SheetRequestState — the pooled singleton's mutable slots are never
    written between dispatch and the payload build.
    """
    adapter, pool = _adapter_with_registered_sheet(
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "sheet_schema",
                    "schema": {"type": "object"},
                },
            }
        }
    )
    backend = MagicMock()
    backend.set_response_format = MagicMock()
    backend.execute = AsyncMock(return_value=_success_result())
    pool.acquire.return_value = backend

    state = SheetExecutionState(sheet_num=1, instrument_name="offline-json")
    await adapter._dispatch_callback("test-job", 1, state)
    if adapter._active_tasks:
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

    # The validated value traveled on the request, not the mutable slot.
    backend.set_response_format.assert_not_called()
    assert backend.execute.await_count >= 1
    request = backend.execute.call_args.kwargs["request"]
    assert request.response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "sheet_schema",
            "schema": {"type": "object"},
        },
    }


async def test_absent_sheet_config_leaves_backend_untouched() -> None:
    """No response_format key → UNSET travels on the request (profile default)."""
    adapter, pool = _adapter_with_registered_sheet({"model": "test-model"})
    backend = MagicMock()
    backend.set_response_format = MagicMock()
    backend.execute = AsyncMock(return_value=_success_result())
    pool.acquire.return_value = backend

    state = SheetExecutionState(sheet_num=1, instrument_name="offline-json")
    await adapter._dispatch_callback("test-job", 1, state)
    if adapter._active_tasks:
        await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)

    backend.set_response_format.assert_not_called()
    request = backend.execute.call_args.kwargs["request"]
    assert request.response_format is RESPONSE_FORMAT_UNSET


async def test_backend_without_support_skips_with_warning() -> None:
    """A backend lacking set_response_format (CLI family) still dispatches."""
    adapter, pool = _adapter_with_registered_sheet(
        {"response_format": {"type": "json_object"}}
    )
    # spec-limited mock: hasattr(set_response_format) is False.
    backend = MagicMock(spec=["execute", "clear_overrides", "apply_overrides"])
    backend.execute = AsyncMock(return_value=_success_result())
    pool.acquire.return_value = backend

    state = SheetExecutionState(sheet_num=1, instrument_name="offline-json")
    await adapter._dispatch_callback("test-job", 1, state)

    assert len(adapter._active_tasks) > 0
    await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)


async def test_invalid_sheet_response_format_becomes_dispatch_failure() -> None:
    """An invalid score-level response_format fails dispatch loudly."""
    adapter, pool = _adapter_with_registered_sheet(
        {"response_format": "per_turn_json_schema"}
    )
    backend = OpenAICompatibleBackend(
        model="test-model", base_url="http://offline.local/v1", api_key_env=None
    )
    pool.acquire.return_value = backend
    adapter._send_dispatch_failure = MagicMock()  # type: ignore[method-assign]

    state = SheetExecutionState(sheet_num=1, instrument_name="offline-json")
    await adapter._dispatch_callback("test-job", 1, state)

    assert len(adapter._active_tasks) == 0  # no musician spawned
    adapter._send_dispatch_failure.assert_called_once()
    failure_msg = adapter._send_dispatch_failure.call_args.args[3]
    assert "response_format" in failure_msg
    # The backend was released back to the pool, not leaked.
    pool.release.assert_awaited_once()


# =========================================================================
# Request-local state contract (W-F1/W-F2/W-F3)
# =========================================================================


async def test_request_state_tri_state_against_profile_default() -> None:
    """The dispatch-resolved tri-state governs the payload, never the slot.

    UNSET → profile default; explicit None → omitted; mapping → forwarded.
    """
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "s", "schema": {"type": "object"}},
    }
    backend, captured = _capturing_backend(
        response_format={"type": "json_object"}
    )

    await backend.execute("p1", request=SheetRequestState())
    assert captured["payload"]["response_format"] == {"type": "json_object"}

    await backend.execute("p2", request=SheetRequestState(response_format=None))
    assert "response_format" not in captured["payload"]

    await backend.execute("p3", request=SheetRequestState(response_format=schema))
    assert captured["payload"]["response_format"] == schema


async def test_request_state_wins_over_mutated_singleton_slots() -> None:
    """A request-bearing execute never reads the mutable slots (W-F1/W-F2).

    Another dispatch may have left a foreign format/preamble on the shared
    singleton — with a request in hand, none of it may reach the payload.
    """
    backend, captured = _capturing_backend()
    # Foreign residue on the singleton, as another sheet's dispatch would
    # have left it before the repair (or a direct setter user today).
    backend.set_response_format({"type": "json_object"})
    backend.set_preamble("FOREIGN-PREAMBLE-EVIDENCE-TOKEN=999")
    backend.set_prompt_extensions(["FOREIGN-EXTENSION"])

    result = await backend.execute(
        "own-prompt", request=SheetRequestState(preamble="OWN-PREAMBLE")
    )

    assert result.success is True
    payload = captured["payload"]
    assert "response_format" not in payload  # explicit absence honored
    content = payload["messages"][0]["content"]
    assert "OWN-PREAMBLE" in content
    assert "FOREIGN-PREAMBLE-EVIDENCE-TOKEN" not in content
    assert "FOREIGN-EXTENSION" not in content


async def test_no_request_preserves_legacy_setter_path() -> None:
    """request=None keeps the documented direct-call setter behavior."""
    backend, captured = _capturing_backend()
    backend.set_response_format({"type": "json_object"})
    backend.set_preamble("LEGACY")

    result = await backend.execute("hi")

    assert result.success is True
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["messages"][0]["content"] == "LEGACY\nhi"


async def test_clear_overrides_resets_preamble_and_extensions() -> None:
    """Release-time reset covers the full per-sheet prompt-state class (W-F3)."""
    backend, captured = _capturing_backend()
    backend.set_preamble("SHEET-A-EVIDENCE-TOKEN=314159")
    backend.set_prompt_extensions(["EXTENSION-BLOCK-EVIDENCE"])
    backend.clear_overrides()

    assert backend._preamble is None
    assert backend._prompt_extensions == []
    await backend.execute("next-sheet")

    content = captured["payload"]["messages"][0]["content"]
    assert "SHEET-A-EVIDENCE-TOKEN" not in content
    assert "EXTENSION-BLOCK-EVIDENCE" not in content
    assert content == "next-sheet"
