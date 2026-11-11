"""The in-process memory gateway: sessions, grants, the six-tool surface, the audit log.

A library a conductor can host; it holds no socket and no daemon. It reaches
backends only through the ``memory-backend/1.1`` protocol.
"""

from marianne.memory.gateway.session import (
    TERMINAL,
    MemoryCaps,
    Session,
    SessionState,
    SessionTable,
)

__all__ = ["TERMINAL", "MemoryCaps", "Session", "SessionState", "SessionTable"]
