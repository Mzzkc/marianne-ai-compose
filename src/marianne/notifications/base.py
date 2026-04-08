"""Notification framework base types and protocols.

Provides the core notification infrastructure for Marianne:
- NotificationEvent enum for event types
- Notifier protocol for notification backends
- NotificationManager for coordinating multiple notifiers

Phase 5 of Marianne implementation: Missing README features.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from marianne.core.logging import get_logger
from marianne.utils.time import utc_now

# Module-level logger
_logger = get_logger("notifications")


class NotificationEvent(Enum):
    """Events that can trigger notifications.

    These events align with the lifecycle of Marianne job execution
    and are referenced in NotificationConfig.on_events.
    """

    # Job-level events
    JOB_START = "job_start"
    JOB_COMPLETE = "job_complete"
    JOB_FAILED = "job_failed"
    JOB_PAUSED = "job_paused"
    JOB_RESUMED = "job_resumed"

    # Sheet-level events
    SHEET_START = "sheet_start"
    SHEET_COMPLETE = "sheet_complete"
    SHEET_FAILED = "sheet_failed"

    # Special events
    RATE_LIMIT_DETECTED = "rate_limit_detected"


@dataclass
class NotificationContext:
    """Context provided to notifiers when sending notifications.

    Contains all relevant information about the event that triggered
    the notification, enabling rich notification messages.
    """

    event: NotificationEvent
    """The event type that triggered this notification."""

    job_id: str
    """Unique identifier for the job."""

    job_name: str
    """Human-readable job name."""

    timestamp: datetime = field(default_factory=utc_now)
    """When the event occurred."""

    # Optional fields populated based on event type
    sheet_num: int | None = None
    """Sheet number (for sheet-level events)."""

    total_sheets: int | None = None
    """Total number of sheets in the job."""

    success_count: int | None = None
    """Number of successful validations/sheets."""

    failure_count: int | None = None
    """Number of failed validations/sheets."""

    error_message: str | None = None
    """Error message (for failure events)."""

    duration_seconds: float | None = None
    """Duration of the operation in seconds."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Additional context-specific data."""

    def format_title(self) -> str:
        """Generate a notification title based on the event.

        Returns:
            A concise title string suitable for notification headers.
        """
        event_titles = {
            NotificationEvent.JOB_START: f"Marianne: Job '{self.job_name}' Started",
            NotificationEvent.JOB_COMPLETE: f"Marianne: Job '{self.job_name}' Complete ✓",
            NotificationEvent.JOB_FAILED: f"Marianne: Job '{self.job_name}' Failed ✗",
            NotificationEvent.JOB_PAUSED: f"Marianne: Job '{self.job_name}' Paused",
            NotificationEvent.JOB_RESUMED: f"Marianne: Job '{self.job_name}' Resumed",
            NotificationEvent.SHEET_START: f"Marianne: Sheet {self.sheet_num} Started",
            NotificationEvent.SHEET_COMPLETE: f"Marianne: Sheet {self.sheet_num} Complete",
            NotificationEvent.SHEET_FAILED: f"Marianne: Sheet {self.sheet_num} Failed",
            NotificationEvent.RATE_LIMIT_DETECTED: "Marianne: Rate Limit Detected",
        }
        return event_titles.get(self.event, f"Marianne: {self.event.value}")

    def format_message(self) -> str:
        """Generate a notification message body based on context.

        Returns:
            A descriptive message string with relevant details.
        """
        parts: list[str] = []

        if self.sheet_num is not None and self.total_sheets is not None:
            parts.append(f"Sheet {self.sheet_num}/{self.total_sheets}")

        if self.success_count is not None or self.failure_count is not None:
            success = self.success_count or 0
            failure = self.failure_count or 0
            parts.append(f"{success} passed, {failure} failed")

        if self.duration_seconds is not None:
            if self.duration_seconds < 60:
                parts.append(f"{self.duration_seconds:.1f}s")
            elif self.duration_seconds < 3600:
                mins = self.duration_seconds / 60
                parts.append(f"{mins:.1f}min")
            else:
                hours = self.duration_seconds / 3600
                parts.append(f"{hours:.1f}h")

        if self.error_message:
            # Truncate long error messages for notifications
            error = self.error_message[:100]
            if len(self.error_message) > 100:
                error += "..."
            parts.append(f"Error: {error}")

        return " | ".join(parts) if parts else self.event.value


@runtime_checkable
class Notifier(Protocol):
    """Protocol for notification backends.

    Implementations handle sending notifications through specific channels
    (desktop, Slack, webhook, etc.). Each notifier:
    - Registers for specific event types
    - Receives NotificationContext when events occur
    - Handles delivery asynchronously

    Following Marianne's Protocol pattern (like OutcomeStore, EscalationHandler).
    """

    @property
    def subscribed_events(self) -> set[NotificationEvent]:
        """Events this notifier is registered to receive.

        Returns:
            Set of NotificationEvent types this notifier handles.
        """
        ...

    async def send(self, context: NotificationContext) -> bool:
        """Send a notification for the given context.

        Args:
            context: Full notification context with event details.

        Returns:
            True if notification was sent successfully, False otherwise.
            Failures should be logged but not raise exceptions.
        """
        ...

    async def close(self) -> None:
        """Clean up any resources held by the notifier.

        Called when the NotificationManager is shutting down.
        Implementations should release connections, close files, etc.
        """
        ...


