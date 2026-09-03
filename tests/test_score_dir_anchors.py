"""score_dir anchors for portable score paths — independent TDD suite.

Authored from the design document ALONE (2026-09-03, MARIANNE-HISTORY/design/
2026-09-03-score-dir-anchors-design.md) before any implementation, per the TDD
mandate. Encodes the design's seven acceptance criteria:

  AC1  ``from_yaml`` scores expose ``score_dir`` in Sheet.template_variables()
       (score file's resolved parent); string-built sheets expose no key.
  AC2  ``{{ score_dir }}`` renders in sheet prompts (empty string when unset).
  AC3  Validation ``path: "{score_dir}/x"`` and ``command:``/``working_directory:``
       expand through the ValidationEngine.
  AC4  ``expand_hook_variables`` substitutes ``{score_dir}`` and no longer warns
       on it (added to _KNOWN_HOOK_VARS).
  AC5  A run_job hook with relative ``job_path`` resolves against the submitting
       score's directory, not the conductor process CWD.
  AC6  V004 ``AbsoluteHomePathCheck`` flags absolute-under-home paths in the
       enumerated fields; passes ``~/...``, ``{workspace}``, relative paths.
  AC7  ``JobConfig.model_dump()`` contains no ``source_path``.

Expected RED against current code for every test EXCEPT the AC7 serialization
pin (``test_model_dump_excludes_source_path`` — marked EXPECTED-GREEN; the
field does not exist yet, so its absence from serialization holds trivially
today and the test pins it against the ``exclude=True`` contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from marianne.core.config import JobConfig
from marianne.core.config.execution import ValidationRule
from marianne.core.sheet import Sheet, build_sheets
from marianne.daemon.manager import JobManager
from marianne.daemon.registry import DaemonJobStatus
from marianne.daemon.types import JobRequest, JobResponse
from marianne.prompts.templating import SheetContext
from marianne.utils.hooks import expand_hook_variables
from marianne.validation.base import ValidationSeverity

# ============================================================================
# Helpers
# ============================================================================


def _write_score(
    directory: Path,
    *,
    filename: str = "score.yaml",
    template: str = "Do the work.",
    workspace: str = "./ws",
) -> Path:
    """Write a minimal valid score into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    score: dict[str, Any] = {
        "name": f"anchor-{directory.name}",
        "workspace": workspace,
        "sheet": {"size": 1, "total_items": 1},
        "prompt": {"template": template},
    }
    path = directory / filename
    path.write_text(yaml.safe_dump(score), encoding="utf-8")
    return path


def _score_sheet_context(tmp_path: Path) -> tuple[Path, Sheet, dict[str, Any]]:
    """Load a score the real way and build the baton's validation context.

    Mirrors ``daemon/baton/musician.py``: the ValidationEngine's sheet_context
    IS ``sheet.template_variables()`` output — the seam the design says score_dir
    flows through with zero engine changes.
    """
    score = _write_score(tmp_path)
    config = JobConfig.from_yaml(score)
    sheet = build_sheets(config)[0]
    context = sheet.template_variables(total_sheets=1, total_movements=1)
    return score, sheet, context


@dataclass
class _HookMeta:
    """The JobMeta fields _execute_hook_run_job actually touches."""

    job_id: str
    config_path: Path
    workspace: Path
    chain_depth: int | None = 0
    status: DaemonJobStatus = DaemonJobStatus.COMPLETED
    held_chain_hook: dict[str, Any] | None = None


# ============================================================================
# AC1 / AC7 — JobConfig.source_path plumbing and serialization
# ============================================================================


