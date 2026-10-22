"""A response_format sheet's validated object survives on the sheet record (GH #489).

Before: the CLI backend turned the terminal JSON's ``structured_output`` into
``stdout`` and the musician cut that to a 500-character tail, so a caller that
polled ``job.status`` after COMPLETED could not recover a 2-3 KB object.
After: the object rides ``ExecutionResult`` -> ``SheetAttemptResult`` ->
``SheetState.structured_output`` whole, credential-redacted, bounded at
``STRUCTURED_OUTPUT_MAX_BYTES``; a larger object sets
``structured_output_omitted`` instead of storing a cut one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from marianne.cli.commands.status import _output_status_json
from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState
from marianne.core.constants import STRUCTURED_OUTPUT_MAX_BYTES
from marianne.daemon.baton.events import SheetAttemptResult
from marianne.daemon.baton.musician import _capture_structured_output
from marianne.execution.base import ExecutionResult, SheetRequestState
from marianne.execution.instruments.openai_compat_backend import (
    OpenAICompatibleBackend,
)
from tests.test_cli_json_schema import (
    JSON_SCHEMA_RF,
    STRUCTURED,
    _claude_terminal_output,
    _execute_captured,
    _make_backend,
)

_BIG_OBJECT = {"acts": [{"id": f"a{i}", "kind": "x" * 64} for i in range(2000)]}


def _result(obj: Any, *, success: bool = True) -> ExecutionResult:
    return ExecutionResult(
        success=success,
        exit_code=0 if success else 1,
        stdout=json.dumps(obj) if obj is not None else "",
        stderr="",
        duration_seconds=1.0,
        structured_output=obj,
    )


# -- backends -------------------------------------------------------------


async def test_cli_schema_sheet_carries_the_validated_object() -> None:
    result, _ = await _execute_captured(
        _make_backend(),
        _claude_terminal_output(STRUCTURED),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is True
    assert result.structured_output == STRUCTURED


async def test_cli_nonconforming_object_is_not_carried() -> None:
    result, _ = await _execute_captured(
        _make_backend(),
        _claude_terminal_output({"wrong": "shape"}),
        request=SheetRequestState(response_format=JSON_SCHEMA_RF),
    )
    assert result.success is False
    assert result.structured_output is None


async def test_cli_plain_sheet_carries_nothing() -> None:
    result, _ = await _execute_captured(
        _make_backend(), _claude_terminal_output(STRUCTURED)
    )
    assert result.structured_output is None


def _http_backend(content: str) -> OpenAICompatibleBackend:
    body = {
        "model": "m",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    backend = OpenAICompatibleBackend(
        model="m", base_url="http://offline.local/v1", api_key_env=None
    )
    backend._client = httpx.AsyncClient(
        base_url="http://offline.local/v1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body, request=request)
        ),
    )
    return backend


async def test_http_json_mode_carries_the_object() -> None:
    backend = _http_backend('{"answer": 1}')
    result = await backend.execute(
        "hi", request=SheetRequestState(response_format={"type": "json_object"})
    )
    assert result.success is True
    assert result.structured_output == {"answer": 1}


async def test_http_plain_request_carries_nothing() -> None:
    result = await _http_backend('{"answer": 1}').execute("hi")
    assert result.structured_output is None


async def test_http_json_mode_non_object_content_carries_nothing() -> None:
    result = await _http_backend("not json").execute(
        "hi", request=SheetRequestState(response_format={"type": "json_object"})
    )
    assert result.structured_output is None


# -- musician capture -----------------------------------------------------


def test_capture_keeps_the_object_whole() -> None:
    assert _capture_structured_output(_result(STRUCTURED)) == (STRUCTURED, False)


def test_capture_ignores_failed_attempts_and_plain_sheets() -> None:
    assert _capture_structured_output(_result(STRUCTURED, success=False)) == (None, False)
    assert _capture_structured_output(_result(None)) == (None, False)


def test_capture_redacts_credentials_at_any_depth() -> None:
    secret = "sk-ant-api03-" + "A" * 90
    obj, omitted = _capture_structured_output(
        _result({"outer": {"note": f"key is {secret}"}})
    )
    assert omitted is False
    assert obj is not None
    assert secret not in json.dumps(obj)
    assert "REDACTED" in obj["outer"]["note"]


def test_capture_omits_an_object_over_the_bound() -> None:
    assert len(json.dumps(_BIG_OBJECT)) > STRUCTURED_OUTPUT_MAX_BYTES
    assert _capture_structured_output(_result(_BIG_OBJECT)) == (None, True)


# -- sheet record and status ---------------------------------------------


def _attempt(**kwargs: Any) -> SheetAttemptResult:
    return SheetAttemptResult(
        job_id="j1",
        sheet_num=1,
        instrument_name="claude-code",
        attempt=1,
        execution_success=True,
        **kwargs,
    )


def test_record_attempt_persists_and_a_later_attempt_replaces() -> None:
    sheet = SheetState(sheet_num=1)
    sheet.record_attempt(_attempt(structured_output=STRUCTURED))
    assert sheet.structured_output == STRUCTURED
    restored = SheetState.model_validate_json(sheet.model_dump_json())
    assert restored.structured_output == STRUCTURED

    sheet.record_attempt(_attempt(structured_output_omitted=True))
    assert sheet.structured_output is None
    assert sheet.structured_output_omitted is True


def test_old_checkpoints_load_without_the_field() -> None:
    raw = SheetState(sheet_num=1).model_dump(mode="json")
    raw.pop("structured_output")
    raw.pop("structured_output_omitted")
    loaded = SheetState.model_validate(raw)
    assert loaded.structured_output is None
    assert loaded.structured_output_omitted is False


@pytest.mark.parametrize(
    ("sheet", "expected"),
    [
        (SheetState(sheet_num=1, structured_output=STRUCTURED), STRUCTURED),
        (SheetState(sheet_num=1), None),
    ],
)
def test_status_json_emits_the_object(
    sheet: SheetState, expected: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    job = CheckpointState(
        job_id="j1", job_name="demo", total_sheets=1,
        status=JobStatus.COMPLETED, sheets={1: sheet},
    )
    _output_status_json(job)
    out = capsys.readouterr().out
    entry = json.loads(out[out.index("{"):])["sheets"]["1"]
    assert entry.get("structured_output") == expected
    assert "structured_output_omitted" not in entry


def test_status_json_says_when_the_object_was_omitted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    job = CheckpointState(
        job_id="j1", job_name="demo", total_sheets=1, status=JobStatus.COMPLETED,
        sheets={1: SheetState(sheet_num=1, structured_output_omitted=True)},
    )
    _output_status_json(job)
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["sheets"]["1"]["structured_output_omitted"] is True
