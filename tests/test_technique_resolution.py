"""Tests for technique resolution — phase filtering and manifest generation.

Tests cover:
- Phase filtering: exact match, wildcard, empty, multiple
- Manifest generation: skills, MCP, protocols, mixed
- ResolvedTechniques data class
- Integration: full pipeline from JobConfig to manifest
"""

from __future__ import annotations

from marianne.core.config.techniques import TechniqueConfig
from marianne.daemon.baton.techniques import (
    ResolvedTechniques,
    filter_techniques_for_phase,
    generate_technique_manifest,
    resolve_techniques_for_sheet,
)

# =============================================================================
# Phase Filtering Tests
# =============================================================================


class TestPhaseFiltering:
    """Tests for filter_techniques_for_phase()."""

    def test_filter_by_exact_phase(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work", "recon"]},
            ),
            "coord": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["recon"]},
            ),
        }
        result = filter_techniques_for_phase(techniques, "work")
        assert "github" in result
        assert "coord" not in result

    def test_all_phase_wildcard(self) -> None:
        techniques = {
            "a2a": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["all"]},
            ),
        }
        result = filter_techniques_for_phase(techniques, "work")
        assert "a2a" in result

    def test_empty_techniques(self) -> None:
        result = filter_techniques_for_phase({}, "work")
        assert result == {}

    def test_no_techniques_for_phase(self) -> None:
        techniques = {
            "coord": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["recon"]},
            ),
        }
        result = filter_techniques_for_phase(techniques, "work")
        assert result == {}

    def test_multiple_phases_per_technique(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["recon", "work", "inspect"]},
            ),
        }
        for phase in ["recon", "work", "inspect"]:
            assert "github" in filter_techniques_for_phase(techniques, phase)

    def test_technique_with_empty_phases(self) -> None:
        techniques = {
            "dead": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": []},
            ),
        }
        result = filter_techniques_for_phase(techniques, "work")
        assert result == {}

    def test_all_wildcard_with_specific(self) -> None:
        techniques = {
            "always": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["all"]},
            ),
            "sometimes": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
        }
        result = filter_techniques_for_phase(techniques, "work")
        assert len(result) == 2


# =============================================================================
# Manifest Generation Tests
# =============================================================================


class TestManifestGeneration:
    """Tests for generate_technique_manifest()."""

    def test_empty_manifest(self) -> None:
        assert generate_technique_manifest({}) == ""

    def test_manifest_has_header(self) -> None:
        techniques = {
            "coord": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["work"]},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "## Techniques Available This Phase" in manifest

    def test_skill_section(self) -> None:
        techniques = {
            "memory": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["work"]},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "### Skills" in manifest
        assert "**memory**" in manifest

    def test_mcp_section(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "### MCP Tools" in manifest
        assert "**github**" in manifest

    def test_mcp_server_from_config(self) -> None:
        techniques = {
            "gh": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"], "config": {"server": "github-server"}},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "github-server" in manifest

    def test_mcp_server_defaults_to_name(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "server: github" in manifest

    def test_protocol_section(self) -> None:
        techniques = {
            "a2a": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["work"]},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "### Protocols" in manifest
        assert "**a2a**" in manifest

    def test_mixed_kinds_all_sections(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
            "a2a": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["work"]},
            ),
            "memory": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["work"]},
            ),
        }
        manifest = generate_technique_manifest(techniques)
        assert "### MCP Tools" in manifest
        assert "### Protocols" in manifest
        assert "### Skills" in manifest


# =============================================================================
# ResolvedTechniques Tests
# =============================================================================


class TestResolvedTechniques:
    """Tests for the ResolvedTechniques data class."""

    def test_default_empty(self) -> None:
        rt = ResolvedTechniques()
        assert rt.skills == []
        assert rt.mcp_servers == {}
        assert rt.protocols == []
        assert rt.manifest == ""

    def test_frozen(self) -> None:
        rt = ResolvedTechniques()
        import pytest as _pytest

        with _pytest.raises(AttributeError):
            rt.manifest = "changed"  # type: ignore[misc]


# =============================================================================
# Resolve Techniques For Sheet Tests
# =============================================================================


