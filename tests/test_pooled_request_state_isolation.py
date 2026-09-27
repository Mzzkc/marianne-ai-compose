"""Pooled HTTP per-sheet state isolation (W-F1/W-F2/W-F3).

The daemon-global HTTP singleton is shared by concurrent sheets across
jobs (default ``max_concurrent_sheets = 10``, per-instrument limits
opt-in). Per-sheet request state — the response_format tri-state, the
renderer preamble, prompt extensions — must be REQUEST-LOCAL: resolved
once at dispatch, consumed at ``execute()``, never a mutable attribute of
the shared backend read at payload build. Sequential reuse must reset to
profile state at release.

These controls run the REAL adapter/pool/musician/backends; network is
replaced by httpx.MockTransport on the REAL OpenAI-compatible backend.
Fictional markers only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from marianne.core.config.instruments import HttpProfile, InstrumentProfile
from marianne.core.sheet import Sheet
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.backend_pool import BackendPool
from marianne.daemon.baton.musician import sheet_task
from marianne.daemon.baton.state import (
    AttemptContext,
    AttemptMode,
    SheetExecutionState,
)
from marianne.execution.instruments.openai_compat_backend import (
    OpenAICompatibleBackend,
)
from marianne.instruments.registry import InstrumentRegistry

INSTRUMENT = "isolation-offline-http"

JOB_A_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "job_a_envelope",
        "schema": {
            "type": "object",
            "properties": {
                "observation_id": {
                    # Fictional marker standing in for job-derived
                    # identifiers under the intended per-turn-schema use.
                    "enum": ["JOB-A-CANDIDATE-SECRET-OBS-7"],
                }
            },
        },
        "strict": True,
    },
}


def _ok_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "isolation-mock",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "OK"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        },
        request=request,
    )


class Capture:
    """Offline transport capturing every raw request payload."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def note(self, request: httpx.Request) -> None:
        self.requests.append({"payload": json.loads(request.content)})

    def payload_with(self, marker: str) -> dict[str, Any] | None:
        return next(
            (
                r["payload"]
                for r in self.requests
                if marker in r["payload"]["messages"][0]["content"]
            ),
            None,
        )

    def client(self) -> httpx.AsyncClient:
        cap = self

        class _T(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                cap.note(request)
                return _ok_response(request)

        return httpx.AsyncClient(
            transport=_T(), base_url="http://isolation-offline.invalid/v1"
        )

    def holding_client(
        self,
        hold_marker: str,
        hold_event: asyncio.Event,
        on_seen: asyncio.Event,
    ) -> httpx.AsyncClient:
        """Transport that HOLDS one marked request in flight (genuine
        in-flight HTTP call) until released."""
        cap = self

        class _T(httpx.AsyncBaseTransport):
            async def handle_async_request(
                self, request: httpx.Request
            ) -> httpx.Response:
                cap.note(request)
                content = json.loads(request.content)["messages"][0]["content"]
                if hold_marker in content:
                    on_seen.set()
                    await hold_event.wait()
                return _ok_response(request)

        return httpx.AsyncClient(
            transport=_T(), base_url="http://isolation-offline.invalid/v1"
        )


def _profile(response_format: dict[str, Any] | None = None) -> InstrumentProfile:
    return InstrumentProfile(
        name=INSTRUMENT,
        display_name="Isolation Offline HTTP",
        kind="http",
        default_model="isolation-fictional-model",
        http=HttpProfile(
            base_url="http://isolation-offline.invalid/v1",
            endpoint="/chat/completions",
            schema_family="openai",
            response_format=response_format,
        ),
    )


def _pool(profile: InstrumentProfile | None = None) -> BackendPool:
    registry = InstrumentRegistry()
    registry.register(profile or _profile())
    return BackendPool(registry)


def _sheet(icfg: dict[str, Any], prompt: str) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=Path("/tmp/isolation-ws"),
        instrument_name=INSTRUMENT,
        prompt_template=prompt,
        timeout_seconds=30.0,
        instrument_config=icfg,
    )


