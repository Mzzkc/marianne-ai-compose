"""Unit tests for interactive mode — config, driver state machine, backend.

The driver is tested against a scripted FakeTmux (no real tmux server, no
model calls). Real-tmux integration lives in test_interactive_integration.py
(skipped when tmux is unavailable).

See docs/specs/2026-06-10-interactive-mode-design.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from marianne.core.config.instruments import (
    CliCommand,
    CliOutputConfig,
    CliProfile,
    InstrumentProfile,
    InteractiveCliConfig,
    InteractiveGate,
)
from marianne.execution.instruments.interactive.backend import (
    DEFAULT_MAX_NUDGES,
    InteractiveCliBackend,
    completion_marker_path,
    session_name,
)
from marianne.execution.instruments.interactive.driver import (
    ContinuationContext,
    InteractiveSessionDriver,
    StaticNudgePolicy,
)
from marianne.execution.instruments.interactive.tmux import (
    TmuxError,
    sanitize_session_name,
)

# =============================================================================
# Helpers
# =============================================================================

READY_SCREEN = "welcome banner\n❯ \nstatus line\n0 tokens"
BUSY_SCREEN = "doing work\n· Percolating… (2s)\nstatus line\n42 tokens"
GATE_SCREEN = (
    "Quick safety check: Is this a project you created or one you trust?\n"
    "Enter to confirm"
)
# The REAL claude-code trust dialog renders a selection cursor — a line that
# a loose ready pattern (``(?m)^\s*❯``) also matches. Pasting into this
# dialog silently discards the prompt (live repro: nudge-verification,
# 2026-06-12). Tests against this screen pin the gate-before-ready ordering.
TRUST_DIALOG_SCREEN = (
    "Quick safety check: Is this a project you created or one you trust?\n"
    "❯ 1. Yes, I trust this folder\n"
    "  2. No, exit\n"
    "status line"
)


def make_interactive_config(**overrides: Any) -> InteractiveCliConfig:
    """Fast-polling interactive config for tests."""
    defaults: dict[str, Any] = {
        "ready_pattern": r"(?m)^\s*❯",
        "busy_patterns": ["esc to interrupt", r"…\s*\(\d+"],
        "quiet_seconds": 0.15,
        "poll_interval_seconds": 0.03,
        "startup_timeout_seconds": 2.0,
        "volatile_tail_lines": 2,
    }
    defaults.update(overrides)
    return InteractiveCliConfig(**defaults)


def make_profile(*, interactive: InteractiveCliConfig | None = None) -> InstrumentProfile:
    """A minimal CLI profile, optionally with interactive support."""
    return InstrumentProfile(
        name="fake-agent",
        display_name="Fake Agent",
        kind="cli",
        cli=CliProfile(
            command=CliCommand(
                executable="fake-agent",
                auto_approve_flag="--yolo",
                model_flag="--model",
                mcp_disable_args=["--no-mcp"],
            ),
            output=CliOutputConfig(format="text"),
            interactive=interactive,
        ),
        default_model="fake-1",
        default_timeout_seconds=30.0,
    )


class FakeTmux:
    """Scripted TmuxControl stand-in.

    ``screens`` is a list served sequentially by capture_screen (the last
    entry repeats forever). Entries may be:
    - str: the screen text
    - Exception instance: raised by that capture
    - callable: invoked (for side effects like creating the marker), and
      its return value used as the screen text

    Input-box fidelity: like a real TUI, pasted text is echoed into the
    capture (appended as an input line) until an Enter submits it. Set
    ``swallow_pastes`` to simulate a modal dialog that silently discards
    paste input (the lost-prompt failure mode).
    """

    def __init__(self, screens: list[Any] | None = None) -> None:
        self.screens: list[Any] = screens if screens is not None else [READY_SCREEN]
        self.capture_count = 0
        self.killed_sessions: list[str] = []
        self.created: list[tuple[str, list[str]]] = []
        self.pasted: list[str] = []
        self.sent_keys: list[list[str]] = []
        self.piped_to: list[Path] = []
        self.pipe_off_count = 0
        self.fake_pane_pid: int | None = 4242
        self.pending_input: str | None = None
        self.swallow_pastes = False

    async def kill_session(self, session: str) -> None:
        self.killed_sessions.append(session)

    async def new_session(
        self, session: str, command: list[str], *, cwd: Path, width: int, height: int,
    ) -> None:
        self.created.append((session, command))

    async def has_session(self, session: str) -> bool:
        return True

    async def pipe_pane_to_file(self, session: str, log_path: Path) -> None:
        self.piped_to.append(log_path)

    async def pipe_pane_off(self, session: str) -> None:
        self.pipe_off_count += 1

    async def pane_pid(self, session: str) -> int | None:
        return self.fake_pane_pid

    async def capture_screen(self, session: str) -> str:
        idx = min(self.capture_count, len(self.screens) - 1)
        self.capture_count += 1
        entry = self.screens[idx]
        if isinstance(entry, Exception):
            raise entry
        screen = str(entry()) if callable(entry) else str(entry)
        if self.pending_input is not None:
            first_line = self.pending_input.splitlines()[0][:60]
            screen = f"{screen}\n❯ {first_line}"
        return screen

    async def paste_text(self, session: str, text: str) -> None:
        self.pasted.append(text)
        if not self.swallow_pastes:
            self.pending_input = text

    async def send_keys(self, session: str, keys: list[str]) -> None:
        self.sent_keys.append(list(keys))
        if "Enter" in keys:
            self.pending_input = None

    async def version(self) -> tuple[int, int] | None:
        return (3, 4)


async def run_driver(
    fake: FakeTmux,
    tmp_path: Path,
    *,
    config: InteractiveCliConfig | None = None,
    timeout_seconds: float = 5.0,
    max_nudges: int = 2,
    marker: Path | None = None,
    transcript: Path | None = None,
    on_pane_pid: Any = None,
) -> Any:
    """Run the driver with test-speed settings."""
    cfg = config or make_interactive_config()
    driver = InteractiveSessionDriver(fake, cfg)  # type: ignore[arg-type]
    return await driver.run(
        session="mzt-test-s1-a1",
        command=["fake-agent", "--yolo"],
        cwd=tmp_path,
        prompt="do the task",
        marker_path=marker if marker is not None else tmp_path / ".marianne" / "m.complete",
        timeout_seconds=timeout_seconds,
        policy=StaticNudgePolicy("please continue"),
        max_nudges=max_nudges,
        transcript_path=transcript,
        on_pane_pid=on_pane_pid,
    )


# =============================================================================
# Config models
# =============================================================================


class TestInteractiveConfigModels:
    def test_defaults(self) -> None:
        cfg = InteractiveCliConfig(ready_pattern="ready")
        assert cfg.quiet_seconds == 15.0
        assert cfg.poll_interval_seconds == 2.0
        assert cfg.startup_timeout_seconds == 90.0
        assert cfg.terminal_width == 200
        assert cfg.terminal_height == 50
        assert cfg.volatile_tail_lines == 2
        assert cfg.startup_gates == []
        assert cfg.busy_patterns == []

    def test_invalid_ready_regex_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid pattern regex"):
            InteractiveCliConfig(ready_pattern="(unclosed")

    def test_invalid_busy_regex_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid pattern regex"):
            InteractiveCliConfig(ready_pattern="ok", busy_patterns=["[bad"])

    def test_invalid_gate_regex_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid gate pattern"):
            InteractiveGate(pattern="(unclosed", keys=["Enter"])

    def test_gate_default_keys(self) -> None:
        gate = InteractiveGate(pattern="trust")
        assert gate.keys == ["Enter"]

    def test_cli_profile_interactive_default_none(self) -> None:
        profile = make_profile()
        assert profile.cli is not None
        assert profile.cli.interactive is None

    def test_builtin_claude_code_has_interactive(self) -> None:
        """The claude-code builtin ships a verified interactive block."""
        import yaml

        builtin = (
            Path(__file__).parent.parent
            / "src" / "marianne" / "instruments" / "builtins" / "claude-code.yaml"
        )
        data = yaml.safe_load(builtin.read_text())
        profile = InstrumentProfile.model_validate(data)
        assert profile.cli is not None
        assert profile.cli.interactive is not None
        assert profile.cli.interactive.startup_gates
        assert profile.cli.interactive.busy_patterns

    def test_builtin_claude_code_ready_rejects_trust_dialog(self) -> None:
        """The ready pattern must NOT match the trust dialog's selection
        cursor (``❯ 1. Yes, I trust this folder``) — that screen swallows
        pasted prompts (live repro 2026-06-12)."""
        import re

        import yaml

        builtin = (
            Path(__file__).parent.parent
            / "src" / "marianne" / "instruments" / "builtins" / "claude-code.yaml"
        )
        data = yaml.safe_load(builtin.read_text())
        profile = InstrumentProfile.model_validate(data)
        assert profile.cli is not None and profile.cli.interactive is not None
        ready_re = re.compile(profile.cli.interactive.ready_pattern)

        assert ready_re.search(TRUST_DIALOG_SCREEN) is None, (
            "ready_pattern matches the trust dialog's selection cursor"
        )
        assert ready_re.search(READY_SCREEN) is not None, (
            "ready_pattern must still match a bare input prompt"
        )
        # The dialog must be covered by a startup gate instead.
        assert any(
            re.search(g.pattern, TRUST_DIALOG_SCREEN)
            for g in profile.cli.interactive.startup_gates
        )


class TestSessionNaming:
    def test_sanitize_replaces_unsafe(self) -> None:
        assert sanitize_session_name("job:with.dots and spaces") == "job-with-dots-and-spaces"

    def test_sanitize_caps_length(self) -> None:
        assert len(sanitize_session_name("x" * 500)) <= 80

    def test_sanitize_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            sanitize_session_name("")
        with pytest.raises(ValueError):
            sanitize_session_name("...")

    def test_session_name_deterministic(self) -> None:
        a = session_name("job-1", 3, 2)
        b = session_name("job-1", 3, 2)
        assert a == b
        assert a.startswith("mzt-")
        assert "s3" in a and "a2" in a

    def test_truncated_names_stay_collision_resistant(self) -> None:
        """Goal-mode audit (GPT P1): two long inputs sharing the first 80
        sanitized chars must NOT map to one session name — the driver's
        pre-launch same-name cleanup would kill the OTHER live sheet."""
        long_a = "x" * 90 + "-alpha"
        long_b = "x" * 90 + "-beta"
        assert sanitize_session_name(long_a) != sanitize_session_name(long_b)
        assert len(sanitize_session_name(long_a)) <= 80
        # Determinism survives the hash suffix.
        assert sanitize_session_name(long_a) == sanitize_session_name(long_a)

    def test_long_job_id_keeps_sheet_attempt_identity(self) -> None:
        """The -sN-aM suffix must never be truncated into ambiguity: two
        sheets (or attempts) of one long-named job must get distinct
        sessions, or the second launch kills the first."""
        long_job = "j" * 120
        assert session_name(long_job, 1, 1) != session_name(long_job, 2, 1)
        assert session_name(long_job, 1, 1) != session_name(long_job, 1, 2)

    def test_long_job_ids_get_distinct_marker_dirs(self, tmp_path: Path) -> None:
        long_a = "y" * 90 + "-alpha"
        long_b = "y" * 90 + "-beta"
        assert completion_marker_path(tmp_path, long_a, 1, 1) != (
            completion_marker_path(tmp_path, long_b, 1, 1)
        )

    def test_marker_path_per_attempt(self, tmp_path: Path) -> None:
        m1 = completion_marker_path(tmp_path, "job-1", 1, 1)
        m2 = completion_marker_path(tmp_path, "job-1", 1, 2)
        m3 = completion_marker_path(tmp_path, "job-1", 2, 1)
        m4 = completion_marker_path(tmp_path, "job-2", 1, 1)
        assert len({m1, m2, m3, m4}) == 4, "markers must be attempt-unique"
        assert all(str(m).startswith(str(tmp_path)) for m in (m1, m2, m3, m4))


# =============================================================================
# Driver state machine (FakeTmux)
# =============================================================================


class TestDriverHappyPath:
    async def test_completed_via_marker(self, tmp_path: Path) -> None:
        marker = tmp_path / ".marianne" / "m.complete"

        def busy_then_done() -> str:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            return BUSY_SCREEN

        fake = FakeTmux([READY_SCREEN, BUSY_SCREEN, busy_then_done, READY_SCREEN])
        result = await run_driver(fake, tmp_path, marker=marker)

        assert result.outcome == "completed"
        assert result.nudges_sent == 0
        # Prompt was pasted, Enter sent
        assert any("do the task" in p for p in fake.pasted)
        assert ["Enter"] in fake.sent_keys
        # Cleanup: pre-launch kill + final kill
        assert fake.killed_sessions.count("mzt-test-s1-a1") >= 2

    async def test_pane_pid_callback_fired(self, tmp_path: Path) -> None:
        marker = tmp_path / "m.complete"
        marker.touch()  # pre-existing — must be deleted at launch

        def make_done() -> str:
            marker.touch()
            return READY_SCREEN

        fake = FakeTmux([READY_SCREEN, make_done])
        seen: list[int] = []
        result = await run_driver(
            fake, tmp_path, marker=marker, on_pane_pid=seen.append,
        )
        assert result.outcome == "completed"
        assert seen == [4242]

    async def test_preexisting_marker_deleted_before_submit(
        self, tmp_path: Path,
    ) -> None:
        """A stale marker must never instantly complete the new attempt."""
        marker = tmp_path / "m.complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        # Agent never works and never re-creates the marker → the attempt
        # must end in nudges_exhausted, NOT completed.
        fake = FakeTmux([READY_SCREEN])
        result = await run_driver(fake, tmp_path, marker=marker, max_nudges=1)
        assert result.outcome == "nudges_exhausted"
        assert not marker.exists()


class TestDriverGates:
    async def test_gate_fires_once_then_ready(self, tmp_path: Path) -> None:
        marker = tmp_path / "m.complete"

        def make_done() -> str:
            marker.touch()
            return READY_SCREEN

        cfg = make_interactive_config(
            startup_gates=[
                InteractiveGate(pattern="Is this a project you created", keys=["Enter"]),
            ],
        )
        fake = FakeTmux([GATE_SCREEN, GATE_SCREEN, READY_SCREEN, make_done])
        result = await run_driver(fake, tmp_path, config=cfg, marker=marker)

        assert result.outcome == "completed"
        # Gate Enter + submit Enter (at least); gate fired exactly once
        # before ready: count Enters sent before the paste happened.
        assert fake.sent_keys.count(["Enter"]) >= 2

    async def test_gates_skipped_after_ready(self, tmp_path: Path) -> None:
        """A gate matching AFTER ready must not fire (misdirected Enter)."""
        marker = tmp_path / "m.complete"

        def make_done() -> str:
            marker.touch()
            # Screen contains BOTH ready prompt and gate-like text
            return READY_SCREEN + "\nIs this a project you created?"

        cfg = make_interactive_config(
            startup_gates=[
                InteractiveGate(pattern="Is this a project you created", keys=["q"]),
            ],
        )
        fake = FakeTmux([READY_SCREEN, make_done])
        result = await run_driver(fake, tmp_path, config=cfg, marker=marker)
        assert result.outcome == "completed"
        assert ["q"] not in fake.sent_keys

    async def test_startup_timeout(self, tmp_path: Path) -> None:
        cfg = make_interactive_config(startup_timeout_seconds=0.15)
        fake = FakeTmux(["booting...\nno prompt here"])
        result = await run_driver(fake, tmp_path, config=cfg)
        assert result.outcome == "startup_failed"
        assert result.detail is not None and "ready prompt" in result.detail
        # Session still cleaned up
        assert "mzt-test-s1-a1" in fake.killed_sessions

    async def test_dialog_cursor_does_not_read_as_ready(
        self, tmp_path: Path,
    ) -> None:
        """A pending gate beats the ready check on the same screen.

        The real trust dialog renders ``❯ 1. Yes, I trust this folder`` —
        a loose ready pattern matches that line, the driver pastes the
        prompt into the dialog, and the dialog discards it. The sheet then
        sits idle with an agent that never saw its instructions (live
        repro 2026-06-12). The gate must fire FIRST; the prompt may only
        be pasted once the dialog is gone.
        """
        marker = tmp_path / "m.complete"
        ops: list[str] = []

        def dialog_then_ready() -> str:
            # Dialog stays up until its Enter arrives.
            if ["Enter"] in fake.sent_keys:
                if not marker.exists() and fake.pasted:
                    marker.touch()
                return READY_SCREEN
            return TRUST_DIALOG_SCREEN

        cfg = make_interactive_config(
            startup_gates=[
                InteractiveGate(
                    pattern="Is this a project you created", keys=["Enter"],
                ),
            ],
        )
        fake = FakeTmux([dialog_then_ready])
        orig_paste = fake.paste_text
        orig_send = fake.send_keys

        async def paste_logged(session: str, text: str) -> None:
            ops.append(f"paste:{text[:12]}")
            await orig_paste(session, text)

        async def send_logged(session: str, keys: list[str]) -> None:
            ops.append(f"keys:{'+'.join(keys)}")
            await orig_send(session, keys)

        fake.paste_text = paste_logged  # type: ignore[method-assign]
        fake.send_keys = send_logged  # type: ignore[method-assign]

        result = await run_driver(fake, tmp_path, config=cfg, marker=marker)

        assert result.outcome == "completed"
        first_paste = next(i for i, op in enumerate(ops) if op.startswith("paste:"))
        gate_enter = next(i for i, op in enumerate(ops) if op == "keys:Enter")
        assert gate_enter < first_paste, (
            f"prompt was pasted before the gate dismissed the dialog: {ops}"
        )

    async def test_swallowed_paste_repastes_bounded(self, tmp_path: Path) -> None:
        """A screen that silently discards pastes triggers bounded re-paste.

        Defense against UNKNOWN dialogs (not covered by startup_gates):
        when the paste produces no visible change, the driver must retry
        the paste a bounded number of times — and proceed afterwards
        rather than dead-ending.
        """
        fake = FakeTmux([READY_SCREEN])
        fake.swallow_pastes = True
        result = await run_driver(fake, tmp_path, max_nudges=0)
        # Nothing lands, agent never works: outcome is nudges_exhausted.
        assert result.outcome == "nudges_exhausted"
        # Original paste retried (bounded, >1 total), not infinite.
        prompt_pastes = [p for p in fake.pasted if p == "do the task"]
        assert 2 <= len(prompt_pastes) <= 4

    async def test_landed_paste_not_repasted(self, tmp_path: Path) -> None:
        """A paste that visibly lands must be pasted exactly once —
        re-pasting a delivered prompt would duplicate the instructions."""
        marker = tmp_path / "m.complete"

        def make_done() -> str:
            if fake.pasted:
                marker.touch()
            return READY_SCREEN

        fake = FakeTmux([make_done])
        result = await run_driver(fake, tmp_path, marker=marker)
        assert result.outcome == "completed"
        assert [p for p in fake.pasted if p == "do the task"] == ["do the task"]


class TestDriverNudges:
    async def test_idle_nudges_then_exhausted(self, tmp_path: Path) -> None:
        fake = FakeTmux([READY_SCREEN])  # idle forever, never busy
        result = await run_driver(fake, tmp_path, max_nudges=2)
        assert result.outcome == "nudges_exhausted"
        assert result.nudges_sent == 2
        # Both nudge messages pasted (plus the original prompt)
        nudges = [p for p in fake.pasted if p == "please continue"]
        assert len(nudges) == 2

    async def test_nudge_then_done(self, tmp_path: Path) -> None:
        marker = tmp_path / "m.complete"
        nudged = False

        def screen() -> str:
            # Idle until a nudge lands; then complete.
            if nudged:
                marker.touch()
            return READY_SCREEN

        fake = FakeTmux([screen])
        orig_paste = fake.paste_text

        async def paste_and_mark(session: str, text: str) -> None:
            nonlocal nudged
            await orig_paste(session, text)
            if text == "please continue":
                nudged = True

        fake.paste_text = paste_and_mark  # type: ignore[method-assign]
        result = await run_driver(fake, tmp_path, marker=marker, max_nudges=3)
        assert result.outcome == "completed"
        assert result.nudges_sent == 1

    async def test_consecutive_counter_resets_on_busy(self, tmp_path: Path) -> None:
        """Verified work after a nudge earns the budget back."""
        marker = tmp_path / "m.complete"
        state = {"nudges_seen": 0, "busy_left": 0}

        def screen() -> str:
            # After each nudge: a few busy polls (covers the submit-verify
            # capture AND the drive-loop capture), then idle again. After
            # the 3rd nudge, complete. With max_nudges=2 this only
            # completes if the consecutive counter resets on busy.
            if state["nudges_seen"] >= 3:
                marker.touch()
                return READY_SCREEN
            if state["busy_left"] > 0:
                state["busy_left"] -= 1
                return BUSY_SCREEN
            return READY_SCREEN

        fake = FakeTmux([screen])
        orig_paste = fake.paste_text

        async def paste_hook(session: str, text: str) -> None:
            await orig_paste(session, text)
            if text == "please continue":
                state["nudges_seen"] += 1
                state["busy_left"] = 3

        fake.paste_text = paste_hook  # type: ignore[method-assign]
        result = await run_driver(fake, tmp_path, marker=marker, max_nudges=2)
        assert result.outcome == "completed"
        assert result.nudges_sent == 3


class TestDriverFailureModes:
    async def test_timeout_while_busy(self, tmp_path: Path) -> None:
        fake = FakeTmux([READY_SCREEN, BUSY_SCREEN])  # busy forever after submit
        result = await run_driver(fake, tmp_path, timeout_seconds=0.4)
        assert result.outcome == "timeout"
        assert "mzt-test-s1-a1" in fake.killed_sessions

    async def test_session_lost_mid_drive(self, tmp_path: Path) -> None:
        fake = FakeTmux([READY_SCREEN, READY_SCREEN, TmuxError("server gone")])
        result = await run_driver(fake, tmp_path)
        assert result.outcome == "session_lost"

    async def test_marker_wins_over_dead_session(self, tmp_path: Path) -> None:
        """Agent wrote the marker then exited — that is completion."""
        marker = tmp_path / "m.complete"

        def die_after_marker() -> str:
            marker.touch()
            return READY_SCREEN

        fake = FakeTmux([READY_SCREEN, die_after_marker, TmuxError("pane dead")])
        result = await run_driver(fake, tmp_path, marker=marker)
        assert result.outcome == "completed"

    async def test_volatile_tail_excluded_from_change_hash(
        self, tmp_path: Path,
    ) -> None:
        """Status-line churn (token counters) must not defeat idle detection."""
        counter = {"n": 0}

        def churning_status() -> str:
            counter["n"] += 1
            # Work area identical; only the last 2 lines change every poll.
            return f"work area stable\n❯ \nstatus {counter['n']}\n{counter['n']} tokens"

        fake = FakeTmux([churning_status])
        result = await run_driver(fake, tmp_path, max_nudges=1)
        # Idle detection fired (nudge then exhaustion) despite tail churn.
        assert result.outcome == "nudges_exhausted"

    async def test_cancellation_cleans_up(self, tmp_path: Path) -> None:
        fake = FakeTmux([BUSY_SCREEN])  # busy forever
        cfg = make_interactive_config()
        driver = InteractiveSessionDriver(fake, cfg)  # type: ignore[arg-type]
        task = asyncio.create_task(driver.run(
            session="mzt-cancel-s1-a1",
            command=["fake-agent"],
            cwd=tmp_path,
            prompt="task",
            marker_path=tmp_path / "m.complete",
            timeout_seconds=30.0,
            policy=StaticNudgePolicy(),
            max_nudges=2,
        ))
        # Let it reach the drive loop, then cancel.
        deadline = asyncio.get_event_loop().time() + 5.0
        while fake.capture_count < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "mzt-cancel-s1-a1" in fake.killed_sessions

    async def test_invalid_args_rejected(self, tmp_path: Path) -> None:
        fake = FakeTmux()
        with pytest.raises(ValueError):
            await run_driver(fake, tmp_path, timeout_seconds=0)
        with pytest.raises(ValueError):
            await run_driver(fake, tmp_path, max_nudges=-1)


class TestStaticNudgePolicy:
    async def test_returns_message(self) -> None:
        policy = StaticNudgePolicy("go on")
        msg = await policy.next_nudge(
            ContinuationContext(nudge_count=0, elapsed_seconds=1.0),
        )
        assert msg == "go on"

    def test_rejects_empty_message(self) -> None:
        with pytest.raises(ValueError):
            StaticNudgePolicy("   ")


# =============================================================================
# Backend
# =============================================================================


class TestInteractiveBackendConstruction:
    def test_requires_interactive_block(self) -> None:
        profile = make_profile(interactive=None)
        with pytest.raises(ValueError, match="no cli.interactive config"):
            InteractiveCliBackend(profile)

    def test_requires_cli_kind(self) -> None:
        profile = InstrumentProfile(
            name="http-thing",
            display_name="HTTP Thing",
            kind="http",
            http={"base_url": "http://x", "schema_family": "openai"},  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="kind=cli"):
            InteractiveCliBackend(profile)

    def test_build_command_interactive_shape(self) -> None:
        profile = make_profile(
            interactive=make_interactive_config(extra_args=["--extra"]),
        )
        backend = InteractiveCliBackend(profile, tmux=FakeTmux())  # type: ignore[arg-type]
        cmd = backend._build_command()
        assert cmd == [
            "fake-agent", "--yolo", "--model", "fake-1", "--no-mcp", "--extra",
        ]
        # Headless machinery absent
        assert "-p" not in cmd

    def test_clear_overrides_resets_per_sheet_state(self) -> None:
        profile = make_profile(interactive=make_interactive_config())
        backend = InteractiveCliBackend(profile, tmux=FakeTmux())  # type: ignore[arg-type]
        backend.apply_overrides({"model": "other-model"})
        backend.set_attempt_identity("job-x", 1, 1)
        backend.configure_interactive(max_nudges=9, nudge_message="hi")

        backend.clear_overrides()

        assert backend._model == "fake-1"
        assert backend._attempt_identity is None
        assert backend._max_nudges == DEFAULT_MAX_NUDGES
        assert backend._nudge_message is None

    def test_configure_interactive_rejects_negative(self) -> None:
        profile = make_profile(interactive=make_interactive_config())
        backend = InteractiveCliBackend(profile, tmux=FakeTmux())  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            backend.configure_interactive(max_nudges=-1)


class TestInteractiveBackendExecute:
    async def test_completed_maps_to_success(self, tmp_path: Path) -> None:
        profile = make_profile(interactive=make_interactive_config())
        marker = completion_marker_path(tmp_path, "job-1", 2, 3)

        def make_done() -> str:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            return READY_SCREEN

        fake = FakeTmux([READY_SCREEN, make_done])
        backend = InteractiveCliBackend(
            profile, working_directory=tmp_path, tmux=fake,  # type: ignore[arg-type]
        )
        backend.set_attempt_identity("job-1", 2, 3)

        spawned: list[tuple[int, int]] = []
        exited: list[int] = []
        backend._on_process_group_spawned = lambda pid, pgid: spawned.append((pid, pgid))
        backend._on_process_exited = exited.append

        result = await backend.execute("do it", timeout_seconds=5.0)

        assert result.success is True
        assert result.exit_code is None  # invariant-compatible
        assert result.input_tokens is None and result.output_tokens is None
        assert result.stderr == ""
        # pane pid registered as its own pgid; exit callback fired
        assert spawned == [(4242, 4242)]
        assert exited == [4242]
        # The prompt carried the completion protocol naming the marker
        assert any(str(marker) in p for p in fake.pasted)
        # Session named deterministically from identity
        assert fake.created and fake.created[0][0] == session_name("job-1", 2, 3)
        # Identity consumed — a second execute would get a fresh token
        assert backend._attempt_identity is None

    async def test_nudges_exhausted_plain_failure(self, tmp_path: Path) -> None:
        profile = make_profile(interactive=make_interactive_config())
        fake = FakeTmux([READY_SCREEN])
        backend = InteractiveCliBackend(
            profile, working_directory=tmp_path, tmux=fake,  # type: ignore[arg-type]
        )
        backend.configure_interactive(max_nudges=1)
        result = await backend.execute("do it", timeout_seconds=5.0)

        assert result.success is False
        assert result.error_type is None  # plain failed attempt (lab 3-1)
        assert result.rate_limited is False
        assert result.error_message is not None and "nudge" in result.error_message

    async def test_timeout_maps_to_timeout(self, tmp_path: Path) -> None:
        profile = make_profile(interactive=make_interactive_config())
        fake = FakeTmux([READY_SCREEN, BUSY_SCREEN])
        backend = InteractiveCliBackend(
            profile, working_directory=tmp_path, tmux=fake,  # type: ignore[arg-type]
        )
        result = await backend.execute("do it", timeout_seconds=0.4)
        assert result.success is False
        assert result.exit_reason == "timeout"

    async def test_session_lost_scans_final_screen_for_rate_limit(
        self, tmp_path: Path,
    ) -> None:
        profile = make_profile(interactive=make_interactive_config())
        profile.cli.errors.rate_limit_patterns = ["You've hit your limit"]  # type: ignore[union-attr]
        limit_screen = "You've hit your limit · resets 9pm"

        # Ready until the prompt is submitted; then one dying capture (ends
        # the submit-verify), then the banner as the drive loop's last
        # successful capture, then the session is gone.
        calls = {"after_submit": 0}

        def banner_then_dead() -> str:
            if not any("Enter" in k for k in fake.sent_keys):
                return READY_SCREEN
            calls["after_submit"] += 1
            if calls["after_submit"] == 1:
                raise TmuxError("dying")
            if calls["after_submit"] == 2:
                return limit_screen
            raise TmuxError("dead")

        fake = FakeTmux([banner_then_dead])
        backend = InteractiveCliBackend(
            profile, working_directory=tmp_path, tmux=fake,  # type: ignore[arg-type]
        )
        result = await backend.execute("do it", timeout_seconds=5.0)
        assert result.success is False
        assert result.rate_limited is True

    async def test_nudges_exhausted_never_scans_for_rate_limit(
        self, tmp_path: Path,
    ) -> None:
        """GH#189 discipline: agent prose mentioning limits ≠ rate limit."""
        profile = make_profile(interactive=make_interactive_config())
        profile.cli.errors.rate_limit_patterns = ["rate.?limit"]  # type: ignore[union-attr]
        prose = READY_SCREEN + "\nI built a rate limit module for the API"
        fake = FakeTmux([prose])
        backend = InteractiveCliBackend(
            profile, working_directory=tmp_path, tmux=fake,  # type: ignore[arg-type]
        )
        backend.configure_interactive(max_nudges=1)
        result = await backend.execute("do it", timeout_seconds=5.0)
        assert result.success is False
        assert result.rate_limited is False

    async def test_startup_failed_auth_classification(self, tmp_path: Path) -> None:
        profile = make_profile(interactive=make_interactive_config(
            startup_timeout_seconds=0.15,
        ))
        profile.cli.errors.auth_error_patterns = ["not authenticated"]  # type: ignore[union-attr]
        fake = FakeTmux(["error: not authenticated\nrun: fake-agent login"])
        backend = InteractiveCliBackend(
            profile, working_directory=tmp_path, tmux=fake,  # type: ignore[arg-type]
        )
        result = await backend.execute("do it", timeout_seconds=5.0)
        assert result.success is False
        assert result.error_type == "auth"


