"""Tests for mozart.prompts.preamble module.

Covers build_preamble() for first-run and retry scenarios,
XML tag structure, workspace inclusion, and parallel execution notes.
"""

from pathlib import Path

import pytest

from marianne.prompts.preamble import build_preamble


class TestBuildPreamble:
    """Tests for build_preamble()."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        return tmp_path / "workspace"

    def test_first_run_basic(self, workspace: Path) -> None:
        """First-run preamble includes identity, workspace, and validation pointer."""
        result = build_preamble(
            sheet_num=2, total_sheets=5, workspace=workspace,
        )
        assert "sheet 2 of 5" in result
        assert str(workspace) in result
        assert "validation" in result.lower()
        assert "background processes" in result.lower()

    def test_first_run_with_parallel(self, workspace: Path) -> None:
        """Parallel note appears when is_parallel=True."""
        result = build_preamble(
            sheet_num=1, total_sheets=3, workspace=workspace,
            is_parallel=True,
        )
        assert "concurrently" in result

    def test_first_run_without_parallel(self, workspace: Path) -> None:
        """No parallel note when is_parallel=False (default)."""
        result = build_preamble(
            sheet_num=1, total_sheets=3, workspace=workspace,
            is_parallel=False,
        )
        assert "concurrently" not in result

    def test_retry_warning(self, workspace: Path) -> None:
        """Retry preamble includes RETRY header and failure study instruction."""
        result = build_preamble(
            sheet_num=1, total_sheets=3, workspace=workspace,
            retry_count=2,
        )
        assert "RETRY #2" in result
        assert "previous attempt failed" in result.lower()
        assert "do not repeat the same approach" in result.lower()

    def test_retry_suppresses_parallel_note(self, workspace: Path) -> None:
        """Retry preamble does not include parallel note (even if parallel)."""
        result = build_preamble(
            sheet_num=1, total_sheets=3, workspace=workspace,
            retry_count=1, is_parallel=True,
        )
        assert "concurrently" not in result

    def test_proper_xml_tags(self, workspace: Path) -> None:
        """Preamble is wrapped in <mozart-preamble> tags."""
        result = build_preamble(
            sheet_num=1, total_sheets=1, workspace=workspace,
        )
        assert result.startswith("<mozart-preamble>")
        assert result.endswith("</mozart-preamble>")

    def test_retry_has_xml_tags(self, workspace: Path) -> None:
        """Retry preamble is also wrapped in <mozart-preamble> tags."""
        result = build_preamble(
            sheet_num=1, total_sheets=1, workspace=workspace,
            retry_count=3,
        )
        assert result.startswith("<mozart-preamble>")
        assert result.endswith("</mozart-preamble>")

    def test_workspace_path_included(self, workspace: Path) -> None:
        """Workspace path is included as-is in the preamble."""
        result = build_preamble(
            sheet_num=1, total_sheets=1, workspace=workspace,
        )
        assert f"Workspace: {workspace}" in result

    def test_retry_count_zero_is_first_run(self, workspace: Path) -> None:
        """retry_count=0 produces first-run preamble, not retry."""
        result = build_preamble(
            sheet_num=1, total_sheets=1, workspace=workspace,
            retry_count=0,
        )
        assert "RETRY" not in result