class TestResolveTechniquesForSheet:
    """Tests for the full resolve_techniques_for_sheet pipeline."""

    def test_empty_techniques_returns_empty(self) -> None:
        rt = resolve_techniques_for_sheet({}, "work")
        assert rt.manifest == ""

    def test_skill_appears_in_manifest(self) -> None:
        techniques = {
            "memory": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert "memory" in rt.skills
        assert "memory" in rt.manifest

    def test_mcp_server_defaults_to_technique_name(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert rt.mcp_servers == {"github": "github"}

    def test_mcp_server_name_from_config(self) -> None:
        techniques = {
            "gh": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"], "config": {"server": "github-mcp"}},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert rt.mcp_servers == {"gh": "github-mcp"}

    def test_protocol_in_manifest_not_mcp(self) -> None:
        techniques = {
            "a2a": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert "a2a" in rt.protocols
        assert rt.mcp_servers == {}

    def test_phase_filtering_applied(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
            "recon_only": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["recon"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert "github" in rt.mcp_servers
        assert "recon_only" not in rt.skills

    def test_mcp_appears_in_both(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert "github" in rt.mcp_servers
        assert "github" in rt.manifest

    def test_mixed_techniques_separation(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
            "a2a": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["all"]},
            ),
            "memory": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert len(rt.mcp_servers) == 1
        assert len(rt.protocols) == 1
        assert len(rt.skills) == 1

    def test_multiple_mcp_servers(self) -> None:
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
            "fs": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert len(rt.mcp_servers) == 2

    def test_no_match_returns_empty(self) -> None:
        techniques = {
            "recon_only": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["recon"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert rt.manifest == ""


# =============================================================================
# Integration Tests
# =============================================================================


class TestTechniqueResolutionIntegration:
    """End-to-end technique resolution from JobConfig."""

    def test_full_pipeline(self) -> None:
        """Resolve techniques from a dict mimicking JobConfig.techniques."""
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["recon", "work"]},
            ),
            "a2a": TechniqueConfig.model_validate(
                {"kind": "protocol", "phases": ["all"]},
            ),
            "memory": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["consolidate"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "work")
        assert "github" in rt.mcp_servers
        assert "a2a" in rt.protocols
        assert "memory" not in rt.skills  # consolidate phase, not work

    def test_resolve_from_job_config(self) -> None:
        """Techniques parsed from JobConfig are resolvable."""
        from marianne.core.config.job import JobConfig

        data = {
            "name": "test",
            "workspace": "/tmp/test",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "techniques": {
                "github": {"kind": "mcp", "phases": ["work"]},
            },
        }
        config = JobConfig.model_validate(data)
        rt = resolve_techniques_for_sheet(config.techniques, "work")
        assert "github" in rt.mcp_servers

    def test_full_pipeline_recon_phase(self) -> None:
        """Technique resolution filters correctly for recon phase."""
        techniques = {
            "github": TechniqueConfig.model_validate(
                {"kind": "mcp", "phases": ["recon", "work"]},
            ),
            "coord": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["recon"]},
            ),
            "work_only": TechniqueConfig.model_validate(
                {"kind": "skill", "phases": ["work"]},
            ),
        }
        rt = resolve_techniques_for_sheet(techniques, "recon")
        assert "github" in rt.mcp_servers
        assert "coord" in rt.skills
        assert "work_only" not in rt.skills


def test_movement_phase_names_gate_techniques() -> None:
    from marianne.daemon.baton.techniques import movement_phase_name

    expected = {1: "recon", 2: "plan", 3: "work", 4: "integration", 5: "inspect", 6: "aar"}
    for number, name in expected.items():
        assert movement_phase_name(number) == name
    for number in (0, -1, 7, 999):
        assert movement_phase_name(number) == "work"
    techniques = {
        "tool": TechniqueConfig(kind="mcp", phases=["work"], config={"server": "tool"}),
        "shared": TechniqueConfig(kind="protocol", phases=["all"]),
    }
    assert resolve_techniques_for_sheet(techniques, movement_phase_name(3)).mcp_servers == {
        "tool": "tool",
    }
    recon = resolve_techniques_for_sheet(techniques, movement_phase_name(1))
    assert recon.mcp_servers == {}
    assert recon.protocols == ["shared"]
