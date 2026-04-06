"""Tests for mozart.review module."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config import AIReviewConfig
from marianne.review.scorer import (
    AIReviewer,
    AIReviewResult,
    GitDiffProvider,
    ReviewIssue,
)


class TestReviewIssue:
    """Tests for ReviewIssue dataclass."""

    def test_create_issue(self):
        """Test creating a review issue."""
        issue = ReviewIssue(
            severity="high",
            category="security",
            description="Hardcoded API key detected",
            suggestion="Use environment variables",
        )
        assert issue.severity == "high"
        assert issue.category == "security"
        assert issue.suggestion == "Use environment variables"

    def test_to_dict(self):
        """Test converting issue to dictionary."""
        issue = ReviewIssue(
            severity="medium",
            category="code_quality",
            description="Complex function",
        )
        data = issue.to_dict()
        assert data["severity"] == "medium"
        assert data["suggestion"] is None


class TestAIReviewResult:
    """Tests for AIReviewResult dataclass."""

    def test_passed_threshold(self):
        """Test passed property at threshold."""
        result = AIReviewResult(score=60)
        assert result.passed is True

    def test_passed_below_threshold(self):
        """Test passed property below threshold."""
        result = AIReviewResult(score=59)
        assert result.passed is False

    def test_high_quality_threshold(self):
        """Test high_quality property at threshold."""
        result = AIReviewResult(score=80)
        assert result.high_quality is True

    def test_high_quality_below(self):
        """Test high_quality property below threshold."""
        result = AIReviewResult(score=79)
        assert result.high_quality is False

    def test_to_dict(self):
        """Test converting result to dictionary."""
        result = AIReviewResult(
            score=85,
            components={"code_quality": 25, "security": 20},
            issues=[
                ReviewIssue("low", "documentation", "Missing docstring")
            ],
            summary="Good code",
        )
        data = result.to_dict()
        assert data["score"] == 85
        assert data["passed"] is True
        assert data["high_quality"] is True
        assert len(data["issues"]) == 1


class TestGitDiffProvider:
    """Tests for GitDiffProvider."""

    def test_get_diff_no_repo(self, tmp_path: Path):
        """Test getting diff from non-git directory."""
        provider = GitDiffProvider()
        # tmp_path is not a git repo
        diff = provider.get_diff(tmp_path)
        assert diff == ""

    def test_get_diff_empty_repo(self, tmp_path: Path):
        """Test getting diff from empty git repo."""
        import subprocess

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True,
        )

        provider = GitDiffProvider()
        diff = provider.get_diff(tmp_path)
        assert diff == ""  # No changes in empty repo


class TestAIReviewer:
    """Tests for AIReviewer."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock backend."""
        backend = MagicMock()
        backend.execute = AsyncMock()
        return backend

    @pytest.fixture
    def config(self):
        """Create default config."""
        return AIReviewConfig(enabled=True)

    @pytest.fixture
    def mock_diff_provider(self):
        """Create a mock diff provider."""
        provider = MagicMock()
        provider.get_diff = MagicMock(return_value="diff content")
        return provider

    @pytest.mark.asyncio
    async def test_review_disabled(self, mock_backend, mock_diff_provider):
        """Test review returns 100 when disabled."""
        config = AIReviewConfig(enabled=False)
        reviewer = AIReviewer(mock_backend, config, mock_diff_provider)

        result = await reviewer.review(Path("/tmp"))

        assert result.score == 100
        assert result.summary == "AI review disabled"
        mock_backend.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_review_no_diff(self, mock_backend, config):
        """Test review returns 100 when no diff."""
        empty_provider = MagicMock()
        empty_provider.get_diff = MagicMock(return_value="")

        reviewer = AIReviewer(mock_backend, config, empty_provider)
        result = await reviewer.review(Path("/tmp"))

        assert result.score == 100
        assert "No changes" in result.summary

    @pytest.mark.asyncio
    async def test_review_success(self, mock_backend, config, mock_diff_provider):
        """Test successful review parsing."""
        response_json = json.dumps({
            "score": 85,
            "components": {
                "code_quality": 25,
                "test_coverage": 20,
                "security": 25,
                "documentation": 15,
            },
            "issues": [
                {
                    "severity": "low",
                    "category": "documentation",
                    "description": "Missing docstring",
                    "suggestion": "Add docstring",
                }
            ],
            "summary": "Good code quality",
        })

        mock_backend.execute.return_value = MagicMock(
            success=True,
            output=response_json,
        )

        reviewer = AIReviewer(mock_backend, config, mock_diff_provider)
        result = await reviewer.review(Path("/tmp"))

        assert result.score == 85
        assert result.high_quality is True
        assert len(result.issues) == 1
        assert result.issues[0].severity == "low"

    @pytest.mark.asyncio
    async def test_review_parse_error(self, mock_backend, config, mock_diff_provider):
        """Test handling of malformed response."""
        mock_backend.execute.return_value = MagicMock(
            success=True,
            output="This is not JSON at all",
        )

        reviewer = AIReviewer(mock_backend, config, mock_diff_provider)
        result = await reviewer.review(Path("/tmp"))

        # Parse failures return score=0 (not a misleading middling score)
        assert result.score == 0
        assert result.error is not None

    def test_evaluate_result_high_quality(self, mock_backend, config):
        """Test evaluating high quality result."""
        reviewer = AIReviewer(mock_backend, config)
        result = AIReviewResult(score=90, summary="Excellent")

        passed, msg = reviewer.evaluate_result(result)

        assert passed is True
        assert "High quality" in msg

    def test_evaluate_result_acceptable(self, mock_backend, config):
        """Test evaluating acceptable result."""
        reviewer = AIReviewer(mock_backend, config)
        result = AIReviewResult(score=70, summary="OK")

        passed, msg = reviewer.evaluate_result(result)

        assert passed is True
        assert "Acceptable" in msg

    def test_evaluate_result_below_threshold(self, mock_backend, config):
        """Test evaluating below threshold result."""
        reviewer = AIReviewer(mock_backend, config)
        result = AIReviewResult(score=50, summary="Needs work")

        passed, msg = reviewer.evaluate_result(result)

        assert passed is False
        assert "Below threshold" in msg
