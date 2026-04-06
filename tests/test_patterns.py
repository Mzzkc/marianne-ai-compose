"""Tests for pattern detection and matching.

Tests the Priority 1 Evolution: Close the Learning Loop.
"""

from datetime import datetime, timedelta, timezone

import pytest

from marianne.core.checkpoint import SheetStatus
from marianne.learning.outcomes import SheetOutcome
from marianne.learning.patterns import (
    DetectedPattern,
    PatternApplicator,
    PatternDetector,
    PatternMatcher,
    PatternType,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_successful_outcome() -> SheetOutcome:
    """Create a sample successful outcome."""
    return SheetOutcome(
        sheet_id="job1-sheet1",
        job_id="job1",
        validation_results=[
            {"rule_type": "file_exists", "passed": True, "confidence": 1.0},
            {"rule_type": "content_contains", "passed": True, "confidence": 0.9},
        ],
        execution_duration=30.0,
        retry_count=0,
        completion_mode_used=False,
        final_status=SheetStatus.COMPLETED,
        validation_pass_rate=1.0,
        success_without_retry=True,
    )


@pytest.fixture
def sample_failed_outcome() -> SheetOutcome:
    """Create a sample failed outcome."""
    return SheetOutcome(
        sheet_id="job1-sheet2",
        job_id="job1",
        validation_results=[
            {"rule_type": "file_exists", "passed": False, "confidence": 1.0},
            {"rule_type": "content_contains", "passed": True, "confidence": 0.8},
        ],
        execution_duration=45.0,
        retry_count=2,
        completion_mode_used=True,
        final_status=SheetStatus.FAILED,
        validation_pass_rate=0.5,
        success_without_retry=False,
    )


@pytest.fixture
def sample_retry_success_outcome() -> SheetOutcome:
    """Create an outcome that succeeded after retries."""
    return SheetOutcome(
        sheet_id="job1-sheet3",
        job_id="job1",
        validation_results=[
            {"rule_type": "file_exists", "passed": True, "confidence": 1.0},
        ],
        execution_duration=60.0,
        retry_count=1,
        completion_mode_used=False,
        final_status=SheetStatus.COMPLETED,
        validation_pass_rate=1.0,
        success_without_retry=False,
    )


@pytest.fixture
def multiple_outcomes(
    sample_successful_outcome: SheetOutcome,
    sample_failed_outcome: SheetOutcome,
    sample_retry_success_outcome: SheetOutcome,
) -> list[SheetOutcome]:
    """Create multiple outcomes for pattern detection."""
    # Create more outcomes to trigger pattern detection
    outcomes = [sample_successful_outcome, sample_failed_outcome, sample_retry_success_outcome]

    # Add more failed file_exists validations to create a pattern
    for i in range(3):
        outcomes.append(
            SheetOutcome(
                sheet_id=f"job1-sheet{i+4}",
                job_id="job1",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False, "confidence": 1.0},
                ],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            )
        )

    # Add more first-attempt successes
    for i in range(4):
        outcomes.append(
            SheetOutcome(
                sheet_id=f"job1-sheet{i+7}",
                job_id="job1",
                validation_results=[
                    {"rule_type": "file_exists", "passed": True, "confidence": 1.0},
                ],
                execution_duration=25.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=True,
            )
        )

    return outcomes


# =============================================================================
# TestPatternDetector
# =============================================================================


