"""Tests for the ExecutionMixin in marianne.learning.store.executions.

Tests the execution recording, querying, statistics, similarity matching,
workspace clustering, and helper methods provided by the ExecutionMixin.
Uses real temp SQLite databases via GlobalLearningStore which composes all mixins.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from marianne.core.checkpoint import SheetStatus
from marianne.learning.outcomes import SheetOutcome
from marianne.learning.store import ExecutionRecord, GlobalLearningStore


@pytest.fixture
def store(tmp_path: Path) -> GlobalLearningStore:
    """Create a GlobalLearningStore backed by a temp SQLite database."""
    db_path = tmp_path / "test-executions.db"
    return GlobalLearningStore(db_path=db_path)


@pytest.fixture
def workspace_path(tmp_path: Path) -> Path:
    """Return a temporary workspace path for hashing."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _make_outcome(
    *,
    sheet_id: str = "job-sheet1",
    job_id: str = "test-job",
    execution_duration: float = 45.0,
    retry_count: int = 0,
    final_status: SheetStatus = SheetStatus.COMPLETED,
    validation_pass_rate: float = 1.0,
    success_without_retry: bool = True,
    timestamp: datetime | None = None,
) -> SheetOutcome:
    """Build a SheetOutcome with defaults for testing."""
    return SheetOutcome(
        sheet_id=sheet_id,
        job_id=job_id,
        validation_results=[],
        execution_duration=execution_duration,
        retry_count=retry_count,
        completion_mode_used=False,
        final_status=final_status,
        validation_pass_rate=validation_pass_rate,
        success_without_retry=success_without_retry,
        timestamp=timestamp or datetime.now(tz=UTC),
    )


# =========================================================================
# record_outcome tests
# =========================================================================


