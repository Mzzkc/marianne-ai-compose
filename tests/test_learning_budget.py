"""Comprehensive tests for the BudgetMixin in marianne.learning.store.budget.

Tests the exploration budget management and automatic entropy response
functionality provided by BudgetMixin, exercised through the composed
GlobalLearningStore class with real temporary SQLite databases.

Covers:
    - EntropyResponseConfig and EntropyTriggerContext dataclasses
    - get_exploration_budget() / update_exploration_budget()
    - Budget floor/ceiling enforcement
    - calculate_budget_adjustment() decay and boost logic
    - get_exploration_budget_history() and statistics
    - trigger_entropy_response() with both calling conventions
    - Entropy response with quarantine revisits
    - check_entropy_response_needed() with cooldown
    - get_entropy_metrics / response statistics
    - calculate_pattern_entropy() and record_pattern_entropy()
    - Pattern entropy history retrieval
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from marianne.learning.store import GlobalLearningStore
from marianne.learning.store.budget import (
    EntropyResponseConfig,
    EntropyTriggerContext,
)
from marianne.learning.store.models import (
    EntropyResponseRecord,
    ExplorationBudgetRecord,
    PatternEntropyMetrics,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> GlobalLearningStore:
    """Create a GlobalLearningStore backed by a temporary SQLite database."""
    db_path = tmp_path / "test-budget.db"
    return GlobalLearningStore(db_path=db_path)


@pytest.fixture
def job_hash() -> str:
    """A stable job hash for test repeatability."""
    return "abcdef1234567890"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_pattern_with_applications(
    store: GlobalLearningStore,
    pattern_name: str,
    app_count: int,
    *,
    quarantine_status: str = "pending",
) -> str:
    """Insert a pattern and applications directly via SQL. Returns pattern ID."""
    pattern_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    with store._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO patterns (
                id, pattern_type, pattern_name, description,
                occurrence_count, first_seen, last_seen, last_confirmed,
                led_to_success_count, led_to_failure_count,
                effectiveness_score, variance, priority_score,
                quarantine_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern_id, "test", pattern_name, "test pattern",
                app_count, now, now, now,
                app_count, 0,
                0.8, 0.0, 0.5,
                quarantine_status,
            ),
        )

        for _ in range(app_count):
            conn.execute(
                """
                INSERT INTO pattern_applications (
                    id, pattern_id, execution_id, applied_at,
                    pattern_led_to_success, retry_count_before, retry_count_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), pattern_id, str(uuid.uuid4()), now, 1, 0, 0),
            )

    return pattern_id


# ===========================================================================
# Dataclass tests
# ===========================================================================

class TestEntropyResponseConfig:
    """Tests for EntropyResponseConfig defaults and field override."""

    def test_default_values(self) -> None:
        cfg = EntropyResponseConfig()
        assert cfg.boost_budget is True
        assert cfg.revisit_quarantine is True
        assert cfg.max_quarantine_revisits == 3
        assert cfg.budget_floor == pytest.approx(0.05)
        assert cfg.budget_ceiling == pytest.approx(0.50)
        assert cfg.budget_boost_amount == pytest.approx(0.10)

    def test_custom_values(self) -> None:
        cfg = EntropyResponseConfig(
            boost_budget=False,
            revisit_quarantine=False,
            max_quarantine_revisits=5,
            budget_floor=0.10,
            budget_ceiling=0.80,
            budget_boost_amount=0.20,
        )
        assert cfg.boost_budget is False
        assert cfg.revisit_quarantine is False
        assert cfg.max_quarantine_revisits == 5
        assert cfg.budget_floor == pytest.approx(0.10)
        assert cfg.budget_ceiling == pytest.approx(0.80)
        assert cfg.budget_boost_amount == pytest.approx(0.20)


class TestEntropyTriggerContext:
    """Tests for EntropyTriggerContext bundling."""

    def test_fields(self) -> None:
        ctx = EntropyTriggerContext(
            job_hash="hash123",
            entropy_at_trigger=0.15,
            threshold_used=0.30,
        )
        assert ctx.job_hash == "hash123"
        assert ctx.entropy_at_trigger == pytest.approx(0.15)
        assert ctx.threshold_used == pytest.approx(0.30)


# ===========================================================================
# Exploration Budget CRUD
# ===========================================================================

