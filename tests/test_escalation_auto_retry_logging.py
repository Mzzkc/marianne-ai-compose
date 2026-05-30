"""Regression tests for escalation auto-retry visibility (#257).

`ConsoleEscalationHandler.should_escalate` suppressed escalation on a sheet's
first failure when ``auto_retry_on_first_failure`` is True (the default) — but
logged nothing, so an operator debugging "why did my sheet retry?" found no
trace. These tests pin that the suppressed-escalation path emits a log line,
and that the other paths (escalate / high-confidence / auto-retry-disabled)
do not.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from marianne.core.checkpoint import SheetState, SheetStatus
from marianne.execution.escalation import ConsoleEscalationHandler


def _sheet(attempt_count: int) -> SheetState:
    return SheetState(
        sheet_num=3,
        status=SheetStatus.IN_PROGRESS,
        attempt_count=attempt_count,
    )


class TestAutoRetryEscalationLogging:
    async def test_first_failure_auto_retry_is_logged_and_not_escalated(self) -> None:
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.6, auto_retry_on_first_failure=True
        )
        with patch("marianne.execution.escalation._logger") as mock_logger:
            result = await handler.should_escalate(
                _sheet(1), MagicMock(), confidence=0.2
            )
        assert result is False  # suppressed → auto-retry, no human prompt
        mock_logger.info.assert_called_once()
        assert mock_logger.info.call_args[0][0] == "escalation.auto_retry_first_failure"

    async def test_second_failure_escalates_without_auto_retry_log(self) -> None:
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.6, auto_retry_on_first_failure=True
        )
        with patch("marianne.execution.escalation._logger") as mock_logger:
            result = await handler.should_escalate(
                _sheet(2), MagicMock(), confidence=0.2
            )
        assert result is True  # not first attempt → escalate
        mock_logger.info.assert_not_called()

    async def test_high_confidence_no_escalation_no_auto_retry_log(self) -> None:
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.6, auto_retry_on_first_failure=True
        )
        with patch("marianne.execution.escalation._logger") as mock_logger:
            result = await handler.should_escalate(
                _sheet(1), MagicMock(), confidence=0.9
            )
        assert result is False  # confidence acceptable → not an auto-retry suppress
        mock_logger.info.assert_not_called()

    async def test_auto_retry_disabled_escalates_first_failure(self) -> None:
        handler = ConsoleEscalationHandler(
            confidence_threshold=0.6, auto_retry_on_first_failure=False
        )
        with patch("marianne.execution.escalation._logger") as mock_logger:
            result = await handler.should_escalate(
                _sheet(1), MagicMock(), confidence=0.2
            )
        assert result is True  # auto-retry off → escalate even on first failure
        mock_logger.info.assert_not_called()