async def _seed_singleton(
    pool: BackendPool, client: httpx.AsyncClient
) -> OpenAICompatibleBackend:
    """Create the shared HTTP singleton and pin the offline client on it."""
    backend = await pool.acquire(INSTRUMENT)
    backend._client = client
    await pool.release(INSTRUMENT, backend)
    return backend


def _adapter_with(pool: BackendPool, jobs: dict[str, Sheet]) -> BatonAdapter:
    adapter = BatonAdapter()
    for job, sheet in jobs.items():
        adapter.register_job(job, [sheet], dependencies={})
    adapter._backend_pool = pool
    return adapter


async def _drain(adapter: BatonAdapter, keys: tuple[tuple[str, int], ...]) -> None:
    for key in keys:
        task = adapter._active_tasks.get(key)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)


# --------------------------------------------------------------------- W-F1
async def test_wf1_foreign_schema_never_rides_concurrent_jobs_payload() -> None:
    """W-F1: job A's per-sheet schema must not appear in job B's outbound
    request while A is genuinely in flight on the shared singleton."""
    pool = _pool()
    cap = Capture()
    a_seen = asyncio.Event()
    a_release = asyncio.Event()
    await _seed_singleton(pool, cap.holding_client("job-A-flight", a_release, a_seen))

    adapter = _adapter_with(
        pool,
        {
            "job-A": _sheet({"response_format": JOB_A_SCHEMA}, "job-A-flight"),
            "job-B": _sheet({}, "job-B-quiet"),
        },
    )
    def state() -> SheetExecutionState:
        return SheetExecutionState(sheet_num=1, instrument_name=INSTRUMENT)

    task = asyncio.create_task(adapter._dispatch_callback("job-A", 1, state()))
    await asyncio.wait_for(a_seen.wait(), timeout=10)  # A truly in flight
    await adapter._dispatch_callback("job-B", 1, state())  # B flies meanwhile
    await _drain(adapter, (("job-B", 1),))
    a_release.set()
    await asyncio.wait_for(task, timeout=10)
    await _drain(adapter, (("job-A", 1),))

    payload_b = cap.payload_with("job-B-quiet")
    payload_a = cap.payload_with("job-A-flight")
    assert payload_b is not None, "B executed"
    # B's profile default is None — its payload must carry NO format at all.
    assert "response_format" not in payload_b
    assert "JOB-A-CANDIDATE-SECRET-OBS-7" not in json.dumps(payload_b)
    # A's own payload is intact.
    assert payload_a is not None
    assert payload_a.get("response_format") == JOB_A_SCHEMA
    await pool.close_all()


# --------------------------------------------------------------------- W-F2
async def test_wf2_explicit_null_not_defeated_at_set_to_build_window() -> None:
    """W-F2: another sheet's dispatch between this sheet's dispatch and its
    payload build must not defeat an explicit null or inject a format."""
    pool = _pool()
    cap = Capture()
    await _seed_singleton(pool, cap.client())

    b_fmt = {"type": "json_object"}
    adapter = _adapter_with(
        pool,
        {
            "job-A": _sheet({"response_format": None}, "A-optout"),
            "job-B": _sheet({"response_format": b_fmt}, "B-schema"),
        },
    )
    state = SheetExecutionState(sheet_num=1, instrument_name=INSTRUMENT)
    # Both dispatches complete before either musician task runs — the
    # create_task scheduling boundary, deterministically.
    await adapter._dispatch_callback("job-A", 1, state)
    await adapter._dispatch_callback("job-B", 1, state)
    await _drain(adapter, (("job-A", 1), ("job-B", 1)))

    payload_a = cap.payload_with("A-optout")
    payload_b = cap.payload_with("B-schema")
    assert payload_a is not None and payload_b is not None
    assert "response_format" not in payload_a  # explicit opt-out held
    assert payload_b.get("response_format") == b_fmt  # B unaffected
    await pool.close_all()


