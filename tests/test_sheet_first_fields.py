"""Tests for sheet-first architecture fields on JobConfig.

TDD: These tests define the contract for:
1. The `instrument:` field on JobConfig (string, optional)
2. The `instrument_config:` field on JobConfig (dict, optional)
3. Coexistence rules between `instrument:` and `backend:`
4. YAML alias validators for backward compatibility

Per the design spec (2026-03-26-instrument-plugin-system-design.md):
- instrument: and backend: are two ways to specify the same thing
- If both present → validation error
- If only backend: → works as today
- If only instrument: → resolves via profile registry at runtime
- If neither → defaults to claude_cli (current default)

The `backend:` field is NOT deprecated. `instrument:` is additive.
"""

from __future__ import annotations

from pathlib import Path

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
        config = JobConfig.model_validate(
            _minimal_job_config(instrument="gemini-cli")
        )
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
# Coexistence: instrument: and backend:
# =============================================================================


class TestInstrumentBackendCoexistence:
    """Tests for the mutual exclusion between instrument: and backend:."""

    def test_backend_only_works_unchanged(self) -> None:
        """backend: without instrument: works exactly as before."""
        config = JobConfig.model_validate(
            _minimal_job_config(
                backend={"type": "claude_cli", "allowed_tools": ["Read"]},
            )
        )
        assert config.backend.type == "claude_cli"
        assert config.instrument is None

    def test_instrument_only_works(self) -> None:
        """instrument: without backend: is accepted."""
        config = JobConfig.model_validate(
            _minimal_job_config(instrument="codex-cli")
        )
        assert config.instrument == "codex-cli"

    def test_both_instrument_and_backend_type_raises(self) -> None:
        """instrument: + backend.type (non-default) raises validation error."""
        with pytest.raises(ValidationError, match="instrument.*backend|backend.*instrument"):
            JobConfig.model_validate(
                _minimal_job_config(
                    instrument="gemini-cli",
                    backend={"type": "anthropic_api"},
                )
            )

    def test_instrument_with_default_backend_is_ok(self) -> None:
        """instrument: with default backend (no explicit type) is fine.

        The backend field always exists with defaults. The conflict is only
        when the user explicitly sets backend.type to a non-default value.
        """
        config = JobConfig.model_validate(
            _minimal_job_config(instrument="gemini-cli")
        )
        # backend exists with default values — that's fine
        assert config.backend is not None
        assert config.instrument == "gemini-cli"

    def test_neither_instrument_nor_backend_uses_default(self) -> None:
        """No instrument: and no backend: → default backend (claude_cli)."""
        config = JobConfig.model_validate(_minimal_job_config())
        assert config.instrument is None
        assert config.backend.type == "claude_cli"


# =============================================================================
# Serialization
# =============================================================================


class TestInstrumentFieldSerialization:
    """Tests for round-trip serialization of instrument fields."""

    def test_instrument_survives_yaml_roundtrip(self) -> None:
        """instrument: field survives to_yaml/from_yaml_string roundtrip."""
        config = JobConfig.model_validate(
            _minimal_job_config(instrument="gemini-cli")
        )
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
        config = JobConfig.model_validate(
            _minimal_job_config(instrument="test-instrument")
        )
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
            JobConfig.model_validate(
                _minimal_job_config(instrument="")
            )

    @pytest.mark.adversarial
    def test_unicode_instrument_name(self) -> None:
        """Unicode instrument names are accepted."""
        config = JobConfig.model_validate(
            _minimal_job_config(instrument="模型-cli")
        )
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
