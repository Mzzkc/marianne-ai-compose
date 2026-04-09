"""Tests for CLI instrument scripts."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml


INSTRUMENTS_DIR = Path("scripts/instruments")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "TASKS.md").write_text("- [ ] Fix critical bug (priority: P0)\n")
    (ws / "composer-notes.yaml").write_text("notes:\n  - directive: work hard\n    priority: P0\n")
    return ws


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    d.mkdir()
    (d / "identity.md").write_text("# Test Agent\n\nYou are a test agent.\n")
    (d / "profile.yaml").write_text(yaml.dump({
        "developmental_stage": "recognition",
        "relationships": {},
        "standing_pattern_count": 0,
        "coherence_trajectory": [],
        "cycle_count": 5,
        "last_play_cycle": 0,
    }))
    (d / "recent.md").write_text("# Recent\n\nDid some work.\n")
    (d / "growth.md").write_text("# Growth\n\nLearning.\n")
    return d


class TestTemperatureCheck:
    def test_work_when_p0_tasks_exist(self, workspace: Path, agent_dir: Path) -> None:
        result = subprocess.run(
            ["bash", str(INSTRUMENTS_DIR / "temperature-check.sh")],
            env={**os.environ, "WORKSPACE": str(workspace), "AGENT_DIR": str(agent_dir),
                 "AGENT_NAME": "test", "MEMORY_BLOAT_THRESHOLD": "3000",
                 "STAGNATION_CYCLES": "3", "MIN_CYCLES_BETWEEN_PLAY": "5"},
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_play_when_no_urgent_tasks(self, workspace: Path, agent_dir: Path) -> None:
        (workspace / "TASKS.md").write_text("- [x] All done (priority: P0)\n")
        (agent_dir / "profile.yaml").write_text(yaml.dump({"cycle_count": 10, "last_play_cycle": 0}))
        result = subprocess.run(
            ["bash", str(INSTRUMENTS_DIR / "temperature-check.sh")],
            env={**os.environ, "WORKSPACE": str(workspace), "AGENT_DIR": str(agent_dir),
                 "AGENT_NAME": "test", "MEMORY_BLOAT_THRESHOLD": "3000",
                 "STAGNATION_CYCLES": "3", "MIN_CYCLES_BETWEEN_PLAY": "5"},
            capture_output=True, text=True,
        )
        assert result.returncode == 0


class TestTokenBudgetCheck:
    def test_passes_when_within_budget(self, agent_dir: Path) -> None:
        result = subprocess.run(
            ["bash", str(INSTRUMENTS_DIR / "token-budget-check.sh")],
            env={**os.environ, "AGENT_DIR": str(agent_dir),
                 "L1_BUDGET": "900", "L2_BUDGET": "1500", "L3_BUDGET": "1500"},
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_fails_when_over_budget(self, agent_dir: Path) -> None:
        (agent_dir / "recent.md").write_text("word " * 2000)
        result = subprocess.run(
            ["bash", str(INSTRUMENTS_DIR / "token-budget-check.sh")],
            env={**os.environ, "AGENT_DIR": str(agent_dir),
                 "L1_BUDGET": "900", "L2_BUDGET": "1500", "L3_BUDGET": "100"},
            capture_output=True, text=True,
        )
        assert result.returncode == 1


class TestMaturityCheck:
    def test_writes_report(self, agent_dir: Path, tmp_path: Path) -> None:
        report_path = tmp_path / "maturity-report.yaml"
        result = subprocess.run(
            ["bash", str(INSTRUMENTS_DIR / "maturity-check.sh")],
            env={**os.environ, "AGENT_DIR": str(agent_dir), "REPORT_PATH": str(report_path)},
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert report_path.exists()
        report = yaml.safe_load(report_path.read_text())
        assert "current_stage" in report
        assert report["current_stage"] == "recognition"