class TestGetExplorationBudget:
    """Tests for get_exploration_budget()."""

    def test_returns_none_when_empty(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        assert store.get_exploration_budget(job_hash) is None

    def test_returns_none_global_when_empty(
        self, store: GlobalLearningStore,
    ) -> None:
        assert store.get_exploration_budget() is None

    def test_returns_latest_record(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.10,
            adjustment_type="initial",
        )
        store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.20,
            adjustment_type="boost",
        )

        record = store.get_exploration_budget(job_hash)
        assert record is not None
        assert record.budget_value == pytest.approx(0.20)
        assert record.adjustment_type == "boost"

    def test_filter_by_job_hash(
        self, store: GlobalLearningStore,
    ) -> None:
        store.update_exploration_budget(
            job_hash="job_a",
            budget_value=0.10,
            adjustment_type="initial",
        )
        store.update_exploration_budget(
            job_hash="job_b",
            budget_value=0.30,
            adjustment_type="initial",
        )

        record_a = store.get_exploration_budget("job_a")
        record_b = store.get_exploration_budget("job_b")
        assert record_a is not None
        assert record_a.budget_value == pytest.approx(0.10)
        assert record_b is not None
        assert record_b.budget_value == pytest.approx(0.30)


class TestUpdateExplorationBudget:
    """Tests for update_exploration_budget()."""

    def test_basic_insert(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.15,
            adjustment_type="initial",
            entropy_at_time=0.5,
            adjustment_reason="Initial budget set",
        )

        assert isinstance(record, ExplorationBudgetRecord)
        assert record.job_hash == job_hash
        assert record.budget_value == pytest.approx(0.15)
        assert record.adjustment_type == "initial"
        assert record.entropy_at_time == pytest.approx(0.5)
        assert record.adjustment_reason == "Initial budget set"
        assert isinstance(record.recorded_at, datetime)
        assert record.id  # non-empty UUID

    def test_floor_enforcement(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Budget below floor should be clamped to floor."""
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.01,
            adjustment_type="decay",
            floor=0.05,
            ceiling=0.50,
        )

        assert record.budget_value == pytest.approx(0.05)
        assert record.adjustment_type == "floor_enforced"
        assert "enforced to floor" in (record.adjustment_reason or "")

    def test_ceiling_enforcement(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Budget above ceiling should be clamped to ceiling."""
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.80,
            adjustment_type="boost",
            floor=0.05,
            ceiling=0.50,
        )

        assert record.budget_value == pytest.approx(0.50)
        assert record.adjustment_type == "ceiling_enforced"
        assert "enforced to ceiling" in (record.adjustment_reason or "")

    def test_value_at_floor_not_enforced(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Value exactly at floor should not trigger floor_enforced."""
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.05,
            adjustment_type="decay",
            floor=0.05,
        )
        assert record.budget_value == pytest.approx(0.05)
        assert record.adjustment_type == "decay"

    def test_value_at_ceiling_not_enforced(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Value exactly at ceiling should not trigger ceiling_enforced."""
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.50,
            adjustment_type="boost",
            ceiling=0.50,
        )
        assert record.budget_value == pytest.approx(0.50)
        assert record.adjustment_type == "boost"

    def test_custom_floor_and_ceiling(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Custom floor/ceiling values should be respected."""
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.02,
            adjustment_type="decay",
            floor=0.10,
            ceiling=0.30,
        )
        assert record.budget_value == pytest.approx(0.10)
        assert record.adjustment_type == "floor_enforced"

    def test_entropy_at_time_none_allowed(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """entropy_at_time can be None."""
        record = store.update_exploration_budget(
            job_hash=job_hash,
            budget_value=0.15,
            adjustment_type="initial",
            entropy_at_time=None,
        )
        assert record.entropy_at_time is None


# ===========================================================================
# Budget History and Statistics
# ===========================================================================

class TestExplorationBudgetHistory:
    """Tests for get_exploration_budget_history()."""

    def test_empty_history(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        history = store.get_exploration_budget_history(job_hash)
        assert history == []

    def test_returns_ordered_records(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        import time

        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.10, adjustment_type="initial",
        )
        time.sleep(0.01)  # Ensure distinct timestamps for ordering
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.15, adjustment_type="boost",
        )
        time.sleep(0.01)
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.14, adjustment_type="decay",
        )

        history = store.get_exploration_budget_history(job_hash)
        assert len(history) == 3
        # Most recent first
        assert history[0].adjustment_type == "decay"
        assert history[1].adjustment_type == "boost"
        assert history[2].adjustment_type == "initial"

    def test_respects_limit(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        for i in range(10):
            store.update_exploration_budget(
                job_hash=job_hash,
                budget_value=0.10 + i * 0.01,
                adjustment_type="boost",
            )

        history = store.get_exploration_budget_history(job_hash, limit=3)
        assert len(history) == 3

    def test_global_history(
        self, store: GlobalLearningStore,
    ) -> None:
        """History without job_hash filter returns all records."""
        store.update_exploration_budget(
            job_hash="job_a", budget_value=0.10, adjustment_type="initial",
        )
        store.update_exploration_budget(
            job_hash="job_b", budget_value=0.20, adjustment_type="initial",
        )

        history = store.get_exploration_budget_history()
        assert len(history) == 2


class TestExplorationBudgetStatistics:
    """Tests for get_exploration_budget_statistics()."""

    def test_empty_statistics(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        stats = store.get_exploration_budget_statistics(job_hash)
        assert stats["current_budget"] is None
        assert stats["total_adjustments"] == 0
        assert stats["avg_budget"] == 0.0
        assert stats["floor_enforcements"] == 0
        assert stats["boost_count"] == 0
        assert stats["decay_count"] == 0

    def test_populated_statistics(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        # Insert various adjustment types
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.15, adjustment_type="initial",
        )
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.25, adjustment_type="boost",
        )
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.20, adjustment_type="decay",
        )
        # This will be floor-enforced
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.01, adjustment_type="decay",
            floor=0.05,
        )

        stats = store.get_exploration_budget_statistics(job_hash)
        assert stats["total_adjustments"] == 4
        assert stats["current_budget"] == pytest.approx(0.05)
        assert stats["floor_enforcements"] == 1
        assert stats["boost_count"] == 1
        assert stats["decay_count"] == 1  # One actual decay + one became floor_enforced
        assert stats["min_budget"] == pytest.approx(0.05)
        assert stats["max_budget"] == pytest.approx(0.25)
        assert stats["avg_budget"] > 0


# ===========================================================================
# Budget Decay and Boost (calculate_budget_adjustment)
# ===========================================================================

class TestCalculateBudgetAdjustment:
    """Tests for calculate_budget_adjustment() -- the core decay/boost logic."""

    def test_initial_budget_when_no_history(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """First call should create an 'initial' budget record."""
        record = store.calculate_budget_adjustment(
            job_hash=job_hash,
            current_entropy=0.5,
            initial_budget=0.15,
        )
        assert record.adjustment_type == "initial"
        assert record.budget_value == pytest.approx(0.15)

    def test_boost_when_entropy_below_threshold(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Low entropy should trigger a budget boost."""
        # Set initial budget
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.15, adjustment_type="initial",
        )

        record = store.calculate_budget_adjustment(
            job_hash=job_hash,
            current_entropy=0.1,  # below default threshold 0.3
            boost_amount=0.10,
        )

        assert record.adjustment_type == "boost"
        assert record.budget_value == pytest.approx(0.25)  # 0.15 + 0.10

    def test_decay_when_entropy_above_threshold(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Healthy entropy should trigger budget decay."""
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.20, adjustment_type="initial",
        )

        record = store.calculate_budget_adjustment(
            job_hash=job_hash,
            current_entropy=0.5,  # above default threshold 0.3
            decay_rate=0.95,
        )

        assert record.adjustment_type == "decay"
        assert record.budget_value == pytest.approx(0.20 * 0.95)

    def test_decay_respects_floor(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Repeated decay should not drop budget below floor."""
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.06, adjustment_type="decay",
        )

        record = store.calculate_budget_adjustment(
            job_hash=job_hash,
            current_entropy=0.8,
            decay_rate=0.50,  # Aggressive decay: 0.06 * 0.5 = 0.03 < floor
            floor=0.05,
        )

        assert record.budget_value == pytest.approx(0.05)
        assert record.adjustment_type == "floor_enforced"

    def test_boost_respects_ceiling(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Boost should not push budget above ceiling."""
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.45, adjustment_type="boost",
        )

        record = store.calculate_budget_adjustment(
            job_hash=job_hash,
            current_entropy=0.1,
            boost_amount=0.10,  # 0.45 + 0.10 = 0.55 > ceiling 0.50
            ceiling=0.50,
        )

        assert record.budget_value == pytest.approx(0.50)
        assert record.adjustment_type == "ceiling_enforced"

    def test_entropy_at_threshold_decays(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Entropy exactly at threshold should decay (>= threshold branch)."""
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.20, adjustment_type="initial",
        )

        record = store.calculate_budget_adjustment(
            job_hash=job_hash,
            current_entropy=0.3,  # exactly at threshold
            entropy_threshold=0.3,
            decay_rate=0.90,
        )

        assert record.adjustment_type == "decay"
        assert record.budget_value == pytest.approx(0.20 * 0.90)

    def test_multiple_adjustments_sequence(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """A sequence of adjustments should compound correctly."""
        # Initial
        r1 = store.calculate_budget_adjustment(
            job_hash=job_hash, current_entropy=0.5, initial_budget=0.20,
        )
        assert r1.adjustment_type == "initial"
        assert r1.budget_value == pytest.approx(0.20)

        # Decay
        r2 = store.calculate_budget_adjustment(
            job_hash=job_hash, current_entropy=0.5, decay_rate=0.90,
        )
        assert r2.adjustment_type == "decay"
        assert r2.budget_value == pytest.approx(0.18)

        # Boost (entropy drops)
        r3 = store.calculate_budget_adjustment(
            job_hash=job_hash, current_entropy=0.1, boost_amount=0.10,
        )
        assert r3.adjustment_type == "boost"
        assert r3.budget_value == pytest.approx(0.28)


# ===========================================================================
# Entropy Response
# ===========================================================================

class TestTriggerEntropyResponse:
    """Tests for trigger_entropy_response()."""

    def test_legacy_positional_calling_convention(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        record = store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
        )

        assert isinstance(record, EntropyResponseRecord)
        assert record.job_hash == job_hash
        assert record.entropy_at_trigger == pytest.approx(0.15)
        assert record.threshold_used == pytest.approx(0.30)
        assert record.budget_boosted is True
        assert "budget_boost" in record.actions_taken

    def test_bundled_trigger_context(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        ctx = EntropyTriggerContext(
            job_hash=job_hash,
            entropy_at_trigger=0.20,
            threshold_used=0.35,
        )
        record = store.trigger_entropy_response(trigger=ctx)

        assert record.job_hash == job_hash
        assert record.entropy_at_trigger == pytest.approx(0.20)
        assert record.threshold_used == pytest.approx(0.35)

    def test_bundled_config(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        cfg = EntropyResponseConfig(
            boost_budget=True,
            revisit_quarantine=False,
            budget_boost_amount=0.20,
        )
        ctx = EntropyTriggerContext(
            job_hash=job_hash, entropy_at_trigger=0.10, threshold_used=0.30,
        )

        record = store.trigger_entropy_response(trigger=ctx, config=cfg)

        assert record.budget_boosted is True
        assert "budget_boost" in record.actions_taken
        # No quarantine revisit since revisit_quarantine=False
        assert record.quarantine_revisits == 0

    def test_keyword_overrides_config(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Explicit keyword args should override config values."""
        cfg = EntropyResponseConfig(boost_budget=True, revisit_quarantine=True)

        record = store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
            config=cfg,
            boost_budget=False,
            revisit_quarantine=False,
        )

        # Overridden: no budget boost, no quarantine revisit
        assert record.budget_boosted is False
        assert record.quarantine_revisits == 0
        assert record.actions_taken == []

    def test_no_boost_no_revisit(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Disabling both actions yields an empty actions list."""
        record = store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
            boost_budget=False,
            revisit_quarantine=False,
        )

        assert record.budget_boosted is False
        assert record.quarantine_revisits == 0
        assert record.actions_taken == []

    def test_budget_boost_creates_budget_record(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Triggering a budget boost should create a budget record."""
        store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
            boost_budget=True,
            revisit_quarantine=False,
            budget_boost_amount=0.10,
        )

        budget = store.get_exploration_budget(job_hash)
        assert budget is not None
        # No prior budget: initial (0.15) + boost (0.10) = 0.25
        assert budget.budget_value == pytest.approx(0.25)

    def test_budget_boost_adds_to_existing(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Boost adds to the current budget value if one exists."""
        store.update_exploration_budget(
            job_hash=job_hash, budget_value=0.20, adjustment_type="initial",
        )

        store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
            boost_budget=True,
            revisit_quarantine=False,
            budget_boost_amount=0.10,
        )

        budget = store.get_exploration_budget(job_hash)
        assert budget is not None
        assert budget.budget_value == pytest.approx(0.30)  # 0.20 + 0.10

    def test_quarantine_revisit(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Entropy response should revisit quarantined patterns."""
        # Create quarantined patterns
        p1 = _insert_pattern_with_applications(
            store, "pattern_a", 3, quarantine_status="quarantined",
        )
        p2 = _insert_pattern_with_applications(
            store, "pattern_b", 2, quarantine_status="quarantined",
        )
        # A non-quarantined pattern should not be revisited
        _insert_pattern_with_applications(
            store, "pattern_c", 5, quarantine_status="validated",
        )

        record = store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
            boost_budget=False,
            revisit_quarantine=True,
            max_quarantine_revisits=10,
        )

        assert record.quarantine_revisits == 2
        assert set(record.patterns_revisited) == {p1, p2}
        assert "quarantine_revisit" in record.actions_taken

        # Verify the quarantine status was changed to 'pending'
        with store._get_connection() as conn:
            cursor = conn.execute(
                "SELECT quarantine_status FROM patterns WHERE id = ?",
                (p1,),
            )
            row = cursor.fetchone()
            assert row["quarantine_status"] == "pending"

    def test_quarantine_revisit_respects_limit(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """max_quarantine_revisits should cap the number revisited."""
        for i in range(5):
            _insert_pattern_with_applications(
                store, f"q_pattern_{i}", 1, quarantine_status="quarantined",
            )

        record = store.trigger_entropy_response(
            job_hash, 0.15, 0.30,
            boost_budget=False,
            revisit_quarantine=True,
            max_quarantine_revisits=2,
        )

        assert record.quarantine_revisits == 2
        assert len(record.patterns_revisited) == 2


# ===========================================================================
# Entropy Response Retrieval
# ===========================================================================

class TestGetEntropyResponse:
    """Tests for get_last_entropy_response() and history."""

    def test_no_responses(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        assert store.get_last_entropy_response(job_hash) is None

    def test_last_response(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        store.trigger_entropy_response(
            job_hash, 0.10, 0.30,
            boost_budget=False, revisit_quarantine=False,
        )
        store.trigger_entropy_response(
            job_hash, 0.20, 0.30,
            boost_budget=False, revisit_quarantine=False,
        )

        last = store.get_last_entropy_response(job_hash)
        assert last is not None
        assert last.entropy_at_trigger == pytest.approx(0.20)

    def test_response_history(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        for i in range(5):
            store.trigger_entropy_response(
                job_hash, 0.10 + i * 0.01, 0.30,
                boost_budget=False, revisit_quarantine=False,
            )

        history = store.get_entropy_response_history(job_hash)
        assert len(history) == 5
        # Most recent first
        assert history[0].entropy_at_trigger > history[-1].entropy_at_trigger

    def test_response_history_limit(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        for _ in range(10):
            store.trigger_entropy_response(
                job_hash, 0.10, 0.30,
                boost_budget=False, revisit_quarantine=False,
            )

        history = store.get_entropy_response_history(job_hash, limit=3)
        assert len(history) == 3


# ===========================================================================
# Entropy Response Statistics
# ===========================================================================

class TestEntropyResponseStatistics:
    """Tests for get_entropy_response_statistics()."""

    def test_empty_statistics(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        stats = store.get_entropy_response_statistics(job_hash)
        assert stats["total_responses"] == 0
        assert stats["avg_entropy_at_trigger"] == 0.0
        assert stats["budget_boosts"] == 0
        assert stats["quarantine_revisits"] == 0
        assert stats["last_response"] is None

    def test_populated_statistics(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        # Two responses with budget boost
        store.trigger_entropy_response(
            job_hash, 0.10, 0.30,
            boost_budget=True, revisit_quarantine=False,
        )
        store.trigger_entropy_response(
            job_hash, 0.20, 0.30,
            boost_budget=True, revisit_quarantine=False,
        )
        # One response without boost
        store.trigger_entropy_response(
            job_hash, 0.25, 0.30,
            boost_budget=False, revisit_quarantine=False,
        )

        stats = store.get_entropy_response_statistics(job_hash)
        assert stats["total_responses"] == 3
        assert stats["budget_boosts"] == 2
        assert stats["avg_entropy_at_trigger"] > 0
        assert stats["last_response"] is not None


# ===========================================================================
# Check Entropy Response Needed
# ===========================================================================

class TestCheckEntropyResponseNeeded:
    """Tests for check_entropy_response_needed()."""

    def test_no_patterns_returns_not_needed(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        needed, entropy, reason = store.check_entropy_response_needed(job_hash)
        assert needed is False
        assert entropy is None
        assert "No pattern applications" in reason

    def test_single_pattern_low_entropy(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """A single pattern with all applications should have zero diversity."""
        _insert_pattern_with_applications(store, "dominant", 10)

        needed, entropy, _reason = store.check_entropy_response_needed(
            job_hash, entropy_threshold=0.3,
        )

        # Single effective pattern => diversity_index = 0 (H/Hmax = 0/1 = 0)
        assert needed is True
        assert entropy is not None
        assert entropy == pytest.approx(0.0)

    def test_balanced_patterns_healthy_entropy(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """Multiple patterns with balanced applications should have high entropy."""
        for i in range(5):
            _insert_pattern_with_applications(store, f"pattern_{i}", 10)

        needed, entropy, reason = store.check_entropy_response_needed(
            job_hash, entropy_threshold=0.3,
        )

        # 5 patterns with equal applications => diversity_index near 1.0
        assert needed is False
        assert entropy is not None
        assert entropy > 0.9
        assert "healthy" in reason

    def test_cooldown_blocks_response(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """A recent entropy response should block new checks via cooldown."""
        # Create patterns so entropy check would normally trigger
        _insert_pattern_with_applications(store, "dominant", 100)

        # Trigger a response first
        store.trigger_entropy_response(
            job_hash, 0.1, 0.3,
            boost_budget=False, revisit_quarantine=False,
        )

        # Now check again -- should be in cooldown
        needed, _entropy, reason = store.check_entropy_response_needed(
            job_hash, cooldown_seconds=3600,
        )

        assert needed is False
        assert "Cooldown active" in reason

    def test_expired_cooldown_allows_response(
        self, store: GlobalLearningStore, job_hash: str,
    ) -> None:
        """After cooldown expires, response should be allowed."""
        _insert_pattern_with_applications(store, "dominant", 100)

        # Insert a response record with an old timestamp directly
        import json

        old_time = (datetime.now() - timedelta(hours=2)).isoformat()
        with store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO entropy_responses (
                    id, job_hash, recorded_at, entropy_at_trigger,
                    threshold_used, actions_taken, budget_boosted,
                    quarantine_revisits, patterns_revisited
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), job_hash, old_time, 0.1, 0.3,
                    json.dumps([]), 0, 0, json.dumps([]),
                ),
            )

        # Cooldown of 1 hour should have expired
        needed, _entropy, _reason = store.check_entropy_response_needed(
            job_hash, cooldown_seconds=3600,
        )

        assert needed is True


# ===========================================================================
# Pattern Entropy Metrics
# ===========================================================================

class TestCalculatePatternEntropy:
    """Tests for calculate_pattern_entropy()."""

    def test_no_patterns(self, store: GlobalLearningStore) -> None:
        metrics = store.calculate_pattern_entropy()

        assert isinstance(metrics, PatternEntropyMetrics)
        assert metrics.shannon_entropy == pytest.approx(0.0)
        assert metrics.diversity_index == pytest.approx(0.0)
        assert metrics.total_applications == 0
        assert metrics.effective_pattern_count == 0

    def test_single_pattern_with_applications(
        self, store: GlobalLearningStore,
    ) -> None:
        _insert_pattern_with_applications(store, "single", 5)

        metrics = store.calculate_pattern_entropy()

        assert metrics.effective_pattern_count == 1
        assert metrics.total_applications == 5
        # Single pattern: H = 0, diversity = 0 (0/1.0)
        assert metrics.shannon_entropy == pytest.approx(0.0)
        assert metrics.diversity_index == pytest.approx(0.0)
        assert metrics.dominant_pattern_share == pytest.approx(1.0)

    def test_two_equal_patterns(self, store: GlobalLearningStore) -> None:
        _insert_pattern_with_applications(store, "a", 10)
        _insert_pattern_with_applications(store, "b", 10)

        metrics = store.calculate_pattern_entropy()

        assert metrics.effective_pattern_count == 2
        assert metrics.total_applications == 20
        # Two equal patterns: H = 1.0 bit, Hmax = 1.0 bit, diversity = 1.0
        assert metrics.shannon_entropy == pytest.approx(1.0, abs=0.01)
        assert metrics.diversity_index == pytest.approx(1.0, abs=0.01)
        assert metrics.dominant_pattern_share == pytest.approx(0.5)

    def test_unbalanced_patterns(self, store: GlobalLearningStore) -> None:
        _insert_pattern_with_applications(store, "dominant", 90)
        _insert_pattern_with_applications(store, "minor", 10)

        metrics = store.calculate_pattern_entropy()

        assert metrics.effective_pattern_count == 2
        assert metrics.total_applications == 100
        # Unbalanced: diversity < 1.0
        assert 0 < metrics.diversity_index < 1.0
        assert metrics.dominant_pattern_share == pytest.approx(0.9)

    def test_pattern_without_applications_not_effective(
        self, store: GlobalLearningStore,
    ) -> None:
        """A pattern with zero applications should not count as effective."""
        _insert_pattern_with_applications(store, "used", 5)
        # Insert pattern with NO applications
        _insert_pattern_with_applications(store, "unused", 0)

        metrics = store.calculate_pattern_entropy()

        assert metrics.unique_pattern_count == 2
        assert metrics.effective_pattern_count == 1
        assert metrics.total_applications == 5


class TestRecordPatternEntropy:
    """Tests for record_pattern_entropy() and get_pattern_entropy_history()."""

    def test_record_and_retrieve(self, store: GlobalLearningStore) -> None:
        metrics = PatternEntropyMetrics(
            calculated_at=datetime.now(),
            shannon_entropy=1.5,
            max_possible_entropy=2.0,
            diversity_index=0.75,
            unique_pattern_count=4,
            effective_pattern_count=4,
            total_applications=40,
            dominant_pattern_share=0.35,
        )

        record_id = store.record_pattern_entropy(metrics)
        assert record_id  # non-empty UUID

        history = store.get_pattern_entropy_history(limit=10)
        assert len(history) == 1
        assert history[0].shannon_entropy == pytest.approx(1.5)
        assert history[0].diversity_index == pytest.approx(0.75)
        assert history[0].total_applications == 40

    def test_history_ordering(self, store: GlobalLearningStore) -> None:
        for i in range(5):
            m = PatternEntropyMetrics(
                calculated_at=datetime.now(),
                shannon_entropy=float(i),
                max_possible_entropy=5.0,
                diversity_index=i / 5.0,
                unique_pattern_count=5,
                effective_pattern_count=5,
                total_applications=50,
                dominant_pattern_share=0.2,
            )
            store.record_pattern_entropy(m)

        history = store.get_pattern_entropy_history(limit=50)
        assert len(history) == 5
        # Most recent first (highest shannon_entropy was inserted last)
        assert history[0].shannon_entropy >= history[-1].shannon_entropy

    def test_history_respects_limit(self, store: GlobalLearningStore) -> None:
        for _ in range(10):
            m = PatternEntropyMetrics(
                calculated_at=datetime.now(),
                shannon_entropy=1.0,
                max_possible_entropy=2.0,
                diversity_index=0.5,
                unique_pattern_count=2,
                effective_pattern_count=2,
                total_applications=20,
                dominant_pattern_share=0.5,
            )
            store.record_pattern_entropy(m)

        history = store.get_pattern_entropy_history(limit=3)
        assert len(history) == 3

    def test_threshold_exceeded_persisted(
        self, store: GlobalLearningStore,
    ) -> None:
        m = PatternEntropyMetrics(
            calculated_at=datetime.now(),
            shannon_entropy=0.5,
            max_possible_entropy=2.0,
            diversity_index=0.25,
            unique_pattern_count=4,
            effective_pattern_count=4,
            total_applications=40,
            dominant_pattern_share=0.7,
            threshold_exceeded=True,
        )
        store.record_pattern_entropy(m)

        history = store.get_pattern_entropy_history()
        assert len(history) == 1
        assert history[0].threshold_exceeded is True


# ===========================================================================
# Where helper
# ===========================================================================

class TestWhereJobHash:
    """Tests for BudgetMixin._where_job_hash static helper."""

    def test_with_job_hash(self) -> None:
        clause, params = GlobalLearningStore._where_job_hash("myhash")
        assert "WHERE" in clause
        assert params == ("myhash",)

    def test_without_job_hash(self) -> None:
        clause, params = GlobalLearningStore._where_job_hash(None)
        assert clause == ""
        assert params == ()
