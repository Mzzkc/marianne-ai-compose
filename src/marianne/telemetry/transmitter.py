"""Explicit submission of inspected envelopes; failures retain local data."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from marianne.core.logging import get_logger
from marianne.telemetry.config import TelemetryConfig
from marianne.telemetry.consent import ConsentRecord
from marianne.telemetry.spool import TelemetrySpool

_logger = get_logger("telemetry.transmitter")


class TelemetryTransmitter:
    """Lazy, bounded HTTP client. No receiver or background sending is configured.

    A successful HTTP response acknowledges a whole batch. Retrying after a lost
    response may deliver duplicates; the receiver must handle that possibility.
    """

    def __init__(
        self, config: TelemetryConfig, consent: ConsentRecord | None = None, *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.consent = consent if consent is not None else ConsentRecord()
        self._transport = transport

    def _ready(self) -> bool:
        return (self.config.enabled and self.config.submit_mode == "push"
                and self.consent.consent_id is not None)

    def _endpoint_allowed(self) -> bool:
        if not self.config.endpoint:
            return False
        endpoint = urlsplit(self.config.endpoint)
        return bool(
            endpoint.hostname and not endpoint.username and not endpoint.password
            and not endpoint.fragment
            and (endpoint.scheme == "https" or (
                endpoint.scheme == "http" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
            ))
        )

    async def _post(self, envelopes: list[dict[str, Any]]) -> bool:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, trust_env=False, transport=self._transport,
        ) as client:
            response = await client.post(self.config.endpoint or "", json={"envelopes": envelopes})
            return 200 <= response.status_code < 300

    async def submit(self, envelopes: list[dict[str, Any]]) -> bool:
        """Check fresh consent at the network boundary; never raise into a run."""
        try:
            if not envelopes or not self._ready():
                return False
            identity = self.consent.consent_id
            if identity is None or not self._endpoint_allowed() or any(
                record.get("consent_id") != identity for record in envelopes
            ):
                _logger.warning("telemetry.submission_refused")
                return False
            if await self._post(envelopes):
                return True
            _logger.warning("telemetry.submission_failed")
        except Exception:  # noqa: BLE001 — best-effort telemetry; never log payload or URL
            _logger.warning("telemetry.submission_failed")
        return False

    async def flush(self, *, dry_run: bool = False) -> int:
        """Return submitted count, zero for a no-op, or -1 on a retained failure."""
        try:
            if dry_run or not self._ready():
                return 0
            spool = TelemetrySpool(self.config.spool_path, consent_path=self.consent.path)
            with spool.submission() as acquired:
                if not acquired:
                    _logger.warning("telemetry.submission_busy")
                    return -1
                snapshot = spool.read()
                if not snapshot:
                    return 0
                if not await self.submit(snapshot):
                    return -1
                if not spool.acknowledge(snapshot):
                    _logger.warning("telemetry.spool_changed_during_submission")
                    return -1
                return len(snapshot)
        except Exception:  # noqa: BLE001 — leave failed storage/transport visible and retryable
            _logger.warning("telemetry.flush_failed")
            return -1


class MockTelemetryTransmitter(TelemetryTransmitter):
    """Network-free test double exercising the real consent and prune paths."""

    def __init__(
        self, config: TelemetryConfig, consent: ConsentRecord | None = None, *,
        succeed: bool = True,
    ) -> None:
        super().__init__(config, consent)
        self.succeed = succeed
        self.submissions: list[list[dict[str, Any]]] = []

    async def _post(self, envelopes: list[dict[str, Any]]) -> bool:
        self.submissions.append(envelopes)
        return self.succeed
