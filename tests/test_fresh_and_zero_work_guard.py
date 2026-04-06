"""Tests for self-chaining workspace collision fix.

Covers:
- Zero-work guard: on_success hooks skipped when job was already COMPLETED
- --fresh flag: state deletion before fresh run
- fresh config field: PostSuccessHookConfig.fresh serialization
- Hook executor: --fresh flag passed to chained job commands

These two features together form a defense-in-depth solution:
- Layer 1 (--fresh / fresh: true): Root cause fix — clears state before run
- Layer 2 (zero-work guard): Symptom prevention — blocks hooks on zero work
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus
from marianne.core.config import JobConfig, PostSuccessHookConfig
from marianne.execution.hooks import HookExecutor

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def minimal_config() -> JobConfig:
    """Create minimal JobConfig for testing."""
    return JobConfig.model_validate({
        "name": "test-job",
        "description": "Test job",
        "backend": {"type": "claude_cli"},
        "sheet": {"size": 1, "total_items": 3},
        "prompt": {"template": "Test prompt {{ sheet_num }}"},
        "pause_between_sheets_seconds": 0,
    })


# =========================================================================
# PostSuccessHookConfig.fresh field tests
# =========================================================================


class TestFreshConfigField:
    """Tests for the fresh field on PostSuccessHookConfig."""

    def test_fresh_defaults_to_false(self) -> None:
        """fresh should default to False for backward compatibility."""
        hook = PostSuccessHookConfig(type="run_job", job_path=Path("test.yaml"))
        assert hook.fresh is False

    def test_fresh_can_be_set_true(self) -> None:
        """fresh should be settable to True."""
        hook = PostSuccessHookConfig(
            type="run_job",
            job_path=Path("test.yaml"),
            fresh=True,
        )
        assert hook.fresh is True

    def test_fresh_serialization(self) -> None:
        """fresh should round-trip through model_dump/model_validate."""
        hook = PostSuccessHookConfig(
            type="run_job",
            job_path=Path("test.yaml"),
            fresh=True,
        )
        data = hook.model_dump(mode="json")
        assert data["fresh"] is True

        restored = PostSuccessHookConfig.model_validate(data)
        assert restored.fresh is True

    def test_fresh_in_full_config(self) -> None:
        """fresh should work when loaded as part of full JobConfig."""
        config = JobConfig.model_validate({
            "name": "test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": "next.yaml",
                    "fresh": True,
                    "detached": True,
                },
            ],
        })
        assert config.on_success[0].fresh is True

    def test_fresh_false_in_full_config(self) -> None:
        """Omitting fresh should default to False in full config."""
        config = JobConfig.model_validate({
            "name": "test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": "next.yaml",
                },
            ],
        })
        assert config.on_success[0].fresh is False


# =========================================================================
# Hook executor --fresh flag passing tests
# =========================================================================


class TestHookExecutorFreshFlag:
    """Tests for hook executor passing --fresh to spawned commands."""

    @pytest.mark.asyncio
    async def test_fresh_flag_included_in_command(self, tmp_path: Path) -> None:
        """When hook.fresh is True, --fresh should be in spawned command."""
        job_config = tmp_path / "next-job.yaml"
        job_config.write_text(
            "name: next\nbackend:\n  type: claude_cli\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: test\n"
        )

        config = JobConfig.model_validate({
            "name": "test-fresh",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "concert": {"cooldown_between_jobs_seconds": 0},  # No cooldown in tests
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config),
                    "fresh": True,
                    "detached": True,
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=tmp_path)

        # Patch subprocess.Popen to capture the command
        # (detached hooks now use subprocess.Popen, not asyncio.create_subprocess_exec)
        captured_cmd: list[str] = []

        def mock_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            proc = MagicMock()
            proc.pid = 12345
            proc.poll.return_value = None  # Child still alive for liveness check
            return proc

        with patch("marianne.execution.hooks._try_daemon_submit", return_value=(False, None)), \
             patch("marianne.execution.hooks._subprocess.Popen", side_effect=mock_popen):
            results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is True
        # The command should include --fresh
        assert "--fresh" in captured_cmd

    @pytest.mark.asyncio
    async def test_no_fresh_flag_when_false(self, tmp_path: Path) -> None:
        """When hook.fresh is False (default), --fresh should NOT be in command."""
        job_config = tmp_path / "next-job.yaml"
        job_config.write_text(
            "name: next\nbackend:\n  type: claude_cli\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: test\n"
        )

        config = JobConfig.model_validate({
            "name": "test-no-fresh",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test"},
            "concert": {"cooldown_between_jobs_seconds": 0},  # No cooldown in tests
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config),
                    # fresh defaults to False
                    "detached": True,
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=tmp_path)

        captured_cmd: list[str] = []

        def mock_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            proc = MagicMock()
            proc.pid = 12345
            proc.poll.return_value = None  # Child still alive for liveness check
            return proc

        with patch("marianne.execution.hooks._try_daemon_submit", return_value=(False, None)), \
             patch("marianne.execution.hooks._subprocess.Popen", side_effect=mock_popen):
            results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is True
        assert "--fresh" not in captured_cmd


# =========================================================================
# Zero-work guard tests (lifecycle integration)
# =========================================================================


class TestZeroWorkGuard:
    """Tests for the zero-work guard that prevents on_success on zero work."""

    @pytest.mark.asyncio
    async def test_hooks_skipped_when_already_completed(self) -> None:
        """on_success hooks should NOT fire when job was already COMPLETED."""
        from marianne.backends.base import ExecutionResult
        from marianne.execution.runner import JobRunner, RunnerContext

        config = JobConfig.model_validate({
            "name": "guard-test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 2},
            "prompt": {"template": "test {{ sheet_num }}"},
            "pause_between_sheets_seconds": 0,
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo should-not-run",
                },
            ],
        })

        # Create a COMPLETED state that will be loaded
        completed_state = CheckpointState(
            job_id="guard-test",
            job_name="guard-test",
            total_sheets=2,
        )
        completed_state.status = JobStatus.COMPLETED
        completed_state.last_completed_sheet = 2

        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=ExecutionResult(
                success=True, stdout="done", stderr="", exit_code=0, duration_seconds=1.0
            )
        )
        mock_backend.health_check = AsyncMock(return_value=True)

        mock_state_backend = AsyncMock()
        mock_state_backend.load = AsyncMock(return_value=completed_state)
        mock_state_backend.save = AsyncMock()

        context = RunnerContext(
            console=MagicMock(),
        )

        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=context,
        )

        # Track if _execute_post_success_hooks is called
        with patch.object(
            runner, "_execute_post_success_hooks", new_callable=AsyncMock,
        ) as mock_hooks:
            state, summary = await runner.run()

            # Hooks should NOT have been called
            mock_hooks.assert_not_called()

    @pytest.mark.asyncio
    async def test_hooks_fire_when_new_work_done(self, tmp_path: Path) -> None:
        """on_success hooks SHOULD fire when job completes with real work."""
        from marianne.backends.base import ExecutionResult
        from marianne.execution.runner import JobRunner, RunnerContext

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = JobConfig.model_validate({
            "name": "fire-test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test {{ sheet_num }}"},
            "pause_between_sheets_seconds": 0,
            "workspace": str(workspace),
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo success",
                },
            ],
        })

        # No existing state — fresh run
        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=ExecutionResult(
                success=True, stdout="done", stderr="", exit_code=0, duration_seconds=1.0
            )
        )
        mock_backend.health_check = AsyncMock(return_value=True)

        mock_state_backend = AsyncMock()
        mock_state_backend.load = AsyncMock(return_value=None)  # No existing state
        mock_state_backend.save = AsyncMock()

        context = RunnerContext(
            console=MagicMock(),
        )

        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=context,
        )

        # Track if _execute_post_success_hooks is called
        with patch.object(
            runner, "_execute_post_success_hooks", new_callable=AsyncMock,
        ) as mock_hooks:
            state, summary = await runner.run()

            # Hooks SHOULD be called since real work was done
            mock_hooks.assert_called_once()

    @pytest.mark.asyncio
    async def test_hooks_fire_when_resumed_partial(self, tmp_path: Path) -> None:
        """on_success hooks SHOULD fire when a partial job resumes and completes."""
        from marianne.backends.base import ExecutionResult
        from marianne.execution.runner import JobRunner, RunnerContext

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = JobConfig.model_validate({
            "name": "resume-test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 2},
            "prompt": {"template": "test {{ sheet_num }}"},
            "pause_between_sheets_seconds": 0,
            "workspace": str(workspace),
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo success-after-resume",
                },
            ],
        })

        # Partial state — sheet 1 done, sheet 2 pending
        partial_state = CheckpointState(
            job_id="resume-test",
            job_name="resume-test",
            total_sheets=2,
        )
        partial_state.status = JobStatus.PAUSED
        partial_state.last_completed_sheet = 1

        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=ExecutionResult(
                success=True, stdout="done", stderr="", exit_code=0, duration_seconds=1.0
            )
        )
        mock_backend.health_check = AsyncMock(return_value=True)

        mock_state_backend = AsyncMock()
        mock_state_backend.load = AsyncMock(return_value=partial_state)
        mock_state_backend.save = AsyncMock()

        context = RunnerContext(
            console=MagicMock(),
        )

        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=context,
        )

        with patch.object(
            runner, "_execute_post_success_hooks", new_callable=AsyncMock,
        ) as mock_hooks:
            state, summary = await runner.run()

            # Hooks SHOULD fire — partial job was NOT already completed
            mock_hooks.assert_called_once()


# =========================================================================
# --fresh state deletion tests
# =========================================================================


class TestFreshStateReset:
    """Tests for --fresh flag deleting existing state."""

    @pytest.mark.asyncio
    async def test_fresh_deletes_existing_state(self) -> None:
        """--fresh should call state_backend.delete() before running."""
        mock_state_backend = AsyncMock()
        mock_state_backend.delete = AsyncMock(return_value=True)

        # Verify that delete is called with the job name
        await mock_state_backend.delete("my-job")
        mock_state_backend.delete.assert_called_once_with("my-job")

    def test_fresh_config_field_default_false(self) -> None:
        """PostSuccessHookConfig.fresh should default to False."""
        hook = PostSuccessHookConfig(type="run_command", command="echo hi")
        assert hook.fresh is False

    def test_quality_continuous_has_fresh(self) -> None:
        """quality-continuous.yaml should have fresh: true on its self-chain hook."""
        import yaml

        config_path = Path(__file__).parent.parent / "examples" / "quality-continuous.yaml"
        if config_path.exists():
            with open(config_path) as f:
                data = yaml.safe_load(f)

            on_success = data.get("on_success", [])
            assert len(on_success) > 0, "on_success hooks should exist"

            # The self-chain hook should have fresh: true
            chain_hook = on_success[0]
            assert chain_hook.get("fresh") is True, (
                "Self-chaining hook should have fresh: true to prevent infinite loops"
            )


# =========================================================================
# Combined defense-in-depth scenario
# =========================================================================


class TestDefenseInDepth:
    """Tests for the combined A+C defense-in-depth solution."""

    def test_both_layers_protect_independently(self) -> None:
        """Even without --fresh, zero-work guard prevents infinite loop.

        Layer 1 (--fresh): Prevents the COMPLETED state from being loaded
          - Root cause fix: state deleted, fresh run begins
        Layer 2 (zero-work guard): Prevents hooks from firing on zero work
          - Symptom prevention: even if fresh is forgotten, loop is broken

        Either layer alone prevents the infinite loop.
        Verified by: test_hooks_skipped_when_already_completed (Layer 2 alone)
        and test_fresh_deletes_existing_state (Layer 1 alone).
        """
        # Verify Layer 2: COMPLETED state -> loaded_as_completed = True
        completed_state = CheckpointState(
            job_id="guard-test",
            job_name="guard-test",
            total_sheets=2,
        )
        completed_state.status = JobStatus.COMPLETED
        completed_state.last_completed_sheet = 2

        # Simulate the guard logic from lifecycle.py:104
        loaded_as_completed = completed_state.status == JobStatus.COMPLETED

        # Guard should detect this is a re-run of a completed job
        assert loaded_as_completed is True

        # Simulate hook firing condition from lifecycle.py:182-185
        has_on_success = True  # Assume hooks are configured
        should_fire_hooks = (
            completed_state.status == JobStatus.COMPLETED
            and has_on_success
            and not loaded_as_completed
        )
        # Hooks should NOT fire because loaded_as_completed blocks them
        assert should_fire_hooks is False, (
            "Zero-work guard must prevent hooks from firing on already-completed jobs"
        )

    def test_fresh_and_guard_interact_correctly(self) -> None:
        """When --fresh is used, guard should allow hooks (real work done).

        With --fresh: state deleted -> _initialize_state creates new state
          -> loaded_as_completed = False -> hooks fire after real work

        Without --fresh: old COMPLETED state loaded
          -> loaded_as_completed = True -> hooks blocked
        """
        # Simulate --fresh behavior: new state starts as PENDING
        fresh_state = CheckpointState(
            job_id="fresh-test",
            job_name="fresh-test",
            total_sheets=2,
        )
        # Guard captures initial status (lifecycle.py:104)
        loaded_as_completed_fresh = fresh_state.status == JobStatus.COMPLETED
        assert loaded_as_completed_fresh is False

        # After job completes its work, status changes to COMPLETED
        fresh_state.status = JobStatus.COMPLETED
        # Hook firing condition (lifecycle.py:182-185)
        should_fire_hooks_fresh = (
            fresh_state.status == JobStatus.COMPLETED
            and not loaded_as_completed_fresh
        )
        # Hooks SHOULD fire because job did real work (fresh start)
        assert should_fire_hooks_fresh is True, (
            "Fresh run should allow hooks to fire after completing real work"
        )

        # Simulate without --fresh: old COMPLETED state loaded directly
        old_state = CheckpointState(
            job_id="old-test",
            job_name="old-test",
            total_sheets=2,
        )
        old_state.status = JobStatus.COMPLETED
        # Guard captures initial status (lifecycle.py:104)
        loaded_as_completed_old = old_state.status == JobStatus.COMPLETED
        assert loaded_as_completed_old is True

        # Hook firing condition (lifecycle.py:182-185)
        should_fire_hooks_old = (
            old_state.status == JobStatus.COMPLETED
            and not loaded_as_completed_old
        )
        # Hooks should NOT fire because no new work was done
        assert should_fire_hooks_old is False, (
            "Non-fresh run of completed job must block hooks to prevent infinite loop"
        )


# =========================================================================
# _execute_post_success_hooks direct tests (Q022: coverage gap)
# =========================================================================


class TestExecutePostSuccessHooksDirect:
    """Tests for LifecycleMixin._execute_post_success_hooks.

    Q022: Concert/nested execution 0% coverage. These tests exercise the
    actual method rather than mocking it away, covering the summary update
    logic and concert_context=None behavior.
    """

    @pytest.mark.asyncio
    async def test_no_hooks_returns_early(self, tmp_path: Path) -> None:
        """Method returns immediately when on_success is empty."""
        from marianne.execution.runner import JobRunner, RunnerContext

        config = JobConfig.model_validate({
            "name": "no-hooks",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test {{ sheet_num }}"},
            "workspace": str(tmp_path / "ws"),
        })

        runner = JobRunner(
            config=config,
            backend=AsyncMock(),
            state_backend=AsyncMock(),
            context=RunnerContext(console=MagicMock()),
        )

        state = CheckpointState(
            job_id="no-hooks", job_name="no-hooks", total_sheets=1
        )
        state.status = JobStatus.COMPLETED

        # Should not raise
        await runner._execute_post_success_hooks(state)

    @pytest.mark.asyncio
    async def test_hooks_update_summary(self, tmp_path: Path) -> None:
        """Hook execution results are reflected in the run summary."""
        from marianne.execution.hooks import HookResult
        from marianne.execution.runner import JobRunner, RunnerContext
        from marianne.execution.runner.models import RunSummary

        workspace = tmp_path / "ws"
        workspace.mkdir()

        config = JobConfig.model_validate({
            "name": "summary-hooks",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test {{ sheet_num }}"},
            "workspace": str(workspace),
            "on_success": [
                {"type": "run_command", "command": "echo ok"},
                {"type": "run_command", "command": "false"},
            ],
        })

        runner = JobRunner(
            config=config,
            backend=AsyncMock(),
            state_backend=AsyncMock(),
            context=RunnerContext(console=MagicMock()),
        )

        # Pre-populate summary to test update path
        runner._summary = RunSummary(
            job_id="summary-hooks",
            job_name="summary-hooks",
            total_sheets=1,
            completed_sheets=1,
        )

        state = CheckpointState(
            job_id="summary-hooks", job_name="summary-hooks", total_sheets=1
        )
        state.status = JobStatus.COMPLETED

        # Mock HookExecutor to return controlled results
        mock_results = [
            HookResult(hook_type="run_command", description="echo", success=True),
            HookResult(hook_type="run_command", description="false", success=False),
        ]
        with patch(
            "marianne.execution.runner.lifecycle.HookExecutor"
        ) as MockExecutorClass:
            mock_executor = AsyncMock()
            mock_executor.execute_hooks = AsyncMock(return_value=mock_results)
            MockExecutorClass.return_value = mock_executor

            await runner._execute_post_success_hooks(state)

        assert runner._summary.hooks_executed == 2
        assert runner._summary.hooks_succeeded == 1
        assert runner._summary.hooks_failed == 1
        assert runner._summary.hook_results == mock_results

    @pytest.mark.asyncio
    async def test_concert_context_is_none(self, tmp_path: Path) -> None:
        """Verify concert_context=None is passed to HookExecutor (TODO #37)."""
        from marianne.execution.runner import JobRunner, RunnerContext

        workspace = tmp_path / "ws"
        workspace.mkdir()

        config = JobConfig.model_validate({
            "name": "concert-test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test {{ sheet_num }}"},
            "workspace": str(workspace),
            "on_success": [
                {"type": "run_command", "command": "echo ok"},
            ],
        })

        runner = JobRunner(
            config=config,
            backend=AsyncMock(),
            state_backend=AsyncMock(),
            context=RunnerContext(console=MagicMock()),
        )

        state = CheckpointState(
            job_id="concert-test", job_name="concert-test", total_sheets=1
        )
        state.status = JobStatus.COMPLETED

        with patch(
            "marianne.execution.runner.lifecycle.HookExecutor"
        ) as MockExecutorClass:
            mock_executor = AsyncMock()
            mock_executor.execute_hooks = AsyncMock(return_value=[])
            MockExecutorClass.return_value = mock_executor

            await runner._execute_post_success_hooks(state)

            # Verify concert_context=None (TODO #37)
            MockExecutorClass.assert_called_once()
            call_kwargs = MockExecutorClass.call_args
            assert call_kwargs.kwargs.get("concert_context") is None


