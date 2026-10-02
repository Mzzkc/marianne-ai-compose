"""Transport echo evidence must survive the real musician/checkpoint boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from marianne.core.checkpoint import SheetState
from marianne.core.config.instruments import HttpProfile, InstrumentProfile
from marianne.core.sheet import Sheet
from marianne.daemon.baton.backend_pool import _create_backend_for_profile
from marianne.daemon.baton.events import SheetAttemptResult, to_observer_event
from marianne.daemon.baton.musician import sheet_task
from marianne.daemon.baton.state import AttemptContext, AttemptMode


@pytest.mark.parametrize(
    ("response_model", "status", "observed", "legacy_model"),
    [
        ({"model": "requested"}, "observed", "requested", "requested"),
        ({"model": "different"}, "observed", "different", "different"),
        ({"model": " requested "}, "observed", " requested ", " requested "),
        ({}, "absent", None, "requested"),
        ({"model": None}, "malformed", None, None),
        ({"model": 17}, "malformed", None, 17),
        ({"model": False}, "malformed", None, False),
        ({"model": []}, "malformed", None, []),
        ({"model": {}}, "malformed", None, {}),
        ({"model": ""}, "empty", None, ""),
        ({"model": " \n"}, "empty", None, " \n"),
    ],
)
async def test_echo_origin_survives_musician_checkpoint_without_inference(
    tmp_path: Path,
    response_model: dict[str, object],
    status: str,
    observed: str | None,
    legacy_model: object,
) -> None:
    """Dropping the source discriminator or substituting requested must fail."""
    profile = InstrumentProfile(
        name="local",
        display_name="Local",
        kind="http",
        default_model="requested",
        http=HttpProfile(
            base_url="http://127.0.0.1:9000/v1", auth_env_var=None, schema_family="openai"
        ),
    )
    backend = _create_backend_for_profile(profile)
    body = {"choices": [{"message": {"content": "model: requested"}}], **response_model}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:9000/v1",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    ) as client:
        backend._client = client
        direct = await backend.execute("fictional input")
        assert direct.model == legacy_model
        assert direct.model_echo_status == status
        assert direct.model_observed == observed
        assert direct.model_requested == "requested"
        inbox: asyncio.Queue[SheetAttemptResult] = asyncio.Queue()
        await sheet_task(
            job_id="echo-job",
            sheet=Sheet(
                num=1,
                movement=1,
                voice_count=1,
                instrument_name="local",
                workspace=tmp_path,
                prompt_template="fictional input",
            ),
            backend=backend,
            attempt_context=AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            inbox=inbox,
        )
    attempt = inbox.get_nowait()
    state = SheetState(sheet_num=1)
    state.record_attempt(attempt)
    retained = SheetState.model_validate_json(state.model_dump_json())
    assert retained.model_echo_status == status
    assert retained.model_observed == observed
    assert retained.model_requested == "requested"
    assert to_observer_event(attempt)["data"]["model_echo_status"] == status


def test_legacy_checkpoint_is_unverified_and_next_attempt_clears_old_echo() -> None:
    state = SheetState.model_validate({"sheet_num": 1, "model": "requested"})
    assert state.model_echo_status is None
    assert state.model_observed is None
    assert state.model_requested is None
    state.model_echo_status = "observed"
    state.model_observed = "old"
    state.model_requested = "old"
    state.record_attempt(SheetAttemptResult("echo-job", 1, "local", 2))
    assert state.model_echo_status is None
    assert state.model_observed is None
    assert state.model_requested is None
