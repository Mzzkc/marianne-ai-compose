"""Outcome recording and pattern detection for learning."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from mozart.core.checkpoint import SheetStatus, ValidationDetailDict

if TYPE_CHECKING:
    from mozart.learning.patterns import DetectedPattern


@dataclass
class SheetOutcome:
    """Structured outcome data from a completed sheet execution.

    This dataclass captures all relevant information about a sheet execution
    for learning and pattern detection purposes.
    """

    sheet_id: str
    job_id: str
    validation_results: list[ValidationDetailDict]  # Serialized ValidationResult data
    execution_duration: float
    retry_count: int
    completion_mode_used: bool
    final_status: SheetStatus
    validation_pass_rate: float
    success_without_retry: bool
    patterns_detected: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    # Semantic validation fields (Evolution: Deep Validation-Learning)
    failure_category_counts: dict[str, int] = field(default_factory=dict)
    """Counts of each failure category from validation results.

    Example: {'missing': 2, 'stale': 1, 'malformed': 0}
    Categories: missing, malformed, incomplete, stale, error
    """

    semantic_patterns: list[str] = field(default_factory=list)
    """Extracted semantic patterns from failure_reason fields.

    Example: ['file not created', 'pattern not found', 'content empty']
    These are normalized phrases that can be aggregated across outcomes.
    """

    fix_suggestions: list[str] = field(default_factory=list)
    """Collected suggested_fix values from failed validations.

    Example: ['Ensure file is created in workspace/', 'Add missing import']
    """

    patterns_applied: list[str] = field(default_factory=list)
    """Pattern descriptions that were applied/injected for this sheet execution.

    These are the patterns from get_relevant_patterns() that were included
    in the prompt. Used for effectiveness tracking: correlate patterns_applied
    with success_without_retry to measure which patterns actually help.

    Example: ['⚠️ Common issue: file_exists validation tends to fail (seen 3x)']
    """

    # Output capture fields (Evolution: Learning Data Collection)
    stdout_tail: str = ""
    """Captured tail of stdout from execution.

    Stores the last N characters of stdout for pattern extraction.
    Used by OutputPatternExtractor to detect error patterns.
    """

    stderr_tail: str = ""
    """Captured tail of stderr from execution.

    Stores the last N characters of stderr for pattern extraction.
    Used by OutputPatternExtractor to detect error patterns.
    """

    error_history: list[dict[str, Any]] = field(default_factory=list)
    """History of errors encountered during execution.

    Each entry is a dict with error_code, category, message, etc.
    Used by _detect_error_code_patterns() for recurring error analysis.

    Example: [{'error_code': 'E009', 'category': 'transient', 'message': 'Rate limited'}]
    """

    # Grounding integration fields (Evolution v11: Grounding→Pattern Integration)
    grounding_passed: bool | None = None
    """Whether all grounding hooks passed (None if grounding not enabled).

    Used to correlate grounding outcomes with validation results and
    pattern effectiveness over time.
    """

    grounding_confidence: float | None = None
    """Average confidence across grounding hooks (0.0-1.0, None if not enabled).

    Higher confidence means external validators had high certainty in their results.
    Can be correlated with success_without_retry to identify reliable grounding sources.
    """

    grounding_guidance: str | None = None
    """Recovery guidance from failed grounding hooks (None if passed or not enabled).

    Captures actionable suggestions from external validators that failed,
    useful for pattern detection and learning what recovery strategies work.
    """


@runtime_checkable
class OutcomeStore(Protocol):
    """Protocol for outcome storage backends.

    Provides async methods for recording outcomes and querying
    for similar past outcomes to inform execution decisions.
    """

    async def record(self, outcome: SheetOutcome) -> None:
        """Record a sheet outcome for future learning."""
        ...

    async def query_similar(
        self, context: dict[str, Any], limit: int = 10
    ) -> list[SheetOutcome]:
        """Query for similar past outcomes based on context."""
        ...

    async def get_patterns(self, job_name: str) -> list[str]:
        """Get detected patterns for a specific job."""
        ...

    async def get_relevant_patterns(
        self, context: dict[str, Any], limit: int = 5
    ) -> list[str]:
        """Get pattern descriptions relevant to the given context."""
        ...


class JsonOutcomeStore:
    """JSON-file based outcome store implementation.

    Stores outcomes in a JSON file with atomic saves,
    following the same pattern as JsonStateBackend.
    """

    def __init__(self, store_path: Path) -> None:
        """Initialize the JSON outcome store.

        Args:
            store_path: Path to the JSON file for storing outcomes.
        """
        self.store_path = store_path
        self._outcomes: list[SheetOutcome] = []
        self._loaded: bool = False
        self._cached_patterns: list[DetectedPattern] | None = None
        self._patterns_outcome_count: int = 0

    async def record(self, outcome: SheetOutcome) -> None:
        """Record a sheet outcome to the store.

        After recording, if there are enough outcomes (>= 5), pattern
        detection is run and patterns_detected is populated on the outcome.

        Args:
            outcome: The sheet outcome to record.
        """
        # Load existing outcomes first to avoid overwriting on fresh store instance
        await self._load()

        # Check if this sheet already has an outcome (update rather than duplicate)
        existing_idx = next(
            (
                i for i, existing in enumerate(self._outcomes)
                if existing.sheet_id == outcome.sheet_id
            ),
            None,
        )
        if existing_idx is not None:
            self._outcomes[existing_idx] = outcome
        else:
            self._outcomes.append(outcome)

        # Invalidate cached patterns when outcomes change
        self._cached_patterns = None

        # Detect patterns after accumulating enough data
        if len(self._outcomes) >= 5:
            patterns = await self.detect_patterns()
            # Populate patterns_detected with top pattern descriptions
            outcome.patterns_detected = [
                p.to_prompt_guidance() for p in patterns[:3]
            ]

        await self._save()

    async def query_similar(
        self, context: dict[str, Any], limit: int = 10
    ) -> list[SheetOutcome]:
        """Query for similar past outcomes.

        Currently returns recent outcomes for the same job_id.
        Future: implement semantic similarity matching.

        Args:
            context: Context dict containing job_id and other metadata.
            limit: Maximum number of outcomes to return.

        Returns:
            List of similar sheet outcomes.
        """
        await self._load()
        job_id = context.get("job_id")
        if not job_id:
            return self._outcomes[-limit:]

        matching = [outcome for outcome in self._outcomes if outcome.job_id == job_id]
        return matching[-limit:]

    async def get_patterns(self, job_name: str) -> list[str]:
        """Get detected patterns for a job.

        Args:
            job_name: The job name to get patterns for.

        Returns:
            List of pattern strings detected across outcomes.
        """
        await self._load()
        patterns: set[str] = set()
        for outcome in self._outcomes:
            if outcome.job_id == job_name:
                patterns.update(outcome.patterns_detected)
        return list(patterns)

    async def detect_patterns(self) -> list["DetectedPattern"]:
        """Detect patterns from all recorded outcomes.

        Uses PatternDetector to analyze historical outcomes and
        identify recurring patterns that can inform future executions.
        Results are cached and invalidated when outcomes change (record/load).

        Returns:
            List of DetectedPattern objects sorted by confidence.
        """
        from mozart.learning.patterns import PatternDetector

        await self._load()
        if not self._outcomes:
            return []

        # Return cached patterns if outcome list hasn't changed
        if (
            self._cached_patterns is not None
            and self._patterns_outcome_count == len(self._outcomes)
        ):
            return self._cached_patterns

        detector = PatternDetector(self._outcomes)
        self._cached_patterns = detector.detect_all()
        self._patterns_outcome_count = len(self._outcomes)
        return self._cached_patterns

    async def get_relevant_patterns(
        self,
        context: dict[str, Any],
        limit: int = 5,
    ) -> list[str]:
        """Get pattern descriptions relevant to the given context.

        This method detects patterns, matches them to the context,
        and returns human-readable descriptions suitable for prompt injection.

        Args:
            context: Context dict containing job_id, sheet_num, validation_types, etc.
            limit: Maximum number of patterns to return.

        Returns:
            List of pattern description strings for prompt injection.
        """
        from mozart.learning.patterns import PatternApplicator, PatternMatcher

        # Detect all patterns
        all_patterns = await self.detect_patterns()
        if not all_patterns:
            return []

        # Match patterns to context
        matcher = PatternMatcher(all_patterns)
        matched = matcher.match(context, limit=limit)

        # Convert to prompt-ready descriptions
        applicator = PatternApplicator(matched)
        return applicator.get_pattern_descriptions()

    async def _save(self) -> None:
        """Save outcomes to JSON file with atomic write."""
        import json
        import tempfile

        data = {
            "outcomes": [
                {
                    "sheet_id": outcome.sheet_id,
                    "job_id": outcome.job_id,
                    "validation_results": outcome.validation_results,
                    "execution_duration": outcome.execution_duration,
                    "retry_count": outcome.retry_count,
                    "completion_mode_used": outcome.completion_mode_used,
                    "final_status": outcome.final_status.value,
                    "validation_pass_rate": outcome.validation_pass_rate,
                    "success_without_retry": outcome.success_without_retry,
                    "patterns_detected": outcome.patterns_detected,
                    "timestamp": outcome.timestamp.isoformat(),
                    # Semantic validation fields (Evolution: Deep Validation-Learning)
                    "failure_category_counts": outcome.failure_category_counts,
                    "semantic_patterns": outcome.semantic_patterns,
                    "fix_suggestions": outcome.fix_suggestions,
                    # Effectiveness tracking field (Evolution: Pattern Effectiveness)
                    "patterns_applied": outcome.patterns_applied,
                    # Output capture fields (Evolution: Learning Data Collection)
                    "stdout_tail": outcome.stdout_tail,
                    "stderr_tail": outcome.stderr_tail,
                    "error_history": outcome.error_history,
                    # Grounding integration fields (Evolution v11: Grounding→Pattern Integration)
                    "grounding_passed": outcome.grounding_passed,
                    "grounding_confidence": outcome.grounding_confidence,
                    "grounding_guidance": outcome.grounding_guidance,
                }
                for outcome in self._outcomes
            ]
        }

        # Atomic write: write to temp file, then rename
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.store_path.parent,
                suffix=".tmp",
                delete=False,
            ) as f:
                json.dump(data, f, indent=2)
                temp_path = Path(f.name)

            temp_path.rename(self.store_path)
        except BaseException:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    async def _load(self) -> None:
        """Load outcomes from JSON file.

        Uses _loaded flag to avoid re-reading from disk on every call.
        """
        import json

        if self._loaded:
            return

        self._loaded = True

        if not self.store_path.exists():
            return

        with open(self.store_path) as f:
            data = json.load(f)

        self._outcomes = [
            SheetOutcome(
                sheet_id=raw["sheet_id"],
                job_id=raw["job_id"],
                validation_results=raw["validation_results"],
                execution_duration=raw["execution_duration"],
                retry_count=raw["retry_count"],
                completion_mode_used=raw["completion_mode_used"],
                final_status=SheetStatus(raw["final_status"]),
                validation_pass_rate=raw["validation_pass_rate"],
                success_without_retry=raw.get(
                    "success_without_retry", raw.get("first_attempt_success", False)
                ),
                patterns_detected=raw.get("patterns_detected", []),
                timestamp=datetime.fromisoformat(raw["timestamp"]),
                # Semantic validation fields (Evolution: Deep Validation-Learning)
                failure_category_counts=raw.get("failure_category_counts", {}),
                semantic_patterns=raw.get("semantic_patterns", []),
                fix_suggestions=raw.get("fix_suggestions", []),
                # Effectiveness tracking field (Evolution: Pattern Effectiveness)
                patterns_applied=raw.get("patterns_applied", []),
                # Output capture fields (Evolution: Learning Data Collection)
                stdout_tail=raw.get("stdout_tail", ""),
                stderr_tail=raw.get("stderr_tail", ""),
                error_history=raw.get("error_history", []),
                # Grounding integration fields (Evolution v11: Grounding→Pattern Integration)
                grounding_passed=raw.get("grounding_passed"),
                grounding_confidence=raw.get("grounding_confidence"),
                grounding_guidance=raw.get("grounding_guidance"),
            )
            for raw in data.get("outcomes", [])
        ]
