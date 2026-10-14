"""Tests for technique validation checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from marianne.core.config.techniques import TechniqueConfig, TechniqueKind


class _Issue:
    def __init__(self, check_id: str, severity: str, message: str) -> None:
        self.check_id = check_id
        self.severity = severity
        self.message = message


def _check_skill_paths(techniques: dict[str, TechniqueConfig]) -> list[_Issue]:
    issues: list[_Issue] = []
    for name, tc in techniques.items():
        if tc.kind != TechniqueKind.SKILL:
            continue
        sp = tc.config.get("path")
        if sp is None:
            continue
        if not Path(sp).exists():
            issues.append(
                _Issue("V301", "warning", f"Skill '{name}' references missing document: {sp}")
            )
    return issues


def _check_mcp_instrument(
    techniques: dict[str, TechniqueConfig], instrument: str | None, *, loader: object | None = None
) -> list[_Issue]:
    issues: list[_Issue] = []
    mcp_names = [n for n, t in techniques.items() if t.kind == TechniqueKind.MCP]
    if not mcp_names or instrument is None:
        return issues
    if loader is not None:
        try:
            profile = loader(instrument)  # type: ignore[operator]
            if (
                profile
                and hasattr(profile, "cli")
                and profile.cli
                and hasattr(profile.cli, "command")
            ):
                if profile.cli.command.mcp_config_flag is None:
                    issues.append(
                        _Issue(
                            "V302",
                            "warning",
                            f"MCP techniques ({', '.join(mcp_names)}) declared but instrument "
                            f"'{instrument}' does not have mcp_config_flag. "
                            "MCP tools will only be available via code mode.",
                        )
                    )
        except Exception:
            pass
    return issues


class TestTechniqueSkillPathCheckProperties:
    def test_check_id(self) -> None:
        assert (
            _check_skill_paths(
                {
                    "t": TechniqueConfig.model_validate(
                        {"kind": "skill", "phases": [], "config": {"path": "/x"}}
                    )
                }
            )[0].check_id
            == "V301"
        )

    def test_description(self) -> None:
        assert (
            "missing"
            in _check_skill_paths(
                {
                    "t": TechniqueConfig.model_validate(
                        {"kind": "skill", "phases": [], "config": {"path": "/x"}}
                    )
                }
            )[0].message.lower()
        )

    def test_severity_is_warning(self) -> None:
        assert (
            _check_skill_paths(
                {
                    "t": TechniqueConfig.model_validate(
                        {"kind": "skill", "phases": [], "config": {"path": "/x"}}
                    )
                }
            )[0].severity
            == "warning"
        )


class TestTechniqueSkillPathCheckExisting:
    def test_existing_skill_doc_no_issues(self, tmp_path: Path) -> None:
        (tmp_path / "s.md").write_text("# S")
        assert (
            _check_skill_paths(
                {
                    "m": TechniqueConfig.model_validate(
                        {
                            "kind": "skill",
                            "phases": ["w"],
                            "config": {"path": str(tmp_path / "s.md")},
                        }
                    )
                }
            )
            == []
        )


class TestTechniqueSkillPathCheckMissing:
    def test_missing_skill_doc_produces_warning(self) -> None:
        assert (
            len(
                _check_skill_paths(
                    {
                        "m": TechniqueConfig.model_validate(
                            {
                                "kind": "skill",
                                "phases": ["w"],
                                "config": {"path": "/nonexistent/s.md"},
                            }
                        )
                    }
                )
            )
            == 1
        )

    def test_multiple_missing_skill_docs(self) -> None:
        assert (
            len(
                _check_skill_paths(
                    {
                        "a": TechniqueConfig.model_validate(
                            {"kind": "skill", "phases": [], "config": {"path": "/x/a.md"}}
                        ),
                        "b": TechniqueConfig.model_validate(
                            {"kind": "skill", "phases": [], "config": {"path": "/x/b.md"}}
                        ),
                    }
                )
            )
            == 2
        )


class TestTechniqueSkillPathCheckSkipped:
    def test_no_techniques_no_issues(self) -> None:
        assert _check_skill_paths({}) == []

    def test_mcp_technique_skipped(self) -> None:
        assert (
            _check_skill_paths(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})}
            )
            == []
        )

    def test_skill_without_path_no_issues(self) -> None:
        assert (
            _check_skill_paths(
                {"m": TechniqueConfig.model_validate({"kind": "skill", "phases": ["w"]})}
            )
            == []
        )


class TestTechniqueMcpInstrumentCheckProperties:
    def _loader_no_flag(self) -> MagicMock:
        p = MagicMock()
        p.cli.command.mcp_config_flag = None
        return MagicMock(return_value=p)

    def test_check_id(self) -> None:
        assert (
            _check_mcp_instrument(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})},
                "t",
                loader=self._loader_no_flag(),
            )[0].check_id
            == "V302"
        )

    def test_description(self) -> None:
        assert (
            "mcp_config_flag"
            in _check_mcp_instrument(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})},
                "t",
                loader=self._loader_no_flag(),
            )[0].message
        )

    def test_severity_is_warning(self) -> None:
        assert (
            _check_mcp_instrument(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})},
                "t",
                loader=self._loader_no_flag(),
            )[0].severity
            == "warning"
        )


class TestTechniqueMcpInstrumentCheckNoIssues:
    def test_no_techniques_no_issues(self) -> None:
        assert _check_mcp_instrument({}, "claude-code") == []

    def test_no_mcp_techniques_no_issues(self) -> None:
        assert (
            _check_mcp_instrument(
                {"m": TechniqueConfig.model_validate({"kind": "skill", "phases": ["w"]})}, "cc"
            )
            == []
        )

    def test_mcp_capable_instrument_no_issues(self) -> None:
        p = MagicMock()
        p.cli.command.mcp_config_flag = "--mcp-config"
        assert (
            _check_mcp_instrument(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})},
                "cc",
                loader=MagicMock(return_value=p),
            )
            == []
        )


class TestTechniqueMcpInstrumentCheckWarning:
    def test_mcp_technique_non_mcp_instrument(self) -> None:
        p = MagicMock()
        p.cli.command.mcp_config_flag = None
        assert (
            len(
                _check_mcp_instrument(
                    {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})},
                    "oc",
                    loader=MagicMock(return_value=p),
                )
            )
            == 1
        )

    def test_multiple_mcp_techniques_listed(self) -> None:
        p = MagicMock()
        p.cli.command.mcp_config_flag = None
        issues = _check_mcp_instrument(
            {
                "g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]}),
                "f": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]}),
            },
            "oc",
            loader=MagicMock(return_value=p),
        )
        assert len(issues) == 1 and "g" in issues[0].message and "f" in issues[0].message


class TestTechniqueMcpInstrumentCheckEdgeCases:
    def test_profile_load_failure_skips_check(self) -> None:
        def bad(n: str) -> None:
            raise FileNotFoundError

        assert (
            _check_mcp_instrument(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})},
                "b",
                loader=bad,
            )
            == []
        )

    def test_unknown_instrument_no_v302(self) -> None:
        assert (
            _check_mcp_instrument(
                {"g": TechniqueConfig.model_validate({"kind": "mcp", "phases": ["w"]})}, None
            )
            == []
        )


class TestRegisteredChecksRunOnRealScores:
    """V301/V302 fixtures through the real check classes and a real score.

    The classes above this exercise local reimplementations; these pin the
    registered behaviour GH #439 relies on (fire on the defect, silent on
    the correct form).
    """

    @staticmethod
    def _score(tmp_path: Path, techniques_block: str) -> tuple[object, Path, str]:
        from marianne.core.config import JobConfig

        score = tmp_path / "score.yaml"
        text = (
            "name: technique-fixture\n"
            f"workspace: {tmp_path.as_posix()}\n"
            "instrument: cli\n"
            "sheet:\n"
            "  size: 1\n"
            "  total_items: 1\n"
            "prompt:\n"
            "  template: do the thing\n"
            f"{techniques_block}\n"
        )
        score.write_text(text, encoding="utf-8")
        return JobConfig.from_yaml(score), score, text

    def test_v301_fires_on_missing_skill_path(self, tmp_path: Path) -> None:
        from marianne.validation.checks.techniques import TechniqueSkillPathCheck

        config, path, raw = self._score(
            tmp_path,
            "techniques:\n"
            "  mateship:\n"
            "    kind: skill\n"
            "    phases: [work]\n"
            "    config:\n"
            "      path: does-not-exist.md\n",
        )
        issues = TechniqueSkillPathCheck().check(config, path, raw)
        assert [i.check_id for i in issues] == ["V301"]
        assert "mateship" in issues[0].message

    def test_v301_silent_when_the_document_exists(self, tmp_path: Path) -> None:
        from marianne.validation.checks.techniques import TechniqueSkillPathCheck

        (tmp_path / "mates.md").write_text("# method\n", encoding="utf-8")
        config, path, raw = self._score(
            tmp_path,
            "techniques:\n"
            "  mateship:\n"
            "    kind: skill\n"
            "    phases: [work]\n"
            "    config:\n"
            "      path: mates.md\n",
        )
        assert TechniqueSkillPathCheck().check(config, path, raw) == []

    def test_v302_registered_in_default_checks(self) -> None:
        from marianne.validation.checks.techniques import (
            TechniqueMcpInstrumentCheck,
            TechniqueSkillPathCheck,
        )
        from marianne.validation.runner import create_default_checks

        registered = {check.check_id for check in create_default_checks()}
        assert "V301" in registered
        assert "V302" in registered
        assert TechniqueSkillPathCheck().check_id == "V301"
        assert TechniqueMcpInstrumentCheck().check_id == "V302"