# =============================================================================
# BackendPool wiring
# =============================================================================


class TestBackendPoolInteractive:
    def _registry(self) -> Any:
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        registry.register(make_profile(interactive=make_interactive_config()))
        return registry

    async def test_acquire_interactive_returns_interactive_backend(self) -> None:
        from marianne.daemon.baton.backend_pool import BackendPool

        pool = BackendPool(self._registry())
        backend = await pool.acquire("fake-agent", interactive=True)
        assert isinstance(backend, InteractiveCliBackend)

    async def test_interactive_and_headless_free_lists_isolated(self) -> None:
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        pool = BackendPool(self._registry())
        inter = await pool.acquire("fake-agent", interactive=True)
        await pool.release("fake-agent", inter)

        # A headless acquire must NOT receive the released interactive one
        headless = await pool.acquire("fake-agent", interactive=False)
        assert isinstance(headless, PluginCliBackend)
        assert headless is not inter

        # And the interactive acquire reuses the interactive instance
        inter2 = await pool.acquire("fake-agent", interactive=True)
        assert inter2 is inter

    async def test_acquire_interactive_without_support_raises(self) -> None:
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        registry.register(make_profile(interactive=None))
        pool = BackendPool(registry)
        with pytest.raises(ValueError, match="interactive"):
            await pool.acquire("fake-agent", interactive=True)

    async def test_default_resolution_supported_profile_is_interactive(
        self,
    ) -> None:
        """Score silence + verified profile → interactive (the new default)."""
        from marianne.daemon.baton.backend_pool import BackendPool

        pool = BackendPool(self._registry())
        backend = await pool.acquire("fake-agent")  # no interactive arg
        assert isinstance(backend, InteractiveCliBackend)

    async def test_default_resolution_unsupported_profile_is_headless(
        self,
    ) -> None:
        """Score silence + no verified support → headless, no error."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.execution.instruments.cli_backend import PluginCliBackend
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        registry.register(make_profile(interactive=None))
        pool = BackendPool(registry)
        backend = await pool.acquire("fake-agent")
        assert isinstance(backend, PluginCliBackend)

    async def test_explicit_false_forces_headless_on_supported_profile(
        self,
    ) -> None:
        """interactive: false opts a supported instrument back to headless."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.execution.instruments.cli_backend import PluginCliBackend

        pool = BackendPool(self._registry())
        backend = await pool.acquire("fake-agent", interactive=False)
        assert isinstance(backend, PluginCliBackend)

    async def test_profile_opt_out_of_default(self) -> None:
        """enabled_by_default: false keeps a verified profile opt-in."""
        from marianne.daemon.baton.backend_pool import BackendPool
        from marianne.execution.instruments.cli_backend import PluginCliBackend
        from marianne.instruments.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        registry.register(make_profile(
            interactive=make_interactive_config(enabled_by_default=False),
        ))
        pool = BackendPool(registry)
        # Silence → headless (profile opted out of the default)
        backend = await pool.acquire("fake-agent")
        assert isinstance(backend, PluginCliBackend)
        # Explicit request still works
        inter = await pool.acquire("fake-agent", interactive=True)
        assert isinstance(inter, InteractiveCliBackend)


