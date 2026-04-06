"""Tests for post-success hook execution.

Covers:
- HookResult dataclass
- ConcertContext tracking
- HookExecutor template expansion
- Hook type dispatch
- Basic hook execution flow
- Detached child process survival (Popen vs asyncio transport)
"""

import os
import signal
import subprocess as _subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig, PostSuccessHookConfig
from marianne.execution.hooks import (
    ConcertContext,
    HookExecutor,
    HookResult,
    expand_hook_variables,
)


class TestExpandHookVariablesShellSafety:
    """Tests for expand_hook_variables for_shell parameter (F-020 regression)."""

    def test_for_shell_quotes_spaces(self) -> None:
        """Workspace with spaces must be quoted for shell safety."""
        result = expand_hook_variables(
            "ls {workspace}",
            workspace="/path with spaces",
            job_id="job-1",
            for_shell=True,
        )
        assert "'/path with spaces'" in result

    def test_for_shell_quotes_dollar_sign(self) -> None:
        """$() command substitution in workspace must be neutralized."""
        result = expand_hook_variables(
            "echo {workspace}",
            workspace="/tmp/$(whoami)",
            job_id="job-1",
            for_shell=True,
        )
        # shlex.quote wraps in single quotes, preventing $() expansion
        assert "'/tmp/$(whoami)'" in result

    def test_for_shell_quotes_backticks(self) -> None:
        """Backtick command substitution must be neutralized."""
        result = expand_hook_variables(
            "echo {workspace}",
            workspace="/tmp/`id`/work",
            job_id="job-1",
            for_shell=True,
        )
        assert "'/tmp/`id`/work'" in result

    def test_for_shell_quotes_semicolon(self) -> None:
        """Semicolon-based command chaining must be neutralized."""
        result = expand_hook_variables(
            "ls {workspace}",
            workspace="/tmp; rm -rf /",
            job_id="j1",
            for_shell=True,
        )
        assert "'/tmp; rm -rf /'" in result

    def test_for_shell_quotes_job_id(self) -> None:
        """Job ID with shell metacharacters must be quoted."""
        result = expand_hook_variables(
            "echo {job_id}",
            workspace="/work",
            job_id="job; cat /etc/passwd",
            for_shell=True,
        )
        assert "'job; cat /etc/passwd'" in result

    def test_for_shell_quotes_sheet_count(self) -> None:
        """Sheet count is also quoted for consistency."""
        result = expand_hook_variables(
            "echo {sheet_count}",
            workspace="/work",
            job_id="j1",
            sheet_count=42,
            for_shell=True,
        )
        # Numbers don't actually need quoting but shlex.quote still
        # returns them unquoted or quoted — either is safe
        assert "42" in result

    def test_no_for_shell_preserves_raw_values(self) -> None:
        """Without for_shell, values are NOT quoted (for path usage)."""
        result = expand_hook_variables(
            "{workspace}/output/{job_id}.json",
            workspace="/path with spaces",
            job_id="my-job",
        )
        assert result == "/path with spaces/output/my-job.json"

    def test_for_shell_pipe_injection(self) -> None:
        """Pipe-based injection must be neutralized."""
        result = expand_hook_variables(
            "cat {workspace}",
            workspace="/tmp | cat /etc/shadow",
            job_id="j1",
            for_shell=True,
        )
        assert "'/tmp | cat /etc/shadow'" in result


class TestHookResult:
    """Tests for HookResult dataclass."""

    def test_basic_result(self) -> None:
        """HookResult should store hook execution metadata."""
        result = HookResult(
            hook_type="run_command",
            description="Test hook",
            success=True,
            exit_code=0,
            duration_seconds=1.5,
        )

        assert result.hook_type == "run_command"
        assert result.description == "Test hook"
        assert result.success is True
        assert result.exit_code == 0
        assert result.duration_seconds == 1.5

    def test_failed_result(self) -> None:
        """HookResult should store failure information."""
        result = HookResult(
            hook_type="run_script",
            description="Failing hook",
            success=False,
            exit_code=1,
            error_message="Script returned non-zero",
        )

        assert result.success is False
        assert result.error_message == "Script returned non-zero"

    def test_chained_job_result(self) -> None:
        """HookResult should store chained job info for run_job hooks."""
        result = HookResult(
            hook_type="run_job",
            description="Chain to next job",
            success=True,
            chained_job_path=Path("/config/next.yaml"),
            chained_job_workspace=Path("/workspace"),
        )

        assert result.chained_job_path == Path("/config/next.yaml")
        assert result.chained_job_workspace == Path("/workspace")


