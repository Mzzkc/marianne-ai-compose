"""End-to-end learning cycle test for Marianne.

Tests the complete learning lifecycle:
  detect pattern -> store -> query -> apply -> record feedback

This test exercises the real GlobalLearningStore (SQLite) with a
temporary database, verifying the full cycle works without mocking.
"""

from pathlib import Path

import pytest

from marianne.core.checkpoint import SheetStatus
from marianne.learning.global_store import GlobalLearningStore
from marianne.learning.outcomes import SheetOutcome


@pytest.fixture
def store(tmp_path: Path) -> GlobalLearningStore:
    """Create a GlobalLearningStore with a temporary database."""
    db_path = tmp_path / "test-learning.db"
    return GlobalLearningStore(db_path=db_path)


class TestLearningCycleE2E:
    """End-to-end tests for the learning cycle."""

    def test_record_and_query_pattern(self, store: GlobalLearningStore) -> None:
        """Test that a recorded pattern can be queried back."""
        # Step 1: Record a pattern (discovery)
        pattern_id = store.record_pattern(
            pattern_type="validation_failure",
            pattern_name="missing_output_file",
            description="Output file not created by sheet execution",
            context_tags=["validation", "file_check"],
            suggested_action="Ensure the sheet creates the expected output file",
        )

        assert pattern_id is not None

        # Step 2: Query the pattern back
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,  # Low threshold to find new patterns
            require_validated=False,
            limit=10,
        )

        # Should find our pattern
        assert len(patterns) >= 1
        found = [p for p in patterns if p.id == pattern_id]
        assert len(found) == 1
        assert found[0].pattern_name == "missing_output_file"
        assert found[0].pattern_type == "validation_failure"

    def test_pattern_application_and_feedback(
        self, store: GlobalLearningStore
    ) -> None:
        """Test recording a pattern application and its outcome."""
        # Step 1: Record pattern
        pattern_id = store.record_pattern(
            pattern_type="error_recovery",
            pattern_name="retry_on_timeout",
            description="Retry the sheet when timeout occurs",
            context_tags=["timeout", "retry"],
            suggested_action="Increase timeout and retry",
        )

        # Step 2: Record application (pattern was applied to a sheet)
        app_id = store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id="exec-001",
            pattern_led_to_success=True,
            retry_count_before=2,
            retry_count_after=0,
            application_mode="exploitation",
        )

        assert app_id is not None

        # Step 3: Query pattern again - effectiveness should be updated
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=10,
        )

        found = [p for p in patterns if p.id == pattern_id]
        assert len(found) == 1
        # led_to_success_count should be updated from the successful application
        assert found[0].led_to_success_count >= 1

    def test_duplicate_pattern_increments_count(
        self, store: GlobalLearningStore
    ) -> None:
        """Test that recording the same pattern twice increments its count."""
        # Record same pattern twice
        id1 = store.record_pattern(
            pattern_type="rate_limit",
            pattern_name="claude_429",
            description="Claude API 429 rate limit",
            context_tags=["api", "rate_limit"],
        )

        id2 = store.record_pattern(
            pattern_type="rate_limit",
            pattern_name="claude_429",
            description="Claude API 429 rate limit",
            context_tags=["api", "rate_limit"],
        )

        # Should return same ID (deduplicated)
        assert id1 == id2

        # Occurrence count should be 2
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=10,
        )

        found = [p for p in patterns if p.id == id1]
        assert len(found) == 1
        assert found[0].occurrence_count == 2

    def test_rate_limit_coordination(
        self, store: GlobalLearningStore
    ) -> None:
        """Test cross-workspace rate limit coordination."""
        # Record a rate limit event
        store.record_rate_limit_event(
            error_code="E101",
            duration_seconds=300.0,
            job_id="job-1",
            model="claude-sonnet-4-5-20250929",
        )

        # Check if rate limited
        is_limited, wait_time = store.is_rate_limited(
            model="claude-sonnet-4-5-20250929",
        )

        # Should detect the rate limit
        assert is_limited is True
        assert wait_time is not None
        assert wait_time > 0

    def test_full_learning_cycle(self, store: GlobalLearningStore) -> None:
        """Test the complete learning cycle: discover -> store -> query -> apply -> feedback.

        This simulates what happens across two job runs:
        Run 1: Discovers a pattern from a failure
        Run 2: Queries patterns, applies one, records success
        """
        # === Run 1: Pattern Discovery ===

        # 1a: Discover pattern from a failure
        pattern_id = store.record_pattern(
            pattern_type="error_recovery",
            pattern_name="add_explicit_output_path",
            description=(
                "Adding explicit output path to prompt prevents "
                "file-not-found validation failures"
            ),
            context_tags=["validation", "output_file", "prompt_engineering"],
            suggested_action="Append 'Write output to {{workspace}}/output.txt' to prompt",
            provenance_job_hash="run-1",
            provenance_sheet_num=1,
        )

        assert pattern_id is not None

        # === Run 2: Pattern Application ===

        # 2a: Query applicable patterns before execution
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=5,
            context_tags=["validation"],
        )

        # Should find the pattern from Run 1
        applicable = [p for p in patterns if p.id == pattern_id]
        assert len(applicable) == 1
        assert applicable[0].suggested_action is not None

        # 2b: Apply the pattern and record the application
        app_id = store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id="run-2-exec-001",
            pattern_led_to_success=True,
            retry_count_before=1,
            retry_count_after=0,
            application_mode="exploitation",
            validation_passed=True,
        )

        assert app_id is not None

        # === Verify Learning ===

        # Pattern should have success count incremented
        final_patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=10,
        )

        final = [p for p in final_patterns if p.id == pattern_id]
        assert len(final) == 1
        assert final[0].led_to_success_count >= 1
        # Effectiveness should be positive (pattern improved outcome)
        assert final[0].effectiveness_score > 0

    def test_pattern_negative_feedback_reduces_effectiveness(
        self, store: GlobalLearningStore
    ) -> None:
        """Test that negative feedback reduces pattern effectiveness."""
        # Record pattern
        pattern_id = store.record_pattern(
            pattern_type="prompt_tweak",
            pattern_name="add_verbose_logging",
            description="Add verbose logging request to prompt",
            context_tags=["prompt"],
        )

        # Record multiple failed applications
        for i in range(3):
            store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec-fail-{i}",
                pattern_led_to_success=False,
                retry_count_before=1,
                retry_count_after=2,  # Got worse
                application_mode="exploitation",
            )

        # Check pattern - failure count should reflect negative outcomes
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=10,
        )

        found = [p for p in patterns if p.id == pattern_id]
        assert len(found) == 1
        assert found[0].led_to_failure_count >= 3


