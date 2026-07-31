"""Config-driven CLI instrument backend.

PluginCliBackend is a generic Backend implementation driven by an
InstrumentProfile. Instead of writing Python for each CLI tool, you
write a ~30-line YAML profile and Marianne handles command construction,
output parsing, and error classification.

The music metaphor: a musician doesn't need to know how the instrument
was built — they need to know how to play it. The profile is the
instrument's spec sheet; this backend is the player.

Security: Uses asyncio.create_subprocess_exec (not shell). No shell
injection risk. Environment variables from the profile are validated
before passing to the subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from marianne.core.config.instruments import InstrumentProfile
from marianne.core.logging import get_logger
from marianne.execution.base import Backend, ExecutionResult, ExitReason
from marianne.utils.json_path import extract_json_path
from marianne.utils.process import safe_killpg as _safe_killpg

_logger = get_logger("backend.plugin_cli")


# Phase 1 grace window for SIGTERM -> SIGKILL in the subprocess kill path.
# Empirically long enough for pytest-xdist workers and claude-code/opencode
# to exit cleanly after SIGTERM; short enough not to stall cancel paths.
# See docs/specs/2026-04-16-process-lifecycle-design.md (Change 2).
_KILL_GRACE_SECONDS = 2.0

# Some CLIs, notably Antigravity, discover MCP servers from a fixed
# workspace-local config file rather than accepting a per-run config flag.
# Keep those temporary materializations serialized per target path.
_MCP_WORKSPACE_CONFIG_LOCKS: dict[Path, asyncio.Lock] = {}


async def _drain_stream(
    stream: asyncio.StreamReader | None,
    chunks: list[bytes],
    on_chunk: Callable[[bytes], None] | None = None,
) -> None:
    """Read one subprocess pipe to EOF in bounded chunks (#352 inc-2).

    ``read(65536)`` rather than ``readline()``: agent output lines can
    exceed any StreamReader limit (LimitOverrunError), and fixed-size
    chunking is binary-safe. Callers decode AFTER joining the chunks so
    multibyte sequences split across chunk boundaries decode cleanly.

    Appends into ``chunks`` in place (rather than returning) so partial
    output is observable even when the surrounding gather is cancelled
    by timeout.
    """
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not isinstance(chunk, bytes):
            # Refuse non-bytes IMMEDIATELY. A mocked pipe whose read()
            # returns a truthy non-bytes value (e.g. AsyncMock's default
            # MagicMock) would otherwise loop forever WITHOUT yielding to
            # the event loop — wait_for's timeout can never fire and the
            # appends exhaust system memory (observed as a WSL2 VM crash).
            # A loud TypeError turns that failure mode into an instant
            # test failure.
            raise TypeError(
                f"subprocess pipe read() returned {type(chunk).__name__}, "
                "expected bytes — is the process mocked without proper "
                "pipe readers?"
            )
        if not chunk:
            return
        chunks.append(chunk)
        if on_chunk is not None:
            on_chunk(chunk)


async def _kill_process_group_if_alive(
    proc: asyncio.subprocess.Process | None,
    pgid: int | None,
) -> None:
    """Terminate a spawned subprocess: SIGTERM -> grace -> SIGKILL.

    Runs idempotently on every exit path (clean completion, timeout,
    CancelledError, arbitrary exceptions). Does nothing if the process
    has already exited. Falls back to ``proc.kill()`` when no process
    group was captured (``start_new_session`` was not active).

    Part of Process Lifecycle Phase 1 — ensures subprocesses die on
    cancellation rather than surviving as orphans.
    """
    if proc is None or proc.returncode is not None:
        return

    if pgid is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass
        return

    # SIGTERM the group first — give pytest workers and MCP servers a chance
    # to flush logs, release locks, and close file descriptors cleanly.
    try:
        _safe_killpg(pgid, signal.SIGTERM, context="cli_backend.kill_grace")
    except (ProcessLookupError, PermissionError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
    except TimeoutError:
        # Grace expired — SIGKILL the entire group.
        try:
            _safe_killpg(pgid, signal.SIGKILL, context="cli_backend.kill_force")
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except TimeoutError:
            # The group kill didn't land (e.g. killpg guard-refused an
            # own-pgroup id, #376). Kill the direct child as the last
            # resort rather than awaiting an unkilled process forever.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

# System env vars that are always passed through to instrument subprocesses,
# regardless of required_env filtering. These are needed for basic process
# operation — without PATH the binary can't be found, without HOME many
# tools fail, without TERM terminal rendering breaks.
SYSTEM_ENV_VARS: frozenset[str] = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
})


class PluginCliBackend(Backend):
    """Generic CLI backend driven by an InstrumentProfile.

    Builds CLI commands, runs them via asyncio subprocess, and parses
    output according to the profile's output and error configuration.

    This is the core of the instrument plugin system — any CLI tool
    with a YAML profile can be used as a mzt instrument.
    """

    def __init__(
        self,
        profile: InstrumentProfile,
        working_directory: Path | None = None,
    ) -> None:
        """Initialize from an InstrumentProfile.

        Args:
            profile: The instrument profile describing how to invoke
                and parse this CLI tool.
            working_directory: Optional working directory for subprocess.

        Raises:
            ValueError: If the profile is not a CLI instrument.
        """
        if profile.kind != "cli" or profile.cli is None:
            raise ValueError(
                f"PluginCliBackend requires kind=cli with a cli profile, "
                f"got kind={profile.kind}"
            )

        self._profile = profile
        self._cli = profile.cli
        self._working_directory: Path | None = working_directory
        self._preamble: str | None = None
        self._prompt_extensions: list[str] = []
        self._model: str | None = profile.default_model

        # #352 inc-3: per-dispatch live output sink. Set by the baton
        # adapter (hub writer) before execute(), cleared at release.
        # Receives (stream_name, chunk) per drained chunk. Exceptions
        # are swallowed — observability must never break execution.
        self._on_output: Callable[[str, bytes], None] | None = None

        # PID tracking callbacks for orphan detection.
        # Set by the daemon's ProcessGroupManager (via BackendPool) when
        # running under the conductor. Standalone CLI mode leaves these
        # as None — no orphan tracking needed.
        self._on_process_spawned: Callable[[int], None] | None = None
        self._on_process_exited: Callable[[int], None] | None = None

        # Process Lifecycle Phase 1 — group-aware callback.
        # Set by the baton adapter with (pid, pgid) at spawn time. Its
        # presence is the signal that we are on the baton path and must
        # force start_new_session=True regardless of profile setting, so
        # that deregister/cancel can kill the whole process tree.
        # See docs/specs/2026-04-16-process-lifecycle-design.md (Change 1, 3).
        self._on_process_group_spawned: Callable[[int, int], None] | None = None

        # Override tracking is shared by profile-driven executions.
        # _saved_model stores the pre-override value so clear_overrides()
        # can restore it. _has_overrides guards against double-clear.
        self._saved_model: str | None = None
        self._has_overrides: bool = False

        # MCP config path — set by callers when an MCP config is available.
        # The profile may consume it through a command flag or a temporary
        # workspace-local config file.
        self._mcp_config_path: Path | None = None

        _logger.debug(
            "plugin_cli_backend_initialized",
            instrument=profile.name,
            executable=self._cli.command.executable,
            model=self._model,
        )

    @property
    def name(self) -> str:
        """Human-readable backend name."""
        return self._profile.display_name

    def apply_overrides(self, overrides: dict[str, object]) -> None:
        """Apply per-sheet parameter overrides for the next execution.

        Supports:
        - ``model``: Override the default_model from the instrument profile.

        Must be paired with clear_overrides() after execution. Callers
        MUST hold override_lock for the entire apply → execute → clear
        window when parallel execution is possible.
        """
        if not overrides:
            return
        self._saved_model = self._model
        self._has_overrides = True
        if "model" in overrides:
            self._model = str(overrides["model"])

    def clear_overrides(self) -> None:
        """Restore original backend parameters after per-sheet execution."""
        if not self._has_overrides:
            return
        self._model = self._saved_model
        self._saved_model = None
        self._has_overrides = False

    def set_preamble(self, preamble: str | None) -> None:
        """Set preamble to prepend to the next prompt."""
        self._preamble = preamble

    def set_prompt_extensions(self, extensions: list[str]) -> None:
        """Set prompt extensions to append to the next prompt."""
        self._prompt_extensions = list(extensions)

    def set_output_callback(
        self, callback: Callable[[str, bytes], None] | None
    ) -> None:
        """Set (or clear with None) the live output sink (#352 inc-3).

        The callback receives ``(stream_name, chunk)`` for every chunk
        drained from the subprocess pipes. It runs synchronously on the
        event loop between pipe reads — keep it cheap and non-blocking.
        """
        self._on_output = callback

    def set_mcp_config(self, config_path: Path | None) -> None:
        """Set path to an MCP config file.

        When set, ``_build_command()`` uses the instrument's ``mcp_config_flag``
        to point the CLI instrument at this config file instead of injecting
        ``mcp_disable_args``. For instruments that discover MCP config through
        a workspace file, ``execute()`` temporarily materializes this file at
        ``mcp_config_workspace_path``.

        Args:
            config_path: Path to the MCP config JSON file, or None to clear.
        """
        self._mcp_config_path = config_path

    def _workspace_mcp_config_target(self) -> Path | None:
        """Return the workspace-local MCP config target, if this profile needs one."""
        rel_path = self._cli.command.mcp_config_workspace_path
        if self._mcp_config_path is None or not rel_path:
            return None
        workspace = self._working_directory or Path.cwd()
        return workspace / rel_path

    def _workspace_mcp_config_bytes(self, target: Path) -> bytes:
        """Return bytes to write to a CLI-discovered workspace config path."""
        if self._mcp_config_path is None:
            return b""

        merge_key = self._cli.command.mcp_config_workspace_merge_key
        if not merge_key:
            return self._mcp_config_path.read_bytes()

        try:
            source_data = json.loads(self._mcp_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Generated MCP config is not valid JSON: {self._mcp_config_path}"
            ) from exc

        if not isinstance(source_data, dict):
            raise RuntimeError(
                f"Generated MCP config must be a JSON object: {self._mcp_config_path}"
            )

        try:
            target_data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        except json.JSONDecodeError:
            target_data = {}
        if not isinstance(target_data, dict):
            target_data = {}

        target_data[merge_key] = source_data.get(merge_key, {})
        return (json.dumps(target_data, indent=2) + "\n").encode("utf-8")

    def _materialize_workspace_mcp_config(
        self,
        target: Path,
    ) -> tuple[Path, bytes | None, bool] | None:
        """Materialize generated MCP config to a CLI-discovered workspace path.

        Returns a restore tuple of ``(target, previous_bytes, parent_existed)``.
        ``previous_bytes`` is ``None`` when no file existed and the file should
        be deleted after execution.
        """
        if self._mcp_config_path is None:
            return None

        source = self._mcp_config_path
        if source.resolve() == target.resolve():
            return None

        parent_existed = target.parent.exists()
        previous = target.read_bytes() if target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._workspace_mcp_config_bytes(target))

        _logger.debug(
            "plugin_cli_workspace_mcp_config_materialized",
            instrument=self._profile.name,
            source=str(source),
            target=str(target),
            merge_key=self._cli.command.mcp_config_workspace_merge_key,
            restored_existing=previous is not None,
        )
        return target, previous, parent_existed

    def _restore_workspace_mcp_config(
        self,
        restore: tuple[Path, bytes | None, bool],
    ) -> None:
        """Restore or remove a temporary workspace MCP config file."""
        target, previous, parent_existed = restore
        try:
            if previous is None:
                target.unlink(missing_ok=True)
                if not parent_existed:
                    with suppress(OSError):
                        target.parent.rmdir()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(previous)
            _logger.debug(
                "plugin_cli_workspace_mcp_config_restored",
                instrument=self._profile.name,
                target=str(target),
                restored_existing=previous is not None,
            )
        except OSError:
            _logger.warning(
                "plugin_cli_workspace_mcp_config_restore_failed",
                instrument=self._profile.name,
                target=str(target),
                exc_info=True,
            )

    def _build_prompt(self, prompt: str) -> str:
        """Assemble the full prompt with preamble and extensions.

        Args:
            prompt: The core prompt text.

        Returns:
            Complete prompt with preamble prepended and extensions appended.
        """
        parts: list[str] = []
        if self._preamble:
            parts.append(self._preamble)
        parts.append(prompt)
        parts.extend(self._prompt_extensions)
        return "\n\n".join(parts)

    def _build_command(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None,
        force_stdin: bool = False,
    ) -> list[str]:
        """Build the CLI command from the profile configuration.

        Follows the construction order specified in the design:
        [executable] [subcommand?] [auto_approve_flag?] [output_format_flag? value?]
        [model_flag? model?] [timeout_flag? timeout?] [prompt_flag?] <prompt>
        [...extra_flags]

        Args:
            prompt: The prompt text (already assembled with preamble/extensions).
            timeout_seconds: Per-execution timeout, or None.
            force_stdin: GH#188 — when True, omit prompt from args even if the
                profile uses positional delivery. Caller handles stdin.

        Returns:
            List of command arguments for subprocess.
        """
        cmd = self._cli.command
        args: list[str] = [cmd.executable]

        # Subcommand
        if cmd.subcommand:
            args.append(cmd.subcommand)

        # Auto-approve
        if cmd.auto_approve_flag:
            args.append(cmd.auto_approve_flag)

        # Output format
        if cmd.output_format_flag:
            args.append(cmd.output_format_flag)
            if cmd.output_format_value is not None:
                args.append(cmd.output_format_value)

        # Model selection
        if cmd.model_flag and self._model:
            args.append(cmd.model_flag)
            args.append(self._model)

        # Timeout
        if cmd.timeout_flag and timeout_seconds is not None:
            args.append(cmd.timeout_flag)
            args.append(str(int(timeout_seconds)))

        # Working directory flag (distinct from subprocess cwd)
        if cmd.working_dir_flag and self._working_directory:
            args.append(cmd.working_dir_flag)
            args.append(str(self._working_directory))

        # Prompt delivery — stdin mode or CLI arg.
        # GH#188: force_stdin overrides positional delivery for large prompts.
        effective_stdin = cmd.prompt_via_stdin or force_stdin
        if effective_stdin:
            # Prompt will be written to subprocess stdin in execute().
            # If a sentinel is configured, include the flag + sentinel in args
            # (e.g. '-p -' for Claude Code's stdin mode).
            if cmd.stdin_sentinel is not None and cmd.prompt_flag:
                args.append(cmd.prompt_flag)
                args.append(cmd.stdin_sentinel)
            # Otherwise, omit the prompt from args entirely — the CLI
            # reads from stdin by default.
        else:
            full_prompt = self._build_prompt(prompt)
            if cmd.prompt_flag:
                args.append(cmd.prompt_flag)
                args.append(full_prompt)
            else:
                args.append(full_prompt)

        # MCP configuration: connect through a provided config or disable.
        # Direct config is either a command flag or a workspace-discovered file.
        # Otherwise fall back to disabling MCP (F-271).
        if self._mcp_config_path:
            if cmd.mcp_config_flag:
                args.extend(cmd.mcp_config_prefix_args)
                args.append(cmd.mcp_config_flag)
                args.append(str(self._mcp_config_path))
            elif cmd.mcp_config_workspace_path:
                # ``execute()`` materializes the config into the workspace.
                pass
            elif cmd.mcp_disable_args:
                args.extend(cmd.mcp_disable_args)
        elif cmd.mcp_disable_args:
            args.extend(cmd.mcp_disable_args)

        # Extra flags (always last)
        args.extend(cmd.extra_flags)

        return args

    def _build_env(self) -> dict[str, str] | None:
        """Build subprocess environment from profile.

        When ``required_env`` is set on the CLI command, only the declared
        env vars (plus system essentials like PATH, HOME) are passed to
        the subprocess.  This prevents credentials for other services
        (ANTHROPIC_API_KEY, OPENAI_API_KEY, AWS_SECRET_ACCESS_KEY, etc.)
        from leaking to instrument subprocesses that don't need them.

        When ``required_env`` is None (the default), the full parent
        environment is inherited — backward compatible with existing
        behavior.

        Profile-declared env vars (``cli.command.env``) are always merged
        in, with ``${VAR}`` expansion from ``os.environ``.

        Returns:
            Environment dict, or None to inherit parent environment
            (only when no filtering and no profile env vars).
        """
        profile_env = self._cli.command.env
        required_env = self._cli.command.required_env

        # No filtering requested and no profile env vars → inherit parent
        if required_env is None and not profile_env:
            return None

        if required_env is not None:
            # Filtered mode: start with system essentials only
            env: dict[str, str] = {}
            for key in SYSTEM_ENV_VARS:
                if key in os.environ:
                    env[key] = os.environ[key]

            # Add declared required vars from parent environment
            for key in required_env:
                if key in os.environ:
                    env[key] = os.environ[key]

            _logger.debug(
                "plugin_cli_env_filtered",
                instrument=self._profile.name,
                required_vars=len(required_env),
                system_vars=len([k for k in SYSTEM_ENV_VARS if k in os.environ]),
                total_env_vars=len(env),
                filtered_out=len(os.environ) - len(env),
            )
        else:
            # Unfiltered mode: full parent environment (backward compatible)
            env = dict(os.environ)

        # Merge profile-declared env vars (always applied)
        for key, value in profile_env.items():
            # Expand ${VAR} references from os.environ (not the filtered env)
            expanded = os.path.expandvars(value)
            env[key] = expanded

        return env

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        *,
        exit_code: int | None,
    ) -> ExecutionResult:
        """Parse CLI output into an ExecutionResult.

        Three modes based on output config format:
        - text: stdout is the result
        - json: parse JSON, extract via dot-paths
        - jsonl: find completion event, extract from it

        Args:
            stdout: Standard output from the process.
            stderr: Standard error from the process.
            exit_code: Process exit code (None if killed by signal).

        Returns:
            ExecutionResult with parsed fields.
        """
        errors = self._cli.errors
        output = self._cli.output

        # Determine success from exit code
        is_success = exit_code in errors.success_exit_codes

        # Check rate limiting and error classification
        rate_limited = self._check_rate_limit(stdout, stderr, is_success=is_success)
        error_type = self._classify_output_errors(
            stdout, stderr, is_success=is_success,
        )

        # Default result
        result_text = stdout
        error_message: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None

        if output.format == "json" and stdout.strip():
            try:
                data = json.loads(stdout)

                # Extract result
                if output.result_path and is_success:
                    extracted = extract_json_path(data, output.result_path)
                    if extracted is not None:
                        result_text = str(extracted)

                # Extract error
                if output.error_path and not is_success:
                    extracted_err = extract_json_path(data, output.error_path)
                    if extracted_err is not None:
                        error_message = str(extracted_err)

                # Extract tokens
                if output.input_tokens_path:
                    if output.aggregate_tokens:
                        from marianne.utils.json_path import extract_json_path_all
                        toks = extract_json_path_all(data, output.input_tokens_path)
                        total = sum(int(t) for t in toks if isinstance(t, (int, float)))
                        if total > 0:
                            input_tokens = total
                    else:
                        tok = extract_json_path(data, output.input_tokens_path)
                        if isinstance(tok, (int, float)):
                            input_tokens = int(tok)
                if output.output_tokens_path:
                    if output.aggregate_tokens:
                        from marianne.utils.json_path import extract_json_path_all
                        toks = extract_json_path_all(data, output.output_tokens_path)
                        total = sum(int(t) for t in toks if isinstance(t, (int, float)))
                        if total > 0:
                            output_tokens = total
                    else:
                        tok = extract_json_path(data, output.output_tokens_path)
                        if isinstance(tok, (int, float)):
                            output_tokens = int(tok)

            except json.JSONDecodeError:
                _logger.warning(
                    "plugin_cli_json_parse_failed",
                    instrument=self._profile.name,
                    stdout_head=stdout[:200],
                )

        elif output.format == "jsonl" and stdout.strip():
            result_text, error_message, input_tokens, output_tokens = (
                self._parse_jsonl(stdout, is_success)
            )

        # ExecutionResult invariant: success=True requires exit_code 0 or None.
        # When the instrument's success_exit_codes includes non-zero codes,
        # normalize to 0 for the result (the actual code is logged above).
        normalized_exit_code = 0 if is_success and exit_code != 0 else exit_code

        # Signal-killed process: Unix returncode is -signal_number. Surface
        # the signal for diagnostics — SheetState.exit_signal, the sqlite
        # column, and the learning error hooks consume this field, and the
        # legacy backend was its only producer until the #347 strip.
        exit_signal: int | None = None
        exit_reason: ExitReason = "completed"
        if exit_code is not None and exit_code < 0:
            from marianne.core.errors.signals import get_signal_name

            exit_signal = -exit_code
            exit_reason = "killed"
            is_success = False
            if error_message is None:
                error_message = (
                    f"Process killed by {get_signal_name(exit_signal)}"
                )

        return ExecutionResult(
            success=is_success,
            stdout=result_text,
            stderr=stderr,
            duration_seconds=0.0,  # Caller sets actual duration
            exit_code=normalized_exit_code,
            exit_signal=exit_signal,
            exit_reason=exit_reason,
            rate_limited=rate_limited,
            error_type=error_type,
            error_message=error_message,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _parse_jsonl(
        self,
        stdout: str,
        is_success: bool,
    ) -> tuple[str, str | None, int | None, int | None]:
        """Parse JSONL output, finding the completion event.

        Args:
            stdout: JSONL output (one JSON object per line).
            is_success: Whether the execution was successful.

        Returns:
            Tuple of (result_text, error_message, input_tokens, output_tokens).
        """
        output = self._cli.output
        result_text = stdout
        error_message: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None

        events: list[dict[str, Any]] = []
        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        # Find completion event
        if output.completion_event_type:
            for event in events:
                event_type = event.get("type")
                if event_type != output.completion_event_type:
                    continue

                # Check additional filter if configured
                if output.completion_event_filter:
                    if not all(
                        event.get(k) == v
                        for k, v in output.completion_event_filter.items()
                    ):
                        continue

                # Found the completion event
                if output.result_path and is_success:
                    extracted = extract_json_path(event, output.result_path)
                    if extracted is not None:
                        result_text = str(extracted)
                break

        # Extract tokens from any event that has them
        for event in events:
            if output.input_tokens_path and input_tokens is None:
                tok = extract_json_path(event, output.input_tokens_path)
                if isinstance(tok, (int, float)):
                    input_tokens = int(tok)
            if output.output_tokens_path and output_tokens is None:
                tok = extract_json_path(event, output.output_tokens_path)
                if isinstance(tok, (int, float)):
                    output_tokens = int(tok)

        return result_text, error_message, input_tokens, output_tokens

    def _check_rate_limit(
        self, stdout: str, stderr: str, *, is_success: bool = False,
    ) -> bool:
        """Check for rate limiting using profile patterns.

        GH#189: ALWAYS scan only stderr for rate limit patterns.
        Rate limit errors from CLI instruments appear on stderr or in
        structured error responses, not in the agent's work output on
        stdout. Previously, failed executions scanned stdout too, causing
        false positives when agents wrote about rate limiting (e.g.,
        building rate-limit infrastructure, documenting API behavior).
        False rate limit classification triggers the instrument fallback
        cascade, which combined with GH#188 causes job failure.

        Args:
            stdout: Standard output text (NOT scanned — contains agent work).
            stderr: Standard error text (scanned for rate limit patterns).
            is_success: Whether the execution exited with a success code.

        Returns:
            True if rate limiting was detected.
        """
        # B1 fix: if the instrument succeeded, it handled the rate limit
        # internally (e.g., gemini-cli retries 429s and logs them to stderr).
        # Marianne should not override a successful result with rate_limited.
        if is_success:
            return False

        text = stderr
        for pattern in self._cli.errors.rate_limit_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Log what matched so we can diagnose false positives
                _logger.debug(
                    "plugin_cli_rate_limit_match",
                    instrument=self._profile.name,
                    pattern=pattern,
                    matched_text=match.group(),
                    match_start=match.start(),
                    context=text[max(0, match.start() - 80):match.end() + 80],
                    stdout_head=stdout[:500] if stdout else "",
                    stderr_head=stderr[:500] if stderr else "",
                )
                return True
        return False

    def _classify_output_errors(
        self,
        stdout: str,
        stderr: str,
        *,
        is_success: bool,
    ) -> str | None:
        """Classify error type using profile-defined patterns.

        Scans combined output for instrument-specific error patterns.
        Only runs on failure — successful executions return None.

        Pattern priority (first match wins):
        1. auth_error_patterns → "auth"
        2. crash_patterns → "crash"
        3. stale_patterns → "stale"
        4. timeout_patterns → "timeout"
        5. capacity_patterns → "capacity"

        Auth and crash are checked first because they are non-retriable
        and should not be masked by retriable patterns.

        Args:
            stdout: Standard output text.
            stderr: Standard error text.
            is_success: Whether the execution succeeded.

        Returns:
            Error type string, or None if no patterns match or execution succeeded.
        """
        if is_success:
            return None

        combined = f"{stdout}\n{stderr}"
        errors = self._cli.errors

        # Priority order: non-retriable first, then retriable
        pattern_groups: list[tuple[list[str], str]] = [
            (errors.auth_error_patterns, "auth"),
            (errors.crash_patterns, "crash"),
            (errors.stale_patterns, "stale"),
            (errors.timeout_patterns, "timeout"),
            (errors.capacity_patterns, "capacity"),
        ]

        for patterns, error_type in pattern_groups:
            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    return error_type

        return None

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Execute a prompt through the CLI instrument.

        Builds the command from profile config, runs it as a subprocess,
        and parses the output according to the profile's output config.

        Args:
            prompt: The prompt to execute.
            timeout_seconds: Per-execution timeout override.

        Returns:
            ExecutionResult with parsed output and metadata.
        """
        effective_timeout = timeout_seconds or self._profile.default_timeout_seconds

        # GH#188: Force stdin delivery when the assembled prompt is large.
        # CLI tools crash when receiving 100KB+ prompts as positional
        # arguments. Marianne prompts routinely exceed this with
        # cadenza/prelude injection. 32KB is a conservative threshold.
        full_prompt_for_size = self._build_prompt(prompt)
        use_stdin = self._cli.command.prompt_via_stdin
        prompt_bytes = len(full_prompt_for_size.encode("utf-8"))
        if not use_stdin and prompt_bytes > 32_768:
            can_force_stdin = self._cli.command.stdin_sentinel is not None
            if can_force_stdin:
                _logger.info(
                    "plugin_cli_stdin_forced",
                    instrument=self._profile.name,
                    prompt_bytes=prompt_bytes,
                    reason="prompt exceeds 32KB safe limit for CLI arguments",
                )
                use_stdin = True
            else:
                _logger.warning(
                    "plugin_cli_stdin_not_supported_for_large_prompt",
                    instrument=self._profile.name,
                    prompt_bytes=prompt_bytes,
                    reason=(
                        "profile has no stdin sentinel; keeping prompt in "
                        "argv to avoid launching the CLI with no prompt"
                    ),
                )

        cmd = self._build_command(
            prompt, timeout_seconds=effective_timeout,
            force_stdin=use_stdin and not self._cli.command.prompt_via_stdin,
        )
        env = self._build_env()

        _logger.info(
            "plugin_cli_execute_start",
            instrument=self._profile.name,
            executable=cmd[0],
            prompt_length=len(prompt),
            timeout=effective_timeout,
            stdin_mode=use_stdin,
        )

        start_time = time.monotonic()
        stdout_data = ""
        stderr_data = ""
        exit_code: int | None = None
        exit_reason = "completed"
        proc: asyncio.subprocess.Process | None = None
        pgid: int | None = None
        workspace_mcp_target = self._workspace_mcp_config_target()
        workspace_mcp_lock: asyncio.Lock | None = None
        workspace_mcp_lock_acquired = False
        workspace_mcp_restore: tuple[Path, bytes | None, bool] | None = None

        # Process Lifecycle Phase 1: presence of the group-aware callback is
        # our signal that we are on the baton path. When set, force
        # start_new_session=True regardless of profile so the whole
        # subprocess tree (including pytest workers) is in a killable group.
        _baton_path = self._on_process_group_spawned is not None
        _force_new_session = (
            True if _baton_path else self._cli.command.start_new_session
        )

        try:
            if workspace_mcp_target is not None:
                lock_key = workspace_mcp_target.resolve()
                workspace_mcp_lock = _MCP_WORKSPACE_CONFIG_LOCKS.setdefault(
                    lock_key,
                    asyncio.Lock(),
                )
                await workspace_mcp_lock.acquire()
                workspace_mcp_lock_acquired = True

            # Resolve executable to full path to avoid FileNotFoundError
            # in daemon contexts where asyncio's posix_spawn may fail to
            # resolve executables even though they exist on PATH.
            import shutil as _shutil
            _resolved = _shutil.which(cmd[0])
            if _resolved:
                cmd = [_resolved] + cmd[1:]

            # Ensure working directory exists before spawning —
            # asyncio.create_subprocess_exec raises FileNotFoundError
            # for both missing executables AND missing cwd, so we
            # must ensure cwd exists to disambiguate errors.
            if self._working_directory and not self._working_directory.exists():
                self._working_directory.mkdir(parents=True, exist_ok=True)

            if workspace_mcp_target is not None:
                workspace_mcp_restore = self._materialize_workspace_mcp_config(
                    workspace_mcp_target,
                )

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if use_stdin else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._working_directory) if self._working_directory else None,
                env=env,
                start_new_session=_force_new_session,
            )

            # Capture pgid at spawn — never re-derive from proc.pid later.
            # See docs/specs/2026-04-16-process-lifecycle-design.md (Change 1).
            # Only when the child got its OWN group: without
            # start_new_session, getpgid returns OUR group, and kill-on-exit
            # would be guard-refused and hang forever awaiting a process
            # nobody killed (#376). pgid=None routes to proc.kill() instead.
            if _force_new_session and proc.pid is not None:
                try:
                    pgid = os.getpgid(proc.pid)
                except ProcessLookupError:
                    # Process already died; nothing to kill later.
                    pgid = None

            # Daemon-own-group safety: never kill the daemon's own group.
            # If start_new_session failed or the kernel ignored it, abort
            # the spawn rather than risk killing ourselves in the finally.
            if _baton_path and pgid is not None:
                try:
                    daemon_pgid = os.getpgid(0)
                except ProcessLookupError:
                    daemon_pgid = None
                if daemon_pgid is not None and pgid == daemon_pgid:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await proc.wait()
                    except ProcessLookupError:
                        pass
                    raise RuntimeError(
                        f"Spawned process shares daemon pgid ({pgid}); "
                        "refusing to continue"
                    )

            # Track PID for orphan detection by the daemon's pgroup manager
            if self._on_process_spawned and proc.pid is not None:
                self._on_process_spawned(proc.pid)

            # Fire the group-aware callback (baton adapter records pid+pgid
            # in _active_pids so deregister/cancel can killpg the tree).
            if (
                self._on_process_group_spawned is not None
                and proc.pid is not None
                and pgid is not None
            ):
                self._on_process_group_spawned(proc.pid, pgid)

            # When using stdin mode, write the assembled prompt to the
            # subprocess stdin and close it to signal EOF. This must
            # happen before draining the output pipes — if the subprocess
            # waits for stdin EOF before producing output, reading stdout
            # first would deadlock.
            if use_stdin and proc.stdin is not None:
                full_prompt = self._build_prompt(prompt)
                proc.stdin.write(full_prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()

            # #352 inc-2: both pipes drained CONCURRENTLY (the deadlock
            # avoidance communicate() provided), with wait() gathered so
            # buffered output after exit is still read to EOF. Decode
            # happens after capture so a chunk boundary can never split
            # a multibyte sequence.
            stdout_chunks: list[bytes] = []
            stderr_chunks: list[bytes] = []

            def _safe_emit(stream_name: str) -> Callable[[bytes], None] | None:
                callback = self._on_output
                if callback is None:
                    return None

                def emit(chunk: bytes) -> None:
                    try:
                        callback(stream_name, chunk)
                    except Exception:
                        _logger.debug(
                            "plugin_cli_output_callback_failed",
                            instrument=self._profile.name,
                            stream=stream_name,
                        )

                return emit

            try:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            _drain_stream(
                                proc.stdout, stdout_chunks,
                                on_chunk=_safe_emit("stdout"),
                            ),
                            _drain_stream(
                                proc.stderr, stderr_chunks,
                                on_chunk=_safe_emit("stderr"),
                            ),
                            proc.wait(),
                        ),
                        timeout=effective_timeout,
                    )
                    exit_code = proc.returncode
                except TimeoutError:
                    _logger.warning(
                        "plugin_cli_timeout",
                        instrument=self._profile.name,
                        timeout=effective_timeout,
                    )
                    exit_reason = "timeout"
            finally:
                # Kill-on-exit: runs on clean completion (idempotent — noop
                # when returncode is already set), TimeoutError, CancelledError,
                # and arbitrary exceptions. SIGTERM -> 2s grace -> SIGKILL of
                # the process group. Closes RC-2 from the spec.
                await _kill_process_group_if_alive(proc, pgid)
                # Decode AFTER the try so partial output survives timeout
                # and cancellation — the drain appends chunks in place
                # precisely so the cancelled gather can't discard them,
                # and a timing-out sheet's last words are the diagnostic
                # payload. exit_code stays clean-completion-only: after
                # the kill above, proc.returncode is OUR signal, not the
                # agent's exit.
                stdout_data = b"".join(stdout_chunks).decode(
                    "utf-8", errors="replace"
                )
                stderr_data = b"".join(stderr_chunks).decode(
                    "utf-8", errors="replace"
                )

        except FileNotFoundError:
            # After ensuring cwd exists above, this is genuinely about
            # a missing executable, not a missing working directory.
            stderr_data = f"Executable not found: {cmd[0]}"
            exit_reason = "error"
            _logger.error(
                "plugin_cli_executable_not_found",
                instrument=self._profile.name,
                executable=cmd[0],
            )
        except OSError as e:
            stderr_data = f"Failed to start process: {e}"
            exit_reason = "error"
            _logger.error(
                "plugin_cli_execution_error",
                instrument=self._profile.name,
                error=str(e),
            )
        finally:
            if workspace_mcp_restore is not None:
                self._restore_workspace_mcp_config(workspace_mcp_restore)
            if workspace_mcp_lock is not None and workspace_mcp_lock_acquired:
                workspace_mcp_lock.release()

        # Untrack PID — process and children are cleaned up
        if self._on_process_exited and proc is not None and proc.pid is not None:
            self._on_process_exited(proc.pid)

        duration = time.monotonic() - start_time

        # Parse the output
        result = self._parse_output(
            stdout_data, stderr_data, exit_code=exit_code,
        )

        # Override fields that _parse_output doesn't set
        result.duration_seconds = duration
        if exit_reason == "timeout":
            result.success = False
            result.exit_reason = "timeout"
        elif exit_reason == "error":
            result.success = False
            result.exit_reason = "error"

        _logger.info(
            "plugin_cli_execute_complete",
            instrument=self._profile.name,
            success=result.success,
            duration=f"{duration:.2f}s",
            exit_code=exit_code,
            rate_limited=result.rate_limited,
        )

        if result.rate_limited:
            _logger.info(
                "plugin_cli_rate_limit_detected",
                instrument=self._profile.name,
                exit_code=exit_code,
                stdout_head=stdout_data[:1000] if stdout_data else "",
                stderr_head=stderr_data[:1000] if stderr_data else "",
                error_type=result.error_type,
                error_message=result.error_message,
            )

        return result

    async def health_check(self) -> bool:
        """Check if the CLI instrument is available.

        Verifies the executable exists on PATH. Does not run a test
        prompt — that would consume API quota.

        Returns:
            True if the executable is found on PATH.
        """
        executable = self._cli.command.executable
        found = shutil.which(executable) is not None

        if not found:
            _logger.warning(
                "plugin_cli_health_check_failed",
                instrument=self._profile.name,
                executable=executable,
                reason="not_on_path",
            )

        return found
