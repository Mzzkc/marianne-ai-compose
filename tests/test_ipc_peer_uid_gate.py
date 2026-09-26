"""IPC peer-identity admission gate (warden daemon-submission-20260926).

The conductor socket is a control-plane boundary: ``job.submit`` accepts an
arbitrary readable path and every other method (cancel, clear, shutdown) is
equally exposed.  File permissions (0o660) are the only gate today, and the
group bits can admit other users on shared-group systems.  These tests pin a
narrow property: **connections whose peer UID differs from the conductor's
effective UID are refused at accept time**, while every legitimate same-UID
client (this process *and* distinct child processes) keeps working.

They also pin submit provenance: ``job.submit`` stamps who (pid/uid) asked,
so a rogue same-UID submission is at least attributable after the fact.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from marianne.daemon.ipc.errors import IPC_PEER_DENIED
from marianne.daemon.ipc.handler import RequestHandler
from marianne.daemon.ipc.server import DaemonServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler() -> tuple[RequestHandler, list[dict[str, Any]]]:
    """Handler with an echo method plus a dispatch spy list."""
    handler = RequestHandler()
    dispatched: list[dict[str, Any]] = []

    async def _echo(params: dict[str, Any], _writer: Any) -> Any:
        dispatched.append(dict(params))
        return {"echo": params}

    handler.register("test.echo", _echo)
    return handler, dispatched


async def _send_request(
    socket_path: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Send one JSON-RPC request over a fresh connection; read one reply."""
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        return json.loads(line)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, OSError):
            # The server rejects before reading our request; closing with
            # unread inbound data makes the kernel reset the stream.  The
            # denial reply itself was already written and read above.
            pass


def _request(request_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "test.echo",
        "params": {"ping": request_id},
        "id": request_id,
    }


# A distinct *process* (own pid, same uid) that submits over the real socket.
_CHILD_SUBMIT_SCRIPT = """
import asyncio, json, sys

async def main() -> None:
    reader, writer = await asyncio.open_unix_connection(sys.argv[1])
    writer.write(json.dumps({
        "jsonrpc": "2.0",
        "method": "test.echo",
        "params": {"child": True},
        "id": 7,
    }).encode() + b"\\n")
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=5.0)
    sys.stdout.write(line.decode())
    writer.close()

asyncio.run(main())
"""


# ---------------------------------------------------------------------------
# Peer-UID admission gate
# ---------------------------------------------------------------------------


class TestPeerUidGate:
    """Foreign-UID peers are refused at accept; same-UID peers pass."""

    @pytest.mark.asyncio
    async def test_foreign_peer_uid_rejected_before_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A peer whose UID differs from the daemon's euid gets IPC_PEER_DENIED
        and nothing is dispatched; the server stays healthy afterwards."""
        sock = tmp_path / "test.sock"
        handler, dispatched = _make_handler()
        server = DaemonServer(sock, handler, enforce_peer_uid=True)
        await server.start()
        try:
            monkeypatch.setattr(
                server, "_read_peer_uid", lambda _writer: os.geteuid() + 1
            )
            resp = await _send_request(sock, _request(1))
            assert resp["error"]["code"] == IPC_PEER_DENIED
            assert dispatched == []

            # Recovery: an admitted (same-uid) peer still gets service.
            monkeypatch.setattr(server, "_read_peer_uid", lambda _writer: os.geteuid())
            resp = await _send_request(sock, _request(2))
            assert resp["result"] == {"echo": {"ping": 2}}
            assert dispatched == [{"ping": 2}]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_same_uid_peer_dispatched_normally(self, tmp_path: Path):
        """Ordinary same-UID client (real peercred, no patch) is admitted —
        the default CLI/dashboard/MCP path must not regress."""
        sock = tmp_path / "test.sock"
        handler, dispatched = _make_handler()
        server = DaemonServer(sock, handler, enforce_peer_uid=True)
        await server.start()
        try:
            resp = await _send_request(sock, _request(1))
            assert resp["result"] == {"echo": {"ping": 1}}
            assert dispatched == [{"ping": 1}]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_distinct_same_uid_process_admitted(self, tmp_path: Path):
        """A different pid with the same uid (real child process over the real
        socket) is admitted — the gate is identity-per-process, not pid-pinning
        and not a same-process-only allowlist."""
        sock = tmp_path / "test.sock"
        handler, dispatched = _make_handler()
        server = DaemonServer(sock, handler, enforce_peer_uid=True)
        await server.start()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _CHILD_SUBMIT_SCRIPT,
                str(sock),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            assert proc.pid != os.getpid()
            if proc.returncode != 0:
                pytest.fail(f"child submitter failed: {stderr.decode()}")
            resp = json.loads(stdout.decode())
            assert resp["result"] == {"echo": {"child": True}}
            assert dispatched == [{"child": True}]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_enforce_peer_uid_false_disables_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The explicit off-switch restores current behavior (compat knob)."""
        sock = tmp_path / "test.sock"
        handler, dispatched = _make_handler()
        server = DaemonServer(
            sock, handler, enforce_peer_uid=False,
        )
        await server.start()
        try:
            monkeypatch.setattr(
                server, "_read_peer_uid", lambda _writer: os.geteuid() + 1
            )
            resp = await _send_request(sock, _request(1))
            assert resp["result"] == {"echo": {"ping": 1}}
            assert dispatched == [{"ping": 1}]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_peercred_unavailable_admits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """On platforms where the peer uid cannot be read (None), the gate
        fails open — documented limitation, must not brick non-Linux."""
        sock = tmp_path / "test.sock"
        handler, dispatched = _make_handler()
        server = DaemonServer(sock, handler, enforce_peer_uid=True)
        await server.start()
        try:
            monkeypatch.setattr(server, "_read_peer_uid", lambda _writer: None)
            resp = await _send_request(sock, _request(1))
            assert resp["result"] == {"echo": {"ping": 1}}
            assert dispatched == [{"ping": 1}]
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# Error code contract
# ---------------------------------------------------------------------------