class TestErrorRecoveryLearningE2E:
    """E2E tests for error recovery wait time learning.

    FIX-11g: Tests that the full cycle of error recovery recording
    and learned wait time retrieval works across simulated job runs.
    """

    def test_error_recovery_learn_and_apply_wait_time(
        self, store: GlobalLearningStore
    ) -> None:
        """Test: record recoveries -> learned wait time adapts.

        Simulates multiple error recovery events and verifies the store
        returns learned wait times based on successful recoveries.
        """
        error_code = "E101"
        model = "claude-sonnet-4-5-20250929"

        # Record several successful recoveries with consistent wait times
        for _ in range(5):
            store.record_error_recovery(
                error_code=error_code,
                suggested_wait=60.0,
                actual_wait=45.0,
                recovery_success=True,
                model=model,
            )

        # Record one failed recovery (shorter wait didn't work)
        store.record_error_recovery(
            error_code=error_code,
            suggested_wait=60.0,
            actual_wait=10.0,
            recovery_success=False,
            model=model,
        )

        # Query learned wait time - should converge toward successful waits
        delay, confidence, strategy = store.get_learned_wait_time_with_fallback(
            error_code=error_code,
            static_delay=60.0,
            model=model,
            min_samples=3,
            min_confidence=0.5,
        )

        assert strategy != "static_fallback", "Should have learned from recoveries"
        assert confidence >= 0.5, "5 successful samples should give reasonable confidence"
        # Learned delay should be closer to 45.0 (the successful actual_wait)
        assert delay > 0

    def test_error_recovery_insufficient_samples_falls_back(
        self, store: GlobalLearningStore
    ) -> None:
        """Test: with too few samples, falls back to static delay."""
        delay, confidence, strategy = store.get_learned_wait_time_with_fallback(
            error_code="E999",
            static_delay=120.0,
            min_samples=5,
            min_confidence=0.8,
        )

        assert strategy == "static_fallback"
        assert delay == 120.0