class TestConcertContext:
    """Tests for ConcertContext tracking."""

    def test_default_values(self) -> None:
        """ConcertContext should have sensible defaults."""
        ctx = ConcertContext(concert_id="test-concert")

        assert ctx.concert_id == "test-concert"
        assert ctx.chain_depth == 0
        assert ctx.parent_job_id is None
        assert ctx.total_jobs_run == 0
        assert ctx.jobs_in_chain == []

    def test_chain_tracking(self) -> None:
        """ConcertContext should track job chain."""
        ctx = ConcertContext(
            concert_id="concert-1",
            chain_depth=2,
            parent_job_id="job-1",
            total_jobs_run=3,
            jobs_in_chain=["job-0", "job-1", "job-2"],
        )

        assert ctx.chain_depth == 2
        assert len(ctx.jobs_in_chain) == 3
        assert "job-1" in ctx.jobs_in_chain


class TestHookExecutor:
    """Tests for HookExecutor class."""

    @pytest.fixture
    def minimal_config(self) -> JobConfig:
        """Create minimal JobConfig for testing."""
        return JobConfig.model_validate({
            "name": "test-job",
            "description": "Test job",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 10, "total_items": 10},
            "prompt": {"template": "Test prompt"},
        })

    @pytest.fixture
    def config_with_hooks(self) -> JobConfig:
        """Create JobConfig with hooks configured."""
        return JobConfig.model_validate({
            "name": "hook-test-job",
            "description": "Job with hooks",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 10, "total_items": 10},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo 'Success!'",
                    "description": "Echo success",
                },
            ],
        })

    def test_expand_hook_variables(self, minimal_config: JobConfig) -> None:
        """_expand_hook_variables should substitute workspace and job variables."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/test/workspace"),
        )

        result = executor._expand_hook_variables("{workspace}/output/{job_id}.json")

        assert result == "/test/workspace/output/test-job.json"

    def test_expand_hook_variables_sheet_count(self, minimal_config: JobConfig) -> None:
        """_expand_hook_variables should substitute sheet_count."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/work"),
        )

        result = executor._expand_hook_variables("Processed {sheet_count} sheets")

        assert result == "Processed 1 sheets"

    def test_expand_hook_variables_for_shell_quotes_workspace(
        self, minimal_config: JobConfig,
    ) -> None:
        """for_shell=True should apply shlex.quote to workspace (F-020 fix)."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/path with spaces/work"),
        )
        result = executor._expand_hook_variables(
            "ls {workspace}", for_shell=True,
        )
        assert result == "ls '/path with spaces/work'"

    def test_expand_hook_variables_for_shell_quotes_job_id(
        self, minimal_config: JobConfig,
    ) -> None:
        """for_shell=True should apply shlex.quote to job_id (F-020 fix)."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/work"),
        )
        result = executor._expand_hook_variables(
            "echo {job_id}", for_shell=True,
        )
        # job_id is "test-job" — shlex.quote only adds quotes if needed
        assert "test-job" in result

    def test_expand_hook_variables_for_shell_prevents_injection(
        self, minimal_config: JobConfig,
    ) -> None:
        """for_shell=True must prevent shell metacharacter injection (F-020)."""
        # A workspace path with shell metacharacters
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/tmp/$(whoami)/workspace"),
        )
        result = executor._expand_hook_variables(
            "ls {workspace}", for_shell=True,
        )
        # The $() must be neutralized by quoting
        assert "$(whoami)" not in result or "'" in result
        # Verify quoting actually protects
        import shlex
        assert shlex.quote("/tmp/$(whoami)/workspace") in result

    def test_expand_hook_variables_without_for_shell_no_quotes(
        self, minimal_config: JobConfig,
    ) -> None:
        """Without for_shell, workspace should NOT be quoted (path usage)."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/path with spaces/work"),
        )
        result = executor._expand_hook_variables("{workspace}/output.json")
        # No quotes — this is for filesystem paths
        assert result == "/path with spaces/work/output.json"

    def test_expand_hook_variables_for_shell_semicolon_injection(
        self, minimal_config: JobConfig,
    ) -> None:
        """for_shell must neutralize semicolon-based command injection (F-020)."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/tmp; rm -rf /; echo"),
        )
        result = executor._expand_hook_variables(
            "ls {workspace}", for_shell=True,
        )
        # The semicolons must be inside quotes — not interpreted by shell
        import shlex
        assert shlex.quote("/tmp; rm -rf /; echo") in result

    @pytest.mark.asyncio
    async def test_no_hooks_returns_empty(self, minimal_config: JobConfig) -> None:
        """execute_hooks should return empty list when no hooks configured."""
        executor = HookExecutor(
            config=minimal_config,
            workspace=Path("/test"),
        )

        result = await executor.execute_hooks()

        assert result == []

    @pytest.mark.asyncio
    async def test_run_command_hook(self, config_with_hooks: JobConfig) -> None:
        """run_command hooks should execute shell commands."""
        executor = HookExecutor(
            config=config_with_hooks,
            workspace=Path("/tmp"),
        )

        results = await executor.execute_hooks()

        # Assuming 'echo' is available and succeeds
        assert len(results) == 1
        assert results[0].hook_type == "run_command"
        assert results[0].success is True
        assert results[0].exit_code == 0

    @pytest.mark.asyncio
    async def test_hook_failure_tracking(self) -> None:
        """Failed hooks should have error information."""
        config = JobConfig.model_validate({
            "name": "fail-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_command",
                    "command": "exit 42",  # Intentional failure
                    "description": "Failing command",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].exit_code == 42

    @pytest.mark.asyncio
    async def test_run_job_missing_path(self) -> None:
        """run_job hook should fail gracefully for missing job_path."""
        config = JobConfig.model_validate({
            "name": "run-job-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": "/nonexistent/job.yaml",
                    "description": "Chain to missing job",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None
        assert "not found" in results[0].error_message.lower()

    @pytest.mark.asyncio
    async def test_unknown_hook_type(self) -> None:
        """Unknown hook types should fail gracefully."""
        config = JobConfig.model_validate({
            "name": "unknown-hook-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
        })
        # Manually add a hook and modify its type attribute after creation
        hook = PostSuccessHookConfig(
            type="run_command",  # Valid type for construction
            command="echo test",  # Required for run_command type
            description="Unknown hook",
        )
        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(hook, "type", "unsupported_type")
        config.on_success = [hook]

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None
        assert "Unknown hook type" in results[0].error_message

    def test_get_next_job_to_chain_none(self, minimal_config: JobConfig) -> None:
        """get_next_job_to_chain should return None when no chained jobs."""
        executor = HookExecutor(config=minimal_config, workspace=Path("/tmp"))

        result = executor.get_next_job_to_chain()

        assert result is None

    def test_get_next_job_to_chain_success(self, minimal_config: JobConfig) -> None:
        """get_next_job_to_chain should return successful run_job path."""
        executor = HookExecutor(config=minimal_config, workspace=Path("/tmp"))

        # Simulate a successful run_job hook
        executor.hook_results = [
            HookResult(
                hook_type="run_job",
                description="Chain job",
                success=True,
                chained_job_path=Path("/config/next.yaml"),
                chained_job_workspace=Path("/work"),
            ),
        ]

        result = executor.get_next_job_to_chain()

        assert result is not None
        job_path, workspace = result
        assert job_path == Path("/config/next.yaml")
        assert workspace == Path("/work")


class TestConcertLimits:
    """Tests for concert chain depth limiting."""

    @pytest.mark.asyncio
    async def test_chain_depth_limit_enforced(self, tmp_path: Path) -> None:
        """run_job should fail when chain depth limit reached."""
        # Create a real job config file so we pass the existence check
        job_config = tmp_path / "next-job.yaml"
        job_config.write_text("name: next-job\nbackend:\n  type: claude_cli\n")

        config = JobConfig.model_validate({
            "name": "depth-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "max_chain_depth": 2,
            },
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config),
                    "description": "Chained job",
                },
            ],
        })

        # Create context at depth limit
        concert_ctx = ConcertContext(
            concert_id="test",
            chain_depth=2,  # Already at limit
        )

        executor = HookExecutor(
            config=config,
            workspace=Path("/tmp"),
            concert_context=concert_ctx,
        )

        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None
        assert "depth limit" in results[0].error_message.lower()


