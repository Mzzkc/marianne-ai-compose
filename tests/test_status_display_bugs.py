"""Tests for status/display/observability bugs — F-068, F-069, F-048.

F-068: Completed timestamp shown for RUNNING scores
F-069: V101 false positive on Jinja2 loop variables
F-048: Cost shows $0.00 when cost limits disabled

TDD: tests written first (red), then fixes (green).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marianne.core.checkpoint import CheckpointState, JobStatus


# =========================================================================
# F-068: Completed timestamp shown for RUNNING scores
# =========================================================================


class TestF068CompletedTimestamp:
    """F-068: 'Completed:' should only show for terminal job statuses.

    When a job is RUNNING and some sheets have completed, job.completed_at
    may be set. The status display should NOT show 'Completed:' because
    the job itself isn't complete — only individual sheets are.
    """

    @pytest.fixture
    def running_job_with_completed_at(self, tmp_path: object) -> CheckpointState:
        """A RUNNING job that has a completed_at timestamp set."""
        job = CheckpointState(
            job_id="test-running",
            job_name="test-running-score",
            config_path="/tmp/test.yaml",
            workspace=str(tmp_path),
            total_sheets=10,
            current_sheet=5,
            last_completed_sheet=5,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(hours=1),
            completed_at=datetime.now(UTC) - timedelta(minutes=5),
            created_at=datetime.now(UTC) - timedelta(hours=2),
            updated_at=datetime.now(UTC),
        )
        return job

    @pytest.fixture
    def completed_job(self, tmp_path: object) -> CheckpointState:
        """A COMPLETED job that has a completed_at timestamp set."""
        job = CheckpointState(
            job_id="test-completed",
            job_name="test-completed-score",
            config_path="/tmp/test.yaml",
            workspace=str(tmp_path),
            total_sheets=10,
            current_sheet=10,
            last_completed_sheet=10,
            status=JobStatus.COMPLETED,
            started_at=datetime.now(UTC) - timedelta(hours=1),
            completed_at=datetime.now(UTC),
            created_at=datetime.now(UTC) - timedelta(hours=2),
            updated_at=datetime.now(UTC),
        )
        return job

    @pytest.fixture
    def failed_job(self, tmp_path: object) -> CheckpointState:
        """A FAILED job that has a completed_at timestamp set."""
        job = CheckpointState(
            job_id="test-failed",
            job_name="test-failed-score",
            config_path="/tmp/test.yaml",
            workspace=str(tmp_path),
            total_sheets=10,
            current_sheet=5,
            last_completed_sheet=5,
            status=JobStatus.FAILED,
            started_at=datetime.now(UTC) - timedelta(hours=1),
            completed_at=datetime.now(UTC),
            created_at=datetime.now(UTC) - timedelta(hours=2),
            updated_at=datetime.now(UTC),
            error_message="Sheet 5 failed after retries exhausted",
        )
        return job

    def test_running_job_hides_completed_timestamp(
        self, running_job_with_completed_at: CheckpointState
    ) -> None:
        """RUNNING job should NOT show 'Completed:' in status display."""
        from marianne.cli.commands.status import _output_status_rich

        _ = None  # output capture handled by mock
        with patch("marianne.cli.commands.status.console") as mock_console:
            # Capture all print calls
            printed: list[str] = []
            mock_console.print = lambda *args, **kwargs: printed.append(
                " ".join(str(a) for a in args)
            )
            # Panel needs special handling
            mock_console.width = 80

            _output_status_rich(running_job_with_completed_at)

        # Join all output and check that "Completed:" is NOT present
        full_output = "\n".join(printed)
        assert "Completed:" not in full_output, (
            f"RUNNING job should not show 'Completed:' timestamp. "
            f"Got: {full_output}"
        )

    def test_completed_job_shows_completed_timestamp(
        self, completed_job: CheckpointState
    ) -> None:
        """COMPLETED job SHOULD show 'Completed:' in status display."""
        from marianne.cli.commands.status import _output_status_rich

        printed: list[str] = []
        with patch("marianne.cli.commands.status.console") as mock_console:
            mock_console.print = lambda *args, **kwargs: printed.append(
                " ".join(str(a) for a in args)
            )
            mock_console.width = 80

            _output_status_rich(completed_job)

        full_output = "\n".join(printed)
        assert "Completed:" in full_output, (
            f"COMPLETED job should show 'Completed:' timestamp. "
            f"Got: {full_output}"
        )

    def test_failed_job_shows_completed_timestamp(
        self, failed_job: CheckpointState
    ) -> None:
        """FAILED job SHOULD show 'Completed:' in status display."""
        from marianne.cli.commands.status import _output_status_rich

        printed: list[str] = []
        with patch("marianne.cli.commands.status.console") as mock_console:
            mock_console.print = lambda *args, **kwargs: printed.append(
                " ".join(str(a) for a in args)
            )
            mock_console.width = 80

            _output_status_rich(failed_job)

        full_output = "\n".join(printed)
        assert "Completed:" in full_output, (
            f"FAILED job should show 'Completed:' timestamp. "
            f"Got: {full_output}"
        )

    def test_paused_job_hides_completed_timestamp(
        self, running_job_with_completed_at: CheckpointState
    ) -> None:
        """PAUSED job should NOT show 'Completed:' in status display."""
        running_job_with_completed_at.status = JobStatus.PAUSED

        from marianne.cli.commands.status import _output_status_rich

        printed: list[str] = []
        with patch("marianne.cli.commands.status.console") as mock_console:
            mock_console.print = lambda *args, **kwargs: printed.append(
                " ".join(str(a) for a in args)
            )
            mock_console.width = 80

            _output_status_rich(running_job_with_completed_at)

        full_output = "\n".join(printed)
        assert "Completed:" not in full_output, (
            f"PAUSED job should not show 'Completed:' timestamp. "
            f"Got: {full_output}"
        )


# =========================================================================
# F-069: V101 false positive on Jinja2 template-declared variables
# =========================================================================


class TestF069V101FalsePositive:
    """F-069: V101 should not flag variables declared via {% set %} or {% for %}.

    jinja2_meta.find_undeclared_variables doesn't properly track variables
    declared inside conditional branches ({% if %}/{% elif %}). The V101
    checker must supplement it by walking the AST for Assign and For nodes
    to find template-declared variables.
    """

    @pytest.fixture
    def checker(self) -> object:
        from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck
        return JinjaUndefinedVariableCheck()

    @pytest.fixture
    def minimal_config(self, tmp_path: Path) -> tuple[object, Path]:
        """Create a minimal config for testing V101."""
        from marianne.core.config.job import JobConfig
        yaml_content = """
