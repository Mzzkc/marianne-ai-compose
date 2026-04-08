"""Desktop notification implementation using plyer.

Provides cross-platform desktop notifications for Marianne job events.
Uses the plyer library for platform-independent notification support.

Phase 5 of Marianne implementation: Missing README features.
"""

from typing import Any

from marianne.core.logging import get_logger
from marianne.notifications.base import (
    NotificationContext,
    NotificationEvent,
)

# Module-level logger for desktop notifications
_logger = get_logger("notifications.desktop")


# Check if plyer is available at module level
# We use a function to avoid mypy no-redef issues with try/except imports
def _get_plyer_notification() -> tuple[bool, Any]:
    """Load plyer notification module if available."""
    try:
        from plyer import notification  # type: ignore[import-untyped,unused-ignore]

        return True, notification
    except ImportError:
        _logger.debug("plyer not installed - desktop notifications disabled")
        return False, None


_PLYER_AVAILABLE, _notification_module = _get_plyer_notification()


def is_desktop_notification_available() -> bool:
    """Check if desktop notifications are available.

    Returns:
        True if plyer is installed and can send notifications.
    """
    return _PLYER_AVAILABLE


class DesktopNotifier:
    """Desktop notification implementation using plyer.

    Provides cross-platform desktop notifications on Windows, macOS, and Linux.
    Gracefully degrades if plyer is not installed - logs warning but doesn't fail.

    Example usage:
        notifier = DesktopNotifier(
            events={NotificationEvent.JOB_COMPLETE, NotificationEvent.JOB_FAILED},
            app_name="Marianne",
        )
        await notifier.send(context)

    Configuration from YAML:
        notifications:
          - type: desktop
            on_events: [job_complete, job_failed]
            config:
              timeout: 10
              app_name: "Marianne AI"
    """

    def __init__(
        self,
        events: set[NotificationEvent] | None = None,
        app_name: str = "Marianne AI Compose",
        timeout: int = 10,
    ) -> None:
        """Initialize the desktop notifier.

        Args:
            events: Set of events to subscribe to. Defaults to job-level events.
            app_name: Application name shown in notifications.
            timeout: Notification display timeout in seconds (platform-dependent).
        """
        self._events = events or {
            NotificationEvent.JOB_COMPLETE,
            NotificationEvent.JOB_FAILED,
            NotificationEvent.JOB_PAUSED,
        }
        self._app_name = app_name
        self._timeout = timeout
        self._warned_unavailable = False

    @classmethod
    def from_config(
        cls,
        on_events: list[str],
        config: dict[str, Any] | None = None,
    ) -> "DesktopNotifier":
        """Create DesktopNotifier from YAML configuration.

        Args:
            on_events: List of event name strings from config.
            config: Optional dict with 'app_name' and 'timeout'.

        Returns:
            Configured DesktopNotifier instance.

        Example:
            notifier = DesktopNotifier.from_config(
                on_events=["job_complete", "job_failed"],
                config={"timeout": 5, "app_name": "My App"},
            )
        """
        config = config or {}

        # Convert string event names to NotificationEvent enums
        events: set[NotificationEvent] = set()
        for event_name in on_events:
            try:
                # Handle both "job_complete" and "JOB_COMPLETE" formats
                normalized = event_name.upper()
                events.add(NotificationEvent[normalized])
            except KeyError:
                _logger.warning("unknown_notification_event", event_name=event_name)

        return cls(
            events=events if events else None,
            app_name=config.get("app_name", "Marianne AI Compose"),
            timeout=config.get("timeout", 10),
        )

    @property
    def subscribed_events(self) -> set[NotificationEvent]:
        """Events this notifier is registered to receive.

        Returns:
            Set of subscribed NotificationEvent types.
        """
        return self._events

    async def send(self, context: NotificationContext) -> bool:
        """Send a desktop notification.

        Uses plyer for cross-platform notification support.
        Fails gracefully if plyer is unavailable or notification fails.

        Args:
            context: Notification context with event details.

        Returns:
            True if notification was sent, False if unavailable or failed.
        """
        if not _PLYER_AVAILABLE:
            if not self._warned_unavailable:
                _logger.warning(
                    "Desktop notifications unavailable - install plyer: "
                    "pip install plyer"
                )
                self._warned_unavailable = True
            return False

        if context.event not in self._events:
            # Not subscribed to this event
            return True

        title = context.format_title()
        message = context.format_message()

        try:
            # plyer.notification.notify is synchronous, but we wrap it
            # in the async interface for consistency
            _notification_module.notify(
                title=title,
                message=message,
                app_name=self._app_name,
                timeout=self._timeout,
            )
            _logger.debug("desktop_notification_sent", title=title)
            return True

        except Exception as e:
            # Various platform-specific errors can occur
            # (missing system notification service, etc.)
            _logger.warning("desktop_notification_failed", error=str(e))
            return False

    async def close(self) -> None:
        """Clean up resources.

        Desktop notifier has no resources to clean up,
        but implements the protocol method.
        """
        pass


class MockDesktopNotifier:
    """Mock desktop notifier for testing.

    Records all notifications sent without actually displaying them.
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
        }
        self.sent_notifications: list[NotificationContext] = []
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
        """Record notification without displaying.

        Args:
            context: Notification context.

        Returns:
            True unless set_fail_next was called.
        """
        if self._fail_next:
            self._fail_next = False
            return False

        self.sent_notifications.append(context)
        return True

    async def close(self) -> None:
        """Clear recorded notifications."""
        self.sent_notifications.clear()

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