class TestInteractiveLaunchInheritance:
    def test_interactive_subcommand_used(self) -> None:
        """goose-style: interactive subcommand, headless subcommand ignored."""
        profile = make_profile(interactive=make_interactive_config(
            subcommand="session",
        ))
        backend = InteractiveCliBackend(profile, tmux=FakeTmux())  # type: ignore[arg-type]
        cmd = backend._build_command()
        assert cmd[:2] == ["fake-agent", "session"]

    def test_inherit_auto_approve_false(self) -> None:
        """codex-style: headless-only approval flag replaced via extra_args."""
        profile = make_profile(interactive=make_interactive_config(
            inherit_auto_approve=False,
            extra_args=["--bypass-everything"],
        ))
        backend = InteractiveCliBackend(profile, tmux=FakeTmux())  # type: ignore[arg-type]
        cmd = backend._build_command()
        assert "--yolo" not in cmd  # headless auto_approve_flag suppressed
        assert "--bypass-everything" in cmd

    def test_inherit_mcp_disable_args_false(self) -> None:
        profile = make_profile(interactive=make_interactive_config(
            inherit_mcp_disable_args=False,
        ))
        backend = InteractiveCliBackend(profile, tmux=FakeTmux())  # type: ignore[arg-type]
        assert "--no-mcp" not in backend._build_command()

    def test_all_verified_builtins_have_interactive_blocks(self) -> None:
        """Five builtins ship verified interactive blocks.

        Default-on is claude-code ONLY (composer decision 2026-06-11);
        the other verified instruments are opt-in via
        instrument_config: {interactive: true}.
        """
        import yaml

        builtins_dir = (
            Path(__file__).parent.parent
            / "src" / "marianne" / "instruments" / "builtins"
        )
        for name in ("claude-code", "gemini-cli", "codex-cli", "opencode", "goose"):
            data = yaml.safe_load((builtins_dir / f"{name}.yaml").read_text())
            profile = InstrumentProfile.model_validate(data)
            assert profile.cli is not None
            assert profile.cli.interactive is not None, f"{name} lost its block"
            expected_default = name == "claude-code"
            assert profile.cli.interactive.enabled_by_default is expected_default, (
                f"{name}: enabled_by_default should be {expected_default}"
            )

    def test_unverified_builtins_stay_headless(self) -> None:
        """No guessed patterns: unverified CLIs must have NO interactive block."""
        import yaml

        builtins_dir = (
            Path(__file__).parent.parent
            / "src" / "marianne" / "instruments" / "builtins"
        )
        for name in ("aider", "crush", "cline-cli", "cli"):
            data = yaml.safe_load((builtins_dir / f"{name}.yaml").read_text())
            profile = InstrumentProfile.model_validate(data)
            if profile.cli is not None:
                assert profile.cli.interactive is None, (
                    f"{name} gained an interactive block without spike verification"
                )


