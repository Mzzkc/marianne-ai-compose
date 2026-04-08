"""Data models for the global learning store.

This module contains all dataclasses and enums used by the GlobalLearningStore.
These models represent the various records stored in the SQLite database for
cross-workspace pattern persistence and learning.

Extracted from global_store.py as part of the modularization effort.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar


class QuarantineStatus(str, Enum):
    """Status of a pattern in the quarantine lifecycle.

    v19 Evolution: Pattern Quarantine & Provenance - patterns transition through
    these states as they are validated through successful applications:

    - PENDING: New patterns start here, awaiting initial validation
    - QUARANTINED: Explicitly marked for review due to concerns
    - VALIDATED: Proven effective through repeated successful applications
    - RETIRED: No longer active, kept for historical reference
    """

    PENDING = "pending"
    """New pattern awaiting validation through application."""

    QUARANTINED = "quarantined"
    """Pattern under review - may have caused issues or needs investigation."""

    VALIDATED = "validated"
    """Pattern has proven effective and is trusted for autonomous application."""

    RETIRED = "retired"
    """Pattern no longer in active use, retained for history."""


@dataclass
class SuccessFactors:
    """Captures WHY a pattern succeeded - the context conditions and factors.

    v22 Evolution: Metacognitive Pattern Reflection - patterns now capture
    not just WHAT happened but WHY it worked. This enables better pattern
    selection by understanding causality, not just correlation.

    Success factors include:
    - Context conditions: validation types, error categories, execution phase
    - Timing factors: time of day, retry iteration, prior sheet outcomes
    - Prerequisite states: prior sheet completion, escalation status
    """

    # Context conditions present when pattern succeeded
    validation_types: list[str] = field(default_factory=list)
    """Validation types that were active: file, regex, artifact, etc."""

    error_categories: list[str] = field(default_factory=list)
    """Error categories present in the execution: rate_limit, auth, validation, etc."""

    prior_sheet_status: str | None = None
    """Status of the immediately prior sheet: completed, failed, skipped."""

    # Timing factors
    time_of_day_bucket: str | None = None
    """Time bucket: morning, afternoon, evening, night."""

    retry_iteration: int = 0
    """Which retry attempt this success occurred on (0 = first attempt)."""

    # Prerequisite states
    escalation_was_pending: bool = False
    """Whether an escalation was pending when pattern succeeded."""

    grounding_confidence: float | None = None
    """Grounding confidence score if external validation was present."""

    # Aggregated metrics
    occurrence_count: int = 1
    """How often this factor combination has been observed."""

    success_rate: float = 1.0
    """Success rate when these factors are present (0.0-1.0)."""

    def __post_init__(self) -> None:
        """Clamp success_rate and occurrence_count to valid bounds."""
        self.success_rate = max(0.0, min(1.0, self.success_rate))
        self.occurrence_count = max(1, self.occurrence_count)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "validation_types": self.validation_types,
            "error_categories": self.error_categories,
            "prior_sheet_status": self.prior_sheet_status,
            "time_of_day_bucket": self.time_of_day_bucket,
            "retry_iteration": self.retry_iteration,
            "escalation_was_pending": self.escalation_was_pending,
            "grounding_confidence": self.grounding_confidence,
            "occurrence_count": self.occurrence_count,
            "success_rate": self.success_rate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SuccessFactors":
        """Deserialize from dictionary."""
        return cls(
            validation_types=data.get("validation_types", []),
            error_categories=data.get("error_categories", []),
            prior_sheet_status=data.get("prior_sheet_status"),
            time_of_day_bucket=data.get("time_of_day_bucket"),
            retry_iteration=data.get("retry_iteration", 0),
            escalation_was_pending=data.get("escalation_was_pending", False),
            grounding_confidence=data.get("grounding_confidence"),
            occurrence_count=data.get("occurrence_count", 1),
            success_rate=data.get("success_rate", 1.0),
        )

    @staticmethod
    def get_time_bucket(hour: int) -> str:
        """Get time bucket for an hour (0-23)."""
        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "night"


@dataclass
class ExecutionRecord:
    """A record of a sheet execution stored in the global database."""

    id: str
    workspace_hash: str
    job_hash: str
    sheet_num: int
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float
    status: str
    retry_count: int
    success_without_retry: bool
    validation_pass_rate: float
    confidence_score: float
    model: str | None
    error_codes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Clamp fields to valid ranges."""
        self.duration_seconds = max(0.0, self.duration_seconds)
        self.retry_count = max(0, self.retry_count)
        self.validation_pass_rate = max(0.0, min(1.0, self.validation_pass_rate))
        self.confidence_score = max(0.0, min(1.0, self.confidence_score))


