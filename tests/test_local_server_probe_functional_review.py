"""Functional review test suite for Issue #494 — Local-server context probe (V235).

Authored by Stage 5 Functional Reviewer to independently verify:
1. Parsing robustness of GET /props payload (types, booleans, negatives, malformed structures)
2. Loopback boundary enforcement (IPv4, IPv6, remote host rejection)
3. Origin URL resolution across arbitrary path variants
4. Formatted context mismatch messaging and numerical formatting
5. CLI check command integration (JSON fields and rich output)
6. Full-stack ValidationRunner detection and emission of V234
7. Independent suppression capability of V234
8. Non-regression of V235 (FanOutMessagingCheck)
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from marianne.cli.commands import instruments as instruments_cli
from marianne.core.config import JobConfig
from marianne.core.config.instruments import HttpProfile, InstrumentProfile, ModelCapacity
from marianne.instruments import availability
from marianne.instruments.availability import (
    ProfileProbe,
    _context_mismatch,
    served_local_context,
)
from marianne.validation.checks.best_practices import FanOutMessagingCheck
from marianne.validation.checks.config import ServedContextCheck
from marianne.validation.output_contract import _known_suppression_severities
from marianne.validation.runner import ValidationRunner, create_default_checks


def _make_http_profile(
    name: str = "localbox",
    model: str = "qwen",
    declared_context: int | None = 32768,
    base_url: str = "http://127.0.0.1:8080/v1",
) -> InstrumentProfile:
    return InstrumentProfile(
        name=name,
        display_name="Local Box",
        description="Local OpenAI-compatible server",
        kind="http",
        default_model=model,
        models=[
            ModelCapacity(
                name=model,
                context_window=declared_context,
                cost_per_1k_input=0,
                cost_per_1k_output=0,
            )
        ]
        if declared_context is not None
        else [],
        http=HttpProfile(
            base_url=base_url,
            endpoint="/chat/completions",
            schema_family="openai",
        ),
    )


class _PropsServer:
    """Mock HTTP server for testing /props and /v1/models responses."""

    def __init__(
        self,
        props_body: object,
        models_body: object | None = None,
        status_code: int = 200,
    ) -> None:
        self.props_body = props_body
        self.models_body = models_body or {"data": [{"id": "qwen"}]}
        self.status_code = status_code

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/props":
                    self.send_response(status_code)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    if isinstance(props_body, bytes):
                        self.wfile.write(props_body)
                    elif props_body is not None:
                        self.wfile.write(json.dumps(props_body).encode("utf-8"))
                elif self.path == "/v1/models":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    payload = models_body or {"data": [{"id": "qwen"}]}
                    self.wfile.write(json.dumps(payload).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *_: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _PropsServer:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class TestPropsParsingRobustness:
    """Verify that served_local_context handles atypical and malformed /props payloads."""

    def test_boolean_n_ctx_is_rejected(self) -> None:
        # In Python, isinstance(True, int) is True, so explicit bool check is required
        with _PropsServer({"default_generation_settings": {"n_ctx": True}}) as s:
            assert served_local_context(s.base_url) is None

    def test_negative_n_ctx_is_rejected(self) -> None:
        with _PropsServer({"default_generation_settings": {"n_ctx": -1024}}) as s:
            assert served_local_context(s.base_url) is None

    def test_float_n_ctx_is_rejected(self) -> None:
        with _PropsServer({"default_generation_settings": {"n_ctx": 8192.5}}) as s:
            assert served_local_context(s.base_url) is None

    def test_string_n_ctx_is_rejected(self) -> None:
        with _PropsServer({"default_generation_settings": {"n_ctx": "8192"}}) as s:
            assert served_local_context(s.base_url) is None

    def test_non_dict_settings_is_handled_gracefully(self) -> None:
        with _PropsServer({"default_generation_settings": "disabled"}) as s:
            assert served_local_context(s.base_url) is None
        with _PropsServer({"default_generation_settings": [8192]}) as s:
            assert served_local_context(s.base_url) is None

    def test_non_dict_root_payload_is_handled_gracefully(self) -> None:
        with _PropsServer(["settings", 8192]) as s:
            assert served_local_context(s.base_url) is None

    def test_http_500_server_error_returns_none(self) -> None:
        with _PropsServer({"default_generation_settings": {"n_ctx": 8192}}, status_code=500) as s:
            assert served_local_context(s.base_url) is None

    def test_origin_resolution_strips_nested_subpaths(self) -> None:
        # Base url with deep path should still query /props at root origin
        with _PropsServer({"default_generation_settings": {"n_ctx": 4096}}) as s:
            deep_base_url = f"http://127.0.0.1:{s.port}/nested/api/v1"
            assert served_local_context(deep_base_url) == 4096


class TestLoopbackBoundaryEnforcement:
    """Ensure remote endpoints are never probed while loopback addresses are."""

    def test_remote_hostnames_refused(self) -> None:
        assert served_local_context("http://example.com:8080/v1") is None
        assert served_local_context("https://api.openai.com/v1") is None
        assert served_local_context("http://192.168.1.15:8080/v1") is None

    def test_ipv6_loopback_recognized(self) -> None:
        # availability._is_loopback parses [::1] correctly
        assert availability._is_loopback("::1") is True
        assert availability._is_loopback("localhost") is True
        assert availability._is_loopback("127.0.0.1") is True
        assert availability._is_loopback("127.0.0.99") is True
        assert availability._is_loopback("10.0.0.1") is False


class TestContextMismatchCalculations:
    """Verify context comparison logic and error message formatting."""

    def test_declared_equal_to_served_is_no_mismatch(self) -> None:
        prof = _make_http_profile(declared_context=8192)
        assert _context_mismatch(prof, 8192) == ""

    def test_declared_less_than_served_is_no_mismatch(self) -> None:
        prof = _make_http_profile(declared_context=4096)
        assert _context_mismatch(prof, 8192) == ""

    def test_declared_greater_than_served_formats_numbers_and_fix(self) -> None:
        prof = _make_http_profile(name="strata", model="qwen3", declared_context=32768)
        msg = _context_mismatch(prof, 8192)
        assert "profile 'strata'" in msg
        assert "declares context_window 32,768 for 'qwen3'" in msg
        assert "serves it with 8,192" in msg
        assert "HTTP 400" in msg
        assert "set context_window: 8192" in msg

    def test_missing_default_model_in_models_is_handled(self) -> None:
        prof = _make_http_profile(declared_context=None)
        assert _context_mismatch(prof, 8192) == ""

    def test_default_model_not_matching_models_entries(self) -> None:
        prof = _make_http_profile(model="llama", declared_context=32768)
        prof.default_model = "unknown-model"
        assert _context_mismatch(prof, 8192) == ""


class TestCliInstrumentsInspection:
    """Verify CLI integration for instruments check in both human and JSON modes."""

    def test_check_json_includes_served_context_and_mismatch(self) -> None:
        prof = _make_http_profile(name="box", declared_context=32768)
        probe = ProfileProbe(
            state="ready",
            served_context=8192,
            context_mismatch="profile 'box' declares context_window 32,768 but serves 8,192",
        )

        with (
            patch("marianne.cli.commands.instruments.probe_profile", return_value=probe),
            patch("marianne.cli.commands.instruments.output_json") as mock_output_json,
        ):
            instruments_cli._check_json(prof)
            assert mock_output_json.called
            data = mock_output_json.call_args[0][0]
            assert data["ready"] is True
            assert data["served_context"] == 8192
            assert "32,768" in data["context_mismatch"]

    def test_check_json_null_served_context_when_props_not_exposed(self) -> None:
        # Pins the JSON contract for script consumers: a ready loopback server
        # that does not expose /props reports served_context: null (the key is
        # present — "not exposed" is meaningful) and omits context_mismatch.
        prof = _make_http_profile(name="quiet-box", declared_context=32768)
        probe = ProfileProbe(state="ready", served_context=None, context_mismatch="")

        with (
            patch("marianne.cli.commands.instruments.probe_profile", return_value=probe),
            patch("marianne.cli.commands.instruments.output_json") as mock_output_json,
        ):
            instruments_cli._check_json(prof)
            assert mock_output_json.called
            data = mock_output_json.call_args[0][0]
            assert "served_context" in data
            assert data["served_context"] is None
            assert "context_mismatch" not in data

    def test_check_rich_prints_props_context_line(self) -> None:
        prof = _make_http_profile(name="box", declared_context=32768)
        probe = ProfileProbe(
            state="ready",
            served_context=8192,
            context_mismatch="mismatch details",
        )

        buf = io.StringIO()
        test_console = Console(file=buf, force_terminal=False, color_system=None)

        with (
            patch("marianne.cli.commands.instruments.probe_profile", return_value=probe),
            patch("marianne.cli.commands.instruments.console", test_console),
        ):
            instruments_cli._check_rich(prof)
            output = buf.getvalue()
            assert "Context:       8,192 tokens (server /props)" in output
            assert "mismatch details" in output


class TestFullStackValidationIntegration:
    """Verify ValidationRunner emits V234 on real score configs."""

    def test_validation_runner_reports_v235_for_mismatch(self) -> None:
        job = JobConfig.model_validate({
            "name": "test-job",
            "workspace": "/tmp/test-ws",
            "instrument": "localbox",
            "sheet": {"total_items": 1},
            "prompt": {"template": "Hello"},
        })
        prof = _make_http_profile(name="localbox", declared_context=32768)
        probe = ProfileProbe(
            state="ready",
            served_context=8192,
            context_mismatch="profile 'localbox' declares context_window 32,768 but serves 8,192",
        )

        with (
            patch("marianne.instruments.loader.load_all_profiles", return_value={"localbox": prof}),
            patch("marianne.instruments.availability.probe_profile", return_value=probe),
        ):
            runner = ValidationRunner(checks=create_default_checks())
            issues = runner.validate(job, Path("job.yaml"), "instrument: localbox")
            v_served_issues = [i for i in issues if i.check_id == "V234"]
            assert len(v_served_issues) == 1
            assert v_served_issues[0].severity.name == "WARNING"
            assert "8192" in (v_served_issues[0].suggestion or "")

    def test_served_context_in_known_suppression_severities(self) -> None:
        suppressions = _known_suppression_severities()
        assert "V234" in suppressions
        assert "V235" in suppressions

    def test_messaging_check_remains_active_and_distinct(self) -> None:
        # FanOutMessagingCheck is V235: at merge, main already owned V234 (served context).
        check_messaging = FanOutMessagingCheck()
        assert check_messaging.check_id == "V235"
        check_served = ServedContextCheck()
        assert check_served.check_id == "V234"
