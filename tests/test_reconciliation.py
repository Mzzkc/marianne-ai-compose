"""Tests for config reconciliation on reload."""
from __future__ import annotations

from typing import Any

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.core.config import JobConfig


class TestConfigStateMapping:
    """Tests for CONFIG_STATE_MAPPING completeness."""

    def test_mapping_covers_all_config_sections(self) -> None:
        """Every reconcilable JobConfig section must have a mapping entry."""
        from marianne.execution.reconciliation import (
            CONFIG_STATE_MAPPING,
            METADATA_FIELDS,
        )

        config_sections = set(JobConfig.model_fields.keys())
        mapped_sections = set(CONFIG_STATE_MAPPING.keys())
        reconcilable = config_sections - METADATA_FIELDS

        unmapped = reconcilable - mapped_sections
        assert not unmapped, (
            f"Config sections {unmapped} have no CONFIG_STATE_MAPPING entry. "
            "Add an entry to define what checkpoint state should be reset "
            "when this section changes. Use [] if the runner recreates it."
        )

    def test_mapped_fields_exist_on_checkpoint_state(self) -> None:
        """All fields referenced in mapping must exist on CheckpointState."""
        from marianne.execution.reconciliation import CONFIG_STATE_MAPPING

        checkpoint_fields = set(CheckpointState.model_fields.keys())
        for section, fields in CONFIG_STATE_MAPPING.items():
            for field_name in fields:
                assert field_name in checkpoint_fields, (
                    f"CONFIG_STATE_MAPPING['{section}'] references "
                    f"'{field_name}' which doesn't exist on CheckpointState"
                )


class TestReconcileConfig:
    """Tests for reconcile_config() logic."""

    def _make_state(self, **overrides: Any) -> CheckpointState:
        defaults: dict[str, Any] = {
            "job_id": "test",
            "job_name": "Test",
            "total_sheets": 3,
        }
        defaults.update(overrides)
        return CheckpointState(**defaults)

    def _make_snapshot(self, **overrides: Any) -> dict:
        base: dict[str, Any] = {
            "name": "test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 3, "total_items": 9},
            "prompt": {"template": "Test {{ sheet_num }}"},
        }
        base.update(overrides)
        return base

    def test_no_changes_returns_empty_report(self) -> None:
        """Identical configs should produce empty report."""
        from marianne.execution.reconciliation import reconcile_config

        snapshot = self._make_snapshot()
        config = JobConfig.model_validate(snapshot)
        # Use the config's own dump as snapshot so defaults match exactly
        state = self._make_state(config_snapshot=config.model_dump(mode="json"))

        report = reconcile_config(state, config)
        assert report.sections_changed == []
        assert report.fields_reset == {}

    def test_cost_limits_change_resets_cost_state(self) -> None:
        """Changing cost_limits should reset cost tracking fields."""
        from marianne.execution.reconciliation import reconcile_config

        old_snapshot = self._make_snapshot(
            cost_limits={"max_cost_per_job": 10.0}
        )
        # Build full snapshot from validated config so defaults match
        old_config = JobConfig.model_validate(old_snapshot)
        new_snapshot = self._make_snapshot(
            cost_limits={"max_cost_per_job": 50.0}
        )
        new_config = JobConfig.model_validate(new_snapshot)
        state = self._make_state(
            config_snapshot=old_config.model_dump(mode="json"),
            total_estimated_cost=8.5,
            cost_limit_reached=True,
        )

        report = reconcile_config(state, new_config)
        assert "cost_limits" in report.sections_changed
        # Verify state was reset
        assert state.total_estimated_cost == 0.0
        assert state.cost_limit_reached is False

    def test_unchanged_section_not_reset(self) -> None:
        """Sections that didn't change should not reset state."""
        from marianne.execution.reconciliation import reconcile_config

        snapshot = self._make_snapshot()
        config = JobConfig.model_validate(snapshot)
        state = self._make_state(
            config_snapshot=config.model_dump(mode="json"),
            total_estimated_cost=5.0,
            rate_limit_waits=3,
        )

        report = reconcile_config(state, config)
        assert state.total_estimated_cost == 5.0  # untouched
        assert state.rate_limit_waits == 3  # untouched

    def test_rate_limit_change_resets_counters(self) -> None:
        """Changing rate_limit should reset rate limit tracking fields."""
        from marianne.execution.reconciliation import reconcile_config

        old_snapshot = self._make_snapshot(
            rate_limit={"wait_minutes": 30, "max_waits": 12}
        )
        old_config = JobConfig.model_validate(old_snapshot)
        new_snapshot = self._make_snapshot(
            rate_limit={"wait_minutes": 60, "max_waits": 24}
        )
        new_config = JobConfig.model_validate(new_snapshot)
        state = self._make_state(
            config_snapshot=old_config.model_dump(mode="json"),
            rate_limit_waits=7,
            quota_waits=2,
        )

        report = reconcile_config(state, new_config)
        assert "rate_limit" in report.sections_changed
        assert state.rate_limit_waits == 0
        assert state.quota_waits == 0

    def test_none_snapshot_treats_all_as_new(self) -> None:
        """When config_snapshot is None, reconciliation treats everything as new.

        A None snapshot represents fresh state (first run or pre-snapshot era).
        All sections appear as 'changed' but no checkpoint fields should need
        resetting because they are already at defaults.
        """
        from marianne.execution.reconciliation import reconcile_config

        snapshot = self._make_snapshot()
        config = JobConfig.model_validate(snapshot)
        # State with no prior config_snapshot — fresh start
        state = self._make_state(config_snapshot=None)

        report = reconcile_config(state, config)

        # Sections show as "changed" (empty old vs populated new)
        assert report.has_changes
        assert len(report.sections_changed) > 0

        # But no fields should be reset since state is at defaults
        assert report.fields_reset == {}

        # Verify state fields are still at defaults (untouched)
        assert state.total_estimated_cost == 0.0
        assert state.cost_limit_reached is False
        assert state.rate_limit_waits == 0
        assert state.quota_waits == 0