class TestSourcePathField:
    """``source_path`` is set by from_yaml only, never serialized."""

    def test_from_yaml_sets_resolved_source_path(self, tmp_path: Path) -> None:
        """from_yaml records the resolved score file path (design change 1)."""
        score = _write_score(tmp_path)
        config = JobConfig.from_yaml(score)
        # RED today: JobConfig has no source_path field at all.
        assert config.source_path == score.resolve()

    def test_string_built_config_has_no_source_and_no_score_dir(
        self, tmp_path: Path
    ) -> None:
        """from_yaml_string (state replay) leaves source_path None, and sheets
        built from it never see a score_dir template variable (AC1 negative)."""
        score = _write_score(tmp_path)
        raw = score.read_text(encoding="utf-8")
        config = JobConfig.from_yaml_string(raw)
        # RED today: no source_path attribute. Post-implementation it must be
        # None — string-built scores have no file to anchor to.
        assert config.source_path is None

        sheet = build_sheets(config)[0]
        tvars = sheet.template_variables(total_sheets=1, total_movements=1)
        assert "score_dir" not in tvars

        # A hand-built Sheet (no build_sheets provenance) must also lack the
        # anchor — the design forbids a bogus default.
        direct = Sheet(
            num=1,
            movement=1,
            voice_count=1,
            workspace=tmp_path / "ws",
            instrument_name="claude-code",
        )
        assert "score_dir" not in direct.template_variables(
            total_sheets=1, total_movements=1
        )

    def test_model_dump_excludes_source_path(self, tmp_path: Path) -> None:
        """AC7 regression pin — EXPECTED-GREEN today and forever.

        source_path is Field(exclude=True): checkpoints/state files must stay
        byte-identical. The field does not exist yet, so serialization trivially
        omits it today; this pins the exclusion once the field lands.
        """
        score = _write_score(tmp_path)
        config = JobConfig.from_yaml(score)
        assert "source_path" not in config.model_dump()
        assert "source_path" not in config.model_dump(mode="json")
        assert "source_path" not in config.to_yaml()


# ============================================================================
# AC1 — Sheet.template_variables() gains score_dir
# ============================================================================


class TestSheetTemplateVariablesScoreDir:
    """score_dir is a string equal to the score file's resolved parent."""

    def test_from_yaml_sheet_exposes_score_dir(self, tmp_path: Path) -> None:
        score = _write_score(tmp_path)
        config = JobConfig.from_yaml(score)
        sheet = build_sheets(config)[0]
        tvars = sheet.template_variables(total_sheets=1, total_movements=1)
        # RED today: no score_dir key (get → None != the expected string).
        value = tvars.get("score_dir")
        assert isinstance(value, str)
        assert value == str(score.resolve().parent)

    def test_score_dir_anchors_to_score_file_not_workspace_parent(
        self, tmp_path: Path
    ) -> None:
        """Discriminating case: score_dir follows the SCORE FILE, not the
        workspace. With workspace './ws' the two parents coincide, so anchor
        the score in a nested dir and point the workspace elsewhere."""
        score_dir = tmp_path / "nested" / "deep"
        score = _write_score(score_dir, workspace="../../elsewhere")
        config = JobConfig.from_yaml(score)
        sheet = build_sheets(config)[0]

        tvars = sheet.template_variables(total_sheets=1, total_movements=1)
        assert tvars.get("score_dir") == str(score_dir.resolve())
        # Prove the anchors genuinely differ — this test discriminates.
        assert str(config.workspace) != str(score_dir.resolve())


# ============================================================================
# AC2 — {{ score_dir }} in sheet prompts
# ============================================================================