# --------------------------------------------------------------------- W-F3
async def test_wf3_preamble_evidence_never_survives_release_direct_seam() -> None:
    """W-F3 (direct setter seam): a preamble carrying prior-attempt
    evidence must not survive release into the next sheet's prompt."""
    pool = _pool()
    cap = Capture()
    backend = await _seed_singleton(pool, cap.client())
    backend = await pool.acquire(INSTRUMENT)

    preamble_a = (
        "PREAMBLE-A: prior attempt failure evidence — "
        "CANDIDATE-A-SECRET-TOKEN=314159 stderr_tail='...'"
    )
    backend.set_preamble(preamble_a)
    await backend.execute("A-prompt")
    await pool.release(INSTRUMENT, backend)

    backend2 = await pool.acquire(INSTRUMENT)  # same singleton
    assert backend2 is backend
    await backend2.execute("B-prompt-fresh-sheet")

    payload_b = cap.payload_with("B-prompt-fresh-sheet")
    assert payload_b is not None
    content = payload_b["messages"][0]["content"]
    assert "CANDIDATE-A-SECRET-TOKEN" not in content
    assert content == "B-prompt-fresh-sheet"
    await pool.release(INSTRUMENT, backend2)
    await pool.close_all()


async def test_wf3_preamble_evidence_never_smears_real_musician() -> None:
    """W-F3 (real musician seam): sheet A's renderer preamble travels
    request-locally; sheet B (no preamble) inherits nothing."""
    pool = _pool()
    cap = Capture()
    backend = await _seed_singleton(pool, cap.client())
    backend = await pool.acquire(INSTRUMENT)

    preamble_a = (
        "PREAMBLE-A: healing context — CANDIDATE-A-SECRET-TOKEN=314159 "
        "stderr_tail='validation output quoted here'"
    )
    inbox_a: asyncio.Queue = asyncio.Queue()
    await sheet_task(
        job_id="job-A",
        sheet=_sheet({}, "A-prompt"),
        backend=backend,
        attempt_context=AttemptContext(
            attempt_number=2, mode=AttemptMode.HEALING
        ),
        inbox=inbox_a,
        rendered_prompt="A-prompt",
        preamble=preamble_a,
    )
    # A's own payload DID carry its preamble (no silent payload downgrade).
    payload_a = cap.payload_with("A-prompt")
    assert payload_a is not None
    assert "CANDIDATE-A-SECRET-TOKEN" in payload_a["messages"][0]["content"]

    await pool.release(INSTRUMENT, backend)  # the wrapper's finally
    backend2 = await pool.acquire(INSTRUMENT)
    assert backend2 is backend
    inbox_b: asyncio.Queue = asyncio.Queue()
    await sheet_task(
        job_id="job-B",
        sheet=_sheet({}, "B-prompt-fresh-sheet"),
        backend=backend2,
        attempt_context=AttemptContext(
            attempt_number=1, mode=AttemptMode.NORMAL
        ),
        inbox=inbox_b,
        rendered_prompt="B-prompt-fresh-sheet",
        preamble=None,  # renderer produced no preamble for B
    )

    payload_b = cap.payload_with("B-prompt-fresh-sheet")
    assert payload_b is not None
    assert "CANDIDATE-A-SECRET-TOKEN" not in payload_b["messages"][0]["content"]
    await pool.release(INSTRUMENT, backend2)
    await pool.close_all()


async def test_wf3_concurrent_preambles_stay_per_request() -> None:
    """W-F3 concurrent member: two sheets' preambles on the singleton at
    once must not cross into each other's prompts."""
    pool = _pool()
    cap = Capture()
    backend = await _seed_singleton(pool, cap.client())
    backend = await pool.acquire(INSTRUMENT)

    inboxes = [asyncio.Queue() for _ in range(2)]
    tasks = [
        asyncio.create_task(
            sheet_task(
                job_id=f"job-{name}",
                sheet=_sheet({}, f"{name}-prompt"),
                backend=backend,
                attempt_context=AttemptContext(
                    attempt_number=1, mode=AttemptMode.NORMAL
                ),
                inbox=inbox,
                rendered_prompt=f"{name}-prompt",
                preamble=f"PREAMBLE-{name}: EVIDENCE-TOKEN-{name}=1",
            )
        )
        for name, inbox in (("A", inboxes[0]), ("B", inboxes[1]))
    ]
    await asyncio.gather(*tasks)

    for name in ("A", "B"):
        payload = cap.payload_with(f"{name}-prompt")
        assert payload is not None
        content = payload["messages"][0]["content"]
        assert f"PREAMBLE-{name}: EVIDENCE-TOKEN-{name}=1" in content
        other = "B" if name == "A" else "A"
        assert f"EVIDENCE-TOKEN-{other}" not in content
    await pool.release(INSTRUMENT, backend)
    await pool.close_all()


