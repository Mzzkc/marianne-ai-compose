"""AI-powered code review scorer for Marianne.

Provides automated quality assessment of code changes after batch execution.
The reviewer analyzes git diffs and produces a quality score (0-100) with
detailed feedback on issues and suggestions.

Score Components:
- Code Quality (30%): Complexity, duplication, pattern adherence
- Test Coverage (25%): New code tested, edge cases
- Security (25%): No secrets, validation, error handling
- Documentation (20%): Public API docs, complex logic explained
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marianne.core.logging import get_logger

if TYPE_CHECKING:
    from marianne.backends.base import Backend
    from marianne.core.config import AIReviewConfig

# Module-level logger for code review scoring
_logger = get_logger("review.scorer")

# Default review prompt template
DEFAULT_REVIEW_PROMPT = """
You are a code reviewer for Marianne AI Compose. Review the following git diff
and provide a quality score from 0-100.

## Git Diff
```
{diff}
```

## Scoring Criteria

Evaluate on these weighted components:

1. **Code Quality (30%)** - 0 to 30 points
   - Reasonable complexity (cyclomatic, cognitive)
   - No obvious code duplication
   - Follows project patterns and conventions
   - Clean, readable code

2. **Test Coverage (25%)** - 0 to 25 points
   - New functions/methods have tests
   - Edge cases considered
   - Tests are meaningful (not just for coverage)

3. **Security (25%)** - 0 to 25 points
   - No hardcoded secrets or API keys
   - Input validation present where needed
   - Safe error handling (no information leaks)
   - No obvious vulnerabilities

4. **Documentation (20%)** - 0 to 20 points
   - Public APIs documented
   - Complex logic has explanatory comments
   - Docstrings present and useful

## Response Format

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):

{{
  "score": <0-100>,
  "components": {{
    "code_quality": <0-30>,
    "test_coverage": <0-25>,
    "security": <0-25>,
    "documentation": <0-20>
  }},
  "issues": [
    {{
      "severity": "critical|high|medium|low",
      "category": "code_quality|test_coverage|security|documentation",
      "description": "Brief issue description",
      "suggestion": "How to fix"
    }}
  ],
  "summary": "One-sentence overall assessment"
}}
"""


@dataclass
class ReviewIssue:
    """A single issue found during code review."""

    severity: str  # critical, high, medium, low
    category: str  # code_quality, test_coverage, security, documentation
    description: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "severity": self.severity,
            "category": self.category,
            "description": self.description,
            "suggestion": self.suggestion,
        }


@dataclass
class AIReviewResult:
    """Result of an AI code review."""

    score: int  # 0-100
    components: dict[str, int] = field(default_factory=dict)
    issues: list[ReviewIssue] = field(default_factory=list)
    summary: str = ""
    raw_response: str = ""
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Check if review passed minimum threshold (60)."""
        return self.score >= 60

    @property
    def high_quality(self) -> bool:
        """Check if review achieved target score (80+)."""
        return self.score >= 80

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "score": self.score,
            "components": self.components,
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
            "passed": self.passed,
            "high_quality": self.high_quality,
            "error": self.error,
        }