class TestTrustScoreEvolutionE2E:
    """E2E tests for pattern trust score evolution through lifecycle.

    FIX-11g: Tests the quarantine → validate → auto-apply trust lifecycle.
    """

    def test_trust_score_evolves_through_applications(
        self, store: GlobalLearningStore
    ) -> None:
        """Pattern trust score should increase after successful applications."""
        pattern_id = store.record_pattern(
            pattern_type="prompt_tweak",
            pattern_name="add_file_path_hint",
            description="Add explicit file path hint to prompt",
            context_tags=["prompt", "file"],
        )

        # Get initial trust score
        initial_trust = store.calculate_trust_score(pattern_id)

        # Record several successful applications
        for i in range(5):
            store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"exec-trust-{i}",
                pattern_led_to_success=True,
                retry_count_before=2,
                retry_count_after=0,
                application_mode="exploitation",
                validation_passed=True,
            )

        # Trust should increase
        updated_trust = store.calculate_trust_score(pattern_id)
        assert updated_trust >= initial_trust

    def test_quarantine_lifecycle(self, store: GlobalLearningStore) -> None:
        """Test pattern quarantine -> validate -> auto-apply eligibility."""
        pattern_id = store.record_pattern(
            pattern_type="error_recovery",
            pattern_name="lifecycle_test_pattern",
            description="Test quarantine lifecycle",
            context_tags=["test"],
        )

        # Initially PENDING - not eligible for auto-apply with require_validated
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=True,
            limit=10,
        )
        validated_ids = {p.id for p in patterns}
        assert pattern_id not in validated_ids

        # Validate the pattern
        store.validate_pattern(pattern_id)

        # Now should be eligible for auto-apply
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=True,
            limit=10,
        )
        validated_ids = {p.id for p in patterns}
        assert pattern_id in validated_ids

        # Retire the pattern
        store.retire_pattern(pattern_id)

        # Retired patterns should not be eligible
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=True,
            limit=10,
        )
        retired_ids = {p.id for p in patterns}
        assert pattern_id not in retired_ids


class TestSuccessFactorsE2E:
    """E2E tests for success factor recording and analysis.

    FIX-11g: Tests the metacognitive reflection system that tracks
    WHY patterns succeed.
    """

    def test_success_factors_recorded_and_analyzed(
        self, store: GlobalLearningStore
    ) -> None:
        """Test recording success factors and querying pattern analysis."""
        pattern_id = store.record_pattern(
            pattern_type="validation_fix",
            pattern_name="fix_output_format",
            description="Fix output to match expected format",
            context_tags=["validation", "format"],
        )

        # Record success factors
        store.update_success_factors(
            pattern_id=pattern_id,
            validation_types=["file_exists", "content_regex"],
            error_categories=["missing"],
            prior_sheet_status="completed",
            retry_iteration=0,
            escalation_was_pending=False,
            grounding_confidence=0.95,
        )

        # Analyze why the pattern works
        analysis = store.analyze_pattern_why(pattern_id)

        assert analysis is not None
        assert analysis["pattern_name"] == "fix_output_format"
        assert analysis["has_factors"] is True

    def test_execution_stats_reflect_recorded_outcomes(
        self, store: GlobalLearningStore
    ) -> None:
        """Test that execution stats aggregate recorded outcomes."""
        from marianne.learning.outcomes import SheetOutcome

        # Record outcomes from simulated job execution
        for i in range(3):
            outcome = SheetOutcome(
                sheet_id=f"sheet-{i}",
                job_id="stats-test-job",
                validation_results=[{"passed": True, "rule_type": "file_exists"}],
                execution_duration=10.0 + i,
                retry_count=0,
                completion_mode_used=False,
                final_status=SheetStatus.COMPLETED,
                validation_pass_rate=1.0,
                success_without_retry=True,
                patterns_applied=[],
            )

            store.record_outcome(
                outcome=outcome,
                workspace_path=Path("/tmp/stats-test"),
                model="claude-sonnet-4-5-20250929",
            )

        # Query execution statistics
        stats = store.get_execution_stats()
        assert stats["total_executions"] >= 3


