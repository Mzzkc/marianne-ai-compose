"""Tests for mozart.notifications module."""

import os
from datetime import datetime
from unittest.mock import patch

import pytest

from marianne.notifications import (
    DesktopNotifier,
    MockDesktopNotifier,
    MockSlackNotifier,
    MockWebhookNotifier,
    NotificationContext,
    NotificationEvent,
    NotificationManager,
    Notifier,
    SlackNotifier,
    WebhookNotifier,
    is_desktop_notification_available,
)


class TestNotificationEvent:
    """Tests for NotificationEvent enum."""

    def test_all_events_exist(self):
        """Test that all expected events are defined."""
        expected_events = [
            "JOB_START",
            "JOB_COMPLETE",
            "JOB_FAILED",
            "JOB_PAUSED",
            "JOB_RESUMED",
            "SHEET_START",
            "SHEET_COMPLETE",
            "SHEET_FAILED",
            "RATE_LIMIT_DETECTED",
        ]
        for event_name in expected_events:
            assert hasattr(NotificationEvent, event_name)

    def test_event_values(self):
        """Test event string values match expected format."""
        assert NotificationEvent.JOB_COMPLETE.value == "job_complete"
        assert NotificationEvent.SHEET_FAILED.value == "sheet_failed"
        assert NotificationEvent.RATE_LIMIT_DETECTED.value == "rate_limit_detected"


class TestNotificationContext:
    """Tests for NotificationContext dataclass."""

    def test_create_minimal_context(self):
        """Test creating context with minimal fields."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_START,
            job_id="test-123",
            job_name="test-job",
        )
        assert ctx.event == NotificationEvent.JOB_START
        assert ctx.job_id == "test-123"
        assert ctx.job_name == "test-job"
        assert isinstance(ctx.timestamp, datetime)

    def test_create_full_context(self):
        """Test creating context with all fields."""
        ctx = NotificationContext(
            event=NotificationEvent.SHEET_COMPLETE,
            job_id="test-123",
            job_name="test-job",
            sheet_num=5,
            total_sheets=10,
            success_count=8,
            failure_count=2,
            duration_seconds=120.5,
            extra={"custom": "data"},
        )
        assert ctx.sheet_num == 5
        assert ctx.total_sheets == 10
        assert ctx.success_count == 8
        assert ctx.failure_count == 2
        assert ctx.duration_seconds == 120.5
        assert ctx.extra["custom"] == "data"

    def test_format_title_job_complete(self):
        """Test title formatting for job complete."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="my-job",
        )
        title = ctx.format_title()
        assert "Mozart" in title
        assert "my-job" in title
        assert "Complete" in title

    def test_format_title_job_failed(self):
        """Test title formatting for job failed."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="failed-job",
        )
        title = ctx.format_title()
        assert "failed-job" in title
        assert "Failed" in title

    def test_format_title_sheet(self):
        """Test title formatting for sheet events."""
        ctx = NotificationContext(
            event=NotificationEvent.SHEET_COMPLETE,
            job_id="123",
            job_name="sheet-job",
            sheet_num=5,
        )
        title = ctx.format_title()
        assert "Sheet 5" in title

    def test_format_message_with_counts(self):
        """Test message formatting with success/failure counts."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
            success_count=10,
            failure_count=2,
        )
        msg = ctx.format_message()
        assert "10 passed" in msg
        assert "2 failed" in msg

    def test_format_message_with_duration_seconds(self):
        """Test message formatting with short duration."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
            duration_seconds=45.5,
        )
        msg = ctx.format_message()
        assert "45.5s" in msg

    def test_format_message_with_duration_minutes(self):
        """Test message formatting with minute-range duration."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
            duration_seconds=180.0,  # 3 minutes
        )
        msg = ctx.format_message()
        assert "3.0min" in msg

    def test_format_message_with_duration_hours(self):
        """Test message formatting with hour-range duration."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
            duration_seconds=7200.0,  # 2 hours
        )
        msg = ctx.format_message()
        assert "2.0h" in msg

    def test_format_message_with_error(self):
        """Test message formatting with error message."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test",
            error_message="Connection timeout",
        )
        msg = ctx.format_message()
        assert "Error" in msg
        assert "Connection timeout" in msg

    def test_format_message_truncates_long_error(self):
        """Test that long error messages are truncated."""
        long_error = "x" * 200
        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test",
            error_message=long_error,
        )
        msg = ctx.format_message()
        assert "..." in msg
        assert len(msg) < 200

    def test_format_message_sheet_progress(self):
        """Test message formatting with sheet progress."""
        ctx = NotificationContext(
            event=NotificationEvent.SHEET_COMPLETE,
            job_id="123",
            job_name="test",
            sheet_num=5,
            total_sheets=10,
        )
        msg = ctx.format_message()
        assert "5/10" in msg

    def test_format_message_minimal(self):
        """Test message formatting with minimal data."""
        ctx = NotificationContext(
            event=NotificationEvent.JOB_START,
            job_id="123",
            job_name="test",
        )
        msg = ctx.format_message()
        # Should return event value when no other data
        assert msg == "job_start"


