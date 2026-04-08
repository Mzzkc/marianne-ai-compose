"""Judgment client for Recursive Light integration.

Provides JudgmentQuery/JudgmentResponse protocol for consulting Recursive Light
on execution decisions, plus LocalJudgmentClient fallback for offline operation.

Phase 4 of AGI Evolution: Judgment Integration
"""

import logging
import types
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from marianne.core.checkpoint import ValidationDetailDict

_logger = logging.getLogger("marianne.learning.judgment")


@dataclass
class JudgmentQuery:
    """Query sent to Recursive Light for execution judgment.

    Contains all relevant context about the current sheet execution
    state needed for RL to provide informed judgment.
    """

    job_id: str
    """Unique identifier for the job."""

    sheet_num: int
    """Current sheet number being executed."""

    validation_results: list[ValidationDetailDict]
    """Serialized validation results from the sheet."""

    execution_history: list[dict[str, Any]]
    """History of previous execution attempts for this sheet."""

    error_patterns: list[str]
    """Detected error patterns from previous attempts."""

    retry_count: int
    """Number of retry attempts already made."""

    confidence: float
    """Current aggregate confidence score (0.0-1.0)."""


@dataclass
class JudgmentResponse:
    """Response from Recursive Light with execution judgment.

    Provides recommended action, reasoning, and optional modifications
    for how to proceed with sheet execution.
    """

    recommended_action: Literal["proceed", "retry", "completion", "escalate", "abort"]
    """Recommended action:
    - proceed: Continue to next sheet (current succeeded)
    - retry: Retry the current sheet
    - completion: Run completion prompt for partial success
    - escalate: Escalate to human for decision
    - abort: Stop the entire job
    """

    confidence: float
    """RL's confidence in this recommendation (0.0-1.0)."""

    reasoning: str
    """Explanation of why this action is recommended."""

    prompt_modifications: list[str] | None = None
    """Optional modifications to apply to prompt on retry."""

    escalation_urgency: Literal["low", "medium", "high"] | None = None
    """Urgency level if action is 'escalate'."""

    human_question: str | None = None
    """Specific question to ask human if escalating."""

    patterns_learned: list[str] = field(default_factory=list)
    """New patterns identified by RL from this execution."""