# =============================================================================
# V211 pre-execution validation
# =============================================================================


class TestInteractiveSupportCheck:
    def _check(self, config_yaml: str) -> list[Any]:
        import yaml as _yaml

        from marianne.core.config import JobConfig
        from marianne.validation.checks.config import InteractiveSupportCheck

        config = JobConfig.model_validate(_yaml.safe_load(config_yaml))
        return InteractiveSupportCheck().check(
            config, Path("score.yaml"), config_yaml,
        )

    BASE = """
name: test-job
workspace: ./ws
sheet:
  size: 1
  total_items: 1
prompt:
  template: "do it"
"""

    def test_no_opt_in_no_issues(self) -> None:
        assert self._check(self.BASE) == []

    def test_interactive_on_supported_instrument_ok(self) -> None:
        yaml_text = self.BASE + """
instrument: claude-code
instrument_config:
  interactive: true
"""
        issues = [i for i in self._check(yaml_text) if i.severity.value == "error"]
        assert issues == []

    def test_interactive_on_unsupported_instrument_errors(self) -> None:
        # crush has no cli.interactive block (unverified — no auth to spike)
        yaml_text = self.BASE + """
instrument: crush
instrument_config:
  interactive: true
"""
        errors = [i for i in self._check(yaml_text) if i.severity.value == "error"]
        assert len(errors) == 1
        assert "crush" in errors[0].message
        assert errors[0].check_id == "V211"

    def test_interactive_alias_resolution(self) -> None:
        yaml_text = self.BASE + """
instrument: my-agent
instruments:
  my-agent:
    profile: crush
    config:
      interactive: true
"""
        errors = [i for i in self._check(yaml_text) if i.severity.value == "error"]
        assert len(errors) == 1
        assert "crush" in errors[0].message


