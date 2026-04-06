"""Tests for F-431: DaemonConfig and ProfilerConfig missing extra='forbid'.

Same bug class as F-441 — unknown fields in conductor.yaml are silently
dropped. These models back user-edited config files and should reject
unknown fields.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marianne.daemon.config import (
    DaemonConfig,
    ObserverConfig,
    ResourceLimitConfig,
    SemanticLearningConfig,
    SocketConfig,
)
from marianne.daemon.profiler.models import (
    AnomalyConfig,
    CorrelationConfig,
    ProfilerConfig,
    RetentionConfig,
)


# All 9 models that should reject unknown fields
_DAEMON_MODELS = [
    ResourceLimitConfig,
    SocketConfig,
    ObserverConfig,
    SemanticLearningConfig,
    DaemonConfig,
]

_PROFILER_MODELS = [
    RetentionConfig,
    AnomalyConfig,
    CorrelationConfig,
    ProfilerConfig,
]


class TestDaemonConfigStrictness:
    """All daemon config models reject unknown fields."""

    @pytest.mark.parametrize(
        "model_cls",
        _DAEMON_MODELS,
        ids=[m.__name__ for m in _DAEMON_MODELS],
    )
    def test_rejects_unknown_field(self, model_cls: type) -> None:
        """Unknown fields raise ValidationError with extra='forbid'."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            model_cls(**{"this_field_doesnt_exist": True})

    @pytest.mark.parametrize(
        "model_cls",
        _DAEMON_MODELS,
        ids=[m.__name__ for m in _DAEMON_MODELS],
    )
    def test_valid_defaults_still_work(self, model_cls: type) -> None:
        """Default construction works fine with strict mode."""
        instance = model_cls()
        assert instance is not None


class TestProfilerConfigStrictness:
    """All profiler config models reject unknown fields."""

    @pytest.mark.parametrize(
        "model_cls",
        _PROFILER_MODELS,
        ids=[m.__name__ for m in _PROFILER_MODELS],
    )
    def test_rejects_unknown_field(self, model_cls: type) -> None:
        """Unknown fields raise ValidationError with extra='forbid'."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            model_cls(**{"this_field_doesnt_exist": True})

    @pytest.mark.parametrize(
        "model_cls",
        _PROFILER_MODELS,
        ids=[m.__name__ for m in _PROFILER_MODELS],
    )
    def test_valid_defaults_still_work(self, model_cls: type) -> None:
        """Default construction works fine with strict mode."""
        instance = model_cls()
        assert instance is not None


class TestDaemonConfigTypoDetection:
    """Realistic typos in conductor.yaml are caught."""

    def test_resource_limits_typo(self) -> None:
        """resource_limit (missing s) is caught."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            DaemonConfig(**{"resource_limit": {"max_memory_mb": 4096}})

    def test_profiler_typo(self) -> None:
        """profiler.enbled (typo) is caught."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ProfilerConfig(**{"enbled": True})

    def test_observer_typo(self) -> None:
        """observer.watch_interval (missing _seconds) is caught."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ObserverConfig(**{"watch_interval": 5.0})

    def test_nested_unknown_in_resource_limits(self) -> None:
        """Unknown field inside resource_limits is caught."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ResourceLimitConfig(**{"max_gpu_usage": 90})

    def test_existing_conductor_yaml_fields_accepted(self) -> None:
        """Fields from the actual production conductor.yaml are accepted."""
        config = DaemonConfig(
            job_timeout_seconds=86400,
            max_job_history=1000,
            log_file="/tmp/test.log",
            resource_limits=ResourceLimitConfig(max_processes=200),
            use_baton=False,
        )
        assert config.job_timeout_seconds == 86400
        assert config.resource_limits.max_processes == 200
