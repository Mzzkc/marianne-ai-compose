"""Per-dispatch socket paths for the memory gateway endpoint (BE1.1/1.2).

The path is ``<run_dir>/s/s_<hash16>.sock`` where ``hash16`` is the first 16
hex digits of a SHA-256 over job id, dispatch id and attachment id. The whole
path must fit in 70 bytes (the kernel's ``sun_path`` limit is 108); a longer
one is refused with a typed error naming the length, never truncated. The
caller supplies ``run_dir``; there is no default location.

Directory safety (security review)
----------------------------------
Path-based ``mkdir``/``stat``/``chmod`` follow symlinks and race. So:

* ``run_dir`` must already exist as a real directory (``lstat``, no symlink),
  owned by this UID and not writable by group or others; it is never created
  or repaired here.
* ``s`` is created with ``os.mkdir(mode=0o700)`` (only ``FileExistsError`` is
  tolerated), then opened ``O_RDONLY|O_DIRECTORY|O_NOFOLLOW``. Every check
  and the mode fix go through that descriptor (``fstat``, ``fchmod``). A
  symlink, a non-directory or a foreign owner is a typed refusal and is never
  repaired.
* The socket is bound *through the descriptor*, at
  ``/proc/self/fd/<dirfd>/<name>``, so the bind lands in the directory that
  was verified even if the path is swapped afterwards. (Chosen over
  re-verification alone because it removes the window instead of detecting
  it; it needs Linux ``/proc``.) After the bind the directory path is
  ``lstat``-ed and compared (st_dev, st_ino) with the held descriptor as a
  second check; on a mismatch the socket is unlinked through the descriptor
  and the bind is refused.
* A pre-existing entry at the socket name is unlinked (through the
  descriptor) only if ``lstat`` says it is a socket owned by this UID;
  anything else is refused untouched.
* Socket mode: the bind runs under ``umask(0o177)`` held under a module lock
  for the duration of the ``bind`` call only (umask is process-wide; other
  threads that create files in that instant get a stricter mode, never a
  looser one), then the mode is set to 0600 via ``chmod(dir_fd=)`` and
  re-checked with ``lstat``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import stat
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from marianne.memory.errors import SchemaViolation

MAX_SOCKET_PATH_BYTES = 70
SOCKET_DIR_NAME = "s"

_UMASK_LOCK = threading.Lock()


def socket_hash(job_id: str | None, dispatch_id: str, attachment_id: str) -> str:
    """16 hex digits, stable for one (job, dispatch, attachment)."""
    blob = "\x1f".join((job_id or "", dispatch_id, attachment_id)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _refuse(subtype: str, detail: str, remedy: str = "") -> SchemaViolation:
    return SchemaViolation(detail, subtype=subtype, remedy=remedy)


def _planned_path(
    run_dir: Path | str, job_id: str | None, dispatch_id: str, attachment_id: str,
) -> Path:
    if not dispatch_id or not attachment_id:
        raise _refuse("socket_path_invalid", "dispatch_id and attachment_id are required")
    path = (
        Path(os.fspath(run_dir)) / SOCKET_DIR_NAME
        / f"s_{socket_hash(job_id, dispatch_id, attachment_id)}.sock"
    )
    length = len(os.fsencode(path))
    if length > MAX_SOCKET_PATH_BYTES:
        raise _refuse(
            "socket_path_too_long",
            f"socket path is {length} bytes, the limit is {MAX_SOCKET_PATH_BYTES}",
            "use a shorter run directory",
        )
    return path


def _check_run_dir(run_dir: Path) -> None:
    try:
        st = os.lstat(run_dir)
    except OSError as exc:
        raise _refuse("run_dir_unsafe", f"run directory is not usable: {exc.strerror}") from None
    if not stat.S_ISDIR(st.st_mode):
        raise _refuse("run_dir_unsafe", "run directory is not a real directory (or is a symlink)")
    if st.st_uid != os.getuid():
        raise _refuse("run_dir_unsafe", "run directory belongs to another user")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _refuse(
            "run_dir_unsafe", "run directory is writable by group or others",
            "chmod go-w the run directory",
        )


def socket_path_for(
    run_dir: Path | str, job_id: str | None, dispatch_id: str, attachment_id: str,
) -> Path:
    """The per-dispatch socket path, with its directory verified (and created 0700)."""
    with prepare_socket_dir(run_dir, job_id, dispatch_id, attachment_id) as d:
        return d.socket_path


@dataclass
class SocketDir:
    """A verified ``<run_dir>/s`` held open by descriptor, plus the socket name inside it."""

    dir_path: Path
    name: str
    fd: int

    @property
    def socket_path(self) -> Path:
        return self.dir_path / self.name

    def _lstat_name(self) -> os.stat_result | None:
        try:
            return os.stat(self.name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def verify_same_directory(self) -> None:
        """Refuse unless the path still names the directory this descriptor holds."""
        held = os.fstat(self.fd)
        try:
            now = os.lstat(self.dir_path)
        except OSError:
            raise _refuse("socket_dir_swapped", "the socket directory vanished") from None
        if (
            not stat.S_ISDIR(now.st_mode)
            or (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino)
        ):
            raise _refuse(
                "socket_dir_swapped", "the socket directory was replaced while in use",
            )

    def clear_stale_socket(self) -> bool:
        """Unlink an existing socket of ours at the name; refuse any other entry."""
        st = self._lstat_name()
        if st is None:
            return False
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise _refuse(
                "socket_name_occupied",
                "an entry that is not a socket owned by this user sits at the socket name",
            )
        os.unlink(self.name, dir_fd=self.fd)
        return True

    def unlink_socket(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.name, dir_fd=self.fd)

    def bind_socket(self) -> socket.socket:
        """Bind a fresh AF_UNIX socket inside the verified directory; returns it (not listening)."""
        self.clear_stale_socket()
        via_fd = f"/proc/self/fd/{self.fd}/{self.name}"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.setblocking(False)
            with _UMASK_LOCK:
                old = os.umask(0o177)
                try:
                    sock.bind(via_fd)
                finally:
                    os.umask(old)
            os.chmod(self.name, 0o600, dir_fd=self.fd)
            st = self._lstat_name()
            if (
                st is None or not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
            ):
                raise _refuse(
                    "socket_not_private", "the bound socket is not an owned 0600 socket",
                )
            self.verify_same_directory()
        except BaseException:
            sock.close()
            self.unlink_socket()
            raise
        return sock

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@contextlib.contextmanager
def prepare_socket_dir(
    run_dir: Path | str, job_id: str | None, dispatch_id: str, attachment_id: str,
) -> Iterator[SocketDir]:
    """Verify ``run_dir``, create/open ``s`` safely and yield a :class:`SocketDir`."""
    planned = _planned_path(run_dir, job_id, dispatch_id, attachment_id)
    _check_run_dir(Path(os.fspath(run_dir)))
    sock_dir = planned.parent
    try:
        os.mkdir(sock_dir, 0o700)
    except FileExistsError:
        pass
    try:
        fd = os.open(sock_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise _refuse(
            "socket_dir_unsafe",
            "the socket directory is a symlink or not a directory",
            "remove it; it is never repaired automatically",
        ) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise _refuse("socket_dir_unsafe", "the socket directory is not ours")
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    handle = SocketDir(sock_dir, planned.name, fd)
    try:
        yield handle
    finally:
        handle.close()


__all__ = [
    "MAX_SOCKET_PATH_BYTES", "SocketDir", "prepare_socket_dir", "socket_hash", "socket_path_for",
]