@dataclass
class PatternRecord:
    """A pattern record stored in the global database.

    v19 Evolution: Extended with quarantine_status, provenance, and trust_score
    fields to support the Pattern Quarantine & Provenance and Pattern Trust Scoring
    evolutions.
    """

    id: str
    pattern_type: str
    pattern_name: str
    description: str | None
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    last_confirmed: datetime
    led_to_success_count: int
    led_to_failure_count: int
    effectiveness_score: float
    variance: float
    suggested_action: str | None
    context_tags: list[str]
    priority_score: float

    # v19: Quarantine & Provenance fields
    quarantine_status: QuarantineStatus = QuarantineStatus.PENDING
    """Current status in the quarantine lifecycle."""

    provenance_job_hash: str | None = None
    """Hash of the job that first created this pattern."""

    provenance_sheet_num: int | None = None
    """Sheet number where this pattern was first observed."""

    quarantined_at: datetime | None = None
    """When the pattern was moved to QUARANTINED status."""

    validated_at: datetime | None = None
    """When the pattern was moved to VALIDATED status."""

    quarantine_reason: str | None = None
    """Reason for quarantine (if quarantined)."""

    # v19: Trust Scoring fields
    trust_score: float = 0.5
    """Trust score (0.0-1.0). 0.5 is neutral, >0.7 is high trust."""

    trust_calculation_date: datetime | None = None
    """When trust_score was last calculated."""

    # v22: Metacognitive Pattern Reflection fields
    success_factors: SuccessFactors | None = None
    """WHY this pattern succeeds - captured context conditions and factors."""

    success_factors_updated_at: datetime | None = None
    """When success_factors were last updated."""

    # v14 (cycle 2): Soft delete, content dedup, instrument scoping
    active: bool = True
    """Whether this pattern is active (False = soft-deleted)."""

    content_hash: str | None = None
    """SHA-256 hash of pattern content for cross-name deduplication."""

    instrument_name: str | None = None
    """Backend instrument that produced this pattern (e.g., 'claude_cli')."""

    def __post_init__(self) -> None:
        """Clamp scored fields to valid ranges."""
        self.trust_score = max(0.0, min(1.0, self.trust_score))
        self.effectiveness_score = max(0.0, min(1.0, self.effectiveness_score))
        self.variance = max(0.0, self.variance)
        self.priority_score = max(0.0, self.priority_score)


@dataclass
class ErrorRecoveryRecord:
    """A record of error recovery timing for learning adaptive waits."""

    id: str
    error_code: str
    suggested_wait: float
    actual_wait: float
    recovery_success: bool
    recorded_at: datetime
    model: str | None
    time_of_day: int  # Hour 0-23

    def __post_init__(self) -> None:
        self.time_of_day = max(0, min(23, self.time_of_day))


@dataclass
class RateLimitEvent:
    """A rate limit event for cross-workspace coordination.

    Evolution #8: Tracks rate limit events across workspaces so that
    parallel jobs can coordinate and avoid hitting the same rate limits.
    """

    id: str
    error_code: str
    model: str | None
    recorded_at: datetime
    expires_at: datetime
    source_job_hash: str
    duration_seconds: float

    def __post_init__(self) -> None:
        if self.expires_at < self.recorded_at:
            self.expires_at = self.recorded_at


@dataclass
class EscalationDecisionRecord:
    """A record of a human/AI escalation decision.

    Evolution v11: Escalation Learning Loop - records escalation decisions
    to learn from feedback over time and potentially suggest actions for
    similar future escalations.
    """

    id: str
    """Unique identifier for this escalation decision."""

    job_hash: str
    """Hash of the job that triggered escalation."""

    sheet_num: int
    """Sheet number that triggered escalation."""

    confidence: float
    """Aggregate confidence score at time of escalation (0.0-1.0)."""

    action: str
    """Action taken: retry, skip, abort, modify_prompt."""

    guidance: str | None
    """Optional guidance/notes from the escalation handler."""

    validation_pass_rate: float
    """Pass percentage of validations at escalation time."""

    retry_count: int
    """Number of retries attempted before escalation."""

    outcome_after_action: str | None = None
    """What happened after the action: success, failed, aborted, skipped."""

    recorded_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    """When the escalation decision was recorded."""

    model: str | None = None
    """Model used for execution (if relevant)."""

    _VALID_ACTIONS: ClassVar[frozenset[str]] = frozenset(
        {"retry", "skip", "abort", "modify_prompt"}
    )

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))
        if self.action not in self._VALID_ACTIONS:
            self.action = "abort"