class JudgmentClient:
    """HTTP client for Recursive Light judgment API.

    Connects to Recursive Light's /api/marianne/judgment endpoint
    to get TDF-aligned execution decisions with accumulated wisdom.

    Falls back to default "retry" action on connection errors
    for graceful degradation.

    Attributes:
        endpoint: Base URL for the Recursive Light API.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        endpoint: str,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the judgment client.

        Args:
            endpoint: Base URL for the Recursive Light API server.
            timeout: Request timeout in seconds. Defaults to 30.0.
        """
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Lazy initialization to avoid creating client before event loop.

        Returns:
            Configured httpx.AsyncClient instance.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint,
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def get_judgment(self, query: JudgmentQuery) -> JudgmentResponse:
        """Get execution judgment from Recursive Light.

        Posts the query to RL's /api/marianne/judgment endpoint and
        parses the response. On errors, returns a default "retry"
        action for graceful degradation.

        Args:
            query: Full context about the sheet execution state.

        Returns:
            JudgmentResponse with recommended action. On connection
            errors, returns default retry action with low confidence.
        """
        try:
            client = await self._get_client()

            # Build request payload from JudgmentQuery
            payload = {
                "job_id": query.job_id,
                "sheet_num": query.sheet_num,
                "validation_results": query.validation_results,
                "execution_history": query.execution_history,
                "error_patterns": query.error_patterns,
                "retry_count": query.retry_count,
                "confidence": query.confidence,
            }

            # POST to RL judgment endpoint
            response = await client.post("/api/marianne/judgment", json=payload)

            if response.status_code != 200:
                # API error - fall back to retry
                return self._default_retry_response(
                    f"RL API error: {response.status_code}"
                )

            # Parse response JSON
            data = response.json()
            return self._parse_judgment_response(data)

        except httpx.ConnectError as e:
            return self._default_retry_response(f"Connection error: {e}")

        except httpx.TimeoutException as e:
            return self._default_retry_response(f"Timeout: {e}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                # 4xx client errors are permanent — retrying won't help.
                _logger.warning(
                    "judgment_client_error: HTTP %s — not retrying",
                    e.response.status_code,
                )
                return JudgmentResponse(
                    recommended_action="retry",
                    confidence=0.1,
                    reasoning=f"RL client error (HTTP {e.response.status_code}), unavailable",
                )
            return self._default_retry_response(f"HTTP server error: {e}")

        except (ValueError, KeyError, TypeError) as e:
            return self._default_retry_response(f"Response parsing error: {e}")

        except (httpx.HTTPError, OSError, OverflowError) as e:
            _logger.warning("judgment_unexpected_error: %s", e, exc_info=True)
            return self._default_retry_response(f"Unexpected error: {e}")
        except ExceptionGroup as eg:
            _logger.warning("judgment_exception_group: %s", eg, exc_info=True)
            return self._default_retry_response(f"Connection error group: {eg}")

    def _parse_judgment_response(self, data: dict[str, Any]) -> JudgmentResponse:
        """Parse RL API response into JudgmentResponse.

        Extracts judgment fields with graceful handling of missing
        or malformed data.

        Args:
            data: JSON response from RL API.

        Returns:
            JudgmentResponse with parsed fields.
        """
        # Extract and validate recommended_action
        action = data.get("recommended_action", "retry")
        valid_actions = {"proceed", "retry", "completion", "escalate", "abort"}
        if action not in valid_actions:
            action = "retry"

        # Extract confidence (0.0-1.0)
        confidence = data.get("confidence", 0.5)
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        # Extract reasoning
        reasoning = data.get("reasoning", "No reasoning provided by RL")
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        # Extract optional fields
        prompt_modifications = data.get("prompt_modifications")
        if prompt_modifications is not None and not isinstance(
            prompt_modifications, list
        ):
            prompt_modifications = None

        escalation_urgency = data.get("escalation_urgency")
        if escalation_urgency not in {"low", "medium", "high", None}:
            escalation_urgency = None

        human_question = data.get("human_question")
        if human_question is not None and not isinstance(human_question, str):
            human_question = None

        patterns_learned = data.get("patterns_learned", [])
        if not isinstance(patterns_learned, list):
            patterns_learned = []
        patterns_learned = [str(p) for p in patterns_learned if p]

        return JudgmentResponse(
            recommended_action=action,
            confidence=confidence,
            reasoning=reasoning,
            prompt_modifications=prompt_modifications,
            escalation_urgency=escalation_urgency,
            human_question=human_question,
            patterns_learned=patterns_learned,
        )

    def _default_retry_response(self, error_reason: str) -> JudgmentResponse:
        """Create default retry response for error cases.

        Used when RL is unavailable to provide graceful degradation.

        Args:
            error_reason: Description of what went wrong.

        Returns:
            JudgmentResponse with retry action and low confidence.
        """
        return JudgmentResponse(
            recommended_action="retry",
            confidence=0.3,
            reasoning=f"RL unavailable ({error_reason}), defaulting to retry",
            prompt_modifications=None,
            escalation_urgency=None,
            human_question=None,
            patterns_learned=[],
        )

    async def close(self) -> None:
        """Close the HTTP client connection.

        Should be called when done using the client to clean up resources.
        """
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "JudgmentClient":
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Async context manager exit - closes client."""
        await self.close()


class LocalJudgmentClient:
    """Local fallback judgment client using simple heuristics.

    Provides judgment without network calls for offline operation
    or when Recursive Light is unavailable. Uses confidence thresholds
    and pass rate heuristics to determine actions.

    This is a simpler, always-available alternative that makes
    reasonable decisions based on local metrics alone.

    Attributes:
        proceed_threshold: Confidence above which to proceed (default 0.7).
        retry_threshold: Confidence above which to retry (default 0.4).
        max_retries: Maximum retries before escalating (default 3).
    """

    def __init__(
        self,
        proceed_threshold: float = 0.7,
        retry_threshold: float = 0.4,
        max_retries: int = 3,
    ) -> None:
        """Initialize the local judgment client.

        Args:
            proceed_threshold: Confidence >= this proceeds to next sheet.
            retry_threshold: Confidence >= this (but < proceed) retries.
                Below this, escalates or aborts.
            max_retries: Maximum retry count before escalating.
        """
        self.proceed_threshold = proceed_threshold
        self.retry_threshold = retry_threshold
        self.max_retries = max_retries

    async def get_judgment(self, query: JudgmentQuery) -> JudgmentResponse:
        """Get execution judgment using local heuristics.

        Decision logic:
        1. High confidence (>= proceed_threshold) -> proceed
        2. Medium confidence (>= retry_threshold) -> retry (unless max retries)
        3. Low confidence (< retry_threshold) -> escalate
        4. Max retries exceeded -> completion or escalate based on pass rate

        Args:
            query: Full context about the sheet execution state.

        Returns:
            JudgmentResponse with recommended action based on heuristics.
        """
        confidence = query.confidence
        retry_count = query.retry_count

        # Calculate validation pass rate
        pass_rate = self._calculate_pass_rate(query.validation_results)

        # High confidence: proceed to next sheet
        if confidence >= self.proceed_threshold:
            return JudgmentResponse(
                recommended_action="proceed",
                confidence=0.8,
                reasoning=f"Confidence {confidence:.1%} >= {self.proceed_threshold:.1%} threshold",
                patterns_learned=[],
            )

        # Max retries exceeded
        if retry_count >= self.max_retries:
            # If pass rate is decent, try completion
            if pass_rate >= 0.5:
                return JudgmentResponse(
                    recommended_action="completion",
                    confidence=0.6,
                    reasoning=(
                        f"Max retries ({self.max_retries}) exceeded but pass rate "
                        f"{pass_rate:.1%} suggests partial success - trying completion"
                    ),
                    patterns_learned=["max_retries_with_partial_success"],
                )
            # Otherwise escalate
            return JudgmentResponse(
                recommended_action="escalate",
                confidence=0.7,
                reasoning=(
                    f"Max retries ({self.max_retries}) exceeded with low pass rate "
                    f"{pass_rate:.1%} - human decision needed"
                ),
                escalation_urgency="medium",
                human_question=(
                    f"Sheet {query.sheet_num} failed {retry_count} times with "
                    f"{pass_rate:.0%} validation pass rate. Continue, skip, or abort?"
                ),
                patterns_learned=["max_retries_exceeded"],
            )

        # Medium confidence: retry
        if confidence >= self.retry_threshold:
            return JudgmentResponse(
                recommended_action="retry",
                confidence=0.6,
                reasoning=(
                    f"Confidence {confidence:.1%} in retry range "
                    f"[{self.retry_threshold:.1%}, {self.proceed_threshold:.1%})"
                ),
                patterns_learned=[],
            )

        # Low confidence: escalate
        urgency: Literal["low", "medium", "high"] = "low"
        if confidence < 0.2:
            urgency = "high"
        elif confidence < 0.3:
            urgency = "medium"

        return JudgmentResponse(
            recommended_action="escalate",
            confidence=0.5,
            reasoning=(
                f"Confidence {confidence:.1%} < {self.retry_threshold:.1%} "
                f"threshold - escalating for human decision"
            ),
            escalation_urgency=urgency,
            human_question=(
                f"Sheet {query.sheet_num} has low confidence ({confidence:.0%}) "
                f"after {retry_count} attempts. How should we proceed?"
            ),
            patterns_learned=["low_confidence_escalation"],
        )

    def _calculate_pass_rate(
        self, validation_results: list[ValidationDetailDict]
    ) -> float:
        """Calculate validation pass rate from results.

        Args:
            validation_results: List of serialized validation results.

        Returns:
            Pass rate as float (0.0-1.0). Returns 0.0 if no validations.
        """
        if not validation_results:
            return 0.0

        passed = sum(1 for v in validation_results if v.get("passed", False))
        return passed / len(validation_results)