class TestMockDesktopNotifier:
    """Tests for MockDesktopNotifier (testing utility)."""

    def test_subscribed_events(self):
        """Test default subscribed events."""
        notifier = MockDesktopNotifier()
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events

    def test_custom_events(self):
        """Test custom event subscription."""
        notifier = MockDesktopNotifier(
            events={NotificationEvent.SHEET_COMPLETE}
        )
        assert NotificationEvent.SHEET_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_COMPLETE not in notifier.subscribed_events

    @pytest.mark.asyncio
    async def test_send_records_notification(self):
        """Test that send() records notifications."""
        notifier = MockDesktopNotifier()
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is True
        assert notifier.get_notification_count() == 1
        assert notifier.sent_notifications[0] == ctx

    @pytest.mark.asyncio
    async def test_send_fail_next(self):
        """Test set_fail_next causes send to fail once."""
        notifier = MockDesktopNotifier()
        notifier.set_fail_next()

        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test",
        )

        # First send should fail
        result1 = await notifier.send(ctx)
        assert result1 is False
        assert notifier.get_notification_count() == 0

        # Second send should succeed
        result2 = await notifier.send(ctx)
        assert result2 is True
        assert notifier.get_notification_count() == 1

    @pytest.mark.asyncio
    async def test_get_notifications_for_event(self):
        """Test filtering notifications by event type."""
        notifier = MockDesktopNotifier(
            events={NotificationEvent.JOB_COMPLETE, NotificationEvent.JOB_FAILED}
        )

        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="1",
            job_name="test1",
        ))
        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="2",
            job_name="test2",
        ))
        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="3",
            job_name="test3",
        ))

        complete_notifs = notifier.get_notifications_for_event(
            NotificationEvent.JOB_COMPLETE
        )
        assert len(complete_notifs) == 2
        assert all(n.event == NotificationEvent.JOB_COMPLETE for n in complete_notifs)

    @pytest.mark.asyncio
    async def test_close_clears_notifications(self):
        """Test that close() clears recorded notifications."""
        notifier = MockDesktopNotifier()
        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="1",
            job_name="test",
        ))
        assert notifier.get_notification_count() == 1

        await notifier.close()
        assert notifier.get_notification_count() == 0


