"""The live context probe (GH #494): `GET /props` → `default_generation_settings.n_ctx`.

llama-server and PolyStrata both report the loaded model's context there. The
profile's `context_window` sizes the executor's `max_tokens`, and a local server
refuses a request above its real context with HTTP 400 before generating, so
`instruments check` shows the served context and `mzt validate` warns (V234)
when the profile overstates it.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from marianne.core.config import JobConfig
from marianne.core.config.instruments import HttpProfile, InstrumentProfile, ModelCapacity
from marianne.instruments import availability
from marianne.instruments.availability import ProfileProbe, probe_profile, served_local_context
from marianne.validation.checks.config import ServedContextCheck


def _profile(declared: int | None = 32768, model: str = "qwen") -> InstrumentProfile:
    return InstrumentProfile(
        name="localbox",
        display_name="Local box",
        description="local server",
        kind="http",
        default_model=model,
        models=[
            ModelCapacity(
                name=model, context_window=declared, cost_per_1k_input=0, cost_per_1k_output=0
            )
        ]
        if declared
        else [],
        http=HttpProfile(
            base_url="http://127.0.0.1:18080/v1", endpoint="/chat/completions",
            schema_family="openai",
        ),
    )


class _Server:
    """A loopback server answering /v1/models and /props with given payloads."""

    def __init__(self, props: object, models: object | None = None) -> None:
        payloads = {"/props": props, "/v1/models": models or {"data": [{"id": "qwen"}]}}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = payloads.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class TestServedLocalContext:
    def test_reads_n_ctx_from_props(self) -> None:
        with _Server({"default_generation_settings": {"n_ctx": 8192, "params": {}}}) as s:
            assert served_local_context(s.base_url) == 8192

    def test_a_zero_context_is_not_a_context(self) -> None:
        # PolyStrata reports 0 until its engine has reported READY.
        with _Server({"default_generation_settings": {"n_ctx": 0}}) as s:
            assert served_local_context(s.base_url) is None

    def test_a_server_without_props_is_silent(self) -> None:
        with _Server(None) as s:
            assert served_local_context(s.base_url) is None

    def test_malformed_props_is_silent(self) -> None:
        with _Server(b"not json") as s:
            assert served_local_context(s.base_url) is None
        with _Server({"default_generation_settings": {"n_ctx": "8192"}}) as s:
            assert served_local_context(s.base_url) is None

    def test_remote_hosts_are_never_probed(self) -> None:
        assert served_local_context("https://api.example.com/v1") is None

    def test_a_closed_port_returns_none_fast(self) -> None:
        assert served_local_context("http://127.0.0.1:1/v1") is None


class TestProbeCarriesTheContext:
    def test_ready_probe_reports_served_context_and_no_mismatch_when_within(self) -> None:
        with (
            patch.object(availability, "served_local_models", return_value=["qwen"]),
            patch.object(availability, "served_local_context", return_value=32768),
        ):
            probe = probe_profile(_profile(declared=32768))
        assert probe.state == "ready"
        assert probe.served_context == 32768
        assert probe.context_mismatch == ""

    def test_an_overstated_context_window_is_named(self) -> None:
        with (
            patch.object(availability, "served_local_models", return_value=["qwen"]),
            patch.object(availability, "served_local_context", return_value=8192),
        ):
            probe = probe_profile(_profile(declared=32768))
        assert probe.state == "ready"
        assert "32,768" in probe.context_mismatch and "8,192" in probe.context_mismatch
        assert "context_window: 8192" in probe.context_mismatch

    def test_a_profile_without_a_models_entry_has_nothing_to_compare(self) -> None:
        with (
            patch.object(availability, "served_local_models", return_value=["qwen"]),
            patch.object(availability, "served_local_context", return_value=8192),
        ):
            probe = probe_profile(_profile(declared=None))
        assert probe.served_context == 8192 and probe.context_mismatch == ""

    def test_an_unexposed_context_is_not_a_mismatch(self) -> None:
        with (
            patch.object(availability, "served_local_models", return_value=["qwen"]),
            patch.object(availability, "served_local_context", return_value=None),
        ):
            probe = probe_profile(_profile(declared=32768))
        assert probe.served_context is None and probe.context_mismatch == ""


class TestV234:
    def _config(self, instrument: str = "localbox") -> JobConfig:
        return JobConfig.model_validate(
            {"name": "r", "workspace": "./ws", "instrument": instrument,
             "sheet": {"total_items": 1}, "prompt": {"template": "x"}}
        )

    def _run(self, probe: ProfileProbe, instrument: str = "localbox") -> list:
        with (
            patch(
                "marianne.instruments.loader.load_all_profiles",
                return_value={"localbox": _profile()},
            ),
            patch("marianne.instruments.availability.probe_profile", return_value=probe),
        ):
            return ServedContextCheck().check(
                self._config(instrument), Path("s.yaml"), f"instrument: {instrument}"
            )

    def test_warns_on_a_mismatch(self) -> None:
        mismatch = "profile 'localbox' declares 32,768 … serves 8,192"
        issues = self._run(ProfileProbe("ready", served_context=8192, context_mismatch=mismatch))
        assert [i.check_id for i in issues] == ["V234"]
        assert issues[0].severity.name == "WARNING"
        assert "8192" in (issues[0].suggestion or "")

    def test_quiet_when_contexts_agree(self) -> None:
        assert self._run(ProfileProbe("ready", served_context=32768)) == []

    def test_quiet_when_the_server_is_not_ready(self) -> None:
        assert self._run(ProfileProbe("unreachable", "no server answered")) == []

    def test_quiet_for_a_chain_without_http_profiles(self) -> None:
        assert self._run(ProfileProbe("ready"), instrument="claude-code") == []


@pytest.mark.parametrize("code", ["V234"])
def test_the_code_is_suppressible(code: str) -> None:
    from marianne.validation.output_contract import _known_suppression_severities

    assert code in _known_suppression_severities()
