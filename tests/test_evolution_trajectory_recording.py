"""Tests for evolution trajectory recording functionality.

v25 Evolution: Record Evolution Trajectory - verifies the simplified
record_evolution_cycle() and get_evolution_history() wrapper methods work
correctly and integrate with the existing evolution tracking infrastructure.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from marianne.learning.store import GlobalLearningStore


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary database for testing."""
    db_path = tmp_path / "test-learning.db"
    return db_path


@pytest.fixture
def store(temp_db: Path) -> GlobalLearningStore:
    """Create a GlobalLearningStore instance with temporary database."""
    return GlobalLearningStore(db_path=temp_db)


def test_record_evolution_cycle_creates_entry(store: GlobalLearningStore) -> None:
    """Test that record_evolution_cycle() creates a trajectory entry."""
    entry_id = store.record_evolution_cycle(
        cycle_number=25,
        candidates_generated=5,
        candidates_applied=3,
        changes_summary="Fixed learning export, wired pattern lifecycle",
        outcome="SUCCESS",
        learning_snapshot={"patterns": 6, "entropy": 0.000, "recovery_rate": 0.0},
    )

    assert entry_id is not None
    assert isinstance(entry_id, str)

    # Verify entry exists in database
    entries = store.get_trajectory(limit=10)
    assert len(entries) == 1
    assert entries[0].cycle == 25
    assert entries[0].evolutions_completed == 3
    assert entries[0].evolutions_deferred == 2  # 5 - 3


def test_record_evolution_cycle_stores_metadata(store: GlobalLearningStore) -> None:
    """Test that record_evolution_cycle() stores all metadata correctly."""
    learning_snapshot = {
        "patterns": 6,
        "entropy": 0.000,
        "recovery_rate": 0.0,
        "semantic_insights": 6,
    }

    store.record_evolution_cycle(
        cycle_number=26,
        candidates_generated=4,
        candidates_applied=4,
        changes_summary="All candidates applied successfully",
        outcome="SUCCESS",
        learning_snapshot=learning_snapshot,
    )

    entries = store.get_trajectory(limit=1)
    assert len(entries) == 1

    entry = entries[0]
    assert entry.cycle == 26
    assert entry.evolutions_completed == 4
    assert entry.evolutions_deferred == 0
    assert "SUCCESS" in entry.notes
    assert "All candidates applied successfully" in entry.notes
    assert json.dumps(learning_snapshot) in entry.notes


def test_record_evolution_cycle_rejects_duplicate_cycle(
    store: GlobalLearningStore,
) -> None:
    """Test that duplicate cycle numbers raise IntegrityError."""
    store.record_evolution_cycle(
        cycle_number=27,
        candidates_generated=3,
        candidates_applied=2,
        changes_summary="First recording",
        outcome="PARTIAL",
        learning_snapshot={},
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.record_evolution_cycle(
            cycle_number=27,  # Duplicate
            candidates_generated=1,
            candidates_applied=1,
            changes_summary="Duplicate attempt",
            outcome="SUCCESS",
            learning_snapshot={},
        )


def test_get_evolution_history_returns_recent_cycles(
    store: GlobalLearningStore,
) -> None:
    """Test that get_evolution_history() returns last N cycles."""
    # Record 5 cycles
    for i in range(20, 25):
        store.record_evolution_cycle(
            cycle_number=i,
            candidates_generated=5,
            candidates_applied=3,
            changes_summary=f"Cycle {i} changes",
            outcome="SUCCESS",
            learning_snapshot={},
        )

    # Get last 3
    history = store.get_evolution_history(last_n=3)
    assert len(history) == 3

    # Should be in descending order (most recent first)
    assert history[0].cycle == 24
    assert history[1].cycle == 23
    assert history[2].cycle == 22


def test_get_evolution_history_empty_database(store: GlobalLearningStore) -> None:
    """Test that get_evolution_history() handles empty database gracefully."""
    history = store.get_evolution_history(last_n=10)
    assert history == []


def test_record_evolution_cycle_with_partial_outcome(
    store: GlobalLearningStore,
) -> None:
    """Test recording a cycle with PARTIAL outcome."""
    store.record_evolution_cycle(
        cycle_number=28,
        candidates_generated=5,
        candidates_applied=2,
        changes_summary="Only 2 of 5 candidates applied",
        outcome="PARTIAL",
        learning_snapshot={"patterns": 8},
    )

    entries = store.get_trajectory(limit=1)
    assert len(entries) == 1
    assert entries[0].evolutions_completed == 2
    assert entries[0].evolutions_deferred == 3
    assert "PARTIAL" in entries[0].notes


def test_record_evolution_cycle_with_deferred_outcome(
    store: GlobalLearningStore,
) -> None:
    """Test recording a cycle with DEFERRED outcome."""
    store.record_evolution_cycle(
        cycle_number=29,
        candidates_generated=5,
        candidates_applied=0,
        changes_summary="All candidates deferred due to insufficient data",
        outcome="DEFERRED",
        learning_snapshot={"patterns": 1, "entropy": 0.0},
    )

    entries = store.get_trajectory(limit=1)
    assert len(entries) == 1
    assert entries[0].evolutions_completed == 0
    assert entries[0].evolutions_deferred == 5
    assert "DEFERRED" in entries[0].notes


def test_get_evolution_history_limit_parameter(store: GlobalLearningStore) -> None:
    """Test that get_evolution_history() respects limit parameter."""
    # Record 15 cycles
    for i in range(1, 16):
        store.record_evolution_cycle(
            cycle_number=i,
            candidates_generated=3,
            candidates_applied=2,
            changes_summary=f"Cycle {i}",
            outcome="SUCCESS",
            learning_snapshot={},
        )

    # Test different limits
    assert len(store.get_evolution_history(last_n=5)) == 5
    assert len(store.get_evolution_history(last_n=10)) == 10
    assert len(store.get_evolution_history(last_n=20)) == 15  # Only 15 exist


def test_record_evolution_cycle_with_complex_learning_snapshot(
    store: GlobalLearningStore,
) -> None:
    """Test recording with a complex learning snapshot dict."""
    complex_snapshot = {
        "semantic_insights": 12,
        "patterns": {"effective": 5, "pending": 7, "quarantined": 0},
        "entropy": 0.234,
        "recovery_rate": 0.67,
        "drift_alerts": 2,
        "budget_remaining": 0.85,
    }

    store.record_evolution_cycle(
        cycle_number=30,
        candidates_generated=8,
        candidates_applied=6,
        changes_summary="Complex cycle with many metrics",
        outcome="SUCCESS",
        learning_snapshot=complex_snapshot,
    )

    entries = store.get_trajectory(limit=1)
    assert len(entries) == 1

    # Verify the snapshot is stored as JSON in notes
    assert json.dumps(complex_snapshot) in entries[0].notes


def test_evolution_cycle_ordering(store: GlobalLearningStore) -> None:
    """Test that cycles are returned in descending order (newest first)."""
    # Record cycles out of order
    for cycle in [5, 2, 8, 1, 10]:
        store.record_evolution_cycle(
            cycle_number=cycle,
            candidates_generated=3,
            candidates_applied=2,
            changes_summary=f"Cycle {cycle}",
            outcome="SUCCESS",
            learning_snapshot={},
        )

    history = store.get_evolution_history(last_n=10)

    # Should be in descending order
    cycles = [entry.cycle for entry in history]
    assert cycles == [10, 8, 5, 2, 1]
