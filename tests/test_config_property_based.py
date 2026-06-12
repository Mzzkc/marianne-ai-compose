"""Property-based tests for core config models.

Uses hypothesis @given to verify invariants across random inputs:
- SpecFragment round-trip serialization
- SpecCorpusConfig hash determinism
- PreflightConfig threshold validation

Extracted from test_execution_property_based.py during runner removal —
the runner-specific property tests were deleted, but these config model
tests are still needed by the quality gate.
"""

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from marianne.core.config.spec import SpecCorpusConfig, SpecFragment

# Strategy for valid SpecFragment names (non-empty, non-whitespace)
_spec_name = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(categories=("L", "N")),
).filter(lambda s: s.strip())

# Strategy for valid SpecFragment content (non-empty)
_spec_content = st.text(
    min_size=1,
    max_size=200,
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
).filter(lambda s: s.strip())

_spec_tags = st.lists(
    st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L",))),
    min_size=0,
    max_size=5,
)


class TestSpecFragmentProperties:
    """Property-based tests for SpecFragment model."""

    @given(name=_spec_name, content=_spec_content, tags=_spec_tags)
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_specfragment_round_trip(self, name: str, content: str, tags: list[str]) -> None:
        """SpecFragment round-trips through model_dump/model_validate."""
        frag = SpecFragment(name=name, content=content, tags=tags)
        dumped = frag.model_dump()
        restored = SpecFragment.model_validate(dumped)
        assert restored.name == frag.name
        assert restored.content == frag.content
        assert restored.tags == frag.tags
        assert restored.kind == "text"
        assert restored.data is None


class TestSpecCorpusConfigProperties:
    """Property-based tests for SpecCorpusConfig model."""

    @given(
        fragments=st.lists(
            st.builds(
                SpecFragment,
                name=_spec_name,
                content=_spec_content,
                tags=_spec_tags,
            ),
            min_size=0,
            max_size=5,
        ),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_speccorpusconfig_corpus_hash_deterministic(
        self,
        fragments: list[SpecFragment],
    ) -> None:
        """SpecCorpusConfig.corpus_hash is deterministic for same fragments."""
        config = SpecCorpusConfig(fragments=fragments)
        assert config.corpus_hash() == config.corpus_hash()


class TestPreflightConfigProperties:
    """Property-based tests for PreflightConfig invariants."""

    @given(
        data=st.fixed_dictionaries(
            {
                "token_warning_threshold": st.integers(min_value=0, max_value=500_000),
                "token_error_threshold": st.integers(min_value=0, max_value=1_000_000),
            }
        )
    )
    @settings(max_examples=50)
    def test_preflight_config_threshold_validation(self, data: dict[str, int]) -> None:
        """PreflightConfig rejects warning >= error when both are nonzero."""
        from marianne.core.config.execution import PreflightConfig

        warn = data["token_warning_threshold"]
        error = data["token_error_threshold"]

        if warn > 0 and error > 0 and warn >= error:
            with pytest.raises(ValueError, match="token_warning_threshold"):
                PreflightConfig(**data)
        else:
            config = PreflightConfig(**data)
            assert config.token_warning_threshold == warn
            assert config.token_error_threshold == error


class TestJudgmentConfigProperties:
    """Property-based tests for JudgmentConfig (#203) invariants."""

    @given(
        data=st.fixed_dictionaries(
            {
                "enabled": st.booleans(),
                "min_confidence": st.floats(min_value=0.0, max_value=1.0),
                "max_judgments_per_sheet": st.integers(min_value=1, max_value=10),
                "timeout_seconds": st.floats(
                    min_value=0.1, max_value=600.0, allow_nan=False
                ),
                "allowed_decisions": st.lists(
                    st.sampled_from(["retry", "skip", "accept", "fail"]),
                    min_size=0,
                    max_size=4,
                    unique=True,
                ),
            }
        )
    )
    @settings(max_examples=50)
    def test_judgment_config_roundtrip(self, data: dict) -> None:
        """JudgmentConfig roundtrips through serialization for valid inputs."""
        from marianne.core.config.judgment import JudgmentConfig

        config = JudgmentConfig.model_validate(data)
        restored = JudgmentConfig.model_validate(config.model_dump())
        assert restored.enabled == config.enabled
        assert restored.allowed_decisions == config.allowed_decisions
        assert restored.min_confidence == pytest.approx(config.min_confidence)
        assert restored.max_judgments_per_sheet == config.max_judgments_per_sheet

    @given(confidence=st.floats(min_value=1.0001, max_value=100.0, allow_nan=False))
    @settings(max_examples=25)
    def test_judgment_config_rejects_out_of_range_confidence(
        self, confidence: float
    ) -> None:
        """min_confidence outside [0, 1] is rejected at config load."""
        from marianne.core.config.judgment import JudgmentConfig

        with pytest.raises(ValueError):
            JudgmentConfig(min_confidence=confidence)


class TestCodeExecutionConfigProperties:
    """Property-based invariants for CodeExecutionConfig (#209)."""

    @given(
        enabled=st.booleans(),
        require_sandbox=st.booleans(),
        timeout=st.floats(min_value=0.1, max_value=86400.0),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_round_trip_and_bounds(
        self, enabled: bool, require_sandbox: bool, timeout: float
    ) -> None:
        from marianne.core.config.execution import CodeExecutionConfig

        cfg = CodeExecutionConfig(
            enabled=enabled,
            require_sandbox=require_sandbox,
            timeout_seconds=timeout,
        )
        assert cfg.enabled is enabled
        assert cfg.require_sandbox is require_sandbox
        assert cfg.timeout_seconds == timeout
        # Serialization round-trips.
        restored = CodeExecutionConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    @given(timeout=st.floats(max_value=0.0))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_nonpositive_timeout_rejected(self, timeout: float) -> None:
        import pytest as _pytest
        from pydantic import ValidationError

        from marianne.core.config.execution import CodeExecutionConfig

        with _pytest.raises(ValidationError):
            CodeExecutionConfig(timeout_seconds=timeout)
