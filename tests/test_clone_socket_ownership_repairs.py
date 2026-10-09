"""Real Unix socket regressions for clone and stdio bridge ownership."""

from __future__ import annotations

import asyncio
import json
import shlex
import socket
import sys
from pathlib import Path

import pytest

from marianne.daemon.clone import build_clone_config
from marianne.daemon.config import DaemonConfig, McpPoolConfig, McpServerEntry, SocketConfig
from marianne.daemon.mcp_socket_bridge import McpSocketBridge
from marianne.daemon.profiler.models import ProfilerConfig


@pytest.fixture
def upstream_command(tmp_path: Path) -> str:
    source = tmp_path / "upstream.py"
    source.write_text("""import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text("started")
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request.get("method") == "initialize":
        result = {"serverInfo": {"name": "owned-canary", "version": "1"}}
    else:
        result = {"canary": request.get("params", {})}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""")
    return shlex.join([sys.executable, str(source), str(tmp_path / "started")])


def bridge(path: Path, command: str) -> McpSocketBridge:
    return McpSocketBridge(name="canary", command=command, socket_path=path)


async def close_server(server: asyncio.AbstractServer) -> None:
    server.close()
    await server.wait_closed()


async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        data = await reader.readline()
        writer.write(data)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def canary(path: Path) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write(
            b'{"jsonrpc":"2.0","id":"caller-id","method":"tools/call","params":{"value":42}}\n'
        )
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_collision_refuses_before_launching_upstream(
    tmp_path: Path, upstream_command: str
) -> None:
    path = tmp_path / "mcp.sock"
    existing = await asyncio.start_unix_server(echo, path=str(path))
    original = path.stat().st_ino
    contender = bridge(path, upstream_command)
    try:
        with pytest.raises(OSError):
            await contender.start()
        assert path.stat().st_ino == original
        assert not (tmp_path / "started").exists()
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(b"existing-owner\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 2) == b"existing-owner\n"
        writer.close()
        await writer.wait_closed()
    finally:
        await contender.stop()
        await close_server(existing)


@pytest.mark.asyncio
async def test_bridge_lifetime_lock_refuses_same_path_after_external_unlink(
    tmp_path: Path, upstream_command: str
) -> None:
    path = tmp_path / "mcp.sock"
    owner, contender = bridge(path, upstream_command), bridge(path, upstream_command)
    await owner.start()
    try:
        path.unlink()
        with pytest.raises(OSError):
            await asyncio.wait_for(contender.start(), timeout=1)
    finally:
        await contender.stop()
        await owner.stop()


@pytest.mark.asyncio
async def test_old_stop_preserves_new_listener_at_same_path(
    tmp_path: Path, upstream_command: str
) -> None:
    path = tmp_path / "mcp.sock"
    owner = bridge(path, upstream_command)
    await owner.start()
    path.unlink()
    replacement = await asyncio.start_unix_server(echo, path=str(path))
    replacement_inode = path.stat().st_ino
    try:
        await owner.stop()
        assert path.stat().st_ino == replacement_inode
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(b"replacement\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 2) == b"replacement\n"
        writer.close()
        await writer.wait_closed()
    finally:
        await owner.stop()
        await close_server(replacement)


@pytest.mark.asyncio
async def test_never_started_bridge_stop_preserves_foreign_socket(
    tmp_path: Path, upstream_command: str
) -> None:
    path = tmp_path / "mcp.sock"
    existing = await asyncio.start_unix_server(echo, path=str(path))
    original = path.stat().st_ino
    try:
        await bridge(path, upstream_command).stop()
        assert path.stat().st_ino == original
    finally:
        await close_server(existing)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["symlink", "file"])
async def test_bridge_refuses_non_socket_paths(
    tmp_path: Path, upstream_command: str, kind: str
) -> None:
    path = tmp_path / "mcp.sock"
    target = tmp_path / "target"
    target.write_text("preserve")
    if kind == "symlink":
        path.symlink_to(target)
    else:
        path.write_text("preserve")
    contender = bridge(path, upstream_command)
    try:
        with pytest.raises(OSError):
            await contender.start()
        assert path.read_text() == "preserve"
        assert not (tmp_path / "started").exists()
    finally:
        await contender.stop()


@pytest.mark.asyncio
async def test_stale_socket_recovery_reaches_real_upstream(
    tmp_path: Path, upstream_command: str
) -> None:
    path = tmp_path / "mcp.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    owner = bridge(path, upstream_command)
    try:
        await owner.start()
        assert await canary(path) == {
            "jsonrpc": "2.0",
            "id": "caller-id",
            "result": {"canary": {"value": 42}},
        }
    finally:
        await owner.stop()
    assert not path.exists()


def test_clone_preserves_all_socket_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = DaemonConfig(socket=SocketConfig(permissions=0o600, backlog=37, enforce_peer_uid=False))
    clone = build_clone_config("alpha", base_config=base)
    expected = base.socket.model_dump(exclude={"path"})
    assert clone.socket.model_dump(exclude={"path"}) == expected


def test_clone_relocates_stdio_and_profiler_but_keeps_external_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    base = DaemonConfig(
        mcp_pool=McpPoolConfig(
            servers={
                "stdio": McpServerEntry(command="canary", socket="/tmp/global.sock"),
                "http": McpServerEntry(
                    command="external", transport="http", socket="https://example.invalid/mcp"
                ),
            }
        ),
        profiler=ProfilerConfig(
            storage_path=Path("/tmp/shared.db"),
            jsonl_path=Path("/tmp/shared.jsonl"),
            interval_seconds=9,
        ),
    )
    alpha = build_clone_config("alpha", base_config=base)
    beta = build_clone_config("beta", base_config=base)
    assert alpha.mcp_pool.servers["stdio"].socket != base.mcp_pool.servers["stdio"].socket
    assert (
        Path(alpha.mcp_pool.servers["stdio"].socket).parent
        != Path(beta.mcp_pool.servers["stdio"].socket).parent
    )
    assert alpha.mcp_pool.servers["http"] == base.mcp_pool.servers["http"]
    assert alpha.profiler.storage_path not in {
        base.profiler.storage_path,
        beta.profiler.storage_path,
    }
    assert alpha.profiler.jsonl_path not in {base.profiler.jsonl_path, beta.profiler.jsonl_path}
    assert alpha.profiler.interval_seconds == 9
    assert base.mcp_pool.servers["stdio"].socket == "/tmp/global.sock"


def test_default_clone_profiler_is_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    clone = build_clone_config(None)
    assert clone.profiler.storage_path != DaemonConfig().profiler.storage_path
    assert clone.profiler.jsonl_path != DaemonConfig().profiler.jsonl_path


@pytest.mark.asyncio
async def test_cancelled_start_reaps_upstream_and_releases_socket(
    tmp_path: Path, upstream_command: str
) -> None:
    hanging = tmp_path / "hang.py"
    hanging.write_text(
        'import sys, time\nprint("ready", file=sys.stderr, flush=True)\ntime.sleep(60)\n'
    )
    owner = bridge(tmp_path / "mcp.sock", shlex.join([sys.executable, str(hanging)]))
    task = asyncio.create_task(owner.start())
    try:
        for _ in range(100):
            if owner.process is not None:
                break
            await asyncio.sleep(0.01)
        process = owner.process
        assert process is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None
        assert owner.process is None
        assert not owner.socket_path.exists()
        replacement = bridge(owner.socket_path, upstream_command)
        try:
            await replacement.start()
            assert (await canary(owner.socket_path))["id"] == "caller-id"
        finally:
            await replacement.stop()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await owner.stop()


@pytest.mark.asyncio
async def test_stale_check_refuses_socket_replaced_during_probe(
    tmp_path: Path, upstream_command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mcp.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    loop = asyncio.get_running_loop()
    connect = loop.sock_connect
    replacement = None

    async def replace_before_refusal(probe: socket.socket, address: str) -> None:
        nonlocal replacement
        try:
            await connect(probe, address)
        except ConnectionRefusedError:
            path.unlink()
            replacement = await asyncio.start_unix_server(echo, path=str(path))
            raise

    monkeypatch.setattr(loop, "sock_connect", replace_before_refusal)
    contender = bridge(path, upstream_command)
    try:
        with pytest.raises(OSError):
            await contender.start()
        assert replacement is not None
        assert path.exists()
        assert not (tmp_path / "started").exists()
    finally:
        await contender.stop()
        if replacement is not None:
            await close_server(replacement)


@pytest.mark.asyncio
async def test_symlink_lock_refuses_before_launch(tmp_path: Path, upstream_command: str) -> None:
    path = tmp_path / "mcp.sock"
    target = tmp_path / "target"
    target.write_text("preserve")
    path.with_name(path.name + ".lock").symlink_to(target)
    contender = bridge(path, upstream_command)
    try:
        with pytest.raises(OSError):
            await contender.start()
        assert target.read_text() == "preserve"
        assert not (tmp_path / "started").exists()
    finally:
        await contender.stop()


def test_long_clone_mcp_path_keeps_unix_socket_headroom(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: Path("/home/emzi"))
    base = DaemonConfig(
        mcp_pool=McpPoolConfig(
            servers={
                "long-server-name": McpServerEntry(command="canary", socket="/tmp/shared.sock"),
            }
        )
    )
    clone = build_clone_config("a" * 64, base_config=base)
    assert len(clone.mcp_pool.servers["long-server-name"].socket.encode()) < 108


@pytest.mark.asyncio
async def test_socket_lifetime_lock_excludes_another_process(tmp_path: Path) -> None:
    from marianne.daemon.socket_ownership import OwnedUnixSocket

    path = tmp_path / "shared.sock"
    script = tmp_path / "listener.py"
    script.write_text(
        "import asyncio, sys\n"
        "from pathlib import Path\n"
        "from marianne.daemon.socket_ownership import OwnedUnixSocket\n"
        "async def main():\n"
        "    owner = OwnedUnixSocket(Path(sys.argv[1]))\n"
        "    server = await owner.start(lambda r, w: w.close())\n"
        "    print('ready', flush=True)\n"
        "    try:\n"
        "        await asyncio.to_thread(sys.stdin.readline)\n"
        "    finally:\n"
        "        server.close()\n"
        "        await server.wait_closed()\n"
        "        owner.release()\n"
        "asyncio.run(main())\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        str(path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    contender = OwnedUnixSocket(path)
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 2) == b"ready\n"
        path.unlink()
        with pytest.raises(OSError):
            await asyncio.wait_for(contender.start(echo), 1)
    finally:
        contender.release()
        process.stdin.write(b"stop\n")
        await process.stdin.drain()
        await asyncio.wait_for(process.wait(), 2)
    replacement = OwnedUnixSocket(path)
    server = await replacement.start(echo)
    await close_server(server)
    replacement.release()
    assert not path.exists()


@pytest.mark.asyncio
async def test_socket_without_o_path_recovers_stale_and_preserves_replacement(
    tmp_path: Path,
    upstream_command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.socket_ownership as ownership

    monkeypatch.delattr(ownership.os, "O_PATH", raising=False)
    path = tmp_path / "portable.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    owner = bridge(path, upstream_command)
    replacement = None
    try:
        await owner.start()
        assert (await canary(path))["id"] == "caller-id"
        path.unlink()
        replacement = await asyncio.start_unix_server(echo, path=str(path))
        replacement_inode = path.stat().st_ino
        await owner.stop()
        assert path.stat().st_ino == replacement_inode
        assert not list(tmp_path.glob(".mzt-inode-*"))
    finally:
        await owner.stop()
        if replacement is not None:
            await close_server(replacement)


@pytest.mark.asyncio
async def test_socket_without_o_path_pins_stale_across_replacement(
    tmp_path: Path,
    upstream_command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.socket_ownership as ownership

    monkeypatch.delattr(ownership.os, "O_PATH", raising=False)
    await test_stale_check_refuses_socket_replaced_during_probe(
        tmp_path,
        upstream_command,
        monkeypatch,
    )
    assert not list(tmp_path.glob(".mzt-inode-*"))


@pytest.fixture
def flooding_upstream_command(tmp_path: Path) -> str:
    source = tmp_path / "flooding-upstream.py"
    source.write_text("""import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    result = {"serverInfo": {"name": "flood-canary", "version": "1"}}
    if request.get("method") == "flood":
        result = {"payload": "x" * (8 * 1024 * 1024)}
    elif request.get("method") != "initialize":
        result = {"canary": request.get("params", {})}
    body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
    sys.stdout.write("Content-Length: " + str(len(body)) + "\\r\\n\\r\\n" + body)
    sys.stdout.flush()
""")
    return shlex.join([sys.executable, str(source)])


async def buffered_nonreading_client(owner: McpSocketBridge) -> asyncio.StreamWriter:
    _, writer = await asyncio.open_unix_connection(str(owner.socket_path))
    writer.write(b'{"jsonrpc":"2.0","id":1,"method":"flood"}\n')
    await writer.drain()
    try:
        async with asyncio.timeout(2):
            while not any(
                client.writer.transport.get_write_buffer_size() > 1_000_000
                for client in owner._clients
            ):
                await asyncio.sleep(0)
    except BaseException:
        writer.transport.abort()
        raise
    return writer


@pytest.mark.asyncio
async def test_stop_bounds_nonreading_client_and_reaps_upstream(
    tmp_path: Path,
    flooding_upstream_command: str,
) -> None:
    owner = bridge(tmp_path / "buffered.sock", flooding_upstream_command)
    await owner.start()
    process = owner.process
    writer = await buffered_nonreading_client(owner)
    stopping = asyncio.create_task(owner.stop())
    try:
        done, _ = await asyncio.wait({stopping}, timeout=0.5)
        assert done, "Bridge stop stalled while flushing output to a client that never reads"
        await stopping
        assert process.returncode is not None
        assert owner.process is None
        assert not owner.socket_path.exists()
    finally:
        writer.transport.abort()
        await asyncio.gather(stopping, return_exceptions=True)
        await owner.stop()


@pytest.mark.asyncio
async def test_cancelled_stop_reaps_upstream_and_allows_rebind_canary(
    tmp_path: Path,
    flooding_upstream_command: str,
    upstream_command: str,
) -> None:
    owner = bridge(tmp_path / "cancelled.sock", flooding_upstream_command)
    await owner.start()
    process = owner.process
    writer = await buffered_nonreading_client(owner)
    stopping = asyncio.create_task(owner.stop())
    try:
        await asyncio.sleep(0)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert process.returncode is not None, (
            "Cancelled stop left its owned upstream process alive"
        )
        assert owner.process is None
        assert not owner.socket_path.exists()
        replacement = bridge(owner.socket_path, upstream_command)
        try:
            await replacement.start()
            assert (await canary(replacement.socket_path))["id"] == "caller-id"
        finally:
            await replacement.stop()
    finally:
        writer.transport.abort()
        await asyncio.gather(stopping, return_exceptions=True)
        await owner.stop()


@pytest.mark.asyncio
async def test_nonreading_client_drain_does_not_block_other_upstream_responses(
    tmp_path: Path,
    flooding_upstream_command: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marianne.daemon.mcp_socket_bridge as bridge_module

    monkeypatch.setattr(bridge_module, "_WRITE_TIMEOUT_SECONDS", 0.03, raising=False)
    owner = bridge(tmp_path / "bounded-drain.sock", flooding_upstream_command)
    await owner.start()
    writer = await buffered_nonreading_client(owner)
    try:
        assert (await canary(owner.socket_path))["id"] == "caller-id"
    finally:
        writer.transport.abort()
        await owner.stop()
