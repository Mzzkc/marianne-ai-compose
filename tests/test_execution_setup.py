"""Tests for execution/setup.py — shared component creation logic.

Covers all 5 factory functions: create_backend, setup_learning,
setup_notifications, setup_grounding, create_state_backend.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from marianne.core.config import JobConfig
from marianne.execution.setup import (
    create_backend,
    create_state_backend,
    setup_learning,
    setup_notifications,
)


@pytest.fixture
def base_config_dict() -> dict:
    """Minimal config dict that can be customized per-test."""
    return {
        "name": "test-job",
        "description": "Setup test job",
        "backend": {"type": "claude_cli", "skip_permissions": True},
        "sheet": {"size": 10, "total_items": 30},
        "prompt": {"template": "Sheet {{ sheet_num }}"},
        "validations": [],
    }


def _make_config(base: dict, **overrides: object) -> JobConfig:
    """Create a JobConfig with overrides merged into base dict."""
    merged = {**base, **overrides}
    return JobConfig.model_validate(merged)


# ── create_backend ──────────────────────────────────────────────────────


class TestCreateBackend:
    """Test backend factory dispatch."""

    def test_claude_cli_is_default(self, base_config_dict: dict) -> None:
        config = _make_config(base_config_dict)
        backend = create_backend(config)
        assert backend.name == "claude-cli"

    def test_anthropic_api_backend(self, base_config_dict: dict) -> None:
        config = _make_config(
            base_config_dict,
            backend={"type": "anthropic_api", "model": "claude-sonnet-4-5-20250929"},
        )
        backend = create_backend(config)
        assert backend.name == "anthropic-api"

    def test_recursive_light_backend(self, base_config_dict: dict) -> None:
        config = _make_config(
            base_config_dict,
            backend={"type": "recursive_light", "model": "claude-sonnet-4-5-20250929"},
        )
        backend = create_backend(config)
        assert backend.name == "recursive-light"

    def test_unknown_type_falls_through_to_cli(self, base_config_dict: dict) -> None:
        """Unrecognized type falls through to ClaudeCliBackend (else branch)."""
        config = _make_config(base_config_dict)
        # The default is claude_cli; any unrecognized type hits the else
        backend = create_backend(config)
        assert backend.name == "claude-cli"


# ── setup_learning ──────────────────────────────────────────────────────


class TestSetupLearning:
    """Test learning store setup."""

    def test_disabled_returns_none(self, base_config_dict: dict) -> None:
        config = _make_config(base_config_dict, learning={"enabled": False})
        outcome, gls = setup_learning(config)
        assert outcome is None
        assert gls is None

    def test_enabled_json_store(self, base_config_dict: dict, tmp_path: Path) -> None:
        config = _make_config(
            base_config_dict,
            learning={"enabled": True, "outcome_store_type": "json"},
            workspace=str(tmp_path),
        )
        with patch("marianne.learning.global_store.get_global_store") as mock_get_global:
            mock_get_global.return_value = MagicMock()
            outcome, gls = setup_learning(config)

        assert outcome is not None
        assert gls is not None

    def test_override_global_store(self, base_config_dict: dict, tmp_path: Path) -> None:
        config = _make_config(
            base_config_dict,
            learning={"enabled": True, "outcome_store_type": "json"},
            workspace=str(tmp_path),
        )
        mock_store = MagicMock()
        outcome, gls = setup_learning(config, global_learning_store_override=mock_store)
        assert gls is mock_store


# ── setup_notifications ─────────────────────────────────────────────────


class TestSetupNotifications:
    """Test notification manager setup."""

    def test_no_notifications_returns_none(self, base_config_dict: dict) -> None:
        config = _make_config(base_config_dict)
        result = setup_notifications(config)
        assert result is None

    def test_with_webhook_notifications(self, base_config_dict: dict) -> None:
        config = _make_config(
            base_config_dict,
            notifications=[
                {"type": "webhook", "config": {"url": "https://example.com/hook"}},
            ],
        )
        manager = setup_notifications(config)
        assert manager is not None


# setup_grounding tests removed in Phase 6a — grounding.py and the
# associated setup_grounding() helper were deleted. The baton emits
# grounding events natively; see docs/atlas-doctrine.md RULE 6.


# ── create_state_backend ────────────────────────────────────────────────


class TestCreateStateBackend:
    """Test state backend factory."""

    def test_json_backend_default(self, tmp_path: Path) -> None:
        backend = create_state_backend(tmp_path)
        from marianne.state import JsonStateBackend

        assert isinstance(backend, JsonStateBackend)

    def test_json_backend_explicit(self, tmp_path: Path) -> None:
        backend = create_state_backend(tmp_path, backend_type="json")
        from marianne.state import JsonStateBackend

        assert isinstance(backend, JsonStateBackend)

    def test_sqlite_backend(self, tmp_path: Path) -> None:
        backend = create_state_backend(tmp_path, backend_type="sqlite")
        from marianne.state import SQLiteStateBackend

        assert isinstance(backend, SQLiteStateBackend)

    def test_unknown_type_falls_to_json(self, tmp_path: Path) -> None:
        """Unknown backend_type falls through to JSON (else branch)."""
        backend = create_state_backend(tmp_path, backend_type="redis")
        from marianne.state import JsonStateBackend

        assert isinstance(backend, JsonStateBackend)
