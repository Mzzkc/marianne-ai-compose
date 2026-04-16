"""Tests for the fleet config generator module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from marianne.compose.fleet import FleetGenerator
from marianne.core.config.fleet import FleetConfig, FleetGroupConfig
from marianne.daemon.fleet import topological_sort_groups


def _make_config(
    agents: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "project": {"name": "test-project"},
        "agents": agents
        or [
            {"name": "canyon", "voice": "v1", "focus": "f1", "group": "architects"},
            {"name": "forge", "voice": "v2", "focus": "f2", "group": "builders"},
            {"name": "sentinel", "voice": "v3", "focus": "f3", "group": "auditors"},
        ],
        "groups": {
            "architects": {"depends_on": []},
            "builders": {"depends_on": ["architects"]},
            "auditors": {"depends_on": ["builders"]},
        },
    }


class TestFleetGenerator:
    """Tests for FleetGenerator."""

    def test_generates_fleet_config(self) -> None:
        """Generate produces a valid fleet config dict."""
        gen = FleetGenerator()
        result = gen.generate(_make_config(), Path("/scores"))

        assert result["name"] == "test-project-fleet"
        assert result["type"] == "fleet"
        assert len(result["scores"]) == 3

    def test_fleet_scores_have_paths(self) -> None:
        """Each score entry has a path to the score YAML."""
        gen = FleetGenerator()
        result = gen.generate(_make_config(), Path("/scores"))

        for score in result["scores"]:
            assert "path" in score
            assert score["path"].endswith(".yaml")

    def test_fleet_scores_have_groups(self) -> None:
        """Score entries include their group assignment."""
        gen = FleetGenerator()
        result = gen.generate(_make_config(), Path("/scores"))

        groups = [s.get("group") for s in result["scores"]]
        assert "architects" in groups
        assert "builders" in groups

    def test_fleet_groups_have_dependencies(self) -> None:
        """Fleet groups include dependency declarations."""
        gen = FleetGenerator()
        result = gen.generate(_make_config(), Path("/scores"))

        groups = result["groups"]
        assert "architects" in groups
        assert groups["architects"]["depends_on"] == []
        assert "builders" in groups
        assert groups["builders"]["depends_on"] == ["architects"]
        assert "auditors" in groups
        assert groups["auditors"]["depends_on"] == ["builders"]

    def test_write_creates_file(self, tmp_path: Path) -> None:
        """write() creates the fleet YAML file on disk."""
        gen = FleetGenerator()
        output_path = tmp_path / "fleet.yaml"

        gen.write(_make_config(), tmp_path / "scores", output_path)

        assert output_path.exists()
        data = yaml.safe_load(output_path.read_text())
        assert data["type"] == "fleet"
        assert len(data["scores"]) == 3

    def test_empty_agents_raises(self) -> None:
        """Empty agent list raises ValueError."""
        gen = FleetGenerator()
        config: dict[str, object] = {"project": {"name": "test"}, "agents": []}

        try:
            gen.generate(config, Path("/scores"))
            assert False, "Should have raised"  # noqa: B011
        except ValueError as e:
            assert "agent" in str(e).lower()

    def test_no_groups_defined(self) -> None:
        """Works when no explicit groups are defined."""
        gen = FleetGenerator()
        config: dict[str, object] = {
            "project": {"name": "test"},
            "agents": [
                {"name": "a1", "voice": "v", "focus": "f"},
                {"name": "a2", "voice": "v", "focus": "f"},
            ],
        }

        result = gen.generate(config, Path("/scores"))
        assert len(result["scores"]) == 2
        # No groups section when no groups assigned
        assert result.get("groups", {}) == {}

    def test_inferred_groups(self) -> None:
        """Groups are inferred from agent assignments when not explicit."""
        gen = FleetGenerator()
        config: dict[str, object] = {
            "project": {"name": "test"},
            "agents": [
                {"name": "a1", "voice": "v", "focus": "f", "group": "alpha"},
                {"name": "a2", "voice": "v", "focus": "f", "group": "beta"},
            ],
        }

        result = gen.generate(config, Path("/scores"))
        groups = result.get("groups", {})
        assert "alpha" in groups
        assert "beta" in groups

    def test_single_agent_fleet(self) -> None:
        """Single agent fleet is valid."""
        gen = FleetGenerator()
        config: dict[str, object] = {
            "project": {"name": "solo"},
            "agents": [{"name": "only", "voice": "v", "focus": "f"}],
        }

        result = gen.generate(config, Path("/scores"))
        assert len(result["scores"]) == 1
        assert result["name"] == "solo-fleet"

    def test_generates_valid_fleet_config_model(self) -> None:
        """Generated dict validates against the FleetConfig Pydantic model."""
        gen = FleetGenerator()
        result = gen.generate(_make_config(), Path("/scores"))

        # Must parse without validation errors
        fleet = FleetConfig(**result)
        assert fleet.name == "test-project-fleet"
        assert fleet.type == "fleet"
        assert len(fleet.scores) == 3
        assert len(fleet.groups) == 3
        # Group dependencies survive round-trip
        assert fleet.groups["builders"].depends_on == ["architects"]
        assert fleet.groups["auditors"].depends_on == ["builders"]

    def test_group_dependencies_produce_correct_dag(self) -> None:
        """Group dependencies produce a correct topological ordering (DAG).

        architects → builders → auditors forms a linear chain.
        Topological sort should produce three layers, each with one group.
        """
        gen = FleetGenerator()
        result = gen.generate(_make_config(), Path("/scores"))

        # Parse groups through FleetConfig model
        fleet = FleetConfig(**result)
        layers = topological_sort_groups(fleet.groups)

        # Linear chain: 3 layers, 1 group each
        assert len(layers) == 3
        assert layers[0] == {"architects"}
        assert layers[1] == {"builders"}
        assert layers[2] == {"auditors"}

    def test_dag_with_diamond_dependency(self) -> None:
        """Diamond dependency graph produces correct layer ordering.

        root → {left, right} → merge
        """
        gen = FleetGenerator()
        config: dict[str, object] = {
            "project": {"name": "diamond"},
            "agents": [
                {"name": "a1", "voice": "v", "focus": "f", "group": "root"},
                {"name": "a2", "voice": "v", "focus": "f", "group": "left"},
                {"name": "a3", "voice": "v", "focus": "f", "group": "right"},
                {"name": "a4", "voice": "v", "focus": "f", "group": "merge"},
            ],
            "groups": {
                "root": {"depends_on": []},
                "left": {"depends_on": ["root"]},
                "right": {"depends_on": ["root"]},
                "merge": {"depends_on": ["left", "right"]},
            },
        }
        result = gen.generate(config, Path("/scores"))
        fleet = FleetConfig(**result)
        layers = topological_sort_groups(fleet.groups)

        assert len(layers) == 3
        assert layers[0] == {"root"}
        assert layers[1] == {"left", "right"}
        assert layers[2] == {"merge"}

    def test_rejects_nested_fleets_depth_limit(self) -> None:
        """FleetConfig enforces depth limit — fleet → score → sheet only.

        Fleet type discriminator is Literal["fleet"]. Score entries must
        reference score paths (not other fleets). The model's type field
        is the discriminator — nesting is prevented at the config level
        because fleets are detected by type and treated separately by the
        conductor. A fleet referencing another fleet would be caught during
        submission (is_fleet_config check), and the FleetConfig model itself
        rejects extra fields that would indicate recursive structure.
        """
        # A FleetConfig cannot contain nested fleet entries — it only holds
        # FleetScoreEntry items with path + optional group. There is no
        # sub-fleet field. Attempting to add nested fleet structure fails.
        with pytest.raises(ValidationError):
            FleetConfig(
                name="outer",
                type="fleet",
                scores=[],
                groups={},
                # extra="forbid" rejects any unknown fields
                nested_fleet={"name": "inner", "type": "fleet"},  # type: ignore[call-arg]
            )

    def test_rejects_circular_group_dependencies(self) -> None:
        """FleetConfig model rejects circular dependency graphs."""
        with pytest.raises(ValueError, match="[Cc]ircular"):
            FleetConfig(
                name="cycle",
                type="fleet",
                scores=[
                    {"path": "a.yaml", "group": "x"},  # type: ignore[list-item]
                    {"path": "b.yaml", "group": "y"},  # type: ignore[list-item]
                ],
                groups={
                    "x": FleetGroupConfig(depends_on=["y"]),
                    "y": FleetGroupConfig(depends_on=["x"]),
                },
            )
