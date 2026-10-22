"""Qwen Code's pinned headless contract through the real generic backend."""

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from marianne.core.config.job import JobConfig
from marianne.core.sheet import build_sheets
from marianne.execution.instruments.cli_backend import PluginCliBackend
from marianne.instruments.loader import InstrumentProfileLoader

BUILTINS = Path(__file__).parents[1] / "src/marianne/instruments/builtins"
FIXTURES = Path(__file__).parent / "fixtures/qwen_code"


def qwen_backend() -> PluginCliBackend:
    profiles = InstrumentProfileLoader.load_directory(BUILTINS)
    assert "qwen-code" in profiles, "Qwen Code must ship as a discoverable built-in"
    return PluginCliBackend(profiles["qwen-code"].model_copy(deep=True))


class TestQwenProfileContract:
    def test_profile_discovery_and_command(self) -> None:
        backend = qwen_backend()
        prompt = "Create a file named 'hello world.py' and print \"hello\""
        assert backend._build_command(prompt, timeout_seconds=None) == [
            "qwen", "--yolo", "--output-format", "json",
        ]
        assert backend._encode_stdin_prompt(prompt) == prompt.encode("utf-8")
        backend.apply_overrides({"model": "provider-model"})
        assert backend._build_command(prompt, timeout_seconds=None) == [
            "qwen", "--yolo", "--output-format", "json", "--model", "provider-model",
        ]

    def test_terminal_array_text_and_usage(self) -> None:
        result = qwen_backend()._parse_output(
            (FIXTURES / "terminal-success.json").read_text(), "", exit_code=0,
        )
        assert result.success
        assert result.stdout == "Created greeting.py."
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    @pytest.mark.parametrize("exit_code", [0, 1])
    def test_terminal_error(self, exit_code: int) -> None:
        result = qwen_backend()._parse_output(
            (FIXTURES / "terminal-error.json").read_text(), "", exit_code=exit_code,
        )
        assert not result.success
        assert result.error_message == "Unsupported slash command in non-interactive mode"

    def test_capabilities_match_supported_execution(self) -> None:
        backend = qwen_backend()
        assert backend._profile.capabilities == {"tool_use", "file_editing", "shell_access"}
        assert backend._profile.models == []
        assert backend._profile.default_model is None
        assert backend._cli.interactive is None
        with pytest.raises(ValueError, match="json_schema_flag"):
            backend._build_command(
                "hello", timeout_seconds=None,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "test", "schema": {"type": "object"},
                }},
            )