class TestPromptScoreDirRendering:
    """score_dir renders via SheetContext.to_dict() through the real renderer."""

    def test_sheet_context_to_dict_defaults_score_dir_to_empty_string(self) -> None:
        """SheetContext.score_dir defaults to "" — templates render an empty
        string, never Undefined (matches how instrument_name defaults)."""
        context = SheetContext(
            sheet_num=1,
            total_sheets=1,
            start_item=1,
            end_item=1,
            workspace=Path("/tmp/ws"),
        )
        # RED today: to_dict() emits no score_dir key.
        assert context.to_dict().get("score_dir") == ""

    def test_score_dir_renders_in_sheet_prompt(self, tmp_path: Path) -> None:
        """End-to-end through the baton's PromptRenderer: from_yaml score with
        {{ score_dir }} in the template renders the score's directory."""
        from marianne.daemon.baton.prompt import PromptRenderer
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        score = _write_score(
            tmp_path, template="Read {{ score_dir }}/input.md and begin."
        )
        config = JobConfig.from_yaml(score)
        sheet = build_sheets(config)[0]
        renderer = PromptRenderer(
            config.prompt, total_sheets=1, total_stages=1, parallel_enabled=False
        )
        # RED today: PromptBuilder uses StrictUndefined — {{ score_dir }} raises
        # UndefinedError because no builder populates it yet.
        rendered = renderer.render(
            sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        )
        assert f"Read {score.resolve().parent}/input.md and begin." in rendered.prompt

    def test_score_dir_renders_empty_string_when_unset(self, tmp_path: Path) -> None:
        """A string-built sheet (no source) renders {{ score_dir }} as "" —
        not an UndefinedError, not a bogus path."""
        from marianne.daemon.baton.prompt import PromptRenderer
        from marianne.daemon.baton.state import AttemptContext, AttemptMode

        raw = yaml.safe_dump(
            {
                "name": "anchor-unset",
                "workspace": str(tmp_path / "ws"),
                "sheet": {"size": 1, "total_items": 1},
                "prompt": {"template": "Anchor: [{{ score_dir }}]"},
            }
        )
        config = JobConfig.from_yaml_string(raw)
        sheet = build_sheets(config)[0]
        renderer = PromptRenderer(
            config.prompt, total_sheets=1, total_stages=1, parallel_enabled=False
        )
        rendered = renderer.render(
            sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        )
        assert "Anchor: []" in rendered.prompt


# ============================================================================
# AC3 — ValidationEngine {score_dir} expansion
# ============================================================================


class TestValidationEngineScoreDir:
    """score_dir flows from template_variables() into ValidationEngine contexts
    with zero engine changes (design consumer enumeration, musician.py:1051)."""

    def test_expand_path_resolves_score_dir_template(self, tmp_path: Path) -> None:
        from marianne.execution.validation.engine import ValidationEngine

        score, sheet, context = _score_sheet_context(tmp_path)
        engine = ValidationEngine(workspace=sheet.workspace, sheet_context=context)
        # RED today: "{score_dir}/output.md".format(**context) raises KeyError —
        # score_dir is not in the sheet context yet.
        expanded = engine.expand_path("{score_dir}/output.md")
        assert expanded == score.resolve().parent / "output.md"

    async def test_command_and_working_directory_expand_score_dir(
        self, tmp_path: Path
    ) -> None:
        """A command_succeeds rule whose command: and working_directory:
        reference {score_dir} runs against the score's directory."""
        from marianne.execution.validation.engine import ValidationEngine

        score, sheet, context = _score_sheet_context(tmp_path)
        rule = ValidationRule(
            type="command_succeeds",
            command="test -d {score_dir}",
            working_directory="{score_dir}",
            description="score dir is a real directory",
        )
        engine = ValidationEngine(workspace=sheet.workspace, sheet_context=context)
        result = await engine.run_validations([rule])
        # RED today: {score_dir} never expands (KeyError on the working
        # directory expansion, or the literal path fails the test) — the rule
        # cannot pass while the anchor is missing from the context.
        assert result.failed_count == 0, str(result.to_dict_list())
        assert result.passed_count == 1


# ============================================================================
# AC4 — expand_hook_variables {score_dir}
# ============================================================================


