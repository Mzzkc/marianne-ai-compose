"""TmuxControl — async wrapper over the tmux binary.

All interactive sessions live on an isolated tmux server (socket name
``marianne``) so Marianne never touches the user's default tmux server.
Sessions are created detached with a fixed virtual terminal size; the agent
command is passed as an explicit argv tail (never a joined shell string) so
the pane process is the agent itself — pid == pgid == session leader, which
is what the baton's PID registration and group-kill plumbing expect.

Every tmux invocation runs with a short per-command timeout: a wedged tmux
server must surface as a structured :class:`TmuxError` (which the driver
treats as session-lost), never as a driver hang the sheet timeout cannot
reach.

``kill_server`` exists for tests only. Production teardown is always
per-session (``kill_session``) — a server kill would take down every
concurrent interactive sheet.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path

from marianne.core.logging import get_logger

_logger = get_logger("execution.interactive.tmux")

# Per-command timeout for every tmux subprocess call. tmux control commands
# complete in milliseconds; 10s means "the server is wedged", not "slow".
_COMMAND_TIMEOUT_SECONDS = 10.0

# Minimum supported tmux version. capture-pane/paste-buffer semantics are
# verified against 3.x; bracketed paste (-p) behavior differs in 2.x.
MIN_TMUX_VERSION: tuple[int, int] = (3, 2)

# tmux session names may not contain '.' or ':' (target syntax); keep to a
# conservative charset and a bounded length.
_SESSION_NAME_SAFE = re.compile(r"[^A-Za-z0-9_-]+")
_SESSION_NAME_MAX = 80

# Every Marianne interactive session name starts with this prefix. The
# daemon's startup orphan sweep and per-job cleanup key off it — single
# source of truth here.
SESSION_PREFIX = "mzt"
SESSION_SWEEP_PREFIX = f"{SESSION_PREFIX}-"

# Env override for the default tmux socket name. Load-bearing for test
# isolation: tests that exercise daemon startup (whose orphan sweep kills
# mzt-* sessions) must NOT touch the production 'marianne' socket — a
# concurrent live job's sessions would be killed mid-flight (observed:
# the first live smoke run was killed by a concurrently-running test
# suite's manager-start tests). tests/conftest.py sets this per run.
SOCKET_ENV_VAR = "MARIANNE_TMUX_SOCKET"
_DEFAULT_SOCKET = "marianne"


def sanitize_session_name(raw: str) -> str:
    """Sanitize a string into a valid tmux session name.

    Replaces unsafe characters with ``-`` and caps the length. Deterministic:
    the same input always produces the same name (cleanup relies on this).
    """
    if not raw:
        raise ValueError("session name must be non-empty")
    cleaned = _SESSION_NAME_SAFE.sub("-", raw).strip("-")
    if not cleaned:
        raise ValueError(f"session name {raw!r} sanitizes to empty")
    return cleaned[:_SESSION_NAME_MAX]


class TmuxError(RuntimeError):
    """A tmux control command failed or timed out.

    The driver treats any TmuxError during DRIVE as session-lost; during
    LAUNCH it is a startup failure. ``stderr`` carries tmux's own message
    for diagnostics.
    """

    def __init__(self, message: str, *, stderr: str = "", timed_out: bool = False) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.timed_out = timed_out


class TmuxControl:
    """Async control surface over an isolated tmux server.

    One instance per socket name; safe to share across sessions (tmux
    serializes commands server-side). All methods raise :class:`TmuxError`
    on failure or per-command timeout.
    """

    def __init__(self, socket_name: str | None = None) -> None:
        if socket_name is None:
            socket_name = os.environ.get(SOCKET_ENV_VAR, _DEFAULT_SOCKET)
        if not socket_name or "/" in socket_name:
            raise ValueError(f"invalid tmux socket name: {socket_name!r}")
        self._socket_name = socket_name

    @property
    def socket_name(self) -> str:
        """The tmux server socket name (``tmux -L <name>``)."""
        return self._socket_name

    async def _run(
        self,
        *args: str,
        input_bytes: bytes | None = None,
        allow_failure: bool = False,
    ) -> tuple[int, str, str]:
        """Run one tmux command with the per-command timeout.

        Returns (exit_code, stdout, stderr). Raises TmuxError on non-zero
        exit (unless ``allow_failure``), on timeout, or when the tmux binary
        is missing.
        """
        cmd = ["tmux", "-L", self._socket_name, *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if input_bytes is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise TmuxError("tmux binary not found on PATH") from e

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=input_bytes),
                timeout=_COMMAND_TIMEOUT_SECONDS,
            )
        except TimeoutError as e:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
            _logger.error(
                "tmux_command_timeout",
                socket=self._socket_name,
                command=args[0] if args else "",
            )
            raise TmuxError(
                f"tmux {args[0] if args else ''} timed out after "
                f"{_COMMAND_TIMEOUT_SECONDS}s — server wedged?",
                timed_out=True,
            ) from e

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        code = proc.returncode if proc.returncode is not None else -1

        if code != 0 and not allow_failure:
            raise TmuxError(
                f"tmux {args[0] if args else ''} failed (exit {code})",
                stderr=stderr.strip(),
            )
        return code, stdout, stderr

    async def version(self) -> tuple[int, int] | None:
        """Parse the tmux version, or None when unparseable.

        Returns (major, minor); suffixes like ``3.4a`` parse as (3, 4).
        """
        try:
            _, stdout, _ = await self._run("-V")
        except TmuxError:
            return None
        match = re.search(r"(\d+)\.(\d+)", stdout)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    async def new_session(
        self,
        session: str,
        command: list[str],
        *,
        cwd: Path,
        width: int,
        height: int,
    ) -> None:
        """Create a detached session running ``command`` as the pane process.

        The command is passed as an explicit argv tail — tmux execs it
        directly (no shell), so the pane process is the agent itself in its
        own process group (spike-verified; integration tests pin this).
        """
        if not command:
            raise ValueError("command must be non-empty")
        await self._run(
            "new-session",
            "-d",
            "-s", session,
            "-x", str(width),
            "-y", str(height),
            "-c", str(cwd),
            "--",
            *command,
        )

    async def has_session(self, session: str) -> bool:
        """Whether the session exists on this server."""
        code, _, _ = await self._run(
            "has-session", "-t", f"={session}", allow_failure=True,
        )
        return code == 0

    async def kill_session(self, session: str) -> None:
        """Kill the session. Idempotent — a missing session is not an error."""
        await self._run("kill-session", "-t", f"={session}", allow_failure=True)

    async def list_sessions(self, prefix: str = "") -> list[str]:
        """List session names on this server, optionally prefix-filtered.

        Returns an empty list when the server is not running.
        """
        code, stdout, _ = await self._run(
            "list-sessions", "-F", "#{session_name}", allow_failure=True,
        )
        if code != 0:
            return []
        names = [line.strip() for line in stdout.splitlines() if line.strip()]
        if prefix:
            names = [n for n in names if n.startswith(prefix)]
        return names

    async def capture_screen(self, session: str) -> str:
        """Capture the session's rendered screen as plain text.

        ``capture-pane -p`` without ``-e`` renders to plain text — no ANSI
        escape sequences (spike-verified), so the result is directly
        pattern-matchable.
        """
        _, stdout, _ = await self._run(
            "capture-pane", "-p", "-t", f"={session}:",
        )
        return stdout

    async def pane_pid(self, session: str) -> int | None:
        """The pane process PID, or None when unavailable."""
        code, stdout, _ = await self._run(
            "list-panes", "-t", f"={session}:", "-F", "#{pane_pid}",
            allow_failure=True,
        )
        if code != 0:
            return None
        first = stdout.strip().splitlines()
        if not first:
            return None
        try:
            return int(first[0].strip())
        except ValueError:
            return None

    async def send_keys(self, session: str, keys: list[str]) -> None:
        """Send key names (tmux key syntax, e.g. 'Enter', 'Escape')."""
        if not keys:
            return
        await self._run("send-keys", "-t", f"={session}:", *keys)

    async def paste_text(self, session: str, text: str) -> None:
        """Deliver text to the session via a named buffer paste.

        Uses load-buffer from a private temp file + bracketed paste
        (``paste-buffer -p``), which delivers arbitrary multiline UTF-8
        without key-escaping issues or ARG_MAX limits. The buffer name is
        per-session so concurrent sheets never clobber each other; ``-d``
        deletes the buffer after pasting. Does NOT submit — callers send
        Enter separately.
        """
        buffer_name = f"mzt-{session}"
        fd, tmp_name = tempfile.mkstemp(prefix="mzt-prompt-", suffix=".txt")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(text.encode("utf-8"))
            await self._run("load-buffer", "-b", buffer_name, str(tmp_path))
            await self._run(
                "paste-buffer", "-p", "-d", "-b", buffer_name,
                "-t", f"={session}:",
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def pipe_pane_to_file(self, session: str, log_path: Path) -> None:
        """Stream the pane's raw output to ``log_path`` (append).

        The raw stream is a debug artifact (cursor-addressing soup from
        TUIs) — it is never machine-parsed. State detection uses
        :meth:`capture_screen` exclusively.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # pipe-pane runs the command via the tmux server's shell; quote the
        # path defensively (it is daemon-constructed, not user input).
        quoted = str(log_path).replace("'", "'\\''")
        await self._run(
            "pipe-pane", "-o", "-t", f"={session}:", f"cat >> '{quoted}'",
        )

    async def pipe_pane_off(self, session: str) -> None:
        """Stop streaming the pane's output (idempotent)."""
        await self._run("pipe-pane", "-t", f"={session}:", allow_failure=True)

    async def kill_server(self) -> None:
        """Kill the entire tmux server. TESTS ONLY.

        Never call from production paths — it destroys every concurrent
        interactive session on the socket.
        """
        await self._run("kill-server", allow_failure=True)
