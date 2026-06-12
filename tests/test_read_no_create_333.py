"""#333: pure reads must never create workspace state.

The 0-byte ``.marianne-state.db`` artifacts came from read paths:
constructing ``SQLiteStateBackend`` and calling ``load()`` (or any other
read) against a workspace with no state file ran the schema migrations
and left an empty database behind — making the workspace look like it
holds local state when the conductor's registry is the source of truth.

Contract: every read method short-circuits with its not-found value when
the database file doesn't exist, leaving the filesystem untouched.
Writers (``save``, ``record_execution``) still create on first use.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.state import SQLiteStateBackend


def _backend(tmp_path: Path) -> tuple[SQLiteStateBackend, Path]:
    db_path = tmp_path / ".marianne-state.db"
    return SQLiteStateBackend(db_path), db_path


class TestReadsDoNotCreate:
    async def test_load_missing_db(self, tmp_path: Path) -> None:
        backend, db_path = _backend(tmp_path)
        assert await backend.load("nope") is None
        assert not db_path.exists()

    async def test_delete_missing_db(self, tmp_path: Path) -> None:
        backend, db_path = _backend(tmp_path)
        assert await backend.delete("nope") is False
        assert not db_path.exists()

    async def test_list_jobs_missing_db(self, tmp_path: Path) -> None:
        backend, db_path = _backend(tmp_path)
        assert await backend.list_jobs() == []
        assert not db_path.exists()

    async def test_execution_history_missing_db(self, tmp_path: Path) -> None:
        backend, db_path = _backend(tmp_path)
        assert await backend.get_execution_history("nope") == []
        assert await backend.get_execution_history_count("nope") == 0
        assert not db_path.exists()

    async def test_statistics_and_query_missing_db(self, tmp_path: Path) -> None:
        backend, db_path = _backend(tmp_path)
        assert await backend.get_job_statistics("nope") == {}
        assert await backend.query_jobs() == []
        assert not db_path.exists()


class TestWritersStillCreate:
    async def test_save_creates_db(self, tmp_path: Path) -> None:
        backend, db_path = _backend(tmp_path)
        state = CheckpointState(
            job_id="writer-job",
            job_name="writer-job",
            total_sheets=1,
            status=JobStatus.RUNNING,
        )
        await backend.save(state)
        assert db_path.exists()

        loaded = await backend.load("writer-job")
        assert loaded is not None
        assert loaded.job_id == "writer-job"