class TestExpandHookVariablesScoreDir:
    def test_substitutes_score_dir(self, tmp_path: Path) -> None:
        """{score_dir} expands for both Path and str inputs (design change 4)."""
        # RED today: TypeError — expand_hook_variables has no score_dir kwarg.
        as_path = expand_hook_variables(
            "cat {score_dir}/notes.md",
            workspace="/tmp/ws",
            job_id="job-1",
            score_dir=tmp_path,
        )
        assert as_path == f"cat {tmp_path}/notes.md"

        as_str = expand_hook_variables(
            "cat {score_dir}/notes.md",
            workspace="/tmp/ws",
            job_id="job-1",
            score_dir=str(tmp_path),
        )
        assert as_str == f"cat {tmp_path}/notes.md"

    def test_score_dir_is_a_known_hook_variable(self) -> None:
        """score_dir joins _KNOWN_HOOK_VARS so the unknown-variable warning no
        longer fires for it (AC4's warning clause — the design names this set
        as the mechanism)."""
        from marianne.utils.hooks import _KNOWN_HOOK_VARS

        # RED today: "score_dir" is not in the known-vars frozenset.
        assert "score_dir" in _KNOWN_HOOK_VARS


# ============================================================================
# AC5 — run_job relative job_path resolves against the score's directory
# ============================================================================