class TestHookErrorPaths:
    """Tests for hook error paths: timeout, abort-on-failure, detached spawning, exceptions."""

    @pytest.mark.asyncio
    async def test_abort_on_failure_stops_remaining_hooks(self) -> None:
        """When on_failure='abort', remaining hooks should not execute."""
        config = JobConfig.model_validate({
            "name": "abort-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_command",
                    "command": "exit 1",
                    "description": "Failing hook",
                    "on_failure": "abort",
                },
                {
                    "type": "run_command",
                    "command": "echo 'should not run'",
                    "description": "Should be skipped",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        # Only the first hook should have run
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].exit_code == 1

    @pytest.mark.asyncio
    async def test_continue_on_failure_runs_remaining_hooks(self) -> None:
        """When on_failure='continue' (default), remaining hooks should execute."""
        config = JobConfig.model_validate({
            "name": "continue-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_command",
                    "command": "exit 1",
                    "description": "Failing hook",
                    "on_failure": "continue",
                },
                {
                    "type": "run_command",
                    "command": "echo 'still runs'",
                    "description": "Should still run",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is True

    @pytest.mark.asyncio
    async def test_concert_abort_on_hook_failure(self) -> None:
        """abort_concert_on_hook_failure should stop remaining hooks."""
        config = JobConfig.model_validate({
            "name": "concert-abort-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "abort_concert_on_hook_failure": True,
            },
            "on_success": [
                {
                    "type": "run_command",
                    "command": "exit 1",
                    "description": "Failing hook",
                },
                {
                    "type": "run_command",
                    "command": "echo 'should not run'",
                    "description": "Should be skipped",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        # Only the first hook should have run due to concert abort
        assert len(results) == 1
        assert results[0].success is False

    @pytest.mark.asyncio
    async def test_command_timeout_returns_failure(self) -> None:
        """Hook exceeding timeout should return failure with timeout message.

        Uses a subprocess that creates its own process group so kill() works
        properly (shell subprocesses don't propagate SIGKILL to children).
        """
        config = JobConfig.model_validate({
            "name": "timeout-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_command",
                    # Use exec to replace the shell process so kill() reaches it
                    "command": "exec sleep 60",
                    "description": "Slow hook",
                    "timeout_seconds": 0.5,
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None
        assert "timeout" in results[0].error_message.lower()

    @pytest.mark.asyncio
    async def test_script_timeout_returns_failure(self) -> None:
        """run_script hook exceeding timeout should return failure.

        run_script uses subprocess_exec (no shell), so kill() targets
        the process directly.
        """
        import sys

        config = JobConfig.model_validate({
            "name": "script-timeout-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_script",
                    # Use python -c for a direct process (no shell children)
                    "command": f"{sys.executable} -c 'import time; time.sleep(60)'",
                    "description": "Slow script",
                    "timeout_seconds": 0.5,
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None
        assert "timeout" in results[0].error_message.lower()

    def test_run_command_missing_command(self) -> None:
        """run_command with no command should be rejected at construction."""
        with pytest.raises(ValidationError, match="requires 'command' field"):
            PostSuccessHookConfig(
                type="run_command",
                description="Missing command",
            )

    def test_run_script_missing_command(self) -> None:
        """run_script with no command should be rejected at construction."""
        with pytest.raises(ValidationError, match="requires 'command' field"):
            PostSuccessHookConfig(
                type="run_script",
                description="Missing script",
            )

    def test_run_job_no_job_path(self) -> None:
        """run_job with no job_path should be rejected at construction."""
        with pytest.raises(ValidationError, match="requires 'job_path' field"):
            PostSuccessHookConfig(
                type="run_job",
                description="Missing path",
            )

    @pytest.mark.asyncio
    async def test_hook_exception_captured_as_failure(self) -> None:
        """Exceptions during hook execution should be captured, not raised."""
        config = JobConfig.model_validate({
            "name": "exception-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_script",
                    "command": "",  # Will cause shlex.split to succeed but empty args
                    "description": "Exception hook",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        # Should produce a result (possibly failure), not raise
        assert len(results) == 1
        assert results[0].success is False

    @pytest.mark.asyncio
    async def test_exception_with_abort_stops_remaining(self) -> None:
        """Exception in hook with on_failure='abort' should stop remaining hooks."""
        config = JobConfig.model_validate({
            "name": "exc-abort-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_script",
                    "command": "/nonexistent/binary/xyzabc123",
                    "description": "Will throw FileNotFoundError",
                    "on_failure": "abort",
                },
                {
                    "type": "run_command",
                    "command": "echo 'should not run'",
                    "description": "Should be skipped",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        # First hook fails with exception, abort prevents second hook
        assert len(results) == 1
        assert results[0].success is False

    @pytest.mark.asyncio
    async def test_chain_depth_below_limit_proceeds(self, tmp_path: Path) -> None:
        """run_job should proceed past the depth check when below limit.

        Rather than actually spawning mozart (which may not be in PATH or may
        take too long), we test that the depth check logic is correct by
        directly calling _execute_run_job and verifying the error is NOT
        about depth limit.
        """
        job_config = tmp_path / "next-job.yaml"
        job_config.write_text("name: next-job\nbackend:\n  type: claude_cli\n")

        config = JobConfig.model_validate({
            "name": "depth-ok-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "max_chain_depth": 5,
                "cooldown_between_jobs_seconds": 0,
            },
        })

        hook = PostSuccessHookConfig(
            type="run_job",
            job_path=job_config,
            description="Chained job",
            timeout_seconds=2.0,  # Short timeout to avoid hanging
        )

        concert_ctx = ConcertContext(
            concert_id="test",
            chain_depth=1,  # Below limit of 5
        )

        executor = HookExecutor(
            config=config,
            workspace=Path("/tmp"),
            concert_context=concert_ctx,
        )

        result = await executor._execute_run_job(hook)

        # Should NOT fail due to depth limit (may fail for other reasons
        # like mozart not being in PATH, but NOT depth limit)
        if not result.success and result.error_message:
            assert "depth limit" not in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_hook_duration_tracked(self) -> None:
        """Hook execution should track duration_seconds."""
        config = JobConfig.model_validate({
            "name": "duration-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "on_success": [
                {
                    "type": "run_command",
                    "command": "echo done",
                    "description": "Quick hook",
                },
            ],
        })

        executor = HookExecutor(config=config, workspace=Path("/tmp"))
        results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].duration_seconds >= 0.0


class TestDetachedChildSurvival:
    """Tests that detached hook children survive parent event loop closure.

    Verifies the fix for the asyncio BaseSubprocessTransport SIGKILL bug:
    asyncio.create_subprocess_exec wraps children in a transport whose
    __del__ kills the child PID when the event loop closes. Using
    subprocess.Popen instead avoids this because Popen.__del__ only warns.
    """

    @pytest.mark.asyncio
    async def test_detached_child_survives_parent(self, tmp_path: Path) -> None:
        """A detached hook child should still be alive after the hook returns.

        This verifies subprocess.Popen is used (not asyncio.create_subprocess_exec),
        since the asyncio transport would SIGKILL the child on event loop close.
        """
        # Create a real job config so _execute_run_job passes existence check
        job_config = tmp_path / "sleep-job.yaml"
        job_config.write_text(
            "name: sleep-job\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: Test\n"
        )

        config = JobConfig.model_validate({
            "name": "detach-survival-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "cooldown_between_jobs_seconds": 0,
            },
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config),
                    "description": "Detached chain test",
                    "detached": True,
                },
            ],
        })

        # Patch Popen to spawn a real `sleep 30` instead of `mozart run ...`
        # (mozart may not be in PATH in test environments). We capture the
        # real Popen object to check liveness afterward.
        spawned_pids: list[int] = []
        original_popen = _subprocess.Popen

        def capturing_popen(cmd, **kwargs):
            # Replace the mozart command with sleep
            proc = original_popen(["sleep", "30"], **kwargs)
            spawned_pids.append(proc.pid)
            return proc

        executor = HookExecutor(config=config, workspace=tmp_path)

        try:
            with patch("marianne.execution.hooks._try_daemon_submit", return_value=(False, None)), \
                 patch("marianne.execution.hooks._subprocess.Popen", side_effect=capturing_popen):
                results = await executor.execute_hooks()

            # Hook should report success
            assert len(results) == 1
            assert results[0].success is True
            assert len(spawned_pids) == 1

            pid = spawned_pids[0]

            # The child should still be alive after the hook returned
            # (os.kill with signal 0 checks existence without sending a signal)
            os.kill(pid, 0)  # Should NOT raise ProcessLookupError

        finally:
            # Clean up: kill any spawned children
            for pid in spawned_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass

    @pytest.mark.asyncio
    async def test_detached_daemon_fallback_still_spawns_popen(self, tmp_path: Path) -> None:
        """When daemon is unavailable, detached hook should fall back to Popen."""
        job_config = tmp_path / "fallback-job.yaml"
        job_config.write_text(
            "name: fallback-job\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: Test\n"
        )

        config = JobConfig.model_validate({
            "name": "detach-fallback-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "cooldown_between_jobs_seconds": 0,
            },
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config),
                    "description": "Fallback test",
                    "detached": True,
                },
            ],
        })

        spawned_pids: list[int] = []
        original_popen = _subprocess.Popen

        def capturing_popen(cmd, **kwargs):
            proc = original_popen(["sleep", "30"], **kwargs)
            spawned_pids.append(proc.pid)
            return proc

        executor = HookExecutor(config=config, workspace=tmp_path)

        try:
            # _try_daemon_submit returns (False, None) → daemon unavailable
            with patch("marianne.execution.hooks._try_daemon_submit", return_value=(False, None)), \
                 patch("marianne.execution.hooks._subprocess.Popen", side_effect=capturing_popen):
                results = await executor.execute_hooks()

            assert len(results) == 1
            assert results[0].success is True
            # Popen WAS called (fallback path)
            assert len(spawned_pids) == 1
            # Should NOT be routed via daemon
            assert results[0].chained_job_info is not None
            assert results[0].chained_job_info.get("routed_via") != "daemon"
        finally:
            for pid in spawned_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass

    @pytest.mark.asyncio
    async def test_detached_liveness_check_catches_dead_child(self, tmp_path: Path) -> None:
        """If the detached child dies immediately, the hook should report failure."""
        job_config = tmp_path / "fail-job.yaml"
        job_config.write_text(
            "name: fail-job\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: Test\n"
        )

        config = JobConfig.model_validate({
            "name": "detach-dead-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "cooldown_between_jobs_seconds": 0,
            },
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config),
                    "description": "Detached dead child test",
                    "detached": True,
                },
            ],
        })

        # Spawn a child that exits immediately (exit 0 completes before
        # the 200ms liveness check)
        original_popen = _subprocess.Popen

        def dying_popen(cmd, **kwargs):
            proc = original_popen(["true"], **kwargs)  # exits immediately
            return proc

        executor = HookExecutor(config=config, workspace=tmp_path)

        with patch("marianne.execution.hooks._try_daemon_submit", return_value=(False, None)), \
             patch("marianne.execution.hooks._subprocess.Popen", side_effect=dying_popen):
            results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error_message is not None
        assert "exited immediately" in results[0].error_message


