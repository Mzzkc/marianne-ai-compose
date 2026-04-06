"""Tests for issue-solver score template rendering.

Verifies that the issue-solver.yaml score loads correctly, has the expected
structure after fan-out expansion, and that all Jinja templates render without
errors for every concrete sheet.

Score structure:
    17 logical stages → 19 concrete sheets (stage 12 fans out to 3 reviewers).
    Stages  1-11 → Sheets  1-11  (1:1)
    Stage  12    → Sheets 12-14  (3 parallel reviewers)
    Stage  13    → Sheet  15     (review synthesis)
    Stage  14    → Sheet  16     (docs update)
    Stage  15    → Sheet  17     (final verification)
    Stage  16    → Sheet  18     (commit & push)
    Stage  17    → Sheet  19     (close issue + chain gate)
"""

from pathlib import Path

import jinja2
import pytest
import yaml

from marianne.core.config import JobConfig


SCORE_PATH = Path(__file__).parent.parent / "examples" / "issue-solver.yaml"


@pytest.fixture(scope="module")
def raw_yaml() -> dict:
    """Load the raw YAML before Pydantic expansion."""
    return yaml.safe_load(SCORE_PATH.read_text())


@pytest.fixture(scope="module")
def config() -> JobConfig:
    """Load the score through JobConfig (triggers fan-out expansion)."""
    return JobConfig.from_yaml(SCORE_PATH)


# ─── Test 1: Score loads ────────────────────────────────────────────────


class TestScoreLoads:
    """Score loads from YAML without errors."""

    def test_score_loads(self, config: JobConfig) -> None:
        assert config.name == "issue-solver"
        assert config.description is not None


# ─── Test 2: Total stages ──────────────────────────────────────────────


class TestTotalStages:
    """Score has 17 logical stages."""

    def test_total_stages(self, config: JobConfig) -> None:
        assert config.sheet.total_stages == 17


# ─── Test 3: Total sheets with fan-out ──────────────────────────────────


class TestTotalSheetsWithFanout:
    """After fan-out, score has 19 concrete sheets."""

    def test_total_sheets_with_fanout(self, config: JobConfig) -> None:
        assert config.sheet.total_sheets == 19
        assert config.sheet.total_items == 19


# ─── Test 4: Fan-out stage 12 ──────────────────────────────────────────


class TestFanOutStage12:
    """Stage 12 has fan_out: {12: 3} configured.

    After expansion fan_out is cleared, but fan_out_stage_map preserves
    the metadata. We also verify the raw YAML to confirm the source config.
    """

    def test_raw_yaml_has_fan_out(self, raw_yaml: dict) -> None:
        fan_out = raw_yaml["sheet"]["fan_out"]
        assert fan_out == {12: 3}

    def test_fan_out_stage_map_has_three_instances(self, config: JobConfig) -> None:
        """Sheets 12, 13, 14 all map to stage 12 with fan_count=3."""
        stage_map = config.sheet.fan_out_stage_map
        assert stage_map is not None

        for sheet_num in (12, 13, 14):
            meta = stage_map[sheet_num]
            assert meta["stage"] == 12
            assert meta["fan_count"] == 3

        # Instances are 1, 2, 3
        instances = [stage_map[s]["instance"] for s in (12, 13, 14)]
        assert sorted(instances) == [1, 2, 3]

    def test_fan_out_cleared_after_expansion(self, config: JobConfig) -> None:
        """fan_out dict is cleared to {} after expansion to prevent re-expansion."""
        assert config.sheet.fan_out == {}


# ─── Test 5: skip_when_command targets valid sheets ─────────────────────


class TestSkipWhenCommandTargetsValidSheets:
    """All skip_when_command keys are within the valid stage range."""

    def test_skip_when_command_targets_valid_sheets(
        self, raw_yaml: dict
    ) -> None:
        total_stages = raw_yaml["sheet"]["total_items"]  # 17 (pre-expansion)
        skip_keys = raw_yaml["sheet"]["skip_when_command"]
        for stage_num in skip_keys:
            assert 1 <= stage_num <= total_stages, (
                f"skip_when_command key {stage_num} is outside valid "
                f"stage range 1-{total_stages}"
            )

    def test_skip_when_command_has_expected_stages(self, raw_yaml: dict) -> None:
        """Stages 6-11 (phases 2-4 fix+completion) should have skip conditions."""
        skip_keys = set(raw_yaml["sheet"]["skip_when_command"].keys())
        assert skip_keys == {6, 7, 8, 9, 10, 11}


# ─── Test 6: Dependencies are valid ────────────────────────────────────


