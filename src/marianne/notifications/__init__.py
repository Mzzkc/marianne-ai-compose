"""Marianne notification framework.

Provides notification infrastructure for Marianne job events:
- Multiple notification backends (desktop, Slack, webhook)
- Event-based subscription model
- Graceful degradation when backends unavailable

Usage:
    from marianne.notifications import (
        NotificationEvent,
        NotificationContext,
        NotificationManager,
        DesktopNotifier,
        SlackNotifier,
        WebhookNotifier,
    )

    manager = NotificationManager([
        DesktopNotifier(events={NotificationEvent.JOB_COMPLETE}),
        SlackNotifier(webhook_url="...", events={NotificationEvent.JOB_FAILED}),
        WebhookNotifier(url="...", events={NotificationEvent.JOB_COMPLETE}),
    ])

    await manager.notify_job_complete(
        job_id="123",
        job_name="my-job",
        success_count=10,
        failure_count=0,
        duration_seconds=120.5,
    )
"""

from marianne.notifications.base import (
    NotificationContext,
    NotificationEvent,
    NotificationManager,
    Notifier,
)
from marianne.notifications.desktop import (
    DesktopNotifier,
    MockDesktopNotifier,
    is_desktop_notification_available,
)
from marianne.notifications.factory import create_notifiers_from_config
from marianne.notifications.slack import (
    MockSlackNotifier,
    SlackNotifier,
)
from marianne.notifications.webhook import (
    MockWebhookNotifier,
    WebhookNotifier,
)

__all__ = [
    # Base types
    "NotificationEvent",
    "NotificationContext",
    "Notifier",
    "NotificationManager",
    # Desktop implementation
    "DesktopNotifier",
    "MockDesktopNotifier",
    "is_desktop_notification_available",
    # Slack implementation
    "SlackNotifier",
    "MockSlackNotifier",
    # Webhook implementation
    "WebhookNotifier",
    "MockWebhookNotifier",
    # Factory
    "create_notifiers_from_config",
]
