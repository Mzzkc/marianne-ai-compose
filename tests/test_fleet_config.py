"""Tests for the fleet configuration system.

Verifies:
1. Fleet detection (is_fleet_config)
2. Topological sorting of group dependencies
3. Fleet submission flow
4. Fleet-level operations (pause, resume, cancel, status)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from marianne.core.config.fleet import FleetConfig, FleetGroupConfig, FleetScoreEntry
from marianne.daemon.registry import DaemonJobStatus

# ─── Fleet Detection Tests ──────────────────────────────────────────


class TestIsFleetConfig:
    """Test fleet config detection from YAML files."""

    def test_detects_fleet_config(self, tmp_path: Path) -> None:
        from marianne.daemon.fleet import is_fleet_config

        fleet_yaml = tmp_path / "fleet.yaml"
        fleet_yaml.write_text("name: test-fleet\ntype: fleet\nscores:\n  - path: a.yaml\n")
        assert is_fleet_config(fleet_yaml) is True

    def test_rejects_normal_config(self, tmp_path: Path) -> None:
        from marianne.daemon.fleet import is_fleet_config

        score_yaml = tmp_path / "score.yaml"
        score_yaml.write_text("name: test-score\nworkspace: ./ws\n")
        assert is_fleet_config(score_yaml) is False

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        from marianne.daemon.fleet import is_fleet_config

        assert is_fleet_config(tmp_path / "nonexistent.yaml") is False

    def test_rejects_invalid_yaml(self, tmp_path: Path) -> None:
        from marianne.daemon.fleet import is_fleet_config

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("{{{{invalid yaml")
        assert is_fleet_config(bad_yaml) is False

    def test_malformed_yaml_logs_warning(self, tmp_path: Path) -> None:
        """#255: a malformed (vs simply non-fleet) file must not fail silently —
        the parse error is surfaced so a misconfig is distinguishable."""
        from unittest.mock import patch

        from marianne.daemon import fleet as fleet_mod

        bad_yaml = tmp_path / "broken.yaml"
        bad_yaml.write_text("type: fleet\n  bad: : indentation")
        with patch.object(fleet_mod, "_logger") as mock_logger:
            assert fleet_mod.is_fleet_config(bad_yaml) is False

        warn_calls = [
            c for c in mock_logger.warning.call_args_list
            if c.args and c.args[0] == "fleet.config_unreadable"
        ]
        assert len(warn_calls) == 1
        assert str(bad_yaml) in warn_calls[0].kwargs["path"]

    def test_missing_file_does_not_warn(self, tmp_path: Path) -> None:
        """A missing file is expected when probing paths — debug, not warning."""
        from unittest.mock import patch

        from marianne.daemon import fleet as fleet_mod

        with patch.object(fleet_mod, "_logger") as mock_logger:
            assert fleet_mod.is_fleet_config(tmp_path / "nope.yaml") is False

        assert mock_logger.warning.call_count == 0


# ─── Topological Sort Tests ─────────────────────────────────────────


class TestTopologicalSortGroups:
    """Test group dependency resolution."""

    def test_empty_groups(self) -> None:
        from marianne.daemon.fleet import topological_sort_groups

        result = topological_sort_groups({})
        assert result == [set()]

    def test_single_group_no_deps(self) -> None:
        from marianne.daemon.fleet import topological_sort_groups

        groups = {"builders": FleetGroupConfig(depends_on=[])}
        result = topological_sort_groups(groups)
        assert len(result) == 1
        assert "builders" in result[0]

    def test_linear_dependency_chain(self) -> None:
        from marianne.daemon.fleet import topological_sort_groups

        groups = {
            "architects": FleetGroupConfig(depends_on=[]),
            "builders": FleetGroupConfig(depends_on=["architects"]),
            "auditors": FleetGroupConfig(depends_on=["builders"]),
        }
        result = topological_sort_groups(groups)
        assert len(result) == 3
        assert result[0] == {"architects"}
        assert result[1] == {"builders"}
        assert result[2] == {"auditors"}

    def test_parallel_independent_groups(self) -> None:
        from marianne.daemon.fleet import topological_sort_groups

        groups = {
            "alpha": FleetGroupConfig(depends_on=[]),
            "beta": FleetGroupConfig(depends_on=[]),
            "gamma": FleetGroupConfig(depends_on=[]),
        }
        result = topological_sort_groups(groups)
        assert len(result) == 1
        assert result[0] == {"alpha", "beta", "gamma"}

    def test_diamond_dependency(self) -> None:
        from marianne.daemon.fleet import topological_sort_groups

        groups = {
            "root": FleetGroupConfig(depends_on=[]),
            "left": FleetGroupConfig(depends_on=["root"]),
            "right": FleetGroupConfig(depends_on=["root"]),
            "join": FleetGroupConfig(depends_on=["left", "right"]),
        }
        result = topological_sort_groups(groups)
        assert len(result) == 3
        assert result[0] == {"root"}
        assert result[1] == {"left", "right"}
        assert result[2] == {"join"}


# ─── Fleet Config Model Tests ───────────────────────────────────────


class TestFleetConfigModel:
    """Test FleetConfig validation and construction."""

    def test_valid_fleet_config(self) -> None:
        config = FleetConfig(
            name="test-fleet",
            type="fleet",
            scores=[
                FleetScoreEntry(path="scores/a.yaml", group="g1"),
                FleetScoreEntry(path="scores/b.yaml", group="g1"),
            ],
            groups={"g1": FleetGroupConfig(depends_on=[])},
        )
        assert config.name == "test-fleet"
        assert len(config.scores) == 2

    def test_undefined_group_dependency_raises(self) -> None:
        with pytest.raises(ValueError, match="undefined group"):
            FleetConfig(
                name="bad-fleet",
                scores=[FleetScoreEntry(path="a.yaml")],
                groups={
                    "g1": FleetGroupConfig(depends_on=["nonexistent"]),
                },
            )

    def test_circular_dependency_raises(self) -> None:
        with pytest.raises(ValueError, match="Circular dependency"):
            FleetConfig(
                name="bad-fleet",
                scores=[FleetScoreEntry(path="a.yaml")],
                groups={
                    "a": FleetGroupConfig(depends_on=["b"]),
                    "b": FleetGroupConfig(depends_on=["a"]),
                },
            )


# ─── Fleet Submission Tests ──────────────────────────────────────────


class TestFleetSubmission:
    """Test fleet submission flow."""

    async def test_submit_fleet_launches_scores(self, tmp_path: Path) -> None:
        from marianne.daemon.fleet import submit_fleet
        from marianne.daemon.types import JobResponse

        # Create score files
        (tmp_path / "a.yaml").write_text("name: agent-a\nworkspace: ./ws\n")
        (tmp_path / "b.yaml").write_text("name: agent-b\nworkspace: ./ws\n")

        fleet_config = FleetConfig(
            name="test-fleet",
            scores=[
                FleetScoreEntry(path="a.yaml"),
                FleetScoreEntry(path="b.yaml"),
            ],
        )
        fleet_path = tmp_path / "fleet.yaml"

        # Mock manager
        manager = MagicMock()
        manager._fleet_records = {}
        manager.submit_job = AsyncMock(
            side_effect=[
                JobResponse(job_id="agent-a", status="accepted", message="ok"),
                JobResponse(job_id="agent-b", status="accepted", message="ok"),
            ]
        )

        response = await submit_fleet(manager, fleet_path, fleet_config)

        assert response.status == "accepted"
        assert "test-fleet" in manager._fleet_records
        record = manager._fleet_records["test-fleet"]
        assert len(record.member_jobs) == 2

    async def test_submit_fleet_missing_score_rejects(self, tmp_path: Path) -> None:
        from marianne.daemon.fleet import submit_fleet

        fleet_config = FleetConfig(
            name="test-fleet",
            scores=[FleetScoreEntry(path="nonexistent.yaml")],
        )

        manager = MagicMock()
        manager._fleet_records = {}
        manager.submit_job = AsyncMock()

        response = await submit_fleet(
            manager,
            tmp_path / "fleet.yaml",
            fleet_config,
        )

        # The fleet should still complete (with partial failure)
        assert response.status in ("accepted", "rejected")

    async def test_submit_fleet_waits_for_dependency_group(
        self,
        tmp_path: Path,
    ) -> None:
        from marianne.daemon.fleet import submit_fleet
        from marianne.daemon.types import JobResponse

        (tmp_path / "a.yaml").write_text("name: agent-a\nworkspace: ./ws\n")
        (tmp_path / "b.yaml").write_text("name: agent-b\nworkspace: ./ws\n")

        fleet_config = FleetConfig(
            name="test-fleet",
            scores=[
                FleetScoreEntry(path="a.yaml", group="root"),
                FleetScoreEntry(path="b.yaml", group="workers"),
            ],
            groups={
                "root": FleetGroupConfig(depends_on=[]),
                "workers": FleetGroupConfig(depends_on=["root"]),
            },
        )

        manager = MagicMock()
        manager._fleet_records = {}
        manager._job_meta = {}
        submitted: list[str] = []

        async def submit_job(request) -> JobResponse:
            job_id = request.config_path.stem
            submitted.append(job_id)
            manager._job_meta[job_id] = SimpleNamespace(
                status=DaemonJobStatus.RUNNING
            )
            if job_id == "a":
                async def complete_a() -> None:
                    await asyncio.sleep(0.05)
                    manager._job_meta[job_id].status = DaemonJobStatus.COMPLETED

                asyncio.create_task(complete_a())
            else:
                assert manager._job_meta["a"].status == DaemonJobStatus.COMPLETED
                manager._job_meta[job_id].status = DaemonJobStatus.COMPLETED
            return JobResponse(job_id=job_id, status="accepted", message="ok")

        manager.submit_job = AsyncMock(side_effect=submit_job)

        response = await submit_fleet(manager, tmp_path / "fleet.yaml", fleet_config)

        assert response.status == "accepted"
        assert submitted == ["a"]

        for _ in range(20):
            if submitted == ["a", "b"]:
                break
            await asyncio.sleep(0.1)

        assert submitted == ["a", "b"]


# ─── Fleet Operations Tests ──────────────────────────────────────────


class TestFleetOperations:
    """Test fleet-level pause/resume/cancel/status."""

    @pytest.fixture
    def manager_with_fleet(self) -> MagicMock:
        from marianne.daemon.fleet import FleetRecord

        manager = MagicMock()
        manager.pause_job = AsyncMock(return_value=True)
        manager.resume_job = AsyncMock(
            return_value=MagicMock(status="accepted"),
        )
        manager.cancel_job = AsyncMock(return_value=True)

        record = FleetRecord(
            fleet_id="test-fleet",
            config=FleetConfig(
                name="test-fleet",
                scores=[
                    FleetScoreEntry(path="a.yaml", group="g1"),
                    FleetScoreEntry(path="b.yaml", group="g1"),
                ],
                groups={"g1": FleetGroupConfig()},
            ),
            config_path=Path("/tmp/fleet.yaml"),
            member_jobs={"a.yaml": "agent-a", "b.yaml": "agent-b"},
            group_order=[{"g1"}],
        )
        manager._fleet_records = {"test-fleet": record}
        manager._job_meta = {
            "agent-a": MagicMock(status=DaemonJobStatus.RUNNING),
            "agent-b": MagicMock(status=DaemonJobStatus.RUNNING),
        }
        return manager

    async def test_pause_fleet(self, manager_with_fleet: MagicMock) -> None:
        from marianne.daemon.fleet import pause_fleet

        result = await pause_fleet(manager_with_fleet, "test-fleet")
        assert result["fleet_id"] == "test-fleet"
        assert all(v is True for v in result["paused"].values())

    async def test_resume_fleet(self, manager_with_fleet: MagicMock) -> None:
        from marianne.daemon.fleet import resume_fleet

        result = await resume_fleet(manager_with_fleet, "test-fleet")
        assert result["fleet_id"] == "test-fleet"
        assert all(v == "accepted" for v in result["resumed"].values())

    async def test_cancel_fleet(self, manager_with_fleet: MagicMock) -> None:
        from marianne.daemon.fleet import cancel_fleet

        result = await cancel_fleet(manager_with_fleet, "test-fleet")
        assert result["fleet_id"] == "test-fleet"
        assert all(v is True for v in result["cancelled"].values())

    def test_fleet_status(self, manager_with_fleet: MagicMock) -> None:
        from marianne.daemon.fleet import get_fleet_status

        result = get_fleet_status(manager_with_fleet, "test-fleet")
        assert result["fleet_id"] == "test-fleet"
        assert len(result["members"]) == 2
        assert all(m["status"] == "running" for m in result["members"])

    async def test_pause_nonexistent_fleet(self) -> None:
        from marianne.daemon.fleet import pause_fleet

        manager = MagicMock()
        manager._fleet_records = {}
        result = await pause_fleet(manager, "nonexistent")
        assert "error" in result

    def test_status_nonexistent_fleet(self) -> None:
        from marianne.daemon.fleet import get_fleet_status

        manager = MagicMock()
        manager._fleet_records = {}
        result = get_fleet_status(manager, "nonexistent")
        assert "error" in result


# ─── FleetRecord Tests ──────────────────────────────────────────────


class TestFleetRecord:
    """Test FleetRecord data structure."""

    def test_all_job_ids(self) -> None:
        from marianne.daemon.fleet import FleetRecord

        record = FleetRecord(
            fleet_id="f1",
            config=FleetConfig(
                name="f1",
                scores=[FleetScoreEntry(path="a.yaml")],
            ),
            config_path=Path("/tmp/f1.yaml"),
            member_jobs={"a.yaml": "job-a", "b.yaml": "job-b"},
            group_order=[set()],
        )
        assert set(record.all_job_ids) == {"job-a", "job-b"}
