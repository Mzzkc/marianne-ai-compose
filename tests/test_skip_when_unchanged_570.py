"""Tests for Issue #570: skip_when unchanged predicate, pass ledger, and runtime wiring."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from marianne.core.config import JobConfig
from marianne.core.config.execution import SkipWhenCommand
from marianne.core.config.job import (
    InjectionCategory,
    InjectionItem,
    PromptConfig,
)
from marianne.core.sheet import Sheet
from marianne.daemon.baton import pass_ledger as pl
from marianne.daemon.baton import unchanged as uc
from marianne.daemon.baton.adapter import BatonAdapter
from marianne.daemon.baton.prompt import PromptRenderer
from marianne.daemon.baton.skip import evaluate_skip_command
from marianne.daemon.baton.state import AttemptContext, AttemptMode, SheetExecutionState
from marianne.daemon.manager import JobManager
from marianne.validation.checks.best_practices import SkipWhenArchiveFreshCheck
from marianne.validation.output_contract import _known_suppression_severities


class TestSkipWhenUnchangedModel:
    """Test model validation and serialization for SkipWhenCommand with unchanged predicate."""

    def test_unchanged_predicate_loads_and_roundtrips(self):
        rule = SkipWhenCommand(unchanged=True)
        assert rule.unchanged is True
        assert rule.command is None
        assert rule.description is None
        data = rule.model_dump(mode="json")
        restored = SkipWhenCommand.model_validate(data)
        assert restored.unchanged is True
        assert restored.command is None

    def test_command_predicate_unchanged(self):
        cmd = SkipWhenCommand(command="test -f marker")
        assert cmd.command == "test -f marker"
        assert cmd.unchanged is False
        assert cmd.timeout_seconds == 10.0

    def test_both_predicates_refused(self):
        with pytest.raises(ValidationError) as exc_info:
            SkipWhenCommand(command="exit 0", unchanged=True)
        msg = str(exc_info.value)
        assert "command" in msg and "unchanged" in msg

    def test_neither_predicate_refused(self):
        with pytest.raises(ValidationError):
            SkipWhenCommand()

    def test_unchanged_with_explicit_timeout_refused(self):
        with pytest.raises(ValidationError) as exc_info:
            SkipWhenCommand(unchanged=True, timeout_seconds=5)
        assert "timeout_seconds" in str(exc_info.value)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            SkipWhenCommand(unchanged=True, extra_field=123)

    def test_description_present(self):
        desc = SkipWhenCommand.model_fields["unchanged"].description or ""
        assert len(desc.strip()) > 0
        assert "GH #570" in desc

    def test_fan_out_expansion_copies_unchanged_rule(self, tmp_path):
        yaml_content = f"""
name: fanout-test
workspace: {tmp_path}/run
sheet:
  size: 1
  total_items: 2
  fan_out:
    1: 2
  skip_when:
    1:
      unchanged: true
      description: Skip voice if unchanged
prompt:
  template: "Run voice {{{{ voice }}}}"
"""
        config = JobConfig.from_yaml_string(yaml_content)
        # Stage 1 fan-out with 2 voices creates sheets 1 and 2
        assert 1 in config.sheet.skip_when
        assert 2 in config.sheet.skip_when
        assert config.sheet.skip_when[1].unchanged is True
        assert config.sheet.skip_when[2].unchanged is True


class TestSkipWhenArchiveFreshCheck:
    """Test V339 warning on archive_on_fresh + unchanged."""

    def test_v339_fires_on_planted_defect(self, tmp_path):
        yaml_content = f"""
name: v339-test
workspace: {tmp_path}/run
workspace_lifecycle:
  archive_on_fresh: true
sheet:
  size: 1
  total_items: 2
  skip_when:
    1:
      unchanged: true
prompt:
  template: "Hello"
"""
        config = JobConfig.from_yaml_string(yaml_content)
        check = SkipWhenArchiveFreshCheck()
        issues = check.check(config, tmp_path / "job.yaml", yaml_content)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.check_id == "V339"
        assert issue.severity.value == "warning"
        assert "archive_on_fresh" in issue.message
        assert "unchanged" in issue.message
        assert "workspace_lifecycle.preserve_patterns" in issue.suggestion

    def test_v339_silent_on_control(self, tmp_path):
        yaml_content = f"""
