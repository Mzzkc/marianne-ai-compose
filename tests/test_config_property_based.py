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
    min_size=1, max_size=50,
    alphabet=st.characters(categories=("L", "N")),
).filter(lambda s: s.strip())

# Strategy for valid SpecFragment content (non-empty)
_spec_content = st.text(
    min_size=1, max_size=200,
    alphabet=st.characters(categories=("L", "N", "P", "Z")),
).filter(lambda s: s.strip())

_spec_tags = st.lists(
    st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L",))),
    min_size=0, max_size=5,
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
            min_size=0, max_size=5,
        ),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_speccorpusconfig_corpus_hash_deterministic(
        self, fragments: list[SpecFragment],
    ) -> None:
        """SpecCorpusConfig.corpus_hash is deterministic for same fragments."""
        config = SpecCorpusConfig(fragments=fragments)
        assert config.corpus_hash() == config.corpus_hash()


class TestPreflightConfigProperties:
    """Property-based tests for PreflightConfig invariants."""

    @given(data=st.fixed_dictionaries({
        "token_warning_threshold": st.integers(min_value=0, max_value=500_000),
        "token_error_threshold": st.integers(min_value=0, max_value=1_000_000),
    }))
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