class TestRecordOutcome:
    """Tests for ExecutionMixin.record_outcome()."""

    def test_record_outcome_returns_uuid(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome = _make_outcome()
        exec_id = store.record_outcome(outcome, workspace_path)
        assert isinstance(exec_id, str)
        assert len(exec_id) == 36  # UUID format

    def test_record_outcome_persists_to_db(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome = _make_outcome(
            sheet_id="job-sheet3",
            execution_duration=120.5,
            retry_count=2,
            final_status=SheetStatus.FAILED,
            validation_pass_rate=0.5,
            success_without_retry=False,
        )
        exec_id = store.record_outcome(outcome, workspace_path, model="claude-3")
        records = store.get_recent_executions(limit=10)

        assert len(records) == 1
        rec = records[0]
        assert rec.id == exec_id
        assert rec.sheet_num == 3
        assert rec.duration_seconds == 120.5
        assert rec.retry_count == 2
        assert rec.status == SheetStatus.FAILED.value
        assert rec.validation_pass_rate == 0.5
        assert rec.success_without_retry is False
        assert rec.model == "claude-3"

    def test_record_outcome_with_error_codes(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome = _make_outcome()
        store.record_outcome(
            outcome,
            workspace_path,
            error_codes=["E101", "E201"],
        )
        records = store.get_recent_executions(limit=1)
        assert records[0].error_codes == ["E101", "E201"]

    def test_record_outcome_without_error_codes_stores_empty_list(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome = _make_outcome()
        store.record_outcome(outcome, workspace_path)
        records = store.get_recent_executions(limit=1)
        assert records[0].error_codes == []

    def test_record_multiple_outcomes(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        for i in range(5):
            outcome = _make_outcome(sheet_id=f"job-sheet{i}")
            store.record_outcome(outcome, workspace_path)

        records = store.get_recent_executions(limit=20)
        assert len(records) == 5

    def test_record_outcome_workspace_hash_consistency(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        """Same workspace path produces the same hash across records."""
        for i in range(3):
            outcome = _make_outcome(sheet_id=f"job-sheet{i}")
            store.record_outcome(outcome, workspace_path)

        records = store.get_recent_executions(limit=10)
        hashes = {rec.workspace_hash for rec in records}
        assert len(hashes) == 1  # all same workspace hash

    def test_record_outcome_job_hash_consistency(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        """Same job_id produces the same job hash across records."""
        for i in range(3):
            outcome = _make_outcome(sheet_id=f"job-sheet{i}", job_id="my-job")
            store.record_outcome(outcome, workspace_path)

        records = store.get_recent_executions(limit=10)
        hashes = {rec.job_hash for rec in records}
        assert len(hashes) == 1

    def test_record_outcome_model_none_by_default(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome = _make_outcome()
        store.record_outcome(outcome, workspace_path)
        records = store.get_recent_executions(limit=1)
        assert records[0].model is None


# =========================================================================
# get_execution_stats tests
# =========================================================================


class TestGetExecutionStats:
    """Tests for ExecutionMixin.get_execution_stats()."""

    def test_empty_store_returns_zero_stats(
        self,
        store: GlobalLearningStore,
    ) -> None:
        stats = store.get_execution_stats()
        assert stats["total_executions"] == 0
        assert stats["success_without_retry_rate"] == 0.0
        assert stats["total_patterns"] == 0
        assert stats["avg_pattern_effectiveness"] == 0.0
        assert stats["total_error_recoveries"] == 0
        assert stats["unique_workspaces"] == 0

    def test_stats_after_recording_executions(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        # 3 success_without_retry, 2 not
        for i in range(3):
            outcome = _make_outcome(
                sheet_id=f"job-sheet{i}",
                success_without_retry=True,
            )
            store.record_outcome(outcome, workspace_path)

        for i in range(3, 5):
            outcome = _make_outcome(
                sheet_id=f"job-sheet{i}",
                success_without_retry=False,
                retry_count=2,
            )
            store.record_outcome(outcome, workspace_path)

        stats = store.get_execution_stats()
        assert stats["total_executions"] == 5
        assert stats["success_without_retry_rate"] == pytest.approx(3 / 5)
        assert stats["unique_workspaces"] == 1

    def test_stats_unique_workspaces_counts_distinct(
        self,
        store: GlobalLearningStore,
        tmp_path: Path,
    ) -> None:
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()

        store.record_outcome(_make_outcome(sheet_id="a-sheet1"), ws1)
        store.record_outcome(_make_outcome(sheet_id="b-sheet2"), ws2)

        stats = store.get_execution_stats()
        assert stats["unique_workspaces"] == 2

    def test_stats_includes_error_recovery_count(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        store.record_error_recovery(
            error_code="E101",
            suggested_wait=30.0,
            actual_wait=25.0,
            recovery_success=True,
        )
        stats = store.get_execution_stats()
        assert stats["total_error_recoveries"] == 1


# =========================================================================
# get_recent_executions tests
# =========================================================================


class TestGetRecentExecutions:
    """Tests for ExecutionMixin.get_recent_executions()."""

    def test_empty_store_returns_empty_list(
        self,
        store: GlobalLearningStore,
    ) -> None:
        records = store.get_recent_executions()
        assert records == []

    def test_returns_execution_records(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome = _make_outcome()
        store.record_outcome(outcome, workspace_path, model="opus")
        records = store.get_recent_executions(limit=10)

        assert len(records) == 1
        rec = records[0]
        assert isinstance(rec, ExecutionRecord)
        assert rec.model == "opus"
        assert rec.status == "completed"

    def test_respects_limit(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        for i in range(10):
            store.record_outcome(
                _make_outcome(sheet_id=f"job-sheet{i}"),
                workspace_path,
            )

        records = store.get_recent_executions(limit=3)
        assert len(records) == 3

    def test_ordered_by_completed_at_descending(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        for i in range(5):
            store.record_outcome(
                _make_outcome(sheet_id=f"job-sheet{i}"),
                workspace_path,
            )

        records = store.get_recent_executions(limit=5)
        # completed_at is set at INSERT time, so the last inserted should be first
        completed_times = [rec.completed_at for rec in records]
        assert all(t is not None for t in completed_times)
        non_none_times: list[datetime] = [t for t in completed_times if t is not None]
        assert non_none_times == sorted(non_none_times, reverse=True)

    def test_filter_by_workspace_hash(
        self,
        store: GlobalLearningStore,
        tmp_path: Path,
    ) -> None:
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()

        store.record_outcome(_make_outcome(sheet_id="a-sheet1"), ws1)
        store.record_outcome(_make_outcome(sheet_id="b-sheet2"), ws2)
        store.record_outcome(_make_outcome(sheet_id="a-sheet3"), ws1)

        ws1_hash = GlobalLearningStore.hash_workspace(ws1)
        records = store.get_recent_executions(workspace_hash=ws1_hash)
        assert len(records) == 2
        for rec in records:
            assert rec.workspace_hash == ws1_hash

    def test_filter_by_workspace_hash_no_match(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        store.record_outcome(_make_outcome(), workspace_path)
        records = store.get_recent_executions(workspace_hash="nonexistenthash")
        assert records == []

    def test_record_fields_round_trip(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        """Verify all ExecutionRecord fields survive a write-read round trip."""
        outcome = _make_outcome(
            sheet_id="job-sheet7",
            execution_duration=99.9,
            retry_count=3,
            final_status=SheetStatus.FAILED,
            validation_pass_rate=0.75,
            success_without_retry=False,
        )
        exec_id = store.record_outcome(
            outcome,
            workspace_path,
            model="test-model",
            error_codes=["E101"],
        )

        records = store.get_recent_executions(limit=1)
        rec = records[0]
        assert rec.id == exec_id
        assert rec.sheet_num == 7
        assert rec.duration_seconds == pytest.approx(99.9)
        assert rec.retry_count == 3
        assert rec.status == "failed"
        assert rec.validation_pass_rate == pytest.approx(0.75)
        assert rec.success_without_retry is False
        assert rec.model == "test-model"
        assert rec.error_codes == ["E101"]
        assert rec.started_at is not None
        assert rec.completed_at is not None
        assert rec.confidence_score >= 0.0
        assert rec.confidence_score <= 1.0


# =========================================================================
# get_similar_executions tests
# =========================================================================


class TestGetSimilarExecutions:
    """Tests for ExecutionMixin.get_similar_executions()."""

    def test_empty_store_returns_empty(
        self,
        store: GlobalLearningStore,
    ) -> None:
        records = store.get_similar_executions(job_hash="abc")
        assert records == []

    def test_filter_by_job_hash(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        outcome_a = _make_outcome(job_id="job-alpha", sheet_id="job-alpha-sheet1")
        outcome_b = _make_outcome(job_id="job-beta", sheet_id="job-beta-sheet1")
        store.record_outcome(outcome_a, workspace_path)
        store.record_outcome(outcome_b, workspace_path)

        job_hash_a = GlobalLearningStore.hash_job("job-alpha")
        records = store.get_similar_executions(job_hash=job_hash_a)
        assert len(records) == 1
        assert records[0].job_hash == job_hash_a

    def test_filter_by_workspace_hash(
        self,
        store: GlobalLearningStore,
        tmp_path: Path,
    ) -> None:
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()

        store.record_outcome(_make_outcome(sheet_id="a-sheet1"), ws1)
        store.record_outcome(_make_outcome(sheet_id="b-sheet1"), ws2)

        ws1_hash = GlobalLearningStore.hash_workspace(ws1)
        records = store.get_similar_executions(workspace_hash=ws1_hash)
        assert len(records) == 1
        assert records[0].workspace_hash == ws1_hash

    def test_filter_by_sheet_num(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        store.record_outcome(
            _make_outcome(sheet_id="job-sheet1"),
            workspace_path,
        )
        store.record_outcome(
            _make_outcome(sheet_id="job-sheet2"),
            workspace_path,
        )
        store.record_outcome(
            _make_outcome(sheet_id="job-sheet1a", job_id="other"),
            workspace_path,
        )

        # sheet_id "job-sheet1" -> sheet_num 1, "job-sheet1a" -> sheet_num 0
        # (no trailing digit match? let me check)
        # Actually "job-sheet1a" ends with "a" not a digit, so _extract_sheet_num returns 0
        # "job-sheet1" ends with "1" -> sheet_num 1
        # "job-sheet2" ends with "2" -> sheet_num 2
        records = store.get_similar_executions(sheet_num=1)
        assert len(records) == 1
        assert records[0].sheet_num == 1

    def test_combined_filters(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        store.record_outcome(
            _make_outcome(sheet_id="job-sheet1", job_id="alpha"),
            workspace_path,
        )
        store.record_outcome(
            _make_outcome(sheet_id="job-sheet2", job_id="alpha"),
            workspace_path,
        )
        store.record_outcome(
            _make_outcome(sheet_id="job-sheet1", job_id="beta"),
            workspace_path,
        )

        job_hash = GlobalLearningStore.hash_job("alpha")
        records = store.get_similar_executions(job_hash=job_hash, sheet_num=1)
        assert len(records) == 1

    def test_no_filters_returns_all(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        for i in range(4):
            store.record_outcome(
                _make_outcome(sheet_id=f"job-sheet{i}"),
                workspace_path,
            )

        records = store.get_similar_executions()
        assert len(records) == 4

    def test_respects_limit(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        for i in range(10):
            store.record_outcome(
                _make_outcome(sheet_id=f"job-sheet{i}"),
                workspace_path,
            )

        records = store.get_similar_executions(limit=3)
        assert len(records) == 3


# =========================================================================
# get_optimal_execution_window tests
# =========================================================================


class TestGetOptimalExecutionWindow:
    """Tests for ExecutionMixin.get_optimal_execution_window()."""

    def test_empty_store_returns_empty_window(
        self,
        store: GlobalLearningStore,
    ) -> None:
        result = store.get_optimal_execution_window()
        assert result["optimal_hours"] == []
        assert result["avoid_hours"] == []
        assert result["confidence"] == 0.0
        assert result["sample_count"] == 0

    def test_optimal_hours_from_high_success_recoveries(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Hours with >= 70% success rate and >= 3 samples are optimal."""
        # Record 4 successful recoveries at hour 10 (time_of_day stored as hour)
        # We need to populate the error_recoveries table with time_of_day values.
        # record_error_recovery uses datetime.now().hour, so we insert directly.
        with store._get_connection() as conn:
            import uuid

            for _ in range(4):
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "E101",
                        30.0,
                        25.0,
                        True,
                        datetime.now(tz=UTC).isoformat(),
                        None,
                        10,
                    ),
                )

        result = store.get_optimal_execution_window()
        assert 10 in result["optimal_hours"]
        assert result["sample_count"] == 4

    def test_avoid_hours_from_low_success_recoveries(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Hours with <= 30% success rate and >= 3 samples should be avoided."""
        import uuid

        with store._get_connection() as conn:
            # 4 recoveries at hour 14: 1 success, 3 failures => 25% success
            for i in range(4):
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "E101",
                        30.0,
                        25.0,
                        i == 0,  # only first is success
                        datetime.now(tz=UTC).isoformat(),
                        None,
                        14,
                    ),
                )

        result = store.get_optimal_execution_window()
        assert 14 in result["avoid_hours"]

    def test_hours_with_insufficient_samples_ignored(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Hours with < 3 samples are not classified."""
        import uuid

        with store._get_connection() as conn:
            # Only 2 samples at hour 8 -- not enough for classification
            for _ in range(2):
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "E101",
                        30.0,
                        25.0,
                        True,
                        datetime.now(tz=UTC).isoformat(),
                        None,
                        8,
                    ),
                )

        result = store.get_optimal_execution_window()
        assert 8 not in result["optimal_hours"]
        assert 8 not in result["avoid_hours"]

    def test_filter_by_error_code(
        self,
        store: GlobalLearningStore,
    ) -> None:
        import uuid

        with store._get_connection() as conn:
            # 4 successful at hour 10 for E101
            for _ in range(4):
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "E101",
                        30.0,
                        25.0,
                        True,
                        datetime.now(tz=UTC).isoformat(),
                        None,
                        10,
                    ),
                )
            # 4 failed at hour 10 for E202
            for _ in range(4):
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "E202",
                        30.0,
                        25.0,
                        False,
                        datetime.now(tz=UTC).isoformat(),
                        None,
                        10,
                    ),
                )

        # With E101 filter: hour 10 should be optimal
        result_e101 = store.get_optimal_execution_window(error_code="E101")
        assert 10 in result_e101["optimal_hours"]

        # With E202 filter: hour 10 should be avoided
        result_e202 = store.get_optimal_execution_window(error_code="E202")
        assert 10 in result_e202["avoid_hours"]

    def test_confidence_scales_with_sample_count(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Confidence = min(total_samples / 50, 1.0)."""
        import uuid

        with store._get_connection() as conn:
            for _ in range(25):
                conn.execute(
                    """
                    INSERT INTO error_recoveries
                    (id, error_code, suggested_wait, actual_wait,
                     recovery_success, recorded_at, model, time_of_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "E101",
                        30.0,
                        25.0,
                        True,
                        datetime.now(tz=UTC).isoformat(),
                        None,
                        10,
                    ),
                )

        result = store.get_optimal_execution_window()
        assert result["confidence"] == pytest.approx(25 / 50)
        assert result["sample_count"] == 25


# =========================================================================
# Workspace clustering tests
# =========================================================================


class TestWorkspaceClustering:
    """Tests for workspace clustering methods."""

    def test_get_workspace_cluster_returns_none_for_unknown(
        self,
        store: GlobalLearningStore,
    ) -> None:
        assert store.get_workspace_cluster("unknown_hash") is None

    def test_assign_and_get_workspace_cluster(
        self,
        store: GlobalLearningStore,
    ) -> None:
        store.assign_workspace_cluster("ws_hash_1", "cluster-A")
        result = store.get_workspace_cluster("ws_hash_1")
        assert result == "cluster-A"

    def test_reassign_workspace_cluster(
        self,
        store: GlobalLearningStore,
    ) -> None:
        store.assign_workspace_cluster("ws_hash_1", "cluster-A")
        store.assign_workspace_cluster("ws_hash_1", "cluster-B")
        result = store.get_workspace_cluster("ws_hash_1")
        assert result == "cluster-B"

    def test_get_similar_workspaces(
        self,
        store: GlobalLearningStore,
    ) -> None:
        store.assign_workspace_cluster("ws1", "cluster-X")
        store.assign_workspace_cluster("ws2", "cluster-X")
        store.assign_workspace_cluster("ws3", "cluster-Y")

        similar = store.get_similar_workspaces("cluster-X")
        assert set(similar) == {"ws1", "ws2"}

    def test_get_similar_workspaces_empty_cluster(
        self,
        store: GlobalLearningStore,
    ) -> None:
        result = store.get_similar_workspaces("nonexistent-cluster")
        assert result == []

    def test_get_similar_workspaces_respects_limit(
        self,
        store: GlobalLearningStore,
    ) -> None:
        for i in range(10):
            store.assign_workspace_cluster(f"ws{i}", "big-cluster")

        result = store.get_similar_workspaces("big-cluster", limit=3)
        assert len(result) == 3

    def test_get_similar_workspaces_ordered_by_assigned_at_desc(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Most recently assigned workspaces should come first."""
        import time

        store.assign_workspace_cluster("ws_old", "cluster-Z")
        time.sleep(0.01)  # small delay to ensure different timestamps
        store.assign_workspace_cluster("ws_new", "cluster-Z")

        result = store.get_similar_workspaces("cluster-Z", limit=10)
        assert result[0] == "ws_new"
        assert result[1] == "ws_old"

    def test_workspace_cluster_with_real_hashes(
        self,
        store: GlobalLearningStore,
        tmp_path: Path,
    ) -> None:
        """Integration test using real workspace hash values."""
        ws1 = tmp_path / "project-a"
        ws1.mkdir()
        ws2 = tmp_path / "project-b"
        ws2.mkdir()

        hash1 = GlobalLearningStore.hash_workspace(ws1)
        hash2 = GlobalLearningStore.hash_workspace(ws2)

        store.assign_workspace_cluster(hash1, "python-projects")
        store.assign_workspace_cluster(hash2, "python-projects")

        similar = store.get_similar_workspaces("python-projects")
        assert hash1 in similar
        assert hash2 in similar


# =========================================================================
# _extract_sheet_num tests
# =========================================================================


class TestExtractSheetNum:
    """Tests for ExecutionMixin._extract_sheet_num() helper."""

    def test_job_sheet_format(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("job-sheet1") == 1

    def test_job_dash_number_format(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("job-1") == 1

    def test_sheet_number_format(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("sheet1") == 1

    def test_plain_number_format(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("1") == 1

    def test_multi_digit_number(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("job-sheet42") == 42

    def test_no_number_returns_zero(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("no-number-here") == 0

    def test_empty_string_returns_zero(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("") == 0

    def test_number_in_middle_ignored(self, store: GlobalLearningStore) -> None:
        """Only the trailing number matters."""
        assert store._extract_sheet_num("job3-sheetX") == 0

    def test_number_at_end_with_prefix(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("review-task-17") == 17

    def test_zero_sheet_number(self, store: GlobalLearningStore) -> None:
        assert store._extract_sheet_num("sheet0") == 0


# =========================================================================
# _calculate_confidence tests
# =========================================================================


class TestCalculateConfidence:
    """Tests for ExecutionMixin._calculate_confidence() helper."""

    def test_perfect_outcome_high_confidence(
        self,
        store: GlobalLearningStore,
    ) -> None:
        outcome = _make_outcome(
            validation_pass_rate=1.0,
            retry_count=0,
            success_without_retry=True,
        )
        confidence = store._calculate_confidence(outcome)
        # 1.0 base - 0.0 penalty + 0.1 boost = 1.1 -> clamped to 1.0
        assert confidence == pytest.approx(1.0)

    def test_high_retry_count_penalizes_confidence(
        self,
        store: GlobalLearningStore,
    ) -> None:
        outcome = _make_outcome(
            validation_pass_rate=1.0,
            retry_count=5,
            success_without_retry=False,
        )
        confidence = store._calculate_confidence(outcome)
        # 1.0 - (0.1 * 5) = 0.5
        assert confidence == pytest.approx(0.5)

    def test_retry_penalty_capped_at_five(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Retry penalty is capped at min(retry_count, 5) * 0.1."""
        outcome = _make_outcome(
            validation_pass_rate=1.0,
            retry_count=10,
            success_without_retry=False,
        )
        confidence = store._calculate_confidence(outcome)
        # 1.0 - (0.1 * 5) = 0.5 (capped at 5)
        assert confidence == pytest.approx(0.5)

    def test_success_without_retry_boosts_confidence(
        self,
        store: GlobalLearningStore,
    ) -> None:
        outcome = _make_outcome(
            validation_pass_rate=0.8,
            retry_count=0,
            success_without_retry=True,
        )
        confidence = store._calculate_confidence(outcome)
        # 0.8 - 0.0 + 0.1 = 0.9
        assert confidence == pytest.approx(0.9)

    def test_zero_validation_with_retries_gives_zero(
        self,
        store: GlobalLearningStore,
    ) -> None:
        outcome = _make_outcome(
            validation_pass_rate=0.0,
            retry_count=5,
            success_without_retry=False,
        )
        confidence = store._calculate_confidence(outcome)
        # 0.0 - 0.5 = -0.5 -> clamped to 0.0
        assert confidence == pytest.approx(0.0)

    def test_confidence_clamped_between_zero_and_one(
        self,
        store: GlobalLearningStore,
    ) -> None:
        # Very low outcome
        low = _make_outcome(
            validation_pass_rate=0.0,
            retry_count=10,
            success_without_retry=False,
        )
        assert store._calculate_confidence(low) >= 0.0

        # Very high outcome
        high = _make_outcome(
            validation_pass_rate=1.0,
            retry_count=0,
            success_without_retry=True,
        )
        assert store._calculate_confidence(high) <= 1.0

    def test_medium_validation_no_retry(
        self,
        store: GlobalLearningStore,
    ) -> None:
        outcome = _make_outcome(
            validation_pass_rate=0.5,
            retry_count=0,
            success_without_retry=False,
        )
        confidence = store._calculate_confidence(outcome)
        # 0.5 - 0.0 = 0.5
        assert confidence == pytest.approx(0.5)

    def test_partial_validation_with_some_retries(
        self,
        store: GlobalLearningStore,
    ) -> None:
        outcome = _make_outcome(
            validation_pass_rate=0.75,
            retry_count=2,
            success_without_retry=False,
        )
        confidence = store._calculate_confidence(outcome)
        # 0.75 - (0.1 * 2) = 0.55
        assert confidence == pytest.approx(0.55)


# =========================================================================
# Error recovery methods (also in ExecutionMixin)
# =========================================================================


class TestErrorRecoveryMethods:
    """Tests for error recovery recording and learned wait times."""

    def test_record_error_recovery_returns_id(
        self,
        store: GlobalLearningStore,
    ) -> None:
        record_id = store.record_error_recovery(
            error_code="E101",
            suggested_wait=30.0,
            actual_wait=25.0,
            recovery_success=True,
        )
        assert isinstance(record_id, str)
        assert len(record_id) == 36

    def test_get_learned_wait_time_insufficient_samples(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Returns None when fewer than min_samples successful recoveries."""
        store.record_error_recovery("E101", 30.0, 25.0, True)
        store.record_error_recovery("E101", 30.0, 20.0, True)
        # Only 2 successful, min_samples=3 by default
        result = store.get_learned_wait_time("E101")
        assert result is None

    def test_get_learned_wait_time_with_enough_samples(
        self,
        store: GlobalLearningStore,
    ) -> None:
        for wait in [20.0, 25.0, 30.0]:
            store.record_error_recovery("E101", 30.0, wait, True)

        result = store.get_learned_wait_time("E101")
        assert result is not None
        # avg = 25.0, min = 20.0, result = (25.0 + 20.0) / 2 = 22.5
        assert result == pytest.approx(22.5)

    def test_get_learned_wait_time_ignores_failures(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Only successful recoveries contribute to learned wait time."""
        store.record_error_recovery("E101", 30.0, 10.0, True)
        store.record_error_recovery("E101", 30.0, 15.0, True)
        store.record_error_recovery("E101", 30.0, 20.0, True)
        store.record_error_recovery("E101", 30.0, 5.0, False)  # ignored

        result = store.get_learned_wait_time("E101")
        assert result is not None
        # avg = 15.0, min = 10.0, result = (15.0 + 10.0) / 2 = 12.5
        assert result == pytest.approx(12.5)

    def test_get_learned_wait_time_with_fallback_static(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Falls back to static delay when no data available."""
        delay, confidence, strategy = store.get_learned_wait_time_with_fallback(
            error_code="E999",
            static_delay=60.0,
        )
        assert delay == 60.0
        assert confidence == 0.0
        assert strategy == "static_fallback"

    def test_get_learned_wait_time_with_fallback_high_confidence(
        self,
        store: GlobalLearningStore,
    ) -> None:
        """Uses learned delay when confidence is high enough."""
        # Record 10 successful recoveries for high confidence (10/10 = 1.0)
        for _ in range(10):
            store.record_error_recovery("E101", 60.0, 25.0, True)

        delay, confidence, strategy = store.get_learned_wait_time_with_fallback(
            error_code="E101",
            static_delay=60.0,
        )
        assert strategy == "global_learned"
        assert confidence == pytest.approx(1.0)
        # learned = (25.0 + 25.0) / 2 = 25.0, floor = 60.0 * 0.5 = 30.0
        # final = max(25.0, 30.0) = 30.0
        assert delay == pytest.approx(30.0)

    def test_get_error_recovery_sample_count(
        self,
        store: GlobalLearningStore,
    ) -> None:
        store.record_error_recovery("E101", 30.0, 25.0, True)
        store.record_error_recovery("E101", 30.0, 20.0, True)
        store.record_error_recovery("E101", 30.0, 15.0, False)

        count = store.get_error_recovery_sample_count("E101")
        assert count == 2  # only successful


# =========================================================================
# Integration / edge case tests
# =========================================================================


class TestIntegration:
    """Integration and edge-case tests combining multiple execution methods."""

    def test_record_and_query_round_trip(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        """Full cycle: record an outcome, get stats, query recent, find similar."""
        outcome = _make_outcome(
            sheet_id="integration-sheet5",
            job_id="integration-job",
            execution_duration=60.0,
            retry_count=1,
            validation_pass_rate=0.8,
            success_without_retry=False,
        )
        exec_id = store.record_outcome(outcome, workspace_path, model="opus")

        # Stats reflect the new record
        stats = store.get_execution_stats()
        assert stats["total_executions"] == 1

        # Recent shows it
        recent = store.get_recent_executions(limit=5)
        assert len(recent) == 1
        assert recent[0].id == exec_id

        # Similar by job hash
        job_hash = GlobalLearningStore.hash_job("integration-job")
        similar = store.get_similar_executions(job_hash=job_hash)
        assert len(similar) == 1
        assert similar[0].sheet_num == 5

    def test_different_workspaces_different_hashes(
        self,
        store: GlobalLearningStore,
        tmp_path: Path,
    ) -> None:
        ws1 = tmp_path / "workspace-alpha"
        ws1.mkdir()
        ws2 = tmp_path / "workspace-beta"
        ws2.mkdir()

        store.record_outcome(_make_outcome(sheet_id="a-sheet1"), ws1)
        store.record_outcome(_make_outcome(sheet_id="b-sheet1"), ws2)

        records = store.get_recent_executions(limit=10)
        hashes = {r.workspace_hash for r in records}
        assert len(hashes) == 2

    def test_large_batch_of_records(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        """Verify the store handles many records efficiently."""
        for i in range(50):
            store.record_outcome(
                _make_outcome(sheet_id=f"job-sheet{i}"),
                workspace_path,
            )

        stats = store.get_execution_stats()
        assert stats["total_executions"] == 50

        recent = store.get_recent_executions(limit=10)
        assert len(recent) == 10

    def test_clear_all_removes_executions(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        store.record_outcome(_make_outcome(), workspace_path)
        assert store.get_execution_stats()["total_executions"] == 1

        store.clear_all()
        assert store.get_execution_stats()["total_executions"] == 0

    def test_cluster_and_execution_combined_workflow(
        self,
        store: GlobalLearningStore,
        workspace_path: Path,
    ) -> None:
        """Assign workspaces to clusters and query executions by workspace hash."""
        ws_hash = GlobalLearningStore.hash_workspace(workspace_path)
        store.assign_workspace_cluster(ws_hash, "ml-projects")

        store.record_outcome(_make_outcome(sheet_id="train-sheet1"), workspace_path)
        store.record_outcome(_make_outcome(sheet_id="train-sheet2"), workspace_path)

        # Verify cluster assignment
        cluster = store.get_workspace_cluster(ws_hash)
        assert cluster == "ml-projects"

        # Verify executions for this workspace
        records = store.get_recent_executions(workspace_hash=ws_hash)
        assert len(records) == 2

        # Similar workspaces in the same cluster
        similar_ws = store.get_similar_workspaces("ml-projects")
        assert ws_hash in similar_ws
