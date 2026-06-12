"""#361 (decouple): escalation works without --self-healing.

Before this change escalation was conjoined with healing at the submit
path — ``escalation_enabled=request.self_healing`` (manager.py) — so
FERMATA-on-exhaustion only existed for jobs that ALSO ran the healing
pipeline (escalation fired only after healing burned its budget). The
``--escalation`` CLI flag existed but was hard-rejected with a stale
"requires interactive console prompts" error that predates the #361
marker-file resolution (escalation is non-interactive now).

This change:

- ``JobRequest.escalation`` (additive IPC field, default False);
- submit wiring: ``escalation_enabled = request.escalation or
  request.self_healing`` — an escalation-only job enters FERMATA directly
  on exhaustion without burning healing budget; healing keeps FERMATA as
  its designed end state;
- run-option durability: ``CheckpointState.escalation_enabled`` /
  ``self_healing_enabled`` persist the effective values so resume and
  conductor-restart recovery inherit them instead of silently dropping
  to False (the previous behavior lost BOTH flags on every resume);
- resume threading: ``mzt resume --escalation/--self-healing`` reach the
  conductor (previously collected and dropped client-side) and OR into
  the inherited checkpoint values;
- the stale CLI rejection is removed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState
from marianne.core.sheet import Sheet
from marianne.daemon.types import JobRequest

# ── Harness (mirrors tests/test_baton_activation_fixes.py) ──────────────


def _make_sheet(num: int = 1) -> Sheet:
    return Sheet(
        num=num,
        movement=1,
        voice_count=1,
        instrument_name="claude-code",
        workspace=Path("/tmp/test-ws"),
        prompt_template="test prompt",
    )


def _make_mock_manager() -> MagicMock:
    from marianne.daemon.baton.adapter import BatonAdapter
    from marianne.daemon.manager import JobManager

    manager = MagicMock()
    manager._baton_adapter = BatonAdapter()
    manager._job_meta = {}
    manager._config_name_to_conductor_id = {}
    manager._config = MagicMock()
    manager._config.default_thinking_method = None

    manager._run_via_baton = JobManager._run_via_baton.__get__(manager)
    manager._resume_via_baton = JobManager._resume_via_baton.__get__(manager)
    manager._set_job_status = JobManager._set_job_status.__get__(manager)
    manager._load_spec_corpus = JobManager._load_spec_corpus
    manager._archive_workspace_on_fresh = JobManager._archive_workspace_on_fresh

    manager._registry = MagicMock()
    manager._registry.update_status = AsyncMock()
    manager._registry.save_checkpoint = AsyncMock()
    return manager


def _make_mock_config() -> MagicMock:
    from marianne.core.config.job import PromptConfig
    from marianne.core.config.spec import SpecCorpusConfig

    config = MagicMock()
    config.name = "test-job"
    config.backend.type = "claude_cli"
    config.retry.max_retries = 3
    config.cost_limits.enabled = False
    config.cost_limits.max_cost_per_job = None
    config.instrument = "claude-code"
    config.workspace = Path("/tmp/ws")
    config.prompt = PromptConfig(template="test prompt")
    config.spec = SpecCorpusConfig(spec_dir="")
    config.sheet.spec_tags = {}
    config.parallel.enabled = False
    return config


def _make_mock_request(
    *, escalation: bool = False, self_healing: bool = False
) -> MagicMock:
    request = MagicMock()
    request.workspace = None
    request.fresh = False
    request.start_sheet = None
    request.escalation = escalation
    request.self_healing = self_healing
    request.self_healing_auto_confirm = False
    request.dry_run = False
    return request


def _meta(job_id: str) -> object:
    from marianne.daemon.manager import DaemonJobStatus, JobMeta

    return JobMeta(
        job_id=job_id,
        config_path=Path("/tmp/test.yaml"),
        workspace=Path("/tmp/ws"),
        status=DaemonJobStatus.RUNNING,
    )


async def _run_submit(manager: MagicMock, request: MagicMock) -> None:
    adapter = manager._baton_adapter
    adapter.wait_for_completion = AsyncMock(return_value=True)
    adapter.register_job = MagicMock()
    adapter.publish_job_event = AsyncMock()
    adapter.has_completed_sheets = MagicMock(return_value=True)
    manager._job_meta["test-job"] = _meta("test-job")

    with (
        patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]),
        patch(
            "marianne.daemon.baton.adapter.extract_dependencies",
            return_value={1: []},
        ),
    ):
        await manager._run_via_baton("test-job", _make_mock_config(), request)


# ── IPC model ────────────────────────────────────────────────────────────


class TestJobRequestField:
    def test_escalation_defaults_false(self) -> None:
        request = JobRequest(config_path=Path("/tmp/x.yaml"))
        assert request.escalation is False

    def test_escalation_parses(self) -> None:
        request = JobRequest(config_path=Path("/tmp/x.yaml"), escalation=True)
        assert request.escalation is True


# ── Run-option durability on CheckpointState ─────────────────────────────


class TestCheckpointRunOptions:
    def test_fields_default_false_for_old_checkpoints(self) -> None:
        state = CheckpointState(job_id="j1", job_name="n", total_sheets=1)
        assert state.escalation_enabled is False
        assert state.self_healing_enabled is False


# ── Submit wiring ────────────────────────────────────────────────────────


class TestSubmitWiring:
    @pytest.mark.asyncio
    async def test_escalation_only_enables_fermata_without_healing(self) -> None:
        manager = _make_mock_manager()
        await _run_submit(
            manager, _make_mock_request(escalation=True, self_healing=False)
        )

        kwargs = manager._baton_adapter.register_job.call_args.kwargs
        assert kwargs["escalation_enabled"] is True
        assert kwargs["self_healing_enabled"] is False

    @pytest.mark.asyncio
    async def test_self_healing_still_implies_escalation(self) -> None:
        """Healing keeps FERMATA as its designed end state (no regression)."""
        manager = _make_mock_manager()
        await _run_submit(
            manager, _make_mock_request(escalation=False, self_healing=True)
        )

        kwargs = manager._baton_adapter.register_job.call_args.kwargs
        assert kwargs["escalation_enabled"] is True
        assert kwargs["self_healing_enabled"] is True

    @pytest.mark.asyncio
    async def test_neither_flag_disables_both(self) -> None:
        manager = _make_mock_manager()
        await _run_submit(manager, _make_mock_request())

        kwargs = manager._baton_adapter.register_job.call_args.kwargs
        assert kwargs["escalation_enabled"] is False
        assert kwargs["self_healing_enabled"] is False

    @pytest.mark.asyncio
    async def test_effective_values_persist_on_initial_state(self) -> None:
        """Restart recovery reads the checkpoint — the submit path must
        record the effective values there or restart silently drops them."""
        manager = _make_mock_manager()
        manager._live_states = {}
        await _run_submit(
            manager, _make_mock_request(escalation=True, self_healing=False)
        )

        state = manager._live_states["test-job"]
        assert state.escalation_enabled is True
        assert state.self_healing_enabled is False


# ── Resume inheritance ───────────────────────────────────────────────────


def _resume_manager_with_checkpoint(
    *, escalation_enabled: bool, self_healing_enabled: bool
) -> tuple[MagicMock, MagicMock]:
    manager = _make_mock_manager()
    adapter = manager._baton_adapter
    adapter.wait_for_completion = AsyncMock(return_value=True)
    adapter.recover_job = MagicMock()
    adapter.publish_job_event = AsyncMock()
    adapter.has_completed_sheets = MagicMock(return_value=True)
    manager._job_meta["resume-job"] = _meta("resume-job")

    checkpoint = MagicMock()
    checkpoint.sheets = {}
    checkpoint.escalation_enabled = escalation_enabled
    checkpoint.self_healing_enabled = self_healing_enabled
    # #359: a real CheckpointState defaults this to {}; the mock must too,
    # or the resume re-merge sees a truthy MagicMock and rewrites config.
    checkpoint.runtime_variables = {}
    manager._load_checkpoint = AsyncMock(return_value=checkpoint)
    return manager, adapter


async def _run_resume(manager: MagicMock, **kwargs: object) -> None:
    mock_config = _make_mock_config()
    with (
        patch("marianne.core.config.JobConfig") as MockJobConfig,
        patch("marianne.core.sheet.build_sheets", return_value=[_make_sheet()]),
        patch(
            "marianne.daemon.baton.adapter.extract_dependencies",
            return_value={1: []},
        ),
    ):
        MockJobConfig.from_yaml.return_value = mock_config
        await manager._resume_via_baton("resume-job", Path("/tmp/ws"), **kwargs)


class TestResumeInheritance:
    @pytest.mark.asyncio
    async def test_resume_inherits_checkpoint_values(self) -> None:
        manager, adapter = _resume_manager_with_checkpoint(
            escalation_enabled=True, self_healing_enabled=True
        )
        await _run_resume(manager)

        kwargs = adapter.recover_job.call_args.kwargs
        assert kwargs["escalation_enabled"] is True
        assert kwargs["self_healing_enabled"] is True

    @pytest.mark.asyncio
    async def test_resume_flag_ors_into_inherited_false(self) -> None:
        manager, adapter = _resume_manager_with_checkpoint(
            escalation_enabled=False, self_healing_enabled=False
        )
        await _run_resume(manager, escalation=True)

        kwargs = adapter.recover_job.call_args.kwargs
        assert kwargs["escalation_enabled"] is True
        assert kwargs["self_healing_enabled"] is False

    @pytest.mark.asyncio
    async def test_resume_without_flags_preserves_disabled(self) -> None:
        manager, adapter = _resume_manager_with_checkpoint(
            escalation_enabled=False, self_healing_enabled=False
        )
        await _run_resume(manager)

        kwargs = adapter.recover_job.call_args.kwargs
        assert kwargs["escalation_enabled"] is False
        assert kwargs["self_healing_enabled"] is False
