"""Per-dispatch socket paths for the memory gateway endpoint (BE1.1/1.2).

The path is ``<run_dir>/s/s_<hash16>.sock`` where ``hash16`` is the first 16
hex digits of a SHA-256 over job id, dispatch id and attachment id. The
``s`` directory is created ``0700`` and must belong to the calling user. The
whole path must fit in 70 bytes (the kernel's ``sun_path`` limit is 108);
a longer one is refused with a typed error naming the length, never
truncated. The caller supplies ``run_dir``; there is no default location.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from marianne.memory.errors import SchemaViolation

MAX_SOCKET_PATH_BYTES = 70
SOCKET_DIR_NAME = "s"


def socket_hash(job_id: str | None, dispatch_id: str, attachment_id: str) -> str:
    """16 hex digits, stable for one (job, dispatch, attachment)."""
    blob = "\x1f".join((job_id or "", dispatch_id, attachment_id)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def socket_path_for(
    run_dir: Path | str, job_id: str | None, dispatch_id: str, attachment_id: str,
) -> Path:
    """The per-dispatch socket path; creates ``<run_dir>/s`` as ``0700``."""
    if not dispatch_id or not attachment_id:
        raise SchemaViolation(
            "dispatch_id and attachment_id are required", subtype="socket_path_invalid",
        )
    sock_dir = Path(os.fspath(run_dir)) / SOCKET_DIR_NAME
    path = sock_dir / f"s_{socket_hash(job_id, dispatch_id, attachment_id)}.sock"
    length = len(os.fsencode(path))
    if length > MAX_SOCKET_PATH_BYTES:
        raise SchemaViolation(
            f"socket path is {length} bytes, the limit is {MAX_SOCKET_PATH_BYTES}",
            subtype="socket_path_too_long",
            remedy="use a shorter run directory",
        )
    sock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.stat(sock_dir)
    if st.st_uid != os.getuid():
        raise SchemaViolation(
            "the socket directory belongs to another user", subtype="socket_dir_not_owned",
        )
    os.chmod(sock_dir, 0o700)
    return path