class NotificationManager:
    """Coordinates multiple notifiers for Marianne job events.

    Central hub for notification delivery:
    - Maintains list of active notifiers
    - Routes events to appropriate notifiers based on subscriptions
    - Handles failures gracefully (log but don't interrupt execution)

    Example usage:
        manager = NotificationManager([
            DesktopNotifier(events={NotificationEvent.JOB_COMPLETE}),
            SlackNotifier(webhook_url=..., events={NotificationEvent.JOB_FAILED}),
        ])

        await manager.notify(NotificationContext(
            event=NotificationEvent.JOB_COMPLETE,
            job_id="123",
            job_name="my-job",
        ))
    """

    def __init__(self, notifiers: list[Notifier] | None = None) -> None:
        """Initialize the notification manager.

        Args:
            notifiers: List of Notifier implementations to use.
                       If None, starts with an empty list.
        """
        self._notifiers: list[Notifier] = notifiers or []

    def add_notifier(self, notifier: Notifier) -> None:
        """Add a notifier to the manager.

        Args:
            notifier: Notifier implementation to add.
        """
        self._notifiers.append(notifier)

    def remove_notifier(self, notifier: Notifier) -> None:
        """Remove a notifier from the manager.

        Args:
            notifier: Notifier to remove.

        Raises:
            ValueError: If notifier is not registered.
        """
        self._notifiers.remove(notifier)

    @property
    def notifier_count(self) -> int:
        """Number of registered notifiers."""
        return len(self._notifiers)

    async def notify(self, context: NotificationContext) -> dict[str, bool]:
        """Send notification to all subscribed notifiers.

        Iterates through registered notifiers and sends to those
        that are subscribed to the event type. Failures are logged
        but don't interrupt other notifications.

        Args:
            context: Notification context with event details.

        Returns:
            Dict mapping notifier class name to success status.
            Only includes notifiers that were subscribed to this event.
        """
        results: dict[str, bool] = {}

        for notifier in self._notifiers:
            if context.event in notifier.subscribed_events:
                notifier_name = type(notifier).__name__
                try:
                    success = await notifier.send(context)
                    results[notifier_name] = success
                except Exception as e:
                    # Log but don't raise - notifications shouldn't break execution
                    _logger.warning(
                        "notifier_failed",
                        notifier=notifier_name,
                        error=str(e),
                    )
                    results[notifier_name] = False

        return results

    async def notify_job_start(
        self,
        job_id: str,
        job_name: str,
        total_sheets: int,
    ) -> dict[str, bool]:
        """Convenience method for job start notification.

        Args:
            job_id: Unique job identifier.
            job_name: Human-readable job name.
            total_sheets: Total number of sheets to process.

        Returns:
            Dict of notifier results.
        """
        return await self.notify(
            NotificationContext(
                event=NotificationEvent.JOB_START,
                job_id=job_id,
                job_name=job_name,
                total_sheets=total_sheets,
            )
        )

    async def notify_job_complete(
        self,
        job_id: str,
        job_name: str,
        success_count: int,
        failure_count: int,
        duration_seconds: float,
    ) -> dict[str, bool]:
        """Convenience method for job completion notification.

        Args:
            job_id: Unique job identifier.
            job_name: Human-readable job name.
            success_count: Number of successful sheets.
            failure_count: Number of failed sheets.
            duration_seconds: Total job duration.

        Returns:
            Dict of notifier results.
        """
        return await self.notify(
            NotificationContext(
                event=NotificationEvent.JOB_COMPLETE,
                job_id=job_id,
                job_name=job_name,
                success_count=success_count,
                failure_count=failure_count,
                duration_seconds=duration_seconds,
            )
        )

    async def notify_job_failed(
        self,
        job_id: str,
        job_name: str,
        error_message: str,
        sheet_num: int | None = None,
    ) -> dict[str, bool]:
        """Convenience method for job failure notification.

        Args:
            job_id: Unique job identifier.
            job_name: Human-readable job name.
            error_message: Error that caused the failure.
            sheet_num: Sheet number where failure occurred (optional).

        Returns:
            Dict of notifier results.
        """
        return await self.notify(
            NotificationContext(
                event=NotificationEvent.JOB_FAILED,
                job_id=job_id,
                job_name=job_name,
                error_message=error_message,
                sheet_num=sheet_num,
            )
        )

    async def notify_sheet_complete(
        self,
        job_id: str,
        job_name: str,
        sheet_num: int,
        total_sheets: int,
        success_count: int,
        failure_count: int,
    ) -> dict[str, bool]:
        """Convenience method for sheet completion notification.

        Args:
            job_id: Unique job identifier.
            job_name: Human-readable job name.
            sheet_num: Completed sheet number.
            total_sheets: Total number of sheets.
            success_count: Validations passed.
            failure_count: Validations failed.

        Returns:
            Dict of notifier results.
        """
        return await self.notify(
            NotificationContext(
                event=NotificationEvent.SHEET_COMPLETE,
                job_id=job_id,
                job_name=job_name,
                sheet_num=sheet_num,
                total_sheets=total_sheets,
                success_count=success_count,
                failure_count=failure_count,
            )
        )

    async def notify_rate_limit(
        self,
        job_id: str,
        job_name: str,
        sheet_num: int,
    ) -> dict[str, bool]:
        """Convenience method for rate limit notification.

        Args:
            job_id: Unique job identifier.
            job_name: Human-readable job name.
            sheet_num: Sheet that hit rate limit.

        Returns:
            Dict of notifier results.
        """
        return await self.notify(
            NotificationContext(
                event=NotificationEvent.RATE_LIMIT_DETECTED,
                job_id=job_id,
                job_name=job_name,
                sheet_num=sheet_num,
            )
        )

    async def close(self) -> None:
        """Close all registered notifiers.

        Should be called when the job completes or the manager
        is no longer needed. Ignores individual notifier errors.
        """
        for notifier in self._notifiers:
            try:
                await notifier.close()
            except Exception as e:
                _logger.warning(
                    "notifier_close_error",
                    notifier=type(notifier).__name__,
                    error=str(e),
                )