class TestRunJobScoreDirAnchoring:
    @pytest.mark.adversarial
    async def test_relative_job_path_resolves_against_score_dir_not_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relative job_path must resolve against the submitting score's
        directory (meta.config_path parent). Attack: the process CWD contains a
        DIFFERENT sibling.yaml decoy — old behavior resolved (by accident)
        against conductor CWD and would chain to the decoy."""
        scores = tmp_path / "scores"
        scores.mkdir()
        (scores / "parent.yaml").write_text("# parent score\n", encoding="utf-8")
        (scores / "sibling.yaml").write_text("# real sibling\n", encoding="utf-8")

        decoy_cwd = tmp_path / "decoy-cwd"
        decoy_cwd.mkdir()
        (decoy_cwd / "sibling.yaml").write_text("# decoy sibling\n", encoding="utf-8")
        monkeypatch.chdir(decoy_cwd)

        meta = _HookMeta(
            job_id="parent",
            config_path=scores / "parent.yaml",
            workspace=tmp_path / "ws",
        )
        mock_self = MagicMock()
        mock_self._set_job_status = AsyncMock()
        mock_self._expand_hook_vars = MagicMock(side_effect=lambda s, *a, **kw: s)
        mock_self.submit_job = AsyncMock(
            return_value=JobResponse(job_id="chained", status="accepted", message="ok")
        )

        result = await JobManager._execute_hook_run_job(
            self=mock_self,
            parent_job_id="parent",
            hook={"type": "run_job", "job_path": "sibling.yaml"},
            concert=None,
            meta=meta,
        )
        assert result["success"] is True, str(result)

        request = mock_self.submit_job.call_args.args[0]
        assert isinstance(request, JobRequest)
        # RED today: the submitted config_path is the bare relative
        # Path("sibling.yaml") — no score-dir anchoring exists yet.
        assert Path(request.config_path) == (scores / "sibling.yaml").resolve()

        # Companion invariant (design: "Absolute paths unchanged"): an absolute
        # job_path is submitted exactly as given.
        mock_self.submit_job.reset_mock()
        absolute = scores / "sibling.yaml"
        result_abs = await JobManager._execute_hook_run_job(
            self=mock_self,
            parent_job_id="parent",
            hook={"type": "run_job", "job_path": str(absolute)},
            concert=None,
            meta=meta,
        )
        assert result_abs["success"] is True, str(result_abs)
        request_abs = mock_self.submit_job.call_args.args[0]
        assert Path(request_abs.config_path) == absolute.resolve()


# ============================================================================
# AC6 — V004 AbsoluteHomePathCheck
# ============================================================================


class TestV004AbsoluteHomePathCheck:
    """V004 lint: ERROR on absolute paths under the current user's home in
    workspace / prelude / cadenza / on_success job_path / prompt variable
    strings. Portable forms (~/, {workspace}, relative) pass."""

    def _check(self) -> Any:
        # RED today: ImportError — the class does not exist yet. That IS the
        # valid RED for a not-yet-implemented check (design change 6).
        from marianne.validation.checks.paths import AbsoluteHomePathCheck

        return AbsoluteHomePathCheck()

    @staticmethod
    def _load(tmp_path: Path, score: dict[str, Any]) -> tuple[JobConfig, Path, str]:
        raw = yaml.safe_dump(score)
        path = tmp_path / "v004-score.yaml"
        path.write_text(raw, encoding="utf-8")
        return JobConfig.from_yaml(path), path, raw

    def test_check_id_and_severity(self) -> None:
        check = self._check()
        assert check.check_id == "V004"
        assert check.severity is ValidationSeverity.ERROR
        assert check.description

    def test_flags_absolute_home_paths_in_enumerated_fields(
        self, tmp_path: Path
    ) -> None:
        home = Path.home()
        score: dict[str, Any] = {
            "name": "v004-flagged",
            "workspace": str(home / "v004-ws"),
            "sheet": {
                "size": 1,
                "total_items": 1,
                "prelude": [
                    {"file": str(home / "v004-prelude-notes.md"), "as": "context"}
                ],
                "cadenzas": {
                    1: [{"directory": str(home / "v004-dir"), "as": "context"}]
                },
            },
            "prompt": {
                "template": "Work.",
                "variables": {"config_ref": str(home / "v004-var-data.json")},
            },
            "on_success": [
                {
                    "type": "run_job",
                    "job_path": str(home / "v004-jobpath-next.yaml"),
                    "description": "chain",
                }
            ],
        }
        config, config_path, raw = self._load(tmp_path, score)

        issues = self._check().check(config, config_path, raw)

        assert issues, "V004 must flag absolute-under-home paths"
        for issue in issues:
            assert issue.check_id == "V004"
            assert issue.severity is ValidationSeverity.ERROR

        # Every enumerated field must be flagged. Union over message/context/
        # metadata so the test pins FIELD COVERAGE without dictating the issue
        # structure (one-issue-per-field vs aggregated is an implementation
        # choice the design leaves open).
        blob = "\n".join(
            issue.message
            + (issue.context or "")
            + " ".join(f"{k}={v}" for k, v in issue.metadata.items())
            for issue in issues
        )
        for token in ("v004-ws", "v004-prelude", "v004-dir", "v004-jobpath", "v004-var"):
            assert token in blob, f"V004 did not flag the '{token}' field"

    def test_allows_portable_forms(self, tmp_path: Path) -> None:
        """~/..., {workspace}, {score_dir}, and relative paths are portable —
        no V004 issues. Note: the model pre-resolves a ~/ workspace to an
        absolute-under-home Path, so the check must judge the RAW YAML value
        for workspace (the AC explicitly allows ~/ there)."""
        score: dict[str, Any] = {
            "name": "v004-portable",
            "workspace": "~/v004-ok/ws",
            "sheet": {
                "size": 1,
                "total_items": 1,
                "prelude": [{"file": "~/v004-ok/notes.md", "as": "context"}],
                "cadenzas": {
                    1: [{"file": "{score_dir}/extra.md", "as": "context"}]
                },
            },
            "prompt": {
                "template": "Work.",
                "variables": {
                    "tilde_ref": "~/v004-ok/data.json",
                    "workspace_ref": "{workspace}/out.md",
                    "relative_ref": "docs/plan.md",
                },
            },
            "on_success": [
                {"type": "run_job", "job_path": "sibling.yaml", "description": "chain"}
            ],
        }
        config, config_path, raw = self._load(tmp_path, score)

        issues = self._check().check(config, config_path, raw)
        assert issues == [], str(issues)

    def test_registered_in_default_checks(self) -> None:
        """Wired-up requirement: V004 ships in the default check set, not as a
        dead class. A check absent from create_default_checks() never runs."""
        from marianne.validation.runner import create_default_checks

        # RED today: no V004 exists anywhere in the default set.
        check_ids = [check.check_id for check in create_default_checks()]
        assert "V004" in check_ids
        assert len(check_ids) == len(set(check_ids)), "check ids must stay unique"
