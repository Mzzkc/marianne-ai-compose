"""Tests for D-027: baton is the only execution model.

The legacy runner has been fully removed. The baton is the sole execution
path and the old feature flag no longer exists in any form.
"""

import pytest
from pydantic import ValidationError

from marianne.daemon.config import DaemonConfig


class TestBatonDefault:
    """D-027: baton is the only execution path — no feature flag needed."""

    def test_no_legacy_feature_flag(self) -> None:
        """DaemonConfig has no legacy runner feature flag."""
        config = DaemonConfig()
        # No runner-related fields should exist
        field_names = set(config.model_fields.keys())
        assert "use_baton" not in field_names

    def test_unknown_fields_rejected(self) -> None:
        """DaemonConfig rejects unknown fields (extra='forbid')."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            DaemonConfig(**{"unknown_field": True})
