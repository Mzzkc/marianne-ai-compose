"""InteractiveCliBackend — Backend ABC implementation over tmux sessions.

Executes a sheet by launching the instrument's CLI as a live interactive TUI
in an isolated tmux session and driving it to completion with
:class:`InteractiveSessionDriver`. The musician calls ``execute()`` exactly
like any other backend — interactive mode is invisible above the Backend ABC.

Completion marker = "the agent claims done", mapped to ``success=True``;
validations remain the authoritative arbiter of sheet success (they run on
the workspace after every successful execution, unchanged).

Error classification honours the GH#189 lesson with extra force: the
interactive transcript is agent-work-contaminated and there is NO stderr
channel. Only the final rendered screen is scanned, only on non-completed
outcomes, only with the profile's own vendor patterns — a missed rate limit
retries normally; a false positive would burn the instrument fallback chain.

See docs/specs/2026-06-10-interactive-mode-design.md.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from marianne.core.config.instruments import InstrumentProfile, InteractiveCliConfig
from marianne.core.config.workspace import (
    MAILBOX_ENV_VAR,
    SCRATCH_ENV_VAR,
    mailbox_root,
    scratch_root,
)
from marianne.core.errors import ErrorClassifier
from marianne.core.logging import get_logger
from marianne.core.mailbox import SHEET_ENV_VAR
from marianne.execution.base import Backend, ExecutionResult, SheetRequestState
from marianne.execution.instruments.cli_backend import build_instrument_env
from marianne.execution.instruments.interactive.driver import (
    DEFAULT_NUDGE_MESSAGE,
    ContinuationPolicy,
    DriverResult,
    InteractiveSessionDriver,
    StaticNudgePolicy,
)
from marianne.execution.instruments.interactive.model_nudge import BackendFactory, ModelNudgePolicy
from marianne.execution.instruments.interactive.tmux import (
    MIN_TMUX_VERSION,
    SESSION_PREFIX,
    TmuxControl,
    TmuxError,
    sanitize_session_name,
)
from marianne.execution.instruments.interactive.usage import apply_usage, read_session_usage
from marianne.execution.instruments.workspace_mcp_config import (
    MCP_WORKSPACE_CONFIG_LOCKS,
    WorkspaceMcpConfigMixin,
)
from marianne.utils.process import supervised_argv

_logger = get_logger("execution.interactive.backend")
_ERROR_CLASSIFIER = ErrorClassifier()

# Default consecutive-idle nudge budget (overridable per sheet via
# instrument_config.interactive_max_nudges).
DEFAULT_MAX_NUDGES = 5

# Where raw pipe-pane transcripts go. Deliberately OUTSIDE any workspace:
# constant TUI redraw output must not keep workspace mtime artificially
# fresh, and the raw stream is a debug artifact, never machine-parsed.
_TRANSCRIPT_DIR = Path.home() / ".marianne" / "interactive-logs"



def completion_marker_path(
    workspace: Path, job_id: str, sheet_num: int, attempt: int,
) -> Path:
    """Per-attempt completion marker path (lab P0: never workspace-global).

    Scoped by job/sheet/attempt so concurrent sheets sharing a workspace can
    never satisfy each other's done condition, and a stale marker from a
    prior attempt never instantly completes a new one.
    """
    job_part = sanitize_session_name(job_id)
    return (
        workspace / ".marianne" / "interactive" / job_part
        / f"s{sheet_num}-a{attempt}.complete"
    )


def session_name(job_id: str, sheet_num: int, attempt: int) -> str:
    """Deterministic tmux session name for one sheet attempt.

    Determinism is load-bearing: pre-launch same-name cleanup, per-job
    teardown (``mzt-{job}-*``), and the daemon-startup orphan sweep all rely
    on reconstructing these names.
    """
    return sanitize_session_name(
        f"{SESSION_PREFIX}-{job_id}-s{sheet_num}-a{attempt}",
    )


def _completion_suffix(marker: Path) -> str:
    """The completion-protocol instructions appended to every prompt."""
    return (
        "---\n"
        "## Completion Protocol (Interactive Session)\n\n"
        "You are running inside a supervised interactive session. When — and "
        "only when — the task above is FULLY complete (all success "
        "requirements satisfied), signal completion by creating this exact "
        "file:\n\n"
        f"    {marker}\n\n"
        f"For example: mkdir -p {marker.parent} && touch {marker}\n\n"
        "Do NOT create it before everything is done. If you still have work "
        "remaining, keep working instead of stopping."
    )


class InteractiveCliBackend(WorkspaceMcpConfigMixin, Backend):
    """Backend that drives a CLI instrument through a live tmux session.

    One instance per concurrent interactive sheet (pooled in a free-list
    separate from headless instances). All per-attempt state is local to
    ``execute()``; the attempt identity set by the adapter is consumed and
    cleared each execution, so free-list reuse is inherently clean.
    """

    def __init__(
        self,
        profile: InstrumentProfile,
        working_directory: Path | None = None,
        tmux: TmuxControl | None = None,
    ) -> None:
        """Initialize from an InstrumentProfile with interactive config.

        Args:
            profile: Instrument profile; must be kind=cli with a
                ``cli.interactive`` block (verified instruments only).
            working_directory: Working directory (the sheet workspace).
            tmux: TmuxControl override for tests; defaults to the shared
                ``marianne`` socket.

        Raises:
            ValueError: If the profile cannot run interactively.
        """
        if profile.kind != "cli" or profile.cli is None:
            raise ValueError(
                f"InteractiveCliBackend requires kind=cli, got "
                f"kind={profile.kind} for instrument '{profile.name}'"
            )
        if profile.cli.interactive is None:
            raise ValueError(
                f"Instrument '{profile.name}' has no cli.interactive config — "
                f"interactive mode is only available for instruments whose "
                f"interactive behavior has been verified. Add a "
                f"cli.interactive block to the profile or remove "
                f"'interactive: true' from the score."
            )

        self._profile = profile
        self._cli = profile.cli
        self._interactive: InteractiveCliConfig = profile.cli.interactive
        self._working_directory: Path | None = working_directory
        self._tmux = tmux or TmuxControl()
        self._driver = InteractiveSessionDriver(self._tmux, self._interactive)

        self._mcp_config_path: Path | None = None
        self._model: str | None = profile.default_model
        self._preamble: str | None = None
        self._prompt_extensions: list[str] = []

        # Per-sheet driver knobs — set by the adapter after acquire,
        # re-set on every dispatch (and reset by clear_overrides).
        self._max_nudges: int = DEFAULT_MAX_NUDGES
        self._nudge_message: str | None = None
        self._nudge_policy = "static"
        self._nudge_instrument: str | None = None
        self._nudge_timeout_seconds = 30.0
        self._nudge_backend_factory: BackendFactory | None = None

        # Attempt identity — set by the adapter after acquire; consumed
        # (and cleared) by the next execute(). Without it, execute()
        # falls back to a unique token (standalone use).
        self._attempt_identity: tuple[str, int, int] | None = None

        # PID callbacks — same contract as PluginCliBackend; wired by the
        # pool/adapter. The pane process is its own pgid (spike-verified,
        # integration-pinned), so the group callback gets (pid, pid).
        self._on_process_spawned: Callable[[int], None] | None = None
        self._on_process_exited: Callable[[int], None] | None = None
        self._on_process_group_spawned: Callable[[int, int], None] | None = None
        # Live output sink — same contract as PluginCliBackend (#352 inc-3);
        # the adapter wires the OutputStreamHub writer per dispatch (GH #381).
        self._on_output: Callable[[str, bytes], None] | None = None

        # Override tracking — mirrors PluginCliBackend.
        self._saved_model: str | None = None
        self._has_overrides: bool = False

        _logger.debug(
            "interactive_backend_initialized",
            instrument=profile.name,
            executable=self._cli.command.executable,
            model=self._model,
        )

    @property
    def name(self) -> str:
        """Human-readable backend name."""
        return f"{self._profile.display_name} (interactive)"

    # ── Per-sheet configuration surface ────────────────────────────────

    def set_output_callback(
        self, callback: Callable[[str, bytes], None] | None
    ) -> None:
        """Set (or clear with None) the live output sink (GH #381).

        Receives ``("stdout", chunk)`` for each new slice of the pipe-pane
        transcript, once per driver poll. Runs on the event loop; keep it
        cheap. Without a transcript (capture failed) nothing is delivered.
        """
        self._on_output = callback

    def set_mcp_config(self, config_path: Path | None) -> None:
        """Connect this attempt to the pool via a flag or workspace config file."""
        self._mcp_config_path = config_path

    def set_attempt_identity(
        self, job_id: str, sheet_num: int, attempt: int,
    ) -> None:
        """Record the attempt identity for session/marker naming.

        Called by the baton adapter after acquire, before execute().
        Consumed by the next execute() and cleared — never carries across
        free-list reuse.
        """
        self._attempt_identity = (job_id, sheet_num, attempt)

    def configure_interactive(
        self,
        *,
        max_nudges: int | None = None,
        nudge_message: str | None = None,
        nudge_policy: str | None = None,
        nudge_instrument: str | None = None,
        nudge_timeout_seconds: float | None = None,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        """Apply per-sheet interactive knobs from instrument_config."""
        if nudge_policy is not None and nudge_policy not in {"static", "model"}:
            raise ValueError("nudge_policy must be static or model")
        if nudge_timeout_seconds is not None and (
            not math.isfinite(nudge_timeout_seconds) or nudge_timeout_seconds <= 0
        ):
            raise ValueError("nudge timeout must be finite and > 0")
        if nudge_instrument is not None and not nudge_instrument.strip():
            raise ValueError("nudge instrument must be non-empty")
        if nudge_policy is not None:
            self._nudge_policy = nudge_policy
        if nudge_instrument is not None:
            self._nudge_instrument = nudge_instrument
        if nudge_timeout_seconds is not None:
            self._nudge_timeout_seconds = nudge_timeout_seconds
        if backend_factory is not None:
            self._nudge_backend_factory = backend_factory
        if max_nudges is not None:
            if max_nudges < 0:
                raise ValueError(f"max_nudges must be >= 0, got {max_nudges}")
            self._max_nudges = max_nudges
        if nudge_message is not None:
            self._nudge_message = nudge_message

    def _build_nudge_policy(self) -> ContinuationPolicy:
        message = self._nudge_message or DEFAULT_NUDGE_MESSAGE
        if self._nudge_policy == "model":
            if self._nudge_backend_factory is not None and self._nudge_instrument:
                return ModelNudgePolicy(
                    backend_factory=self._nudge_backend_factory,
                    instrument_name=self._nudge_instrument,
                    timeout_seconds=self._nudge_timeout_seconds,
                    fallback_message=message,
                )
            _logger.warning("interactive_model_nudge_unconfigured", instrument=self._profile.name)
        return StaticNudgePolicy(message)

    def apply_overrides(self, overrides: dict[str, object]) -> None:
        """Apply per-sheet parameter overrides (model)."""
        if not overrides:
            return
        self._saved_model = self._model
        self._has_overrides = True
        if "model" in overrides:
            self._model = str(overrides["model"])

    def clear_overrides(self) -> None:
        """Restore defaults after execution (called on pool release)."""
        if self._has_overrides:
            self._model = self._saved_model
            self._saved_model = None
            self._has_overrides = False
        # Per-sheet knobs and identity never survive into the free list.
        self._max_nudges = DEFAULT_MAX_NUDGES
        self._nudge_message = None
        self._nudge_policy = "static"
        self._nudge_instrument = None
        self._nudge_timeout_seconds = 30.0
        self._nudge_backend_factory = None
        self._attempt_identity = None
        self._mcp_config_path = None
        # W-F3: per-sheet prompt bytes neither — see Backend.set_preamble.
        self._preamble = None
        self._prompt_extensions = []

    def set_preamble(self, preamble: str | None) -> None:
        """Set preamble to prepend to the next prompt.

        Direct-use contract: the dispatch path carries the per-attempt
        preamble request-locally (``SheetRequestState``) — see
        ``Backend.set_preamble``.
        """
        self._preamble = preamble

    def set_prompt_extensions(self, extensions: list[str]) -> None:
        """Set prompt extensions to append to the next prompt.

        Direct-use contract — see ``Backend.set_prompt_extensions``.
        """
        self._prompt_extensions = list(extensions)

    # ── Execution ──────────────────────────────────────────────────────

    def effort_refusal(self, level: str, *, schema_mode: bool) -> str | None:
        """Why this instrument cannot run a sheet at ``level`` (None = it can).

        Judged as an interactive launch: a mapping whose ``interactive`` is
        false refuses every level here.
        """
        return self._profile.effort_refusal(
            level, model=self._model, schema_mode=schema_mode, interactive=True,
        )

    def _build_command(self, *, effort: str | None = None) -> list[str]:
        """Build the interactive launch argv.

        Interactive launch deliberately omits the headless machinery
        (prompt flag, output-format, timeout flag, the HEADLESS subcommand).
        The interactive config controls what carries over: its own
        ``subcommand`` (e.g. goose's ``session``), whether the
        auto-approve flag applies interactively, and whether mcp_disable_args
        carry over (F-271 — verified for claude-code).

        The headless ``extra_flags`` never carry over, so an interactive
        launch has no effort flag unless ``extra_args`` holds one. A sheet's
        ``effort`` (``instrument_config.effort``, GH #535) is mapped through
        the profile's ``effort:`` block and replaces that value, or is
        appended; a level the profile does not map, or a mapping marked
        ``interactive: false``, raises before the session is created.
        """
        cmd = self._cli.command
        interactive = self._interactive
        args: list[str] = [cmd.executable]
        if interactive.subcommand:
            args.append(interactive.subcommand)
        if cmd.auto_approve_flag and interactive.inherit_auto_approve:
            args.append(cmd.auto_approve_flag)
        if cmd.model_flag and self._model:
            args.append(cmd.model_flag)
            args.append(self._model)
        if self._mcp_config_path is not None and cmd.mcp_config_flag:
            args.extend(cmd.mcp_config_prefix_args)
            args.extend([cmd.mcp_config_flag, str(self._mcp_config_path)])
        elif self._mcp_config_path is not None and cmd.mcp_config_workspace_path:
            pass  # execute() temporarily materializes the workspace file.
        elif cmd.mcp_disable_args and interactive.inherit_mcp_disable_args:
            args.extend(cmd.mcp_disable_args)
        args.extend(cmd.disable_hooks_args)  # GH #536: no operator hooks in a musician
        if effort is None:
            args.extend(interactive.extra_args)
            return args
        refusal = self.effort_refusal(effort, schema_mode=False)
        mapping = self._profile.effort
        if refusal is not None or mapping is None:
            raise ValueError(
                f"instrument '{self._profile.name}' {refusal}. Refusing to "
                "run the sheet at an effort it did not ask for"
            )
        native = self._profile.native_effort(effort, model=self._model)
        args.extend(mapping.apply(interactive.extra_args, native))
        return args

    def _build_prompt(
        self,
        prompt: str,
        marker: Path,
        preamble: str | None = None,
        prompt_extensions: tuple[str, ...] | None = None,
    ) -> str:
        """Assemble preamble + prompt + extensions + completion protocol.

        ``preamble``/``prompt_extensions`` are the request-local values
        (W-F3) threaded from ``execute``; when both are None (direct/test
        callers), the mutable per-sheet slots are used.
        """
        if preamble is None and prompt_extensions is None:
            preamble = self._preamble
            prompt_extensions = tuple(self._prompt_extensions)
        parts: list[str] = []
        if preamble:
            parts.append(preamble)
        parts.append(prompt)
        if prompt_extensions:
            parts.extend(prompt_extensions)
        parts.append(_completion_suffix(marker))
        return "\n\n".join(parts)

    def _resolve_identity(self) -> tuple[str, str, Path]:
        """Resolve (session_name, attempt_label, marker_path) for this run.

        Uses the adapter-provided attempt identity when present; otherwise
        a unique token (standalone use). Identity is consumed here — it
        never leaks into a future execution.
        """
        workspace = self._working_directory or Path.cwd()
        identity = self._attempt_identity
        self._attempt_identity = None
        if identity is not None:
            job_id, sheet_num, attempt = identity
            return (
                session_name(job_id, sheet_num, attempt),
                f"{job_id}/s{sheet_num}/a{attempt}",
                completion_marker_path(workspace, job_id, sheet_num, attempt),
            )
        token = uuid.uuid4().hex[:12]
        return (
            sanitize_session_name(f"{SESSION_PREFIX}-{token}"),
            token,
            workspace / ".marianne" / "interactive" / f"{token}.complete",
        )

    async def execute(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        request: SheetRequestState | None = None,
    ) -> ExecutionResult:
        """Execute a prompt by driving a live interactive session.

        Returns an ExecutionResult mapped from the driver outcome; never
        raises for session-level failures (the musician's exception path is
        reserved for genuine bugs).
        """
        effective_timeout = timeout_seconds or self._profile.default_timeout_seconds
        session, attempt_label, marker = self._resolve_identity()
        workspace = self._working_directory or Path.cwd()
        workspace.mkdir(parents=True, exist_ok=True)
        marker.parent.mkdir(parents=True, exist_ok=True)
        transcript = _TRANSCRIPT_DIR / f"{session}.log"

        # Request-local per-sheet prompt state (W-F3) — see the CLI backend.
        if request is not None:
            req_preamble: str | None = request.preamble
            req_extensions: tuple[str, ...] | None = request.prompt_extensions
        else:
            req_preamble = None
            req_extensions = None

        command = self._build_command(
            effort=request.effort if request is not None else None,
        )
        # GH #533: choose the session id so the harness's own usage record
        # can be found after the sheet, when the profile declares one.
        usage_cfg = self._interactive.usage
        usage_session_id: str | None = None
        if usage_cfg is not None:
            usage_session_id = str(uuid.uuid4())
            command = [*command, usage_cfg.session_id_flag, usage_session_id]
        # GH #487: per-attempt supervisor forensic report, next to the raw
        # transcript (same deliberate outside-any-workspace home: a debug
        # artifact whose writes must not freshen workspace mtimes). The
        # driver reads it on session_lost so the detail names the signal the
        # supervisor forwarded and whether it escalated — an absent report
        # is itself evidence (external kill).
        supervisor_report_path = _TRANSCRIPT_DIR / f"{session}.supervisor.json"
        if self._cli.command.supervise:
            # The pane process stays its own group leader (the supervisor is
            # the leader; the agent shares the group), so pid == pgid holds.
            command = supervised_argv(
                command, io_priority=self._cli.command.io_priority,
                max_open_files=self._cli.command.max_open_files,
                report_path=supervisor_report_path,
            )
        full_prompt = self._build_prompt(
            prompt, marker, req_preamble, req_extensions
        )
        policy = self._build_nudge_policy()

        pane_pid: int | None = None

        def _register_pid(pid: int) -> None:
            nonlocal pane_pid
            pane_pid = pid
            if self._on_process_spawned is not None:
                self._on_process_spawned(pid)
            if self._on_process_group_spawned is not None:
                # pane pid == its own pgid (spike-verified, test-pinned)
                self._on_process_group_spawned(pid, pid)

        _logger.info(
            "interactive_execute_start",
            instrument=self._profile.name,
            session=session,
            attempt=attempt_label,
            prompt_length=len(full_prompt),
            timeout=effective_timeout,
        )

        start = time.monotonic()
        target = self._workspace_mcp_config_target()
        workspace_lock: asyncio.Lock | None = None
        lock_acquired = False
        restore: tuple[Path, bytes | None, bool] | None = None
        try:
            if target is not None:
                workspace_lock = MCP_WORKSPACE_CONFIG_LOCKS.setdefault(
                    target.resolve(), asyncio.Lock(),
                )
                await workspace_lock.acquire()
                lock_acquired = True
                restore = self._materialize_workspace_mcp_config(target)
            driver_result = await self._driver.run(
                session=session,
                command=command,
                cwd=workspace,
                prompt=full_prompt,
                marker_path=marker,
                timeout_seconds=effective_timeout,
                policy=policy,
                max_nudges=self._max_nudges,
                transcript_path=transcript,
                supervisor_report_path=(
                    supervisor_report_path
                    if self._cli.command.supervise else None
                ),
                on_pane_pid=_register_pid,
                env=self._instrument_env(
                    workspace, sheet_num=request.sheet_num if request is not None else None,
                ),
                on_output=self._on_output,
            )
        finally:
            try:
                await self._ensure_session_gone(session)
            finally:
                if restore is not None:
                    self._restore_workspace_mcp_config(restore)
                if workspace_lock is not None and lock_acquired:
                    workspace_lock.release()
            # The pane process should be dead here. This PID is an UNTRACK
            # TOKEN, not a live process reference — the OS may have recycled
            # it. Consumers must only ever remove it from tracking
            # (pgroup.untrack does exactly that); never signal it directly
            # (goal-mode audit, GLM P2-LOW).
            if pane_pid is not None and self._on_process_exited is not None:
                self._on_process_exited(pane_pid)

        duration = time.monotonic() - start
        # The driver may hold a parsed supervisor report (session_lost path);
        # expose it for observability even when the mapped result does not
        # carry it in the error message.
        supervisor_report = driver_result.supervisor_report
        if supervisor_report is not None:
            _logger.info(
                "interactive_supervisor_report",
                instrument=self._profile.name,
                session=session,
                attempt=attempt_label,
                forwarded_signals=supervisor_report.get("forwarded_signals"),
                escalated_to_sigkill=supervisor_report.get("escalated_to_sigkill"),
                escalated_after_seconds=supervisor_report.get(
                    "escalated_after_seconds"
                ),
            )
        result = self._map_result(driver_result, duration)
        # GH #533: on EVERY outcome the attempt cost tokens; never a free zero.
        reading = await asyncio.to_thread(
            read_session_usage, usage_cfg, usage_session_id,
        )
        apply_usage(result, reading)
        _logger.info(
            "interactive_usage_read",
            instrument=self._profile.name,
            session=session,
            outcome=driver_result.outcome,
            measured=reading.measured,
            responses=reading.responses,
            reason=reading.reason,
        )

        _logger.info(
            "interactive_execute_complete",
            instrument=self._profile.name,
            session=session,
            outcome=driver_result.outcome,
            success=result.success,
            nudges=driver_result.nudges_sent,
            duration=f"{duration:.2f}s",
            transcript=str(transcript),
        )
        return result

    def _instrument_env(
        self, workspace: Path, *, sheet_num: int | None = None,
    ) -> dict[str, str]:
        """Snapshot the shared policy, then add the job-owned scratch root.

        Even opt-out profiles pass a complete snapshot to tmux: its server may
        have been started with a different environment from this conductor.
        """
        env = build_instrument_env(self._cli.command, self._profile.name)
        if env is None:
            env = dict(os.environ)
        for key in (SCRATCH_ENV_VAR, MAILBOX_ENV_VAR, SHEET_ENV_VAR):
            env.pop(key, None)
        env.update(self._scratch_env(workspace, sheet_num=sheet_num) or {})
        return env

    @staticmethod
    def _scratch_env(
        workspace: Path, *, sheet_num: int | None = None,
    ) -> dict[str, str] | None:
        """Export scratch and MARIANNE_MAILBOX paths for the pane environment."""
        root = scratch_root(workspace)
        mailbox = mailbox_root(workspace)
        try:
            root.mkdir(parents=True, exist_ok=True)
            mailbox.mkdir(parents=True, exist_ok=True)
            if sheet_num is not None:
                (mailbox / str(sheet_num)).mkdir(exist_ok=True)
        except OSError:
            return None
        env = {SCRATCH_ENV_VAR: str(root), MAILBOX_ENV_VAR: str(mailbox)}
        if sheet_num is not None:
            env[SHEET_ENV_VAR] = str(sheet_num)
        return env

    async def _ensure_session_gone(self, session: str) -> None:
        """Backend-level final tmux teardown guard.

        The driver owns normal cleanup, but live Antigravity quota proof showed
        a rate-limited interactive attempt can return to the baton while the
        tmux session still exists. Do not untrack the pane PID until this
        second guard has checked the actual tmux server.
        """
        for attempt in range(3):
            try:
                if not await self._tmux.has_session(session):
                    return
            except TmuxError as e:
                _logger.warning(
                    "interactive_backend_teardown_check_failed",
                    session=session,
                    step="has_session",
                    error=str(e),
                )
                return

            if attempt == 0:
                _logger.warning(
                    "interactive_backend_session_survived_driver_cleanup",
                    session=session,
                )
            try:
                await self._tmux.kill_session(session)
            except TmuxError as e:
                _logger.warning(
                    "interactive_backend_teardown_check_failed",
                    session=session,
                    step="kill_session",
                    error=str(e),
                )
            if attempt < 2:
                await asyncio.sleep(0.15 * (attempt + 1))

        try:
            alive = await self._tmux.has_session(session)
        except TmuxError:
            alive = False
        if alive:
            _logger.error(
                "interactive_backend_session_survived_final_teardown",
                session=session,
            )

    def _map_result(
        self, driver_result: DriverResult, duration: float,
    ) -> ExecutionResult:
        """Map a DriverResult onto the ExecutionResult contract."""
        screen = driver_result.final_screen
        outcome = driver_result.outcome

        if outcome == "completed":
            # "Agent claims done" — validations remain the arbiter.
            return ExecutionResult(
                success=True,
                stdout=screen,
                stderr="",
                duration_seconds=duration,
                exit_code=None,
                model=self._model,
            )

        rate_limited = self._scan_final_screen(
            screen, self._interactive.rate_limit_screen_patterns,
        )
        rate_limit_wait_seconds = (
            _ERROR_CLASSIFIER.extract_rate_limit_wait(screen)
            if rate_limited else None
        )
        error_type: str | None = None
        if not rate_limited and self._scan_final_screen(
            screen, self._interactive.auth_error_screen_patterns,
        ):
            error_type = "auth"
        elif self._scan_final_screen(
            screen, self._interactive.crash_screen_patterns,
        ):
            error_type = "crash"
        elif self._scan_final_screen(
            screen, self._interactive.capacity_screen_patterns,
        ):
            error_type = "capacity"

        if outcome in ("session_lost", "startup_failed") and error_type is None:
            rate_limited = self._scan_final_screen(
                screen, self._cli.errors.rate_limit_patterns,
            )
            if rate_limited and rate_limit_wait_seconds is None:
                rate_limit_wait_seconds = (
                    _ERROR_CLASSIFIER.extract_rate_limit_wait(screen)
                )
            if not rate_limited and self._scan_final_screen(
                screen, self._cli.errors.auth_error_patterns,
            ):
                error_type = "auth"
            elif outcome == "session_lost":
                error_type = "crash"

        return ExecutionResult(
            success=False,
            stdout=screen,
            stderr="",
            duration_seconds=duration,
            exit_code=None,
            exit_reason="timeout" if outcome == "timeout" else "completed"
            if outcome == "nudges_exhausted" else "error",
            rate_limited=rate_limited,
            rate_limit_wait_seconds=rate_limit_wait_seconds,
            error_type=error_type,
            error_message=driver_result.detail,
            model=self._model,
        )

    @staticmethod
    def _scan_final_screen(screen: str, patterns: list[str]) -> bool:
        """Narrow final-screen-only pattern scan (GH#189 discipline).

        Never applied to the transcript, never on completion — only the
        last rendered screen of a failed attempt, with the profile's own
        vendor patterns.
        """
        if not screen:
            return False
        return any(
            re.search(pattern, screen, re.IGNORECASE) for pattern in patterns
        )

    async def health_check(self) -> bool:
        """tmux present at a supported version AND the instrument on PATH."""
        if shutil.which(self._cli.command.executable) is None:
            _logger.warning(
                "interactive_health_check_failed",
                instrument=self._profile.name,
                reason="executable_not_on_path",
                executable=self._cli.command.executable,
            )
            return False
        if shutil.which("tmux") is None:
            _logger.warning(
                "interactive_health_check_failed",
                instrument=self._profile.name,
                reason="tmux_not_on_path",
            )
            return False
        version = await self._tmux.version()
        if version is None or version < MIN_TMUX_VERSION:
            _logger.warning(
                "interactive_health_check_failed",
                instrument=self._profile.name,
                reason="tmux_version_unsupported",
                found=str(version),
                minimum=".".join(str(v) for v in MIN_TMUX_VERSION),
            )
            return False
        return True
