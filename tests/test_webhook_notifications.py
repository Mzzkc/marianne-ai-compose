"""Tests for mozart.notifications.webhook module.

Covers WebhookNotifier: creation, env var expansion, from_config, payload
building, retry logic, send with success/failure, and MockWebhookNotifier.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from marianne.notifications.base import NotificationContext, NotificationEvent
from marianne.notifications.webhook import (
    MockWebhookNotifier,
    WebhookNotifier,
    _serialize_context,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def context() -> NotificationContext:
    """Sample notification context."""
    return NotificationContext(
        event=NotificationEvent.JOB_COMPLETE,
        job_id="test-123",
        job_name="test-job",
        timestamp=datetime(2025, 6, 15, 12, 0, 0),
        success_count=5,
        failure_count=1,
        duration_seconds=120.5,
    )


# ─── _serialize_context ──────────────────────────────────────────────


class TestSerializeContext:
    """Tests for _serialize_context helper."""

    def test_serializes_event_to_value(self, context: NotificationContext):
        data = _serialize_context(context)
        assert data["event"] == "job_complete"

    def test_serializes_timestamp_to_iso(self, context: NotificationContext):
        data = _serialize_context(context)
        assert isinstance(data["timestamp"], str)
        assert "2025-06-15" in data["timestamp"]

    def test_includes_all_fields(self, context: NotificationContext):
        data = _serialize_context(context)
        assert data["job_id"] == "test-123"
        assert data["success_count"] == 5


# ─── WebhookNotifier Init ─────────────────────────────────────────────


class TestWebhookNotifierInit:
    """Tests for WebhookNotifier construction."""

    def test_direct_url(self):
        n = WebhookNotifier(url="https://example.com/hook")
        assert n._url == "https://example.com/hook"

    def test_url_from_env(self):
        with patch.dict(os.environ, {"MY_WEBHOOK": "https://env.example.com/hook"}):
            n = WebhookNotifier(url_env="MY_WEBHOOK")
            assert n._url == "https://env.example.com/hook"

    def test_url_env_missing(self):
        n = WebhookNotifier(url_env="NONEXISTENT_VAR_12345")
        assert n._url == ""

    def test_direct_url_takes_precedence(self):
        with patch.dict(os.environ, {"MY_WEBHOOK": "https://env.com"}):
            n = WebhookNotifier(url="https://direct.com", url_env="MY_WEBHOOK")
            assert n._url == "https://direct.com"

    def test_default_events(self):
        n = WebhookNotifier(url="https://example.com")
        assert NotificationEvent.JOB_COMPLETE in n.subscribed_events
        assert NotificationEvent.JOB_FAILED in n.subscribed_events

    def test_custom_events(self):
        events = {NotificationEvent.SHEET_COMPLETE}
        n = WebhookNotifier(url="https://example.com", events=events)
        assert n.subscribed_events == events


# ─── Env Header Expansion ─────────────────────────────────────────────


class TestExpandEnvHeaders:
    """Tests for WebhookNotifier._expand_env_headers."""

    def test_no_env_vars(self):
        result = WebhookNotifier._expand_env_headers({"X-Key": "static"})
        assert result == {"X-Key": "static"}

    def test_expand_env_var(self):
        with patch.dict(os.environ, {"TOKEN": "secret123"}):
            result = WebhookNotifier._expand_env_headers(
                {"Authorization": "Bearer ${TOKEN}"}
            )
            assert result["Authorization"] == "Bearer secret123"

    def test_missing_env_var_empty_string(self):
        result = WebhookNotifier._expand_env_headers(
            {"Auth": "Bearer ${NONEXISTENT_12345}"}
        )
        assert result["Auth"] == "Bearer "

    def test_multiple_vars_in_one_header(self):
        with patch.dict(os.environ, {"USER": "admin", "HOST": "example.com"}):
            result = WebhookNotifier._expand_env_headers(
                {"X-Info": "${USER}@${HOST}"}
            )
            assert result["X-Info"] == "admin@example.com"


# ─── from_config ──────────────────────────────────────────────────────


class TestFromConfig:
    """Tests for WebhookNotifier.from_config()."""

    def test_basic_config(self):
        n = WebhookNotifier.from_config(
            on_events=["job_complete"],
            config={"url": "https://example.com/hook"},
        )
        assert n._url == "https://example.com/hook"
        assert NotificationEvent.JOB_COMPLETE in n.subscribed_events

    def test_unknown_event_ignored(self):
        n = WebhookNotifier.from_config(
            on_events=["job_complete", "invalid_event"],
            config={"url": "https://example.com"},
        )
        assert NotificationEvent.JOB_COMPLETE in n.subscribed_events
        # Only valid events registered
        assert len(n.subscribed_events) >= 1

    def test_empty_config(self):
        n = WebhookNotifier.from_config(on_events=["job_failed"])
        assert n._url is None

    def test_custom_timeout_and_retries(self):
        n = WebhookNotifier.from_config(
            on_events=["job_complete"],
            config={"url": "https://example.com", "timeout": 60, "max_retries": 5},
        )
        assert n._timeout == 60
        assert n._max_retries == 5


# ─── Payload Building ─────────────────────────────────────────────────


class TestBuildPayload:
    """Tests for WebhookNotifier._build_payload()."""

    def test_payload_structure(self, context: NotificationContext):
        n = WebhookNotifier(url="https://example.com")
        payload = n._build_payload(context)
        assert "event_type" in payload
        assert payload["event_type"] == "job_complete"
        assert "context" in payload

    def test_metadata_included_by_default(self, context: NotificationContext):
        n = WebhookNotifier(url="https://example.com")
        payload = n._build_payload(context)
        assert "metadata" in payload
        assert payload["metadata"]["source"] == "mozart-ai-compose"

    def test_metadata_excluded(self, context: NotificationContext):
        n = WebhookNotifier(url="https://example.com", include_metadata=False)
        payload = n._build_payload(context)
        assert "metadata" not in payload


# ─── Send ─────────────────────────────────────────────────────────────


class TestSend:
    """Tests for WebhookNotifier.send()."""

    @pytest.mark.asyncio
    async def test_no_url_returns_false(self, context: NotificationContext):
        n = WebhookNotifier()
        assert not await n.send(context)

    @pytest.mark.asyncio
    async def test_no_url_warns_once(self, context: NotificationContext):
        n = WebhookNotifier()
        await n.send(context)
        assert n._warned_no_url
        # Second call doesn't change state
        await n.send(context)
        assert n._warned_no_url

    @pytest.mark.asyncio
    async def test_unsubscribed_event_returns_true(self):
        n = WebhookNotifier(
            url="https://example.com",
            events={NotificationEvent.JOB_FAILED},
        )
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="test", job_name="test",
        )
        assert await n.send(ctx)

    @pytest.mark.asyncio
    async def test_successful_send(self, context: NotificationContext):
        n = WebhookNotifier(url="https://example.com/hook")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.is_success = True

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        n._client = mock_client

        assert await n.send(context)

    @pytest.mark.asyncio
    async def test_client_error_no_retry(self, context: NotificationContext):
        """4xx errors are not retried."""
        n = WebhookNotifier(url="https://example.com/hook", max_retries=2)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        n._client = mock_client

        result = await n.send(context)
        assert not result
        # Only called once (no retry for 4xx)
        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_retries(self, context: NotificationContext):
        """5xx errors are retried."""
        n = WebhookNotifier(url="https://example.com/hook", max_retries=1, retry_delay=0)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        n._client = mock_client

        result = await n.send(context)
        assert not result
        # Called twice: initial + 1 retry
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_retries(self, context: NotificationContext):
        """Timeout is retried."""
        n = WebhookNotifier(url="https://example.com/hook", max_retries=1, retry_delay=0)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        n._client = mock_client

        result = await n.send(context)
        assert not result

    @pytest.mark.asyncio
    async def test_close(self):
        n = WebhookNotifier(url="https://example.com")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        n._client = mock_client
        await n.close()
        mock_client.aclose.assert_called_once()
        assert n._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        n = WebhookNotifier(url="https://example.com")
        await n.close()  # Should not raise
        # Verify no exception raised by reaching this point
        assert n._client is None  # Client should remain None


# ─── MockWebhookNotifier ─────────────────────────────────────────────


class TestMockWebhookNotifier:
    """Tests for MockWebhookNotifier."""

    @pytest.mark.asyncio
    async def test_records_notifications(self, context: NotificationContext):
        mock = MockWebhookNotifier()
        await mock.send(context)
        assert mock.get_notification_count() == 1
        assert mock.sent_notifications[0].job_id == "test-123"

    @pytest.mark.asyncio
    async def test_fail_next(self, context: NotificationContext):
        mock = MockWebhookNotifier()
        mock.set_fail_next()
        assert not await mock.send(context)
        # Reset after one failure
        assert await mock.send(context)

    @pytest.mark.asyncio
    async def test_simulate_status_code(self, context: NotificationContext):
        mock = MockWebhookNotifier()
        mock.simulate_status_code(500)
        assert not await mock.send(context)
        # Reset after check
        assert await mock.send(context)

    @pytest.mark.asyncio
    async def test_get_notifications_for_event(self, context: NotificationContext):
        mock = MockWebhookNotifier(events={
            NotificationEvent.JOB_COMPLETE,
            NotificationEvent.JOB_FAILED,
        })
        await mock.send(context)
        complete = mock.get_notifications_for_event(NotificationEvent.JOB_COMPLETE)
        assert len(complete) == 1
        failed = mock.get_notifications_for_event(NotificationEvent.JOB_FAILED)
        assert len(failed) == 0

    @pytest.mark.asyncio
    async def test_close_clears(self, context: NotificationContext):
        mock = MockWebhookNotifier()
        await mock.send(context)
        await mock.close()
        assert mock.get_notification_count() == 0

    @pytest.mark.asyncio
    async def test_payloads_recorded(self, context: NotificationContext):
        mock = MockWebhookNotifier()
        await mock.send(context)
        assert len(mock.sent_payloads) == 1
        assert "event_type" in mock.sent_payloads[0]