class TestDesktopNotifier:
    """Tests for DesktopNotifier."""

    def test_default_events(self):
        """Test default subscribed events."""
        notifier = DesktopNotifier()
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events
        assert NotificationEvent.JOB_PAUSED in notifier.subscribed_events

    def test_custom_events(self):
        """Test custom event subscription."""
        notifier = DesktopNotifier(
            events={NotificationEvent.SHEET_COMPLETE}
        )
        assert NotificationEvent.SHEET_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_COMPLETE not in notifier.subscribed_events

    def test_custom_app_name(self):
        """Test custom app name."""
        notifier = DesktopNotifier(app_name="MyApp")
        assert notifier._app_name == "MyApp"

    def test_from_config(self):
        """Test creating notifier from YAML config."""
        notifier = DesktopNotifier.from_config(
            on_events=["job_complete", "job_failed"],
            config={"app_name": "Test App", "timeout": 5},
        )
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events
        assert notifier._app_name == "Test App"
        assert notifier._timeout == 5

    def test_from_config_uppercase_events(self):
        """Test from_config handles uppercase event names."""
        notifier = DesktopNotifier.from_config(
            on_events=["JOB_COMPLETE", "SHEET_FAILED"],
        )
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.SHEET_FAILED in notifier.subscribed_events

    def test_from_config_unknown_event_warning(self):
        """Test from_config handles unknown events gracefully."""
        # Should not raise, just log warning
        notifier = DesktopNotifier.from_config(
            on_events=["unknown_event", "job_complete"],
        )
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events

    def test_is_desktop_notification_available(self):
        """Test availability check function."""
        # This just checks the function exists and returns bool
        result = is_desktop_notification_available()
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_close(self):
        """Test close method doesn't raise."""
        notifier = DesktopNotifier()
        await notifier.close()  # Should not raise


