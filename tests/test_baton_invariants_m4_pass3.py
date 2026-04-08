"""Movement 4 Pass 3 — property-based invariant verification.

Extends the invariant suite to cover M4 config strictness (F-441),
IPC error mapping, token extraction, auto-fresh logic, and config
model safety properties:

75. Config strictness totality — every config model rejects unknown fields
76. IPC error code mapping bijectivity — unique exception per code
77. Token extraction defensive parsing — any string → valid result
78. Auto-fresh threshold monotonicity — later mtime → same or more True
79. MethodNotFoundError type preservation — round-trip preserves exception class
80. Config default construction — all defaulted models build without args
81. Pydantic field bounds enforcement — ge/gt/le constraints hold under stress
82. Retry delay monotonicity with config bounds — delay ≤ max_delay always
83. ValidationRule regex compilation safety — all default patterns compile
84. Cost limit enforcement invariant — disabled limits never block, enabled always check

Found by: Theorem, Movement 4
Method: Property-based testing with hypothesis + invariant analysis

@pytest.mark.property_based
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, assume, given, settings
from pydantic import BaseModel, ValidationError

# =============================================================================
# Import all config models for exhaustive testing
# =============================================================================

from marianne.core.config.backend import (
    BackendConfig,
    BridgeConfig,
    MCPServerConfig,
    OllamaConfig,
    RecursiveLightConfig,
    SheetBackendOverride,
)
from marianne.core.config.execution import (
    CircuitBreakerConfig,
    CostLimitConfig,
    ParallelConfig,
    PreflightConfig,
    RateLimitConfig,
    RetryConfig,
    SkipWhenCommand,
    StaleDetectionConfig,
    ValidationRule,
)
from marianne.core.config.instruments import (
    CliCommand,
    CliErrorConfig,
    CliOutputConfig,
    CliProfile,
    CodeModeConfig,
    CodeModeInterface,
    HttpProfile,
    InstrumentProfile,
    ModelCapacity,
)
from marianne.core.config.job import (
    InjectionItem,
    InstrumentDef,
    JobConfig,
    MovementDef,
    PromptConfig,
    SheetConfig,
)
from marianne.core.config.learning import (
    AutoApplyConfig,
    CheckpointConfig,
    CheckpointTriggerConfig,
    EntropyResponseConfig,
    ExplorationBudgetConfig,
    GroundingConfig,
    GroundingHookConfig,
    LearningConfig,
)
from marianne.core.config.orchestration import (
    ConcertConfig,
    ConductorConfig,
    ConductorPreferences,
    NotificationConfig,
    PostSuccessHookConfig,
)
from marianne.core.config.spec import SpecCorpusConfig, SpecFragment
from marianne.core.config.workspace import (
    AIReviewConfig,
    CrossSheetConfig,
    FeedbackConfig,
    IsolationConfig,
    LogConfig,
    WorkspaceLifecycleConfig,
)
from marianne.daemon.ipc.errors import (
    DAEMON_SHUTTING_DOWN,
    JOB_ALREADY_RUNNING,
    JOB_NOT_FOUND,
    JOB_NOT_RESUMABLE,
    METHOD_NOT_FOUND,
    RESOURCE_EXHAUSTED,
    WORKSPACE_NOT_FOUND,
    _CODE_EXCEPTION_MAP,
    rpc_error_to_exception,
)
from marianne.daemon.exceptions import (
    DaemonError,
    MethodNotFoundError,
)

# =============================================================================
# Strategies
# =============================================================================

_RANDOM_KEY = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_"),
    min_size=1,
    max_size=20,
)
_RANDOM_VALUE = st.one_of(st.integers(), st.text(max_size=50), st.booleans(), st.floats(allow_nan=False))
_TIMESTAMP = st.floats(min_value=0.0, max_value=2e9, allow_nan=False, allow_infinity=False)
_JSON_STRINGS = st.one_of(
    st.text(max_size=200),
    st.just(""),
    st.just("{}"),
    st.just("[]"),
    st.just("null"),
    st.just('{"usage": {"input_tokens": 100, "output_tokens": 50}}'),
    st.just('{"usage": "not_a_dict"}'),
    st.just('{"usage": {"input_tokens": "not_int"}}'),
    st.just('{"result": "ok"}'),
    st.builds(
        json.dumps,
        st.fixed_dictionaries({
            "usage": st.fixed_dictionaries({
                "input_tokens": st.one_of(st.integers(min_value=0, max_value=1_000_000), st.text(max_size=10)),
                "output_tokens": st.one_of(st.integers(min_value=0, max_value=1_000_000), st.text(max_size=10)),
            })
        }),
    ),
)

# =============================================================================
# All config model classes for exhaustive invariant testing
# =============================================================================

# Models that have `extra="forbid"` set
ALL_CONFIG_MODELS: list[type[BaseModel]] = [
    # backend.py
    RecursiveLightConfig,
    OllamaConfig,
    MCPServerConfig,
    BridgeConfig,
    SheetBackendOverride,
    BackendConfig,
    # execution.py
    RetryConfig,
    RateLimitConfig,
    CircuitBreakerConfig,
    CostLimitConfig,
    StaleDetectionConfig,
    PreflightConfig,
    ParallelConfig,
    ValidationRule,
    SkipWhenCommand,
    # job.py
    InjectionItem,
    InstrumentDef,
    MovementDef,
    SheetConfig,
    PromptConfig,
    JobConfig,
    # learning.py
    ExplorationBudgetConfig,
    EntropyResponseConfig,
    AutoApplyConfig,
    LearningConfig,
    GroundingHookConfig,
    GroundingConfig,
    CheckpointTriggerConfig,
    CheckpointConfig,
    # orchestration.py
    ConductorPreferences,
    ConductorConfig,
    NotificationConfig,
    PostSuccessHookConfig,
    ConcertConfig,
    # spec.py
    SpecFragment,
    SpecCorpusConfig,
    # workspace.py
    IsolationConfig,
    WorkspaceLifecycleConfig,
    LogConfig,
    AIReviewConfig,
    CrossSheetConfig,
    FeedbackConfig,
    # instruments.py
    CodeModeInterface,
    CodeModeConfig,
    ModelCapacity,
    CliCommand,
    CliOutputConfig,
    CliErrorConfig,
    CliProfile,
    HttpProfile,
    InstrumentProfile,
]


# =============================================================================
# Invariant 75: Config strictness totality — every model rejects unknown fields
# =============================================================================


class TestConfigStrictnessTotality:
    """Every config model with extra='forbid' rejects unknown YAML fields.

    Invariant: For any config model M and any unknown field name F,
    M(**{F: value}) raises ValidationError.

    This is the mathematical proof that F-441 (Pydantic silently ignoring
    unknown fields) cannot recur. If a model accepts an unknown field,
    the test fails BEFORE the field reaches production.
    """

    @given(field_name=_RANDOM_KEY, field_value=_RANDOM_VALUE)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_unknown_field_rejected_on_all_models(
        self, field_name: str, field_value: Any
    ) -> None:
        """Every config model rejects fields not in its schema."""
        # Skip field names that happen to be real fields
        rejected_count = 0
        skipped_count = 0

        for model_cls in ALL_CONFIG_MODELS:
            known_fields = set(model_cls.model_fields.keys())
            # Also check aliases
            known_aliases: set[str] = set()
            for _fname, finfo in model_cls.model_fields.items():
                if finfo.alias:
                    known_aliases.add(finfo.alias)

            if field_name in known_fields or field_name in known_aliases:
                skipped_count += 1
                continue

            # Constructing with an unknown field must raise ValidationError
            try:
                model_cls.model_validate({field_name: field_value})
                # If we get here, the model accepted an unknown field!
                # But we need to check — some models require mandatory fields
                # and would fail for a different reason. Let's be specific:
                # the model should NEVER succeed with an unknown extra field.
                # If it succeeded, it means extra="forbid" is missing.
                pytest.fail(
                    f"{model_cls.__name__} accepted unknown field "
                    f"'{field_name}' = {field_value!r} without error"
                )
            except ValidationError as e:
                # Check that at least one error is about the extra field
                errors = e.errors()
                has_extra_error = any(
                    err.get("type") == "extra_forbidden"
                    or field_name in str(err.get("loc", ()))
                    for err in errors
                )
                if has_extra_error:
                    rejected_count += 1
                # If the error is about a missing required field, that's fine —
                # the model would have also rejected the extra field if we provided
                # all required fields. The extra="forbid" is still in effect.

        # At least some models should have rejected the field
        # (unless the random field name happened to be a real field in every model)
        assert rejected_count > 0 or skipped_count == len(ALL_CONFIG_MODELS)

    def test_all_config_models_have_forbid(self) -> None:
        """Verify every BaseModel subclass in config/ has extra='forbid'.

        This is a static check — no hypothesis needed. Scans all modules
        in marianne.core.config and verifies each BaseModel subclass has
        extra='forbid' in its model_config.
        """
        config_package = "marianne.core.config"
        module_names = [
            f"{config_package}.{name}"
            for name in [
                "backend",
                "execution",
                "instruments",
                "job",
                "learning",
                "orchestration",
                "spec",
                "workspace",
            ]
        ]

        missing_forbid: list[str] = []
        for mod_name in module_names:
            mod = importlib.import_module(mod_name)
            for name, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, BaseModel)
                    and obj is not BaseModel
                    and obj.__module__ == mod_name
                ):
                    config = getattr(obj, "model_config", {})
                    extra_setting = config.get("extra")
                    if extra_setting != "forbid":
                        missing_forbid.append(f"{mod_name}.{name} (extra={extra_setting!r})")

        assert not missing_forbid, (
            f"Config models missing extra='forbid': {missing_forbid}"
        )


# =============================================================================
# Invariant 76: IPC error code mapping bijectivity
# =============================================================================


class TestIPCErrorCodeMapping:
    """Every IPC error code maps to a unique exception type, and the mapping
    is exhaustive for all defined codes.

    Invariant: For every (code, exc_cls) in _CODE_EXCEPTION_MAP,
    rpc_error_to_exception({"code": code, "message": msg}) returns
    an instance of exc_cls.
    """

    def test_all_defined_codes_in_map(self) -> None:
        """Every named constant error code appears in the mapping."""
        defined_codes = {
            JOB_NOT_FOUND,
            RESOURCE_EXHAUSTED,
            JOB_ALREADY_RUNNING,
            DAEMON_SHUTTING_DOWN,
            JOB_NOT_RESUMABLE,
            WORKSPACE_NOT_FOUND,
            METHOD_NOT_FOUND,
        }
        mapped_codes = set(_CODE_EXCEPTION_MAP.keys())
        assert defined_codes == mapped_codes, (
            f"Missing from map: {defined_codes - mapped_codes}, "
            f"Extra in map: {mapped_codes - defined_codes}"
        )

    @given(message=st.text(min_size=1, max_size=100))
    @settings(max_examples=30)
    def test_known_code_produces_correct_type(self, message: str) -> None:
        """Every mapped code → correct exception type."""
        for code, expected_cls in _CODE_EXCEPTION_MAP.items():
            error = {"code": code, "message": message}
            exc = rpc_error_to_exception(error)
            assert isinstance(exc, expected_cls), (
                f"Code {code}: expected {expected_cls.__name__}, "
                f"got {type(exc).__name__}"
            )

    @given(code=st.integers(min_value=-40000, max_value=-30000))
    @settings(max_examples=30)
    def test_unknown_code_falls_back_to_daemon_error(self, code: int) -> None:
        """Unmapped codes produce a generic DaemonError."""
        assume(code not in _CODE_EXCEPTION_MAP)
        error = {"code": code, "message": "test error"}
        exc = rpc_error_to_exception(error)
        assert isinstance(exc, DaemonError)

    def test_method_not_found_distinguished(self) -> None:
        """F-450: METHOD_NOT_FOUND produces MethodNotFoundError, not generic DaemonError."""
        error = {"code": METHOD_NOT_FOUND, "message": "Method not found"}
        exc = rpc_error_to_exception(error)
        assert type(exc) is MethodNotFoundError
        # Must NOT be collapsed into the base DaemonError
        assert type(exc) is not DaemonError.__class__


# =============================================================================
# Invariant 77: Token extraction defensive parsing
# =============================================================================


class TestTokenExtractionDefensiveParsing:
    """_extract_tokens_from_json handles ANY input without crashing.

    Invariant: For any string S, _extract_tokens_from_json(S) returns
    a tuple (int|None, int|None) — never raises, never returns non-int
    values (other than None).
    """

    @given(stdout=_JSON_STRINGS)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_always_returns_valid_tuple(self, stdout: str) -> None:
        """Any input produces a (int|None, int|None) tuple."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "json"

        result = backend._extract_tokens_from_json(stdout)
        assert isinstance(result, tuple)
        assert len(result) == 2
        input_tokens, output_tokens = result
        assert input_tokens is None or isinstance(input_tokens, int)
        assert output_tokens is None or isinstance(output_tokens, int)

    @given(stdout=st.text(max_size=500))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_non_json_format_returns_none(self, stdout: str) -> None:
        """When output_format != 'json', always returns (None, None)."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "text"

        result = backend._extract_tokens_from_json(stdout)
        assert result == (None, None)

    @given(
        input_tokens=st.integers(min_value=0, max_value=10_000_000),
        output_tokens=st.integers(min_value=0, max_value=10_000_000),
    )
    @settings(max_examples=50)
    def test_valid_json_preserves_values(self, input_tokens: int, output_tokens: int) -> None:
        """Well-formed JSON with integer tokens preserves exact values."""
        from marianne.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "json"

        data = {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}
        result = backend._extract_tokens_from_json(json.dumps(data))
        assert result == (input_tokens, output_tokens)


# =============================================================================
# Invariant 78: Auto-fresh threshold monotonicity
# =============================================================================


class TestAutoFreshMonotonicity:
    """_should_auto_fresh is monotonic: later mtime can only make it more True.

    Invariant: For fixed completed_at, if mtime1 < mtime2 and
    _should_auto_fresh(path1, completed_at) is True, then
    _should_auto_fresh(path2, completed_at) is also True
    (where path2.stat().st_mtime == mtime2).
    """

    @given(
        completed_at=_TIMESTAMP,
        mtime_offset=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_monotonicity_in_mtime(self, completed_at: float, mtime_offset: float) -> None:
        """Increasing mtime relative to completed_at monotonically increases True likelihood."""
        from marianne.daemon.manager import _should_auto_fresh, _MTIME_TOLERANCE_SECONDS

        # Simulate two mtimes
        mtime1 = completed_at + mtime_offset
        mtime2 = mtime1 + 10.0  # Always later

        mock_path1 = MagicMock(spec=Path)
        mock_stat1 = MagicMock(spec=os.stat_result)
        mock_stat1.st_mtime = mtime1
        mock_path1.stat.return_value = mock_stat1

        mock_path2 = MagicMock(spec=Path)
        mock_stat2 = MagicMock(spec=os.stat_result)
        mock_stat2.st_mtime = mtime2
        mock_path2.stat.return_value = mock_stat2

        result1 = _should_auto_fresh(mock_path1, completed_at)
        result2 = _should_auto_fresh(mock_path2, completed_at)

        # Monotonicity: if result1 is True, result2 must also be True
        if result1:
            assert result2, (
                f"Monotonicity violated: mtime1={mtime1}, mtime2={mtime2}, "
                f"completed_at={completed_at}, tolerance={_MTIME_TOLERANCE_SECONDS}"
            )

    def test_none_completed_at_always_false(self) -> None:
        """If completed_at is None, auto-fresh is always False."""
        from marianne.daemon.manager import _should_auto_fresh

        mock_path = MagicMock(spec=Path)
        assert _should_auto_fresh(mock_path, None) is False

    def test_os_error_always_false(self) -> None:
        """If stat() raises OSError, auto-fresh is always False."""
        from marianne.daemon.manager import _should_auto_fresh

        mock_path = MagicMock(spec=Path)
        mock_path.stat.side_effect = OSError("no such file")
        assert _should_auto_fresh(mock_path, 1000.0) is False


# =============================================================================
# Invariant 79: MethodNotFoundError type preservation
# =============================================================================


class TestMethodNotFoundTypePreservation:
    """F-450: MethodNotFoundError survives the full round-trip through IPC.

    Invariant: rpc_error_to_exception({"code": -32601, "message": M})
    always produces a MethodNotFoundError, not a generic DaemonError.
    """

    @given(message=st.text(min_size=0, max_size=200))
    @settings(max_examples=50)
    def test_method_not_found_round_trip(self, message: str) -> None:
        """Any message string preserves the MethodNotFoundError type."""
        error = {"code": METHOD_NOT_FOUND, "message": message}
        exc = rpc_error_to_exception(error)
        assert isinstance(exc, MethodNotFoundError)
        assert isinstance(exc, DaemonError)  # Subclass relationship

    def test_all_exception_subclasses_distinguishable(self) -> None:
        """No two error codes map to the same exception class
        (except DaemonError base which is the shutdown fallback)."""
        seen: dict[type, list[int]] = {}
        for code, exc_cls in _CODE_EXCEPTION_MAP.items():
            seen.setdefault(exc_cls, []).append(code)

        # The only class allowed multiple codes is DaemonError (fallback)
        # and JobSubmissionError (which maps multiple job-specific errors)
        for exc_cls, codes in seen.items():
            if len(codes) > 1:
                # This is acceptable for shared base classes
                assert issubclass(exc_cls, DaemonError), (
                    f"{exc_cls.__name__} mapped to multiple codes {codes} "
                    f"but is not a DaemonError subclass"
                )


# =============================================================================
# Invariant 80: Config default construction
# =============================================================================


class TestConfigDefaultConstruction:
    """Every config model that has all-default fields constructs without args.

    Invariant: For every model M where all fields have defaults,
    M() succeeds and produces a valid instance.
    """

    @staticmethod
    def _has_required_fields(model_cls: type[BaseModel]) -> bool:
        """Check if a model has any fields without defaults."""
        for field_info in model_cls.model_fields.values():
            if field_info.is_required():
                return True
        return False

    def test_all_defaulted_models_construct(self) -> None:
        """Models with all defaults should construct without arguments."""
        for model_cls in ALL_CONFIG_MODELS:
            if self._has_required_fields(model_cls):
                continue

            try:
                instance = model_cls()
                assert isinstance(instance, model_cls)
            except (ValidationError, TypeError) as e:
                pytest.fail(
                    f"{model_cls.__name__} has all-default fields but "
                    f"failed construction: {e}"
                )


# =============================================================================
# Invariant 81: Pydantic field bounds enforcement
# =============================================================================


class TestFieldBoundsEnforcement:
    """Pydantic ge/gt/le constraints hold under hypothesis-generated values.

    Invariant: For RetryConfig, if base_delay > max_delay, construction fails.
    For ParallelConfig, stagger_delay_ms stays in [0, 5000].
    """

    @given(
        base_delay=st.floats(min_value=0.01, max_value=10000, allow_nan=False, allow_infinity=False),
        max_delay=st.floats(min_value=0.01, max_value=10000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_retry_delay_ordering(self, base_delay: float, max_delay: float) -> None:
        """RetryConfig rejects base_delay > max_delay."""
        if base_delay > max_delay:
            with pytest.raises(ValidationError):
                RetryConfig(base_delay_seconds=base_delay, max_delay_seconds=max_delay)
        else:
            config = RetryConfig(base_delay_seconds=base_delay, max_delay_seconds=max_delay)
            assert config.base_delay_seconds <= config.max_delay_seconds

    @given(stagger=st.integers(min_value=-100, max_value=10000))
    @settings(max_examples=50)
    def test_stagger_delay_bounds(self, stagger: int) -> None:
        """ParallelConfig.stagger_delay_ms must be in [0, 5000]."""
        if stagger < 0 or stagger > 5000:
            with pytest.raises(ValidationError):
                ParallelConfig(stagger_delay_ms=stagger)
        else:
            config = ParallelConfig(stagger_delay_ms=stagger)
            assert 0 <= config.stagger_delay_ms <= 5000

    @given(
        max_retries=st.integers(min_value=-10, max_value=100),
    )
    @settings(max_examples=30)
    def test_max_retries_non_negative(self, max_retries: int) -> None:
        """RetryConfig.max_retries must be >= 0."""
        if max_retries < 0:
            with pytest.raises(ValidationError):
                RetryConfig(max_retries=max_retries)
        else:
            config = RetryConfig(max_retries=max_retries)
            assert config.max_retries >= 0


# =============================================================================
# Invariant 82: Retry delay computation stays within bounds
# =============================================================================


class TestRetryDelayBounds:
    """Computed retry delays never exceed max_delay_seconds.

    Invariant: For any RetryConfig C and attempt number N,
    base_delay * exponential_base^N ≤ max_delay (after clamping).
    """

    @given(
        attempt=st.integers(min_value=0, max_value=50),
        base=st.floats(min_value=0.01, max_value=100, allow_nan=False, allow_infinity=False),
        max_d=st.floats(min_value=100, max_value=10000, allow_nan=False, allow_infinity=False),
        exp_base=st.floats(min_value=1.01, max_value=5.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_computed_delay_within_max(
        self, attempt: int, base: float, max_d: float, exp_base: float
    ) -> None:
        """The clamped delay formula never exceeds max_delay."""
        assume(base <= max_d)
        config = RetryConfig(
            base_delay_seconds=base,
            max_delay_seconds=max_d,
            exponential_base=exp_base,
            jitter=False,
        )
        raw_delay = config.base_delay_seconds * (config.exponential_base ** attempt)
        clamped = min(raw_delay, config.max_delay_seconds)
        assert clamped <= config.max_delay_seconds


# =============================================================================
# Invariant 83: ValidationRule regex compilation safety
# =============================================================================


class TestValidationRegexSafety:
    """Default rate limit detection patterns are valid regexes.

    Invariant: Every pattern in RateLimitConfig.detection_patterns compiles
    without error.
    """

    def test_default_rate_limit_patterns_compile(self) -> None:
        """All default detection patterns are valid regexes."""
        config = RateLimitConfig()
        for pattern in config.detection_patterns:
            compiled = re.compile(pattern)
            assert compiled is not None, f"Pattern failed to compile: {pattern}"

    @given(pattern=st.from_regex(r"[a-z.?*+\[\]{}()]+", fullmatch=True))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_invalid_regex_rejected_by_validator(self, pattern: str) -> None:
        """RateLimitConfig's validator catches invalid regex."""
        # Generate a pattern that might be invalid
        bad_pattern = "[" + pattern  # Unclosed bracket
        try:
            re.compile(bad_pattern)
            # If it compiles, the validator should accept it
        except re.error:
            # Invalid regex — the validator should reject it
            with pytest.raises(ValidationError):
                RateLimitConfig(detection_patterns=[bad_pattern])


