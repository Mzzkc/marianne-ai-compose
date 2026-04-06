"""Regression tests for min_priority default change (issue #101).

The default min_priority was 0.3, which suppressed patterns with priority
~0.075 (the natural priority for single-occurrence patterns). The fix
lowers the default to 0.01, making low-priority patterns visible by default.

These tests verify that:
1. The default parameter is 0.01, not 0.3
2. Patterns with priority 0.05 appear in default queries
3. Instrument-scoped queries work correctly
4. Dedup-merged patterns retain visibility
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.learning.store import GlobalLearningStore


@pytest.fixture()
def temp_db(tmp_path: Path) -> Path:
    """Return a temporary database path for an isolated learning store."""
    return tmp_path / "test-priority-regression.db"


@pytest.fixture()
def store(temp_db: Path) -> GlobalLearningStore:
    """Create a fresh GlobalLearningStore with a temporary database."""
    return GlobalLearningStore(db_path=temp_db)


def _insert_pattern_direct(
    store: GlobalLearningStore,
    pattern_id: str,
    priority: float,
    instrument_name: str | None = None,
    active: int = 1,
) -> None:
    """Insert a pattern with an exact priority value, bypassing calculation."""
    from datetime import datetime

    now = datetime.now().isoformat()
    with store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO patterns (
                id, pattern_type, pattern_name, description,
                occurrence_count, first_seen, last_seen, last_confirmed,
                led_to_success_count, led_to_failure_count,
                effectiveness_score, variance, suggested_action,
                context_tags, priority_score,
                quarantine_status, trust_score, trust_calculation_date,
                active, instrument_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0.5, 0.0, NULL, '[]', ?,
                      'pending', 0.5, ?, ?, ?)
            """,
            (
                pattern_id,
                "TEST_PATTERN",
                f"test {pattern_id}",
                f"Test pattern {pattern_id}",
                1,
                now,
                now,
                now,
                priority,
                now,
                active,
                instrument_name,
            ),
        )


class TestMinPriorityDefault:
    """Verify the default min_priority parameter is 0.01."""

    def test_default_parameter_is_001(self) -> None:
        """The default min_priority in get_patterns() signature is 0.01."""
        import inspect

        from marianne.learning.store.patterns_query import PatternQueryMixin

        sig = inspect.signature(PatternQueryMixin.get_patterns)
        param = sig.parameters["min_priority"]
        assert param.default == 0.01, (
            f"Expected default min_priority=0.01, got {param.default}"
        )

    def test_pattern_priority_005_returned_by_default(
        self, store: GlobalLearningStore,
    ) -> None:
        """A pattern with priority 0.05 must appear in default queries.

        This is the core regression: under the old default of 0.3,
        a pattern with priority 0.05 would be suppressed.
        """
        _insert_pattern_direct(store, "low-pri-001", priority=0.05)
        results = store.get_patterns()
        ids = [p.id for p in results]
        assert "low-pri-001" in ids, (
            "Pattern with priority 0.05 must be visible at default threshold"
        )

    def test_pattern_priority_002_returned_by_default(
        self, store: GlobalLearningStore,
    ) -> None:
        """Even very low priority (0.02) should appear with new default."""
        _insert_pattern_direct(store, "very-low-001", priority=0.02)
        results = store.get_patterns()
        ids = [p.id for p in results]
        assert "very-low-001" in ids

    def test_pattern_below_new_threshold_excluded(
        self, store: GlobalLearningStore,
    ) -> None:
        """Patterns with priority < 0.01 are still excluded."""
        _insert_pattern_direct(store, "too-low-001", priority=0.005)
        results = store.get_patterns()
        ids = [p.id for p in results]
        assert "too-low-001" not in ids, (
            "Patterns below 0.01 threshold should still be filtered out"
        )


class TestInstrumentIsolation:
    """Verify instrument-scoped pattern queries work correctly."""

    def test_instrument_filter_returns_matching_only(
        self, store: GlobalLearningStore,
    ) -> None:
        """get_patterns(instrument_name=X) returns only X-scoped patterns.

        Note: Current behavior does exact match — NULL instrument patterns
        are NOT included when filtering by a specific instrument. This is
        the v14 implementation. The baton design spec calls for including
        universal patterns, but that's a separate task.
        """
        _insert_pattern_direct(
            store, "claude-001", priority=0.5, instrument_name="claude-cli",
        )
        _insert_pattern_direct(
            store, "gemini-001", priority=0.5, instrument_name="gemini-cli",
        )
        _insert_pattern_direct(
            store, "universal-001", priority=0.5, instrument_name=None,
        )

        claude_patterns = store.get_patterns(instrument_name="claude-cli")
        claude_ids = [p.id for p in claude_patterns]
        assert "claude-001" in claude_ids
        assert "gemini-001" not in claude_ids

    def test_no_instrument_filter_returns_all(
        self, store: GlobalLearningStore,
    ) -> None:
        """get_patterns() without instrument filter returns all patterns."""
        _insert_pattern_direct(
            store, "scoped-001", priority=0.5, instrument_name="claude-cli",
        )
        _insert_pattern_direct(
            store, "unscoped-001", priority=0.5, instrument_name=None,
        )

        all_patterns = store.get_patterns()
        ids = [p.id for p in all_patterns]
        assert "scoped-001" in ids
        assert "unscoped-001" in ids

    def test_instrument_filter_with_low_priority(
        self, store: GlobalLearningStore,
    ) -> None:
        """Instrument-scoped low-priority patterns are visible with new default."""
        _insert_pattern_direct(
            store, "low-claude-001", priority=0.05, instrument_name="claude-cli",
        )
        results = store.get_patterns(instrument_name="claude-cli")
        ids = [p.id for p in results]
        assert "low-claude-001" in ids, (
            "Low-priority instrument-scoped pattern must be visible"
        )


class TestDedupMerging:
    """Verify that dedup-merged patterns retain visibility."""

    def test_dedup_merged_pattern_visible(
        self, store: GlobalLearningStore,
    ) -> None:
        """A pattern created via record_pattern (which handles dedup) is visible."""
        pid = store.record_pattern(
            pattern_type="DEDUP_TEST",
            pattern_name="dedup test",
            description="First occurrence",
        )
        # Record the same pattern again — should merge
        pid2 = store.record_pattern(
            pattern_type="DEDUP_TEST",
            pattern_name="dedup test",
            description="Second occurrence",
        )
        # Both calls should return the same ID (dedup)
        assert pid == pid2

        results = store.get_patterns()
        ids = [p.id for p in results]
        assert pid in ids, "Dedup-merged pattern must be visible"

    def test_dedup_merged_pattern_has_incremented_count(
        self, store: GlobalLearningStore,
    ) -> None:
        """Dedup-merged pattern has occurrence_count >= 2."""
        pid = store.record_pattern(
            pattern_type="COUNT_TEST",
            pattern_name="count test",
        )
        store.record_pattern(
            pattern_type="COUNT_TEST",
            pattern_name="count test",
        )
        pattern = store.get_pattern_by_id(pid)
        assert pattern is not None
        assert pattern.occurrence_count >= 2