class TestNotificationManager:
    """Tests for NotificationManager."""

    def test_create_empty_manager(self):
        """Test creating manager without notifiers."""
        manager = NotificationManager()
        assert manager.notifier_count == 0

    def test_create_with_notifiers(self):
        """Test creating manager with notifiers."""
        mock = MockDesktopNotifier()
        manager = NotificationManager([mock])
        assert manager.notifier_count == 1

    def test_add_notifier(self):
        """Test adding a notifier."""
        manager = NotificationManager()
        mock = MockDesktopNotifier()
        manager.add_notifier(mock)
        assert manager.notifier_count == 1

    def test_remove_notifier(self):
        """Test removing a notifier."""
        mock = MockDesktopNotifier()
        manager = NotificationManager([mock])
        manager.remove_notifier(mock)
        assert manager.notifier_count == 0

    def test_remove_notifier_not_found(self):
        """Test removing non-existent notifier raises."""
        manager = NotificationManager()
        mock = MockDesktopNotifier()
        with pytest.raises(ValueError):
            manager.remove_notifier(mock)

    @pytest.mark.asyncio
    async def test_notify_sends_to_subscribed(self):
        """Test notify sends to subscribed notifiers."""
        mock = MockDesktopNotifier(
            events={NotificationEvent.JOB_COMPLETE}
        )
        manager = NotificationManager([mock])

        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )

        results = await manager.notify(ctx)
        assert results["MockDesktopNotifier"] is True
        assert mock.get_notification_count() == 1

    @pytest.mark.asyncio
    async def test_notify_skips_unsubscribed(self):
        """Test notify skips notifiers not subscribed to event."""
        mock = MockDesktopNotifier(
            events={NotificationEvent.JOB_FAILED}
        )
        manager = NotificationManager([mock])

        # Send a JOB_COMPLETE event - mock is only subscribed to JOB_FAILED
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )

        results = await manager.notify(ctx)
        assert len(results) == 0  # Not subscribed, not in results
        assert mock.get_notification_count() == 0

    @pytest.mark.asyncio
    async def test_notify_multiple_notifiers(self):
        """Test notify sends to multiple notifiers."""
        mock1 = MockDesktopNotifier(events={NotificationEvent.JOB_COMPLETE})
        mock2 = MockDesktopNotifier(events={NotificationEvent.JOB_COMPLETE})
        manager = NotificationManager([mock1, mock2])

        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )

        results = await manager.notify(ctx)
        # Both notifiers have same class name, so only last result appears
        assert "MockDesktopNotifier" in results
        # Both should have received
        assert mock1.get_notification_count() == 1
        assert mock2.get_notification_count() == 1

    @pytest.mark.asyncio
    async def test_notify_handles_failure(self):
        """Test notify handles notifier failures gracefully."""
        mock = MockDesktopNotifier(events={NotificationEvent.JOB_FAILED})
        mock.set_fail_next()
        manager = NotificationManager([mock])

        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test",
        )

        results = await manager.notify(ctx)
        assert results["MockDesktopNotifier"] is False

    @pytest.mark.asyncio
    async def test_notify_job_start(self):
        """Test convenience method for job start."""
        mock = MockDesktopNotifier(events={NotificationEvent.JOB_START})
        manager = NotificationManager([mock])

        await manager.notify_job_start(
            job_id="123",
            job_name="test-job",
            total_sheets=10,
        )

        assert mock.get_notification_count() == 1
        ctx = mock.sent_notifications[0]
        assert ctx.event == NotificationEvent.JOB_START
        assert ctx.total_sheets == 10

    @pytest.mark.asyncio
    async def test_notify_job_complete(self):
        """Test convenience method for job complete."""
        mock = MockDesktopNotifier(events={NotificationEvent.JOB_COMPLETE})
        manager = NotificationManager([mock])

        await manager.notify_job_complete(
            job_id="123",
            job_name="test-job",
            success_count=8,
            failure_count=2,
            duration_seconds=120.0,
        )

        assert mock.get_notification_count() == 1
        ctx = mock.sent_notifications[0]
        assert ctx.event == NotificationEvent.JOB_COMPLETE
        assert ctx.success_count == 8
        assert ctx.failure_count == 2
        assert ctx.duration_seconds == 120.0

    @pytest.mark.asyncio
    async def test_notify_job_failed(self):
        """Test convenience method for job failed."""
        mock = MockDesktopNotifier(events={NotificationEvent.JOB_FAILED})
        manager = NotificationManager([mock])

        await manager.notify_job_failed(
            job_id="123",
            job_name="test-job",
            error_message="Connection refused",
            sheet_num=5,
        )

        assert mock.get_notification_count() == 1
        ctx = mock.sent_notifications[0]
        assert ctx.event == NotificationEvent.JOB_FAILED
        assert ctx.error_message == "Connection refused"
        assert ctx.sheet_num == 5

    @pytest.mark.asyncio
    async def test_notify_sheet_complete(self):
        """Test convenience method for sheet complete."""
        mock = MockDesktopNotifier(events={NotificationEvent.SHEET_COMPLETE})
        manager = NotificationManager([mock])

        await manager.notify_sheet_complete(
            job_id="123",
            job_name="test-job",
            sheet_num=3,
            total_sheets=10,
            success_count=5,
            failure_count=0,
        )

        assert mock.get_notification_count() == 1
        ctx = mock.sent_notifications[0]
        assert ctx.event == NotificationEvent.SHEET_COMPLETE
        assert ctx.sheet_num == 3
        assert ctx.total_sheets == 10

    @pytest.mark.asyncio
    async def test_notify_rate_limit(self):
        """Test convenience method for rate limit."""
        mock = MockDesktopNotifier(events={NotificationEvent.RATE_LIMIT_DETECTED})
        manager = NotificationManager([mock])

        await manager.notify_rate_limit(
            job_id="123",
            job_name="test-job",
            sheet_num=7,
        )

        assert mock.get_notification_count() == 1
        ctx = mock.sent_notifications[0]
        assert ctx.event == NotificationEvent.RATE_LIMIT_DETECTED
        assert ctx.sheet_num == 7

    @pytest.mark.asyncio
    async def test_close(self):
        """Test close clears all notifiers."""
        mock = MockDesktopNotifier()
        await mock.send(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="1",
            job_name="test",
        ))

        manager = NotificationManager([mock])
        await manager.close()

        # Mock's close clears its notifications
        assert mock.get_notification_count() == 0


class TestNotifierProtocol:
    """Tests verifying Protocol compliance."""

    def test_mock_notifier_implements_protocol(self):
        """Test MockDesktopNotifier implements Notifier protocol."""
        mock = MockDesktopNotifier()
        # runtime_checkable allows isinstance check
        assert isinstance(mock, Notifier)

    def test_desktop_notifier_implements_protocol(self):
        """Test DesktopNotifier implements Notifier protocol."""
        notifier = DesktopNotifier()
        assert isinstance(notifier, Notifier)

    def test_slack_notifier_implements_protocol(self):
        """Test SlackNotifier implements Notifier protocol."""
        notifier = SlackNotifier(webhook_url="https://mock")
        assert hasattr(notifier, "subscribed_events")
        assert hasattr(notifier, "send")
        assert hasattr(notifier, "close")

    def test_webhook_notifier_implements_protocol(self):
        """Test WebhookNotifier implements Notifier protocol."""
        notifier = WebhookNotifier(url="https://mock")
        assert hasattr(notifier, "subscribed_events")
        assert hasattr(notifier, "send")
        assert hasattr(notifier, "close")


