"""Lifetime ownership for conductor Unix listeners.

A persistent adjacent lock serializes Marianne owners without blocking the event
loop. Foreign live listeners are preserved, and cleanup checks the exact inode
held from bind through shutdown. The parent directory must remain trusted: this
is not a sandbox against hostile directory or lock-file replacement.
"""

from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import socket
import stat
import uuid
from pathlib import Path
from typing import Any


class _PinnedSocketInode:
    """Prevent inode recycling with O_PATH or a temporary same-directory link."""

    def __init__(self, path: Path) -> None:
        self._fd: int | None = None
        self._anchor: Path | None = None
        if hasattr(os, "O_PATH"):
            self._fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
            self.info = os.fstat(self._fd)
        else:
            # Darwin has no O_PATH. POSIX hard links preserve the same inode
            # after unlink and work for socket files without opening them.
            # A random same-directory name avoids cross-filesystem failures.
            anchor = path.parent / f".mzt-inode-{uuid.uuid4().hex}"
            os.link(path, anchor, follow_symlinks=False)
            self._anchor = anchor
            self.info = anchor.lstat()

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._anchor is not None:
            try:
                current = self._anchor.lstat()
            except FileNotFoundError:
                pass
            else:
                if (current.st_dev, current.st_ino) == (self.info.st_dev, self.info.st_ino):
                    self._anchor.unlink()
            self._anchor = None


class OwnedUnixSocket:
    """Bind once under a nonblocking lifetime lock; release only our inode."""

    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.abspath(path))
        self._lock_fd: int | None = None
        self._inode_pin: _PinnedSocketInode | None = None
        self._identity: tuple[int, int] | None = None
        self._socket: socket.socket | None = None

    async def prepare(self) -> None:
        """Claim and bind before launching a dependent upstream process."""
        if self._lock_fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(
                    errno.EINVAL, "Socket ownership lock must be a regular file", str(lock_path)
                )
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = lock_path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise OSError(errno.EADDRINUSE, "Socket ownership lock changed", str(lock_path))
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd = fd
        try:
            await self._remove_stale_socket()
            bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket = bound
            bound.setblocking(False)
            bound.bind(str(self.path))
            # Socket FDs identify the kernel socket, not its filesystem inode.
            self._inode_pin = _PinnedSocketInode(self.path)
            info = self._inode_pin.info
            if not stat.S_ISSOCK(info.st_mode):
                raise OSError(
                    errno.EADDRINUSE, "Socket pathname changed while binding", str(self.path)
                )
            self._identity = (info.st_dev, info.st_ino)
        except BaseException:
            self.release()
            raise

    async def _remove_stale_socket(self) -> None:
        try:
            previous_pin = _PinnedSocketInode(self.path)
        except FileNotFoundError:
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.setblocking(False)
        try:
            # Keep the candidate inode pinned across the async probe. Without
            # this pin, an unlink/rebind can reuse the inode and fool equality.
            previous = previous_pin.info
            if not stat.S_ISSOCK(previous.st_mode):
                raise OSError(
                    errno.EEXIST, "Socket path is a symlink or non-socket file", str(self.path)
                )
            try:
                await asyncio.wait_for(
                    asyncio.get_running_loop().sock_connect(probe, str(self.path)), timeout=0.25
                )
            except OSError as exc:
                # A timeout, access failure or full backlog is not stale proof.
                if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                    raise OSError(
                        errno.EADDRINUSE,
                        "Socket listener cannot be safely reclaimed",
                        str(self.path),
                    ) from exc
            else:
                raise OSError(
                    errno.EADDRINUSE, "Socket already has a live listener", str(self.path)
                )
            try:
                current = self.path.lstat()
            except FileNotFoundError:
                return
            if (current.st_dev, current.st_ino) != (previous.st_dev, previous.st_ino):
                raise OSError(
                    errno.EADDRINUSE, "Socket pathname changed during stale check", str(self.path)
                )
            self.path.unlink()
        finally:
            probe.close()
            previous_pin.close()

    async def start(
        self, callback: Any, *, permissions: int | None = None, backlog: int = 100, **kwargs: Any
    ) -> asyncio.AbstractServer:
        """Start accepting using our bound socket, avoiding asyncio's unlink."""
        await self.prepare()
        try:
            assert self._socket is not None
            if permissions is not None:
                os.chmod(self.path, permissions)
            server = await asyncio.start_unix_server(
                callback, sock=self._socket, backlog=backlog, **kwargs
            )
            self._socket = None  # asyncio now owns and closes the socket FD.
            return server
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        """After closing the server, remove our pathname and release the lock."""
        try:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            if self._identity is not None:
                try:
                    current = self.path.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if (current.st_dev, current.st_ino) == self._identity:
                        self.path.unlink()
        finally:
            self._identity = None
            if self._inode_pin is not None:
                self._inode_pin.close()
                self._inode_pin = None
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
