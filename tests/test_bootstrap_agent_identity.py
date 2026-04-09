"""Tests for agent identity bootstrapper."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    """Use tmp_path as the agents root instead of ~/.mzt/agents/."""
    return tmp_path / "agents"


def run_bootstrap(
    agent_dir: Path,
    name: str,
    voice: str = "You are a builder.",
    focus: str = "infrastructure",
    role: str = "builder",
) -> subprocess.CompletedProcess:
    """Run the bootstrapper as a subprocess."""
    return subprocess.run(
        [
            sys.executable,
            "scripts/bootstrap-agent-identity.py",
            "--agents-dir", str(agent_dir),
            "--name", name,
            "--voice", voice,
            "--focus", focus,
            "--role", role,
        ],
        capture_output=True,
        text=True,
    )


class TestBootstrapCreatesIdentityStore:
    """Test that the bootstrapper creates the expected file structure."""

    def test_creates_identity_md(self, agent_dir: Path) -> None:
        result = run_bootstrap(agent_dir, "foundry")
        assert result.returncode == 0
        identity = agent_dir / "foundry" / "identity.md"
        assert identity.exists()
        content = identity.read_text()
        assert "You are a builder." in content
        assert "infrastructure" in content

    def test_creates_profile_yaml(self, agent_dir: Path) -> None:
        run_bootstrap(agent_dir, "foundry")
        profile = agent_dir / "foundry" / "profile.yaml"
        assert profile.exists()
        data = yaml.safe_load(profile.read_text())
        assert data["developmental_stage"] == "recognition"
        assert data["relationships"] == {}
        assert data["role"] == "builder"

    def test_creates_recent_md(self, agent_dir: Path) -> None:
        run_bootstrap(agent_dir, "foundry")
        recent = agent_dir / "foundry" / "recent.md"
        assert recent.exists()
        content = recent.read_text()
        assert "No activity yet" in content

    def test_creates_growth_md(self, agent_dir: Path) -> None:
        run_bootstrap(agent_dir, "foundry")
        growth = agent_dir / "foundry" / "growth.md"
        assert growth.exists()

    def test_creates_archive_dir(self, agent_dir: Path) -> None:
        run_bootstrap(agent_dir, "foundry")
        archive = agent_dir / "foundry" / "archive"
        assert archive.is_dir()

    def test_refuses_to_overwrite_existing(self, agent_dir: Path) -> None:
        run_bootstrap(agent_dir, "foundry")
        result = run_bootstrap(agent_dir, "foundry")
        assert result.returncode != 0
        assert "already exists" in result.stderr

    def test_identity_within_token_budget(self, agent_dir: Path) -> None:
        run_bootstrap(agent_dir, "foundry")
        identity = agent_dir / "foundry" / "identity.md"
        word_count = len(identity.read_text().split())
        # L1 budget is ~1200 tokens, roughly 900 words
        assert word_count < 900