@dataclass
class PatternDiscoveryEvent:
    """A pattern discovery event for cross-job broadcasting.

    v14 Evolution: Real-time Pattern Broadcasting - enables jobs to share
    newly discovered patterns with other concurrent jobs, so knowledge
    propagates across the ecosystem without waiting for aggregation.
    """

    id: str
    """Unique identifier for this discovery event."""

    pattern_id: str
    """ID of the pattern that was discovered."""

    pattern_name: str
    """Human-readable name of the pattern."""

    pattern_type: str
    """Type of pattern (validation_failure, retry_pattern, etc.)."""

    source_job_hash: str
    """Hash of the job that discovered the pattern."""

    recorded_at: datetime
    """When the discovery was recorded."""

    expires_at: datetime
    """When this broadcast expires (TTL-based)."""

    effectiveness_score: float
    """Effectiveness score at time of discovery."""

    context_tags: list[str] = field(default_factory=list)
    """Context tags for pattern matching."""


@dataclass
class DriftMetrics:
    """Metrics for pattern effectiveness drift detection.

    v12 Evolution: Goal Drift Detection - tracks how pattern effectiveness
    changes over time to detect drifting patterns that may need attention.
    """

    pattern_id: str
    """Pattern ID being analyzed."""

    pattern_name: str
    """Human-readable pattern name."""

    window_size: int
    """Number of applications in each comparison window."""

    effectiveness_before: float
    """Effectiveness score in the older window (applications N-2W to N-W)."""

    effectiveness_after: float
    """Effectiveness score in the recent window (applications N-W to N)."""

    grounding_confidence_avg: float
    """Average grounding confidence across all applications in analysis."""

    drift_magnitude: float
    """Absolute magnitude of drift: |effectiveness_after - effectiveness_before|."""

    drift_direction: str
    """Direction of drift: 'positive', 'negative', or 'stable'."""

    applications_analyzed: int
    """Total number of applications analyzed (should be 2 × window_size)."""

    threshold_exceeded: bool = False
    """Whether drift_magnitude exceeds the alert threshold."""


@dataclass
class EpistemicDriftMetrics:
    """Metrics for epistemic drift detection - tracking belief changes about patterns.

    v21 Evolution: Epistemic Drift Detection - tracks how confidence/belief in
    patterns changes over time, complementing effectiveness drift. While effectiveness
    drift measures outcome changes, epistemic drift measures belief evolution.

    This enables detection of belief degradation before effectiveness actually declines.
    """

    pattern_id: str
    """Pattern ID being analyzed."""

    pattern_name: str
    """Human-readable pattern name."""

    window_size: int
    """Number of applications in each comparison window."""

    confidence_before: float
    """Average grounding confidence in the older window (applications N-2W to N-W)."""

    confidence_after: float
    """Average grounding confidence in the recent window (applications N-W to N)."""

    belief_change: float
    """Change in belief/confidence: confidence_after - confidence_before."""

    belief_entropy: float
    """Entropy of confidence values (0 = consistent beliefs, 1 = high variance)."""

    applications_analyzed: int
    """Total number of applications analyzed (should be 2 × window_size)."""

    threshold_exceeded: bool = False
    """Whether belief_change magnitude exceeds the alert threshold."""

    drift_direction: str = "stable"
    """Direction of belief drift: 'strengthening', 'weakening', or 'stable'."""


@dataclass
class EvolutionEntryInput:
    """Input parameters for recording an evolution trajectory entry.

    Bundles the 11 parameters of record_evolution_entry() into a single
    object for cleaner API usage. The method still accepts individual kwargs
    for backward compatibility.
    """

    cycle: int
    """Evolution cycle number (e.g., 16 for v16)."""

    evolutions_completed: int
    """Number of evolutions completed in this cycle."""

    evolutions_deferred: int
    """Number of evolutions deferred in this cycle."""

    issue_classes: list[str]
    """Issue classes addressed (e.g., ['infrastructure_activation'])."""

    cv_avg: float
    """Average Consciousness Volume of selected evolutions."""

    implementation_loc: int
    """Total implementation LOC for this cycle."""

    test_loc: int
    """Total test LOC for this cycle."""

    loc_accuracy: float
    """LOC estimation accuracy (actual/estimated as ratio)."""

    research_candidates_resolved: int = 0
    """Number of research candidates resolved."""

    research_candidates_created: int = 0
    """Number of new research candidates created."""

    notes: str = ""
    """Optional notes about this evolution cycle."""


