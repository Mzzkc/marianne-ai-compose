"""Profile endpoint grants reach a filtered child, including the real Claude CLI."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from marianne.core.config.instruments import CliCommand
from marianne.execution.instruments.cli_backend import PluginCliBackend, build_instrument_env
from marianne.instruments.loader import InstrumentProfileLoader

BUILTINS = Path(__file__).resolve().parents[1] / "src/marianne/instruments/builtins"
# Single seam-test prompt: sent via stdin, asserted in the parsed result,
# and echoed by the mock server — must stay identical across all three uses.
SEAM_PROMPT = "Reply LOCAL_SEAM_OK"
# The mock server's streamed reply and the expected parsed result.
SEAM_REPLY = "LOCAL_SEAM_OK"


def test_pinned_endpoint_and_token_do_not_require_parent_grants(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    command = CliCommand(executable="claude", required_env=[], env={
        "ANTHROPIC_BASE_URL": "http://localhost:11434", "ANTHROPIC_AUTH_TOKEN": "local",
    })
    env = build_instrument_env(command)
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "local"


def test_expand_endpoint_override_and_filter_unrelated_credentials(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:12345")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://unused.example")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    command = CliCommand(executable="claude", required_env=["OLLAMA_HOST"], env={
        "ANTHROPIC_BASE_URL": "${OLLAMA_HOST}", "ANTHROPIC_AUTH_TOKEN": "local",
    })
    env = build_instrument_env(command)
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:12345"
    assert env["OLLAMA_HOST"] == "http://localhost:12345"
    assert "OPENAI_API_KEY" not in env
    assert env["PATH"] == os.environ["PATH"]


@pytest.mark.skipif(not shutil.which("claude"), reason="Claude Code is not installed")
@pytest.mark.timeout(60)
@pytest.mark.parametrize("profile_name", ["claude-code", "claude-code-ollama"])
def test_real_claude_posts_to_profile_pinned_anthropic_endpoint(
    tmp_path, monkeypatch, profile_name,
) -> None:
    """Exercise the real binary against a local SSE server, never a paid provider."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("Authorization")))
            if self.path.split("?")[0].endswith("/count_tokens"):
                payload = b'{"input_tokens": 10}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            events = [
                ("message_start", {"type": "message_start", "message": {
                    "id": "msg_local", "type": "message", "role": "assistant",
                    "model": body["model"], "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0},
                }}),
                ("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "text_delta", "text": SEAM_REPLY}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {
                    "stop_reason": "end_turn", "stop_sequence": None,
                }, "usage": {"output_tokens": 4}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            payload = "".join(f"event: {event}\ndata: {json.dumps(data)}\n\n"
                              for event, data in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    process = None
    try:
        profile = InstrumentProfileLoader.load_directory(BUILTINS)[profile_name].model_copy(
            deep=True,
        )
        assert profile.cli is not None
        command = profile.cli.command.model_copy(deep=True)
        # Same invocation contract as the local derivative; no provider-specific effort.
        command.extra_flags = []
        command.env = {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "ANTHROPIC_AUTH_TOKEN": "local",
            "ANTHROPIC_API_KEY": "",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        command.env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "claude")
        command.required_env = []
        # No tools needed to prove the transport, so do not bypass permissions.
        command.auto_approve_flag = None
        profile.cli.command = command
        profile.default_model = "llama3.1:8b"
        backend = PluginCliBackend(profile, working_directory=tmp_path)
        # Private-API coupling: _build_command builds the argv the public async
        # run path would launch; used directly because this seam test drives a
        # real subprocess synchronously. Update here if the private signature
        # changes (a public builder would remove this coupling).
        args = backend._build_command(SEAM_PROMPT, timeout_seconds=None)
        args.extend(["--settings", "{}", "--setting-sources", "", "--tools", ""])
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=tmp_path, env=build_instrument_env(command), start_new_session=True,
        )
        stdout, stderr = process.communicate(SEAM_PROMPT, timeout=40)
        assert process.returncode == 0, (stdout, stderr)
        data = json.loads(stdout)
        if isinstance(data, list):
            data = next(item for item in reversed(data) if item.get("type") == "result")
        assert data["result"] == SEAM_REPLY
        assert data.get("is_error") is False
        messages = [body for path, body, _ in requests if path.split("?")[0] == "/v1/messages"]
        assert messages
        assert any(body["model"] == "llama3.1:8b" for body in messages)
        assert any(auth == "Bearer local" for _, _, auth in requests)
    finally:
        if process is not None:
            # Reap the owned process group on every path, including timeouts.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        server.shutdown()
        server.server_close()
        thread.join()


def test_local_profile_filters_parent_cloud_routing_and_credentials(monkeypatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "AWS_ACCESS_KEY_ID",
                "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_DEFAULT_SONNET_MODEL"):
        monkeypatch.setenv(key, "must-not-reach-local-child")
    profile = InstrumentProfileLoader.load_directory(BUILTINS)["claude-code-ollama"]
    assert profile.cli is not None
    env = build_instrument_env(profile.cli.command)
    assert env is not None
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "local"
    assert env["ANTHROPIC_API_KEY"] == ""
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "CLAUDE_CODE_USE_VERTEX" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in env