# =============================================================================
# Adapter: stale-check skip for interactive sheets
# =============================================================================


class TestAdapterInteractiveStaleSkip:
    async def test_stale_check_skips_idle_kill_for_interactive(self) -> None:
        """Interactive sheets must never take the workspace-mtime kill path."""
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.baton.events import StaleCheck

        adapter = BatonAdapter.__new__(BatonAdapter)
        baton = MagicMock()

        async def _handle_event(event: Any) -> None:
            return None

        baton.handle_event = _handle_event
        adapter._baton = baton

        live_task = MagicMock()
        live_task.done.return_value = False
        adapter._active_tasks = {("job-1", 1): live_task}
        adapter._interactive_sheets = {"job-1": {1}}
        adapter._timer_wheel = MagicMock()
        # Stale config that WOULD kill if consulted — proves the skip.
        cfg = MagicMock()
        cfg.enabled = True
        cfg.idle_timeout_seconds = 0.0
        cfg.max_idle_checks_before_kill = 0
        adapter._stale_configs = {"job-1": cfg}
        adapter._stale_dispatch_time = {("job-1", 1): 0.0}
        adapter._stale_idle_strikes = {}
        adapter._stale_markers = set()
        adapter._job_sheets = {"job-1": {1: MagicMock()}}

        await adapter._handle_stale_check(StaleCheck(job_id="job-1", sheet_num=1))

        live_task.cancel.assert_not_called()
        assert ("job-1", 1) not in adapter._stale_markers
        # Re-scheduled (the dead-task backstop continues to poll)
        adapter._timer_wheel.schedule.assert_called_once()