@dataclass
class EvolutionTrajectoryEntry:
    """A record of a single evolution cycle in Marianne's self-improvement trajectory.

    v16 Evolution: Evolution Trajectory Tracking - enables Marianne to track its
    own evolution history, identifying recurring issue classes and measuring
    improvement over time.
    """

    id: str
    """Unique identifier for this trajectory entry."""

    cycle: int
    """Evolution cycle number (e.g., 16 for v16)."""

    recorded_at: datetime
    """When this entry was recorded."""

    evolutions_completed: int
    """Number of evolutions completed in this cycle."""

    evolutions_deferred: int
    """Number of evolutions deferred in this cycle."""

    issue_classes: list[str]
    """Issue classes addressed (e.g., 'infrastructure_activation', 'epistemic_drift')."""

    cv_avg: float
    """Average Consciousness Volume of selected evolutions."""

    implementation_loc: int
    """Total implementation LOC for this cycle."""

    test_loc: int
    """Total test LOC for this cycle."""

    loc_accuracy: float
    """LOC estimation accuracy (actual/estimated as ratio)."""

    research_candidates_resolved: int = 0
    """Number of research candidates resolved in this cycle."""

    research_candidates_created: int = 0
    """Number of new research candidates created in this cycle."""

    notes: str = ""
    """Optional notes about this evolution cycle."""


@dataclass
class PatternEntropyMetrics:
    """Metrics for pattern population Shannon entropy analysis.

    Used by the `patterns-entropy` CLI command to monitor pattern diversity.
    Shannon entropy measures how evenly patterns are used across the population:
    high entropy indicates healthy diversity, low entropy signals collapse risk.
    """

    calculated_at: datetime
    """When this entropy measurement was calculated."""

    shannon_entropy: float
    """Shannon entropy in bits: -sum(p * log2(p)) for each pattern probability."""

    max_possible_entropy: float
    """Maximum possible entropy: log2(unique_pattern_count)."""

    diversity_index: float
    """Normalized diversity: shannon_entropy / max_possible_entropy (0.0-1.0)."""

    unique_pattern_count: int
    """Total number of unique patterns in the population."""

    effective_pattern_count: int
    """Number of patterns with at least one application."""

    total_applications: int
    """Total number of pattern applications across all patterns."""

    dominant_pattern_share: float
    """Fraction of applications held by the most-used pattern (0.0-1.0)."""

    threshold_exceeded: bool = False
    """Whether diversity_index fell below the alert threshold (set by caller)."""


@dataclass
class ExplorationBudgetRecord:
    """A record of exploration budget state over time.

    v23 Evolution: Exploration Budget Maintenance - tracks the dynamic
    exploration budget to prevent convergence to zero. The budget adjusts
    based on pattern entropy observations.
    """

    id: str
    """Unique identifier for this budget record."""

    job_hash: str
    """Hash of the job this budget adjustment belongs to."""

    recorded_at: datetime
    """When this budget state was recorded."""

    budget_value: float
    """Current budget value (0.0-1.0)."""

    entropy_at_time: float | None
    """Pattern entropy at time of recording (if measured)."""

    adjustment_type: str
    """Type of adjustment: 'initial', 'decay', 'boost', 'floor_enforced'."""

    adjustment_reason: str | None = None
    """Human-readable reason for this adjustment."""


@dataclass
class EntropyResponseRecord:
    """A record of an automatic entropy response event.

    v23 Evolution: Automatic Entropy Response - records when the system
    automatically responded to low entropy conditions by injecting diversity.
    """

    id: str
    """Unique identifier for this response record."""

    job_hash: str
    """Hash of the job that triggered this response."""

    recorded_at: datetime
    """When this response was triggered."""

    entropy_at_trigger: float
    """The entropy value that triggered this response."""

    threshold_used: float
    """The threshold that was crossed."""

    actions_taken: list[str]
    """List of actions taken: 'budget_boost', 'quarantine_revisit', etc."""

    budget_boosted: bool = False
    """Whether the exploration budget was boosted."""

    quarantine_revisits: int = 0
    """Number of quarantined patterns revisited."""

    patterns_revisited: list[str] = field(default_factory=list)
    """IDs of patterns that were marked for revisit."""


# Export all models for convenient importing
__all__ = [
    "QuarantineStatus",
    "SuccessFactors",
    "ExecutionRecord",
    "PatternRecord",
    "ErrorRecoveryRecord",
    "RateLimitEvent",
    "EscalationDecisionRecord",
    "PatternDiscoveryEvent",
    "DriftMetrics",
    "EpistemicDriftMetrics",
    "EvolutionTrajectoryEntry",
    "PatternEntropyMetrics",
    "ExplorationBudgetRecord",
    "EntropyResponseRecord",
]
