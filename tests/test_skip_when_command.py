"""Tests for skip_when_command support in _should_skip_sheet.

Tests the Phase 2 (command-based) skip logic added to LifecycleMixin,
covering exit codes, timeout handling, workspace expansion, and fail-open
behavior.

GH#71
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mozart.core.checkpoint import CheckpointState, JobStatus
from mozart.core.config.job import JobConfig
from mozart.execution.runner.lifecycle import LifecycleMixin


def _make_runner(
    skip_when_command: dict | None = None,
    skip_when: dict | None = None,
    workspace: Path | None = None,
) -> MagicMock:
    """Create a minimal mock runner with skip_when_command config."""
    sheet_config: dict = {"size": 1, "total_items": 5}
    if skip_when:
        sheet_config["skip_when"] = skip_when
    if skip_when_command:
        sheet_config["skip_when_command"] = skip_when_command

    config_dict: dict = {
        "name": "test-skip-cmd",
        "sheet": sheet_config,
        "prompt": {"template": "{{ sheet_num }}"},
    }
    if workspace is not None:
        config_dict["workspace"] = str(workspace)

    config = JobConfig.model_validate(config_dict)

    runner = MagicMock(spec=LifecycleMixin)
    runner.config = config
    runner._logger = MagicMock()
    return runner


def _make_state() -> CheckpointState:
    return CheckpointState(
        job_id="test-job",
        job_name="test-skip-cmd",
        config_path="test.yaml",
        total_sheets=5,
        status=JobStatus.RUNNING,
    )


class TestSkipWhenCommandNoConditions:
    """Tests for cases where no command conditions are configured."""

    async def test_no_command_conditions_returns_none(self) -> None:
        """No skip_when_command configured at all -> returns None (run sheet)."""
        runner = _make_runner(skip_when_command=None)
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None

    async def test_sheet_not_in_conditions_returns_none(self) -> None:
        """Sheet 1 not in skip_when_command {3: ...} -> returns None."""
        runner = _make_runner(
            skip_when_command={3: {"command": "true", "description": "Only for sheet 3"}}
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None


class TestSkipWhenCommandExitCodes:
    """Tests for command exit code handling."""

    async def test_command_exits_0_skips_sheet(self, tmp_path: Path) -> None:
        """Command 'true' exits 0 -> sheet is skipped."""
        runner = _make_runner(
            skip_when_command={1: {"command": "true", "description": "Always skip"}},
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is not None
        assert "Always skip" in result

    async def test_command_exits_nonzero_runs_sheet(self, tmp_path: Path) -> None:
        """Command 'false' exits 1 -> sheet runs (returns None)."""
        runner = _make_runner(
            skip_when_command={1: {"command": "false"}},
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None


class TestSkipWhenCommandWorkspace:
    """Tests for workspace template expansion."""

    async def test_workspace_expansion_in_command(self, tmp_path: Path) -> None:
        """Create a file in tmp_path, use {workspace} in command to grep for it."""
        marker = tmp_path / "marker.txt"
        marker.write_text("SKIP_ME")

        runner = _make_runner(
            skip_when_command={
                1: {
                    "command": 'grep -q "SKIP_ME" {workspace}/marker.txt',
                    "description": "Marker found",
                }
            },
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is not None
        assert "Marker found" in result


class TestSkipWhenCommandFailOpen:
    """Tests for fail-open behavior on timeout and errors."""

    async def test_timeout_fails_open(self, tmp_path: Path) -> None:
        """sleep 10 with timeout_seconds=0.1 -> returns None (fail-open)."""
        runner = _make_runner(
            skip_when_command={
                1: {"command": "sleep 10", "timeout_seconds": 0.1}
            },
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None
        runner._logger.warning.assert_called()
        # Verify the structured log event name
        call_args = runner._logger.warning.call_args
        assert call_args[0][0] == "skip_when_command_timeout"

    async def test_command_error_fails_open(self) -> None:
        """/nonexistent/binary -> returns None (fail-open)."""
        runner = _make_runner(
            skip_when_command={
                1: {"command": "/nonexistent/binary/that/does/not/exist"}
            }
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        # Fail-open: either returns None (command error) or None (nonzero exit)
        assert result is None


class TestSkipWhenCommandPrecedence:
    """Tests for interaction between expression and command conditions."""

    async def test_expression_skip_checked_first(self) -> None:
        """Both skip_when=True and skip_when_command=false on sheet 3.

        Expression returns True -> skip. Command not even checked.
        """
        runner = _make_runner(
            skip_when={3: "True"},
            skip_when_command={3: {"command": "false"}},
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 3, state)

        # Expression matched -> skipped with expression reason
        assert result is not None
        assert "Condition met: True" in result


class TestSkipWhenCommandReasons:
    """Tests for skip reason messages."""

    async def test_description_used_in_skip_reason(self, tmp_path: Path) -> None:
        """Command 'true' with description 'Only 1 phase' -> reason contains it."""
        runner = _make_runner(
            skip_when_command={1: {"command": "true", "description": "Only 1 phase"}},
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is not None
        assert "Only 1 phase" in result

    async def test_no_description_uses_command_in_reason(self, tmp_path: Path) -> None:
        """Command 'true' with no description -> reason contains 'Command succeeded'."""
        runner = _make_runner(
            skip_when_command={1: {"command": "true"}},
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is not None
        assert "Command succeeded" in result


class TestSkipWhenFailOpenSemantics116:
    """Tests for fail-open behavior on skip_when eval errors (Bug #116).

    Bug #116 changed skip_when expression errors from fail-closed (sheet SKIPPED)
    to fail-open (sheet RUNS), matching skip_when_command's existing behavior.

    The fix is in lifecycle.py: except Exception block now returns None instead
    of a skip-reason string.
    """

    async def test_skip_when_eval_error_sheet_runs(self) -> None:
        """skip_when eval error -> None (sheet runs, fail-open) (TEST-116-A).

        Primary fail-before-pass test.
        """
        # undefined_name_that_does_not_exist raises NameError in safe_builtins context
        runner = _make_runner(
            skip_when={1: "undefined_name_that_does_not_exist"},
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None  # fail-open: sheet runs
        runner._logger.error.assert_called_once()
        call_args = runner._logger.error.call_args
        assert call_args[0][0] == "skip_condition_eval_failed"

    async def test_skip_when_command_timeout_sheet_runs(
        self, tmp_path: Path
    ) -> None:
        """skip_when_command timeout -> None (non-regression gate) (TEST-116-B).

        This was already fail-open before Bug #116 fix.
        """
        runner = _make_runner(
            skip_when_command={1: {"command": "sleep 10", "timeout_seconds": 0.01}},
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None  # fail-open for timeout

    async def test_skip_when_command_error_sheet_runs(
        self, tmp_path: Path
    ) -> None:
        """skip_when_command subprocess error -> None (non-regression gate) (TEST-116-C).

        Non-regression gate for #116 — skip_when_command subprocess error was already fail-open.
        """
        runner = _make_runner(
            skip_when_command={
                1: {"command": "/nonexistent/binary/that/does/not/exist"}
            },
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None  # fail-open for subprocess error

    async def test_both_fail_sheet_runs(self, tmp_path: Path) -> None:
        """Both skip_when error and skip_when_command error -> sheet runs (TEST-116-D).

        Parity invariant: when ALL skip logic fails, the default is always to run.
        """
        runner = _make_runner(
            skip_when={1: "undefined_var_xyz"},
            skip_when_command={1: {"command": "/nonexistent/binary"}},
            workspace=tmp_path,
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None  # fail-open for both mechanisms

    async def test_normal_skip_when_true_skips_sheet(self) -> None:
        """Normal skip_when evaluates True -> sheet IS skipped (TEST-116-E).

        Regression test for #116 — the normal skip path must still work.
        """
        runner = _make_runner(
            skip_when={1: "True"},
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is not None  # sheet IS skipped normally
        runner._logger.error.assert_not_called()  # no error on clean eval

    async def test_type_error_in_skip_when_expression_fails_open(self) -> None:
        """TypeError in skip_when expression -> None (fail-open, not just NameError)."""
        runner = _make_runner(
            skip_when={1: "1 + 'string'"},
        )
        state = _make_state()

        result = await LifecycleMixin._should_skip_sheet(runner, 1, state)

        assert result is None
        runner._logger.error.assert_called()