class TestDaemonAwareChaining:
    """Tests for daemon-aware chained job submission (Issue #74).

    Verifies that detached run_job hooks attempt to submit through the
    daemon IPC before falling back to subprocess.Popen, and that
    chain_depth is correctly propagated.
    """

    @pytest.fixture
    def job_config_file(self, tmp_path: Path) -> Path:
        """Create a minimal job config file for chaining tests."""
        config = tmp_path / "chain-target.yaml"
        config.write_text(
            "name: chain-target\n"
            "backend:\n  type: claude_cli\n"
            "sheet:\n  size: 1\n  total_items: 1\n"
            "prompt:\n  template: Test\n"
        )
        return config

    def _make_config(self, job_config_file: Path, *, detached: bool = True) -> JobConfig:
        """Build a JobConfig with a run_job hook."""
        return JobConfig.model_validate({
            "name": "daemon-chain-test",
            "description": "Test",
            "backend": {"type": "claude_cli"},
            "sheet": {"size": 1, "total_items": 1},
            "prompt": {"template": "Test"},
            "concert": {
                "enabled": True,
                "cooldown_between_jobs_seconds": 0,
            },
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(job_config_file),
                    "description": "Daemon chaining test",
                    "detached": detached,
                },
            ],
        })

    @pytest.mark.asyncio
    async def test_daemon_available_submits_via_ipc(
        self, tmp_path: Path, job_config_file: Path,
    ) -> None:
        """When daemon is available and accepts, Popen should NOT be called."""
        config = self._make_config(job_config_file)
        executor = HookExecutor(
            config=config,
            workspace=tmp_path,
            concert_context=ConcertContext(concert_id="test", chain_depth=0),
        )

        with patch(
            "marianne.execution.hooks._try_daemon_submit",
            return_value=(True, "daemon-job-123"),
        ) as mock_submit, \
             patch("marianne.execution.hooks._subprocess.Popen") as mock_popen:
            results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].chained_job_info is not None
        assert results[0].chained_job_info["routed_via"] == "daemon"
        assert results[0].chained_job_info["job_id"] == "daemon-job-123"
        # Popen must NOT have been called
        mock_popen.assert_not_called()
        # _try_daemon_submit was called with correct args
        mock_submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_daemon_unavailable_falls_back_to_popen(
        self, tmp_path: Path, job_config_file: Path,
    ) -> None:
        """When daemon is unavailable, hook should fall back to Popen."""
        config = self._make_config(job_config_file)
        executor = HookExecutor(
            config=config,
            workspace=tmp_path,
            concert_context=ConcertContext(concert_id="test", chain_depth=0),
        )

        spawned_pids: list[int] = []
        original_popen = _subprocess.Popen

        def capturing_popen(cmd, **kwargs):
            proc = original_popen(["sleep", "30"], **kwargs)
            spawned_pids.append(proc.pid)
            return proc

        try:
            with patch(
                "marianne.execution.hooks._try_daemon_submit",
                return_value=(False, None),
            ), patch(
                "marianne.execution.hooks._subprocess.Popen",
                side_effect=capturing_popen,
            ):
                results = await executor.execute_hooks()

            assert len(results) == 1
            assert results[0].success is True
            assert len(spawned_pids) == 1
            # Fallback — not routed through daemon
            assert results[0].chained_job_info is not None
            assert results[0].chained_job_info.get("routed_via") != "daemon"
            assert "pid" in results[0].chained_job_info
        finally:
            for pid in spawned_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass

    @pytest.mark.asyncio
    async def test_daemon_submission_error_falls_back_to_popen(
        self, tmp_path: Path, job_config_file: Path,
    ) -> None:
        """When _try_daemon_submit raises, hook should fall back to Popen.

        This tests the fail-open design: any exception in the daemon path
        returns (False, None), and Popen is used as fallback.
        """
        config = self._make_config(job_config_file)
        executor = HookExecutor(
            config=config,
            workspace=tmp_path,
            concert_context=ConcertContext(concert_id="test", chain_depth=0),
        )

        spawned_pids: list[int] = []
        original_popen = _subprocess.Popen

        def capturing_popen(cmd, **kwargs):
            proc = original_popen(["sleep", "30"], **kwargs)
            spawned_pids.append(proc.pid)
            return proc

        try:
            # Simulate _try_daemon_submit returning failure (it catches
            # exceptions internally and returns (False, None))
            with patch(
                "marianne.execution.hooks._try_daemon_submit",
                return_value=(False, None),
            ), patch(
                "marianne.execution.hooks._subprocess.Popen",
                side_effect=capturing_popen,
            ):
                results = await executor.execute_hooks()

            assert len(results) == 1
            assert results[0].success is True
            assert len(spawned_pids) == 1
        finally:
            for pid in spawned_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass

    @pytest.mark.asyncio
    async def test_chain_depth_propagated_in_daemon_submit(
        self, tmp_path: Path, job_config_file: Path,
    ) -> None:
        """chain_depth from ConcertContext should be incremented and passed."""
        config = self._make_config(job_config_file)
        concert_ctx = ConcertContext(concert_id="test", chain_depth=3)
        executor = HookExecutor(
            config=config,
            workspace=tmp_path,
            concert_context=concert_ctx,
        )

        captured_args: list[dict] = []

        async def capturing_submit(job_path, workspace, fresh, chain_depth):
            captured_args.append({
                "job_path": job_path,
                "workspace": workspace,
                "fresh": fresh,
                "chain_depth": chain_depth,
            })
            return True, "daemon-job-depth"

        with patch(
            "marianne.execution.hooks._try_daemon_submit",
            side_effect=capturing_submit,
        ):
            results = await executor.execute_hooks()

        assert len(results) == 1
        assert results[0].success is True
        # chain_depth should be concert_ctx.chain_depth + 1 = 4
        assert len(captured_args) == 1
        assert captured_args[0]["chain_depth"] == 4

    @pytest.mark.asyncio
    async def test_non_detached_hooks_skip_daemon_path(
        self, tmp_path: Path, job_config_file: Path,
    ) -> None:
        """Non-detached run_job hooks should NOT attempt daemon submission."""
        config = self._make_config(job_config_file, detached=False)
        executor = HookExecutor(
            config=config,
            workspace=tmp_path,
            concert_context=ConcertContext(concert_id="test", chain_depth=0),
        )

        with patch(
            "marianne.execution.hooks._try_daemon_submit",
        ) as mock_submit:
            # Non-detached hook will try to run `mozart run ...` and likely
            # fail because mozart isn't in PATH — but _try_daemon_submit
            # should NOT be called at all.
            results = await executor.execute_hooks()

        mock_submit.assert_not_called()
        assert len(results) == 1
        # The hook may fail (mozart not in PATH) but that's fine — we're
        # testing that daemon path was not attempted


