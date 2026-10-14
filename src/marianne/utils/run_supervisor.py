"""Linux run-action supervisor; orphaned action children reparent here."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


def subreaper_available() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "prctl"):
        return False
    enabled = ctypes.c_int()
    return bool(libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(enabled), 0, 0, 0) == 0)


def _write_state(path: Path, *, shell_pid: int, exit_code: int | None) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"shell_pid": shell_pid, "exit_code": exit_code}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    state = Path(sys.argv[1])
    command = sys.argv[2]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        return 125
    shell = subprocess.Popen(
        ["bash", "-c", command], stdin=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    _write_state(state, shell_pid=shell.pid, exit_code=None)
    exit_code = shell.wait()
    _write_state(state, shell_pid=shell.pid, exit_code=exit_code)
    # Reap adopted children. The parent inspects and signals live descendants;
    # the supervisor stays alive until they have actually left its subtree.
    while True:
        try:
            child_pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return 0
        if child_pid == 0:
            time.sleep(0.01)


if __name__ == "__main__":
    raise SystemExit(main())
