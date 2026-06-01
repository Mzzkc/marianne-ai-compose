"""#351: classify immediate model-not-found failures distinctly.

An invalid/unavailable model id (typo, renamed/removed model) makes a CLI exit
non-zero almost immediately. Previously the baton saw a generic non-zero exit,
classified it as a plain execution failure, and the cause was invisible — it
looked identical to a real crash, burning a fallback step while nobody could see
"bad model id" without running the CLI by hand.

The lab (4 models, unanimous) rejected runtime probing and blocking validation
in favor of **diagnostic classification**: detect the model-not-found stderr
signature, surface it as a distinct ``MODEL_NOT_FOUND`` code, and treat it as a
fatal config error (``retriable=False``) so the baton fails fast to the next
instrument instead of retrying a doomed model.

These tests pin the classification and — critically — that the model-anchored
patterns do NOT shadow more-specific categories (auth, rate-limit, ENOENT).
"""

from __future__ import annotations

import pytest

from marianne.core.errors import ErrorClassifier
from marianne.core.errors.codes import ErrorCategory, ErrorCode


@pytest.fixture
def classifier() -> ErrorClassifier:
    return ErrorClassifier()


class TestModelNotFoundClassification:
    @pytest.mark.parametrize(
        "stderr",
        [
            "Error: model 'claude-opus-4-5-20250514' not found",
            "invalid model: gpt-9",
            "unknown model 'foo-bar'",
            "no such model",
            "the model \"x\" does not exist",
            "not a valid model id",
            "model 'y' is not available",
            "model not supported: z",
        ],
    )
    def test_model_not_found_classified(self, classifier: ErrorClassifier, stderr: str) -> None:
        result = classifier.classify(stderr=stderr, exit_code=1)
        assert result.error_code == ErrorCode.MODEL_NOT_FOUND
        assert result.category == ErrorCategory.CONFIGURATION
        assert result.retriable is False  # fail fast to fallback, don't retry a doomed model

    # NOTE: the agent-stdout-must-not-classify invariant (#195) is enforced at
    # the musician call site (it passes stdout="" to the classifier), not in the
    # classifier itself — which combines stdout+stderr by design, as every other
    # pattern category (auth, rate-limit) does. That invariant is covered by the
    # #195 musician tests; it is out of scope for this classifier-level test.


class TestNoShadowing:
    """Model-anchored patterns must not steal more-specific categories."""

    def test_rate_limit_still_wins(self, classifier: ErrorClassifier) -> None:
        result = classifier.classify(stderr="429 rate limit exceeded", exit_code=1)
        assert result.category == ErrorCategory.RATE_LIMIT

    def test_auth_still_wins(self, classifier: ErrorClassifier) -> None:
        result = classifier.classify(
            stderr="401 authentication failed: invalid api key", exit_code=1
        )
        assert result.category == ErrorCategory.AUTH

    def test_plain_not_found_is_not_model_error(self, classifier: ErrorClassifier) -> None:
        # A generic "not found" without "model" must not become MODEL_NOT_FOUND.
        result = classifier.classify(stderr="config.yaml: no such file or directory", exit_code=1)
        assert result.error_code != ErrorCode.MODEL_NOT_FOUND