class TestTryDaemonSubmitUnit:
    """Unit tests for the _try_daemon_submit function itself (Issue #74).

    Tests the function directly with mocked daemon components, covering
    success, rejection, connection failures, and import errors.
    """

    @pytest.mark.asyncio
    async def test_daemon_available_and_accepts(self) -> None:
        """When daemon is available and accepts, should return (True, job_id)."""
        from marianne.daemon.types import JobResponse
        from marianne.execution.hooks import _try_daemon_submit

        mock_response = JobResponse(
            job_id="test-job-42", status="accepted", message="OK",
        )

        mock_is_available = AsyncMock(return_value=True)
        with patch("marianne.daemon.detect.is_daemon_available", mock_is_available), \
             patch("marianne.daemon.ipc.client.DaemonClient") as MockClient:
            MockClient.return_value.submit_job = AsyncMock(return_value=mock_response)
            ok, job_id = await _try_daemon_submit(
                job_path=Path("/config/test.yaml"),
                workspace=Path("/workspace"),
                fresh=False,
                chain_depth=2,
            )

        assert ok is True
        assert job_id == "test-job-42"

    @pytest.mark.asyncio
    async def test_daemon_available_but_rejects(self) -> None:
        """When daemon rejects submission, should return (False, None)."""
        from marianne.daemon.types import JobResponse
        from marianne.execution.hooks import _try_daemon_submit

        mock_response = JobResponse(
            job_id="", status="rejected", message="Under pressure",
        )

        mock_is_available = AsyncMock(return_value=True)
        with patch("marianne.daemon.detect.is_daemon_available", mock_is_available), \
             patch("marianne.daemon.ipc.client.DaemonClient") as MockClient:
            MockClient.return_value.submit_job = AsyncMock(return_value=mock_response)
            ok, job_id = await _try_daemon_submit(
                job_path=Path("/config/test.yaml"),
                workspace=None,
                fresh=True,
                chain_depth=None,
            )

        assert ok is False
        assert job_id is None

    @pytest.mark.asyncio
    async def test_daemon_unavailable(self) -> None:
        """When daemon is not available, should return (False, None) immediately."""
        from marianne.execution.hooks import _try_daemon_submit

        mock_is_available = AsyncMock(return_value=False)
        with patch("marianne.daemon.detect.is_daemon_available", mock_is_available):
            ok, job_id = await _try_daemon_submit(
                job_path=Path("/config/test.yaml"),
                workspace=None,
                fresh=False,
                chain_depth=1,
            )

        assert ok is False
        assert job_id is None

    @pytest.mark.asyncio
    async def test_connection_error_falls_back(self) -> None:
        """Connection errors during submit should return (False, None)."""
        from marianne.execution.hooks import _try_daemon_submit

        mock_is_available = AsyncMock(return_value=True)
        with patch("marianne.daemon.detect.is_daemon_available", mock_is_available), \
             patch("marianne.daemon.ipc.client.DaemonClient") as MockClient:
            MockClient.return_value.submit_job = AsyncMock(
                side_effect=ConnectionRefusedError("Socket gone"),
            )
            ok, job_id = await _try_daemon_submit(
                job_path=Path("/config/test.yaml"),
                workspace=Path("/workspace"),
                fresh=False,
                chain_depth=3,
            )

        assert ok is False
        assert job_id is None

    @pytest.mark.asyncio
    async def test_chain_depth_passed_to_job_request(self) -> None:
        """chain_depth argument should be included in the JobRequest."""
        from marianne.daemon.types import JobResponse
        from marianne.execution.hooks import _try_daemon_submit

        mock_response = JobResponse(
            job_id="chained-42", status="accepted", message="OK",
        )
        captured_requests = []

        async def capture_submit(request):
            captured_requests.append(request)
            return mock_response

        mock_is_available = AsyncMock(return_value=True)
        with patch("marianne.daemon.detect.is_daemon_available", mock_is_available), \
             patch("marianne.daemon.ipc.client.DaemonClient") as MockClient:
            MockClient.return_value.submit_job = AsyncMock(side_effect=capture_submit)
            await _try_daemon_submit(
                job_path=Path("/config/test.yaml"),
                workspace=Path("/work"),
                fresh=True,
                chain_depth=5,
            )

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.chain_depth == 5
        assert req.fresh is True
        assert req.config_path == Path("/config/test.yaml")
        assert req.workspace == Path("/work")


