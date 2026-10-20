"""Tests for PluginCliBackend stdin prompt delivery (F-105).

When an instrument profile sets `prompt_via_stdin: true`, the backend
must pass the prompt via subprocess stdin instead of CLI args. This
avoids ARG_MAX limits on large prompts and preserves the proven behavior of
the retired native implementation.

TDD: Red first, then green.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
)
from marianne.execution.instruments.cli_backend import PluginCliBackend


def _make_profile(
    *,
    prompt_via_stdin: bool = False,
    stdin_sentinel: str | None = None,
    start_new_session: bool = False,
    prompt_flag: str | None = "-p",
    output_format: str = "json",
    result_path: str | None = "result",
    extra_flags: list[str] | None = None,
) -> InstrumentProfile:
    """Create a minimal CLI profile for testing."""
    return InstrumentProfile(
        name="test-instrument",
        display_name="Test Instrument",
        description="Test CLI instrument",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(
                executable="test-cli",
                prompt_flag=prompt_flag,
                prompt_via_stdin=prompt_via_stdin,
                stdin_sentinel=stdin_sentinel,
                start_new_session=start_new_session,
                output_format_flag="--output-format",
                output_format_value=output_format,
                extra_flags=extra_flags or [],
            ),
            output=CliOutputConfig(
                format=output_format,
                result_path=result_path,
            ),
            errors=CliErrorConfig(),
        ),
    )


def _wire_pipes(
    proc: AsyncMock,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    hang: bool = False,
) -> None:
    """Give a mocked process REAL-shaped pipe readers (#352 inc-2).

    The drain loop calls ``await proc.stdout.read(n)`` until it returns
    ``b""``. An unconfigured AsyncMock attribute returns a truthy
    MagicMock from read() forever — an infinite non-yielding loop that
    exhausts system memory (crashed WSL). Every mocked-proc test on the
    execute() path MUST wire pipes through this helper.
    """
    if hang:
        async def _hang(_n: int = -1) -> bytes:
            await asyncio.sleep(3600)
            return b""

        proc.stdout = AsyncMock()
        proc.stdout.read = _hang
        proc.stderr = AsyncMock()
        proc.stderr.read = _hang
        # wait() must NOT hang: the kill-on-exit helper awaits it after
        # kill(), and a real killed process exits promptly.
        proc.wait = AsyncMock(return_value=None)
        return

    proc.stdout = AsyncMock()
    proc.stdout.read = AsyncMock(side_effect=[stdout, b""] if stdout else [b""])
    proc.stderr = AsyncMock()
    proc.stderr.read = AsyncMock(side_effect=[stderr, b""] if stderr else [b""])
    proc.wait = AsyncMock(return_value=proc.returncode)


class TestCliCommandStdinFields:
    """Test that CliCommand accepts the new stdin fields."""

    def test_prompt_via_stdin_defaults_true(self) -> None:
        cmd = CliCommand(executable="test")
        assert cmd.prompt_via_stdin is True

    def test_prompt_via_stdin_can_be_set_false(self) -> None:
        cmd = CliCommand(executable="test", prompt_via_stdin=False)
        assert cmd.prompt_via_stdin is False

    def test_stdin_sentinel_defaults_none(self) -> None:
        cmd = CliCommand(executable="test")
        assert cmd.stdin_sentinel is None

    def test_stdin_sentinel_can_be_set(self) -> None:
        cmd = CliCommand(executable="test", stdin_sentinel="-")
        assert cmd.stdin_sentinel == "-"

    def test_start_new_session_defaults_false(self) -> None:
        cmd = CliCommand(executable="test")
        assert cmd.start_new_session is False

    def test_start_new_session_can_be_set_true(self) -> None:
        cmd = CliCommand(executable="test", start_new_session=True)
        assert cmd.start_new_session is True


class TestBuildCommandStdin:
    """Test _build_command behavior with stdin mode."""

    def test_normal_mode_includes_prompt_in_args(self) -> None:
        profile = _make_profile(prompt_via_stdin=False)
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("hello world", timeout_seconds=None)
        assert "hello world" in cmd

    def test_stdin_mode_excludes_prompt_from_args(self) -> None:
        """When prompt_via_stdin is True, the prompt text must NOT appear in args."""
        profile = _make_profile(prompt_via_stdin=True)
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("hello world", timeout_seconds=None)
        assert "hello world" not in cmd

    def test_stdin_mode_with_sentinel_includes_sentinel(self) -> None:
        """When stdin_sentinel is set, it replaces the prompt in the args."""
        profile = _make_profile(
            prompt_via_stdin=True,
            stdin_sentinel="-",
            prompt_flag="-p",
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("hello world", timeout_seconds=None)
        # Should have -p - in the args
        assert "-p" in cmd
        assert "-" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "-"

    def test_stdin_mode_without_sentinel_omits_prompt_entirely(self) -> None:
        """No sentinel = no prompt in args at all (some CLIs read from stdin by default)."""
        profile = _make_profile(
            prompt_via_stdin=True,
            stdin_sentinel=None,
            prompt_flag="-p",
        )
        backend = PluginCliBackend(profile)
        cmd = backend._build_command("hello world", timeout_seconds=None)
        # Neither -p nor the prompt should appear
        assert "-p" not in cmd
        assert "hello world" not in cmd

    def test_stdin_mode_preamble_not_in_args(self) -> None:
        """Preamble is assembled into the prompt, which goes to stdin not args."""
        profile = _make_profile(prompt_via_stdin=True, stdin_sentinel="-")
        backend = PluginCliBackend(profile)
        backend.set_preamble("SYSTEM: you are helpful")
        cmd = backend._build_command("hello world", timeout_seconds=None)
        assert "SYSTEM: you are helpful" not in " ".join(cmd)
        assert "hello world" not in " ".join(cmd)


class TestExecuteStdin:
    """Test execute() passes prompt via stdin when configured."""

    @pytest.mark.asyncio
    async def test_stdin_mode_writes_prompt_to_stdin(self) -> None:
        """The prompt must be written to the subprocess stdin pipe."""
        profile = _make_profile(prompt_via_stdin=True, stdin_sentinel="-")
        backend = PluginCliBackend(profile)

        result_json = json.dumps({"result": "response text"})

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=result_json.encode())

        # Mock stdin as a MagicMock with write/drain/close
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdin.close = MagicMock()
        mock_proc.stdin = mock_stdin

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await backend.execute("test prompt", timeout_seconds=30)

        # Verify stdin=PIPE was passed
        call_kwargs = mock_exec.call_args
        assert call_kwargs.kwargs.get("stdin") == asyncio.subprocess.PIPE

        # Verify prompt was written to stdin
        mock_stdin.write.assert_called_once()
        written_bytes = mock_stdin.write.call_args[0][0]
        assert b"test prompt" in written_bytes

        # Verify stdin was closed (signals EOF)
        mock_stdin.drain.assert_awaited_once()
        mock_stdin.close.assert_called_once()

        assert result.success is True

    @pytest.mark.asyncio
    async def test_normal_mode_does_not_use_stdin_pipe(self) -> None:
        """Normal mode should NOT set stdin=PIPE."""
        profile = _make_profile(prompt_via_stdin=False)
        backend = PluginCliBackend(profile)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=b'{"result": "ok"}')

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await backend.execute("test prompt", timeout_seconds=30)

        # stdin should not be PIPE
        call_kwargs = mock_exec.call_args
        assert (
            call_kwargs.kwargs.get("stdin") is None
            or call_kwargs.kwargs.get("stdin") != asyncio.subprocess.PIPE
        )

    @pytest.mark.asyncio
    async def test_large_prompt_without_stdin_contract_stays_in_argv(self) -> None:
        """Do not force stdin for CLIs that have no declared stdin contract."""
        profile = _make_profile(prompt_via_stdin=False, stdin_sentinel=None)
        backend = PluginCliBackend(profile)
        large_prompt = "x" * 40_000

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=b'{"result": "ok"}')

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await backend.execute(large_prompt, timeout_seconds=30)

        call_args = list(mock_exec.call_args.args)
        assert "-p" in call_args
        assert large_prompt in call_args
        assert mock_exec.call_args.kwargs.get("stdin") is None

    @pytest.mark.asyncio
    async def test_large_prompt_with_sentinel_forces_stdin(self) -> None:
        """A declared stdin sentinel makes forced large-prompt stdin safe."""
        profile = _make_profile(prompt_via_stdin=False, stdin_sentinel="-")
        backend = PluginCliBackend(profile)
        large_prompt = "x" * 40_000

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=b'{"result": "ok"}')

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdin.close = MagicMock()
        mock_proc.stdin = mock_stdin

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await backend.execute(large_prompt, timeout_seconds=30)

        call_args = list(mock_exec.call_args.args)
        assert "-p" in call_args
        assert call_args[call_args.index("-p") + 1] == "-"
        assert large_prompt not in call_args
        assert mock_exec.call_args.kwargs.get("stdin") == asyncio.subprocess.PIPE
        assert large_prompt.encode("utf-8") in mock_stdin.write.call_args[0][0]

    @pytest.mark.asyncio
    async def test_stdin_mode_with_preamble(self) -> None:
        """Preamble + prompt should be assembled and written to stdin."""
        profile = _make_profile(prompt_via_stdin=True, stdin_sentinel="-")
        backend = PluginCliBackend(profile)
        backend.set_preamble("SYSTEM PREAMBLE")

        result_json = json.dumps({"result": "ok"})
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=result_json.encode())

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdin.close = MagicMock()
        mock_proc.stdin = mock_stdin

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await backend.execute("user prompt", timeout_seconds=30)

        written_bytes = mock_stdin.write.call_args[0][0]
        written_text = written_bytes.decode()
        assert "SYSTEM PREAMBLE" in written_text
        assert "user prompt" in written_text

    @pytest.mark.asyncio
    async def test_stdin_mode_timeout_kills_process(self) -> None:
        """Timeout should still work correctly with stdin mode."""
        profile = _make_profile(prompt_via_stdin=True, stdin_sentinel="-")
        backend = PluginCliBackend(profile)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        # Must be explicit: AsyncMock() auto-mocks returncode as a MagicMock,
        # which is not None and would skip the liveness check in the new
        # _kill_process_group_if_alive path (Phase 1 Process Lifecycle).
        mock_proc.returncode = None

        # stdin operations succeed
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdin.close = MagicMock()
        mock_proc.stdin = mock_stdin

        # Pipes hang (a real asyncio.sleep — it must YIELD so wait_for's
        # timeout can fire; a non-yielding mock would loop or stall forever).
        _wire_pipes(mock_proc, hang=True)
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute("test prompt", timeout_seconds=1)

        assert result.success is False
        # No process group was captured (profile without start_new_session):
        # the direct child is terminated first so an instrument supervisor
        # can sweep what it adopted. The mocked wait() returns at once (the
        # child "exited" on TERM), so no escalation to kill() follows.
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()


class TestStartNewSession:
    """Test start_new_session process group isolation."""

    @pytest.mark.asyncio
    async def test_start_new_session_flag_passed_to_subprocess(self) -> None:
        """When start_new_session is True, the subprocess should get start_new_session=True."""
        profile = _make_profile(start_new_session=True)
        backend = PluginCliBackend(profile)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=b'{"result": "ok"}')

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await backend.execute("test", timeout_seconds=30)

        call_kwargs = mock_exec.call_args
        assert call_kwargs.kwargs.get("start_new_session") is True

    @pytest.mark.asyncio
    async def test_no_start_new_session_by_default(self) -> None:
        """Default behavior should NOT set start_new_session."""
        profile = _make_profile(start_new_session=False)
        backend = PluginCliBackend(profile)

        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        _wire_pipes(mock_proc, stdout=b'{"result": "ok"}')

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await backend.execute("test", timeout_seconds=30)

        call_kwargs = mock_exec.call_args
        # start_new_session should either be False or not present
        assert call_kwargs.kwargs.get("start_new_session") in (None, False)


class TestClaudeCodeProfile:
    """Test that the claude-code profile can be loaded with stdin fields."""

    def test_claude_code_profile_loads_with_stdin_fields(self) -> None:
        """The built-in claude-code profile should load and have stdin configured."""
        from marianne.instruments.loader import InstrumentProfileLoader

        # Load built-in profiles
        builtins_dir = (
            Path(__file__).parent.parent / "src" / "marianne" / "instruments" / "builtins"
        )
        if not builtins_dir.exists():
            pytest.skip("builtins directory not found")

        profiles = InstrumentProfileLoader.load_directory(builtins_dir)
        claude_profile = profiles.get("claude-code")
        assert claude_profile is not None
        assert claude_profile.cli is not None
        assert claude_profile.cli.command.prompt_via_stdin is True
        assert claude_profile.cli.command.stdin_sentinel == "-"
        assert claude_profile.cli.command.start_new_session is True


# ---------------------------------------------------------------------------
# JSON stdin envelope (Antigravity stream-json; E2BIG on argv, 2026-10-14)
# ---------------------------------------------------------------------------


def _json_envelope_profile(*, output_format: str = "jsonl") -> InstrumentProfile:
    """A profile whose only stdin path is one NDJSON line (agy-shaped)."""
    return InstrumentProfile(
        name="stream-json-cli",
        display_name="Stream JSON CLI",
        description="reads one NDJSON turn from stdin",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(
                executable="stream-json-cli",
                prompt_flag="-p",
                prompt_via_stdin=True,
                stdin_sentinel="",
                stdin_json_template={
                    "event": "user",
                    "message": {"role": "user", "content": "{prompt}"},
                },
                output_format_flag="--output-format",
                output_format_value="stream-json",
            ),
            output=CliOutputConfig(
                format=output_format,
                event_type_key="event",
                completion_event_type="result",
                result_path="result.response",
                error_path="result.error",
                input_tokens_path="result.usage.input_tokens",
                output_tokens_path="result.usage.output_tokens",
            ),
            errors=CliErrorConfig(),
        ),
        models=[],
    )


class TestStdinJsonTemplate:
    def test_schema_fields_default_off(self) -> None:
        assert CliCommand(executable="x").stdin_json_template is None
        assert CliOutputConfig().event_type_key == "type"

    def test_prompt_is_embedded_as_one_json_line(self) -> None:
        backend = PluginCliBackend(_json_envelope_profile())
        raw = backend._encode_stdin_prompt('say "hi"\nthen stop')
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1
        assert json.loads(raw) == {
            "event": "user",
            "message": {"role": "user", "content": 'say "hi"\nthen stop'},
        }

    def test_without_template_stdin_is_raw_text(self) -> None:
        backend = PluginCliBackend(_make_profile(prompt_via_stdin=True))
        assert backend._encode_stdin_prompt("plain") == b"plain"

    def test_empty_sentinel_keeps_the_flag_with_an_empty_value(self) -> None:
        """agy needs `-p ''` to stay in print mode while stdin carries the turn."""
        backend = PluginCliBackend(_json_envelope_profile())
        cmd = backend._build_command("hello", timeout_seconds=None)
        assert cmd[cmd.index("-p") + 1] == ""
        assert "hello" not in cmd

    def test_jsonl_completion_event_uses_event_type_key(self) -> None:
        backend = PluginCliBackend(_json_envelope_profile())
        lines = [
            json.dumps({"event": "init", "init": {"model": "m"}}),
            json.dumps({"event": "step_update", "step_update": {"text_delta": "HO"}}),
            json.dumps({"event": "result", "result": {
                "status": "SUCCESS", "response": "HOTEL\n",
                "usage": {"input_tokens": 12, "output_tokens": 2}}}),
        ]
        result = backend._parse_output("\n".join(lines), "", exit_code=0)
        assert result.success is True
        assert result.stdout == "HOTEL\n"
        assert (result.input_tokens, result.output_tokens) == (12, 2)

    def test_jsonl_error_event_names_the_error(self) -> None:
        backend = PluginCliBackend(_json_envelope_profile())
        line = json.dumps({"event": "result", "result": {
            "status": "ERROR", "response": "",
            "error": 'stream input message is missing the "event" field'}})
        result = backend._parse_output(line, "", exit_code=1)
        assert result.success is False
        assert result.error_message is not None
        assert "missing" in result.error_message

    @pytest.mark.asyncio
    async def test_real_subprocess_receives_the_envelope_for_a_150kb_prompt(
        self, tmp_path: Path,
    ) -> None:
        """End to end with a real child: a prompt far beyond the kernel's
        per-argument limit (128 KiB) arrives intact through the JSON line,
        and the result event is parsed back. The fake CLI is agy-shaped."""
        fake = tmp_path / "stream-json-cli"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "argv = sys.argv[1:]\n"
            "assert argv[argv.index('-p') + 1] == '', argv\n"
            "turn = json.loads(sys.stdin.readline())\n"
            "assert turn['event'] == 'user'\n"
            "content = turn['message']['content']\n"
            "print(json.dumps({'event': 'init', 'init': {'model': 'fake'}}))\n"
            "print(json.dumps({'event': 'result', 'result': {\n"
            "    'status': 'SUCCESS', 'response': content[-11:],\n"
            "    'usage': {'input_tokens': len(content), 'output_tokens': 11}}}))\n"
        )
        fake.chmod(0o755)
        profile = _json_envelope_profile()
        profile.cli.command.executable = str(fake)  # type: ignore[union-attr]
        backend = PluginCliBackend(profile)
        prompt = ("x" * 150_000) + "\nmarker-9731"

        result = await backend.execute(prompt, timeout_seconds=30)

        assert result.success is True, result.error_message
        assert result.stdout == "marker-9731"
        assert result.input_tokens == len(prompt)