class GitDiffProvider:
    """Gets diffs using git commands."""

    def __init__(self, since_commit: str | None = None) -> None:
        """Initialize with optional since commit.

        Args:
            since_commit: Commit hash to diff from. If None, uses staged + unstaged.
        """
        self.since_commit = since_commit

    def get_diff(self, workspace: Path) -> str:
        """Get git diff for the workspace.

        Args:
            workspace: Directory to get diff from.

        Returns:
            Git diff as string, or empty string if not a git repo.
        """
        try:
            if self.since_commit:
                # Diff from specific commit
                # Use "--" separator to prevent flag injection from since_commit
                result = subprocess.run(
                    ["git", "diff", self.since_commit, "HEAD", "--"],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                # Get both staged and unstaged changes
                staged = subprocess.run(
                    ["git", "diff", "--cached"],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                unstaged = subprocess.run(
                    ["git", "diff"],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return staged.stdout + unstaged.stdout

            return result.stdout
        except subprocess.TimeoutExpired:
            _logger.warning("Git diff timed out")
            return ""
        except FileNotFoundError:
            raise RuntimeError(
                "Git is not installed or not found on PATH. "
                "Git is required for code review scoring."
            ) from None
        except (subprocess.SubprocessError, OSError) as e:
            _logger.warning("git_diff_error", error=str(e))
            return ""


class AIReviewer:
    """Performs AI-powered code review using a backend.

    Uses the same backend as Marianne execution to send the diff
    for review and parse the scoring response.
    """

    def __init__(
        self,
        backend: "Backend",
        config: "AIReviewConfig",
        diff_provider: GitDiffProvider | None = None,
    ) -> None:
        """Initialize reviewer.

        Args:
            backend: Execution backend for AI calls.
            config: AI review configuration.
            diff_provider: Provider for git diffs. Defaults to GitDiffProvider.
        """
        self.backend = backend
        self.config = config
        self.diff_provider = diff_provider or GitDiffProvider()

    async def review(self, workspace: Path) -> AIReviewResult:
        """Perform AI review on workspace changes.

        Args:
            workspace: Directory to review.

        Returns:
            AIReviewResult with score and feedback.
        """
        if not self.config.enabled:
            return AIReviewResult(
                score=100,
                summary="AI review disabled",
            )

        # Get the diff
        diff = self.diff_provider.get_diff(workspace)

        if not diff or not diff.strip():
            _logger.debug("No diff to review")
            return AIReviewResult(
                score=100,
                summary="No changes to review",
            )

        # Truncate very large diffs
        max_diff_chars = 50000
        if len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars] + "\n\n... [diff truncated] ..."

        # Build review prompt
        prompt_template = self.config.review_prompt_template or DEFAULT_REVIEW_PROMPT
        prompt = prompt_template.format(diff=diff)

        try:
            # Execute review using backend
            result = await self.backend.execute(prompt)

            if result.success and result.output:
                return self._parse_review_response(result.output)
            else:
                return AIReviewResult(
                    score=0,
                    error=result.error_message or "Review execution failed",
                    summary="Failed to execute review",
                )
        except (OSError, TimeoutError, RuntimeError) as e:
            _logger.error("ai_review_failed", error=str(e))
            return AIReviewResult(
                score=0,
                error=str(e),
                summary="Review failed with exception",
            )

    def _parse_review_response(self, response: str) -> AIReviewResult:
        """Parse the AI review response.

        Args:
            response: Raw response from AI.

        Returns:
            Parsed AIReviewResult.
        """
        try:
            # Try to extract JSON from response
            # Handle cases where response includes markdown or extra text
            json_start = response.find("{")
            json_end = response.rfind("}") + 1

            if json_start == -1 or json_end <= json_start:
                _logger.warning("review_no_json_in_response", response_length=len(response))
                return AIReviewResult(
                    score=0,
                    error="Could not find JSON in response",
                    raw_response=response,
                    summary="Review response parsing failed",
                )

            json_str = response[json_start:json_end]
            data = json.loads(json_str)

            # Extract fields
            raw_score = data.get("score")
            if raw_score is None:
                _logger.warning(
                    "review_missing_score_field",
                    response_keys=list(data.keys()),
                )
            score = int(raw_score) if raw_score is not None else 0
            components = data.get("components", {})
            summary = data.get("summary", "")

            # Parse issues
            issues: list[ReviewIssue] = []
            for issue_data in data.get("issues", []):
                issues.append(
                    ReviewIssue(
                        severity=issue_data.get("severity", "medium"),
                        category=issue_data.get("category", "code_quality"),
                        description=issue_data.get("description", "Unknown issue"),
                        suggestion=issue_data.get("suggestion"),
                    )
                )

            return AIReviewResult(
                score=min(100, max(0, score)),  # Clamp to 0-100
                components=components,
                issues=issues,
                summary=summary,
                raw_response=response,
            )

        except json.JSONDecodeError as e:
            _logger.warning("review_json_parse_failed", error=str(e))
            return AIReviewResult(
                score=0,
                error=f"JSON parse error: {e}",
                raw_response=response,
                summary="Review response was malformed",
            )
        except (ValueError, TypeError, KeyError) as e:
            _logger.warning("review_response_parse_error", error=str(e))
            return AIReviewResult(
                score=0,
                error=str(e),
                raw_response=response,
                summary="Review parsing failed",
            )

    def evaluate_result(
        self, result: AIReviewResult
    ) -> tuple[bool, str]:
        """Evaluate review result against config thresholds.

        Args:
            result: Review result to evaluate.

        Returns:
            Tuple of (passed, message).
            passed is True if score >= min_score.
        """
        if result.score >= self.config.target_score:
            return True, f"High quality ({result.score}/100): {result.summary}"
        elif result.score >= self.config.min_score:
            return True, f"Acceptable ({result.score}/100): {result.summary}"
        else:
            return False, f"Below threshold ({result.score}/100): {result.summary}"
