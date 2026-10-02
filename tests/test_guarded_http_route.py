"""Guarded requests verify actual resolver state before allocating network custody."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from marianne.daemon.baton.backend_pool import _create_backend_for_profile
from marianne.execution.base import SheetRequestState
from marianne.instruments.loader import capture_resolved_instrument_route, load_all_profiles


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    venue = tmp_path / ".marianne" / "instruments"
    venue.mkdir(parents=True)
    profile_file = venue / "local.yaml"
    profile_file.write_text(
        "name: local\ndisplay_name: Local\nkind: http\ndefault_model: requested\n"
        "http:\n  base_url: http://127.0.0.1:9000/v1\n"
        "  endpoint: /chat/completions\n  schema_family: openai\n"
    )
    profile = load_all_profiles()["local"]
    backend = _create_backend_for_profile(profile)
    binding = capture_resolved_instrument_route("local", {}, now=datetime.now(UTC))
    return backend, binding, profile_file


def _client_spy(monkeypatch, *, status=200):
    import marianne.execution.instruments.openai_compat_backend as module

    original = httpx.AsyncClient
    clients, calls, options, retries = [], [], [], []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://outside.invalid/"},
            json={"model": "requested", "choices": [{"message": {"content": "ok"}}]},
        )

    def transport(**kwargs):
        retries.append(kwargs)
        return httpx.MockTransport(respond)

    def create(**kwargs):
        options.append(kwargs.copy())
        kwargs.setdefault("transport", httpx.MockTransport(respond))
        client = original(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(module.httpx, "AsyncHTTPTransport", transport)
    monkeypatch.setattr(module.httpx, "AsyncClient", create)
    return clients, calls, options, retries


async def test_guarded_client_is_request_local_closed_and_echo_is_transport_origin(
    guarded, monkeypatch
):
    backend, binding, _ = guarded
    clients, calls, options, retries = _client_spy(monkeypatch)
    for _ in range(2):
        result = await backend.execute(
            "fictional", request=SheetRequestState(expected_route=binding)
        )
        assert result.success
        assert result.model_echo_status == "observed"
    assert len(clients) == len(calls) == 2
    assert clients[0] is not clients[1]
    assert all(client.is_closed for client in clients)
    assert all(
        option["trust_env"] is False and option["follow_redirects"] is False for option in options
    )
    assert retries == [{"retries": 0}, {"retries": 0}]
    assert backend._client is None


@pytest.mark.parametrize(
    "change", ["profile", "model", "endpoint", "port", "host", "scheme", "arm", "missing_pin"]
)
async def test_drift_refuses_before_client_or_post(guarded, monkeypatch, change):
    backend, binding, path = guarded
    clients, calls, _, _ = _client_spy(monkeypatch)
    if change == "profile":
        path.write_text(path.read_text() + "\n# changed raw bytes\n")
    else:
        field, value = {
            "model": ("effective_model", "other"),
            "endpoint": ("transport_endpoint", "/other"),
            "port": ("transport_port", 9001),
            "host": ("transport_host", "example.com"),
            "scheme": ("transport_scheme", "https"),
            "arm": ("arm", "remote"),
            "missing_pin": ("profile_file_sha256", None),
        }[change]
        binding = binding.model_copy(update={field: value})
    result = await backend.execute("fictional", request=SheetRequestState(expected_route=binding))
    assert not result.success
    assert result.error_type == "attempt_route_drift"
    assert clients == calls == []


async def test_redirect_is_single_failed_request_never_followed(guarded, monkeypatch):
    backend, binding, _ = guarded
    clients, calls, _, _ = _client_spy(monkeypatch, status=302)
    result = await backend.execute("fictional", request=SheetRequestState(expected_route=binding))
    assert not result.success
    assert result.error_type == "attempt_route_drift"
    assert len(calls) == 1
    assert clients[0].is_closed


@pytest.mark.parametrize("age", [60.001, -0.001])
async def test_daemon_capture_clock_window_refuses_before_post(guarded, monkeypatch, age):
    import marianne.execution.instruments.openai_compat_backend as module

    backend, binding, _ = guarded
    now = datetime.now(UTC)
    times = iter([now, now, now + timedelta(seconds=age)])
    monkeypatch.setattr(module, "utc_now", lambda: next(times))
    clients, calls, _, _ = _client_spy(monkeypatch)
    result = await backend.execute("fictional", request=SheetRequestState(expected_route=binding))
    assert not result.success
    assert result.error_type == "attempt_route_drift"
    assert calls == []
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("rate_limited", [False, True])
async def test_guarded_failure_is_terminal_even_with_legacy_retry_budget(guarded, rate_limited):
    from marianne.core.checkpoint import SheetState, SheetStatus
    from marianne.daemon.baton.core import BatonCore
    from marianne.daemon.baton.events import SheetAttemptResult

    _, binding, _ = guarded
    state = SheetState(sheet_num=1, instrument_name="local", expected_route=binding)
    core = BatonCore()
    core.register_job("guard", {1: state}, {})
    await core.handle_event(
        SheetAttemptResult(
            "guard",
            1,
            "local",
            1,
            execution_success=False,
            rate_limited=rate_limited,
            error_code="E301",
            error_message="attempt_route_drift",
        )
    )
    assert state.status == SheetStatus.FAILED
    assert state.error_code == "E301"
    assert core.inbox.empty()


def test_guard_refusal_uses_existing_configuration_taxonomy():
    from marianne.daemon.baton.musician import _classify_error
    from marianne.execution.base import ExecutionResult

    result = _classify_error(
        ExecutionResult(
            success=False,
            stdout="",
            stderr="attempt_route_drift",
            duration_seconds=0,
            exit_code=1,
            error_type="attempt_route_drift",
            error_message="attempt_route_drift",
        )
    )
    assert result.classification == "EXECUTION_ERROR"
    assert result.error_code == "E301"


async def test_proxy_environment_and_concurrent_guards_never_change_legacy_pool(
    guarded, monkeypatch
):
    backend, binding, _ = guarded
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9999")
    monkeypatch.setenv("NO_PROXY", "")
    clients, calls, options, _ = _client_spy(monkeypatch)
    pooled = await backend._get_client()
    results = await asyncio.gather(
        backend.execute("legacy"),
        backend.execute("guard A", request=SheetRequestState(expected_route=binding)),
        backend.execute("guard B", request=SheetRequestState(expected_route=binding)),
    )
    assert all(result.success for result in results)
    assert len(clients) == 3
    assert "trust_env" not in options[0]
    assert pooled._trust_env is True
    assert all(client._trust_env is False for client in clients[1:])
    assert all(call.url.host == "127.0.0.1" and call.url.port == 9000 for call in calls)
    assert backend._client is pooled and not pooled.is_closed
    assert all(client.is_closed for client in clients[1:])
    with pytest.raises(RuntimeError, match="closed"):
        await clients[1].post("/chat/completions", json={})
    assert (await backend.execute("legacy still usable")).success
    assert len(clients) == 3
    await backend._close_httpx_client()


async def test_guard_timeout_preserves_timeout_result_and_closes_client(guarded, monkeypatch):
    backend, binding, _ = guarded
    clients, _, _, _ = _client_spy(monkeypatch)

    def timeout(request):
        raise httpx.ReadTimeout("mock timeout", request=request)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(timeout))
    result = await backend.execute("fictional", request=SheetRequestState(expected_route=binding))
    assert result.error_type == "timeout" and result.exit_reason == "timeout"
    assert clients[0].is_closed
    assert backend._client is None


@pytest.mark.parametrize("completion_delta,success", [(0, True), (-0.001, False)])
async def test_exact_60_second_boundary_and_completion_order(
    guarded, monkeypatch, completion_delta, success
):
    import marianne.execution.instruments.openai_compat_backend as module

    backend, binding, _ = guarded
    now = datetime.now(UTC)
    start = now + timedelta(seconds=60)
    times = iter([now, now, start, start + timedelta(seconds=completion_delta)])
    monkeypatch.setattr(module, "utc_now", lambda: next(times))
    clients, calls, _, _ = _client_spy(monkeypatch)
    result = await backend.execute("fictional", request=SheetRequestState(expected_route=binding))
    assert result.success is success
    assert len(calls) == 1 and clients[0].is_closed
    if not success:
        assert result.model_echo_status is None
