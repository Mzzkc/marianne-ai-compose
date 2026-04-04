"""Test that mozart run detects changed score files on re-run (#103).

Bug: When a score file is modified after a completed run, `mozart run`
picks up the previous run's completed state instead of starting fresh.
The user has to know to pass `--fresh` to get a new run.

Fix: In submit_job, when a COMPLETED job exists and --fresh is not set,
compare the score file's modification time against the job's completed_at
timestamp. If the score file was modified after completion, auto-set
fresh=True and log a message.
"""

from __future__ import annotations

import time
from pathlib import Path

from mozart.daemon.manager import _should_auto_fresh


class TestShouldAutoFresh:
    """Tests for _should_auto_fresh() stale completion detection."""

    def test_score_modified_after_completion(self, tmp_path: Path) -> None:
        """Score file modified after completion → should auto-fresh."""
        score_file = tmp_path / "test.yaml"
        score_file.write_text("name: test")

        # completed_at is 10 seconds before the file's mtime
        mtime = score_file.stat().st_mtime
        completed_at = mtime - 10.0

        assert _should_auto_fresh(score_file, completed_at) is True

    def test_score_not_modified_after_completion(self, tmp_path: Path) -> None:
        """Score file NOT modified after completion → no auto-fresh."""
        score_file = tmp_path / "test.yaml"
        score_file.write_text("name: test")

        # completed_at is 10 seconds AFTER the file's mtime
        mtime = score_file.stat().st_mtime
        completed_at = mtime + 10.0

        assert _should_auto_fresh(score_file, completed_at) is False

    def test_no_completed_at(self, tmp_path: Path) -> None:
        """No completed_at timestamp → no auto-fresh (can't compare)."""
        score_file = tmp_path / "test.yaml"
        score_file.write_text("name: test")

        assert _should_auto_fresh(score_file, None) is False

    def test_score_file_missing(self, tmp_path: Path) -> None:
        """Score file doesn't exist → no auto-fresh (error handled elsewhere)."""
        score_file = tmp_path / "nonexistent.yaml"
        assert _should_auto_fresh(score_file, time.time()) is False

    def test_score_modified_same_second(self, tmp_path: Path) -> None:
        """Score file mtime == completed_at → no auto-fresh (same run)."""
        score_file = tmp_path / "test.yaml"
        score_file.write_text("name: test")

        mtime = score_file.stat().st_mtime
        completed_at = mtime  # Exact same time

        assert _should_auto_fresh(score_file, completed_at) is False

    def test_score_modified_slightly_after(self, tmp_path: Path) -> None:
        """Score file mtime slightly after completed_at → auto-fresh."""
        score_file = tmp_path / "test.yaml"
        score_file.write_text("name: test")

        mtime = score_file.stat().st_mtime
        # completed_at is 2 seconds before mtime (safely past 1s tolerance)
        completed_at = mtime - 2.0

        assert _should_auto_fresh(score_file, completed_at) is True

    def test_tolerance_for_filesystem_granularity(self, tmp_path: Path) -> None:
        """Small mtime differences within 1s tolerance → no auto-fresh.

        Filesystem timestamp granularity varies (FAT32: 2s, NTFS: 100ns,
        ext4: 1ns). Use 1-second tolerance to avoid false positives.
        """
        score_file = tmp_path / "test.yaml"
        score_file.write_text("name: test")

        mtime = score_file.stat().st_mtime
        # completed_at is 0.5s before mtime (within tolerance)
        completed_at = mtime - 0.5

        assert _should_auto_fresh(score_file, completed_at) is False
