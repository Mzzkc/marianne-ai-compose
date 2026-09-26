"""Best-effort Unix peer credential reading for the daemon IPC boundary.

Single shared primitive for ``SO_PEERCRED`` (Linux).  Two consumers:

- ``ipc/server.py`` — peer-UID admission gate (identity check at accept).
- ``daemon/process.py`` — control-plane audit metadata (cancel/submit provenance).

Platforms without ``SO_PEERCRED`` (e.g. macOS) return ``None``; callers must
decide their own fail-open/fail-closed policy.
"""

from __future__ import annotations

import socket
import struct
from asyncio import StreamWriter
from dataclasses import dataclass


@dataclass(frozen=True)
class PeerCredentials:
    """Kernel-reported credentials of the process at the other socket end."""

    pid: int
    uid: int
    gid: int


def read_peer_credentials(writer: StreamWriter) -> PeerCredentials | None:
    """Read ``SO_PEERCRED`` from the connection's socket.

    Returns ``None`` when the platform provides no ``SO_PEERCRED`` or the
    read fails — availability is platform-dependent, so absence is not
    itself evidence of a foreign peer.
    """
    sock = writer.get_extra_info("socket")
    option = getattr(socket, "SO_PEERCRED", None)
    if sock is None or option is None:
        return None

    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, TypeError, AttributeError, struct.error):
        return None

    return PeerCredentials(pid=pid, uid=uid, gid=gid)


__all__ = ["PeerCredentials", "read_peer_credentials"]