class TestSlackNotifier:
    """Tests for SlackNotifier."""

    def test_default_events(self):
        """Test default subscribed events."""
        notifier = SlackNotifier(webhook_url="https://mock")
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events
        assert NotificationEvent.SHEET_FAILED in notifier.subscribed_events

    def test_custom_events(self):
        """Test custom event subscription."""
        notifier = SlackNotifier(
            webhook_url="https://mock",
            events={NotificationEvent.SHEET_COMPLETE},
        )
        assert NotificationEvent.SHEET_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_COMPLETE not in notifier.subscribed_events

    def test_from_config(self):
        """Test creating notifier from YAML config."""
        notifier = SlackNotifier.from_config(
            on_events=["job_complete", "sheet_failed"],
            config={
                "webhook_url": "https://hooks.slack.com/test",
                "channel": "#alerts",
                "username": "Test Bot",
            },
        )
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.SHEET_FAILED in notifier.subscribed_events
        assert notifier._channel == "#alerts"
        assert notifier._username == "Test Bot"

    def test_from_config_env_var(self):
        """Test from_config uses environment variable for webhook."""
        with patch.dict(os.environ, {"MY_SLACK_WEBHOOK": "https://from-env"}):
            notifier = SlackNotifier.from_config(
                on_events=["job_complete"],
                config={"webhook_url_env": "MY_SLACK_WEBHOOK"},
            )
            assert notifier._webhook_url == "https://from-env"

    def test_from_config_unknown_event(self):
        """Test from_config handles unknown events gracefully."""
        notifier = SlackNotifier.from_config(
            on_events=["unknown_event", "job_complete"],
        )
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events

    def test_build_payload(self):
        """Test payload building with context."""
        notifier = SlackNotifier(webhook_url="https://mock", channel="#test")
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test-job",
            success_count=10,
            failure_count=2,
            duration_seconds=120.5,
        )
        payload = notifier._build_payload(ctx)

        assert "attachments" in payload
        assert payload["channel"] == "#test"
        assert len(payload["attachments"]) == 1
        attachment = payload["attachments"][0]
        assert "color" in attachment
        assert "title" in attachment

    def test_build_payload_with_error(self):
        """Test payload includes error message."""
        notifier = SlackNotifier(webhook_url="https://mock")
        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test-job",
            error_message="Connection refused",
        )
        payload = notifier._build_payload(ctx)

        # Find error field in attachment
        fields = payload["attachments"][0].get("fields", [])
        error_field = next((f for f in fields if f["title"] == "Error"), None)
        assert error_field is not None
        assert "Connection refused" in error_field["value"]

    @pytest.mark.asyncio
    async def test_send_no_webhook_returns_false(self):
        """Test send returns False when webhook URL not configured."""
        notifier = SlackNotifier()  # No webhook URL
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_unsubscribed_event_returns_true(self):
        """Test send returns True for unsubscribed events (no-op)."""
        notifier = SlackNotifier(
            webhook_url="https://mock",
            events={NotificationEvent.JOB_FAILED},
        )
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,  # Not subscribed
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_close(self):
        """Test close method cleans up client."""
        notifier = SlackNotifier(webhook_url="https://mock")
        # Create client first
        await notifier._get_client()
        assert notifier._client is not None

        await notifier.close()
        assert notifier._client is None


