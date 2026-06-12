"""#209: opt-in code-mode execution config + the unsandboxed warning.

Composer decision (2026-06-12): activate Stage 3 code execution, but
OPT-IN (default off) and do NOT require bwrap — warn strongly at
validation when code mode would run unsandboxed. These tests pin the
safety contract: default off, and a loud warning when enabled on a host
without bwrap.
"""

from __future__ import annotations

from pathlib import Path

from marianne.core.config import JobConfig
from marianne.core.config.execution import CodeExecutionConfig


def _config(**code_exec: object) -> JobConfig:
    data: dict[str, object] = {
        "name": "s",
        "workspace": "./ws",
        "instrument": "claude-code",
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": "x"},
    }
    if code_exec:
        data["code_execution"] = code_exec
    return JobConfig.model_validate(data)


class TestCodeExecutionConfig:
    def test_default_is_disabled(self) -> None:
        cfg = _config()
        assert cfg.code_execution.enabled is False

    def test_opt_in(self) -> None:
        cfg = _config(enabled=True)
        assert cfg.code_execution.enabled is True

    def test_does_not_require_sandbox_by_default(self) -> None:
        # Composer: don't require bwrap (warn instead).
        cfg = _config(enabled=True)
        assert cfg.code_execution.require_sandbox is False

    def test_timeout_default(self) -> None:
        assert CodeExecutionConfig().timeout_seconds == 30.0


class TestUnsandboxedWarning:
    """V-code: a loud WARNING when code execution is enabled but bwrap is
    unavailable (execution would be UNSANDBOXED)."""

    def _run_check(self, config: JobConfig, *, bwrap_present: bool):
        from unittest.mock import patch

        from marianne.validation.checks.config import (
            CodeExecutionSandboxCheck,
        )

        check = CodeExecutionSandboxCheck()
        which = "/usr/bin/bwrap" if bwrap_present else None
        with patch("shutil.which", return_value=which):
            return check.check(config, Path("s.yaml"), "code_execution:\n  enabled: true")

    def test_warns_when_enabled_and_no_bwrap(self) -> None:
        issues = self._run_check(_config(enabled=True), bwrap_present=False)
        assert len(issues) == 1
        assert issues[0].severity.value == "warning"
        assert "unsandboxed" in issues[0].message.lower()

    def test_no_warning_when_bwrap_present(self) -> None:
        assert self._run_check(_config(enabled=True), bwrap_present=True) == []

    def test_no_warning_when_disabled(self) -> None:
        assert self._run_check(_config(), bwrap_present=False) == []

    def test_require_sandbox_escalates_to_error(self) -> None:
        # If a score explicitly requires the sandbox, a missing bwrap is an
        # ERROR (the score asked for isolation it can't get).
        issues = self._run_check(
            _config(enabled=True, require_sandbox=True), bwrap_present=False
        )
        assert len(issues) == 1
        assert issues[0].severity.value == "error"


class TestAdapterWiringSafety:
    """The load-bearing safety property, against the REAL register_job:
    agent code runs ONLY when a job explicitly opts in. Default off => no
    code-execution config stored, and a router is created only on opt-in."""

    def _sheet(self):
        from marianne.core.sheet import Sheet

        return Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            instrument_name="claude-code",
            workspace=Path("/tmp/ce-ws"),
            prompt_template="x",
            validations=[],
            timeout_seconds=60.0,
        )

    def test_default_off_stores_no_config_and_no_router(self) -> None:
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        adapter.register_job("j-off", [self._sheet()], {1: []})
        # No code_execution passed (default) => nothing stored, no router.
        assert "j-off" not in adapter._job_code_execution
        assert adapter.get_router("j-off") is None

    def test_disabled_config_stores_nothing(self) -> None:
        from marianne.core.config.execution import CodeExecutionConfig
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        adapter.register_job(
            "j-dis", [self._sheet()], {1: []},
            code_execution=CodeExecutionConfig(enabled=False),
        )
        assert "j-dis" not in adapter._job_code_execution
        assert adapter.get_router("j-dis") is None

    def test_opt_in_stores_config_and_ensures_router(self) -> None:
        from marianne.core.config.execution import CodeExecutionConfig
        from marianne.daemon.baton.adapter import BatonAdapter
        from marianne.daemon.technique_router import TechniqueRouter

        adapter = BatonAdapter()
        adapter.register_job(
            "j-on", [self._sheet()], {1: []},
            code_execution=CodeExecutionConfig(enabled=True),
        )
        assert adapter._job_code_execution["j-on"].enabled is True
        assert isinstance(adapter.get_router("j-on"), TechniqueRouter)

    def test_deregister_clears_code_execution(self) -> None:
        from marianne.core.config.execution import CodeExecutionConfig
        from marianne.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter()
        adapter.register_job(
            "j-clr", [self._sheet()], {1: []},
            code_execution=CodeExecutionConfig(enabled=True),
        )
        adapter.deregister_job("j-clr")
        assert "j-clr" not in adapter._job_code_execution