class TestQwenExecution:
    async def test_score_alias_and_per_sheet_model_reach_backend(self, tmp_path: Path) -> None:
        from marianne.daemon.baton.backend_pool import create_backend_for_instrument
        from marianne.instruments.registry import InstrumentRegistry

        config = JobConfig(
            name="qwen-routing", workspace=str(tmp_path), instrument="qwen-code",
            instruments={"coder": {"profile": "qwen-code", "config": {"model": "custom"}}},
            sheet={"size": 1, "total_items": 2, "per_sheet_instruments": {1: "coder"}},
            prompt={"template": "Write code"},
        )
        first, second = build_sheets(config)
        assert first.instrument_name == second.instrument_name == "qwen-code"
        assert first.instrument_config["model"] == "custom"
        assert "model" not in second.instrument_config
        registry = InstrumentRegistry()
        registry.register(qwen_backend()._profile)
        backends = []
        try:
            for sheet in [first, second]:
                backend = create_backend_for_instrument(registry, sheet.instrument_name)
                backends.append(backend)
                assert isinstance(backend, PluginCliBackend)
                backend.apply_overrides(sheet.instrument_config)
            assert backends[0]._build_command("hi", timeout_seconds=None) == [
                "qwen", "--yolo", "--output-format", "json", "--model", "custom",
            ]
            assert backends[1]._build_command("hi", timeout_seconds=None) == [
                "qwen", "--yolo", "--output-format", "json",
            ]
        finally:
            for backend in backends:
                await backend.close()

    async def test_subprocess_success_and_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backend = fake_qwen(tmp_path, '''
import json, os, sys
from pathlib import Path
Path("request.json").write_text(json.dumps({
    "argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(),
    "env": dict(os.environ),
}))
print(FIXTURE)
''')
        monkeypatch.setenv("OPENAI_API_KEY", "test-qwen-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.invalid/v1")
        monkeypatch.setenv("OPENAI_MODEL", "configured-model")
        monkeypatch.setenv("QWEN_MODEL", "fallback-model")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "unrelated-dummy-secret")
        backend.apply_overrides({"model": "chosen-model"})
        prompt = "Print 'hello world' and \"quoted text\""
        try:
            result = await backend.execute(prompt, timeout_seconds=10)
            assert result.success
            assert result.stdout == "Created greeting.py."
            assert (result.input_tokens, result.output_tokens) == (100, 50)
            captured = json.loads((tmp_path / "request.json").read_text())
            assert captured["stdin"] == prompt
            assert captured["argv"] == [
                "--yolo", "--output-format", "json", "--model", "chosen-model",
            ]
            assert captured["cwd"] == str(tmp_path)
            assert captured["env"]["OPENAI_API_KEY"] == "test-qwen-key"
            assert captured["env"]["OPENAI_BASE_URL"] == "https://provider.invalid/v1"
            assert captured["env"]["OPENAI_MODEL"] == "configured-model"
            assert captured["env"]["QWEN_MODEL"] == "fallback-model"
            assert captured["env"]["HOME"] == os.environ["HOME"]
            assert "PATH" in captured["env"]
            assert "ANTHROPIC_API_KEY" not in captured["env"]
        finally:
            await backend.close()

    @pytest.mark.parametrize(("diagnostic", "error_type", "rate_limited"), [
        ("401 unauthorized", "auth", False),
        ("429 rate limit exceeded", None, True),
        ("503 service unavailable", "capacity", False),
    ])
    async def test_nonzero_failures(
        self, tmp_path: Path, diagnostic: str, error_type: str | None, rate_limited: bool,
    ) -> None:
        backend = fake_qwen(
            tmp_path, f"import sys\nprint({diagnostic!r}, file=sys.stderr)\nsys.exit(1)",
        )
        try:
            result = await backend.execute("hello", timeout_seconds=10)
            assert not result.success
            assert result.error_type == error_type
            assert result.rate_limited == rate_limited
            assert diagnostic in result.stderr
        finally:
            await backend.close()

    async def test_missing_executable(self, tmp_path: Path) -> None:
        backend = qwen_backend()
        backend._cli.command.executable = str(tmp_path / "absent-qwen")
        try:
            result = await backend.execute("hello", timeout_seconds=5)
            assert not result.success
            assert result.error_type == "executable_not_found"
            assert "Executable not found" in result.stderr
        finally:
            await backend.close()

    async def test_timeout_reaps_child(self, tmp_path: Path) -> None:
        backend = fake_qwen(tmp_path, '''
import os, time
from pathlib import Path
Path("child.pid").write_text(str(os.getpid()))
print("started", flush=True)
time.sleep(60)
''')
        try:
            result = await backend.execute("hello", timeout_seconds=1)
            assert not result.success
            assert result.exit_reason == "timeout"
            child_pid = int((tmp_path / "child.pid").read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            await backend.close()

    @pytest.mark.parametrize("exit_code", [0, 1])
    async def test_error_envelope_exit_semantics(self, tmp_path: Path, exit_code: int) -> None:
        backend = fake_qwen(
            tmp_path, f"import sys\nprint(FIXTURE)\nsys.exit({exit_code})",
            fixture="terminal-error.json",
        )
        try:
            result = await backend.execute("/unsupported", timeout_seconds=10)
            assert not result.success
            assert result.error_message == "Unsupported slash command in non-interactive mode"
        finally:
            await backend.close()

    def test_malformed_output_fallback(self) -> None:
        backend = qwen_backend()
        for stdout in ["not JSON", '{"response":"wrong key"}']:
            result = backend._parse_output(stdout, "", exit_code=0)
            assert result.success
            assert result.stdout == stdout
        result = backend._parse_output(
            '{"result":"Explain a rate limit", "is_error":false}', "", exit_code=0,
        )
        assert result.success
        assert result.stdout == "Explain a rate limit"
        assert not result.rate_limited


def fake_qwen(
    tmp_path: Path, body: str, *, fixture: str = "terminal-success.json",
) -> PluginCliBackend:
    """A real child process speaking the pinned vendor contract, without network."""
    executable = tmp_path / "qwen"
    executable.write_text(
        f"#!{sys.executable}\nFIXTURE = {(FIXTURES / fixture).read_text()!r}\n{body}\n",
    )
    executable.chmod(0o755)
    backend = qwen_backend()
    backend._working_directory = tmp_path
    backend._cli.command.executable = str(executable)
    return backend


class TestQwenDocumentation:
    def test_documented_score_resolves_qwen(self, tmp_path: Path) -> None:
        guide = (BUILTINS.parents[3] / "docs/instrument-guide.md").read_text()
        assert "### Qwen Code" in guide
        section = guide.split("### Qwen Code", 1)[1]
        example = section.split("```yaml\n", 1)[1].split("```", 1)[0]
        data = yaml.safe_load(example)
        data["workspace"] = str(tmp_path)
        config = JobConfig.model_validate(data)
        first, second = build_sheets(config)
        assert first.instrument_name == second.instrument_name == "qwen-code"
        assert first.instrument_config["model"] == "your-provider-model"
        assert "model" not in second.instrument_config
