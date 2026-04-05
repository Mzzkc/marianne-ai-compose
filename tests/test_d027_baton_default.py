"""Tests for D-027: use_baton defaults to True.

The baton is the conductor's event-driven execution model.
D-027 makes it the default, replacing the legacy monolithic runner.

TDD: Tests define the contract. Implementation fulfills it.
"""

from mozart.daemon.config import DaemonConfig


class TestBatonDefault:
    """D-027: use_baton must default to True (Phase 2 of baton transition)."""

    def test_use_baton_defaults_to_true(self) -> None:
        """DaemonConfig.use_baton defaults to True."""
        config = DaemonConfig()
        assert config.use_baton is True, (
            "D-027: use_baton must default to True. "
            "The baton IS how the conductor runs."
        )

    def test_use_baton_can_be_disabled(self) -> None:
        """Explicit use_baton: false remains supported as fallback."""
        config = DaemonConfig(use_baton=False)
        assert config.use_baton is False

    def test_use_baton_in_yaml_override(self) -> None:
        """YAML config can override use_baton to False for legacy fallback."""
        import yaml

        yaml_str = "use_baton: false"
        data = yaml.safe_load(yaml_str)
        config = DaemonConfig(**data)
        assert config.use_baton is False
