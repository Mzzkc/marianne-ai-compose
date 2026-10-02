"""Opt-in route identity protects a request without changing legacy dispatch."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from marianne.core.config import instruments


def _binding(**updates):
    fields = {
        "arm": "local",
        "instrument": "local",
        "kind": "http",
        "profile_origin": "venue",
        "profile_file_sha256": "a" * 64,
        "effective_model": "requested",
        "effective_provider": None,
        "model_source": "score_override",
        "provider_source": None,
        "transport_scheme": "http",
        "transport_host": "127.0.0.1",
        "transport_port": 9000,
        "transport_endpoint": "/chat/completions",
        "resolved_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return instruments.InstrumentRouteBinding(**(fields | updates))


def test_later_capture_has_same_exact_identity_but_keeps_observation_time():
    from marianne.instruments import loader

    first = _binding()
    later = _binding(resolved_at=first.resolved_at + timedelta(seconds=2))
    assert first != later
    assert loader.route_identity(first) == loader.route_identity(later)
    assert loader.route_identity(first) == (
        "local",
        "local",
        "http",
        "venue",
        "a" * 64,
        "requested",
        None,
        "score_override",
        None,
        "http",
        "127.0.0.1",
        9000,
        "/chat/completions",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("arm", "remote"),
        ("instrument", "other"),
        ("kind", "cli"),
        ("profile_origin", "organization"),
        ("profile_file_sha256", "b" * 64),
        ("effective_model", " requested "),
        ("effective_provider", "provider"),
        ("model_source", "profile"),
        ("provider_source", "profile"),
        ("transport_scheme", "https"),
        ("transport_host", "localhost"),
        ("transport_port", 9001),
        ("transport_endpoint", "/other"),
    ],
)
def test_no_route_identity_field_can_silently_change(field, value):
    from marianne.instruments import loader

    assert loader.route_identity(_binding()) != loader.route_identity(_binding(**{field: value}))


def test_binding_rejects_naive_clock_unknown_fields_and_mutation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _binding(resolved_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        _binding(extra_field="not allowed")
    binding = _binding()
    with pytest.raises(ValidationError):
        binding.effective_model = "other"


def test_expected_route_survives_public_wire_and_checkpoint_roundtrips():
    from marianne.core.checkpoint import CheckpointState, SheetState
    from marianne.daemon.types import JobRequest

    binding = _binding()
    request = JobRequest(config_path="score.yaml", expected_route=binding)
    assert JobRequest.model_validate_json(request.model_dump_json()).expected_route == binding
    sheet = SheetState(sheet_num=1, expected_route=binding)
    state = CheckpointState(
        job_id="bound", job_name="bound", total_sheets=1, sheets={1: sheet}, expected_route=binding
    )
    restored = CheckpointState.model_validate_json(state.model_dump_json())
    assert restored.expected_route == binding
    assert restored.sheets[1].expected_route == binding
    assert JobRequest(config_path="score.yaml").expected_route is None
    assert SheetState(sheet_num=1).expected_route is None


@pytest.mark.parametrize(
    "alternate",
    [
        {"instrument_fallbacks": ["other"]},
        {"sheet": {"size": 1, "total_items": 1, "per_sheet_instruments": {1: "other"}}},
    ],
)
async def test_manager_refuses_alternate_route_before_adapter_or_workspace(alternate):
    from marianne.core.config import JobConfig
    from marianne.daemon.manager import JobManager
    from marianne.daemon.types import JobRequest

    config = JobConfig.model_validate(
        {
            "name": "guard",
            "instrument": "local",
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "fictional"},
        }
        | alternate
    )
    manager = object.__new__(JobManager)
    with pytest.raises(ValueError, match="LOCAL_TRANSPORT"):
        await manager._run_via_baton(
            "guard", config, JobRequest(config_path="score.yaml", expected_route=_binding())
        )


async def test_actual_manager_retains_binding_and_registry_status_retains_echo(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

    from marianne.core.checkpoint import JobStatus, SheetStatus
    from marianne.core.config import JobConfig
    from marianne.daemon.config import DaemonConfig
    from marianne.daemon.manager import JobManager
    from marianne.daemon.types import JobRequest

    binding = _binding()
    config = JobConfig.model_validate(
        {
            "name": "guard",
            "instrument": "local",
            "workspace": str(tmp_path / "ws"),
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "fictional"},
            "spec": {"spec_dir": ""},
        }
    )
    manager = JobManager(DaemonConfig(state_db_path=tmp_path / "registry.db"))
    adapter = MagicMock()
    adapter.publish_job_event = AsyncMock()
    adapter.wait_for_completion = AsyncMock(return_value=True)
    manager._baton_adapter = adapter
    manager._set_job_status = AsyncMock()
    await manager._run_via_baton(
        "guard", config, JobRequest(config_path="score.yaml", expected_route=binding)
    )
    state = manager._live_states["guard"]
    assert state.expected_route == binding
    assert adapter.register_job.call_args.kwargs["live_sheets"][1].expected_route == binding
    state.status = JobStatus.COMPLETED
    state.sheets[1].status = SheetStatus.COMPLETED
    state.sheets[1].model_echo_status = "observed"
    state.sheets[1].model_requested = "requested"
    state.sheets[1].model_observed = "requested"
    await manager._registry.open()
    try:
        await manager._registry.register_job("guard", tmp_path / "score.yaml", tmp_path / "ws")
        await manager._registry.save_checkpoint("guard", state.model_dump_json())
        await manager._registry.update_status("guard", "completed")
        manager._live_states.clear()
        public = await manager.get_job_status("guard")
        assert public["status"] == "completed"
        assert public["completed_at"] is not None
        assert public["sheets"]["1"]["model_echo_status"] == "observed"
        assert public["sheets"]["1"]["model_requested"] == "requested"
        assert public["sheets"]["1"]["model_observed"] == "requested"
        assert public["expected_route"] == binding.model_dump(mode="json")
    finally:
        await manager._registry.close()


def test_capture_uses_winning_profile_bytes_and_actual_score_override(tmp_path):
    from marianne.instruments import loader

    org, venue = tmp_path / "org", tmp_path / "venue"
    org.mkdir()
    venue.mkdir()
    template = (
        "name: local\ndisplay_name: Local\nkind: http\ndefault_model: {}\n"
        "http:\n  base_url: http://127.0.0.1:9000/v1\n  schema_family: openai\n"
        "  endpoint: /chat/completions\n"
    )
    (org / "local.yaml").write_text(template.format("org-default"))
    winning = venue / "override.yaml"
    winning.write_bytes(template.format("venue-default").replace("\n", "\r\n").encode())
    score = tmp_path / "score.yaml"
    score.write_text("name: binding\ninstrument: local\ninstrument_config:\n  model: override\n")
    digest = hashlib.sha256(score.read_bytes()).hexdigest()
    binding = loader.capture_instrument_route_binding(
        score,
        "local",
        digest,
        now=datetime(2026, 1, 1, tzinfo=UTC),
        organization_dir=org,
        venue_dir=venue,
    )
    assert binding.profile_origin == "venue"
    assert binding.profile_file_sha256 == hashlib.sha256(winning.read_bytes()).hexdigest()
    assert binding.effective_model == "override"
    assert binding.model_source == "score_override"
    assert binding.effective_provider is None
    assert binding.transport_host == "127.0.0.1"
    assert binding.transport_port == 9000
    assert binding.transport_endpoint == "/chat/completions"
    winning.unlink()
    changed = loader.capture_instrument_route_binding(
        score,
        "local",
        digest,
        now=datetime(2026, 1, 1, tzinfo=UTC),
        organization_dir=org,
        venue_dir=venue,
    )
    assert changed.profile_origin == "organization"
    assert loader.route_identity(changed) != loader.route_identity(binding)


@pytest.mark.parametrize("mutation", ["remote", "credentials", "endpoint", "model", "score"])
def test_local_capture_refuses_unproven_route_or_changed_score(tmp_path, mutation):
    from marianne.instruments import loader

    venue = tmp_path / "venue"
    venue.mkdir()
    base = "http://127.0.0.1:9000/v1"
    if mutation == "remote":
        base = "https://example.com/v1"
    elif mutation == "credentials":
        base = "http://user:secret@127.0.0.1:9000/v1"
    endpoint = "https://example.com/chat" if mutation == "endpoint" else "/chat/completions"
    model = "" if mutation == "model" else "default_model: local\n"
    (venue / "local.yaml").write_text(
        f"name: local\ndisplay_name: Local\nkind: http\n{model}"
        f"http:\n  base_url: {base}\n  endpoint: {endpoint}\n  schema_family: openai\n"
    )
    score = tmp_path / "score.yaml"
    score.write_text("name: binding\ninstrument: local\n")
    digest = "a" * 64 if mutation == "score" else hashlib.sha256(score.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        loader.capture_instrument_route_binding(
            score,
            "local",
            digest,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            organization_dir=tmp_path / "org",
            venue_dir=venue,
        )
