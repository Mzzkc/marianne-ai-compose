"""InteractiveSessionDriver — the state machine that pushes a TUI agent to done.

::

    LAUNCH → GATES → READY → SUBMIT → DRIVE ⟲ → HARVEST (once) → CLEANUP

The driver owns idle handling for interactive sheets: busy detection from
profile ``busy_patterns`` (primary signal), a work-area change hash that only
resets the quiet clock (never alone means busy), and a nudge budget consulted
through a :class:`ContinuationPolicy`. Completion is signalled by the agent
creating a per-attempt marker file — a protocol signal, not semantic success;
validations remain the authoritative arbiter downstream.

All per-attempt state lives in locals inside :meth:`run` — never on the
instance — so BackendPool free-list reuse of the owning backend is inherently
clean.

See docs/specs/2026-06-10-interactive-mode-design.md.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from marianne.core.config.instruments import InteractiveCliConfig
from marianne.core.logging import get_logger
from marianne.execution.instruments.interactive.tmux import TmuxControl, TmuxError

_logger = get_logger("execution.interactive.driver")

# Hard ceiling on total nudges as a multiple of the consecutive-idle budget.
# The consecutive counter resets when the agent does verified work after a
# nudge; without a total cap, a nudge→trivial-work→idle loop could cycle
# until the sheet timeout. 5x keeps productive sessions alive while bounding
# the pathological loop.
_TOTAL_NUDGE_MULTIPLIER = 5

# Raw pipe-pane log size cap. The log is a debug artifact; a stuck TUI
# redrawing at full speed must not exhaust the disk before the sheet
# timeout fires. Checked each poll; piping stops (session keeps running)
# once exceeded.
_TRANSCRIPT_MAX_BYTES = 50 * 1024 * 1024

# Submit verification. Enter can be LOST when it arrives while the TUI is
# still ingesting a large bracketed paste — observed live with Claude Code:
# the full prompt sat unsubmitted in the input box while the agent never
# received it. So: settle after the paste, send Enter, then verify the
# submission took (busy pattern visible OR the input prompt returned bare)
# and re-send Enter if not. An extra Enter on an empty input box is a
# no-op, so over-sending is safe; under-sending silently loses the sheet.
_PASTE_SETTLE_SECONDS = 1.0
_SUBMIT_VERIFY_SECONDS = 3.0
_SUBMIT_MAX_ENTERS = 3
# A paste that produces no visible screen change was swallowed (modal
# dialog, etc.) — retry the paste this many times total before proceeding.
_PASTE_MAX_ATTEMPTS = 3

DriverOutcome = Literal[
    "completed",        # agent created the completion marker
    "nudges_exhausted", # idle with budget spent; validations arbitrate
    "timeout",          # sheet deadline reached
    "session_lost",     # pane/session died or tmux control failed mid-drive
    "startup_failed",   # never reached the ready prompt
    "provider_error",   # verified provider/account/runtime error screen
]


@dataclass
class ContinuationContext:
    """What a continuation policy may consult when the agent goes idle.

    Deliberately minimal for V1 (the static policy ignores it); extended
    only when a real consumer (model-driven policy) lands.
    """

    nudge_count: int
    """Total nudges sent so far this attempt."""

    elapsed_seconds: float
    """Seconds since the prompt was submitted."""


class ContinuationPolicy(Protocol):
    """Decides what to type when the agent goes idle without finishing.

    V1 ships :class:`StaticNudgePolicy`. V2 will add a model-driven policy
    (a cheap model reads the screen and decides the next input); V3 is the
    spec'd conductor agent. The driver owns the nudge budget — policies
    only produce the message.
    """

    async def next_nudge(self, context: ContinuationContext) -> str:
        """Return the message to type into the idle session."""
        ...


DEFAULT_NUDGE_MESSAGE = (
    "You appear to have stopped. If the task is NOT yet fully complete, "
    "continue working on it now. If it IS fully complete, create the "
    "completion file exactly as instructed in the original prompt."
)


class StaticNudgePolicy:
    """Deterministic continuation policy: always the same message."""

    def __init__(self, message: str = DEFAULT_NUDGE_MESSAGE) -> None:
        if not message.strip():
            raise ValueError("nudge message must be non-empty")
        self._message = message

    async def next_nudge(self, context: ContinuationContext) -> str:
        """Return the configured nudge message."""
        return self._message


@dataclass
class DriverResult:
    """Outcome of one interactive session attempt."""

    outcome: DriverOutcome
    """How the attempt ended."""

    final_screen: str
    """Last captured rendered screen (plain text). May be empty when the
    session died before any capture succeeded."""

    nudges_sent: int
    """Total nudges delivered this attempt."""

    detail: str | None = None
    """Human-readable diagnostic for non-completed outcomes."""


class InteractiveSessionDriver:
    """Drives one agent CLI session to completion inside tmux.

    Stateless across attempts: everything attempt-specific is passed to
    :meth:`run` and lives in its locals.
    """

    def __init__(self, tmux: TmuxControl, config: InteractiveCliConfig) -> None:
        self._tmux = tmux
        self._config = config

    async def run(
        self,
        *,
        session: str,
        command: list[str],
        cwd: Path,
        prompt: str,
        marker_path: Path,
        timeout_seconds: float,
        policy: ContinuationPolicy,
        max_nudges: int,
        transcript_path: Path | None = None,
        on_pane_pid: Callable[[int], None] | None = None,
    ) -> DriverResult:
        """Run one full attempt: launch, gate, submit, drive, harvest.

        Args:
            session: tmux session name (already sanitized, deterministic).
            command: agent argv (executable + args), exec'd directly.
            cwd: working directory for the agent (the sheet workspace).
            prompt: full prompt text, already carrying the completion
                protocol suffix that names ``marker_path``.
            marker_path: per-attempt completion marker the agent creates.
            timeout_seconds: overall attempt deadline.
            policy: continuation policy consulted on idle.
            max_nudges: consecutive-idle nudge budget (resets when the
                agent transitions back to verified-busy).
            transcript_path: raw pipe-pane log destination (debug artifact),
                or None to skip transcript capture.
            on_pane_pid: callback fired with the pane PID once known —
                the backend routes this to the baton's process-group
                registration.

        Never raises TmuxError — tmux failures map to ``session_lost`` /
        ``startup_failed`` outcomes. CancelledError propagates (after
        cleanup) so musician-task cancellation works unchanged.
        """
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
        if max_nudges < 0:
            raise ValueError(f"max_nudges must be >= 0, got {max_nudges}")

        cfg = self._config
        deadline = time.monotonic() + timeout_seconds
        last_screen = ""
        nudges_total = 0

        try:
            # ── LAUNCH ────────────────────────────────────────────────
            # Idempotent relaunch safety: a stale same-name session from a
            # crashed prior attempt must die before we create ours.
            await self._tmux.kill_session(session)

            # A pre-existing marker (crashed prior attempt, adversarial
            # leftover) must never complete this attempt instantly.
            self._remove_marker(marker_path)

            await self._tmux.new_session(
                session,
                command,
                cwd=cwd,
                width=cfg.terminal_width,
                height=cfg.terminal_height,
            )

            if transcript_path is not None:
                try:
                    await self._tmux.pipe_pane_to_file(session, transcript_path)
                except TmuxError as e:
                    # Transcript is a debug artifact — its failure must not
                    # fail the attempt.
                    _logger.warning(
                        "interactive_transcript_unavailable",
                        session=session,
                        error=str(e),
                    )
                    transcript_path = None

            pid = await self._tmux.pane_pid(session)
            if pid is not None and on_pane_pid is not None:
                on_pane_pid(pid)

            # ── GATES → READY ─────────────────────────────────────────
            ready, last_screen = await self._await_ready(session, deadline)
            if not ready:
                return DriverResult(
                    outcome="startup_failed",
                    final_screen=last_screen,
                    nudges_sent=0,
                    detail=(
                        f"session never reached the ready prompt within "
                        f"{cfg.startup_timeout_seconds}s "
                        f"(ready_pattern={cfg.ready_pattern!r})"
                    ),
                )

            # ── SUBMIT (verified — see _submit_text) ──────────────────
            await self._submit_text(session, prompt)
            submitted_at = time.monotonic()

            # ── DRIVE ─────────────────────────────────────────────────
            outcome, last_screen, nudges_total = await self._drive(
                session=session,
                marker_path=marker_path,
                deadline=deadline,
                submitted_at=submitted_at,
                policy=policy,
                max_nudges=max_nudges,
                transcript_path=transcript_path,
            )

            # ── HARVEST (exactly once — the single return below) ──────
            final_screen = (
                last_screen
                if outcome == "provider_error"
                else await self._final_screen(session, last_screen)
            )
            detail: str | None = None
            if outcome == "nudges_exhausted":
                detail = (
                    f"agent went idle and did not complete after "
                    f"{nudges_total} nudge(s)"
                )
            elif outcome == "timeout":
                detail = f"attempt deadline ({timeout_seconds}s) reached"
            elif outcome == "session_lost":
                detail = "agent session died before signalling completion"
            elif outcome == "provider_error":
                detail = "interactive provider error screen detected"
            return DriverResult(
                outcome=outcome,
                final_screen=final_screen,
                nudges_sent=nudges_total,
                detail=detail,
            )

        except TmuxError as e:
            # Control-plane failure outside the drive loop (launch/submit).
            _logger.error(
                "interactive_session_control_failure",
                session=session,
                error=str(e),
                stderr=e.stderr,
            )
            return DriverResult(
                outcome="startup_failed",
                final_screen=last_screen,
                nudges_sent=nudges_total,
                detail=f"tmux control failure: {e}",
            )
        finally:
            # CLEANUP — idempotent, every exit path including cancellation.
            await self._cleanup(session)

    # ──────────────────────────────────────────────────────────────────
    # Phases
    # ──────────────────────────────────────────────────────────────────

    async def _await_ready(
        self, session: str, deadline: float,
    ) -> tuple[bool, str]:
        """Poll until the ready prompt appears, dismissing startup gates.

        Gates fire in order, each at most once, and are never fired after
        the ready pattern has been seen — a late-matching gate must not
        type into the agent's input. Returns (ready, last_screen).

        Gate checks run BEFORE the ready check, and a screen showing any
        gate pattern is never declared ready: real dialogs render a
        selection cursor (claude's trust prompt shows ``❯ 1. Yes, …``)
        that a ready pattern can match, and a prompt pasted into a modal
        dialog is silently discarded — the agent then runs with no
        instructions at all (live repro: nudge-verification, 2026-06-12).
        """
        cfg = self._config
        ready_re = re.compile(cfg.ready_pattern)
        startup_deadline = min(
            time.monotonic() + cfg.startup_timeout_seconds, deadline,
        )
        fired: set[int] = set()
        screen = ""

        while time.monotonic() < startup_deadline:
            try:
                screen = await self._tmux.capture_screen(session)
            except TmuxError:
                # Session may still be booting; brief grace, then retry.
                if not await self._session_alive(session):
                    return False, screen
            else:
                gate_on_screen = False
                for idx, gate in enumerate(cfg.startup_gates):
                    if not re.search(gate.pattern, screen):
                        continue
                    gate_on_screen = True
                    if idx in fired:
                        continue
                    _logger.info(
                        "interactive_startup_gate_fired",
                        session=session,
                        gate_index=idx,
                        pattern=gate.pattern,
                    )
                    await self._tmux.send_keys(session, gate.keys)
                    fired.add(idx)
                    break  # one gate per poll; re-capture before the next
                if not gate_on_screen and ready_re.search(screen):
                    return True, screen
            await asyncio.sleep(cfg.poll_interval_seconds)

        return False, screen

    async def _drive(
        self,
        *,
        session: str,
        marker_path: Path,
        deadline: float,
        submitted_at: float,
        policy: ContinuationPolicy,
        max_nudges: int,
        transcript_path: Path | None,
    ) -> tuple[DriverOutcome, str, int]:
        """The poll loop: busy / idle / done / dead until an outcome.

        Returns (outcome, last_screen, total_nudges).
        """
        cfg = self._config
        busy_res = [re.compile(p) for p in cfg.busy_patterns]
        total_cap = max(max_nudges, 1) * _TOTAL_NUDGE_MULTIPLIER

        last_screen = ""
        last_work_area: str | None = None
        last_change = time.monotonic()
        consecutive_nudges = 0
        total_nudges = 0
        nudged_since_busy = False
        transcript_live = transcript_path is not None

        while True:
            now = time.monotonic()
            if now >= deadline:
                return "timeout", last_screen, total_nudges

            await asyncio.sleep(
                min(cfg.poll_interval_seconds, max(deadline - now, 0.05)),
            )

            # Done check first — the marker is valid even if the pane has
            # already exited (agent wrote it, then quit).
            if marker_path.is_file():
                return "completed", last_screen, total_nudges

            try:
                screen = await self._tmux.capture_screen(session)
            except TmuxError:
                if marker_path.is_file():
                    return "completed", last_screen, total_nudges
                return "session_lost", last_screen, total_nudges
            last_screen = screen

            # Transcript growth cap — debug artifact must not eat the disk.
            if transcript_live and transcript_path is not None:
                transcript_live = await self._enforce_transcript_cap(
                    session, transcript_path,
                )

            provider_error_kind = self._provider_error_screen_kind(screen)
            if provider_error_kind is not None:
                _logger.warning(
                    "interactive_provider_error_screen",
                    session=session,
                    kind=provider_error_kind,
                )
                return "provider_error", last_screen, total_nudges

            busy = any(r.search(screen) for r in busy_res)
            work_area = self._work_area(screen)
            if work_area != last_work_area:
                last_work_area = work_area
                last_change = time.monotonic()

            if busy:
                # Verified work after a nudge earns the budget back.
                if nudged_since_busy:
                    consecutive_nudges = 0
                    nudged_since_busy = False
                continue

            quiet_for = time.monotonic() - last_change
            if quiet_for < cfg.quiet_seconds:
                continue

            # Idle: consult the budget, then the policy.
            if consecutive_nudges >= max_nudges or total_nudges >= total_cap:
                return "nudges_exhausted", last_screen, total_nudges

            context = ContinuationContext(
                nudge_count=total_nudges,
                elapsed_seconds=time.monotonic() - submitted_at,
            )
            message = await policy.next_nudge(context)
            _logger.info(
                "interactive_nudge_sent",
                session=session,
                consecutive=consecutive_nudges + 1,
                total=total_nudges + 1,
                idle_seconds=round(quiet_for, 1),
            )
            try:
                await self._submit_text(session, message)
            except TmuxError:
                # Mid-drive control failure is session-lost, not a startup
                # problem — keep the outcome label honest.
                if marker_path.is_file():
                    return "completed", last_screen, total_nudges
                return "session_lost", last_screen, total_nudges
            consecutive_nudges += 1
            total_nudges += 1
            nudged_since_busy = True
            # Restart the quiet clock — give the agent a full window to react.
            last_change = time.monotonic()

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    async def _submit_text(self, session: str, text: str) -> None:
        """Paste text into the input box and submit it, verifying delivery.

        The live-smoke failure mode this guards against: Enter arriving
        while the TUI is still ingesting the bracketed paste gets consumed
        by the paste handling instead of submitting, leaving the prompt
        stranded in the input box. Settle after the paste; after each
        Enter, watch briefly for evidence the message went through (a busy
        pattern, or the input prompt returning bare); re-send Enter when
        unverified — harmless on an already-empty input box.
        """
        cfg = self._config
        busy_res = [re.compile(p) for p in cfg.busy_patterns]
        ready_re = re.compile(cfg.ready_pattern)

        # Paste-landed verification: a paste must produce SOME visible
        # change (input-box echo, or claude's "[Pasted text #N]" chip).
        # An unchanged screen means a modal dialog or similar swallowed
        # the paste — re-paste bounded, then proceed (the drive loop's
        # nudge/timeout machinery owns the aftermath). Guards against
        # dialogs NOT covered by startup_gates; the known ones are
        # handled before ready in _await_ready.
        before: str | None
        try:
            before = self._work_area(await self._tmux.capture_screen(session))
        except TmuxError:
            before = None

        for paste_attempt in range(1, _PASTE_MAX_ATTEMPTS + 1):
            await self._tmux.paste_text(session, text)
            await asyncio.sleep(_PASTE_SETTLE_SECONDS)
            if before is None:
                break
            try:
                after = self._work_area(await self._tmux.capture_screen(session))
            except TmuxError:
                break
            if after != before:
                break
            _logger.warning(
                "interactive_paste_unverified",
                session=session,
                attempt=paste_attempt,
                text_length=len(text),
            )

        for attempt in range(1, _SUBMIT_MAX_ENTERS + 1):
            await self._tmux.send_keys(session, ["Enter"])
            deadline = time.monotonic() + _SUBMIT_VERIFY_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(min(0.5, cfg.poll_interval_seconds))
                try:
                    screen = await self._tmux.capture_screen(session)
                except TmuxError:
                    # Verification capture failed (session may have died).
                    # Hand off to the drive loop — it classifies dead
                    # sessions correctly; aborting here would mislabel.
                    return
                if any(r.search(screen) for r in busy_res):
                    return
                if self._input_box_bare(screen, ready_re):
                    return
            _logger.warning(
                "interactive_submit_unverified",
                session=session,
                attempt=attempt,
                text_length=len(text),
            )
        # Bounded retries exhausted — proceed; the drive loop's idle/nudge
        # machinery (and ultimately the sheet timeout) takes it from here.

    @staticmethod
    def _input_box_bare(screen: str, ready_re: re.Pattern[str]) -> bool:
        """Whether some input-prompt line exists with nothing typed after it.

        After a successful submit the TUI clears the input box, leaving the
        bare prompt (e.g. ``❯ ``). While text is pending, the prompt line
        carries content. Heuristic — combined with the busy check and a
        bounded retry whose extra Enter is a no-op on an empty box.
        """
        for line in screen.splitlines():
            match = ready_re.search(line)
            if match is not None and not line[match.end():].strip():
                return True
        return False

    def _work_area(self, screen: str) -> str:
        """The screen minus its volatile tail lines (status bars/counters)."""
        tail = self._config.volatile_tail_lines
        if tail <= 0:
            return screen
        lines = screen.splitlines()
        return "\n".join(lines[:-tail]) if len(lines) > tail else ""

    def _provider_error_screen_kind(self, screen: str) -> str | None:
        """Return the verified provider error kind currently on screen.

        These patterns are profile-owned and deliberately separate from the
        broad CLI stdout/stderr classifiers. The driver checks them before
        idle handling so a prompt-returning quota/auth screen cannot be
        nudged as if the agent merely stopped working.
        """
        checks = (
            ("rate_limit", self._config.rate_limit_screen_patterns),
            ("auth", self._config.auth_error_screen_patterns),
            ("crash", self._config.crash_screen_patterns),
            ("capacity", self._config.capacity_screen_patterns),
        )
        for kind, patterns in checks:
            if any(re.search(pattern, screen, re.IGNORECASE) for pattern in patterns):
                return kind
        return None

    @staticmethod
    def _remove_marker(marker_path: Path) -> None:
        """Delete exactly this attempt's marker if present (pre-submit)."""
        try:
            marker_path.unlink(missing_ok=True)
        except OSError as e:
            # Non-fatal: a stale undeletable marker means instant false
            # completion, so log loudly.
            _logger.error(
                "interactive_marker_cleanup_failed",
                marker=str(marker_path),
                error=str(e),
            )

    async def _session_alive(self, session: str) -> bool:
        """Best-effort session existence probe (False on control failure)."""
        try:
            return await self._tmux.has_session(session)
        except TmuxError:
            return False

    async def _enforce_transcript_cap(
        self, session: str, transcript_path: Path,
    ) -> bool:
        """Stop pipe-pane once the raw log exceeds the size cap.

        Returns whether the transcript is still live.
        """
        try:
            size = transcript_path.stat().st_size
        except OSError:
            return True
        if size <= _TRANSCRIPT_MAX_BYTES:
            return True
        _logger.warning(
            "interactive_transcript_capped",
            session=session,
            transcript=str(transcript_path),
            size_bytes=size,
        )
        try:
            await self._tmux.pipe_pane_off(session)
        except TmuxError:
            pass
        return False

    async def _final_screen(self, session: str, fallback: str) -> str:
        """Final-screen capture for HARVEST; resilient to a dead session."""
        try:
            return await self._tmux.capture_screen(session)
        except TmuxError:
            return fallback

    async def _cleanup(self, session: str) -> None:
        """Idempotent teardown: stop piping, kill the session.

        Never raises — cleanup failures are logged, not propagated, so the
        real outcome (or cancellation) is preserved. Uses shield-free
        sequential awaits; each tmux call has its own short timeout.
        """
        try:
            await self._tmux.pipe_pane_off(session)
        except TmuxError as e:
            _logger.warning(
                "interactive_cleanup_step_failed",
                session=session,
                step="pipe_pane_off",
                error=str(e),
            )
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._force_teardown(session))
            finally:
                raise

        try:
            await self._verified_kill_session(session)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._force_teardown(session))
            finally:
                raise

    async def _verified_kill_session(self, session: str) -> None:
        """Kill a tmux session and verify it disappeared.

        A single tmux ``kill-session`` call can fail silently when invoked with
        ``allow_failure=True`` at the tmux-control layer. Verification keeps a
        completed/failed attempt from leaving an agent alive to keep mutating
        the workspace after the conductor advances.
        """
        for attempt in range(3):
            try:
                await self._tmux.kill_session(session)
            except TmuxError as e:
                _logger.warning(
                    "interactive_cleanup_step_failed",
                    session=session,
                    step="kill_session",
                    error=str(e),
                )
            try:
                if not await self._tmux.has_session(session):
                    return
            except TmuxError as e:
                _logger.warning(
                    "interactive_cleanup_step_failed",
                    session=session,
                    step="has_session",
                    error=str(e),
                )
                return
            if attempt < 2:
                await asyncio.sleep(0.15 * (attempt + 1))

        _logger.error(
            "interactive_cleanup_session_survived",
            session=session,
        )

    async def _force_teardown(self, session: str) -> None:
        """Last-resort teardown used when cleanup itself is cancelled."""
        try:
            await self._tmux.kill_session(session)
        except TmuxError:
            pass
