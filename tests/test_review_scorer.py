"""Tests for mozart.review.scorer module.

Covers ReviewIssue, AIReviewResult, GitDiffProvider, AIReviewer,
and _parse_review_response / evaluate_result methods.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.review.scorer import (
    AIReviewResult,
    AIReviewer,
    GitDiffProvider,
    ReviewIssue,
)

# ─── ReviewIssue ──────────────────────────────────────────────────────


class TestReviewIssue:
    """Tests for ReviewIssue dataclass."""

    def test_basic_creation(self):
        issue = ReviewIssue(
            severity="high",
            category="security",
            description="Hardcoded secret",
            suggestion="Use environment variables",
        )
        assert issue.severity == "high"
        assert issue.category == "security"

    def test_to_dict(self):
        issue = ReviewIssue(
            severity="medium",
            category="code_quality",
            description="Duplicate code",
            suggestion="Extract helper",
        )
        d = issue.to_dict()
        assert d["severity"] == "medium"
        assert d["category"] == "code_quality"
        assert d["description"] == "Duplicate code"
        assert d["suggestion"] == "Extract helper"

    def test_suggestion_optional(self):
        issue = ReviewIssue(
            severity="low",
            category="documentation",
            description="Missing docstring",
        )
        assert issue.suggestion is None
        assert issue.to_dict()["suggestion"] is None


# ─── AIReviewResult ───────────────────────────────────────────────────


class TestAIReviewResult:
    """Tests for AIReviewResult dataclass."""

    def test_passed_at_60(self):
        """Score >= 60 passes."""
        assert AIReviewResult(score=60).passed
        assert AIReviewResult(score=100).passed
        assert not AIReviewResult(score=59).passed
        assert not AIReviewResult(score=0).passed

    def test_high_quality_at_80(self):
        """Score >= 80 is high quality."""
        assert AIReviewResult(score=80).high_quality
        assert AIReviewResult(score=100).high_quality
        assert not AIReviewResult(score=79).high_quality

    def test_to_dict_includes_computed_props(self):
        result = AIReviewResult(
            score=85,
            components={"code_quality": 25},
            issues=[ReviewIssue(severity="low", category="docs", description="test")],
            summary="Good code",
        )
        d = result.to_dict()
        assert d["score"] == 85
        assert d["passed"] is True
        assert d["high_quality"] is True
        assert len(d["issues"]) == 1
        assert d["error"] is None

    def test_to_dict_with_error(self):
        result = AIReviewResult(score=0, error="Parse failed")
        d = result.to_dict()
        assert d["score"] == 0
        assert d["passed"] is False
        assert d["error"] == "Parse failed"


# ─── GitDiffProvider ──────────────────────────────────────────────────


class TestGitDiffProvider:
    """Tests for GitDiffProvider."""

    def test_get_diff_with_since_commit(self, tmp_path: Path):
        """Diff from a specific commit uses git diff commit HEAD."""
        provider = GitDiffProvider(since_commit="abc123")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="diff content")
            result = provider.get_diff(tmp_path)
            assert result == "diff content"
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args == ["git", "diff", "abc123", "HEAD", "--"]

    def test_get_diff_without_commit_combines_staged_unstaged(self, tmp_path: Path):
        """Without since_commit, combines staged and unstaged diffs."""
        provider = GitDiffProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="staged\n"),
                MagicMock(stdout="unstaged\n"),
            ]
            result = provider.get_diff(tmp_path)
            assert result == "staged\nunstaged\n"
            assert mock_run.call_count == 2

    def test_get_diff_timeout(self, tmp_path: Path):
        """Timeout returns empty string."""
        provider = GitDiffProvider(since_commit="abc")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            result = provider.get_diff(tmp_path)
            assert result == ""

    def test_get_diff_git_not_found(self, tmp_path: Path):
        """Missing git raises RuntimeError."""
        provider = GitDiffProvider(since_commit="abc")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="Git is not installed"):
                provider.get_diff(tmp_path)

    def test_get_diff_subprocess_error(self, tmp_path: Path):
        """Other subprocess errors return empty string."""
        provider = GitDiffProvider(since_commit="abc")
        with patch("subprocess.run", side_effect=subprocess.SubprocessError("fail")):
            result = provider.get_diff(tmp_path)
            assert result == ""


# ─── AIReviewer ───────────────────────────────────────────────────────


class TestAIReviewer:
    """Tests for AIReviewer."""

    @pytest.fixture
    def mock_backend(self) -> MagicMock:
        backend = MagicMock()
        backend.execute = AsyncMock()
        return backend

    @pytest.fixture
    def mock_config(self) -> MagicMock:
        config = MagicMock()
        config.enabled = True
        config.review_prompt_template = None
        config.target_score = 80
        config.min_score = 60
        return config

    @pytest.fixture
    def reviewer(self, mock_backend: MagicMock, mock_config: MagicMock) -> AIReviewer:
        diff_provider = MagicMock()
        return AIReviewer(mock_backend, mock_config, diff_provider=diff_provider)

    @pytest.mark.asyncio
    async def test_review_disabled(self, mock_backend: MagicMock):
        """When disabled, returns score=100 with status message."""
        config = MagicMock()
        config.enabled = False
        reviewer = AIReviewer(mock_backend, config)
        result = await reviewer.review(Path("/tmp/ws"))
        assert result.score == 100
        assert "disabled" in result.summary

    @pytest.mark.asyncio
    async def test_review_empty_diff(self, reviewer: AIReviewer):
        """Empty diff returns score=100."""
        reviewer.diff_provider.get_diff.return_value = ""
        result = await reviewer.review(Path("/tmp/ws"))
        assert result.score == 100
        assert "No changes" in result.summary

    @pytest.mark.asyncio
    async def test_review_whitespace_only_diff(self, reviewer: AIReviewer):
        """Whitespace-only diff treated as empty."""
        reviewer.diff_provider.get_diff.return_value = "   \n\t  "
        result = await reviewer.review(Path("/tmp/ws"))
        assert result.score == 100

    @pytest.mark.asyncio
    async def test_review_successful(self, reviewer: AIReviewer, mock_backend: MagicMock):
        """Successful review parses JSON response."""
        reviewer.diff_provider.get_diff.return_value = "diff --git a/foo.py"
        response_json = json.dumps({
            "score": 85,
            "components": {"code_quality": 25, "test_coverage": 20, "security": 22, "documentation": 18},
            "issues": [{"severity": "low", "category": "docs", "description": "Missing doc"}],
            "summary": "Good overall",
        })
        mock_backend.execute.return_value = MagicMock(
            success=True, output=response_json, error_message=None,
        )
        result = await reviewer.review(Path("/tmp/ws"))
        assert result.score == 85
        assert result.summary == "Good overall"
        assert len(result.issues) == 1

    @pytest.mark.asyncio
    async def test_review_backend_failure(self, reviewer: AIReviewer, mock_backend: MagicMock):
        """Backend failure returns score=0."""
        reviewer.diff_provider.get_diff.return_value = "diff content"
        mock_backend.execute.return_value = MagicMock(
            success=False, output="", error_message="API error",
        )
        result = await reviewer.review(Path("/tmp/ws"))
        assert result.score == 0
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_review_exception_handling(self, reviewer: AIReviewer, mock_backend: MagicMock):
        """OSError during review returns score=0 with error."""
        reviewer.diff_provider.get_diff.return_value = "diff content"
        mock_backend.execute.side_effect = OSError("connection failed")
        result = await reviewer.review(Path("/tmp/ws"))
        assert result.score == 0
        assert "connection failed" in result.error

    @pytest.mark.asyncio
    async def test_review_truncates_large_diff(self, reviewer: AIReviewer, mock_backend: MagicMock):
        """Very large diffs are truncated to 50K chars."""
        reviewer.diff_provider.get_diff.return_value = "x" * 60000
        mock_backend.execute.return_value = MagicMock(
            success=True, output='{"score": 70, "summary": "ok"}', error_message=None,
        )
        result = await reviewer.review(Path("/tmp/ws"))
        # Verify it still works (prompt is built with truncated diff)
        assert result.score == 70


# ─── _parse_review_response ──────────────────────────────────────────


class TestParseReviewResponse:
    """Tests for AIReviewer._parse_review_response()."""

    @pytest.fixture
    def reviewer(self) -> AIReviewer:
        config = MagicMock()
        config.enabled = True
        config.target_score = 80
        config.min_score = 60
        return AIReviewer(MagicMock(), config)

    def test_valid_json(self, reviewer: AIReviewer):
        response = json.dumps({
            "score": 75,
            "components": {"code_quality": 20},
            "issues": [],
            "summary": "Decent code",
        })
        result = reviewer._parse_review_response(response)
        assert result.score == 75
        assert result.summary == "Decent code"

    def test_json_with_surrounding_text(self, reviewer: AIReviewer):
        """JSON embedded in markdown/text still parses."""
        response = 'Here is my review:\n```json\n{"score": 90, "summary": "great"}\n```'
        result = reviewer._parse_review_response(response)
        assert result.score == 90

    def test_no_json_in_response(self, reviewer: AIReviewer):
        """Response without JSON returns error."""
        result = reviewer._parse_review_response("No JSON here, just text.")
        assert result.score == 0
        assert result.error is not None

    def test_invalid_json(self, reviewer: AIReviewer):
        """Malformed JSON returns error."""
        result = reviewer._parse_review_response("{invalid json}")
        assert result.score == 0
        assert "JSON parse error" in result.error

    def test_missing_score_field(self, reviewer: AIReviewer):
        """Missing score field defaults to 0."""
        result = reviewer._parse_review_response('{"summary": "no score"}')
        assert result.score == 0

    def test_score_clamped_to_100(self, reviewer: AIReviewer):
        """Score above 100 is clamped."""
        result = reviewer._parse_review_response('{"score": 150}')
        assert result.score == 100

    def test_score_clamped_to_0(self, reviewer: AIReviewer):
        """Negative score is clamped to 0."""
        result = reviewer._parse_review_response('{"score": -10}')
        assert result.score == 0

    def test_issues_parsed(self, reviewer: AIReviewer):
        response = json.dumps({
            "score": 60,
            "issues": [
                {"severity": "high", "category": "security", "description": "SQL injection"},
                {"severity": "low", "category": "docs", "description": "Missing docs"},
            ],
        })
        result = reviewer._parse_review_response(response)
        assert len(result.issues) == 2
        assert result.issues[0].severity == "high"
        assert result.issues[1].category == "docs"

    def test_issues_with_defaults(self, reviewer: AIReviewer):
        """Issues with missing fields use defaults."""
        response = json.dumps({
            "score": 50,
            "issues": [{}],
        })
        result = reviewer._parse_review_response(response)
        assert result.issues[0].severity == "medium"
        assert result.issues[0].category == "code_quality"
        assert result.issues[0].description == "Unknown issue"

    def test_raw_response_preserved(self, reviewer: AIReviewer):
        """Raw response is stored for debugging."""
        response = '{"score": 80}'
        result = reviewer._parse_review_response(response)
        assert result.raw_response == response


# ─── evaluate_result ──────────────────────────────────────────────────


class TestEvaluateResult:
    """Tests for AIReviewer.evaluate_result()."""

    @pytest.fixture
    def reviewer(self) -> AIReviewer:
        config = MagicMock()
        config.target_score = 80
        config.min_score = 60
        return AIReviewer(MagicMock(), config)

    def test_high_quality_pass(self, reviewer: AIReviewer):
        result = AIReviewResult(score=90, summary="Excellent")
        passed, msg = reviewer.evaluate_result(result)
        assert passed
        assert "High quality" in msg

    def test_acceptable_pass(self, reviewer: AIReviewer):
        result = AIReviewResult(score=65, summary="OK")
        passed, msg = reviewer.evaluate_result(result)
        assert passed
        assert "Acceptable" in msg

    def test_below_threshold(self, reviewer: AIReviewer):
        result = AIReviewResult(score=50, summary="Poor")
        passed, msg = reviewer.evaluate_result(result)
        assert not passed
        assert "Below threshold" in msg

    def test_boundary_at_min(self, reviewer: AIReviewer):
        result = AIReviewResult(score=60, summary="Borderline")
        passed, _ = reviewer.evaluate_result(result)
        assert passed

    def test_boundary_at_target(self, reviewer: AIReviewer):
        result = AIReviewResult(score=80, summary="Target")
        passed, msg = reviewer.evaluate_result(result)
        assert passed
        assert "High quality" in msg