class TestMockSlackNotifier:
    """Tests for MockSlackNotifier."""

    def test_default_events(self):
        """Test default subscribed events."""
        notifier = MockSlackNotifier()
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events

    @pytest.mark.asyncio
    async def test_send_records_notification(self):
        """Test send records notifications."""
        notifier = MockSlackNotifier()
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is True
        assert notifier.get_notification_count() == 1
        assert len(notifier.sent_payloads) == 1

    @pytest.mark.asyncio
    async def test_send_fail_next(self):
        """Test set_fail_next causes failure."""
        notifier = MockSlackNotifier()
        notifier.set_fail_next()

        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_notifications_for_event(self):
        """Test filtering by event type."""
        notifier = MockSlackNotifier()
        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="1",
            job_name="test1",
        ))
        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="2",
            job_name="test2",
        ))

        complete = notifier.get_notifications_for_event(NotificationEvent.JOB_COMPLETE)
        assert len(complete) == 1


class TestWebhookNotifier:
    """Tests for WebhookNotifier."""

    def test_default_events(self):
        """Test default subscribed events."""
        notifier = WebhookNotifier(url="https://mock")
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events

    def test_custom_events(self):
        """Test custom event subscription."""
        notifier = WebhookNotifier(
            url="https://mock",
            events={NotificationEvent.SHEET_COMPLETE, NotificationEvent.RATE_LIMIT_DETECTED},
        )
        assert NotificationEvent.SHEET_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.RATE_LIMIT_DETECTED in notifier.subscribed_events
        assert NotificationEvent.JOB_COMPLETE not in notifier.subscribed_events

    def test_from_config(self):
        """Test creating notifier from YAML config."""
        notifier = WebhookNotifier.from_config(
            on_events=["job_complete", "job_failed"],
            config={
                "url": "https://example.com/webhook",
                "headers": {"X-API-Key": "secret"},
                "timeout": 15.0,
                "max_retries": 3,
            },
        )
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert notifier._url == "https://example.com/webhook"
        assert notifier._timeout == 15.0
        assert notifier._max_retries == 3

    def test_from_config_env_var(self):
        """Test from_config uses environment variable for URL."""
        with patch.dict(os.environ, {"MY_WEBHOOK_URL": "https://from-env"}):
            notifier = WebhookNotifier.from_config(
                on_events=["job_complete"],
                config={"url_env": "MY_WEBHOOK_URL"},
            )
            assert notifier._url == "https://from-env"

    def test_expand_env_headers(self):
        """Test environment variable expansion in headers."""
        with patch.dict(os.environ, {"API_TOKEN": "secret123"}):
            headers = WebhookNotifier._expand_env_headers({
                "Authorization": "Bearer ${API_TOKEN}",
                "X-Static": "value",
            })
            assert headers["Authorization"] == "Bearer secret123"
            assert headers["X-Static"] == "value"

    def test_build_payload(self):
        """Test payload building with context."""
        notifier = WebhookNotifier(url="https://mock")
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test-job",
            success_count=10,
            failure_count=0,
        )
        payload = notifier._build_payload(ctx)

        assert payload["event_type"] == "job_complete"
        assert "context" in payload
        assert payload["context"]["job_id"] == "123"
        assert payload["context"]["job_name"] == "test-job"
        assert "metadata" in payload
        assert payload["metadata"]["source"] == "mozart-ai-compose"

    def test_build_payload_without_metadata(self):
        """Test payload building without metadata."""
        notifier = WebhookNotifier(url="https://mock", include_metadata=False)
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test-job",
        )
        payload = notifier._build_payload(ctx)

        assert "metadata" not in payload

    @pytest.mark.asyncio
    async def test_send_no_url_returns_false(self):
        """Test send returns False when URL not configured."""
        notifier = WebhookNotifier()  # No URL
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_unsubscribed_event_returns_true(self):
        """Test send returns True for unsubscribed events (no-op)."""
        notifier = WebhookNotifier(
            url="https://mock",
            events={NotificationEvent.JOB_FAILED},
        )
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,  # Not subscribed
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is True

    @pytest.mark.asyncio
    async def test_close(self):
        """Test close method cleans up client."""
        notifier = WebhookNotifier(url="https://mock")
        # Create client first
        await notifier._get_client()
        assert notifier._client is not None

        await notifier.close()
        assert notifier._client is None