class TestDependenciesAreValid:
    """All dependency references are valid sheet numbers."""

    def test_raw_dependencies_reference_valid_stages(self, raw_yaml: dict) -> None:
        """Pre-expansion: dependency keys and values within 1..total_items."""
        total_stages = raw_yaml["sheet"]["total_items"]
        deps = raw_yaml["sheet"]["dependencies"]
        for stage, dep_list in deps.items():
            assert 1 <= stage <= total_stages, (
                f"Dependency key {stage} outside valid range"
            )
            for dep in dep_list:
                assert 1 <= dep <= total_stages, (
                    f"Stage {stage} depends on {dep}, outside valid range"
                )

    def test_expanded_dependencies_reference_valid_sheets(
        self, config: JobConfig
    ) -> None:
        """Post-expansion: all dependency keys and values within 1..total_sheets."""
        total_sheets = config.sheet.total_sheets
        deps = config.sheet.dependencies
        for sheet_num, dep_list in deps.items():
            assert 1 <= sheet_num <= total_sheets, (
                f"Expanded dep key {sheet_num} outside valid range 1-{total_sheets}"
            )
            for dep in dep_list:
                assert 1 <= dep <= total_sheets, (
                    f"Sheet {sheet_num} depends on sheet {dep}, "
                    f"outside valid range 1-{total_sheets}"
                )

    def test_no_self_dependencies(self, config: JobConfig) -> None:
        """No sheet depends on itself."""
        for sheet_num, dep_list in config.sheet.dependencies.items():
            assert sheet_num not in dep_list, (
                f"Sheet {sheet_num} has a self-dependency"
            )

    def test_dependency_chain_is_complete(self, raw_yaml: dict) -> None:
        """Every stage 2-17 has at least one dependency (linear chain)."""
        deps = raw_yaml["sheet"]["dependencies"]
        for stage in range(2, 18):
            assert stage in deps, f"Stage {stage} has no dependencies declared"
            assert len(deps[stage]) > 0, f"Stage {stage} has empty dependency list"


# ─── Test 7: Self-chain configured ─────────────────────────────────────


class TestSelfChainConfigured:
    """on_success hook present with self-chaining config."""

    def test_on_success_hook_present(self, config: JobConfig) -> None:
        assert len(config.on_success) >= 1

    def test_on_success_is_run_job(self, config: JobConfig) -> None:
        hook = config.on_success[0]
        assert hook.type == "run_job"

    def test_on_success_points_to_self(self, config: JobConfig) -> None:
        hook = config.on_success[0]
        assert hook.job_path is not None
        assert "issue-solver.yaml" in str(hook.job_path)

    def test_on_success_is_detached(self, config: JobConfig) -> None:
        hook = config.on_success[0]
        assert hook.detached is True

    def test_on_success_is_fresh(self, config: JobConfig) -> None:
        hook = config.on_success[0]
        assert hook.fresh is True


# ─── Test 8: Concert configured ────────────────────────────────────────


class TestConcertConfigured:
    """Concert config has reasonable depth."""

    def test_concert_enabled(self, config: JobConfig) -> None:
        assert config.concert.enabled is True

    def test_concert_has_reasonable_depth(self, config: JobConfig) -> None:
        assert config.concert.max_chain_depth >= 1
        assert config.concert.max_chain_depth <= 100

    def test_concert_max_depth_value(self, config: JobConfig) -> None:
        assert config.concert.max_chain_depth == 30

    def test_concert_cooldown_set(self, config: JobConfig) -> None:
        assert config.concert.cooldown_between_jobs_seconds > 0


# ─── Test 9: Workspace lifecycle configured ─────────────────────────────


class TestWorkspaceLifecycleConfigured:
    """Archive on fresh is enabled."""

    def test_archive_on_fresh_enabled(self, config: JobConfig) -> None:
        assert config.workspace_lifecycle.archive_on_fresh is True

    def test_max_archives_set(self, config: JobConfig) -> None:
        assert config.workspace_lifecycle.max_archives == 30


# ─── Test 10: Has validations ──────────────────────────────────────────


class TestHasValidations:
    """Score has validation rules."""

    def test_has_validations(self, config: JobConfig) -> None:
        assert len(config.validations) > 0

    def test_has_file_exists_validations(self, config: JobConfig) -> None:
        types = {v.type for v in config.validations}
        assert "file_exists" in types

    def test_has_command_succeeds_validations(self, config: JobConfig) -> None:
        types = {v.type for v in config.validations}
        assert "command_succeeds" in types

    def test_all_validations_have_descriptions(self, config: JobConfig) -> None:
        for v in config.validations:
            assert v.description is not None and v.description.strip() != "", (
                f"Validation of type '{v.type}' is missing a description"
            )


# ─── Test 11: Template renders for all sheets ──────────────────────────