# ------------------------------------------------- sequential compatibility
async def test_ten_concurrent_sheets_mixed_tri_states_one_instrument() -> None:
    """The default 10-sheet daemon shape: ten concurrent dispatches on ONE
    HTTP instrument with mixed tri-states (per-sheet schemas, explicit
    nulls, absent keys) — every payload must carry exactly its own
    resolution, concurrently, no holds."""
    pool = _pool(_profile(response_format={"type": "json_object"}))
    cap = Capture()
    await _seed_singleton(pool, cap.client())

    def fmt_for(i: int) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"schema_{i}",
                "schema": {"type": "object"},
            },
        }

    # Even sheets: per-sheet schema. Sheets ≡ 3 mod 5: explicit null.
    # The rest: absent key (must inherit the json_object profile default).
    sheets = {
        f"job-{i}": _sheet(
            {"response_format": fmt_for(i)} if i % 2 == 0
            else ({"response_format": None} if i % 5 == 3
                  else {}),
            f"prompt-{i}",
        )
        for i in range(10)
    }
    adapter = _adapter_with(pool, sheets)
    for job, sheet in sheets.items():
        state = SheetExecutionState(sheet_num=1, instrument_name=INSTRUMENT)
        await adapter._dispatch_callback(job, 1, state)
    await _drain(adapter, tuple((job, 1) for job in sheets))

    for i in range(10):
        payload = cap.payload_with(f"prompt-{i}")
        assert payload is not None, f"sheet {i} executed"
        rf = payload.get("response_format")
        if i % 2 == 0:
            assert rf is not None and rf["json_schema"]["name"] == f"schema_{i}", i
        elif i % 5 == 3:
            assert rf is None, f"explicit null defeated for sheet {i}: {rf}"
        else:
            assert rf == {"type": "json_object"}, f"sheet {i}: {rf}"
    await pool.close_all()


async def test_sequential_tri_state_over_reuse_after_repair() -> None:
    """Sequential reuse keeps the exact documented semantics: per-sheet
    schema, explicit null, absent key inheriting the profile default."""
    pool = _pool(_profile(response_format={"type": "json_object"}))
    cap = Capture()
    await _seed_singleton(pool, cap.client())

    async def run(icfg: dict[str, Any], prompt: str, job: str) -> dict[str, Any]:
        adapter = _adapter_with(pool, {job: _sheet(icfg, prompt)})
        state = SheetExecutionState(sheet_num=1, instrument_name=INSTRUMENT)
        await adapter._dispatch_callback(job, 1, state)
        await _drain(adapter, ((job, 1),))
        payload = cap.payload_with(prompt)
        assert payload is not None
        return payload

    p1 = await run({"response_format": None}, "null-optout", "job-null")
    p2 = await run({}, "inherit-default", "job-inherit")
    p3 = await run(
        {"response_format": {"type": "json_schema", "json_schema": {
            "name": "s3", "schema": {"type": "object"}}}},
        "persheet-schema", "job-s3",
    )
    p4 = await run({}, "after-release", "job-s4")

    assert "response_format" not in p1
    assert p2.get("response_format") == {"type": "json_object"}
    assert p3.get("response_format", {}).get("json_schema", {}).get("name") == "s3"
    assert p4.get("response_format") == {"type": "json_object"}
    await pool.close_all()
