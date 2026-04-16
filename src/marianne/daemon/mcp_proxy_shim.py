"""stdio-to-socket MCP proxy shim.

A lightweight bridge that forwards MCP JSON-RPC messages between a CLI
instrument's stdin/stdout and the conductor's shared MCP pool Unix socket.

The proxy is spawned as a subprocess by MCP-native CLI instruments (e.g.
claude-code via --mcp-config). It reads JSON-RPC from stdin, forwards to
the pool's Unix socket, reads responses from the socket, and writes them
to stdout.

Usage::

    python -m marianne.daemon.mcp_proxy_shim /tmp/mzt/mcp/github.sock

Or invoked automatically via MCP config JSON::

    {"mcpServers": {"github": {
        "command": "python3",
        "args": ["-m", "marianne.daemon.mcp_proxy_shim",
                 "/tmp/mzt/mcp/github.sock"]
    }}}

Design constraints:
- Minimal dependencies (stdlib only — no Pydantic, no Marianne imports)
- Async I/O throughout for non-blocking forwarding
- Graceful error handling (log to stderr, exit non-zero on failure)
- Bidirectional: stdin -> socket AND socket -> stdout run concurrently
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def _frame_response(body: bytes) -> bytes:
    """Wrap a JSON-RPC body in Content-Length framing (LSP-style).

    Args:
        body: Raw JSON-RPC response body bytes.

    Returns:
        Framed message with ``Content-Length: N\\r\\n\\r\\n`` header.
    """
    header = f"Content-Length: {len(body)}\r\n\r\n"
    return header.encode("utf-8") + body


async def _forward_loop(
    stdin_reader: asyncio.StreamReader,
    stdout_writer: asyncio.StreamWriter,
    socket_path: Path,
) -> None:
    """Combined bidirectional forwarder accepting pre-wrapped streams.

    Connects to the Unix socket at ``socket_path``, then concurrently
    forwards stdin→socket and socket→stdout. On connection failure,
    writes a JSON-RPC error response (code -32000) to ``stdout_writer``.

    This is the testable core of the proxy — callers provide their own
    stdin/stdout streams rather than wrapping sys.stdin/sys.stdout.

    Args:
        stdin_reader: StreamReader providing incoming JSON-RPC messages.
        stdout_writer: StreamWriter receiving forwarded JSON-RPC responses.
        socket_path: Path to the Unix domain socket.
    """
    try:
        sock_reader, sock_writer = await asyncio.open_unix_connection(
            str(socket_path),
        )
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        error_resp = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32000,
                "message": f"Cannot connect to MCP pool socket: {socket_path}",
            },
            "id": None,
        }
        stdout_writer.write(_frame_response(json.dumps(error_resp).encode("utf-8")))
        await stdout_writer.drain()
        return

    stdin_task = asyncio.create_task(
        forward_stdin_to_socket(stdin_reader, sock_writer),
    )
    stdout_task = asyncio.create_task(
        forward_socket_to_stdout(sock_reader, stdout_writer),
    )

    try:
        await asyncio.wait(
            {stdin_task, stdout_task},
            return_when=asyncio.ALL_COMPLETED,
        )
    finally:
        sock_writer.close()
        try:
            await sock_writer.wait_closed()
        except Exception:
            pass


async def forward_stdin_to_socket(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Forward data from stdin to the Unix socket.

    Reads chunks from stdin and writes them to the socket connection.
    Closes the socket write side when stdin is exhausted (EOF).

    Args:
        reader: asyncio StreamReader wrapping stdin.
        writer: asyncio StreamWriter connected to the Unix socket.
    """
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                if writer.can_write_eof():
                    writer.write_eof()
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as exc:
        print(f"mcp_proxy_shim: stdin->socket error: {exc}", file=sys.stderr)


async def forward_socket_to_stdout(
    reader: asyncio.StreamReader,
    stdout_writer: asyncio.StreamWriter,
) -> None:
    """Forward data from the Unix socket to stdout.

    Reads chunks from the socket connection and writes them to stdout.

    Args:
        reader: asyncio StreamReader connected to the Unix socket.
        stdout_writer: asyncio StreamWriter wrapping stdout.
    """
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            stdout_writer.write(data)
            await stdout_writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as exc:
        print(f"mcp_proxy_shim: socket->stdout error: {exc}", file=sys.stderr)


async def run_proxy(socket_path: str) -> int:
    """Run the stdio-to-socket proxy.

    Connects to the Unix socket at ``socket_path``, then concurrently
    forwards stdin->socket and socket->stdout until either side closes.

    Args:
        socket_path: Path to the Unix domain socket.

    Returns:
        Exit code: 0 on success, 1 on connection failure.
    """
    path = Path(socket_path)

    if not path.exists():
        print(
            f"mcp_proxy_shim: socket not found: {socket_path}",
            file=sys.stderr,
        )
        return 1

    try:
        sock_reader, sock_writer = await asyncio.open_unix_connection(
            str(path),
        )
    except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
        print(
            f"mcp_proxy_shim: cannot connect to {socket_path}: {exc}",
            file=sys.stderr,
        )
        return 1

    # Wrap stdin/stdout as async streams
    loop = asyncio.get_running_loop()
    stdin_reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(stdin_reader),
        sys.stdin.buffer,
    )

    stdout_transport, stdout_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin,
        sys.stdout.buffer,
    )
    stdout_writer = asyncio.StreamWriter(
        stdout_transport,
        stdout_protocol,
        None,
        loop,
    )

    # Run both directions concurrently
    stdin_task = asyncio.create_task(
        forward_stdin_to_socket(stdin_reader, sock_writer),
    )
    stdout_task = asyncio.create_task(
        forward_socket_to_stdout(sock_reader, stdout_writer),
    )

    try:
        done, _pending = await asyncio.wait(
            {stdin_task, stdout_task},
            return_when=asyncio.ALL_COMPLETED,
        )
    finally:
        sock_writer.close()
        try:
            await sock_writer.wait_closed()
        except Exception:
            pass

    for task in done:
        task_exc = task.exception()
        if task_exc is not None:
            print(
                f"mcp_proxy_shim: forwarding error: {task_exc}",
                file=sys.stderr,
            )
            return 1

    return 0


def main() -> None:
    """Entry point for the proxy shim."""
    if len(sys.argv) != 2:
        print(
            "Usage: python -m marianne.daemon.mcp_proxy_shim <socket_path>",
            file=sys.stderr,
        )
        sys.exit(2)

    exit_code = asyncio.run(run_proxy(sys.argv[1]))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
