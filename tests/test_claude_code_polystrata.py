"""The ``claude-code-polystrata`` builtin: a profile over PolyStrata's Anthropic API.

PolyStrata (https://github.com/VecSzn/PolyStrata) is a local server for sparse
MoE models on one GPU. No GPU and no PolyStrata exist on the authoring host, so
the server side is a mock Anthropic-compatible HTTP server on a random port and
the client side is the real ``claude`` binary when it is installed. What these
tests prove is the profile's routing contract (env, auth, model name, effort,
tools, error typing, the documented ``extends`` overlay); they do not prove a
real PolyStrata answers a tool call.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from typer.testing import CliRunner

from marianne.cli import app
from marianne.core.config.instruments import InstrumentProfile
from marianne.execution.instruments.cli_backend import PluginCliBackend, build_instrument_env
from marianne.instruments.loader import (
    InstrumentProfileLoader,
    clear_profile_memo,
    load_all_profiles,
)

BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"
NAME = "claude-code-polystrata"
REPLY = "POLYSTRATA_SEAM_OK"
needs_claude = pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code is not installed")


@pytest.fixture
def profile() -> InstrumentProfile:
    # Loader results are cached: never mutate the shared instance.
    return InstrumentProfileLoader.load_directory(BUILTINS)[NAME].model_copy(deep=True)


# ── The profile loads and says what it is ────────────────────────────────


def test_profile_loads_with_the_documented_models_and_honest_status(profile) -> None:
    assert profile.kind == "cli"
    assert profile.execution_status == "warning"
    assert "mock" in (profile.execution_status_detail or "")
    assert "no GPU" in (profile.execution_status_detail or "")
    names = [m.name for m in profile.models]
    assert names == [
        "qwen3.8-flash-next", "glm-5.3-flash", "qwen3.6-35b-a3b",
        "gemma-4-26b-a4b", "tiel-coder-35b-a3b",
    ]
    assert profile.default_model == "qwen3.8-flash-next"
    # One model at a time and one request at a time by default.
    assert all(m.max_concurrent == 1 for m in profile.models)
    assert all(m.cost_per_1k_input == m.cost_per_1k_output == 0 for m in profile.models)
    assert profile.capabilities == {
        "tool_use", "file_editing", "shell_access", "structured_output",
    }
    assert profile.cli is not None
    assert profile.cli.command.executable == "claude"
    assert profile.cli.command.prompt_via_stdin
    assert profile.cli.command.start_new_session


def test_profile_does_not_change_the_ollama_route() -> None:
    profiles = InstrumentProfileLoader.load_directory(BUILTINS)
    ollama = profiles["claude-code-ollama"]
    assert ollama.cli is not None
    assert ollama.cli.command.env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert ollama.effort is None


# ── Environment: pinned endpoint, local auth, nothing paid leaks ─────────


def test_env_pins_polystrata_and_never_leaks_paid_credentials(monkeypatch, profile) -> None:
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "AWS_ACCESS_KEY_ID",
                "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "OPENAI_API_KEY", "OLLAMA_HOST", "POLYSTRATA_API_KEY"):
        monkeypatch.setenv(key, "must-not-reach-the-child")
    assert profile.cli is not None
    env = build_instrument_env(profile.cli.command)
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "local"
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "3"
    for leaked in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "AWS_ACCESS_KEY_ID",
                   "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                   "OPENAI_API_KEY", "OLLAMA_HOST", "POLYSTRATA_API_KEY"):
        assert leaked not in env, leaked
    assert "must-not-reach-the-child" not in env.values()


def test_command_carries_the_model_and_no_unmapped_effort(profile) -> None:
    backend = PluginCliBackend(profile)
    argv = backend._build_command("work", timeout_seconds=None)
    assert argv[argv.index("--model") + 1] == "qwen3.8-flash-next"
    assert argv[argv.index("-p") + 1] == "-"
    assert "--effort" not in argv  # a sheet that names no effort keeps Claude Code's own
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    backend.apply_overrides({"model": "glm-5.3-flash"})
    argv = backend._build_command("work", timeout_seconds=None)
    assert argv[argv.index("--model") + 1] == "glm-5.3-flash"
    backend.clear_overrides()


# ── Effort: only what PolyStrata documents is mapped ─────────────────────


@pytest.mark.parametrize(("level", "native"), [
    ("minimal", "low"), ("low", "low"), ("medium", "medium"), ("high", "high"),
])
def test_effort_levels_reach_claude_code(profile, level, native) -> None:
    argv = PluginCliBackend(profile)._build_command(
        "work", timeout_seconds=None, effort=level,
    )
    assert argv[argv.index("--effort") + 1] == native


@pytest.mark.parametrize("level", ["xhigh", "max"])
def test_levels_polystrata_does_not_document_are_refused(profile, level) -> None:
    backend = PluginCliBackend(profile)
    assert backend.effort_refusal(level, schema_mode=False) is not None
    with pytest.raises(ValueError):
        backend._build_command("work", timeout_seconds=None, effort=level)


def test_tools_flag_is_the_claude_code_one(profile) -> None:
    argv = PluginCliBackend(profile)._build_command(
        "work", timeout_seconds=None, tools=("Read", "Grep"),
    )
    assert argv[argv.index("--tools") + 1] == "Read,Grep"


# ── A score that names the instrument validates ──────────────────────────


def test_score_on_the_profile_passes_mzt_validate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    clear_profile_memo()
    score = tmp_path / "poly.yaml"
    score.write_text(dedent("""\
        name: polystrata-seam
        description: One sheet on a local sparse model through PolyStrata
        workspace: ./ws
        instrument: claude-code-polystrata
        instrument_config:
          model: qwen3.8-flash-next
          effort: low
          tools: [Read, Grep]
        instrument_fallbacks: []
        sheet: {size: 1, total_items: 1}
        prompt:
          template: "Reply with one word."
        """))
    result = CliRunner().invoke(app, ["validate", str(score)])
    assert result.exit_code == 0, result.stdout
    assert "V335" not in result.stdout and "V337" not in result.stdout


def test_score_asking_for_max_effort_is_refused_by_validate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    clear_profile_memo()
    score = tmp_path / "poly-max.yaml"
    score.write_text(dedent("""\
        name: polystrata-max
        description: effort max is not a PolyStrata level
        workspace: ./ws
        instrument: claude-code-polystrata
        instrument_config: {effort: max}
        instrument_fallbacks: []
        sheet: {size: 1, total_items: 1}
        prompt:
          template: "Reply with one word."
        """))
    result = CliRunner().invoke(app, ["validate", str(score)])
    assert result.exit_code != 0
    assert "V335" in result.stdout


# ── The documented overlay: another port and an API key ──────────────────


def test_overlay_extends_the_builtin_for_a_keyed_server_on_another_port(
    tmp_path, monkeypatch,
) -> None:
    clear_profile_memo()
    org = tmp_path / "org"
    org.mkdir()
    (org / "poly-keyed.yaml").write_text(dedent("""\
        extends: claude-code-polystrata
        name: poly-keyed
        cli:
          command:
            required_env: [POLYSTRATA_API_KEY]
            env:
              ANTHROPIC_BASE_URL: http://127.0.0.1:8081
              ANTHROPIC_AUTH_TOKEN: ${POLYSTRATA_API_KEY}
        """))
    monkeypatch.setenv("POLYSTRATA_API_KEY", "s3cret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "paid-key-must-not-reach-child")
    got = load_all_profiles(organization_dir=org, venue_dir=tmp_path / "none")["poly-keyed"]
    assert got.models == load_all_profiles(
        organization_dir=tmp_path / "x", venue_dir=tmp_path / "y",
    )[NAME].models
    assert got.effort is not None and got.tools is not None
    assert got._extends == (NAME, "builtin")
    assert got.cli is not None
    env = build_instrument_env(got.cli.command)
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8081"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "s3cret"
    assert env["ANTHROPIC_API_KEY"] == ""  # still cleared by the base
    assert env["POLYSTRATA_API_KEY"] == "s3cret"


# ── The mock Anthropic-compatible server and the real claude binary ──────


class _Seam:
    """Records every request; answers /v1/messages with a streamed text reply."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any] | None, str | None]] = []
        seam = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def _send(self, status: int, ctype: str, payload: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                seam.requests.append((self.path, None, self.headers.get("Authorization")))
                if self.path == "/health":
                    self._send(200, "application/json", b'{"status": "ok", "loaded": true}')
                else:
                    self._send(404, "application/json", b"{}")

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seam.requests.append((self.path, body, self.headers.get("Authorization")))
                if self.path.split("?")[0].endswith("/count_tokens"):
                    self._send(200, "application/json", b'{"input_tokens": 10}')
                    return
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": "msg_poly", "type": "message", "role": "assistant",
                        "model": body["model"], "content": [], "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    }}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": REPLY}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {
                        "stop_reason": "end_turn", "stop_sequence": None,
                    }, "usage": {"output_tokens": 4}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                self._send(200, "text/event-stream", "".join(
                    f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode())

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def messages(self) -> list[dict[str, Any]]:
        return [b for p, b, _ in self.requests if b is not None
                and p.split("?")[0] == "/v1/messages"]


@pytest.fixture
def seam() -> Iterator[_Seam]:
    s = _Seam()
    s.thread.start()
    try:
        yield s
    finally:
        s.server.shutdown()
        s.server.server_close()
        s.thread.join(timeout=5)


def _isolated(profile: InstrumentProfile, tmp_path: Path, monkeypatch, origin: str) -> None:
    """Point the profile's own command at the seam; keep claude off the real HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert profile.cli is not None
    command = profile.cli.command
    command.env["ANTHROPIC_BASE_URL"] = origin
    command.env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "claude")
    # No user settings, hooks or plugins: the seam must see only the profile's route.
    command.extra_flags = ["--settings", "{}", "--setting-sources", ""]


@needs_claude
@pytest.mark.timeout(90)
async def test_real_claude_reaches_the_server_with_model_auth_effort_and_tools(
    tmp_path, monkeypatch, profile, seam,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "paid-key-must-not-reach-the-server")
    _isolated(profile, tmp_path, monkeypatch, seam.origin)
    backend = PluginCliBackend(profile, working_directory=tmp_path)
    backend.apply_overrides({"model": "glm-5.3-flash"})
    try:
        result = await backend.execute(
            "Reply POLYSTRATA", timeout_seconds=60,
        )
    finally:
        backend.clear_overrides()
    assert result.success, (result.error_message, result.stderr)
    assert result.stdout and REPLY in result.stdout
    assert (result.input_tokens, result.output_tokens) == (10, 4)
    messages = seam.messages()
    assert messages
    assert {m["model"] for m in messages} == {"glm-5.3-flash"}
    # Claude Code's 32,000-token ask: the reason the docs demand fit_max_tokens.
    assert any(m.get("max_tokens") == 32000 for m in messages)
    auths = {a for _, _, a in seam.requests}
    assert auths == {"Bearer local"}, auths


@needs_claude
@pytest.mark.timeout(90)
async def test_tools_narrowing_and_effort_are_on_the_wire(
    tmp_path, monkeypatch, profile, seam,
) -> None:
    _isolated(profile, tmp_path, monkeypatch, seam.origin)
    backend = PluginCliBackend(profile, working_directory=tmp_path)
    argv = backend._build_command(
        "-", timeout_seconds=None, effort="low", tools=("Read", "Grep"),
    )
    assert profile.cli is not None
    done = subprocess.run(
        argv, input="Reply POLYSTRATA", capture_output=True, text=True, timeout=60,
        cwd=tmp_path, env=build_instrument_env(profile.cli.command),
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    messages = seam.messages()
    assert messages
    efforts = {(m.get("output_config") or {}).get("effort") for m in messages}
    assert efforts == {"low"}, efforts
    tool_names = {t["name"] for m in messages for t in m.get("tools", [])}
    # Claude Code adds its own DeferredToolPlaceholder entry; no real tool beyond the two.
    assert {"Read", "Grep"} <= tool_names, tool_names
    assert tool_names - {"Read", "Grep", "DeferredToolPlaceholder"} == set(), tool_names


@needs_claude
@pytest.mark.timeout(90)
async def test_overlay_with_a_key_reaches_the_server_as_bearer(
    tmp_path, monkeypatch, seam,
) -> None:
    clear_profile_memo()
    org = tmp_path / "org"
    org.mkdir()
    (org / "poly-keyed.yaml").write_text(dedent(f"""\
        extends: claude-code-polystrata
        name: poly-keyed
        cli:
          command:
            required_env: [POLYSTRATA_API_KEY]
            env:
              ANTHROPIC_BASE_URL: {seam.origin}
              ANTHROPIC_AUTH_TOKEN: ${{POLYSTRATA_API_KEY}}
              CLAUDE_CONFIG_DIR: {tmp_path / "claude"}
            extra_flags: [--settings, "{{}}", --setting-sources, ""]
        """))
    monkeypatch.setenv("POLYSTRATA_API_KEY", "s3cret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    keyed = load_all_profiles(organization_dir=org, venue_dir=tmp_path / "none")["poly-keyed"]
    backend = PluginCliBackend(keyed.model_copy(deep=True), working_directory=tmp_path)
    result = await backend.execute("Reply POLYSTRATA", timeout_seconds=60)
    assert result.success, (result.error_message, result.stderr)
    assert {a for _, _, a in seam.requests} == {"Bearer s3cret"}


@needs_claude
@pytest.mark.timeout(120)
async def test_a_stopped_server_fails_the_sheet_instead_of_succeeding(
    tmp_path, monkeypatch, profile,
) -> None:
    """Nothing answers: the attempt fails fast and typed, never as a silent success.

    The availability probe of a CLI-kind profile only finds the binary, so this
    first-sheet failure is the readiness signal (documented gap).
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _isolated(profile, tmp_path, monkeypatch, f"http://127.0.0.1:{port}")
    backend = PluginCliBackend(profile, working_directory=tmp_path)
    result = await backend.execute("Reply POLYSTRATA", timeout_seconds=100)
    assert not result.success
    assert result.error_type == "api_error"
    assert "Connection refused" in (result.error_message or "")


def test_the_mock_server_speaks_the_health_route_polystrata_documents(seam) -> None:
    """The documented manual probe (`curl /health`) against the seam's shape."""
    with urllib.request.urlopen(f"{seam.origin}/health", timeout=5) as resp:
        body = json.loads(resp.read())
    assert body["loaded"] is True
