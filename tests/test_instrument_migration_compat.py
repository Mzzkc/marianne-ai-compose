"""Post-strip contract for the instrument-only execution config (#347).

The legacy ``backend:`` score syntax and its models (BackendConfig and
friends) were fully stripped — the instrument plugin system is the only
execution-config path. These tests pin the post-strip contract:

- ``backend:`` in score YAML fails loudly at parse time (never silently
  ignored).
- Scores with no ``instrument:`` resolve to the default instrument
  (claude-code).
- The instrument path\'s per-scope ``timeout_seconds`` is honored.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marianne.core.config.job import JobConfig
from marianne.core.constants import DEFAULT_INSTRUMENT_NAME
from marianne.core.sheet import build_sheets


def _minimal(**extra: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "strip-test",
        "sheet": {"size": 1, "total_items": 2},
        "prompt": {"template": "work {{ sheet_num }}"},
    }
    data.update(extra)
    return data


class TestBackendSyntaxRejected:
    """backend: must fail loudly — a silently ignored block would let old
    scores run with entirely different settings than their authors wrote."""

    def test_backend_block_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**_minimal(backend={"type": "claude_cli"}))

    def test_backend_block_rejected_with_instrument_present(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**_minimal(
                instrument="claude-code",
                backend={"skip_permissions": True},
            ))

    def test_empty_backend_block_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            JobConfig(**_minimal(backend={}))


class TestDefaultInstrumentResolution:
    def test_no_instrument_resolves_to_default(self) -> None:
        config = JobConfig(**_minimal())
        assert config.effective_instrument_name == DEFAULT_INSTRUMENT_NAME

    def test_explicit_instrument_wins(self) -> None:
        config = JobConfig(**_minimal(instrument="opencode"))
        assert config.effective_instrument_name == "opencode"

    def test_sheets_inherit_default_instrument(self) -> None:
        config = JobConfig(**_minimal())
        sheets = build_sheets(config)
        assert all(s.instrument_name == DEFAULT_INSTRUMENT_NAME for s in sheets)


class TestInstrumentConfigTimeout:
    """timeout_seconds now flows from the merged instrument_config —
    score, movement, alias, and per-sheet scopes all compose."""

    def test_default_timeout(self) -> None:
        config = JobConfig(**_minimal())
        sheets = build_sheets(config)
        assert sheets[0].timeout_seconds == 1800.0

    def test_score_level_timeout(self) -> None:
        config = JobConfig(**_minimal(
            instrument="claude-code",
            instrument_config={"timeout_seconds": 600},
        ))
        sheets = build_sheets(config)
        assert all(s.timeout_seconds == 600.0 for s in sheets)

    def test_per_sheet_timeout_overrides_score(self) -> None:
        data = _minimal(
            instrument="claude-code",
            instrument_config={"timeout_seconds": 600},
        )
        sheet_block = data["sheet"]
        assert isinstance(sheet_block, dict)
        sheet_block["per_sheet_instrument_config"] = {2: {"timeout_seconds": 3600}}
        config = JobConfig(**data)
        sheets = build_sheets(config)
        assert sheets[0].timeout_seconds == 600.0
        assert sheets[1].timeout_seconds == 3600.0

    def test_malformed_timeout_falls_back_to_default(self) -> None:
        config = JobConfig(**_minimal(
            instrument="claude-code",
            instrument_config={"timeout_seconds": "not-a-number"},
        ))
        sheets = build_sheets(config)
        assert sheets[0].timeout_seconds == 1800.0


class TestCliModelAliasNormalization:
    """Legacy \'cli_model\' spelling normalizes onto \'model\' at sheet build."""

    def test_cli_model_aliased(self) -> None:
        config = JobConfig(**_minimal(
            instrument="claude-code",
            instrument_config={"cli_model": "claude-opus-4-8"},
        ))
        sheets = build_sheets(config)
        assert sheets[0].instrument_config["model"] == "claude-opus-4-8"

    def test_explicit_model_wins_over_cli_model(self) -> None:
        config = JobConfig(**_minimal(
            instrument="claude-code",
            instrument_config={"model": "a", "cli_model": "b"},
        ))
        sheets = build_sheets(config)
        assert sheets[0].instrument_config["model"] == "a"