class TestMockWebhookNotifier:
    """Tests for MockWebhookNotifier."""

    def test_default_events(self):
        """Test default subscribed events."""
        notifier = MockWebhookNotifier()
        assert NotificationEvent.JOB_COMPLETE in notifier.subscribed_events
        assert NotificationEvent.JOB_FAILED in notifier.subscribed_events

    @pytest.mark.asyncio
    async def test_send_records_notification(self):
        """Test send records notifications."""
        notifier = MockWebhookNotifier()
        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is True
        assert notifier.get_notification_count() == 1
        assert len(notifier.sent_payloads) == 1

    @pytest.mark.asyncio
    async def test_send_fail_next(self):
        """Test set_fail_next causes failure."""
        notifier = MockWebhookNotifier()
        notifier.set_fail_next()

        ctx = NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_simulate_error_status(self):
        """Test simulating HTTP error status codes."""
        notifier = MockWebhookNotifier()
        notifier.simulate_status_code(500)

        ctx = NotificationContext(
            event=NotificationEvent.JOB_FAILED,
            job_id="123",
            job_name="test",
        )
        result = await notifier.send(ctx)
        assert result is False
        assert notifier.get_notification_count() == 0

    @pytest.mark.asyncio
    async def test_close_clears_data(self):
        """Test close clears recorded data."""
        notifier = MockWebhookNotifier()
        await notifier.send(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="1",
            job_name="test",
        ))
        assert notifier.get_notification_count() == 1

        await notifier.close()
        assert notifier.get_notification_count() == 0
        assert len(notifier.sent_payloads) == 0


class TestCLINotificationIntegration:
    """Tests for CLI notification integration."""

    def test_create_notifiers_from_config_desktop(self):
        """Test creating desktop notifier from config."""
        from marianne.cli import create_notifiers_from_config
        from marianne.core.config import NotificationConfig

        configs = [
            NotificationConfig(
                type="desktop",
                on_events=["job_complete", "job_failed"],
                config={"app_name": "Test"},
            )
        ]
        notifiers = create_notifiers_from_config(configs)
        assert len(notifiers) == 1
        assert isinstance(notifiers[0], DesktopNotifier)

    def test_create_notifiers_from_config_slack(self):
        """Test creating Slack notifier from config."""
        from marianne.cli import create_notifiers_from_config
        from marianne.core.config import NotificationConfig

        configs = [
            NotificationConfig(
                type="slack",
                on_events=["job_failed"],
                config={"webhook_url": "https://hooks.slack.com/test"},
            )
        ]
        notifiers = create_notifiers_from_config(configs)
        assert len(notifiers) == 1
        assert isinstance(notifiers[0], SlackNotifier)

    def test_create_notifiers_from_config_webhook(self):
        """Test creating webhook notifier from config."""
        from marianne.cli import create_notifiers_from_config
        from marianne.core.config import NotificationConfig

        configs = [
            NotificationConfig(
                type="webhook",
                on_events=["job_complete"],
                config={"url": "https://example.com/hook"},
            )
        ]
        notifiers = create_notifiers_from_config(configs)
        assert len(notifiers) == 1
        assert isinstance(notifiers[0], WebhookNotifier)

    def test_create_notifiers_from_config_multiple(self):
        """Test creating multiple notifiers from config."""
        from marianne.cli import create_notifiers_from_config
        from marianne.core.config import NotificationConfig

        configs = [
            NotificationConfig(type="desktop", on_events=["job_complete"]),
            NotificationConfig(
                type="slack",
                on_events=["job_failed"],
                config={"webhook_url": "https://slack"},
            ),
            NotificationConfig(
                type="webhook",
                on_events=["job_complete"],
                config={"url": "https://webhook"},
            ),
        ]
        notifiers = create_notifiers_from_config(configs)
        assert len(notifiers) == 3

    def test_create_notifiers_unknown_type_skipped(self):
        """Test unknown notification types are skipped."""
        from marianne.cli import create_notifiers_from_config
        from marianne.core.config import NotificationConfig

        configs = [
            NotificationConfig(
                type="email",  # Not implemented yet
                on_events=["job_complete"],
            )
        ]
        notifiers = create_notifiers_from_config(configs)
        assert len(notifiers) == 0