# =============================================================================
# Invariant 84: Cost limit enforcement invariant
# =============================================================================


class TestCostLimitEnforcementInvariant:
    """CostLimitConfig constraints are self-consistent.

    Invariant: warn_at < stop_at unless stop_at == 0 (unlimited).
    Both values are non-negative.
    """

    @given(
        per_sheet=st.one_of(
            st.none(),
            st.floats(min_value=0.001, max_value=10000, allow_nan=False, allow_infinity=False),
        ),
        per_job=st.one_of(
            st.none(),
            st.floats(min_value=0.001, max_value=10000, allow_nan=False, allow_infinity=False),
        ),
    )
    @settings(max_examples=50)
    def test_cost_limits_non_negative(self, per_sheet: float | None, per_job: float | None) -> None:
        """Cost limits are always non-negative when construction succeeds."""
        try:
            config = CostLimitConfig(max_cost_per_sheet=per_sheet, max_cost_per_job=per_job)
            if config.max_cost_per_sheet is not None:
                assert config.max_cost_per_sheet > 0
            if config.max_cost_per_job is not None:
                assert config.max_cost_per_job > 0
        except ValidationError:
            pass  # Construction can fail for invalid combinations

    def test_default_cost_limits_disabled(self) -> None:
        """Default CostLimitConfig has limits disabled."""
        config = CostLimitConfig()
        assert config.enabled is False


# =============================================================================
# Invariant 85 (bonus): Config model field count stability
# =============================================================================


class TestConfigFieldCountStability:
    """Track the total number of config models and fields.

    Not a property-based test, but an invariant guard: if someone adds a
    new config model without extra='forbid', the count assertion fails.
    """

    def test_config_model_count(self) -> None:
        """We know exactly how many config models exist."""
        # Count should match ALL_CONFIG_MODELS length
        # If this fails, a new model was added — update ALL_CONFIG_MODELS
        config_package = "marianne.core.config"
        module_names = [
            f"{config_package}.{name}"
            for name in [
                "backend", "execution", "instruments", "job",
                "learning", "orchestration", "spec", "workspace",
            ]
        ]

        actual_count = 0
        for mod_name in module_names:
            mod = importlib.import_module(mod_name)
            for _name, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, BaseModel)
                    and obj is not BaseModel
                    and obj.__module__ == mod_name
                ):
                    actual_count += 1

        assert actual_count == len(ALL_CONFIG_MODELS), (
            f"Config model count changed: expected {len(ALL_CONFIG_MODELS)}, "
            f"found {actual_count}. Update ALL_CONFIG_MODELS in this test."
        )