class TestPatternDetector:
    """Tests for PatternDetector class."""

    def test_detect_validation_patterns(self, multiple_outcomes: list[SheetOutcome]) -> None:
        """Test detection of recurring validation failure patterns."""
        detector = PatternDetector(multiple_outcomes)
        patterns = detector.detect_all()

        # Should detect file_exists validation failures (seen 4 times)
        validation_patterns = [
            p for p in patterns if p.pattern_type == PatternType.VALIDATION_FAILURE
        ]
        assert len(validation_patterns) >= 1

        file_exists_pattern = next(
            (p for p in validation_patterns if "file_exists" in p.description),
            None,
        )
        assert file_exists_pattern is not None
        assert file_exists_pattern.frequency >= 2

    def test_detect_retry_patterns(self, multiple_outcomes: list[SheetOutcome]) -> None:
        """Test detection of retry success patterns."""
        # Add more retry successes
        outcomes = multiple_outcomes + [
            SheetOutcome(
                sheet_id="job1-retry1",
                job_id="job1",
                validation_results=[{"rule_type": "file_exists", "passed": True}],
                execution_duration=50.0,
                retry_count=2,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=False,
            )
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        retry_patterns = [
            p for p in patterns if p.pattern_type == PatternType.RETRY_SUCCESS
        ]
        assert len(retry_patterns) >= 1

    def test_empty_outcomes_returns_empty(self) -> None:
        """Test that empty outcomes returns empty patterns list."""
        detector = PatternDetector([])
        patterns = detector.detect_all()
        assert patterns == []

    def test_success_rate_calculation(self, multiple_outcomes: list[SheetOutcome]) -> None:
        """Test success rate calculation."""
        success_rate = PatternDetector.calculate_success_rate(multiple_outcomes)
        # 6 successful (1 original + 1 retry_success + 4 first_attempt) out of 10 total
        assert success_rate == pytest.approx(0.6)

    def test_detect_success_patterns(self, multiple_outcomes: list[SheetOutcome]) -> None:
        """Test detection of first-attempt success patterns."""
        detector = PatternDetector(multiple_outcomes)
        patterns = detector.detect_all()

        success_patterns = [
            p for p in patterns if p.pattern_type == PatternType.SUCCESS_WITHOUT_RETRY
        ]
        # Should detect first-attempt success pattern (5 successes)
        assert len(success_patterns) >= 1

    def test_detect_completion_patterns(self) -> None:
        """Test detection of completion mode patterns."""
        outcomes = [
            SheetOutcome(
                sheet_id=f"job1-sheet{i}",
                job_id="job1",
                validation_results=[{"rule_type": "file_exists", "passed": True}],
                execution_duration=50.0,
                retry_count=1,
                completion_mode_used=True,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=False,
            )
            for i in range(3)
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        completion_patterns = [
            p for p in patterns if p.pattern_type == PatternType.COMPLETION_MODE
        ]
        assert len(completion_patterns) >= 1

    def test_detect_low_confidence_patterns(self) -> None:
        """Test detection of low confidence validation patterns."""
        outcomes = [
            SheetOutcome(
                sheet_id=f"job1-sheet{i}",
                job_id="job1",
                validation_results=[
                    {"rule_type": "content_regex", "passed": True, "confidence": 0.5},
                    {"rule_type": "file_exists", "passed": True, "confidence": 0.6},
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=True,
            )
            for i in range(3)
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        low_conf_patterns = [
            p for p in patterns if p.pattern_type == PatternType.LOW_CONFIDENCE
        ]
        # Should detect low confidence pattern (6 low-confidence validations)
        assert len(low_conf_patterns) >= 1


# =============================================================================
# TestPatternMatcher
# =============================================================================


class TestPatternMatcher:
    """Tests for PatternMatcher class."""

    @pytest.fixture
    def sample_patterns(self) -> list[DetectedPattern]:
        """Create sample patterns for matching tests."""
        return [
            DetectedPattern(
                pattern_type=PatternType.VALIDATION_FAILURE,
                description="file_exists validation tends to fail",
                frequency=5,
                success_rate=0.0,
                context_tags=["validation:file_exists"],
                confidence=0.7,
            ),
            DetectedPattern(
                pattern_type=PatternType.RETRY_SUCCESS,
                description="Retrying works: avg 1.5 retries lead to success",
                frequency=3,
                success_rate=1.0,
                context_tags=["retry:effective"],
                confidence=0.6,
            ),
            DetectedPattern(
                pattern_type=PatternType.SUCCESS_WITHOUT_RETRY,
                description="First-attempt success rate: 70%",
                frequency=7,
                success_rate=0.7,
                context_tags=["success:first_attempt"],
                confidence=0.8,
            ),
        ]

    def test_match_by_context_tags(self, sample_patterns: list[DetectedPattern]) -> None:
        """Test matching patterns by context tags."""
        matcher = PatternMatcher(sample_patterns)

        context = {"tags": ["validation:file_exists"]}
        matched = matcher.match(context, limit=5)

        # Should include the file_exists pattern
        assert len(matched) >= 1
        file_exists_matched = any(
            "file_exists" in p.description for p in matched
        )
        assert file_exists_matched

    def test_relevance_scoring(self, sample_patterns: list[DetectedPattern]) -> None:
        """Test that patterns are scored and sorted by relevance."""
        matcher = PatternMatcher(sample_patterns)

        context = {"tags": []}
        matched = matcher.match(context, limit=5)

        # Should return patterns sorted by score (highest first)
        # Higher confidence patterns should come first
        assert len(matched) >= 1

    def test_limit_results(self, sample_patterns: list[DetectedPattern]) -> None:
        """Test that limit parameter works correctly."""
        matcher = PatternMatcher(sample_patterns)

        context = {}
        matched = matcher.match(context, limit=1)

        assert len(matched) <= 1

    def test_empty_patterns_returns_empty(self) -> None:
        """Test that empty patterns returns empty matches."""
        matcher = PatternMatcher([])
        matched = matcher.match({}, limit=5)
        assert matched == []

    def test_recency_affects_score(self, sample_patterns: list[DetectedPattern]) -> None:
        """Test that more recent patterns score higher."""
        # Create two patterns: one recent, one old
        recent = DetectedPattern(
            pattern_type=PatternType.RETRY_SUCCESS,
            description="Recent pattern",
            frequency=1,
            confidence=0.5,
            last_seen=datetime.now(tz=timezone.utc),
        )
        old = DetectedPattern(
            pattern_type=PatternType.RETRY_SUCCESS,
            description="Old pattern",
            frequency=1,
            confidence=0.5,
            last_seen=datetime.now(tz=timezone.utc) - timedelta(days=30),
        )

        matcher = PatternMatcher([old, recent])
        matched = matcher.match({}, limit=2)

        # Recent pattern should score higher and come first
        assert len(matched) == 2
        assert matched[0].description == "Recent pattern"


# =============================================================================
# TestPatternApplicator
# =============================================================================


class TestPatternApplicator:
    """Tests for PatternApplicator class."""

    @pytest.fixture
    def sample_patterns(self) -> list[DetectedPattern]:
        """Create sample patterns for applicator tests."""
        return [
            DetectedPattern(
                pattern_type=PatternType.VALIDATION_FAILURE,
                description="file_exists validation tends to fail",
                frequency=5,
            ),
            DetectedPattern(
                pattern_type=PatternType.RETRY_SUCCESS,
                description="Retry with 2 attempts usually works",
                frequency=3,
                success_rate=0.85,
            ),
        ]

    def test_generate_prompt_section(self, sample_patterns: list[DetectedPattern]) -> None:
        """Test generating a prompt section from patterns."""
        applicator = PatternApplicator(sample_patterns)
        section = applicator.generate_prompt_section()

        assert "## Learned Patterns" in section
        assert "file_exists" in section
        assert "Retry" in section

    def test_empty_patterns_returns_empty(self) -> None:
        """Test that empty patterns returns empty section."""
        applicator = PatternApplicator([])
        section = applicator.generate_prompt_section()
        assert section == ""

    def test_get_pattern_descriptions(self, sample_patterns: list[DetectedPattern]) -> None:
        """Test getting pattern descriptions as list."""
        applicator = PatternApplicator(sample_patterns)
        descriptions = applicator.get_pattern_descriptions()

        assert len(descriptions) == 2
        assert any("file_exists" in d for d in descriptions)

    def test_pattern_to_prompt_guidance(self) -> None:
        """Test individual pattern to prompt guidance conversion."""
        patterns = [
            DetectedPattern(
                pattern_type=PatternType.VALIDATION_FAILURE,
                description="Test failure pattern",
                frequency=3,
            ),
            DetectedPattern(
                pattern_type=PatternType.RETRY_SUCCESS,
                description="Test retry pattern",
                frequency=2,
                success_rate=0.9,
            ),
            DetectedPattern(
                pattern_type=PatternType.SUCCESS_WITHOUT_RETRY,
                description="Test success pattern",
                frequency=5,
            ),
            DetectedPattern(
                pattern_type=PatternType.LOW_CONFIDENCE,
                description="Test low confidence",
                frequency=2,
            ),
        ]

        for pattern in patterns:
            guidance = pattern.to_prompt_guidance()
            assert guidance  # Should not be empty
            assert pattern.description in guidance or "Test" in guidance


# =============================================================================
# TestDetectedPattern
# =============================================================================


class TestDetectedPattern:
    """Tests for DetectedPattern dataclass."""

    def test_to_prompt_guidance_validation_failure(self) -> None:
        """Test prompt guidance for validation failure pattern."""
        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="file_exists validation fails",
            frequency=5,
        )
        guidance = pattern.to_prompt_guidance()

        assert "⚠️" in guidance
        assert "Common issue" in guidance
        assert "5x" in guidance

    def test_to_prompt_guidance_retry_success(self) -> None:
        """Test prompt guidance for retry success pattern."""
        pattern = DetectedPattern(
            pattern_type=PatternType.RETRY_SUCCESS,
            description="Retry after file creation works",
            frequency=3,
            success_rate=0.85,
        )
        guidance = pattern.to_prompt_guidance()

        assert "✓" in guidance
        assert "Tip" in guidance
        assert "85%" in guidance

    def test_to_prompt_guidance_success_without_retry(self) -> None:
        """Test prompt guidance for first-attempt success pattern."""
        pattern = DetectedPattern(
            pattern_type=PatternType.SUCCESS_WITHOUT_RETRY,
            description="Following spec exactly works",
            frequency=10,
        )
        guidance = pattern.to_prompt_guidance()

        assert "✓" in guidance
        assert "Best practice" in guidance

    def test_default_values(self) -> None:
        """Test DetectedPattern default values."""
        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="Test pattern",
        )

        assert pattern.frequency == 1
        assert pattern.success_rate == 0.0
        assert pattern.confidence == 0.5
        assert pattern.context_tags == []
        assert pattern.evidence == []

    def test_semantic_failure_guidance(self) -> None:
        """Test prompt guidance for semantic failure pattern."""
        pattern = DetectedPattern(
            pattern_type=PatternType.SEMANTIC_FAILURE,
            description="'stale' failures are common",
            frequency=5,
        )
        guidance = pattern.to_prompt_guidance()

        assert "🔍" in guidance
        assert "Semantic insight" in guidance
        assert "stale" in guidance
        assert "5x" in guidance


# =============================================================================
# TestSemanticPatterns
# =============================================================================


class TestSemanticPatterns:
    """Tests for semantic pattern detection (Evolution: Deep Validation-Learning)."""

    def test_detect_semantic_category_patterns(self) -> None:
        """Test detection of patterns from failure_category."""
        outcomes = [
            SheetOutcome(
                sheet_id=f"job1-sheet{i}",
                job_id="job1",
                validation_results=[
                    {
                        "rule_type": "file_exists",
                        "passed": False,
                        "confidence": 1.0,
                        "failure_category": "missing",
                        "failure_reason": "File was not created",
                    },
                ],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            )
            for i in range(3)
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        semantic_patterns = [
            p for p in patterns if p.pattern_type == PatternType.SEMANTIC_FAILURE
        ]
        assert len(semantic_patterns) >= 1

        # Should detect 'missing' category pattern
        missing_pattern = next(
            (p for p in semantic_patterns if "missing" in p.description.lower()),
            None,
        )
        assert missing_pattern is not None
        assert missing_pattern.frequency >= 2

    def test_detect_semantic_reason_patterns(self) -> None:
        """Test detection of patterns from failure_reason normalization."""
        # Use a failure_reason that will match one of our normalized patterns
        outcomes = [
            SheetOutcome(
                sheet_id=f"job1-sheet{i}",
                job_id="job1",
                validation_results=[
                    {
                        "rule_type": "content_contains",
                        "passed": False,
                        "confidence": 1.0,
                        "failure_category": "malformed",
                        # "pattern not found" will match
                        "failure_reason": "Pattern not found in the output",
                    },
                ],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            )
            for i in range(3)
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        semantic_patterns = [
            p for p in patterns if p.pattern_type == PatternType.SEMANTIC_FAILURE
        ]
        assert len(semantic_patterns) >= 1

        # Should detect 'pattern not found' reason pattern (normalized form)
        reason_pattern = next(
            (p for p in semantic_patterns if "pattern not found" in p.description.lower()),
            None,
        )
        assert reason_pattern is not None

    def test_detect_fix_suggestion_patterns(self) -> None:
        """Test detection of patterns from recurring fix suggestions."""
        fix_text = "Ensure file is created in workspace/"
        outcomes = [
            SheetOutcome(
                sheet_id=f"job1-sheet{i}",
                job_id="job1",
                validation_results=[
                    {
                        "rule_type": "file_exists",
                        "passed": False,
                        "confidence": 1.0,
                        "failure_category": "missing",
                        "failure_reason": "File not created",
                        "suggested_fix": fix_text,
                    },
                ],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
            )
            for i in range(4)  # Need >= 3 to trigger pattern
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        semantic_patterns = [
            p for p in patterns if p.pattern_type == PatternType.SEMANTIC_FAILURE
        ]

        # Should detect fix suggestion pattern
        fix_pattern = next(
            (p for p in semantic_patterns if "suggested fix" in p.description.lower()),
            None,
        )
        assert fix_pattern is not None
        assert fix_pattern.frequency >= 3

    def test_outcome_semantic_fields_persistence(self) -> None:
        """Test that semantic fields are properly stored in SheetOutcome."""
        outcome = SheetOutcome(
            sheet_id="job1-sheet1",
            job_id="job1",
            validation_results=[],
            execution_duration=30.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=1.0,
            success_without_retry=True,
            failure_category_counts={"missing": 2, "stale": 1},
            semantic_patterns=["file not created", "content empty"],
            fix_suggestions=["Add proper import", "Create directory first"],
        )

        assert outcome.failure_category_counts == {"missing": 2, "stale": 1}
        assert outcome.semantic_patterns == ["file not created", "content empty"]
        assert len(outcome.fix_suggestions) == 2

    def test_normalize_failure_reason(self) -> None:
        """Test failure_reason normalization for aggregation."""
        detector = PatternDetector([])

        # Test common pattern extraction - patterns must contain exact substring
        assert (
            detector._normalize_failure_reason("file not created during execution")
            == "file not created"
        )
        assert (
            detector._normalize_failure_reason("Error: pattern not found in output")
            == "pattern not found"
        )
        assert (
            detector._normalize_failure_reason("command failed with exit code 1")
            == "command failed"
        )
        assert detector._normalize_failure_reason("Timeout after 60 seconds") == "timeout"

        # Test that phrases only partially matching get full normalized string
        # (when under 50 chars)
        result = detector._normalize_failure_reason("short error")
        assert result == "short error"

        # Test empty handling
        assert detector._normalize_failure_reason("") == ""

        # Test whitespace-only is normalized to empty
        result = detector._normalize_failure_reason("   ")
        # After strip, whitespace becomes ""
        assert result == ""

        # Test very long strings without keywords are ignored (too specific)
        long_reason = "A" * 100
        assert detector._normalize_failure_reason(long_reason) == ""

        # Test that long strings WITH keywords extract the keyword (Issue #8 fix)
        long_with_timeout = (
            "connection to database at localhost:5432 failed with timeout after 30s"
        )
        assert detector._normalize_failure_reason(long_with_timeout) == "timeout"

        long_with_rate_limit = (
            "API request failed: rate limit exceeded after 5 retries,"
            " please wait before trying again"
        )
        assert detector._normalize_failure_reason(long_with_rate_limit) == "rate limit"

        long_with_connection = (
            "Unable to establish connection to the remote server"
            " at https://api.example.com"
        )
        assert detector._normalize_failure_reason(long_with_connection) == "connection"

        # Test new keywords added in Issue #8 fix
        assert (
            detector._normalize_failure_reason("connection refused by host")
            == "connection refused"
        )
        assert detector._normalize_failure_reason("authentication required") == "authentication"
        assert detector._normalize_failure_reason("access denied for user") == "access denied"

    def test_semantic_patterns_with_pre_aggregated_data(self) -> None:
        """Test semantic pattern detection using pre-aggregated SheetOutcome fields."""
        outcomes = [
            SheetOutcome(
                sheet_id="job1-sheet1",
                job_id="job1",
                validation_results=[],  # Empty - using pre-aggregated instead
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
                failure_category_counts={"stale": 3},
                semantic_patterns=["file not modified"],
            ),
            SheetOutcome(
                sheet_id="job1-sheet2",
                job_id="job1",
                validation_results=[],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
                failure_category_counts={"stale": 2},
                semantic_patterns=["file not modified"],
            ),
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        semantic_patterns = [
            p for p in patterns if p.pattern_type == PatternType.SEMANTIC_FAILURE
        ]
        assert len(semantic_patterns) >= 1

        # Should detect 'stale' pattern from pre-aggregated counts (3+2=5)
        stale_pattern = next(
            (p for p in semantic_patterns if "stale" in p.description.lower()),
            None,
        )
        assert stale_pattern is not None
        assert stale_pattern.frequency >= 5


# =============================================================================
# TestPatternEffectiveness (Evolution: Pattern Effectiveness Tracking)
# =============================================================================


class TestPatternEffectiveness:
    """Tests for pattern effectiveness tracking."""

    def test_effectiveness_rate_default_with_few_applications(self) -> None:
        """Test that effectiveness_rate returns 0.4 with < 3 applications.

        Returns 0.4 (slightly below neutral) to prefer proven patterns
        over unproven ones.
        """
        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test pattern",
            applications=2,
            successes_after_application=2,  # Would be 100% but too few samples
        )

        # Should return default 0.4 because applications < 3
        # 0.4 is below neutral to slightly penalize unproven patterns
        assert pattern.effectiveness_rate == 0.4

    def test_effectiveness_rate_calculated_with_enough_applications(self) -> None:
        """Test that effectiveness_rate is calculated with >= 3 applications."""
        pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test pattern",
            applications=5,
            successes_after_application=3,
        )

        # 3/5 = 0.6
        assert pattern.effectiveness_rate == 0.6

    def test_effectiveness_weight_ramps_up(self) -> None:
        """Test that effectiveness_weight increases with applications."""
        pattern_0 = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            applications=0,
        )
        pattern_2 = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            applications=2,
        )
        pattern_5 = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            applications=5,
        )
        pattern_10 = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="test",
            applications=10,
        )

        assert pattern_0.effectiveness_weight == 0.0
        assert pattern_2.effectiveness_weight == 0.4
        assert pattern_5.effectiveness_weight == 1.0
        assert pattern_10.effectiveness_weight == 1.0  # Capped at 1.0

    def test_effectiveness_calculated_from_patterns_applied(self) -> None:
        """Test that detector calculates effectiveness from patterns_applied."""
        # Create outcomes where a specific pattern was applied
        # The pattern description must match exactly what to_prompt_guidance() generates
        # With 4 outcomes that have file_exists failures, frequency=4, so "(seen 4x)"
        pattern_desc = "⚠️ Common issue: 'file_exists' validation tends to fail (seen 4x)"

        outcomes = [
            # Outcome 1: pattern applied, succeeded
            SheetOutcome(
                sheet_id="job1-sheet1",
                job_id="job1",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=0.0,
                success_without_retry=True,
                patterns_applied=[pattern_desc],
            ),
            # Outcome 2: pattern applied, succeeded
            SheetOutcome(
                sheet_id="job1-sheet2",
                job_id="job1",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=0.0,
                success_without_retry=True,
                patterns_applied=[pattern_desc],
            ),
            # Outcome 3: pattern applied, failed
            SheetOutcome(
                sheet_id="job1-sheet3",
                job_id="job1",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
                execution_duration=30.0,
                retry_count=1,
                completion_mode_used=False,
                final_status=SheetStatus.FAILED,
                validation_pass_rate=0.0,
                success_without_retry=False,
                patterns_applied=[pattern_desc],
            ),
            # Outcome 4: pattern applied, succeeded
            SheetOutcome(
                sheet_id="job1-sheet4",
                job_id="job1",
                validation_results=[
                    {"rule_type": "file_exists", "passed": False}
                ],
                execution_duration=30.0,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=0.0,
                success_without_retry=True,
                patterns_applied=[pattern_desc],
            ),
        ]

        detector = PatternDetector(outcomes)
        patterns = detector.detect_all()

        # Find the file_exists pattern
        file_exists_pattern = next(
            (
                p
                for p in patterns
                if p.pattern_type == PatternType.VALIDATION_FAILURE
                and "file_exists" in p.description
            ),
            None,
        )

        assert file_exists_pattern is not None
        # The pattern_desc matches the generated guidance, so should be tracked
        # 4 applications, 3 successes = 75%
        assert file_exists_pattern.applications == 4
        assert file_exists_pattern.successes_after_application == 3
        assert file_exists_pattern.effectiveness_rate == 0.75

    def test_effective_patterns_weighted_higher_in_matching(self) -> None:
        """Test that patterns with high effectiveness score higher in matching."""
        # Create two patterns with same base properties but different effectiveness
        low_eff_pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="low effectiveness pattern",
            frequency=5,
            confidence=0.7,
            applications=10,
            successes_after_application=2,  # 20% effectiveness
        )
        high_eff_pattern = DetectedPattern(
            pattern_type=PatternType.VALIDATION_FAILURE,
            description="high effectiveness pattern",
            frequency=5,
            confidence=0.7,
            applications=10,
            successes_after_application=9,  # 90% effectiveness
        )

        matcher = PatternMatcher([low_eff_pattern, high_eff_pattern])
        matched = matcher.match({}, limit=2)

        # High effectiveness pattern should rank higher
        assert len(matched) == 2
        assert matched[0].description == "high effectiveness pattern"

    def test_patterns_applied_recorded_in_outcome(self) -> None:
        """Test that patterns_applied field is properly stored."""
        outcome = SheetOutcome(
            sheet_id="job1-sheet1",
            job_id="job1",
            validation_results=[],
            execution_duration=30.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=1.0,
            success_without_retry=True,
            patterns_applied=[
                "⚠️ Common issue: test pattern (seen 3x)",
                "✓ Tip: another pattern (works 80% of the time)",
            ],
        )

        assert len(outcome.patterns_applied) == 2
        assert "Common issue" in outcome.patterns_applied[0]

    def test_zero_applications_returns_default_effectiveness(self) -> None:
        """Test that zero applications returns default 0.4 effectiveness.

        Returns 0.4 (slightly below neutral) to prefer proven patterns
        over completely untested ones.
        """
        pattern = DetectedPattern(
            pattern_type=PatternType.RETRY_SUCCESS,
            description="untested pattern",
            applications=0,
            successes_after_application=0,
        )

        assert pattern.effectiveness_rate == 0.4
        assert pattern.effectiveness_weight == 0.0