name: test-v101
sheet:
  count: 1
prompt:
  template: "{{ workspace }}"
"""
        config_path = tmp_path / "test.yaml"
        config_path.write_text(yaml_content)
        config = JobConfig.from_yaml(config_path)
        return config, config_path

    def test_for_loop_variable_not_flagged(self, checker: object) -> None:
        """{% for id, char in items %} should NOT flag char or id."""
        from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck
        import jinja2

        template = (
            "{% for id, char in characters.items() %}"
            "{{ char.name }}"
            "{% endfor %}"
        )
        env = jinja2.Environment()
        defined = set(JinjaUndefinedVariableCheck.BUILTIN_VARIABLES)
        defined.add("characters")

        issues = checker._check_undefined_vars(template, "test", defined, env)
        flagged_vars = {i.metadata["variable"] for i in issues}
        assert "char" not in flagged_vars, "char is defined in {% for %}, should not be flagged"
        assert "id" not in flagged_vars, "id is defined in {% for %}, should not be flagged"

    def test_set_variable_not_flagged(self, checker: object) -> None:
        """{% set char = expr %} should NOT flag char."""
        from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck
        import jinja2

        template = (
            "{% set char = characters[instance] %}"
            "{{ char.name }}"
        )
        env = jinja2.Environment()
        defined = set(JinjaUndefinedVariableCheck.BUILTIN_VARIABLES)
        defined.update({"characters", "instance"})

        issues = checker._check_undefined_vars(template, "test", defined, env)
        flagged_vars = {i.metadata["variable"] for i in issues}
        assert "char" not in flagged_vars, "char is defined in {% set %}, should not be flagged"

    def test_conditional_branch_set_not_flagged(self, checker: object) -> None:
        """{% set %} inside {% elif %} should NOT cause false positive."""
        from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck
        import jinja2

        template = (
            "{% if stage == 1 %}"
            "{% for id, char in characters.items() %}{{ char.name }}{% endfor %}"
            "{% elif stage == 2 %}"
            "{% set char = characters[instance] %}{{ char.name }}"
            "{% endif %}"
        )
        env = jinja2.Environment()
        defined = set(JinjaUndefinedVariableCheck.BUILTIN_VARIABLES)
        defined.update({"characters", "stage"})

        issues = checker._check_undefined_vars(template, "test", defined, env)
        flagged_vars = {i.metadata["variable"] for i in issues}
        assert "char" not in flagged_vars, "char is defined in both branches, should not be flagged"
        assert "id" not in flagged_vars, "id is defined in for loop, should not be flagged"

    def test_truly_undefined_variable_still_flagged(self, checker: object) -> None:
        """Variables that are genuinely undefined should still be flagged."""
        from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck
        import jinja2

        template = "{{ totally_undefined }}"
        env = jinja2.Environment()
        defined = set(JinjaUndefinedVariableCheck.BUILTIN_VARIABLES)

        issues = checker._check_undefined_vars(template, "test", defined, env)
        flagged_vars = {i.metadata["variable"] for i in issues}
        assert "totally_undefined" in flagged_vars, "Truly undefined vars should be flagged"

    def test_hello_yaml_no_false_positives(self) -> None:
        """The flagship hello-marianne.yaml example should produce zero V101 warnings."""
        from marianne.core.config.job import JobConfig
        from marianne.validation.checks.jinja import JinjaUndefinedVariableCheck

        config_path = Path("examples/hello-marianne.yaml")
        if not config_path.exists():
            pytest.skip("hello-marianne.yaml not found")

        raw_yaml = config_path.read_text()
        config = JobConfig.from_yaml(config_path)
        checker = JinjaUndefinedVariableCheck()
        issues = checker.check(config, config_path, raw_yaml)
        assert len(issues) == 0, (
            f"hello-marianne.yaml should produce zero V101 warnings. "
            f"Got: {[i.message for i in issues]}"
        )


# =========================================================================
# F-048: Cost shows $0.00 when cost limits are disabled
# =========================================================================


class TestF048CostTrackingWithoutLimits:
    """F-048: Cost should be tracked even when cost limits are disabled.

    The cost tracking and cost limit enforcement are currently bundled in
    _enforce_cost_limits(). When cost_limits.enabled=False, the entire
    method returns early — skipping _track_cost(). This means
    total_estimated_cost is always 0.00 for jobs without cost limits.

    The fix: always track costs, only gate limit enforcement.
    """

    def test_enforce_cost_limits_tracks_cost_when_disabled(self) -> None:
        """The _enforce_cost_limits method should track costs even with limits off.

        Verifies the fix by inspecting the source code structure:
        the await _track_cost call must appear BEFORE the cost_limits.enabled return.
        """
        import inspect
        from marianne.execution.runner.sheet import SheetExecutionMixin

        source = inspect.getsource(SheetExecutionMixin._enforce_cost_limits)
        # Strip docstring — only look at executable code
        # Find the closing triple-quote of the docstring
        lines = source.split("\n")
        code_lines = []
        in_docstring = False
        docstring_done = False
        for line in lines:
            stripped = line.strip()
            if not docstring_done:
                if '"""' in stripped:
                    if in_docstring:
                        docstring_done = True
                    elif stripped.count('"""') == 1:
                        in_docstring = True
                    else:
                        # Single-line docstring
                        docstring_done = True
                continue
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        # In the executable code, await self._track_cost must appear
        # BEFORE if not self.config.cost_limits.enabled
        track_pos = code_only.find("await self._track_cost")
        enabled_check_pos = code_only.find("cost_limits.enabled")

        assert track_pos != -1, "_track_cost call not found in _enforce_cost_limits code"
        assert enabled_check_pos != -1, "cost_limits.enabled check not found in code"
        assert track_pos < enabled_check_pos, (
            "_track_cost must be called BEFORE checking cost_limits.enabled. "
            "Cost tracking should always happen; only enforcement is gated."
        )

    @pytest.mark.asyncio
    async def test_cost_tracked_in_state_when_limits_disabled(self) -> None:
        """Integration: cost tracking populates state even with limits off."""
        from marianne.backends.base import ExecutionResult
        from marianne.core.checkpoint import CheckpointState, SheetState
        from marianne.core.config.job import CostLimitConfig, JobConfig
        from marianne.execution.runner.cost import CostMixin

        # Create a minimal CostMixin with disabled cost limits
        mixin = CostMixin.__new__(CostMixin)
        mixin.config = MagicMock(spec=JobConfig)
        mixin.config.cost_limits = CostLimitConfig(enabled=False)
        mixin.config.cost_limits.cost_per_1k_input_tokens = 0.003
        mixin.config.cost_limits.cost_per_1k_output_tokens = 0.015
        mixin._circuit_breaker = None
        mixin._summary = None
        mixin._logger = MagicMock(spec=["warning", "info", "debug", "error"])
        mixin.console = MagicMock(spec=["print", "log"])
        mixin._fire_event = AsyncMock()

        # Create a mock result with tokens
        result = MagicMock(spec=ExecutionResult)
        result.input_tokens = 1000
        result.output_tokens = 500
        result.tokens_used = None
        result.stdout = ""
        result.stderr = ""

        # Create real state objects
        sheet_state = MagicMock(spec=SheetState)
        sheet_state.input_tokens = None
        sheet_state.output_tokens = None
        sheet_state.estimated_cost = None

        state = MagicMock(spec=CheckpointState)
        state.total_input_tokens = 0
        state.total_output_tokens = 0
        state.total_estimated_cost = 0.0

        # Call _track_cost directly — this should always work
        await mixin._track_cost(result, sheet_state, state)

        # Verify state was updated with cost data
        assert state.total_input_tokens == 1000
        assert state.total_output_tokens == 500
        assert state.total_estimated_cost > 0, (
            "total_estimated_cost should be > 0 after tracking"
        )
