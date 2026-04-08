"""Generic webhook notification implementation using httpx.

Provides configurable HTTP webhook notifications for Marianne job events.
Supports custom headers, retries, and flexible JSON payloads.

Phase 5 of Marianne implementation: Missing README features.
"""

import asyncio
import os
import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

import httpx

from marianne.core.logging import get_logger
from marianne.notifications.base import (
    NotificationContext,
    NotificationEvent,
)

# Module-level logger for webhook notifications
_logger = get_logger("notifications.webhook")

# Pre-compiled regex for environment variable expansion (${VAR} syntax)
_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _serialize_context(context: NotificationContext) -> dict[str, Any]:
    """Serialize NotificationContext to JSON-compatible dict.

    Args:
        context: Notification context to serialize.

    Returns:
        Dict with all context fields, datetime converted to ISO string.
    """
    data = asdict(context)
    # Convert datetime to ISO string
    if isinstance(data.get("timestamp"), datetime):
        data["timestamp"] = data["timestamp"].isoformat()
    # Convert enum to string value
    if "event" in data:
        data["event"] = context.event.value
    return data


class WebhookNotifier:
    """Generic HTTP webhook notification implementation.

    Posts JSON notifications to configurable HTTP endpoints.
    Supports custom headers (for auth tokens), retries, and timeouts.

    Example usage:
        notifier = WebhookNotifier(
            url="https://example.com/webhooks/marianne",
            headers={"Authorization": "Bearer token123"},
            events={NotificationEvent.JOB_COMPLETE, NotificationEvent.JOB_FAILED},
        )
        await notifier.send(context)

    Configuration from YAML:
        notifications:
          - type: webhook
            on_events: [job_complete, job_failed]
            config:
              url: https://example.com/webhook
              url_env: MZT_WEBHOOK_URL  # Alternative to url
              headers:
                Authorization: "Bearer ${WEBHOOK_TOKEN}"
                X-Custom-Header: "value"
              timeout: 30
              max_retries: 2
              retry_delay: 1.0
    """

    def __init__(
        self,
        url: str | None = None,
        url_env: str | None = None,
        headers: dict[str, str] | None = None,
        events: set[NotificationEvent] | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        include_metadata: bool = True,
    ) -> None:
        """Initialize the webhook notifier.

        Args:
            url: Direct webhook URL.
            url_env: Environment variable containing webhook URL.
            headers: HTTP headers to include in requests.
            events: Set of events to subscribe to. Defaults to job-level events.
            timeout: HTTP request timeout in seconds.
            max_retries: Maximum retry attempts on failure (0 = no retries).
            retry_delay: Delay between retries in seconds.
            include_metadata: Include Marianne metadata in payload (version, source).
        """
        # Get URL from param or environment
        self._url = url
        if not self._url and url_env:
            self._url = os.environ.get(url_env, "")

        # Process headers - expand environment variables
        self._headers = self._expand_env_headers(headers or {})
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._include_metadata = include_metadata

        self._events = events or {
            NotificationEvent.JOB_COMPLETE,
            NotificationEvent.JOB_FAILED,
        }

        self._client: httpx.AsyncClient | None = None
        self._warned_no_url = False

    @staticmethod
    def _expand_env_headers(headers: dict[str, str]) -> dict[str, str]:
        """Expand environment variables in header values.

        Supports ${VAR} syntax in header values.

        Args:
            headers: Dict of header names to values.

        Returns:
            Dict with environment variables expanded.
        """
        expanded: dict[str, str] = {}
        for key, value in headers.items():
            # Simple ${VAR} expansion using pre-compiled pattern
            if "${" in value:
                matches = _ENV_VAR_PATTERN.findall(value)
                for var_name in matches:
                    env_value = os.environ.get(var_name)
                    if env_value is None:
                        _logger.warning(
                            "webhook_env_var_missing",
                            header=key,
                            var_name=var_name,
                        )
                        env_value = ""
                    value = value.replace(f"${{{var_name}}}", env_value)
            expanded[key] = value
        return expanded

    @classmethod
    def from_config(
        cls,
        on_events: list[str],
        config: dict[str, Any] | None = None,
    ) -> "WebhookNotifier":
        """Create WebhookNotifier from YAML configuration.

        Args:
            on_events: List of event name strings from config.
            config: Optional dict with webhook-specific settings:
                - url: Direct webhook URL
                - url_env: Env var for webhook URL
                - headers: Dict of HTTP headers
                - timeout: Request timeout in seconds
                - max_retries: Retry attempts on failure
                - retry_delay: Delay between retries
                - include_metadata: Include Marianne metadata

        Returns:
            Configured WebhookNotifier instance.

        Example:
            notifier = WebhookNotifier.from_config(
                on_events=["job_complete", "job_failed"],
                config={
                    "url": "https://example.com/webhook",
                    "headers": {"X-API-Key": "secret"},
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
            url=config.get("url"),
            url_env=config.get("url_env"),
            headers=config.get("headers"),
            events=events if events else None,
            timeout=config.get("timeout", 30.0),
            max_retries=config.get("max_retries", 2),
            retry_delay=config.get("retry_delay", 1.0),
            include_metadata=config.get("include_metadata", True),
        )

    @property
    def subscribed_events(self) -> set[NotificationEvent]:
        """Events this notifier is registered to receive.

        Returns:
            Set of subscribed NotificationEvent types.
        """
        return self._events

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with connection pooling.

        Returns:
            Configured httpx.AsyncClient instance.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers=self._headers,
            )
        return self._client

    def _build_payload(self, context: NotificationContext) -> dict[str, Any]:
        """Build webhook JSON payload.

        Args:
            context: Notification context with event details.

        Returns:
            Dict suitable for JSON POST body.
        """
        payload: dict[str, Any] = {
            "event_type": context.event.value,
            "context": _serialize_context(context),
        }

        if self._include_metadata:
            payload["metadata"] = {
                "source": "marianne-ai-compose",
                "version": "1.0.0",
            }

        return payload

    async def _send_with_retry(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Send request with retry logic.

        Args:
            client: HTTP client to use.
            payload: JSON payload to send.

        Returns:
            Tuple of (success, error_message).
        """
        last_error: str | None = None

        # Type narrowing: _url is verified non-None by caller (send method)
        assert self._url is not None, "URL must be set before sending requests"

        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(
                    self._url,
                    json=payload,
                )

                if response.is_success:
                    return True, None
                if response.status_code >= 500:
                    # Server error - retry
                    last_error = f"HTTP {response.status_code}: {response.text[:100]}"
                    _logger.debug(
                        "webhook_retry_server_error",
                        attempt=attempt + 1,
                        status_code=response.status_code,
                    )
                else:
                    # Client error - don't retry
                    return False, f"HTTP {response.status_code}: {response.text[:100]}"

            except httpx.TimeoutException:
                last_error = "Request timed out"
                _logger.debug("webhook_retry_timeout", attempt=attempt + 1)
            except httpx.RequestError as e:
                last_error = str(e)
                _logger.debug(
                    "webhook_retry_request_error",
                    attempt=attempt + 1,
                    error=last_error,
                    exc_info=True,
                )

            # Wait before retry (except on last attempt)
            if attempt < self._max_retries:
                await asyncio.sleep(self._retry_delay)

        return False, last_error

    async def send(self, context: NotificationContext) -> bool:
        """Send a webhook notification.

        Posts JSON payload to the configured URL.
        Implements retry logic for transient failures.

        Args:
            context: Notification context with event details.

        Returns:
            True if notification was sent, False if unavailable or failed.
        """
        if not self._url:
            if not self._warned_no_url:
                _logger.warning(
                    "Webhook URL not configured. "
                    "Set url or url_env in webhook notification config."
                )
                self._warned_no_url = True
            return False

        if context.event not in self._events:
            # Not subscribed to this event
            return True

        try:
            client = await self._get_client()
            payload = self._build_payload(context)

            success, error = await self._send_with_retry(client, payload)

            if success:
                _logger.debug("webhook_notification_sent", title=context.format_title())
            else:
                _logger.warning("webhook_notification_failed", error=error)

            return success

        except Exception as e:
            _logger.warning(
                "webhook_notification_unexpected_error",
                error=str(e),
                exc_info=True,
            )
            return False

    async def close(self) -> None:
        """Clean up HTTP client resources.

        Called when the NotificationManager is shutting down.
        """
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


class MockWebhookNotifier:
    """Mock webhook notifier for testing.

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
        }
        self.sent_notifications: list[NotificationContext] = []
        self.sent_payloads: list[dict[str, Any]] = []
        self._fail_next = False
        self._simulated_status_code = 200

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

    def simulate_status_code(self, code: int) -> None:
        """Simulate a specific HTTP status code response.

        Args:
            code: HTTP status code to simulate.
        """
        self._simulated_status_code = code

    async def send(self, context: NotificationContext) -> bool:
        """Record notification without making HTTP call.

        Args:
            context: Notification context.

        Returns:
            True unless set_fail_next was called or simulated error status.
        """
        if self._fail_next:
            self._fail_next = False
            return False

        if self._simulated_status_code >= 400:
            self._simulated_status_code = 200  # Reset after checking
            return False

        self.sent_notifications.append(context)
        # Build payload like real notifier would
        notifier = WebhookNotifier(url="https://mock")
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
