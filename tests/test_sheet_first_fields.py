"""Tests for sheet-first architecture fields on JobConfig.

TDD: These tests define the contract for:
1. The `instrument:` field on JobConfig (string, optional)
2. The `instrument_config:` field on JobConfig (dict, optional)
3. Coexistence rules between `instrument:` and `backend:`
4. Rejection of the removed backend configuration surface

The current contract has one path: ``instrument:`` resolves a profile at
runtime, while the removed ``backend:`` field fails loudly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marianne.core.config.job import JobConfig

# --- Helpers ---


def _minimal_job_config(**overrides) -> dict:
    """Return a minimal valid JobConfig dict with optional overrides."""
    base = {
        "name": "test-job",
        "workspace": "/tmp/test-ws",
        "sheet": {"size": 1, "total_items": 3},
        "prompt": {"template": "Do something."},
    }
    base.update(overrides)
    return base


# =============================================================================
# Instrument field on JobConfig
# =============================================================================


class TestJobConfigInstrumentField:
    """Tests for the instrument: and instrument_config: fields."""

    def test_instrument_field_accepted(self) -> None:
        """instrument: field is accepted on JobConfig."""
        config = JobConfig.model_validate(_minimal_job_config(instrument="gemini-cli"))
        assert config.instrument == "gemini-cli"

    def test_instrument_field_defaults_to_none(self) -> None:
        """instrument: field defaults to None when not specified."""
        config = JobConfig.model_validate(_minimal_job_config())
        assert config.instrument is None

    def test_instrument_config_accepted(self) -> None:
        """instrument_config: field is accepted."""
        config = JobConfig.model_validate(
            _minimal_job_config(
                instrument="gemini-cli",
                instrument_config={"model": "gemini-2.5-flash", "timeout_seconds": 600},
            )
        )
        assert config.instrument_config == {
            "model": "gemini-2.5-flash",
            "timeout_seconds": 600,
        }

    def test_instrument_config_defaults_to_empty(self) -> None:
        """instrument_config: defaults to empty dict."""
        config = JobConfig.model_validate(_minimal_job_config())
        assert config.instrument_config == {}


# =============================================================================
# Post-strip: backend: rejected, instrument: is the only path (#347)
# =============================================================================


class TestBackendStripped:
    """The legacy backend: field is gone — these pin the replacement rules."""

    def test_backend_field_rejected(self) -> None:
        import pytest as _pytest
        from pydantic import ValidationError

        with _pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**_minimal_job_config(backend={"type": "claude_cli"}))

    def test_no_instrument_uses_default(self) -> None:
        config = JobConfig(**_minimal_job_config())
        assert config.effective_instrument_name == "claude-code"

    def test_instrument_only_path_works(self) -> None:
        config = JobConfig(**_minimal_job_config(instrument="gemini-cli"))
        assert config.effective_instrument_name == "gemini-cli"


# =============================================================================
# Serialization
# =============================================================================


class TestInstrumentFieldSerialization:
    """Tests for round-trip serialization of instrument fields."""

    def test_instrument_survives_yaml_roundtrip(self) -> None:
        """instrument: field survives to_yaml/from_yaml_string roundtrip."""
        config = JobConfig.model_validate(_minimal_job_config(instrument="gemini-cli"))
        yaml_str = config.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.instrument == "gemini-cli"

    def test_instrument_config_survives_roundtrip(self) -> None:
        """instrument_config: survives roundtrip."""
        config = JobConfig.model_validate(
            _minimal_job_config(
                instrument="codex-cli",
                instrument_config={"model": "gpt-4.1"},
            )
        )
        yaml_str = config.to_yaml()
        restored = JobConfig.from_yaml_string(yaml_str)
        assert restored.instrument_config == {"model": "gpt-4.1"}

    def test_instrument_in_model_dump(self) -> None:
        """instrument field appears in model_dump output."""
        config = JobConfig.model_validate(_minimal_job_config(instrument="test-instrument"))
        data = config.model_dump()
        assert data["instrument"] == "test-instrument"
        assert data["instrument_config"] == {}


# =============================================================================
# Adversarial
# =============================================================================


class TestInstrumentFieldAdversarial:
    """Adversarial tests for instrument fields."""

    @pytest.mark.adversarial
    def test_empty_instrument_name_rejected(self) -> None:
        """Empty string instrument name is rejected."""
        with pytest.raises(ValidationError, match="instrument"):
            JobConfig.model_validate(_minimal_job_config(instrument=""))

    @pytest.mark.adversarial
    def test_unicode_instrument_name(self) -> None:
        """Unicode instrument names are accepted."""
        config = JobConfig.model_validate(_minimal_job_config(instrument="模型-cli"))
        assert config.instrument == "模型-cli"

    @pytest.mark.adversarial
    def test_instrument_config_without_instrument_is_ignored(self) -> None:
        """instrument_config: without instrument: is accepted but meaningless.

        No validation error — the config is just unused. A future
        runtime check will warn about this.
        """
        config = JobConfig.model_validate(
            _minimal_job_config(
                instrument_config={"model": "gpt-4"},
            )
        )
        assert config.instrument is None
        assert config.instrument_config == {"model": "gpt-4"}