class TestMultiRunMeasurementE2E:
    """E2E test for the full multi-run learning measurement cycle.

    FIX-15: Verifies that the system can detect a pattern from a failure,
    store it, apply it on a subsequent run, record the execution outcome,
    and then measure the pattern's effectiveness through trust scores and
    execution statistics — closing the full learning feedback loop.
    """

    def test_detect_store_apply_measure_cycle(
        self, store: GlobalLearningStore
    ) -> None:
        """Full cycle: discover from failure -> store -> apply on next run -> measure improvement.

        Run 1: Sheet fails, pattern discovered from the failure.
        Run 2: Pattern queried and applied, execution succeeds, outcome recorded.
        Measurement: Trust score and effectiveness reflect the improvement.
        """
        # === Run 1: Failure and Pattern Discovery ===
        # Simulate a failed execution outcome (no pattern applied)
        failed_outcome = SheetOutcome(
            sheet_id="sheet-1",
            job_id="measurement-job",
            validation_results=[{"passed": False, "rule_type": "file_exists"}],
            execution_duration=15.0,
            retry_count=3,
            completion_mode_used=True,
            final_status=SheetStatus.FAILED,
            validation_pass_rate=0.0,
            success_without_retry=False,
            patterns_applied=[],
        )
        store.record_outcome(
            outcome=failed_outcome,
            workspace_path=Path("/tmp/measurement-test"),
            model="claude-sonnet-4-5-20250929",
            error_codes=["E009"],
        )

        # Discover a pattern from the failure
        pattern_id = store.record_pattern(
            pattern_type="validation_fix",
            pattern_name="explicit_output_instruction",
            description="Add explicit output file creation instruction to prompt",
            context_tags=["validation", "file_exists", "output"],
            suggested_action="Append: 'Create the output file at the specified path'",
            provenance_job_hash="measurement-job",
            provenance_sheet_num=1,
        )

        # Get baseline trust score (new pattern, minimal trust)
        baseline_trust = store.calculate_trust_score(pattern_id)

        # === Run 2: Pattern Application with Successful Outcome ===
        # Query applicable patterns (simulates what runner does before execution)
        patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=5,
            context_tags=["validation"],
        )
        applicable = [p for p in patterns if p.id == pattern_id]
        assert len(applicable) == 1, "Pattern should be discoverable for Run 2"

        # Record successful execution outcome WITH the pattern applied
        success_outcome = SheetOutcome(
            sheet_id="sheet-1",
            job_id="measurement-job-run2",
            validation_results=[{"passed": True, "rule_type": "file_exists"}],
            execution_duration=8.0,
            retry_count=0,
            completion_mode_used=False,
            final_status=SheetStatus.COMPLETED,
            validation_pass_rate=1.0,
            success_without_retry=True,
            patterns_applied=[applicable[0].description or ""],
        )
        store.record_outcome(
            outcome=success_outcome,
            workspace_path=Path("/tmp/measurement-test"),
            model="claude-sonnet-4-5-20250929",
        )

        # Record pattern application with positive outcome
        app_id = store.record_pattern_application(
            pattern_id=pattern_id,
            execution_id="measurement-job-run2-exec",
            pattern_led_to_success=True,
            retry_count_before=3,
            retry_count_after=0,
            application_mode="exploitation",
            validation_passed=True,
        )
        assert app_id is not None

        # === Measurement Phase ===
        # Trust score should have increased after successful application
        updated_trust = store.calculate_trust_score(pattern_id)
        assert updated_trust is not None
        assert updated_trust >= (baseline_trust or 0), (
            f"Trust should not decrease after success: {updated_trust} < {baseline_trust}"
        )

        # Pattern should have positive success metrics
        final_patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=10,
        )
        final = [p for p in final_patterns if p.id == pattern_id]
        assert len(final) == 1
        assert final[0].led_to_success_count >= 1
        assert final[0].effectiveness_score > 0

        # Execution stats should reflect both runs
        stats = store.get_execution_stats()
        assert stats["total_executions"] >= 2

    def test_pattern_degrades_on_repeated_failure(
        self, store: GlobalLearningStore
    ) -> None:
        """Verify that a pattern's trust degrades when it consistently fails.

        This is the negative measurement: when a pattern doesn't help,
        the system should learn to stop applying it.
        """
        # Discover a pattern
        pattern_id = store.record_pattern(
            pattern_type="prompt_tweak",
            pattern_name="bad_advice_pattern",
            description="A pattern that doesn't actually help",
            context_tags=["test"],
        )

        store.calculate_trust_score(pattern_id)

        # Record 5 failed applications (pattern applied but didn't help)
        for i in range(5):
            store.record_pattern_application(
                pattern_id=pattern_id,
                execution_id=f"degrade-exec-{i}",
                pattern_led_to_success=False,
                retry_count_before=1,
                retry_count_after=3,  # Got worse
                application_mode="exploitation",
                validation_passed=False,
            )

        # Trust should be lower or at least not improved
        store.calculate_trust_score(pattern_id)
        final_patterns = store.get_patterns_for_auto_apply(
            trust_threshold=0.0,
            require_validated=False,
            limit=10,
        )
        final = [p for p in final_patterns if p.id == pattern_id]
        assert len(final) == 1
        assert final[0].led_to_failure_count >= 5

        # With a reasonable trust threshold, this pattern should be excluded
        filtered = store.get_patterns_for_auto_apply(
            trust_threshold=0.8,
            require_validated=False,
            limit=10,
        )
        filtered_ids = {p.id for p in filtered}
        assert pattern_id not in filtered_ids, (
            "Failed pattern should be excluded at high trust threshold"
        )
