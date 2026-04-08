"""Slack notification implementation using httpx.

Provides Slack webhook notifications for Marianne job events.
Messages are formatted with rich Slack Block Kit formatting.

Phase 5 of Marianne implementation: Missing README features.
"""

import os
from typing import Any

import httpx

from marianne.core.logging import get_logger
from marianne.notifications.base import (
    NotificationContext,
    NotificationEvent,
)

_logger = get_logger("notifications.slack")


def _get_event_emoji(event: NotificationEvent) -> str:
    """Get Slack emoji for a notification event type."""
    emoji_map = {
        NotificationEvent.JOB_START: ":rocket:",
        NotificationEvent.JOB_COMPLETE: ":white_check_mark:",
        NotificationEvent.JOB_FAILED: ":x:",
        NotificationEvent.JOB_PAUSED: ":pause_button:",
        NotificationEvent.JOB_RESUMED: ":play_button:",
        NotificationEvent.SHEET_START: ":hourglass_flowing_sand:",
        NotificationEvent.SHEET_COMPLETE: ":heavy_check_mark:",
        NotificationEvent.SHEET_FAILED: ":warning:",
        NotificationEvent.RATE_LIMIT_DETECTED: ":snail:",
    }
    return emoji_map.get(event, ":bell:")


def _get_event_color(event: NotificationEvent) -> str:
    """Get Slack attachment color hex code for an event type."""
    color_map = {
        NotificationEvent.JOB_COMPLETE: "#36a64f",  # Green
        NotificationEvent.JOB_FAILED: "#d00000",  # Red
        NotificationEvent.SHEET_FAILED: "#ff9800",  # Orange
        NotificationEvent.RATE_LIMIT_DETECTED: "#ff9800",  # Orange
        NotificationEvent.JOB_PAUSED: "#ffcc00",  # Yellow
    }
    return color_map.get(event, "#439fe0")  # Default blue


class SlackNotifier:
    """Slack notification implementation using webhooks.

    Sends notifications to Slack channels via incoming webhooks.
    Supports rich formatting with Slack Block Kit attachments.

    Example usage:
        notifier = SlackNotifier(
            webhook_url="https://hooks.slack.com/services/...",
            channel="#alerts",
            events={NotificationEvent.JOB_COMPLETE, NotificationEvent.JOB_FAILED},
        )
        await notifier.send(context)

    Configuration from YAML:
        notifications:
          - type: slack
            on_events: [job_complete, job_failed, sheet_failed]
            config:
              webhook_url_env: SLACK_WEBHOOK_URL
              channel: "#marianne-alerts"
              username: "Marianne Bot"
              timeout: 10
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        webhook_url_env: str = "SLACK_WEBHOOK_URL",
        channel: str | None = None,
        username: str = "Marianne AI Compose",
        icon_emoji: str = ":musical_score:",
        events: set[NotificationEvent] | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Initialize the Slack notifier.

        Args:
            webhook_url: Direct webhook URL. If not provided, reads from env var.
            webhook_url_env: Environment variable containing webhook URL.
            channel: Override channel (optional, uses webhook default if not set).
            username: Bot username displayed in Slack.
            icon_emoji: Emoji for bot avatar.
            events: Set of events to subscribe to. Defaults to job-level events.
            timeout: HTTP request timeout in seconds.
        """
        # Get webhook URL from param or environment
        self._webhook_url = webhook_url or os.environ.get(webhook_url_env, "")
        self._channel = channel
        self._username = username
        self._icon_emoji = icon_emoji
        self._timeout = timeout

        self._events = events or {
            NotificationEvent.JOB_COMPLETE,
            NotificationEvent.JOB_FAILED,
            NotificationEvent.SHEET_FAILED,
        }

        self._client: httpx.AsyncClient | None = None
        self._warned_no_webhook = False

    @classmethod
    def from_config(
        cls,
        on_events: list[str],
        config: dict[str, Any] | None = None,
    ) -> "SlackNotifier":
        """Create SlackNotifier from YAML configuration.

        Args:
            on_events: List of event name strings from config.
            config: Optional dict with Slack-specific settings:
                - webhook_url: Direct webhook URL
                - webhook_url_env: Env var for webhook URL (default: SLACK_WEBHOOK_URL)
                - channel: Override channel
                - username: Bot username
                - icon_emoji: Bot avatar emoji
                - timeout: Request timeout in seconds

        Returns:
            Configured SlackNotifier instance.

        Example:
            notifier = SlackNotifier.from_config(
                on_events=["job_complete", "job_failed"],
                config={
                    "webhook_url_env": "MY_SLACK_WEBHOOK",
                    "channel": "#alerts",
                },
            )
        """
        config = config or {}

        # Convert string event names to NotificationEvent enums
        events: set[NotificationEvent] = set()
        for event_name in on_events:
            try:
                normalized = event_name.upper()
                events.add(NotificationEvent[normalized])
            except KeyError:
                _logger.warning("unknown_notification_event", event_name=event_name)

        return cls(
            webhook_url=config.get("webhook_url"),
            webhook_url_env=config.get("webhook_url_env", "SLACK_WEBHOOK_URL"),
            channel=config.get("channel"),
            username=config.get("username", "Marianne AI Compose"),
            icon_emoji=config.get("icon_emoji", ":musical_score:"),
            events=events if events else None,
            timeout=config.get("timeout", 10.0),
        )

    @property
    def subscribed_events(self) -> set[NotificationEvent]:
        """Events this notifier is registered to receive."""
        return self._events

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    def _build_payload(self, context: NotificationContext) -> dict[str, Any]:
        """Build Slack webhook payload with rich formatting.

        Args:
            context: Notification context with event details.

        Returns:
            Dict suitable for Slack webhook POST body.
        """
        emoji = _get_event_emoji(context.event)
        color = _get_event_color(context.event)
        title = context.format_title()
        message = context.format_message()

        # Build fields for attachment
        fields: list[dict[str, Any]] = []

        if context.sheet_num is not None and context.total_sheets is not None:
            fields.append({
                "title": "Progress",
                "value": f"Sheet {context.sheet_num}/{context.total_sheets}",
                "short": True,
            })

        if context.success_count is not None or context.failure_count is not None:
            success = context.success_count or 0
            failure = context.failure_count or 0
            fields.append({
                "title": "Results",
                "value": f"{success} passed, {failure} failed",
                "short": True,
            })

        if context.duration_seconds is not None:
            if context.duration_seconds < 60:
                duration_str = f"{context.duration_seconds:.1f}s"
            elif context.duration_seconds < 3600:
                duration_str = f"{context.duration_seconds / 60:.1f}min"
            else:
                duration_str = f"{context.duration_seconds / 3600:.1f}h"
            fields.append({
                "title": "Duration",
                "value": duration_str,
                "short": True,
            })

        if context.error_message:
            suffix = "..." if len(context.error_message) > 200 else ""
            error = context.error_message[:200] + suffix
            fields.append({
                "title": "Error",
                "value": f"```{error}```",
                "short": False,
            })

        # Build payload
        payload: dict[str, Any] = {
            "username": self._username,
            "icon_emoji": self._icon_emoji,
            "attachments": [
                {
                    "fallback": f"{title}: {message}",
                    "color": color,
                    "title": f"{emoji} {title}",
                    "text": message if not fields else None,
                    "fields": fields if fields else None,
                    "footer": f"Job: {context.job_name} ({context.job_id})",
                    "ts": int(context.timestamp.timestamp()),
                }
            ],
        }

        if self._channel:
            payload["channel"] = self._channel

        # Remove None values from attachment
        payload["attachments"][0] = {
            k: v for k, v in payload["attachments"][0].items() if v is not None
        }

        return payload

    async def send(self, context: NotificationContext) -> bool:
        """Send a Slack notification.

        Posts to the configured Slack webhook with rich formatting.
        Fails gracefully if webhook is unavailable or request fails.

        Args:
            context: Notification context with event details.

        Returns:
            True if notification was sent, False if unavailable or failed.
        """
        if not self._webhook_url:
            if not self._warned_no_webhook:
                _logger.warning(
                    "Slack webhook URL not configured. "
                    "Set webhook_url or SLACK_WEBHOOK_URL environment variable."
                )
                self._warned_no_webhook = True
            return False

        if context.event not in self._events:
            # Not subscribed to this event
            return True

        try:
            client = await self._get_client()
            payload = self._build_payload(context)

            response = await client.post(
                self._webhook_url,
                json=payload,
            )

            if response.status_code == 200:
                _logger.debug("slack_notification_sent", title=context.format_title())
                return True

            _logger.warning(
                "slack_webhook_error",
                status_code=response.status_code,
                body=response.text[:200],
            )
            return False

        except httpx.TimeoutException:
            _logger.warning("Slack notification timed out")
            return False
        except httpx.RequestError as e:
            _logger.warning("slack_notification_failed", error=str(e))
            return False
        except Exception as e:
            _logger.warning("slack_notification_unexpected_error", error=str(e), exc_info=True)
            return False

    async def close(self) -> None:
        """Clean up HTTP client resources.

        Called when the NotificationManager is shutting down.
        """
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