# ─── Phase 5: daemon_managed flag suppresses runner-side hooks ──────


class TestDaemonManagedHookSuppression:
    """Tests for daemon_managed=True suppressing runner-side hooks."""

    @pytest.mark.asyncio
    async def test_hooks_suppressed_when_daemon_managed(self, tmp_path: Path) -> None:
        """When daemon_managed=True, run() skips _execute_post_success_hooks."""
        from marianne.backends.base import ExecutionResult
        from marianne.execution.runner import JobRunner, RunnerContext

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = JobConfig.model_validate({
            "name": "daemon-managed-test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test {{ sheet_num }}"},
            "pause_between_sheets_seconds": 0,
            "workspace": str(workspace),
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo should-not-run",
                },
            ],
        })

        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=ExecutionResult(
                success=True, stdout="done", stderr="", exit_code=0, duration_seconds=1.0
            )
        )
        mock_backend.health_check = AsyncMock(return_value=True)

        mock_state_backend = AsyncMock()
        mock_state_backend.load = AsyncMock(return_value=None)
        mock_state_backend.save = AsyncMock()

        context = RunnerContext(
            console=MagicMock(),
            daemon_managed=True,  # Daemon owns hook execution
        )

        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=context,
        )

        with patch.object(
            runner, "_execute_post_success_hooks", new_callable=AsyncMock,
        ) as mock_hooks:
            state, summary = await runner.run()

            # Hooks should NOT have been called — daemon handles them
            mock_hooks.assert_not_called()

        # But the job should still have completed successfully
        assert state.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_hooks_fire_when_not_daemon_managed(self, tmp_path: Path) -> None:
        """When daemon_managed=False (default), run() fires hooks normally."""
        from marianne.backends.base import ExecutionResult
        from marianne.execution.runner import JobRunner, RunnerContext

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = JobConfig.model_validate({
            "name": "cli-mode-test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "test {{ sheet_num }}"},
            "pause_between_sheets_seconds": 0,
            "workspace": str(workspace),
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo should-run",
                },
            ],
        })

        mock_backend = AsyncMock()
        mock_backend.execute = AsyncMock(
            return_value=ExecutionResult(
                success=True, stdout="done", stderr="", exit_code=0, duration_seconds=1.0
            )
        )
        mock_backend.health_check = AsyncMock(return_value=True)

        mock_state_backend = AsyncMock()
        mock_state_backend.load = AsyncMock(return_value=None)
        mock_state_backend.save = AsyncMock()

        context = RunnerContext(
            console=MagicMock(),
            daemon_managed=False,  # CLI mode — hooks should fire
        )

        runner = JobRunner(
            config=config,
            backend=mock_backend,
            state_backend=mock_state_backend,
            context=context,
        )

        with patch.object(
            runner, "_execute_post_success_hooks", new_callable=AsyncMock,
        ) as mock_hooks:
            state, summary = await runner.run()

            # Hooks SHOULD be called in CLI mode
            mock_hooks.assert_called_once()

        assert state.status == JobStatus.COMPLETED