class TestJobMetaChainDepth:
    """Tests for chain_depth propagation in JobMeta.to_dict() (Issue #74)."""

    def test_chain_depth_in_to_dict_when_set(self) -> None:
        """to_dict() should include chain_depth when it's not None."""
        from marianne.daemon.manager import JobMeta

        meta = JobMeta(
            job_id="test-job",
            config_path=Path("/config.yaml"),
            workspace=Path("/workspace"),
            chain_depth=3,
        )
        result = meta.to_dict()
        assert result["chain_depth"] == 3

    def test_chain_depth_absent_in_to_dict_when_none(self) -> None:
        """to_dict() should NOT include chain_depth when it's None."""
        from marianne.daemon.manager import JobMeta

        meta = JobMeta(
            job_id="test-job",
            config_path=Path("/config.yaml"),
            workspace=Path("/workspace"),
        )
        result = meta.to_dict()
        assert "chain_depth" not in result

    def test_chain_depth_in_job_request_model(self) -> None:
        """JobRequest should accept chain_depth and default to None."""
        from marianne.daemon.types import JobRequest

        # With chain_depth
        req = JobRequest(config_path=Path("/c.yaml"), chain_depth=7)
        assert req.chain_depth == 7

        # Without chain_depth (defaults to None)
        req2 = JobRequest(config_path=Path("/c.yaml"))
        assert req2.chain_depth is None

    def test_chain_depth_in_job_submit_params(self) -> None:
        """JobSubmitParams TypedDict should accept chain_depth."""
        from marianne.daemon.types import JobSubmitParams

        params: JobSubmitParams = {
            "config_path": "/config.yaml",
            "chain_depth": 4,
        }
        assert params["chain_depth"] == 4

    def test_job_request_round_trip_with_chain_depth(self) -> None:
        """JobRequest should serialize and deserialize chain_depth correctly."""
        from marianne.daemon.types import JobRequest

        req = JobRequest(
            config_path=Path("/config/test.yaml"),
            workspace=Path("/workspace"),
            fresh=True,
            chain_depth=2,
        )
        dumped = req.model_dump(mode="json")
        restored = JobRequest(**dumped)
        assert restored.chain_depth == 2
        assert restored.fresh is True
        assert restored.config_path == Path("/config/test.yaml")