name: v339-control
workspace: {tmp_path}/run
sheet:
  size: 1
  total_items: 2
  skip_when:
    1:
      unchanged: true
prompt:
  template: "Hello"
"""
        config = JobConfig.from_yaml_string(yaml_content)
        check = SkipWhenArchiveFreshCheck()
        issues = check.check(config, tmp_path / "job.yaml", yaml_content)
        assert len(issues) == 0

    def test_v339_silent_on_command_rule_with_archive(self, tmp_path):
        yaml_content = f"""
name: v339-command
workspace: {tmp_path}/run
workspace_lifecycle:
  archive_on_fresh: true
sheet:
  size: 1
  total_items: 2
  skip_when:
    1:
      command: "exit 0"
prompt:
  template: "Hello"
"""
        config = JobConfig.from_yaml_string(yaml_content)
        check = SkipWhenArchiveFreshCheck()
        issues = check.check(config, tmp_path / "job.yaml", yaml_content)
        assert len(issues) == 0

    def test_v339_registered_severity(self):
        severities = _known_suppression_severities()
        assert "V339" in severities
        assert severities["V339"].value == "warning"


class TestSkipCommandEvaluatorNarrowing:
    """Test evaluate_skip_command fails open for unchanged rules."""

    @pytest.mark.asyncio
    async def test_fails_open_for_rule_without_command(self, tmp_path):
        skip, reason = await evaluate_skip_command(
            SkipWhenCommand(unchanged=True),
            workspace=tmp_path,
            context={},
        )
        assert skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_command_rule_still_skips(self, tmp_path):
        skip, reason = await evaluate_skip_command(
            SkipWhenCommand(command="exit 0"),
            workspace=tmp_path,
            context={},
        )
        assert skip is True
        assert "exit 0" in reason


class TestPassLedger:
    """Test PassLedger storage, atomic writes, and invalidation."""

    @pytest.mark.asyncio
    async def test_ledger_crud(self, tmp_path):
        root = pl.PassLedger.job_root(tmp_path, "scope-test")
        ledger = pl.PassLedger(root)

        assert await ledger.lookup(1, None) is None

        rec = pl.PassRecord(
            job_id="j",
            ledger_scope="scope-test",
            sheet_num=1,
            movement=1,
            voice=None,
            workspace=str(tmp_path),
            marianne_version="0.1.0",
            passed_at="2026-11-26T00:00:00Z",
            attempt=1,
            inputs={"prompt": "sha256:11", "preamble": "sha256:22"},
            sources=[
                {
                    "kind": "cadenza",
                    "path": "/p",
                    "resolved_path": "/p/a.md",
                    "sha256": "sha256:33",
                }
            ],
            products={str(tmp_path / "out.txt"): "sha256:44"},
            stdout_tail="output",
        )
        written_path = await ledger.write(rec)
        assert written_path.is_file()

        loaded = await ledger.lookup(1, None)
        assert loaded is not None
        assert loaded.inputs == rec.inputs
        assert loaded.products == rec.products
        assert loaded.stdout_tail == "output"

        # Key isolation
        assert await ledger.lookup(1, 1) is None
        assert await ledger.lookup(2, None) is None

        # Invalidate
        removed = await ledger.invalidate([(1, None)])
        assert removed == 1
        assert await ledger.lookup(1, None) is None

        # Clear
        await ledger.write(rec)
        assert root.exists()
        await ledger.clear()
        assert not root.exists()

    def test_scope_for(self):
        p1 = Path("/a/b/score.yaml")
        p2 = Path("/a/c/score.yaml")
        assert pl.scope_for(p1, "job1") == pl.scope_for(p1, "job2")
        assert pl.scope_for(p1, "job1") != pl.scope_for(p2, "job1")
        assert pl.scope_for(None, "job-fallback") == "job-fallback"


class TestUnchangedDigest:
    """Test input digest calculation, diff detection, and product hashing."""

    def test_digests_and_diffs(self, tmp_path):
        cadenza = tmp_path / "cadenza"
        cadenza.mkdir()
        (cadenza / "data.md").write_text("v1", encoding="utf-8")

        ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
        renderer = PromptRenderer(
            PromptConfig(), total_sheets=1, total_stages=1, parallel_enabled=False
        )

        sheet = Sheet(
            num=1, movement=1, voice=None, voice_count=1, workspace=tmp_path,
            instrument_name="test-cli", prompt_template="Prompt", timeout_seconds=60,
            stdout_to="out.txt",
            cadenza=[InjectionItem(directory=str(cadenza), **{"as": InjectionCategory.CONTEXT})],
        )

        d1 = uc.dispatch_digest(
            rendered=renderer.render(sheet, ctx),
            sheet=sheet,
            instrument="test-cli",
            model="m1",
            profile=None,
        )
        d2 = uc.dispatch_digest(
            rendered=renderer.render(sheet, ctx),
            sheet=sheet,
            instrument="test-cli",
            model="m1",
            profile=None,
        )
        assert d1 == d2
        assert uc.first_difference(d1, d2) is None

        (cadenza / "data.md").write_text("v2", encoding="utf-8")
        d3 = uc.dispatch_digest(
            rendered=renderer.render(sheet, ctx),
            sheet=sheet,
            instrument="test-cli",
            model="m1",
            profile=None,
        )
        assert d1 != d3
        assert uc.first_difference(d1, d3) == "prompt"

        (cadenza / "data.md").write_text("v1", encoding="utf-8")
        d4 = uc.dispatch_digest(
            rendered=renderer.render(sheet, ctx),
            sheet=sheet,
            instrument="test-cli",
            model="m2",
            profile=None,
        )
        assert uc.first_difference(d1, d4) == "model"


class TestUnchangedHousekeeping:
    """Test manager cleanup and adapter state pruning."""

    @pytest.mark.asyncio
    async def test_manager_clear_deletes_pass_ledger(self, tmp_path):
        scope = "scope-clear-test"
        root = pl.PassLedger.job_root(tmp_path, scope)
        root.mkdir(parents=True)
        (root / "m1.yaml").write_text("schema_version: 1\n", encoding="utf-8")

        await JobManager._delete_workspace_state("j", tmp_path, ledger_scope=scope)
        assert not root.exists()

    def test_deregister_drops_unchanged_maps(self, tmp_path):
        adapter = BatonAdapter()
        sheet = Sheet(
            num=1, movement=1, voice=None, voice_count=1, workspace=tmp_path,
            instrument_name="test-cli", prompt_template="Prompt", timeout_seconds=60,
        )
        adapter.register_job(
            "j", [sheet], {1: []},
            skip_when={1: SkipWhenCommand(unchanged=True)},
            ledger_scope="scope-1",
        )
        assert "j" in adapter._job_skip_unchanged
        assert "j" in adapter._job_unchanged_enabled
        assert "j" in adapter._job_ledger_scope

        adapter.deregister_job("j")
        assert "j" not in adapter._job_skip_unchanged
        assert "j" not in adapter._job_unchanged_enabled
        assert "j" not in adapter._job_ledger_scope


class TestUnchangedFunctionalReview:
    """Targeted functional verification tests written by the Functional Reviewer.

    Verifies:
    1. Happy path: end-to-end execution, pass recording, skip on identical rerun
    2. Edge cases: loops ignored, non-first attempt, raw_prompt, missing renderer,
       corrupt record handling, invalidation error handling
    3. Regression: bare string rejection pointing to mapping syntax
    """

    @pytest.fixture
    def setup_harness(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock

        from marianne.execution.base import ExecutionResult

        def make_backend(stdout="OUTPUT", ok=True):
            b = MagicMock()
            b.execute = AsyncMock(
                return_value=ExecutionResult(
                    success=ok,
                    stdout=stdout,
                    stderr="" if ok else "error",
                    duration_seconds=0.01,
                    exit_code=0 if ok else 1,
                )
            )
            return b

        def mount(adapter, *backends):
            pool = MagicMock()
            pool._registry = None
            pool.acquire = AsyncMock(side_effect=list(backends))
            pool.release = AsyncMock()
            adapter._backend_pool = pool

        def make_sheets(ws, count=1, tmpl="Execute sheet {{ sheet_num }}"):
            return [
                Sheet(
                    num=i,
                    movement=i,
                    voice=None,
                    voice_count=1,
                    workspace=ws,
                    instrument_name="test-cli",
                    prompt_template=tmpl,
                    timeout_seconds=60,
                    stdout_to=f"out{i}.txt",
                )
                for i in range(1, count + 1)
            ]

        async def dispatch(adapter, sheet_num, attempt_num=1, mode=AttemptMode.NORMAL):
            ok = await adapter._dispatch_callback(
                "job-test",
                sheet_num,
                SheetExecutionState(
                    sheet_num=sheet_num,
                    instrument_name="test-cli",
                    max_retries=3,
                    attempt_number=attempt_num,
                    mode=mode,
                ),
            )
            import asyncio
            await asyncio.gather(*adapter._active_tasks.values(), return_exceptions=True)
            events = []
            while not adapter._baton.inbox.empty():
                events.append(adapter._baton.inbox.get_nowait())
            return ok, events

        async def settle(adapter, events):
            for event in events:
                await adapter._baton.handle_event(event)
                await adapter._record_pass_on_event(event)

        return make_backend, mount, make_sheets, dispatch, settle

    @pytest.mark.asyncio
    async def test_happy_path_record_and_skip(self, tmp_path, setup_harness):
        from marianne.daemon.baton.events import SheetSkipped
        from marianne.daemon.baton.state import BatonSheetStatus

        make_backend, mount, make_sheets, dispatch, settle = setup_harness
        adapter = BatonAdapter()
        sheets = make_sheets(tmp_path)
        rule = {1: SkipWhenCommand(unchanged=True)}
        prods = {1: ["{workspace}/out1.txt"]}

        # Pass 1: fresh run executes backend and completes
        adapter.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=PromptConfig(),
            skip_when=rule,
            declared_products=prods,
            ledger_scope="scope-test",
        )
        b1 = make_backend("FIRST")
        mount(adapter, b1)
        ok, ev = await dispatch(adapter, 1)
        assert ok is True
        assert b1.execute.await_count == 1
        await settle(adapter, ev)
        assert adapter._baton._jobs["job-test"].sheets[1].status is BatonSheetStatus.COMPLETED

        # Check ledger record written
        root = pl.PassLedger.job_root(tmp_path, "scope-test")
        ledger = pl.PassLedger(root)
        rec = await ledger.lookup(1, None)
        assert rec is not None
        assert rec.stdout_tail == "FIRST"

        # Pass 2: fresh run with identical inputs skips sheet without backend execution
        adapter2 = BatonAdapter()
        adapter2.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=PromptConfig(),
            skip_when=rule,
            declared_products=prods,
            ledger_scope="scope-test",
        )
        b2 = make_backend("SECOND")
        mount(adapter2, b2)
        ok2, ev2 = await dispatch(adapter2, 1)
        assert ok2 is True
        assert b2.execute.await_count == 0  # Did not call backend!

        skipped = [e for e in ev2 if isinstance(e, SheetSkipped) and e.sheet_num == 1]
        assert len(skipped) == 1
        assert skipped[0].reason.startswith("unchanged since it last passed")
        await settle(adapter2, ev2)
        assert adapter2._baton._jobs["job-test"].sheets[1].status is BatonSheetStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_edge_case_loop_member_ignored(self, tmp_path, setup_harness):
        make_backend, mount, make_sheets, dispatch, settle = setup_harness
        from marianne.core.config.flow import LoopConfig

        adapter = BatonAdapter()
        sheets = make_sheets(tmp_path)
        rule = {1: SkipWhenCommand(unchanged=True)}

        # Job registers with sheet 1 inside a loop span
        adapter.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=PromptConfig(),
            skip_when=rule,
            loops={"1": LoopConfig(count=2, index="i")},
            declared_products={1: ["{workspace}/out1.txt"]},
            ledger_scope="scope-test",
        )
        # Sheet 1 unchanged rule should have been dropped
        assert 1 not in adapter._job_skip_unchanged.get("job-test", {})

        b = make_backend("LOOP")
        mount(adapter, b)
        ok, ev = await dispatch(adapter, 1)
        assert ok is True
        assert b.execute.await_count == 1  # Always executes

    @pytest.mark.asyncio
    async def test_edge_case_not_first_attempt(self, tmp_path, setup_harness):
        make_backend, mount, make_sheets, dispatch, settle = setup_harness
        adapter = BatonAdapter()
        sheets = make_sheets(tmp_path)
        rule = {1: SkipWhenCommand(unchanged=True)}
        prods = {1: ["{workspace}/out1.txt"]}

        # Pre-seed a valid record
        root = pl.PassLedger.job_root(tmp_path, "scope-test")
        ledger = pl.PassLedger(root)
        (tmp_path / "out1.txt").write_text("done", encoding="utf-8")
        rec = pl.PassRecord(
            job_id="job-test",
            ledger_scope="scope-test",
            sheet_num=1,
            movement=1,
            voice=None,
            workspace=str(tmp_path),
            marianne_version="0.1.0",
            passed_at="2026-11-26T00:00:00Z",
            attempt=1,
            inputs={"prompt": "sha256:dummy"},
            products={str(tmp_path / "out1.txt"): uc.sha256_file(tmp_path / "out1.txt")},
            stdout_tail="OLD",
        )
        await ledger.write(rec)

        adapter.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=PromptConfig(),
            skip_when=rule,
            declared_products=prods,
            ledger_scope="scope-test",
        )
        b = make_backend("RETRY")
        mount(adapter, b)

        # Attempt 2 (e.g. retry) should NOT skip even if record exists
        ok, ev = await dispatch(adapter, 1, attempt_num=2, mode=AttemptMode.NORMAL)
        assert ok is True
        assert b.execute.await_count == 1  # Executes because not first attempt

    @pytest.mark.asyncio
    async def test_edge_case_raw_prompt_profile(self, tmp_path, setup_harness):
        make_backend, mount, make_sheets, dispatch, settle = setup_harness
        adapter = BatonAdapter()
        sheets = make_sheets(tmp_path)
        rule = {1: SkipWhenCommand(unchanged=True)}

        adapter.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=PromptConfig(),
            skip_when=rule,
            declared_products={1: ["{workspace}/out1.txt"]},
            ledger_scope="scope-test",
        )
        b = make_backend("RAW")
        # Mount with raw_prompt profile
        pool = setup_harness[1]
        raw_profile = MagicMock(raw_prompt=True)
        raw_registry = MagicMock()
        raw_registry.resolve_profile.return_value = raw_profile
        pool = MagicMock()
        pool._registry = raw_registry
        pool.acquire = AsyncMock(return_value=b)
        pool.release = AsyncMock()
        adapter._backend_pool = pool

        ok, ev = await dispatch(adapter, 1)
        assert ok is True
        assert b.execute.await_count == 1  # Fails open for raw_prompt

    @pytest.mark.asyncio
    async def test_edge_case_missing_renderer(self, tmp_path, setup_harness):
        make_backend, mount, make_sheets, dispatch, settle = setup_harness
        adapter = BatonAdapter()
        sheets = make_sheets(tmp_path)
        rule = {1: SkipWhenCommand(unchanged=True)}

        # No prompt_config provided
        adapter.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=None,
            skip_when=rule,
            declared_products={1: ["{workspace}/out1.txt"]},
            ledger_scope="scope-test",
        )
        b = make_backend("NO_RENDERER")
        mount(adapter, b)
        ok, ev = await dispatch(adapter, 1)
        assert ok is True
        assert b.execute.await_count == 1  # Fails open when renderer absent

    @pytest.mark.asyncio
    async def test_edge_case_corrupt_record_on_disk(self, tmp_path, setup_harness):
        make_backend, mount, make_sheets, dispatch, settle = setup_harness
        adapter = BatonAdapter()
        sheets = make_sheets(tmp_path)
        rule = {1: SkipWhenCommand(unchanged=True)}

        # Write corrupt YAML in ledger
        root = pl.PassLedger.job_root(tmp_path, "scope-test")
        root.mkdir(parents=True)
        (root / "m1.yaml").write_text("invalid: [yaml: unclosed", encoding="utf-8")

        adapter.register_job(
            "job-test",
            sheets,
            {1: []},
            prompt_config=PromptConfig(),
            skip_when=rule,
            declared_products={1: ["{workspace}/out1.txt"]},
            ledger_scope="scope-test",
        )
        b = make_backend("CORRUPT_FALLBACK")
        mount(adapter, b)
        ok, ev = await dispatch(adapter, 1)
        assert ok is True
        assert b.execute.await_count == 1  # Fails open on corrupt record

    def test_regression_bare_string_rejected_with_guidance(self):
        yaml_content = """
name: test-bare
sheet:
  size: 1
  total_items: 1
  skip_when:
    1: unchanged
prompt:
  template: "Run"
"""
        with pytest.raises(ValidationError) as exc_info:
            JobConfig.from_yaml_string(yaml_content)
        msg = str(exc_info.value)
        assert "unchanged" in msg
        assert "GH #570" in msg or "{unchanged: true}" in msg
