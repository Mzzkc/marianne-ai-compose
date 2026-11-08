"""A project venue class file the conductor never read is refused at submit (GH #510).

The conductor resolves ``.marianne/classes.yaml`` from ITS working directory
(``$HOME`` under the shipped systemd unit), so a checkout's venue file reached
``mzt validate`` but never dispatch: an edit to it followed by ``mzt conductor
reload`` changed what the validator printed and not what the conductor ran
(Pallas, 2026-11-08, while reseating classes around a quota outage).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from marianne.core.config.job import JobConfig
from marianne.instruments.classes import (
    load_class_map,
    requested_class_names,
    unloaded_venue_class_layer,
)


def _job(**overrides: object) -> JobConfig:
    data: dict[str, object] = {
        "name": "venue-job", "workspace": "/tmp/venue-job",
        "sheet": {"size": 1, "total_items": 1}, "prompt": {"template": "Work"},
    }
    data.update(overrides)
    return JobConfig.model_validate(data)


def _layers(tmp_path: Path) -> tuple[Path, Path]:
    user = tmp_path / "user-classes.yaml"
    user.write_text(yaml.safe_dump({"version": 1, "classes": {"strong": ["claude-code"]}}))
    daemon_venue = tmp_path / "daemon-cwd" / ".marianne" / "classes.yaml"
    daemon_venue.parent.mkdir(parents=True)
    daemon_venue.write_text(yaml.safe_dump({"version": 1, "classes": {"fast": ["codex-cli"]}}))
    return user, daemon_venue


def _project(tmp_path: Path, classes: dict[str, object]) -> Path:
    project = tmp_path / "project"
    (project / ".marianne").mkdir(parents=True)
    (project / ".marianne" / "classes.yaml").write_text(
        yaml.safe_dump({"version": 1, "classes": classes})
    )
    return project


PROFILES = {"claude-code", "codex-cli", "cli"}


def test_project_venue_file_redefining_a_used_class_is_reported(tmp_path: Path) -> None:
    user, daemon_venue = _layers(tmp_path)
    class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    project = _project(tmp_path, {"strong": ["codex-cli"]})
    job = _job(instrument="strong")
    requested = requested_class_names(job, PROFILES, class_map)
    assert requested == {"strong"}
    found = unloaded_venue_class_layer(class_map, project, requested)
    assert found is not None
    beside, loaded = found
    assert beside == project / ".marianne" / "classes.yaml"
    assert loaded.resolve() == daemon_venue.resolve()


def test_project_venue_file_that_touches_no_used_class_is_silent(tmp_path: Path) -> None:
    user, daemon_venue = _layers(tmp_path)
    class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    project = _project(tmp_path, {"cheap": ["cli"]})
    job = _job(instrument="strong")
    requested = requested_class_names(job, PROFILES, class_map)
    assert unloaded_venue_class_layer(class_map, project, requested) is None


def test_same_venue_file_is_not_a_conflict(tmp_path: Path) -> None:
    user, daemon_venue = _layers(tmp_path)
    class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    project = daemon_venue.parents[1]  # the conductor started from the project
    job = _job(instrument="fast")
    requested = requested_class_names(job, PROFILES, class_map)
    assert requested == {"fast"}
    assert unloaded_venue_class_layer(class_map, project, requested) is None


def test_profile_and_alias_names_are_not_class_requests(tmp_path: Path) -> None:
    user, daemon_venue = _layers(tmp_path)
    class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    job = _job(instrument="claude-code")
    assert requested_class_names(job, PROFILES, class_map) == set()


def test_no_client_cwd_or_no_file_is_silent(tmp_path: Path) -> None:
    user, daemon_venue = _layers(tmp_path)
    class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    assert unloaded_venue_class_layer(class_map, None, {"strong"}) is None
    assert unloaded_venue_class_layer(class_map, tmp_path / "empty", {"strong"}) is None


def test_invalid_project_venue_file_is_left_to_the_validator(tmp_path: Path) -> None:
    user, daemon_venue = _layers(tmp_path)
    class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    project = tmp_path / "project"
    (project / ".marianne").mkdir(parents=True)
    (project / ".marianne" / "classes.yaml").write_text("classes: [not, a, map\n")
    assert unloaded_venue_class_layer(class_map, project, {"strong"}) is None


async def test_submit_refuses_with_both_paths_named(tmp_path: Path) -> None:
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.manager import JobManager
    from marianne.daemon.types import JobRequest
    from marianne.instruments.loader import load_all_profiles
    from marianne.instruments.registry import InstrumentRegistry

    user, daemon_venue = _layers(tmp_path)
    project = _project(tmp_path, {"strong": ["codex-cli"]})
    score_path = project / "score.yaml"
    score = _job(instrument="strong", workspace=str(tmp_path / "ws"))
    score_path.write_text(yaml.safe_dump(score.model_dump(mode="json")))
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    registry = InstrumentRegistry()
    for profile in load_all_profiles().values():
        registry.register(profile)
    manager._instrument_registry = registry
    manager._class_map = load_class_map(user_path=user, venue_path=daemon_venue)
    await manager._registry.open()
    try:
        response = await manager.submit_job(
            JobRequest(config_path=score_path, client_cwd=project)
        )
    finally:
        await manager._registry.close()
    assert response.status == "rejected"
    assert str(project / ".marianne" / "classes.yaml") in response.message
    assert str(daemon_venue.resolve()) in response.message
    assert "mzt conductor reload" in response.message