class TestIpcPeerDeniedCode:
    """The new extension code is mapped for clients too."""

    def test_code_value(self):
        assert IPC_PEER_DENIED == -32006

    def test_client_side_mapping(self):
        from marianne.daemon.ipc.errors import rpc_error_to_exception

        exc = rpc_error_to_exception(
            {"code": IPC_PEER_DENIED, "message": "denied"}
        )
        assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# Submit provenance
# ---------------------------------------------------------------------------


class TestSubmitProvenance:
    """job.submit leaves an attributable trace of the IPC peer."""

    @pytest.mark.asyncio
    async def test_ipc_peer_metadata_reads_real_peercred(self):
        """The shared primitive surfaces pid/uid/gid of the actual peer."""
        import socket as socket_mod

        from marianne.daemon.ipc.peercred import read_peer_credentials

        rsock, wsock = socket_mod.socketpair()
        rsock.setblocking(False)
        wsock.setblocking(False)
        loop = asyncio.get_event_loop()
        transport, _protocol = await loop.create_connection(
            lambda: asyncio.Protocol(), sock=wsock
        )
        reader = asyncio.StreamReader()
        reader_protocol = asyncio.StreamReaderProtocol(reader)
        writer = asyncio.StreamWriter(transport, reader_protocol, reader, loop)
        try:
            creds = read_peer_credentials(writer)
            assert creds is not None
            assert creds.pid == os.getpid()
            assert creds.uid == os.geteuid()
        finally:
            writer.close()
            rsock.close()

    @pytest.mark.asyncio
    async def test_job_submit_logs_peer_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The real job.submit handler stamps ipc_job_submitted with the
        peer's pid/uid — the 1% case (rogue same-uid submission) is at least
        attributable afterwards."""
        import socket as socket_mod
        from unittest.mock import AsyncMock, MagicMock

        from marianne.daemon import process as daemon_process
        from marianne.daemon.config import DaemonConfig
        from marianne.daemon.types import JobResponse

        class _SpyLogger:
            def __init__(self) -> None:
                self.events: list[tuple[str, dict[str, Any]]] = []

            def info(self, event: str, **kw: Any) -> None:
                self.events.append((event, kw))

            def warning(self, event: str, **kw: Any) -> None:
                self.events.append((event, kw))

            def debug(self, event: str, **kw: Any) -> None:
                pass

            def bind(self, **_ctx: Any) -> _SpyLogger:
                return self

        spy = _SpyLogger()
        monkeypatch.setattr(daemon_process, "_logger", spy)

        dp = daemon_process.DaemonProcess(DaemonConfig())
        handler = MagicMock()
        mgr = MagicMock()
        mgr.submit_job = AsyncMock(
            return_value=JobResponse(
                job_id="j1", status="accepted", message="ok"
            )
        )
        dp._register_methods(handler, mgr, health=None)
        handle_submit = None
        for call in handler.register.call_args_list:
            if call.args[0] == "job.submit":
                handle_submit = call.args[1]
                break
        assert handle_submit is not None

        rsock, wsock = socket_mod.socketpair()
        rsock.setblocking(False)
        wsock.setblocking(False)
        loop = asyncio.get_event_loop()
        transport, _protocol = await loop.create_connection(
            lambda: asyncio.Protocol(), sock=wsock
        )
        reader = asyncio.StreamReader()
        reader_protocol = asyncio.StreamReaderProtocol(reader)
        writer = asyncio.StreamWriter(transport, reader_protocol, reader, loop)
        try:
            result = await handle_submit(
                {"config_path": "/tmp/some-score.yaml"}, writer
            )
            assert result["status"] == "accepted"
            submitted = [
                kw for event, kw in spy.events if event == "ipc_job_submitted"
            ]
            assert submitted, "job.submit must log ipc_job_submitted"
            assert submitted[0]["client_pid"] == os.getpid()
            assert submitted[0]["client_uid"] == os.geteuid()
            assert submitted[0]["config_path"] == "/tmp/some-score.yaml"
        finally:
            writer.close()
            rsock.close()
