"""Tests for IPC client error paths — response parsing and streaming.

Covers the gaps in DaemonClient: malformed JSON responses, empty
responses, streaming notification parsing, and connection-close
handling. Tests use mocked asyncio streams (no running server needed).

GH#82 — IPC client error path tests at 0% coverage.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.daemon.exceptions import DaemonError, DaemonNotRunningError, JobSubmissionError
from marianne.daemon.ipc.client import DaemonClient
from marianne.daemon.ipc.errors import JOB_NOT_FOUND


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_line(obj: Any) -> bytes:
    """Encode a dict as a JSON line (bytes with trailing newline)."""
    return json.dumps(obj).encode() + b"\n"


def _make_reader(lines: list[bytes]) -> asyncio.StreamReader:
    """Build a StreamReader that yields the given byte lines."""
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line)
    reader.feed_eof()
    return reader


def _make_writer() -> MagicMock:
    """Build a mock StreamWriter with write/drain/close/wait_closed."""
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def _make_client(tmp_path: Path) -> DaemonClient:
    """Create a DaemonClient with a touched socket file."""
    sock = tmp_path / "test.sock"
    sock.touch()
    return DaemonClient(sock)


@contextmanager
def _mock_connection(client: DaemonClient, lines: list[bytes]):
    """Patch client._connect to return a mocked reader/writer pair.

    The reader is fed the given byte lines then EOF. The writer is a
    no-op mock. Yields nothing -- just provides the patched context.
    """
    reader = _make_reader(lines)
    writer = _make_writer()

    with patch.object(client, "_connect") as mock_connect:
        mock_connect.return_value.__aenter__ = AsyncMock(
            return_value=(reader, writer)
        )
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=False)
        yield


# ---------------------------------------------------------------------------
# Response parsing tests
# ---------------------------------------------------------------------------


class TestCallResponseParsing:
    """Test DaemonClient.call() response parsing edge cases."""

    @pytest.mark.asyncio
    async def test_successful_result(self, tmp_path: Path) -> None:
        """Normal success response returns the result dict."""
        client = _make_client(tmp_path)
        response = {"jsonrpc": "2.0", "result": {"status": "ok"}, "id": 1}

        with _mock_connection(client, [_json_line(response)]):
            result = await client.call("test.method")

        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_null_result(self, tmp_path: Path) -> None:
        """Response with null result returns None."""
        client = _make_client(tmp_path)
        response = {"jsonrpc": "2.0", "result": None, "id": 1}

        with _mock_connection(client, [_json_line(response)]):
            result = await client.call("test.method")

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_result_key(self, tmp_path: Path) -> None:
        """Response with neither 'error' nor 'result' returns None via .get()."""
        client = _make_client(tmp_path)
        response = {"jsonrpc": "2.0", "id": 1}

        with _mock_connection(client, [_json_line(response)]):
            result = await client.call("test.method")

        assert result is None

    @pytest.mark.asyncio
    async def test_error_response_raises_exception(self, tmp_path: Path) -> None:
        """Error response raises the mapped DaemonError subclass."""
        client = _make_client(tmp_path)
        response = {
            "jsonrpc": "2.0",
            "error": {"code": JOB_NOT_FOUND, "message": "no such job"},
            "id": 1,
        }

        with _mock_connection(client, [_json_line(response)]):
            with pytest.raises(JobSubmissionError, match="no such job"):
                await client.call("job.status")

    @pytest.mark.asyncio
    async def test_malformed_json_raises(self, tmp_path: Path) -> None:
        """Non-JSON response raises json.JSONDecodeError."""
        client = _make_client(tmp_path)

        with _mock_connection(client, [b"this is not json\n"]):
            with pytest.raises(json.JSONDecodeError):
                await client.call("test.method")

    @pytest.mark.asyncio
    async def test_empty_response_raises_not_running(self, tmp_path: Path) -> None:
        """Empty response (connection closed) raises DaemonNotRunningError."""
        client = _make_client(tmp_path)

        with _mock_connection(client, []):
            with pytest.raises(DaemonNotRunningError, match="closed connection"):
                await client.call("test.method")

    @pytest.mark.asyncio
    async def test_error_missing_code_uses_default(self, tmp_path: Path) -> None:
        """Error object missing 'code' uses INTERNAL_ERROR default."""
        client = _make_client(tmp_path)
        response = {
            "jsonrpc": "2.0",
            "error": {"message": "something went wrong"},
            "id": 1,
        }

        with _mock_connection(client, [_json_line(response)]):
            with pytest.raises(DaemonError, match="something went wrong"):
                await client.call("test.method")

    @pytest.mark.asyncio
    async def test_error_missing_message_uses_default(self, tmp_path: Path) -> None:
        """Error object missing 'message' uses 'Unknown error' default."""
        client = _make_client(tmp_path)
        response = {
            "jsonrpc": "2.0",
            "error": {"code": JOB_NOT_FOUND},
            "id": 1,
        }

        with _mock_connection(client, [_json_line(response)]):
            with pytest.raises(JobSubmissionError, match="Unknown error"):
                await client.call("test.method")


# ---------------------------------------------------------------------------
# Streaming response tests
# ---------------------------------------------------------------------------


class TestStreamResponseParsing:
    """Test DaemonClient.stream() notification and response parsing."""

    @pytest.mark.asyncio
    async def test_stream_yields_notifications(self, tmp_path: Path) -> None:
        """stream() yields notification params until final response."""
        client = _make_client(tmp_path)
        lines = [
            _json_line({"jsonrpc": "2.0", "method": "progress", "params": {"pct": 50}}),
            _json_line({"jsonrpc": "2.0", "method": "progress", "params": {"pct": 100}}),
            _json_line({"jsonrpc": "2.0", "result": "done", "id": 1}),
        ]

        with _mock_connection(client, lines):
            notifications = [n async for n in client.stream("test.stream")]

        assert len(notifications) == 2
        assert notifications[0] == {"pct": 50}
        assert notifications[1] == {"pct": 100}

    @pytest.mark.asyncio
    async def test_stream_notification_missing_params(self, tmp_path: Path) -> None:
        """Notification without 'params' key yields empty dict."""
        client = _make_client(tmp_path)
        lines = [
            _json_line({"jsonrpc": "2.0", "method": "heartbeat"}),
            _json_line({"jsonrpc": "2.0", "result": "ok", "id": 1}),
        ]

        with _mock_connection(client, lines):
            notifications = [n async for n in client.stream("test.stream")]

        assert notifications == [{}]

    @pytest.mark.asyncio
    async def test_stream_error_in_final_response(self, tmp_path: Path) -> None:
        """Error in the final response raises DaemonError."""
        client = _make_client(tmp_path)
        lines = [
            _json_line({"jsonrpc": "2.0", "method": "progress", "params": {"pct": 50}}),
            _json_line({
                "jsonrpc": "2.0",
                "error": {"code": JOB_NOT_FOUND, "message": "gone"},
                "id": 1,
            }),
        ]

        with _mock_connection(client, lines):
            with pytest.raises(JobSubmissionError, match="gone"):
                async for _ in client.stream("test.stream"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_connection_closes_early(self, tmp_path: Path) -> None:
        """Connection closing before final response ends stream gracefully."""
        client = _make_client(tmp_path)
        lines = [
            _json_line({"jsonrpc": "2.0", "method": "progress", "params": {"pct": 50}}),
        ]

        with _mock_connection(client, lines):
            notifications = [n async for n in client.stream("test.stream")]

        assert len(notifications) == 1

    @pytest.mark.asyncio
    async def test_stream_malformed_json_in_notification(
        self, tmp_path: Path,
    ) -> None:
        """Malformed JSON in a notification line raises JSONDecodeError."""
        client = _make_client(tmp_path)

        with _mock_connection(client, [b"not valid json\n"]):
            with pytest.raises(json.JSONDecodeError):
                async for _ in client.stream("test.stream"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_empty_yields_nothing(self, tmp_path: Path) -> None:
        """Empty stream (immediate EOF) yields no notifications."""
        client = _make_client(tmp_path)

        with _mock_connection(client, []):
            notifications = [n async for n in client.stream("test.stream")]

        assert notifications == []


# ---------------------------------------------------------------------------
# Request ID tests
# ---------------------------------------------------------------------------


class TestRequestIdGeneration:
    """Test that request IDs are monotonically increasing."""

    def test_ids_increment(self, tmp_path: Path) -> None:
        client = DaemonClient(tmp_path / "test.sock")
        id1 = client._next_request_id()
        id2 = client._next_request_id()
        id3 = client._next_request_id()
        assert id1 < id2 < id3

    def test_ids_start_from_one(self, tmp_path: Path) -> None:
        client = DaemonClient(tmp_path / "test.sock")
        assert client._next_request_id() == 1
