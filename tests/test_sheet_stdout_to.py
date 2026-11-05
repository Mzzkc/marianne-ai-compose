"""`sheet.stdout_to`: an instrument's full reply as a workspace file (GH #494).

An `http` profile answers but has no filesystem, so until now its reply
reached later sheets only through `cross_sheet` capture and could never meet
a `file_exists` or `content_regex` validation. The musician now writes the
whole stdout to the named file before validations run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marianne.core.config import JobConfig
from marianne.core.sheet import Sheet, build_sheets
from marianne.daemon.baton.musician import _write_stdout_to, resolve_stdout_to
from marianne.execution.base import ExecutionResult
from marianne.validation.checks.paths import AbsoluteHomePathCheck
from marianne.validation.checks.structure import FanOutCoherenceCheck


def _config(sheet: dict[str, object], **extra: object) -> JobConfig:
    return JobConfig.model_validate(
        {
            "name": "reply",
            "workspace": "./ws",
            "sheet": sheet,
            "prompt": {"template": "x"},
            **extra,
        }
    )


def _sheet(tmp_path: Path, stdout_to: str | None) -> Sheet:
    return Sheet(
        num=1,
        movement=1,
        voice=None,
        voice_count=1,
        workspace=tmp_path,
        instrument_name="local-server",
        prompt_template="x",
        stdout_to=stdout_to,
    )


def _result(stdout: str, *, success: bool = True) -> ExecutionResult:
    return ExecutionResult(
        success=success,
        exit_code=0 if success else 1,
        stdout=stdout,
        stderr="",
        duration_seconds=0.1,
    )


class TestSchema:
    def test_the_key_reaches_the_sheet_entity(self) -> None:
        config = _config({"total_items": 2, "stdout_to": {1: "design.md"}})
        sheets = build_sheets(config)
        assert sheets[0].stdout_to == "design.md"
        assert sheets[1].stdout_to is None

    def test_a_fanned_out_stage_gets_one_file_per_voice(self) -> None:
        config = _config({"total_items": 2, "fan_out": {2: 3}, "stdout_to": {2: "answer.md"}})
        assert config.sheet.stdout_to == {2: "answer-2.md", 3: "answer-3.md", 4: "answer-4.md"}

    def test_a_key_outside_the_sheet_range_is_v214(self, tmp_path: Path) -> None:
        config = _config({"total_items": 2, "stdout_to": {5: "late.md"}})
        issues = FanOutCoherenceCheck().check(config, tmp_path / "s.yaml", "")
        assert any("stdout_to sheet 5" in i.message for i in issues)

    def test_an_absolute_home_path_is_v004(self, tmp_path: Path) -> None:
        home = str(Path.home())
        raw = (
            "name: r\nworkspace: ./ws\nprompt: {template: x}\n"
            f"sheet:\n  total_items: 1\n  stdout_to:\n    1: {home}/reply.md\n"
        )
        config = _config({"total_items": 1, "stdout_to": {1: f"{home}/reply.md"}})
        issues = AbsoluteHomePathCheck().check(config, tmp_path / "s.yaml", raw)
        assert [i.metadata["field"] for i in issues] == ["sheet.stdout_to.1"]


class TestResolution:
    def test_relative_paths_anchor_in_the_workspace(self, tmp_path: Path) -> None:
        assert resolve_stdout_to(_sheet(tmp_path, "out/design.md")) == (
            tmp_path.resolve() / "out" / "design.md"
        )

    def test_workspace_placeholder_expands(self, tmp_path: Path) -> None:
        assert resolve_stdout_to(_sheet(tmp_path, "{workspace}/design.md")) == (
            tmp_path.resolve() / "design.md"
        )

    def test_a_path_that_escapes_the_workspace_is_refused(self, tmp_path: Path) -> None:
        assert resolve_stdout_to(_sheet(tmp_path, "../elsewhere.md")) is None
        assert resolve_stdout_to(_sheet(tmp_path, "/etc/passwd")) is None

    def test_no_target_means_nothing(self, tmp_path: Path) -> None:
        assert resolve_stdout_to(_sheet(tmp_path, None)) is None


class TestWrite:
    def test_the_whole_reply_is_written(self, tmp_path: Path) -> None:
        reply = "line\n" * 400  # 2,000 chars: four times the stdout_tail
        assert _write_stdout_to(_sheet(tmp_path, "design.md"), _result(reply)) is None
        assert (tmp_path / "design.md").read_text() == reply

    def test_parent_directories_are_created(self, tmp_path: Path) -> None:
        _write_stdout_to(_sheet(tmp_path, "out/deep/design.md"), _result("ok"))
        assert (tmp_path / "out" / "deep" / "design.md").read_text() == "ok"

    def test_a_failed_attempt_writes_nothing(self, tmp_path: Path) -> None:
        failed = _result("partial", success=False)
        assert _write_stdout_to(_sheet(tmp_path, "design.md"), failed) is None
        assert not (tmp_path / "design.md").exists()

    def test_an_escaping_target_is_a_warning_not_a_write(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "escaped.md"
        warning = _write_stdout_to(_sheet(tmp_path, "../escaped.md"), _result("x"))
        assert warning is not None and "escapes the workspace" in warning
        assert not outside.exists()

    def test_credentials_are_redacted(self, tmp_path: Path) -> None:
        secret = "sk-ant-api03-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        _write_stdout_to(_sheet(tmp_path, "r.md"), _result(f"key {secret}"))
        text = (tmp_path / "r.md").read_text()
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in text

    @pytest.mark.parametrize("target", ["", None])
    def test_an_unset_target_is_a_no_op(self, tmp_path: Path, target: str | None) -> None:
        assert _write_stdout_to(_sheet(tmp_path, target), _result("x")) is None
        assert list(tmp_path.iterdir()) == []