class TestTemplateRendersForAllSheets:
    """Jinja template renders for every sheet number without errors.

    Uses jinja2.Undefined (default) so undefined variables produce empty
    strings instead of errors — we only validate template *syntax*, not
    that every variable is provided.
    """

    def test_template_renders_for_all_sheets(self, config: JobConfig) -> None:
        env = jinja2.Environment(
            undefined=jinja2.Undefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        template_str = config.prompt.template
        assert template_str is not None, "Score must have an inline template"

        template = env.from_string(template_str)

        # Build a context with common variables (values don't matter for
        # syntax testing — Undefined silently resolves missing vars).
        base_vars = {
            **config.prompt.variables,
            "total_sheets": config.sheet.total_sheets,
            "workspace": str(config.workspace),
        }

        for sheet_num in range(1, config.sheet.total_sheets + 1):
            meta = config.sheet.get_fan_out_metadata(sheet_num)
            ctx = {
                **base_vars,
                "sheet_num": sheet_num,
                "stage": meta.stage,
                "instance": meta.instance,
                "fan_count": meta.fan_count,
                "total_stages": config.sheet.total_stages,
                "start_item": sheet_num,
                "end_item": sheet_num,
                "previous_outputs": {},
                "previous_files": {},
                "stakes": "",
                "thinking_method": "",
            }
            try:
                rendered = template.render(**ctx)
            except jinja2.TemplateSyntaxError as exc:
                pytest.fail(
                    f"Template syntax error rendering sheet {sheet_num} "
                    f"(stage={meta.stage}, instance={meta.instance}): {exc}"
                )
            except Exception as exc:
                pytest.fail(
                    f"Unexpected error rendering sheet {sheet_num} "
                    f"(stage={meta.stage}, instance={meta.instance}): {exc}"
                )

            # Rendered output should be non-empty for every sheet
            assert rendered.strip(), (
                f"Template rendered empty for sheet {sheet_num} "
                f"(stage={meta.stage}, instance={meta.instance})"
            )

    def test_template_renders_each_stage_branch(self, config: JobConfig) -> None:
        """Each stage 1-17 produces unique content (hits a different if-branch)."""
        env = jinja2.Environment(
            undefined=jinja2.Undefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        assert config.prompt.template is not None, "Score must have a prompt template"
        template = env.from_string(config.prompt.template)

        base_vars = {
            **config.prompt.variables,
            "total_sheets": config.sheet.total_sheets,
            "workspace": str(config.workspace),
            "stakes": "",
            "thinking_method": "",
            "previous_outputs": {},
            "previous_files": {},
        }

        seen_stages: set[int] = set()
        for stage in range(1, 18):
            ctx = {
                **base_vars,
                "sheet_num": stage,
                "stage": stage,
                "instance": 1,
                "fan_count": 1,
                "total_stages": 17,
                "start_item": stage,
                "end_item": stage,
            }
            rendered = template.render(**ctx)
            assert f"STAGE {stage}" in rendered or f"Stage {stage}" in rendered.upper(), (
                f"Expected stage {stage} heading in rendered output"
            )
            seen_stages.add(stage)

        assert seen_stages == set(range(1, 18))

    def test_fan_out_instances_render_differently(self, config: JobConfig) -> None:
        """Stage 12 instances 1, 2, 3 each produce different reviewer content."""
        env = jinja2.Environment(
            undefined=jinja2.Undefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        assert config.prompt.template is not None
        template = env.from_string(config.prompt.template)

        base_vars = {
            **config.prompt.variables,
            "total_sheets": config.sheet.total_sheets,
            "workspace": str(config.workspace),
            "stakes": "",
            "thinking_method": "",
            "previous_outputs": {},
            "previous_files": {},
        }

        renders: dict[int, str] = {}
        for instance in range(1, 4):
            ctx = {
                **base_vars,
                "sheet_num": 11 + instance,  # sheets 12, 13, 14
                "stage": 12,
                "instance": instance,
                "fan_count": 3,
                "total_stages": 17,
                "start_item": 11 + instance,
                "end_item": 11 + instance,
            }
            renders[instance] = template.render(**ctx)

        # Each instance should produce distinct output
        assert renders[1] != renders[2], "Instance 1 and 2 should differ"
        assert renders[2] != renders[3], "Instance 2 and 3 should differ"
        assert renders[1] != renders[3], "Instance 1 and 3 should differ"

        # Verify each hits the correct reviewer branch
        assert "Functional" in renders[1], "Instance 1 should be Functional Reviewer"
        assert "E2E" in renders[2] or "Smoke" in renders[2], (
            "Instance 2 should be E2E/Smoke Tester"
        )
        assert "Code Quality" in renders[3], "Instance 3 should be Code Quality Reviewer"
