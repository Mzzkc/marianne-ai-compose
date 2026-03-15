"""Tests for mozart.notifications.slack module.

Covers SlackNotifier: creation, from_config, payload building (Block Kit
formatting), send with success/failure, close, and MockSlackNotifier.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mozart.notifications.base import NotificationContext, NotificationEvent
from mozart.notifications.slack import (
    MockSlackNotifier,
    SlackNotifier,
    _get_event_color,
    _get_event_emoji,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def context() -> NotificationContext:
    """Sample notification context for tests."""
    return NotificationContext(
        event=NotificationEvent.JOB_COMPLETE,
        job_id="test-123",
        job_name="test-job",
        timestamp=datetime(2025, 6, 15, 12, 0, 0),
        success_count=5,
        failure_count=1,
        duration_seconds=120.5,
    )


# ─── Helper Functions ─────────────────────────────────────────────────


class TestHelperFunctions:
    """Tests for _get_event_emoji and _get_event_color."""

    def test_job_complete_emoji(self):
        assert _get_event_emoji(NotificationEvent.JOB_COMPLETE) == ":white_check_mark:"

    def test_job_failed_emoji(self):
        assert _get_event_emoji(NotificationEvent.JOB_FAILED) == ":x:"

    def test_unknown_event_emoji(self):
        # All events should have an emoji — test a less common one
        assert _get_event_emoji(NotificationEvent.JOB_START) == ":rocket:"

    def test_job_complete_color(self):
        assert _get_event_color(NotificationEvent.JOB_COMPLETE) == "#36a64f"

    def test_job_failed_color(self):
        assert _get_event_color(NotificationEvent.JOB_FAILED) == "#d00000"

    def test_default_color_for_start(self):
        # JOB_START isn't in the color map → default blue
        assert _get_event_color(NotificationEvent.JOB_START) == "#439fe0"


# ─── SlackNotifier Init ───────────────────────────────────────────────


class TestSlackNotifierInit:
    """Tests for SlackNotifier construction."""

    def test_direct_url(self):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/services/test")
        assert n._webhook_url == "https://hooks.slack.com/services/test"

    def test_url_from_env(self):
        with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/env"}):
            n = SlackNotifier()
            assert n._webhook_url == "https://hooks.slack.com/env"

    def test_url_from_custom_env(self):
        with patch.dict(os.environ, {"MY_SLACK": "https://hooks.slack.com/custom"}):
            n = SlackNotifier(webhook_url_env="MY_SLACK")
            assert n._webhook_url == "https://hooks.slack.com/custom"

    def test_no_url_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            n = SlackNotifier(webhook_url_env="NONEXISTENT_VAR_12345")
            assert n._webhook_url == ""

    def test_default_events(self):
        n = SlackNotifier(webhook_url="https://test")
        assert NotificationEvent.JOB_COMPLETE in n.subscribed_events
        assert NotificationEvent.JOB_FAILED in n.subscribed_events
        assert NotificationEvent.SHEET_FAILED in n.subscribed_events

    def test_custom_events(self):
        events = {NotificationEvent.RATE_LIMIT_DETECTED}
        n = SlackNotifier(webhook_url="https://test", events=events)
        assert n.subscribed_events == events

    def test_custom_username_and_emoji(self):
        n = SlackNotifier(webhook_url="https://test", username="MyBot", icon_emoji=":robot:")
        assert n._username == "MyBot"
        assert n._icon_emoji == ":robot:"


# ─── from_config ──────────────────────────────────────────────────────


class TestFromConfig:
    """Tests for SlackNotifier.from_config()."""

    def test_basic_config(self):
        n = SlackNotifier.from_config(
            on_events=["job_complete", "job_failed"],
            config={"webhook_url": "https://hooks.slack.com/test"},
        )
        assert n._webhook_url == "https://hooks.slack.com/test"
        assert NotificationEvent.JOB_COMPLETE in n.subscribed_events
        assert NotificationEvent.JOB_FAILED in n.subscribed_events

    def test_unknown_event_ignored(self):
        n = SlackNotifier.from_config(
            on_events=["job_complete", "not_real"],
            config={"webhook_url": "https://test"},
        )
        assert NotificationEvent.JOB_COMPLETE in n.subscribed_events

    def test_empty_config_defaults(self):
        n = SlackNotifier.from_config(on_events=["job_failed"])
        assert n._username == "Mozart AI Compose"
        assert n._icon_emoji == ":musical_score:"

    def test_custom_options(self):
        n = SlackNotifier.from_config(
            on_events=["job_complete"],
            config={
                "webhook_url": "https://test",
                "channel": "#alerts",
                "username": "Custom",
                "timeout": 30,
            },
        )
        assert n._channel == "#alerts"
        assert n._username == "Custom"
        assert n._timeout == 30


# ─── Payload Building ─────────────────────────────────────────────────


class TestBuildPayload:
    """Tests for SlackNotifier._build_payload()."""

    @pytest.fixture
    def notifier(self) -> SlackNotifier:
        return SlackNotifier(webhook_url="https://test")

    def test_basic_payload_structure(self, notifier: SlackNotifier, context: NotificationContext):
        payload = notifier._build_payload(context)
        assert "username" in payload
        assert "icon_emoji" in payload
        assert "attachments" in payload
        assert len(payload["attachments"]) == 1

    def test_attachment_has_color(self, notifier: SlackNotifier, context: NotificationContext):
        payload = notifier._build_payload(context)
        attachment = payload["attachments"][0]
        assert "color" in attachment
        assert attachment["color"] == "#36a64f"  # green for JOB_COMPLETE

    def test_attachment_has_emoji_title(self, notifier: SlackNotifier, context: NotificationContext):
        payload = notifier._build_payload(context)
        attachment = payload["attachments"][0]
        assert ":white_check_mark:" in attachment["title"]

    def test_progress_field(self, notifier: SlackNotifier):
        ctx = NotificationContext(
            event=NotificationEvent.SHEET_COMPLETE,
            job_id="j1", job_name="test",
            sheet_num=3, total_sheets=10,
        )
        payload = notifier._build_payload(ctx)
        fields = payload["attachments"][0].get("fields", [])
        progress_field = next((f for f in fields if f["title"] == "Progress"), None)
        assert progress_field is not None
        assert "3/10" in progress_field["value"]

    def test_duration_formatting(self, notifier: SlackNotifier):
        # Under 1 minute
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="j", job_name="t", duration_seconds=30.5,
        )
        payload = notifier._build_payload(ctx)
        fields = payload["attachments"][0].get("fields", [])
        dur_field = next((f for f in fields if f["title"] == "Duration"), None)
        assert "30.5s" in dur_field["value"]

    def test_duration_minutes(self, notifier: SlackNotifier):
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="j", job_name="t", duration_seconds=300.0,
        )
        payload = notifier._build_payload(ctx)
        fields = payload["attachments"][0].get("fields", [])
        dur_field = next((f for f in fields if f["title"] == "Duration"), None)
        assert "min" in dur_field["value"]

    def test_duration_hours(self, notifier: SlackNotifier):
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="j", job_name="t", duration_seconds=7200.0,
        )
        payload = notifier._build_payload(ctx)
        fields = payload["attachments"][0].get("fields", [])
        dur_field = next((f for f in fields if f["title"] == "Duration"), None)
        assert "h" in dur_field["value"]

    def test_error_field_truncated(self, notifier: SlackNotifier):
        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="j", job_name="t",
            error_message="x" * 300,
        )
        payload = notifier._build_payload(ctx)
        fields = payload["attachments"][0].get("fields", [])
        err_field = next((f for f in fields if f["title"] == "Error"), None)
        assert err_field is not None
        assert "..." in err_field["value"]

    def test_channel_included(self):
        n = SlackNotifier(webhook_url="https://test", channel="#custom")
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="j", job_name="t",
        )
        payload = n._build_payload(ctx)
        assert payload.get("channel") == "#custom"

    def test_no_channel_by_default(self, notifier: SlackNotifier):
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="j", job_name="t",
        )
        payload = notifier._build_payload(ctx)
        assert "channel" not in payload

    def test_none_values_removed_from_attachment(self, notifier: SlackNotifier):
        ctx = NotificationContext(
            event=NotificationEvent.JOB_START,
            job_id="j", job_name="t",
        )
        payload = notifier._build_payload(ctx)
        attachment = payload["attachments"][0]
        assert all(v is not None for v in attachment.values())


# ─── Send ─────────────────────────────────────────────────────────────


class TestSend:
    """Tests for SlackNotifier.send()."""

    @pytest.mark.asyncio
    async def test_no_webhook_returns_false(self, context: NotificationContext):
        with patch.dict(os.environ, {}, clear=True):
            n = SlackNotifier(webhook_url_env="NONEXISTENT_VAR_12345")
            assert not await n.send(context)

    @pytest.mark.asyncio
    async def test_warns_once_on_missing_url(self, context: NotificationContext):
        with patch.dict(os.environ, {}, clear=True):
            n = SlackNotifier(webhook_url_env="NONEXISTENT_VAR_12345")
            await n.send(context)
            assert n._warned_no_webhook
            await n.send(context)
            assert n._warned_no_webhook

    @pytest.mark.asyncio
    async def test_unsubscribed_event_returns_true(self):
        n = SlackNotifier(
            webhook_url="https://test",
            events={NotificationEvent.JOB_FAILED},
        )
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="t", job_name="t",
        )
        assert await n.send(ctx)

    @pytest.mark.asyncio
    async def test_successful_send(self, context: NotificationContext):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        n._client = mock_client

        assert await n.send(context)

    @pytest.mark.asyncio
    async def test_webhook_error_status(self, context: NotificationContext):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = "invalid_payload"

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_response)
        n._client = mock_client

        assert not await n.send(context)

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, context: NotificationContext):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        n._client = mock_client

        assert not await n.send(context)

    @pytest.mark.asyncio
    async def test_request_error_returns_false(self, context: NotificationContext):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=httpx.RequestError("conn failed"))
        n._client = mock_client

        assert not await n.send(context)

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_false(self, context: NotificationContext):
        n = SlackNotifier(webhook_url="https://hooks.slack.com/test")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))
        n._client = mock_client

        assert not await n.send(context)

    @pytest.mark.asyncio
    async def test_close(self):
        n = SlackNotifier(webhook_url="https://test")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        n._client = mock_client
        await n.close()
        mock_client.aclose.assert_called_once()
        assert n._client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        n = SlackNotifier(webhook_url="https://test")
        await n.close()  # Should not raise


# ─── MockSlackNotifier ─────────────────────────────────────────────────


class TestMockSlackNotifier:
    """Tests for MockSlackNotifier."""

    @pytest.mark.asyncio
    async def test_records_notifications(self, context: NotificationContext):
        mock = MockSlackNotifier()
        await mock.send(context)
        assert mock.get_notification_count() == 1
        assert mock.sent_notifications[0].job_id == "test-123"

    @pytest.mark.asyncio
    async def test_fail_next(self, context: NotificationContext):
        mock = MockSlackNotifier()
        mock.set_fail_next()
        assert not await mock.send(context)
        assert await mock.send(context)

    @pytest.mark.asyncio
    async def test_filter_by_event(self, context: NotificationContext):
        mock = MockSlackNotifier()
        await mock.send(context)
        complete = mock.get_notifications_for_event(NotificationEvent.JOB_COMPLETE)
        assert len(complete) == 1
        failed = mock.get_notifications_for_event(NotificationEvent.JOB_FAILED)
        assert len(failed) == 0

    @pytest.mark.asyncio
    async def test_close_clears(self, context: NotificationContext):
        mock = MockSlackNotifier()
        await mock.send(context)
        await mock.close()
        assert mock.get_notification_count() == 0

    @pytest.mark.asyncio
    async def test_payloads_recorded(self, context: NotificationContext):
        mock = MockSlackNotifier()
        await mock.send(context)
        assert len(mock.sent_payloads) == 1
        assert "attachments" in mock.sent_payloads[0]