class MockSlackNotifier:
    """Mock Slack notifier for testing.

    Records all notifications sent without making HTTP calls.
    Useful for testing notification integration.
    """

    def __init__(
        self,
        events: set[NotificationEvent] | None = None,
    ) -> None:
        """Initialize mock notifier.

        Args:
            events: Set of events to subscribe to.
        """
        self._events = events or {
            NotificationEvent.JOB_COMPLETE,
            NotificationEvent.JOB_FAILED,
            NotificationEvent.SHEET_FAILED,
        }
        self.sent_notifications: list[NotificationContext] = []
        self.sent_payloads: list[dict[str, Any]] = []
        self._fail_next = False

    @property
    def subscribed_events(self) -> set[NotificationEvent]:
        """Events this notifier handles."""
        return self._events

    def set_fail_next(self, should_fail: bool = True) -> None:
        """Configure the next send() call to fail.

        Args:
            should_fail: If True, next send() returns False.
        """
        self._fail_next = should_fail

    async def send(self, context: NotificationContext) -> bool:
        """Record notification without making HTTP call.

        Args:
            context: Notification context.

        Returns:
            True unless set_fail_next was called.
        """
        if self._fail_next:
            self._fail_next = False
            return False

        self.sent_notifications.append(context)
        # Build payload like real notifier would
        notifier = SlackNotifier(webhook_url="https://mock")
        self.sent_payloads.append(notifier._build_payload(context))
        return True

    async def close(self) -> None:
        """Clear recorded notifications."""
        self.sent_notifications.clear()
        self.sent_payloads.clear()

    def get_notification_count(self) -> int:
        """Get number of recorded notifications."""
        return len(self.sent_notifications)

    def get_notifications_for_event(
        self, event: NotificationEvent
    ) -> list[NotificationContext]:
        """Get all notifications for a specific event type.

        Args:
            event: Event type to filter by.

        Returns:
            List of matching notification contexts.
        """
        return [n for n in self.sent_notifications if n.event == event]
