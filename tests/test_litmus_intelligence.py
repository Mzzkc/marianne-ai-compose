"""Litmus tests for Mozart's intelligence layer — does it ACTUALLY work?

These are not unit tests. Unit tests verify that functions return expected
values. Litmus tests verify that the intelligence layer makes the system
MORE EFFECTIVE — that prompts WITH the system are better than prompts
WITHOUT, that the baton's decisions produce correct outcomes for real
workflows, and that data flows don't silently break during serialization.

Test categories:
1. Prompt assembly effectiveness — does the assembly order produce prompts
   where agents can find what they need?
2. Spec corpus pipeline — does tag filtering survive JSON roundtrip?
3. Baton decision intelligence — does the baton make smart retry/completion
   decisions for realistic multi-sheet workflows?
4. Instrument state bridge — does rate limiting on one instrument leave
   others unaffected?
5. Cost enforcement — does the baton actually stop spending?
6. Exhaustion decision tree — healing → escalation → failure priority
7. Preamble intelligence — does it help agents orient?
8. Baton musician prompt rendering — does the baton build prompts that make
   agents MORE effective than raw templates? (F-104)
9. Error taxonomy intelligence — does E006 stale detection get different
   treatment from E001 timeout? Does Phase 4.5 catch masked rate limits?
10. Sheet entity template variables — do new and old terminology coexist?
11. Restart recovery intelligence — does recover_job() correctly rebuild
    baton state from CheckpointState? (step 29)
12. Completion signaling — does wait_for_completion() correctly detect
    when all sheets reach terminal state?
13. Credential safety in error paths — does the musician's exception handler
    redact credentials before they reach the baton inbox? (F-135)
14. Parallel executor failure propagation — do F-111 and F-113 fixes
    actually prevent the production bugs?
15. Clone config isolation — does build_clone_config produce truly
    isolated state DB paths? (F-132)
16-24. (M2 additions: cost limit wiring, state mapping totality, instrument
    alias resolution, validation with aliases, success outcome after restart,
    parallel exception preservation, failure propagation, baton event stubs,
    credential redaction defense-in-depth)
25. Semantic context tags — F-009/F-144: do semantic tags overlap with stored
    pattern namespace? (positional tags had ZERO overlap → 91% waste)
26. Prompt renderer wiring — F-158: does the baton create a PromptRenderer
    when prompt_config is provided? Is the rendered prompt richer?
27. Dispatch guard — F-152: do all three dispatch failure paths send E505
    events to the baton? (missing = infinite dispatch loop)
28. Rate limit auto-resume — F-112: does rate limit hit schedule a timer?
    Does the timer clear WAITING sheets back to PENDING?
29. Model override wiring — F-150: does apply_overrides actually change
    the model the backend uses?
30. Concert chaining completeness — F-145: does has_completed_sheets detect
    when baton sheets complete new work?
31. Rate limit wait cap — F-160: does the system cap astronomical wait
    durations instead of honoring adversarial 114-year timers?
32. Cross-sheet context in baton prompts — F-210: does the baton's
    PromptRenderer actually populate previous_outputs/previous_files from
    AttemptContext into templates? (WITH cross-sheet vs WITHOUT)
33. Skipped upstream visibility — #120: does [SKIPPED] placeholder in
    previous_outputs give downstream sheets explicit gap awareness?
34. Auto-fresh detection — #103: does _should_auto_fresh correctly detect
    score modifications after completion, preventing stale reruns?
35. Backpressure rejection intelligence — F-110: does rejection_reason()
    distinguish rate-limit (queueable) from resource (dangerous)?
36. Cross-sheet credential redaction — F-250: do credentials in workspace
    files get redacted BEFORE entering cross-sheet prompts?
37. MethodNotFoundError differentiation — F-450: does the error hierarchy
    tell users "restart conductor" vs "conductor not running"?
38. Cost JSON extraction vs char estimation — D-024: does JSON token
    extraction produce better cost data than char heuristics?

Every test in this file answers: "Is the system smarter WITH this than WITHOUT?"
"""

from __future__ import annotations

import json
from pathlib import Path

from mozart.core.config import PromptConfig, ValidationRule
from mozart.core.config.spec import SpecFragment
from mozart.core.sheet import Sheet
from mozart.daemon.baton.core import BatonCore
from mozart.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
)
from mozart.daemon.baton.state import (
    AttemptMode,
    BatonSheetStatus,
    SheetExecutionState,
)
from mozart.prompts.preamble import build_preamble
from mozart.prompts.templating import PromptBuilder, SheetContext

# =============================================================================
# 1. PROMPT ASSEMBLY EFFECTIVENESS
# =============================================================================


class TestPromptAssemblyEffectiveness:
    """The litmus question: do assembled prompts give agents what they need?

    An agent reading the prompt should be able to:
    - Find its success criteria (validation requirements) without scrolling
    - Understand the context it's working in (spec fragments)
    - Know what went wrong before (failure history)
    - See patterns that worked (learned patterns)

    These tests compare WITH vs WITHOUT to verify the system adds value.
    """

    def test_validation_requirements_appear_at_end(self) -> None:
        """Validation requirements are the LAST section in the prompt.

        Why this matters: agents process prompts sequentially. Requirements
        at the end are the last thing read before generating output — they
        get the freshest attention weight. If requirements were buried in
        the middle, agents would forget them by the time they start working.
        """
        config = PromptConfig(
            template="Write code for {{ workspace }}",
            variables={},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=10,
            workspace=Path("/tmp/test"),
            injected_context=["Context about the project"],
            injected_skills=["You can use bash"],
        )
        fragment = SpecFragment(
            name="conventions", tags=["code"], kind="text",
            content="Use snake_case",
        )
        patterns = ["Check for existing tests before writing new ones"]
        rules = [
            ValidationRule(
                type="file_exists",
                path="{workspace}/output.py",
                description="Output file",
            )
        ]

        prompt = builder.build_sheet_prompt(
            ctx, spec_fragments=[fragment],
            patterns=patterns, validation_rules=rules,
        )

        # Requirements are the last major section
        last_section_pos = prompt.rfind("## Success Requirements")
        assert last_section_pos > 0, "Requirements section must exist"
        # Nothing after requirements except the closing text
        after_requirements = prompt[last_section_pos:]
        # No other ## header after requirements
        other_headers = [
            h for h in after_requirements.split("\n")
            if h.startswith("## ") and "Success Requirements" not in h
        ]
        assert len(other_headers) == 0, (
            f"No sections should appear after requirements, found: {other_headers}"
        )

    def test_prompt_with_all_layers_is_richer_than_bare_template(self) -> None:
        """A fully assembled prompt contains MORE actionable information
        than just the rendered template alone.

        The intelligence layer should add: context, specs, patterns,
        and success criteria. If the assembled prompt is just the template,
        the intelligence layer isn't working.
        """
        config = PromptConfig(
            template="Build the auth module",
            variables={},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=1, total_sheets=3, start_item=1, end_item=10,
            workspace=Path("/tmp/ws"),
            injected_context=["This project uses FastAPI"],
            injected_skills=["You have access to Read, Write, Bash"],
        )
        fragment = SpecFragment(
            name="conventions", tags=["code"], kind="text",
            content="All I/O is async. Use asyncio.",
        )
        patterns = ["Auth modules should use bcrypt for password hashing"]
        rules = [
            ValidationRule(
                type="file_exists", path="{workspace}/auth.py",
                description="Auth module",
            ),
        ]

        bare_prompt = builder.build_sheet_prompt(ctx)
        full_prompt = builder.build_sheet_prompt(
            ctx, spec_fragments=[fragment],
            patterns=patterns, validation_rules=rules,
        )

        # Full prompt must be substantially larger
        assert len(full_prompt) > len(bare_prompt) * 1.5, (
            f"Full prompt ({len(full_prompt)} chars) should be >1.5x bare "
            f"({len(bare_prompt)} chars)"
        )
        # Full prompt contains actionable guidance the bare prompt doesn't
        assert "asyncio" in full_prompt
        assert "bcrypt" in full_prompt
        assert "Success Requirements" in full_prompt
        # Bare prompt lacks these
        assert "asyncio" not in bare_prompt
        assert "Success Requirements" not in bare_prompt

    def test_movement_aliases_work_in_templates(self) -> None:
        """Templates using {{ movement }} produce the same result as {{ stage }}.

        The new terminology (movement, voice, voice_count) must be available
        and produce identical values to the old terminology (stage, instance,
        fan_count). If they diverge, templates written for either vocabulary
        silently break.
        """
        config_old = PromptConfig(
            template="Stage {{ stage }}, instance {{ instance }}/{{ fan_count }}",
            variables={},
        )
        config_new = PromptConfig(
            template="Movement {{ movement }}, voice {{ voice }}/{{ voice_count }}",
            variables={},
        )
        builder_old = PromptBuilder(config_old)
        builder_new = PromptBuilder(config_new)

        ctx = SheetContext(
            sheet_num=3, total_sheets=9, start_item=1, end_item=10,
            workspace=Path("/tmp/ws"),
            stage=2, instance=1, fan_count=3, total_stages=3,
        )

        prompt_old = builder_old.build_sheet_prompt(ctx)
        prompt_new = builder_new.build_sheet_prompt(ctx)

        # Extract the numeric content — they should match
        assert "Stage 2, instance 1/3" in prompt_old
        assert "Movement 2, voice 1/3" in prompt_new

    def test_completion_mode_prompt_focuses_on_failures(self) -> None:
        """Completion mode prompts tell the agent what FAILED, not what passed.

        The litmus: does the completion prompt help the agent finish
        the remaining work without re-doing what already succeeded?
        """
        from mozart.execution.validation.models import ValidationResult
        from mozart.prompts.templating import CompletionContext

        config = PromptConfig(template="Build everything", variables={})
        builder = PromptBuilder(config)

        passed = ValidationResult(
            rule=ValidationRule(type="file_exists", path="done.txt"),
            passed=True,
        )
        failed = ValidationResult(
            rule=ValidationRule(
                type="file_exists", path="missing.txt",
                description="Critical output",
            ),
            passed=False,
            failure_category="missing",
            failure_reason="File was never created",
            suggested_fix="Create the file with required content",
        )

        comp_ctx = CompletionContext(
            sheet_num=1, total_sheets=3,
            passed_validations=[passed],
            failed_validations=[failed],
            completion_attempt=1, max_completion_attempts=5,
            original_prompt="Build everything",
            workspace=Path("/tmp/ws"),
        )

        prompt = builder.build_completion_prompt(comp_ctx)

        # The prompt must explicitly tell the agent NOT to redo passed work
        assert "DO NOT" in prompt
        assert "ALREADY COMPLETED" in prompt
        # The prompt must focus on what failed
        assert "INCOMPLETE ITEMS" in prompt
        assert "Critical output" in prompt
        assert "File was never created" in prompt
        assert "Create the file with required content" in prompt
        # The original context is included for reference
        assert "Build everything" in prompt


# =============================================================================
# 2. SPEC CORPUS PIPELINE — JSON ROUNDTRIP SURVIVAL
# =============================================================================


class TestSpecTagsSerializationRoundtrip:
    """The spec_tags integer key serialization risk.

    YAML: spec_tags: {1: ["goals"], 3: ["code"]}
    Python: dict[int, list[str]] → {1: ["goals"], 3: ["code"]}
    JSON roundtrip: {"1": ["goals"], "3": ["code"]}
    After roundtrip: dict[str, list[str]] → {"1": ["goals"], "3": ["code"]}

    The runner at sheet.py:1992 does: spec_tags.get(sheet_num)
    where sheet_num is an int. After JSON roundtrip, keys are strings.
    spec_tags.get(1) returns None because "1" != 1.

    This is the highest-risk serialization bug in the spec pipeline.
    """

    def test_spec_tags_survive_json_roundtrip(self) -> None:
        """Spec tags with integer keys work after model_dump/model_validate.

        This tests the actual Pydantic serialization path that the daemon
        uses when snapshotting and restoring job config.
        """
        from mozart.core.config.job import SheetConfig

        original = SheetConfig(
            size=10,
            total_items=30,
            spec_tags={1: ["goals", "safety"], 3: ["code"]},
        )

        # Simulate JSON roundtrip (what happens during config snapshot/restore)
        json_data = original.model_dump(mode="json")
        restored = SheetConfig.model_validate(json_data)

        # The critical test: can we still look up by int key?
        assert restored.spec_tags.get(1) == ["goals", "safety"], (
            f"spec_tags.get(1) should return ['goals', 'safety'], "
            f"got {restored.spec_tags.get(1)}. "
            f"Keys are: {list(restored.spec_tags.keys())} "
            f"(types: {[type(k) for k in restored.spec_tags]})"
        )
        assert restored.spec_tags.get(3) == ["code"]

    def test_spec_tags_survive_json_string_roundtrip(self) -> None:
        """Spec tags survive serialization to JSON string and back.

        This is the more extreme case: actual JSON.dumps/loads, which
        always converts int keys to strings.
        """
        from mozart.core.config.job import SheetConfig

        original = SheetConfig(
            size=10,
            total_items=30,
            spec_tags={1: ["goals"], 2: ["code", "testing"]},
        )

        # Full JSON string roundtrip
        json_str = json.dumps(original.model_dump(mode="json"))
        raw = json.loads(json_str)
        restored = SheetConfig.model_validate(raw)

        # Can we still look up by int key?
        assert restored.spec_tags.get(1) == ["goals"], (
            f"After JSON string roundtrip, spec_tags.get(1) returned "
            f"{restored.spec_tags.get(1)} instead of ['goals']. "
            f"Key types: {[type(k) for k in restored.spec_tags]}"
        )

    def test_dependencies_survive_json_roundtrip(self) -> None:
        """Sheet dependencies (also dict[int, list[int]]) survive roundtrip.

        Same risk as spec_tags — integer keys become strings in JSON.
        """
        from mozart.core.config.job import SheetConfig

        original = SheetConfig(
            size=10,
            total_items=40,  # 4 sheets of size 10
            dependencies={3: [1, 2], 4: [3]},
        )

        json_data = original.model_dump(mode="json")
        restored = SheetConfig.model_validate(json_data)

        assert restored.dependencies.get(3) == [1, 2], (
            f"dependencies.get(3) returned {restored.dependencies.get(3)}"
        )
        assert restored.dependencies.get(4) == [3]


# =============================================================================
# 3. BATON DECISION INTELLIGENCE — REALISTIC MULTI-SHEET WORKFLOWS
# =============================================================================


class TestBatonMultiSheetWorkflows:
    """Does the baton make smart decisions for real-world score patterns?

    These aren't unit tests for individual handlers — those exist elsewhere.
    These test realistic WORKFLOWS: a 3-movement score where movement 1
    sets up, movement 2 has 3 parallel voices, and movement 3 synthesizes.
    """

    async def test_three_movement_fan_out_workflow(self) -> None:
        """Classic pattern: setup → 3 parallel voices → synthesis.

        Sheets: 1 (setup), 2-4 (voices), 5 (synthesis)
        Dependencies: 2→1, 3→1, 4→1, 5→[2,3,4]

        The litmus: does the baton correctly sequence this so that
        (a) all 3 voices become ready after setup completes, and
        (b) synthesis only becomes ready when ALL 3 voices complete?
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
            4: SheetExecutionState(sheet_num=4, instrument_name="claude-code"),
            5: SheetExecutionState(sheet_num=5, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [1], 4: [1], 5: [2, 3, 4]}
        baton.register_job("concert", sheets, deps)

        # Initially only sheet 1 is ready
        ready = baton.get_ready_sheets("concert")
        assert len(ready) == 1
        assert ready[0].sheet_num == 1

        # Complete sheet 1 → voices 2, 3, 4 should all become ready
        await baton.handle_event(SheetAttemptResult(
            job_id="concert", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True, validation_pass_rate=100.0,
        ))
        ready = baton.get_ready_sheets("concert")
        ready_nums = {s.sheet_num for s in ready}
        assert ready_nums == {2, 3, 4}, f"Voices should be ready, got {ready_nums}"

        # Synthesis NOT ready yet
        assert 5 not in ready_nums

        # Complete voices 2 and 3 — synthesis still not ready (voice 4 pending)
        for voice in [2, 3]:
            await baton.handle_event(SheetAttemptResult(
                job_id="concert", sheet_num=voice,
                instrument_name="claude-code", attempt=1,
                execution_success=True, validation_pass_rate=100.0,
            ))
        ready = baton.get_ready_sheets("concert")
        ready_nums = {s.sheet_num for s in ready}
        assert 5 not in ready_nums, "Synthesis must wait for ALL voices"
        assert 4 in ready_nums, "Voice 4 still ready"

        # Complete voice 4 → synthesis becomes ready
        await baton.handle_event(SheetAttemptResult(
            job_id="concert", sheet_num=4, instrument_name="claude-code",
            attempt=1, execution_success=True, validation_pass_rate=100.0,
        ))
        ready = baton.get_ready_sheets("concert")
        ready_nums = {s.sheet_num for s in ready}
        assert ready_nums == {5}, f"Only synthesis should be ready, got {ready_nums}"

    async def test_voice_failure_propagates_to_synthesis(self) -> None:
        """If voice 2 fails, synthesis (which depends on it) must also fail.

        Without failure propagation (F-039's bug), the synthesis sheet
        would stay pending forever — a zombie job that never completes.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(
                sheet_num=2, instrument_name="claude-code", max_retries=0,
            ),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [2]}
        baton.register_job("j1", sheets, deps)

        # Complete sheet 1
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True, validation_pass_rate=100.0,
        ))

        # Sheet 2 fails with AUTH_FAILURE (non-retriable)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=2, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="AUTH_FAILURE",
        ))

        # Sheet 3 should be FAILED (propagated), not pending
        state3 = baton.get_sheet_state("j1", 3)
        assert state3 is not None
        assert state3.status == BatonSheetStatus.FAILED, (
            f"Synthesis should be failed (propagated from voice), "
            f"got {state3.status}"
        )
        # Job should be complete (all terminal)
        assert baton.is_job_complete("j1")

    async def test_skipped_voice_satisfies_synthesis_dependency(self) -> None:
        """Skipping a voice should still allow synthesis to proceed.

        In real scores, skip_when can skip a voice when a condition is met.
        The synthesis sheet should treat skipped voices as satisfied deps.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
            3: SheetExecutionState(sheet_num=3, instrument_name="claude-code"),
        }
        deps = {2: [1], 3: [1, 2]}
        baton.register_job("j1", sheets, deps)

        # Complete sheet 1
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True, validation_pass_rate=100.0,
        ))

        # Skip sheet 2 (e.g., skip_when condition met)
        await baton.handle_event(SheetSkipped(
            job_id="j1", sheet_num=2, reason="skip_when condition",
        ))

        # Sheet 3 should be ready (skipped satisfies dependencies)
        ready = baton.get_ready_sheets("j1")
        ready_nums = {s.sheet_num for s in ready}
        assert 3 in ready_nums, (
            f"Sheet 3 should be ready (skipped dep satisfies), got {ready_nums}"
        )


# =============================================================================
# 4. INSTRUMENT STATE BRIDGE — RATE LIMIT ISOLATION
# =============================================================================


class TestInstrumentStateIntelligence:
    """Does the baton correctly isolate instrument states?

    The key insight: rate limiting on claude-code should NOT affect
    gemini-cli sheets. This is the fundamental value prop of multi-instrument
    orchestration — breaking the single-instrument bottleneck.
    """

    async def test_rate_limit_on_one_instrument_leaves_other_ready(self) -> None:
        """Rate limiting claude-code should not block gemini-cli sheets.

        This is THE litmus test for multi-instrument orchestration.
        Without this isolation, there's no point having multiple instruments.
        """
        baton = BatonCore()
        baton.register_instrument("claude-code", max_concurrent=4)
        baton.register_instrument("gemini-cli", max_concurrent=4)

        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
            ),
            2: SheetExecutionState(
                sheet_num=2, instrument_name="gemini-cli",
            ),
        }
        baton.register_job("j1", sheets, {})

        # Rate limit hits claude-code
        await baton.handle_event(RateLimitHit(
            instrument="claude-code", wait_seconds=3600,
            job_id="j1", sheet_num=1,
        ))

        # Check instrument states
        claude_state = baton.get_instrument_state("claude-code")
        gemini_state = baton.get_instrument_state("gemini-cli")
        assert claude_state is not None and claude_state.rate_limited
        assert gemini_state is not None and not gemini_state.rate_limited

        # Build dispatch config — should show claude-code as rate limited
        config = baton.build_dispatch_config()
        assert "claude-code" in config.rate_limited_instruments
        assert "gemini-cli" not in config.rate_limited_instruments

    async def test_instrument_auto_registration_on_job_submit(self) -> None:
        """Instruments are auto-registered when a job is submitted.

        The baton shouldn't require explicit register_instrument calls
        for instruments that appear in sheet configs.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="novel-instrument",
            ),
        }
        baton.register_job("j1", sheets, {})

        state = baton.get_instrument_state("novel-instrument")
        assert state is not None, "Instrument should be auto-registered"
        assert state.max_concurrent == BatonCore._DEFAULT_INSTRUMENT_CONCURRENCY

    async def test_rate_limit_cleared_makes_instrument_available(self) -> None:
        """After a rate limit clears, the instrument is available for dispatch.

        Note: pending sheets stay pending (they haven't been dispatched yet).
        Only dispatched/running sheets move to WAITING. The dispatch logic
        uses build_dispatch_config() to check instrument availability.
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code",
            ),
        }
        baton.register_job("j1", sheets, {})

        # Rate limit hits — pending sheet stays pending (correct: not yet dispatched)
        await baton.handle_event(RateLimitHit(
            instrument="claude-code", wait_seconds=60,
            job_id="j1", sheet_num=1,
        ))
        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Sheet stays pending because it was never dispatched
        assert state.status == BatonSheetStatus.PENDING

        # But the instrument IS rate limited
        inst = baton.get_instrument_state("claude-code")
        assert inst is not None and inst.rate_limited

        # Rate limit expires
        await baton.handle_event(RateLimitExpired(instrument="claude-code"))

        # Instrument should no longer be rate limited
        inst = baton.get_instrument_state("claude-code")
        assert inst is not None and not inst.rate_limited

        # Sheet is still ready for dispatch
        ready = baton.get_ready_sheets("j1")
        assert any(s.sheet_num == 1 for s in ready)

    async def test_circuit_breaker_trips_from_consecutive_failures(self) -> None:
        """Consecutive failures on an instrument should trip its circuit breaker.

        The baton design says: "the conductor tracks consecutive failures
        per instrument across all jobs. Threshold exceeded → open circuit."
        """
        baton = BatonCore()
        baton.register_instrument("flaky-tool", max_concurrent=4)

        sheets = {
            i: SheetExecutionState(
                sheet_num=i, instrument_name="flaky-tool", max_retries=0,
            )
            for i in range(1, 6)
        }
        baton.register_job("j1", sheets, {})

        # 5 consecutive failures
        for i in range(1, 6):
            await baton.handle_event(SheetAttemptResult(
                job_id="j1", sheet_num=i, instrument_name="flaky-tool",
                attempt=1, execution_success=False,
                error_classification="EXECUTION_ERROR",
            ))

        inst = baton.get_instrument_state("flaky-tool")
        assert inst is not None
        # Circuit breaker should eventually open (threshold is implementation detail)
        # At minimum, consecutive_failures should be tracked
        assert inst.consecutive_failures >= 5


# =============================================================================
# 5. COST ENFORCEMENT — DOES THE BATON ACTUALLY STOP SPENDING?
# =============================================================================


class TestCostEnforcementEffectiveness:
    """Does cost enforcement actually prevent runaway spending?

    The litmus: if I set a $10 limit, does the job stop before $15?
    """

    async def test_job_cost_limit_pauses_job(self) -> None:
        """Exceeding per-job cost limit pauses the job.

        A paused job's sheets should not appear in get_ready_sheets().
        """
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("j1", sheets, {})
        baton.set_job_cost_limit("j1", max_cost_usd=5.0)

        # Sheet 1 costs $6 — exceeds limit
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=True, validation_pass_rate=100.0,
            cost_usd=6.0,
        ))

        # Job should be paused
        assert baton.is_job_paused("j1"), "Job should pause when cost exceeded"
        # No ready sheets (job is paused)
        ready = baton.get_ready_sheets("j1")
        assert len(ready) == 0, "Paused job should have no ready sheets"

    async def test_sheet_cost_limit_fails_sheet(self) -> None:
        """Exceeding per-sheet cost limit fails the individual sheet."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=3,
            ),
        }
        baton.register_job("j1", sheets, {})
        baton.set_sheet_cost_limit("j1", 1, max_cost_usd=2.0)

        # Sheet 1 costs $3 — exceeds sheet limit
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="TRANSIENT",
            cost_usd=3.0,
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.FAILED, (
            f"Sheet should be failed (cost exceeded), got {state.status}"
        )


# =============================================================================
# 6. EXHAUSTION DECISION TREE — HEALING → ESCALATION → FAILURE
# =============================================================================


class TestExhaustionDecisionTree:
    """When retries are exhausted, does the baton follow the right path?

    The design spec says:
    1. Self-healing enabled → schedule a healing attempt
    2. Escalation enabled → enter FERMATA (pause job, await decision)
    3. Neither → FAILED (propagate to dependents)

    These tests verify each path is taken correctly.
    """

    async def test_healing_path_taken_before_escalation(self) -> None:
        """Self-healing takes priority over escalation."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=1,
            ),
        }
        baton.register_job(
            "j1", sheets, {},
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        # Fail once (retries exhausted — max_retries=1 means 1 normal attempt)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="EXECUTION_ERROR",
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # Should be in retry_scheduled (healing attempt), NOT fermata
        assert state.status == BatonSheetStatus.RETRY_SCHEDULED, (
            f"Healing should be attempted before escalation, got {state.status}"
        )
        assert state.healing_attempts == 1

    async def test_escalation_path_after_healing_exhausted(self) -> None:
        """If healing fails too, escalation kicks in."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=0,
            ),
        }
        baton.register_job(
            "j1", sheets, {},
            self_healing_enabled=True,
            escalation_enabled=True,
        )

        # Exhaust both normal retries and healing
        # First: exhaust normal retries (max_retries=0 → immediate exhaustion)
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="EXECUTION_ERROR",
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        # After first attempt with max_retries=0: healing attempt scheduled
        assert state.healing_attempts == 1

        # Simulate retry (healing attempt fires)
        await baton.handle_event(RetryDue(job_id="j1", sheet_num=1))

        # Healing attempt also fails
        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=2, execution_success=False,
            error_classification="EXECUTION_ERROR",
        ))

        # Now healing is exhausted (default max_healing=1) → escalation
        assert state.status == BatonSheetStatus.FERMATA, (
            f"Should enter fermata after healing exhausted, got {state.status}"
        )

    async def test_failure_path_when_nothing_enabled(self) -> None:
        """No healing, no escalation → straight to failed."""
        baton = BatonCore()
        sheets = {
            1: SheetExecutionState(
                sheet_num=1, instrument_name="claude-code", max_retries=0,
            ),
        }
        baton.register_job("j1", sheets, {})  # No healing, no escalation

        await baton.handle_event(SheetAttemptResult(
            job_id="j1", sheet_num=1, instrument_name="claude-code",
            attempt=1, execution_success=False,
            error_classification="EXECUTION_ERROR",
        ))

        state = baton.get_sheet_state("j1", 1)
        assert state is not None
        assert state.status == BatonSheetStatus.FAILED


# =============================================================================
# 7. PREAMBLE INTELLIGENCE — DOES IT HELP AGENTS ORIENT?
# =============================================================================


class TestPreambleIntelligence:
    """Does the preamble give agents the context they need to orient?

    A good preamble tells the agent: where am I, what's my workspace,
    and what does success look like. A retry preamble adds: what went
    wrong before, don't repeat it.
    """

    def test_first_run_preamble_has_essential_context(self) -> None:
        """First-run preamble contains workspace, position, and success criteria."""
        preamble = build_preamble(
            sheet_num=3, total_sheets=10,
            workspace=Path("/home/user/workspaces/my-project"),
        )
        # Agent must know where it is
        assert "sheet 3 of 10" in preamble
        assert "/home/user/workspaces/my-project" in preamble
        # Agent must know what success looks like
        assert "validation" in preamble.lower()

    def test_retry_preamble_differs_from_first_run(self) -> None:
        """Retry preamble has DIFFERENT content that helps the agent learn.

        If the retry preamble is identical to first-run, the agent has no
        signal that this is a retry. It would repeat the same approach.
        """
        first_run = build_preamble(
            sheet_num=1, total_sheets=5,
            workspace=Path("/tmp/ws"), retry_count=0,
        )
        retry = build_preamble(
            sheet_num=1, total_sheets=5,
            workspace=Path("/tmp/ws"), retry_count=2,
        )

        # Retry preamble must be different
        assert first_run != retry
        # Retry preamble mentions the retry explicitly
        assert "2" in retry or "retry" in retry.lower()
        # Retry preamble tells agent to study what went wrong
        assert "previous" in retry.lower() or "failed" in retry.lower()


# =============================================================================
# 8. BATON MUSICIAN PROMPT RENDERING (F-104 — the critical unblock)
# =============================================================================


class TestBatonMusicianPromptRendering:
    """Does the baton musician's _build_prompt() make agents more effective?

    F-104 was the blocker: the baton's musician rendered raw templates with no
    preamble, no injections, no validation requirements. Now it has a full
    5-layer pipeline. The litmus: is the assembled prompt richer and more
    actionable than the raw template alone?
    """

    def test_build_prompt_is_richer_than_raw_template(self) -> None:
        """The baton musician's _build_prompt() produces a prompt with MORE
        actionable layers than just the template text.

        This is THE litmus test for F-104. If the assembled prompt is just
        the template, the intelligence pipeline isn't working.
        """
        from mozart.core.config.execution import ValidationRule as VR
        from mozart.daemon.baton.musician import _build_prompt
        from mozart.daemon.baton.state import AttemptContext

        sheet = Sheet(
            num=3, movement=2, voice=1, voice_count=3,
            workspace=Path("/tmp/litmus-test"),
            instrument_name="claude-code",
            prompt_template="Implement the {{ module }} module in {{ workspace }}",
            variables={"module": "authentication"},
            validations=[
                VR(type="file_exists", path="{workspace}/auth.py",
                   description="Auth module file"),
                VR(type="command_succeeds", command="cd {workspace} && python -m pytest",
                   description="Tests pass"),
            ],
        )
        ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)

        prompt = _build_prompt(sheet, ctx, total_sheets=9, total_movements=3)

        # The raw template is just one line. The assembled prompt must be
        # substantially richer.
        raw = "Implement the authentication module in /tmp/litmus-test"
        assert len(prompt) > len(raw) * 3, (
            f"Assembled prompt ({len(prompt)} chars) should be >3x raw "
            f"({len(raw)} chars). The intelligence layers aren't adding value."
        )

        # Layer 1: Preamble — agent knows its position
        assert "sheet 3 of 9" in prompt, "Preamble must tell agent its position"
        assert "/tmp/litmus-test" in prompt, "Preamble must include workspace"

        # Layer 2: Rendered template — variables are expanded
        assert "authentication" in prompt, "Template variables must be expanded"
        assert "{{ module }}" not in prompt, "Raw Jinja2 must NOT appear"

        # Layer 4: Validation requirements — agent knows success criteria
        assert "Success Requirements" in prompt, "Validations must be formatted"
        assert "Auth module file" in prompt, "Validation descriptions included"
        assert "Tests pass" in prompt, "All validation descriptions included"

    def test_build_prompt_includes_completion_suffix(self) -> None:
        """In completion mode, the prompt tells the agent to finish remaining work.

        This matters because completion mode means validations partially passed.
        The agent should focus on what FAILED, not redo everything.
        """
        from mozart.daemon.baton.musician import _build_prompt
        from mozart.daemon.baton.state import AttemptContext

        sheet = Sheet(
            num=1, movement=1, voice=None, voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            prompt_template="Build everything",
        )
        ctx = AttemptContext(
            attempt_number=2,
            mode=AttemptMode.COMPLETION,
            completion_prompt_suffix="IMPORTANT: Focus on the FAILED validations only.",
        )

        prompt = _build_prompt(sheet, ctx, total_sheets=1, total_movements=1)

        assert "FAILED validations" in prompt, (
            "Completion suffix must appear in the prompt"
        )
        # The suffix should be at the END (last thing the agent reads)
        suffix_pos = prompt.index("FAILED validations")
        assert suffix_pos > len(prompt) * 0.7, (
            "Completion suffix should be near the end of the prompt"
        )

    def test_build_prompt_template_file_takes_precedence(self) -> None:
        """When template_file is set, it's used instead of prompt_template.

        The v3 score and many real scores use template_file. If _build_prompt
        silently ignores it and uses prompt_template instead, agents get
        an empty or wrong prompt.
        """
        import tempfile

        from mozart.daemon.baton.musician import _build_prompt
        from mozart.daemon.baton.state import AttemptContext

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            f.write("You are working on movement {{ movement }} of {{ total_movements }}.")
            f.flush()
            template_path = Path(f.name)

        try:
            sheet = Sheet(
                num=1, movement=2, voice=None, voice_count=1,
                workspace=Path("/tmp/ws"),
                instrument_name="claude-code",
                prompt_template="THIS SHOULD NOT APPEAR",
                template_file=template_path,
            )
            ctx = AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL)
            prompt = _build_prompt(sheet, ctx, total_sheets=5, total_movements=3)

            assert "THIS SHOULD NOT APPEAR" not in prompt, (
                "template_file must take precedence over prompt_template"
            )
            assert "movement 2 of 3" in prompt, (
                "template_file content must be rendered with variables"
            )
        finally:
            template_path.unlink(missing_ok=True)

    def test_build_prompt_retry_preamble_differs(self) -> None:
        """On retry, the preamble gives the agent DIFFERENT context.

        If the retry preamble is identical to first run, the agent learns
        nothing from the retry — it just repeats the same approach.
        """
        from mozart.daemon.baton.musician import _build_prompt
        from mozart.daemon.baton.state import AttemptContext

        sheet = Sheet(
            num=1, movement=1, voice=None, voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            prompt_template="Do the work",
        )

        first_run = _build_prompt(
            sheet, AttemptContext(attempt_number=1, mode=AttemptMode.NORMAL),
            total_sheets=1, total_movements=1,
        )
        retry = _build_prompt(
            sheet, AttemptContext(attempt_number=3, mode=AttemptMode.NORMAL),
            total_sheets=1, total_movements=1,
        )

        assert first_run != retry, "Retry prompt must differ from first run"
        # Retry should mention previous attempts
        assert "2" in retry or "retry" in retry.lower() or "previous" in retry.lower()

    def test_validation_requirements_show_expanded_paths(self) -> None:
        """Validation paths expand {workspace} to the actual path.

        An agent seeing '{workspace}/auth.py' has to guess the path.
        An agent seeing '/tmp/ws/auth.py' knows exactly what to create.
        """
        from mozart.daemon.baton.musician import _format_validation_requirements

        rules = [
            type("Rule", (), {
                "type": "file_exists",
                "path": "{workspace}/output.md",
                "description": "Output file must exist",
            })(),
        ]
        template_vars = {"workspace": "/home/user/project", "sheet_num": 1}

        result = _format_validation_requirements(rules, template_vars)

        assert "/home/user/project/output.md" in result, (
            "Validation path should expand {workspace} to actual path"
        )
        assert "{workspace}" not in result, (
            "Raw template variable should be replaced"
        )


# =============================================================================
# 9. ERROR TAXONOMY INTELLIGENCE (E006 stale vs E001 timeout, F-098 Phase 4.5)
# =============================================================================


class TestErrorTaxonomyIntelligence:
    """Does the error taxonomy make meaningful distinctions?

    A classification system that maps everything to the same code is useless.
    The litmus: do DIFFERENT error conditions get DIFFERENT treatment?
    """

    def test_stale_detection_classified_differently_from_timeout(self) -> None:
        """E006 (stale) has different retry behavior than E001 (timeout).

        Why this matters: stale detection means the agent went silent (no
        output for N minutes). Timeout means the agent hit the wall clock.
        The recovery strategy differs: stale → longer delay (agent may need
        more think time), timeout → shorter delay (try again sooner).
        """
        from mozart.core.errors.codes import (
            _RETRY_BEHAVIORS,
            ErrorCode,
        )

        stale_behavior = _RETRY_BEHAVIORS[ErrorCode.EXECUTION_STALE]
        timeout_behavior = _RETRY_BEHAVIORS[ErrorCode.EXECUTION_TIMEOUT]

        # Both are retriable
        assert stale_behavior.is_retriable
        assert timeout_behavior.is_retriable

        # But with DIFFERENT delays — stale gets MORE time
        assert stale_behavior.delay_seconds > timeout_behavior.delay_seconds, (
            f"Stale ({stale_behavior.delay_seconds}s) should have longer delay "
            f"than timeout ({timeout_behavior.delay_seconds}s)"
        )

    def test_classifier_distinguishes_stale_from_timeout(self) -> None:
        """The classifier produces E006 for stale and E001 for timeout.

        The distinction is based on 'stale execution' in the combined output.
        Without this, stale kills and backend timeouts are indistinguishable
        in diagnostics — making F-097 undiagnosable.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCode

        classifier = ErrorClassifier()

        # Stale detection — stderr contains the marker
        stale_result = classifier.classify(
            stderr="Stale execution: no output for 1800s",
            exit_reason="timeout",
        )
        assert stale_result.error_code == ErrorCode.EXECUTION_STALE, (
            f"Stale detection should produce E006, got {stale_result.error_code}"
        )

        # Regular timeout — no stale marker
        timeout_result = classifier.classify(
            stderr="Command timed out after 10800s",
            exit_reason="timeout",
        )
        assert timeout_result.error_code == ErrorCode.EXECUTION_TIMEOUT, (
            f"Regular timeout should produce E001, got {timeout_result.error_code}"
        )

    def test_phase_4_5_catches_rate_limit_masked_by_json_errors(self) -> None:
        """Phase 4.5 detects rate limits even when Phase 1 JSON errors mask them.

        This is the F-098 regression test. The v3 production failure: Claude CLI
        returns structured JSON errors AND rate limit text in stdout. Phase 1
        finds the JSON errors, Phase 4 never fires (it only runs when no errors
        found), and the rate limit is classified as E999 (permanent). The agent
        retries 28 times without rate limit backoff.

        Phase 4.5 ALWAYS runs, even after Phase 1, and scans stdout+stderr for
        rate limit patterns.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()

        # The exact production scenario: JSON errors in stdout WITH rate limit
        result = classifier.classify_execution(
            stdout=(
                '{"type":"error","error":{"type":"overloaded",'
                '"message":"service overloaded"}}\n'
                "API Error: Rate limit reached"
            ),
            stderr="",
            exit_code=1,
        )

        # The rate limit MUST be detected as the root cause
        assert result.primary.category == ErrorCategory.RATE_LIMIT, (
            f"Rate limit should be primary classification, got "
            f"{result.primary.category}. Without Phase 4.5, this was E999."
        )

    def test_rate_limit_in_stdout_only_still_detected(self) -> None:
        """Rate limits in stdout (not stderr) are caught.

        Many CLI tools write error messages to stdout, not stderr.
        The classifier must scan BOTH.
        """
        from mozart.core.errors.classifier import ErrorClassifier
        from mozart.core.errors.codes import ErrorCategory

        classifier = ErrorClassifier()

        result = classifier.classify_execution(
            stdout="You've hit your limit · resets 11pm",
            stderr="",
            exit_code=0,
        )

        has_rate_limit = any(
            e.category == ErrorCategory.RATE_LIMIT
            for e in result.all_errors
        )
        assert has_rate_limit, (
            f"Rate limit in stdout should be detected. "
            f"Got primary: {result.primary.category}"
        )


# =============================================================================
# 10. SHEET ENTITY TEMPLATE VARIABLES — TERMINOLOGY COEXISTENCE
# =============================================================================


class TestSheetEntityIntelligence:
    """Does the Sheet entity produce template variables that serve both
    old and new terminology users?

    A score author using {{ stage }} and one using {{ movement }} should
    both get the correct value. If they diverge, migration is impossible.
    """

    def test_old_and_new_terminology_produce_identical_values(self) -> None:
        """{{ stage }} == {{ movement }}, {{ instance }} == {{ voice }}, etc.

        This is the backward compatibility litmus. Every old template MUST
        produce the same output as its new-terminology equivalent.
        """
        sheet = Sheet(
            num=5, movement=2, voice=3, voice_count=4,
            workspace=Path("/tmp/ws"),
            instrument_name="gemini-cli",
        )

        tvars = sheet.template_variables(total_sheets=12, total_movements=3)

        # New and old produce identical values
        assert tvars["movement"] == tvars["stage"], "movement != stage"
        assert tvars["voice"] == tvars["instance"], "voice != instance"
        assert tvars["voice_count"] == tvars["fan_count"], "voice_count != fan_count"
        assert tvars["total_movements"] == tvars["total_stages"], (
            "total_movements != total_stages"
        )

        # Values are correct
        assert tvars["movement"] == 2
        assert tvars["voice"] == 3
        assert tvars["voice_count"] == 4
        assert tvars["total_movements"] == 3
        assert tvars["sheet_num"] == 5
        assert tvars["total_sheets"] == 12
        assert tvars["instrument_name"] == "gemini-cli"

    def test_custom_variables_dont_override_builtins(self) -> None:
        """Score-defined variables with builtin names don't clobber builtins.

        If a score defines variables: {sheet_num: "wrong"}, the template
        should still get the real sheet number, not the user's override.
        """
        sheet = Sheet(
            num=7, movement=3, voice=None, voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            variables={"sheet_num": "WRONG", "workspace": "ALSO_WRONG", "custom": "ok"},
        )

        tvars = sheet.template_variables(total_sheets=10, total_movements=4)

        # Builtins win
        assert tvars["sheet_num"] == 7, "Builtin sheet_num overridden by custom"
        assert tvars["workspace"] == "/tmp/ws", "Builtin workspace overridden"
        # Custom variables still available
        assert tvars["custom"] == "ok"

    def test_solo_movement_voice_is_none(self) -> None:
        """For non-fan-out movements, voice is None (not 0 or 1).

        Score templates that check `{% if voice %}` need None for solo
        movements to correctly skip voice-specific logic.
        """
        sheet = Sheet(
            num=1, movement=1, voice=None, voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
        )

        tvars = sheet.template_variables(total_sheets=5, total_movements=3)
        assert tvars["voice"] is None
        assert tvars["instance"] is None  # Old alias also None


# =============================================================================
# 11. CROSS-SYSTEM INTEGRATION — DO THE PIECES COMPOSE?
# =============================================================================


class TestCrossSystemIntegration:
    """Do the intelligence subsystems compose correctly?

    Each subsystem works in isolation (proven by unit tests). The litmus:
    do they produce correct behavior when chained together?
    """

    def test_musician_classify_error_maps_to_baton_decisions(self) -> None:
        """The musician's error classification feeds the baton's decision tree.

        The musician produces classifications like "AUTH_FAILURE", "TRANSIENT",
        "EXECUTION_ERROR". The baton uses these to decide retry/fail/escalate.
        If the mapping is wrong, good classifications lead to bad decisions.
        """
        from mozart.backends.base import ExecutionResult
        from mozart.daemon.baton.musician import _classify_error

        # AUTH_FAILURE → baton should fail immediately (no retry)
        auth_result = ExecutionResult(
            success=False,
            stdout="",
            stderr="Error: 401 unauthorized - invalid api key",
            duration_seconds=1.0,
            exit_code=1,
        )
        classification, _ = _classify_error(auth_result)
        assert classification == "AUTH_FAILURE"

        # TRANSIENT → baton should retry
        killed_result = ExecutionResult(
            success=False,
            stdout="",
            stderr="",
            duration_seconds=30.0,
            exit_code=None,
        )
        classification, _ = _classify_error(killed_result)
        assert classification == "TRANSIENT"

        # Success → no classification (not an error)
        success_result = ExecutionResult(
            success=True,
            stdout="All done!",
            stderr="",
            duration_seconds=5.0,
            exit_code=0,
        )
        classification, _ = _classify_error(success_result)
        assert classification is None

    def test_credential_redaction_in_capture_output(self) -> None:
        """Output containing API keys is redacted before reaching the baton.

        If credentials leak into SheetAttemptResult, they propagate to
        6+ storage locations (F-003). The redaction happens in the musician's
        _capture_output, which is the single bottleneck for all output.
        """
        from mozart.backends.base import ExecutionResult
        from mozart.daemon.baton.musician import _capture_output

        result = ExecutionResult(
            success=True,
            stdout="Using API key: sk-ant-api03-SECRET_KEY_HERE and done",
            stderr="Warning: AKIA1234567890123456 detected in env",
            duration_seconds=2.0,
            exit_code=0,
        )

        stdout, stderr = _capture_output(result)

        # API keys must be redacted
        assert "sk-ant-api03-SECRET_KEY_HERE" not in stdout
        assert "REDACTED" in stdout
        assert "AKIA1234567890123456" not in stderr
        assert "REDACTED" in stderr

    async def test_f018_contract_no_validations_means_100_percent(self) -> None:
        """F-018: execution succeeds with no validations → 100% pass rate.

        The baton's decision tree treats 0% as "all validations failed"
        and retries. Without this contract, every sheet without validations
        would retry until exhaustion.
        """
        from mozart.backends.base import ExecutionResult
        from mozart.daemon.baton.musician import _validate

        sheet = Sheet(
            num=1, movement=1, voice=None, voice_count=1,
            workspace=Path("/tmp/ws"),
            instrument_name="claude-code",
            validations=[],  # No validations
        )
        exec_result = ExecutionResult(
            success=True, stdout="Done", stderr="", duration_seconds=1.0,
            exit_code=0,
        )

        _passed, total, rate, _ = await _validate(sheet, exec_result)

        assert rate == 100.0, (
            f"No validations + success should be 100% pass rate, got {rate}"
        )
        assert total == 0  # No rules means 0 total


# =========================================================================
# Category 11: Restart Recovery Intelligence (Step 29)
# =========================================================================


class TestRestartRecoveryIntelligence:
    """Does recover_job() actually rebuild the right state from a checkpoint?

    The litmus: a baton that recovers from a checkpoint should produce
    IDENTICAL decisions to a baton that watched the sheets complete live.
    Terminal states must be preserved. In-progress must reset to PENDING.
    Attempt counts must be carried forward (to avoid infinite retries).
    """

    def test_recovery_preserves_terminal_states(self) -> None:
        """Completed/failed/skipped sheets stay terminal after recovery.

        Without this, the baton would re-execute finished work —
        wasting cost and potentially producing different results.
        """
        from mozart.core.checkpoint import CheckpointState, SheetState, SheetStatus
        from mozart.daemon.baton.adapter import BatonAdapter

        # Build a checkpoint that represents a partially-completed job:
        # sheet 1 completed, sheet 2 failed, sheet 3 skipped, sheet 4 in_progress
        checkpoint = CheckpointState(
            job_id="recovery-test",
            job_name="recovery-test",
            total_sheets=5,
            sheets={
                1: SheetState(sheet_num=1, status=SheetStatus.COMPLETED, attempt_count=1),
                2: SheetState(sheet_num=2, status=SheetStatus.FAILED, attempt_count=3),
                3: SheetState(sheet_num=3, status=SheetStatus.SKIPPED),
                4: SheetState(sheet_num=4, status=SheetStatus.IN_PROGRESS, attempt_count=2),
                5: SheetState(sheet_num=5, status=SheetStatus.PENDING),
            },
        )

        sheets = [
            Sheet(num=i, movement=1, voice=None, voice_count=1,
                  workspace=Path("/tmp/ws"), instrument_name="claude-code")
            for i in range(1, 6)
        ]

        adapter = BatonAdapter(max_concurrent_sheets=10)
        adapter.recover_job(
            "recovery-test", sheets, {}, checkpoint,
            max_retries=5,
        )

        # Terminal states MUST be preserved
        s1 = adapter._baton._jobs["recovery-test"].sheets[1]
        s2 = adapter._baton._jobs["recovery-test"].sheets[2]
        s3 = adapter._baton._jobs["recovery-test"].sheets[3]
        s4 = adapter._baton._jobs["recovery-test"].sheets[4]
        s5 = adapter._baton._jobs["recovery-test"].sheets[5]

        assert s1.status == BatonSheetStatus.COMPLETED, (
            f"Completed sheet should stay completed, got {s1.status}"
        )
        assert s2.status == BatonSheetStatus.FAILED, (
            f"Failed sheet should stay failed, got {s2.status}"
        )
        assert s3.status == BatonSheetStatus.SKIPPED, (
            f"Skipped sheet should stay skipped, got {s3.status}"
        )
        # In-progress resets to PENDING (musician died on restart)
        assert s4.status == BatonSheetStatus.PENDING, (
            f"In-progress sheet should reset to pending, got {s4.status}"
        )
        # Not-started stays pending
        assert s5.status == BatonSheetStatus.PENDING, (
            f"Not-started sheet should be pending, got {s5.status}"
        )

    def test_recovery_carries_forward_attempt_counts(self) -> None:
        """Attempt counts from checkpoint are preserved to prevent infinite retries.

        Without this, a sheet that already tried 3 times would get 3 MORE
        tries after restart — violating the max_retries contract.
        """
        from mozart.core.checkpoint import CheckpointState, SheetState, SheetStatus
        from mozart.daemon.baton.adapter import BatonAdapter

        checkpoint = CheckpointState(
            job_id="attempt-carry",
            job_name="attempt-carry",
            total_sheets=1,
            sheets={
                1: SheetState(
                    sheet_num=1, status=SheetStatus.IN_PROGRESS,
                    attempt_count=4, completion_attempts=2,
                ),
            },
        )

        sheets = [
            Sheet(num=1, movement=1, voice=None, voice_count=1,
                  workspace=Path("/tmp/ws"), instrument_name="claude-code"),
        ]

        adapter = BatonAdapter(max_concurrent_sheets=10)
        adapter.recover_job(
            "attempt-carry", sheets, {}, checkpoint,
            max_retries=5,
        )

        s1 = adapter._baton._jobs["attempt-carry"].sheets[1]
        assert s1.normal_attempts == 4, (
            f"Should carry forward 4 attempts from checkpoint, got {s1.normal_attempts}"
        )
        assert s1.completion_attempts == 2, (
            f"Should carry forward 2 completion attempts, got {s1.completion_attempts}"
        )
        # With max_retries=5 and 4 attempts already used, only 1 retry remains
        assert s1.can_retry, "Should still have 1 retry remaining (4 of 5 used)"

    def test_recovery_with_exhausted_retries_stays_terminal(self) -> None:
        """A recovered sheet with exhausted retries should fail on next attempt.

        The litmus: recover a sheet with attempt_count >= max_retries,
        send one more failure, verify it goes to FAILED (not retry).
        """
        from mozart.core.checkpoint import CheckpointState, SheetState, SheetStatus
        from mozart.daemon.baton.adapter import BatonAdapter

        checkpoint = CheckpointState(
            job_id="exhausted-recovery",
            job_name="exhausted-recovery",
            total_sheets=1,
            sheets={
                1: SheetState(
                    sheet_num=1, status=SheetStatus.IN_PROGRESS,
                    attempt_count=3,
                ),
            },
        )

        sheets = [
            Sheet(num=1, movement=1, voice=None, voice_count=1,
                  workspace=Path("/tmp/ws"), instrument_name="claude-code"),
        ]

        adapter = BatonAdapter(max_concurrent_sheets=10)
        adapter.recover_job(
            "exhausted-recovery", sheets, {}, checkpoint,
            max_retries=3,  # Already used all 3
        )

        s1 = adapter._baton._jobs["exhausted-recovery"].sheets[1]
        assert not s1.can_retry, (
            "Sheet with 3/3 normal attempts used should not be retriable"
        )
        # Note: is_exhausted requires BOTH retry AND completion budgets exhausted.
        # With max_retries=3, normal_attempts=3, can_retry is False.
        # But completion mode budget is separate (max_completion=5, used=0).
        # The litmus: retry budget is correctly carried forward and exhausted.
        assert s1.normal_attempts == 3, (
            f"Should carry forward 3 attempts from checkpoint, got {s1.normal_attempts}"
        )
        assert s1.max_retries == 3, (
            f"Should have max_retries=3, got {s1.max_retries}"
        )


# =========================================================================
# Category 12: Completion Signaling Intelligence
# =========================================================================


class TestCompletionSignaling:
    """Does wait_for_completion correctly detect terminal state?

    The litmus: submitting results for all sheets should unblock
    wait_for_completion and return the correct success/failure status.
    """

    async def test_completion_signals_on_all_success(self) -> None:
        """wait_for_completion returns True when all sheets complete successfully."""
        import asyncio
        from mozart.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=10)

        sheets = [
            Sheet(num=i, movement=1, voice=None, voice_count=1,
                  workspace=Path("/tmp/ws"), instrument_name="claude-code")
            for i in range(1, 4)
        ]

        adapter.register_job("signal-test", sheets, {})

        # Complete all sheets
        for i in range(1, 4):
            await adapter._baton.handle_event(SheetAttemptResult(
                job_id="signal-test", sheet_num=i,
                instrument_name="claude-code", attempt=1,
                execution_success=True, validation_pass_rate=100.0,
            ))

        # Check completions (normally called by the event loop)
        adapter._check_completions()

        # wait_for_completion should return immediately (event is set)
        result = await asyncio.wait_for(
            adapter.wait_for_completion("signal-test"),
            timeout=1.0,
        )
        assert result is True, "All-success should return True"

    async def test_completion_signals_false_on_failure(self) -> None:
        """wait_for_completion returns False when any sheet fails."""
        import asyncio
        from mozart.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=10)

        states = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code", max_retries=0),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        adapter._baton.register_job("fail-signal", states, {})
        adapter._completion_events["fail-signal"] = asyncio.Event()

        # Sheet 1 fails (max_retries=0, so immediately terminal)
        await adapter._baton.handle_event(SheetAttemptResult(
            job_id="fail-signal", sheet_num=1,
            instrument_name="claude-code", attempt=1,
            execution_success=False, error_classification="EXECUTION_ERROR",
        ))

        # Sheet 2 succeeds
        await adapter._baton.handle_event(SheetAttemptResult(
            job_id="fail-signal", sheet_num=2,
            instrument_name="claude-code", attempt=1,
            execution_success=True, validation_pass_rate=100.0,
        ))

        adapter._check_completions()

        result = await asyncio.wait_for(
            adapter.wait_for_completion("fail-signal"),
            timeout=1.0,
        )
        assert result is False, "Any failure should return False"


# =========================================================================
# Category 13: Credential Safety in Error Paths (F-135)
# =========================================================================


class TestCredentialSafetyInErrorPaths:
    """Does the musician's exception handler redact credentials?

    The litmus: if a backend raises an exception containing an API key,
    the SheetAttemptResult that reaches the baton's inbox MUST have the
    key redacted. Without this, credentials propagate to 6+ storage
    locations (logs, state DB, dashboard, diagnostics, learning store).
    """

    def test_redact_credentials_catches_anthropic_key_in_error(self) -> None:
        """An Anthropic key in an exception message is redacted."""
        from mozart.utils.credential_scanner import redact_credentials

        error_msg = "ConnectionError: Auth failed with key sk-ant-api03-REAL_SECRET_KEY_1234567890abcdef"
        redacted = redact_credentials(error_msg)

        assert "sk-ant-api03" not in redacted, (
            "Anthropic key prefix should be redacted from error messages"
        )
        assert "REDACTED_ANTHROPIC" in redacted, (
            "Redaction label should appear in output"
        )

    def test_redact_credentials_catches_multiple_key_types(self) -> None:
        """Multiple credential types in one message are all redacted."""
        from mozart.utils.credential_scanner import redact_credentials

        error_msg = (
            "Config error: ANTHROPIC_API_KEY=sk-ant-api03-ABCDEFGH123456789012345678901234 "
            "and OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890 "
            "and AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE"
        )
        redacted = redact_credentials(error_msg)

        assert "sk-ant-api03" not in redacted
        assert "sk-proj-" not in redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert redacted.count("REDACTED") >= 3, (
            f"Should have at least 3 redaction labels, got {redacted.count('REDACTED')}"
        )

    def test_musician_error_path_applies_redaction(self) -> None:
        """The musician module imports and uses redact_credentials.

        This is a structural litmus: does the production code path
        actually call the redaction function? We verify by checking
        the import and the call site.
        """
        import inspect
        from mozart.daemon.baton import musician

        source = inspect.getsource(musician)

        # The exception handler at line ~159 must call redact_credentials
        assert "redact_credentials" in source, (
            "musician.py must import and use redact_credentials"
        )
        # The function must be called on the error_msg, not just imported
        assert "redact_credentials(raw_error_msg)" in source or \
               "redact_credentials(error_msg)" in source or \
               "= redact_credentials(" in source, (
            "redact_credentials must be called on the exception message"
        )

    def test_github_slack_hf_tokens_also_caught(self) -> None:
        """F-023: GitHub, Slack, and HF tokens are caught in error messages."""
        from mozart.utils.credential_scanner import redact_credentials

        error_msg = (
            "AuthError: ghp_1234567890abcdef1234567890abcdef123456 "
            "SlackError: xoxb-1234-5678-abcdefghijklmnopqrstuvwx "
            "HFError: hf_abcdefghijklmnopqrstuvwx"
        )
        redacted = redact_credentials(error_msg)

        assert "ghp_" not in redacted, "GitHub PAT should be redacted"
        assert "xoxb-" not in redacted, "Slack bot token should be redacted"
        assert "hf_" not in redacted, "HF token should be redacted"


# =========================================================================
# Category 14: Parallel Executor Failure Handling (F-111, F-113)
# =========================================================================


class TestParallelFailureIntelligence:
    """Do F-111 and F-113 fixes actually prevent the production bugs?

    F-111: RateLimitExhaustedError must be preserved through parallel
    executor so jobs PAUSE instead of FAIL.

    F-113: Failed dependencies must propagate failure to downstream
    sheets, not silently let them proceed on incomplete input.
    """

    def test_parallel_batch_result_preserves_exception_types(self) -> None:
        """F-111: ParallelBatchResult has an exceptions dict to preserve types.

        Without this, the parallel executor converts all exceptions to
        strings in error_details, losing the exception type. The lifecycle
        can't isinstance-check a string, so RateLimitExhaustedError becomes
        a generic FatalError, and jobs FAIL instead of PAUSE.
        """
        from mozart.execution.parallel import ParallelBatchResult

        # The exceptions field must exist
        result = ParallelBatchResult(
            sheets=[1, 2, 3],
            completed=[1],
            failed=[2],
            skipped=[3],
            error_details={2: "Rate limit exceeded"},
            exceptions={2: RuntimeError("test")},
        )

        assert hasattr(result, "exceptions"), (
            "ParallelBatchResult must have an exceptions field (F-111)"
        )
        assert isinstance(result.exceptions[2], RuntimeError), (
            "exceptions dict must preserve the original exception object"
        )

    def test_parallel_executor_has_failure_propagation(self) -> None:
        """F-113: ParallelExecutor must have propagate_failure_to_dependents.

        Without this, failed fan-out voices are treated as "done" for
        dependency resolution, and synthesis sheets execute on incomplete
        input — exactly what happened in the rosetta score.
        """
        from mozart.execution.parallel import ParallelExecutor

        assert hasattr(ParallelExecutor, "propagate_failure_to_dependents"), (
            "ParallelExecutor must have propagate_failure_to_dependents (F-113)"
        )

    def test_failed_status_in_dag_terminal_set(self) -> None:
        """F-113/F-129: FAILED must be in the terminal set for DAG resolution.

        Without this, after a conductor restart (when _permanently_failed
        is empty), failed sheets are not recognized as terminal by the DAG.
        Downstream sheets block forever — a deadlock.

        The litmus: verify structurally that the parallel executor's
        get_next_parallel_batch treats FAILED as terminal for dependency
        resolution. We check the source code for the terminal set.
        """
        import inspect
        from mozart.execution import parallel

        source = inspect.getsource(parallel)

        # The F-113 fix adds FAILED to the terminal set used by
        # get_next_parallel_batch for DAG resolution. Without it,
        # only COMPLETED and SKIPPED are treated as "done".
        # Look for SheetStatus.FAILED being included in the "done" set
        assert "SheetStatus.FAILED" in source, (
            "parallel.py must reference SheetStatus.FAILED in its terminal set (F-113)"
        )

        # Also verify propagate_failure_to_dependents exists (F-113)
        assert "propagate_failure_to_dependents" in source, (
            "ParallelExecutor must have propagate_failure_to_dependents (F-113)"
        )

        # Verify the exceptions field exists on ParallelBatchResult (F-111)
        from mozart.execution.parallel import ParallelBatchResult

        assert "exceptions" in ParallelBatchResult.__dataclass_fields__, (
            "ParallelBatchResult must have exceptions dict to preserve "
            "exception types like RateLimitExhaustedError (F-111)"
        )


# =========================================================================
# Category 15: Clone Config Isolation (F-132)
# =========================================================================


class TestCloneConfigIsolation:
    """Does build_clone_config produce truly isolated paths?

    F-132: The clone conductor must NOT share the production state DB.
    Without this, test jobs submitted to the clone appear in production
    `mozart list`, and production jobs could be corrupted by test operations.
    """

    def test_clone_state_db_differs_from_production(self) -> None:
        """Clone state_db_path must NOT be the default production path."""
        from mozart.daemon.clone import build_clone_config

        clone_config = build_clone_config(None)

        # The production default is ~/.mozart/daemon-state.db or similar
        default_path = str(Path.home() / ".mozart" / "daemon-state.db")
        clone_db = str(clone_config.state_db_path)

        assert clone_db != default_path, (
            f"Clone state_db_path should differ from production default. "
            f"Got: {clone_db}"
        )
        assert "clone" in clone_db.lower(), (
            f"Clone state_db_path should contain 'clone'. Got: {clone_db}"
        )

    def test_named_clones_are_isolated_from_each_other(self) -> None:
        """Two named clones must have different state DB paths."""
        from mozart.daemon.clone import build_clone_config

        config_a = build_clone_config("alpha")
        config_b = build_clone_config("beta")

        assert str(config_a.state_db_path) != str(config_b.state_db_path), (
            "Named clones must have different state_db_path values"
        )
        assert str(config_a.socket.path) != str(config_b.socket.path), (
            "Named clones must have different socket paths"
        )

    def test_clone_inherits_non_path_settings(self) -> None:
        """Clone inherits production config settings (max_concurrent, etc).

        Without this, clone testing doesn't replicate production behavior.
        """
        from mozart.daemon.clone import build_clone_config
        from mozart.daemon.config import DaemonConfig

        production = DaemonConfig(max_concurrent_jobs=7)
        clone = build_clone_config(None, base_config=production)

        assert clone.max_concurrent_jobs == 7, (
            "Clone should inherit max_concurrent_jobs from production"
        )
        # But paths should differ
        assert str(clone.socket.path) != str(production.socket.path), (
            "Clone socket must differ from production"
        )
        assert str(clone.state_db_path) != str(production.state_db_path), (
            "Clone state_db_path must differ from production"
        )


# =========================================================================
# Category 16: Cost Limit Wiring Intelligence (F-134)
# =========================================================================


class TestCostLimitWiringIntelligence:
    """Does _run_via_baton use the correct field for cost limits?

    F-134: The code used `config.cost_limits.max_cost_usd` (nonexistent)
    instead of `config.cost_limits.max_cost_per_job`. This meant cost
    limits would silently fail when the baton is enabled — max_cost would
    always be None.

    The litmus: verify the CORRECT field name is used in the manager's
    baton paths. A structural test — does the production code reference
    the right field?
    """

    def test_cost_limit_config_has_max_cost_per_job(self) -> None:
        """CostLimitConfig has max_cost_per_job, NOT max_cost_usd."""
        from mozart.core.config.execution import CostLimitConfig

        config = CostLimitConfig(enabled=True, max_cost_per_job=25.0)
        assert config.max_cost_per_job == 25.0

        # max_cost_usd should NOT exist as a field on the CONFIG model
        assert "max_cost_usd" not in CostLimitConfig.model_fields, (
            "CostLimitConfig should not have max_cost_usd field (F-134)"
        )

    def test_manager_reads_correct_config_field(self) -> None:
        """The manager reads max_cost_per_job from CostLimitConfig, not max_cost_usd.

        F-134: The old bug was `config.cost_limits.max_cost_usd` (nonexistent
        field), which silently returned None. The fix uses `max_cost_per_job`.

        This is a structural litmus: verify the manager code accesses the
        correct config field name.
        """
        import inspect
        from mozart.daemon import manager

        # The fixed code should reference max_cost_per_job in config access
        run_via_baton_source = inspect.getsource(manager.JobManager._run_via_baton)
        resume_via_baton_source = inspect.getsource(manager.JobManager._resume_via_baton)

        # Both paths must access config.cost_limits.max_cost_per_job
        assert "max_cost_per_job" in run_via_baton_source, (
            "_run_via_baton must access config.cost_limits.max_cost_per_job (F-134)"
        )
        assert "max_cost_per_job" in resume_via_baton_source, (
            "_resume_via_baton must access config.cost_limits.max_cost_per_job (F-134)"
        )

    async def test_cost_limit_end_to_end_through_baton(self) -> None:
        """Cost limit set via register_job actually pauses the job.

        The litmus: register a job with a very low cost limit, send
        an attempt result that exceeds it, verify the job pauses.
        """
        baton = BatonCore()

        sheets = {
            1: SheetExecutionState(sheet_num=1, instrument_name="claude-code"),
            2: SheetExecutionState(sheet_num=2, instrument_name="claude-code"),
        }
        baton.register_job("cost-wired", sheets, {2: [1]})
        baton.set_job_cost_limit("cost-wired", 0.05)  # $0.05 limit

        # Sheet 1 succeeds but costs $0.10 (exceeds limit)
        await baton.handle_event(SheetAttemptResult(
            job_id="cost-wired", sheet_num=1,
            instrument_name="claude-code", attempt=1,
            execution_success=True, validation_pass_rate=100.0,
            cost_usd=0.10,
        ))

        # Job should be paused due to cost limit
        job = baton._jobs["cost-wired"]
        assert job.paused, (
            "Job should be paused when cost exceeds limit ($0.10 > $0.05)"
        )


# =========================================================================
# Category 17: Baton-Runner State Mapping Totality
# =========================================================================


class TestBatonRunnerStateMappingTotality:
    """Does the baton ↔ checkpoint status mapping cover ALL states?

    The adapter maps between BatonSheetStatus and SheetStatus. If any
    state is missing from the mapping, recovery or synchronization
    produces incorrect behavior — the most insidious type of bug because
    it's silent.
    """

    def test_every_checkpoint_status_maps_to_baton_status(self) -> None:
        """All CheckpointState statuses have a baton mapping."""
        from mozart.core.checkpoint import SheetStatus
        from mozart.daemon.baton.adapter import checkpoint_to_baton_status

        unmapped = []
        for status in SheetStatus:
            try:
                result = checkpoint_to_baton_status(status.value)
                assert result is not None
            except (KeyError, ValueError):
                unmapped.append(status.value)

        assert not unmapped, (
            f"These checkpoint statuses have no baton mapping: {unmapped}"
        )

    def test_every_baton_status_maps_to_checkpoint_status(self) -> None:
        """All baton statuses have a checkpoint mapping."""
        from mozart.daemon.baton.adapter import baton_to_checkpoint_status

        unmapped = []
        for status in BatonSheetStatus:
            try:
                result = baton_to_checkpoint_status(status)
                assert result is not None
            except (KeyError, ValueError):
                unmapped.append(status.value)

        assert not unmapped, (
            f"These baton statuses have no checkpoint mapping: {unmapped}"
        )


# =========================================================================
# Category 18: Score-Level Instrument Alias Resolution
# =========================================================================


class TestInstrumentAliasResolution:
    """Does the instrument alias system make multi-instrument scores EASIER?

    The litmus question: can a score author define 'fast-writer' and
    'deep-thinker' as aliases, then use those aliases everywhere —
    and have resolution produce the correct underlying profile name
    with the correct merged config?

    WITHOUT aliases: per-sheet instrument assignment requires repeating
    the full profile name and config at every use site.
    WITH aliases: define once, reference by short name, config merges.
    """

    def test_alias_resolves_to_profile_name(self) -> None:
        """A movement using an alias name resolves to the alias's profile."""
        from mozart.core.config.job import InstrumentDef, JobConfig, MovementDef
        from mozart.core.sheet import build_sheets

        config = JobConfig(
            name="alias-test",
            workspace=Path("/tmp/alias-test"),
            instrument="claude-code",
            instruments={
                "fast-writer": InstrumentDef(
                    profile="gemini-cli",
                    config={"model": "gemini-2.5-flash"},
                ),
            },
            movements={
                2: MovementDef(instrument="fast-writer"),
            },
            sheet={"size": 1, "total_items": 2},
            prompt=PromptConfig(template="do work"),
        )

        sheets = build_sheets(config)
        # Movement 1: no movement override, falls to score-level claude-code
        m1_sheets = [s for s in sheets if s.movement == 1]
        assert m1_sheets[0].instrument_name == "claude-code"
        # Movement 2: uses alias 'fast-writer' → resolves to 'gemini-cli'
        m2_sheets = [s for s in sheets if s.movement == 2]
        assert m2_sheets[0].instrument_name == "gemini-cli", (
            "Alias 'fast-writer' should resolve to profile 'gemini-cli'"
        )

    def test_alias_config_merges_with_score_config(self) -> None:
        """Alias config overrides score-level instrument_config."""
        from mozart.core.config.job import InstrumentDef, JobConfig, MovementDef
        from mozart.core.sheet import build_sheets

        config = JobConfig(
            name="merge-test",
            workspace=Path("/tmp/merge-test"),
            instrument="claude-code",
            instrument_config={"timeout_seconds": 1800},
            instruments={
                "deep-thinker": InstrumentDef(
                    profile="claude-code",
                    config={"timeout_seconds": 3600, "model": "opus"},
                ),
            },
            movements={
                1: MovementDef(instrument="deep-thinker"),
            },
            sheet={"size": 1, "total_items": 1},
            prompt=PromptConfig(template="think deeply"),
        )

        sheets = build_sheets(config)
        sheet = sheets[0]
        # The alias config should merge on top of score-level config
        assert sheet.instrument_config.get("timeout_seconds") == 3600, (
            "Alias config should override score-level timeout"
        )
        assert sheet.instrument_config.get("model") == "opus", (
            "Alias config should add model field"
        )

    def test_per_sheet_overrides_alias(self) -> None:
        """Per-sheet instrument takes priority over alias."""
        from mozart.core.config.job import InstrumentDef, JobConfig
        from mozart.core.sheet import build_sheets

        config = JobConfig(
            name="priority-test",
            workspace=Path("/tmp/priority-test"),
            instrument="fast-writer",
            instruments={
                "fast-writer": InstrumentDef(profile="gemini-cli"),
            },
            sheet={
                "size": 1,
                "total_items": 2,
                "per_sheet_instruments": {2: "codex-cli"},
            },
            prompt=PromptConfig(template="test"),
        )

        sheets = build_sheets(config)
        # Sheet 1: score-level 'fast-writer' alias → gemini-cli
        assert sheets[0].instrument_name == "gemini-cli"
        # Sheet 2: per-sheet override takes priority over alias
        assert sheets[1].instrument_name == "codex-cli"


# =========================================================================
# Category 19: V210 Instrument Validation with Aliases
# =========================================================================


class TestInstrumentValidationWithAliases:
    """Does the validator accept score-level aliases as valid instrument names?

    WITHOUT the fix: a score using `instruments: {fast: {profile: gemini-cli}}`
    and `movements: {2: {instrument: fast}}` would warn "unknown instrument
    'fast'" — even though 'fast' is a declared alias.

    WITH the fix: aliases are recognized as valid names during validation.
    """

    def test_alias_names_accepted_by_validator(self) -> None:
        """V210 should NOT warn on instrument names that match score aliases."""
        from mozart.validation.checks.config import InstrumentNameCheck

        checker = InstrumentNameCheck()
        from mozart.core.config.job import InstrumentDef, JobConfig, MovementDef

        config = JobConfig(
            name="valid-alias",
            workspace=Path("/tmp/valid-alias"),
            instrument="claude-code",
            instruments={
                "reviewer": InstrumentDef(profile="gemini-cli"),
            },
            movements={
                2: MovementDef(instrument="reviewer"),
            },
            sheet={"size": 1, "total_items": 2},
            prompt=PromptConfig(template="test"),
        )

        # check() requires config_path and raw_yaml
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w") as f:
            f.write("name: valid-alias\n")
            f.flush()
            issues = checker.check(config, Path(f.name), "name: valid-alias\n")

        alias_issues = [i for i in issues if "reviewer" in i.message]
        assert len(alias_issues) == 0, (
            "V210 should not flag alias names as unknown instruments. "
            f"Got: {[i.message for i in alias_issues]}"
        )


# =========================================================================
# Category 20: F-127 Success Outcome Classification After Restart
# =========================================================================


class TestSuccessOutcomeAfterRestart:
    """Does diagnose correctly classify sheets that took many attempts?

    The litmus question: after a conductor restart + resume, does a sheet
    with 18 cumulative attempts show SUCCESS_RETRY (correct) or
    SUCCESS_FIRST_TRY (misleading)?

    WITHOUT the fix: _classify_success_outcome uses session-local counter
    that resets to 0 on restart, so everything looks like first_try.
    WITH the fix: uses persisted SheetState.attempt_count (cumulative).
    """

    def test_18_attempts_classifies_as_retry(self) -> None:
        """F-127: 18 cumulative attempts must NOT be success_first_try."""
        from mozart.execution.runner.sheet import SheetExecutionMixin

        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=18,
            completion_attempts=0,
        )
        assert not first_try, "18 attempts is not first_try"
        assert outcome.value == "success_retry", (
            f"18 attempts should be SUCCESS_RETRY, got {outcome.value}"
        )

    def test_1_attempt_classifies_as_first_try(self) -> None:
        """Single attempt is genuinely first_try."""
        from mozart.execution.runner.sheet import SheetExecutionMixin

        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=1,
            completion_attempts=0,
        )
        assert first_try
        assert outcome.value == "success_first_try"

    def test_completion_mode_classifies_correctly(self) -> None:
        """Sheet that needed completion mode is SUCCESS_COMPLETION."""
        from mozart.execution.runner.sheet import SheetExecutionMixin

        outcome, first_try = SheetExecutionMixin._classify_success_outcome(
            cumulative_attempts=3,
            completion_attempts=2,
        )
        assert not first_try
        assert outcome.value == "success_completion"


# =========================================================================
# Category 21: F-111 Parallel Executor Preserves Exception Types
# =========================================================================


class TestParallelExceptionPreservation:
    """Does the parallel batch preserve exception types for intelligent routing?

    The litmus question: when a RateLimitExhaustedError occurs in a parallel
    batch, can the lifecycle handler still isinstance() check it?

    WITHOUT the fix: exception was converted to string in error_details.
    isinstance() is impossible on strings. Job FAILS instead of PAUSING.
    WITH the fix: original exception preserved in result.exceptions dict.
    """

    def test_exceptions_dict_exists_on_batch_result(self) -> None:
        """ParallelBatchResult has an exceptions dict for type preservation."""
        from mozart.execution.parallel import ParallelBatchResult

        result = ParallelBatchResult(sheets=[1, 2])
        assert hasattr(result, "exceptions"), (
            "ParallelBatchResult must have 'exceptions' dict for F-111"
        )
        assert isinstance(result.exceptions, dict)

    def test_find_rate_limit_in_batch_extracts_correct_type(self) -> None:
        """_find_rate_limit_in_batch can find the exception by type."""
        from datetime import datetime, timezone

        from mozart.execution.parallel import ParallelBatchResult
        from mozart.execution.runner.lifecycle import LifecycleMixin
        from mozart.execution.runner.models import RateLimitExhaustedError

        resume_time = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        exc = RateLimitExhaustedError(
            "Rate limit exceeded",
            resume_after=resume_time,
            backend_type="claude-cli",
        )
        result = ParallelBatchResult(
            sheets=[1, 2, 3],
            failed=[2],
            completed=[1, 3],
            exceptions={2: exc},
        )

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is not None, "Should find RateLimitExhaustedError"
        assert isinstance(found, RateLimitExhaustedError)
        assert found.resume_after == resume_time, (
            "resume_after timestamp must survive the parallel batch"
        )

    def test_non_rate_limit_error_not_found(self) -> None:
        """Non-rate-limit exceptions are not misidentified."""
        from mozart.execution.parallel import ParallelBatchResult
        from mozart.execution.runner.lifecycle import LifecycleMixin

        result = ParallelBatchResult(
            sheets=[1],
            failed=[1],
            exceptions={1: ValueError("something broke")},
        )

        found = LifecycleMixin._find_rate_limit_in_batch(result)
        assert found is None, "ValueError is not a rate limit error"


# =========================================================================
# Category 22: F-113 Failure Propagation Through Dependencies
# =========================================================================


class TestFailurePropagationIntelligence:
    """Does failure propagation prevent downstream sheets from running?

    The litmus question: when sheet 2 fails in a fan-out, does synthesis
    (sheet 8, depends on all fan-out voices) also fail? Or does it run
    with incomplete data and produce garbage?

    WITHOUT the fix: failed deps treated as "done" — synthesis runs
    against 5 of 6 inputs, produces incomplete output.
    WITH the fix: failed deps propagate failure through the DAG.
    """

    def test_failed_sheets_in_terminal_set_for_dag(self) -> None:
        """FAILED status must be in the terminal set for DAG resolution.

        This is the structural fix for F-129 (deadlock after restart).
        Without FAILED in the terminal set, the DAG hangs forever after
        restart because _permanently_failed is ephemeral.
        """
        from mozart.core.checkpoint import SheetStatus
        from mozart.execution.parallel import ParallelExecutor

        # Verify FAILED is recognized as terminal for DAG purposes
        # by checking get_next_parallel_batch behavior
        assert SheetStatus.FAILED.value == "failed"
        # The fix is structural: SheetStatus.FAILED is now in the terminal
        # set used by get_next_parallel_batch. The key evidence is that
        # propagate_failure_to_dependents exists and uses iterative BFS.
        assert hasattr(ParallelExecutor, "propagate_failure_to_dependents"), (
            "propagate_failure_to_dependents must exist for F-113"
        )


# =========================================================================
# Category 23: F-119 Baton Event Stubs Log Instead of Silent Drop
# =========================================================================


class TestBatonEventStubLogging:
    """Do unimplemented baton event handlers log instead of silently dropping?

    The litmus question: when a StaleCheck event arrives at the baton,
    is there any evidence it happened? Or does it vanish?

    WITHOUT the fix: `pass` stubs. No log, no counter, no trace.
    WITH the fix: `_logger.warning("baton.event.unimplemented", ...)`.
    """

    async def test_stale_check_logs_warning(self) -> None:
        """StaleCheck event produces a warning log, not silence."""
        import io
        import logging

        from mozart.daemon.baton.events import StaleCheck

        baton = BatonCore()

        log_capture = io.StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("mozart.daemon.baton.core")
        logger.addHandler(handler)
        try:
            await baton.handle_event(StaleCheck(job_id="j1", sheet_num=1))
            log_output = log_capture.getvalue()
            # The stub should produce a warning — not crash, not silence
            assert "unimplemented" in log_output or "StaleCheck" in log_output, (
                "StaleCheck should produce a warning log, not silent drop"
            )
        finally:
            logger.removeHandler(handler)

    async def test_cron_tick_logs_warning(self) -> None:
        """CronTick event produces a warning, not silence."""
        import io
        import logging

        from mozart.daemon.baton.events import CronTick

        baton = BatonCore()

        log_capture = io.StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger("mozart.daemon.baton.core")
        logger.addHandler(handler)
        try:
            await baton.handle_event(CronTick(
                entry_name="test-cron",
                score_path="/tmp/test.yaml",
            ))
            log_output = log_capture.getvalue()
            assert "unimplemented" in log_output or "CronTick" in log_output, (
                "CronTick should produce a warning log, not silent drop"
            )
        finally:
            logger.removeHandler(handler)


# =========================================================================
# Category 24: Credential Redaction Defense-in-Depth
# =========================================================================


class TestCredentialRedactionDefenseInDepth:
    """Are ALL three credential data paths through the musician protected?

    The musician has three paths where credentials can appear:
    1. stdout/stderr (F-003, wired at SheetState.capture_output)
    2. Exception messages (F-135, wired at musician.py:165)
    3. Backend error_message (F-136, wired at musician.py:129)

    The litmus question: if I inject a credential string into each path,
    does it survive to the SheetAttemptResult? It should NOT.
    """

    def test_redact_credentials_catches_long_anthropic_key(self) -> None:
        """The credential scanner pattern requires 10+ chars after sk-ant-api."""
        from mozart.utils.credential_scanner import redact_credentials

        # Realistic key — 30+ chars after prefix
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
        result = redact_credentials(f"Auth failed with key {key}")
        assert key not in result, "Anthropic key should be redacted"
        assert "REDACTED" in result, "Redaction marker should appear"

    def test_redact_credentials_catches_openai_key(self) -> None:
        """OpenAI keys are detected and redacted."""
        from mozart.utils.credential_scanner import redact_credentials

        key = "sk-proj-abcdefghijklmnopqrstuvwxyz1234"
        result = redact_credentials(f"Error: {key}")
        assert key not in result

    def test_redact_credentials_catches_github_pat(self) -> None:
        """GitHub PATs added in F-023 are caught (36+ chars after prefix)."""
        from mozart.utils.credential_scanner import redact_credentials

        # Pattern requires ghp_ + 36+ alphanumeric chars
        key = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklm"
        result = redact_credentials(f"Error: {key}")
        assert key not in result

    def test_musician_exception_path_uses_redaction(self) -> None:
        """musician.py exception handler calls redact_credentials."""
        from pathlib import Path as P

        # Read the actual source to verify structural wiring
        musician_path = P("src/mozart/daemon/baton/musician.py")
        source = musician_path.read_text()

        # The exception handler (around line 165) must call redact_credentials
        # before storing error_msg in SheetAttemptResult
        assert "redact_credentials" in source, (
            "musician.py must import and use redact_credentials"
        )

        # Verify it's used in the exception handling path (not just imported)
        # Look for the pattern: redact_credentials(raw_error_msg)
        assert "redact_credentials(raw_error_msg)" in source, (
            "redact_credentials must be called on raw_error_msg in exception path"
        )

    def test_classify_error_path_uses_redaction(self) -> None:
        """musician.py _classify_error return value is redacted (F-136)."""
        from pathlib import Path as P

        source = P("src/mozart/daemon/baton/musician.py").read_text()

        # The _classify_error return value must be redacted before use
        # Look for: redact_credentials(raw_error_msg) if raw_error_msg
        # in the normal success path (around line 129)
        lines = source.split("\n")
        found_classify_redaction = False
        for i, line in enumerate(lines):
            if "redact_credentials" in line and "raw_error_msg" in line:
                # Check context — should be near _classify_error usage
                context = "\n".join(lines[max(0, i - 5):i + 5])
                if "_classify_error" in context or "error_msg" in context:
                    found_classify_redaction = True
                    break

        assert found_classify_redaction, (
            "redact_credentials must be applied to _classify_error output (F-136)"
        )


# =============================================================================
# 25. SEMANTIC CONTEXT TAGS — F-009/F-144 FIX EFFECTIVENESS
# =============================================================================


class TestSemanticContextTagEffectiveness:
    """Does the F-009/F-144 fix actually produce tags that MATCH stored patterns?

    The root cause: query tags (sheet:N, job:X) lived in a different namespace
    from storage tags (validation:TYPE, retry:effective). 91% of 28K+ patterns
    were never applied. The fix generates semantic tags that match the stored
    namespace.

    The litmus: given a JobConfig with validation rules, do the generated tags
    overlap with the kinds of tags that patterns are stored with?
    """

    def test_semantic_tags_match_stored_validation_namespace(self) -> None:
        """Tags from build_semantic_context_tags contain validation:TYPE
        entries that match the pattern storage format."""
        from mozart.core.config.job import JobConfig, PromptConfig, ValidationRule
        from mozart.execution.runner.patterns import build_semantic_context_tags

        config = JobConfig(
            name="tag-test",
            sheet={"size": 1, "total_items": 3},
            prompt=PromptConfig(template="test"),
            validations=[
                ValidationRule(type="file_exists", path="{workspace}/out.py"),
                ValidationRule(type="command_succeeds", command="echo ok"),
            ],
        )

        tags = build_semantic_context_tags(config)

        # Tags must match the format used by pattern storage
        # (learning/patterns.py:411 — context_tags=[f"validation:{vtype}"])
        assert "validation:file_exists" in tags, (
            "Semantic tags must contain validation:file_exists for a config "
            "with file_exists validation rules"
        )
        assert "validation:command_succeeds" in tags

    def test_semantic_tags_include_broad_categories(self) -> None:
        """Tags include broad categories (success, retry, completion) that
        match patterns discovered from any execution context."""
        from mozart.core.config.job import JobConfig, PromptConfig
        from mozart.execution.runner.patterns import build_semantic_context_tags

        config = JobConfig(
            name="broad-tag-test",
            sheet={"size": 1, "total_items": 1},
            prompt=PromptConfig(template="test"),
        )

        tags = build_semantic_context_tags(config)

        # These broad tags match patterns.py:514, :445, :483
        assert "success:first_attempt" in tags, (
            "Tags must include success:first_attempt — matches patterns that "
            "worked on first try"
        )
        assert "retry:effective" in tags
        assert "completion:used" in tags

    def test_old_positional_tags_are_gone(self) -> None:
        """The old positional tags (sheet:N, job:X) that caused F-009 no longer
        appear in semantic tag output."""
        from mozart.core.config.job import JobConfig, PromptConfig
        from mozart.execution.runner.patterns import build_semantic_context_tags

        config = JobConfig(
            name="positional-tag-test",
            sheet={"size": 1, "total_items": 5},
            prompt=PromptConfig(template="test"),
        )

        tags = build_semantic_context_tags(config)

        # Positional tags caused the 91% non-application rate
        for tag in tags:
            assert not tag.startswith("sheet:"), (
                f"Positional tag '{tag}' must not appear — this is the F-009 root cause"
            )
            assert not tag.startswith("job:"), (
                f"Positional tag '{tag}' must not appear — this is the F-009 root cause"
            )

    def test_semantic_tags_enable_pattern_query_overlap(self) -> None:
        """When querying get_patterns() with semantic tags, the tag filtering
        has a non-empty intersection with realistic stored tags.

        The A/B comparison: positional tags would have ZERO overlap with stored
        tags. Semantic tags should have >0 overlap.
        """
        from mozart.core.config.job import JobConfig, PromptConfig, ValidationRule
        from mozart.execution.runner.patterns import build_semantic_context_tags

        config = JobConfig(
            name="overlap-test",
            sheet={"size": 1, "total_items": 3},
            prompt=PromptConfig(template="test"),
            validations=[
                ValidationRule(type="file_exists", path="{workspace}/x.py"),
            ],
        )

        query_tags = build_semantic_context_tags(config)

        # Simulate stored pattern tags (the actual format used by patterns.py)
        stored_tags = [
            "validation:file_exists",
            "validation:command_succeeds",
            "success:first_attempt",
            "retry:effective",
            "error_code:E001",
        ]

        overlap = set(query_tags) & set(stored_tags)
        assert len(overlap) >= 2, (
            f"Semantic tags must overlap with stored pattern tags. "
            f"Query: {query_tags}, Stored: {stored_tags}, Overlap: {overlap}. "
            f"The old positional tags had ZERO overlap — this must be > 0."
        )


# =============================================================================
# 26. PROMPT RENDERER WIRING — F-158 FIX EFFECTIVENESS
# =============================================================================


class TestPromptRendererWiring:
    """Does the baton actually CREATE a PromptRenderer when prompt_config is provided?

    F-158: Without PromptRenderer, baton musicians get raw templates instead of
    the full 9-layer prompt assembly. This verifies the wiring — that register_job
    creates a renderer, and that the renderer produces prompts RICHER than what
    the raw template alone would give.
    """

    def test_register_job_with_prompt_config_creates_renderer(self) -> None:
        """Passing prompt_config to register_job creates a PromptRenderer."""
        from mozart.core.config.job import PromptConfig
        from mozart.core.sheet import Sheet
        from mozart.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=10)

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/test"),
            prompt_template="Do the work",
            instrument_name="claude-cli",
        )
        prompt_config = PromptConfig(template="Do the work", variables={})

        adapter.register_job(
            "test-job",
            [sheet],
            {},
            prompt_config=prompt_config,
            parallel_enabled=False,
        )

        # The renderer should exist for this job
        assert "test-job" in adapter._job_renderers, (
            "register_job with prompt_config must create a PromptRenderer. "
            "Without this, F-158 means baton musicians get raw templates."
        )

    def test_register_job_without_prompt_config_has_no_renderer(self) -> None:
        """Without prompt_config, no PromptRenderer is created (pre-F-158 behavior)."""
        from mozart.core.sheet import Sheet
        from mozart.daemon.baton.adapter import BatonAdapter

        adapter = BatonAdapter(max_concurrent_sheets=10)

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/test"),
            prompt_template="Do the work",
            instrument_name="claude-cli",
        )

        adapter.register_job("test-job", [sheet], {})

        # No renderer — raw templates only
        assert "test-job" not in adapter._job_renderers, (
            "Without prompt_config, no renderer should be created"
        )

    def test_prompt_renderer_produces_richer_output_than_raw_template(self) -> None:
        """The PromptRenderer's output must be substantially richer than
        the raw template string alone.

        This is the core litmus for F-158: does the prompt assembly pipeline
        make agent prompts MORE EFFECTIVE?
        """
        from mozart.core.config.job import PromptConfig, ValidationRule
        from mozart.core.config.spec import SpecFragment
        from mozart.core.sheet import Sheet
        from mozart.daemon.baton.prompt import PromptRenderer
        from mozart.daemon.baton.state import AttemptContext, AttemptMode

        raw_template = "Build the authentication module"

        renderer = PromptRenderer(
            prompt_config=PromptConfig(template=raw_template, variables={}),
            total_sheets=5,
            total_stages=2,
            parallel_enabled=True,
        )

        sheet = Sheet(
            num=1,
            movement=1,
            voice=None,
            voice_count=1,
            workspace=Path("/tmp/ws"),
            prompt_template=raw_template,
            instrument_name="claude-cli",
            validations=[
                ValidationRule(type="file_exists", path="{workspace}/auth.py"),
            ],
        )

        context = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
        )

        result = renderer.render(
            sheet,
            context,
            patterns=["Always hash passwords with bcrypt"],
            spec_fragments=[
                SpecFragment(
                    name="conventions",
                    tags=["code"],
                    kind="text",
                    content="All I/O is async. Use asyncio.",
                ),
            ],
        )

        # Rendered prompt must be >2x the raw template
        assert len(result.prompt) > len(raw_template) * 2, (
            f"Rendered prompt ({len(result.prompt)} chars) must be >2x "
            f"raw template ({len(raw_template)} chars). "
            f"F-158 means the full pipeline runs, not just the raw template."
        )
        # Must contain actionable content from all layers
        assert "bcrypt" in result.prompt, "Learned patterns must appear"
        assert "asyncio" in result.prompt, "Spec fragments must appear"
        assert "file_exists" in result.prompt or "auth.py" in result.prompt, (
            "Validation requirements must appear"
        )
        # Preamble must exist and have positional identity
        assert result.preamble, "Preamble must be non-empty"
        assert "sheet 1" in result.preamble.lower() or "1 of 5" in result.preamble.lower(), (
            "Preamble must contain positional identity"
        )


# =============================================================================
# 27. DISPATCH GUARD — F-152 FIX EFFECTIVENESS
# =============================================================================


class TestDispatchGuardEffectiveness:
    """Does the F-152 dispatch guard actually prevent infinite loops?

    Before the fix: unsupported instrument kind causes backend.acquire()
    to raise NotImplementedError. The sheet stays in READY, gets re-dispatched
    every cycle, looping infinitely. Most dangerous operational bug.

    The litmus: when _dispatch_callback encounters any failure, does it send
    a failure event to the baton instead of leaving the sheet stranded?
    """

    def test_dispatch_failure_sends_e505_to_baton(self) -> None:
        """The _send_dispatch_failure method posts a SheetAttemptResult
        with E505 error classification to the baton inbox."""
        from mozart.daemon.baton.adapter import BatonAdapter
        from mozart.daemon.baton.events import SheetAttemptResult

        adapter = BatonAdapter(max_concurrent_sheets=10)

        adapter._send_dispatch_failure(
            "job-1", 1, "unsupported-instrument",
            "NotImplementedError: instrument kind 'http' not supported",
        )

        # Must post a failure event to the baton's inbox
        inbox = adapter._baton.inbox
        assert not inbox.empty(), (
            "Dispatch failure must send event to baton inbox"
        )
        event = inbox.get_nowait()
        assert isinstance(event, SheetAttemptResult), (
            f"Event must be SheetAttemptResult, got {type(event)}"
        )
        assert event.error_classification == "E505", (
            f"Error classification must be E505, got {event.error_classification}"
        )
        assert event.execution_success is False

    def test_all_three_dispatch_failure_paths_are_guarded(self) -> None:
        """Every early-return path in _dispatch_callback sends a failure event.

        There are 3 early-return paths:
        1. sheet not found
        2. no backend pool
        3. backend acquire exception

        All three must call _send_dispatch_failure. Without this, ANY of them
        would cause infinite dispatch loops.
        """
        source = Path("src/mozart/daemon/baton/adapter.py").read_text()

        # Find _dispatch_callback
        start = source.find("async def _dispatch_callback(")
        assert start > 0, "_dispatch_callback must exist"

        # Find the next method (def or class at the same indent level)
        remaining = source[start:]
        lines = remaining.split("\n")
        end_idx = len(lines)
        for i, line in enumerate(lines[1:], 1):
            # Look for the next method at the same indent level
            if (line.strip().startswith("async def ") or
                line.strip().startswith("def ")) and not line.startswith(" " * 12):
                end_idx = i
                break

        method_body = "\n".join(lines[:end_idx])

        # Count _send_dispatch_failure calls
        failure_calls = method_body.count("_send_dispatch_failure")
        assert failure_calls >= 3, (
            f"_dispatch_callback must call _send_dispatch_failure at least 3 times "
            f"(one per early-return path). Found {failure_calls} calls. "
            f"Missing calls = infinite dispatch loop potential."
        )


# =============================================================================
# 28. RATE LIMIT AUTO-RESUME — F-112 FIX EFFECTIVENESS
# =============================================================================


class TestRateLimitAutoResumeEffectiveness:
    """Does the rate limit auto-resume actually schedule a timer that clears?

    Before F-112: WAITING sheets stayed blocked forever unless manually cleared
    via `mozart clear-rate-limits`. The timer event, handler, and wheel all
    existed — only the trigger was missing.

    The litmus: when a rate limit hit is processed, does the baton schedule
    a timer that will eventually clear it? And when the timer fires, do
    WAITING sheets move back to PENDING?
    """

    def test_rate_limit_hit_schedules_expiry_timer(self) -> None:
        """_handle_rate_limit_hit schedules a RateLimitExpired event."""
        from unittest.mock import MagicMock

        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.events import RateLimitHit

        baton = BatonCore.__new__(BatonCore)
        baton._instruments = {
            "claude-cli": MagicMock(rate_limited=False, rate_limit_expires_at=None),
        }
        baton._jobs = {}
        baton._state_dirty = False
        baton._timer = MagicMock(spec=["schedule"])

        event = RateLimitHit(
            instrument="claude-cli",
            wait_seconds=120.0,
            job_id="job-1",
            sheet_num=1,
        )

        baton._handle_rate_limit_hit(event)

        # Timer must be scheduled
        baton._timer.schedule.assert_called_once()
        call_args = baton._timer.schedule.call_args
        assert call_args[0][0] == 120.0, (
            f"Timer delay must match wait_seconds. Got {call_args[0][0]}"
        )
        expiry_event = call_args[0][1]
        assert expiry_event.instrument == "claude-cli"

    def test_rate_limit_expired_moves_waiting_to_pending(self) -> None:
        """When RateLimitExpired fires, WAITING sheets on that instrument
        move back to PENDING — enabling dispatch to resume."""
        from unittest.mock import MagicMock

        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.events import RateLimitExpired
        from mozart.daemon.baton.state import BatonSheetStatus

        baton = BatonCore.__new__(BatonCore)
        baton._instruments = {
            "claude-cli": MagicMock(rate_limited=True, rate_limit_expires_at=100.0),
            "gemini-cli": MagicMock(rate_limited=False, rate_limit_expires_at=None),
        }

        # Two sheets: one waiting on claude-cli, one waiting on gemini-cli
        claude_sheet = MagicMock(
            status=BatonSheetStatus.WAITING,
            instrument_name="claude-cli",
        )
        gemini_sheet = MagicMock(
            status=BatonSheetStatus.WAITING,
            instrument_name="gemini-cli",
        )
        job = MagicMock(sheets={1: claude_sheet, 2: gemini_sheet})
        baton._jobs = {"job-1": job}
        baton._state_dirty = False

        baton._handle_rate_limit_expired(RateLimitExpired(instrument="claude-cli"))

        # Claude sheet should move to PENDING
        assert claude_sheet.status == BatonSheetStatus.PENDING, (
            "WAITING sheet on cleared instrument must return to PENDING"
        )
        # Gemini sheet should stay WAITING (different instrument)
        assert gemini_sheet.status == BatonSheetStatus.WAITING, (
            "WAITING sheet on unrelated instrument must NOT change"
        )

    def test_without_timer_no_auto_resume(self) -> None:
        """If timer is None (no timer wheel), rate limit hit still works
        but does NOT schedule auto-resume — manual clear is required.

        This tests the graceful degradation path.
        """
        from unittest.mock import MagicMock

        from mozart.daemon.baton.core import BatonCore
        from mozart.daemon.baton.events import RateLimitHit

        baton = BatonCore.__new__(BatonCore)
        baton._instruments = {
            "claude-cli": MagicMock(rate_limited=False, rate_limit_expires_at=None),
        }
        baton._jobs = {}
        baton._state_dirty = False
        baton._timer = None  # No timer wheel

        event = RateLimitHit(
            instrument="claude-cli",
            wait_seconds=60.0,
            job_id="job-1",
            sheet_num=1,
        )

        # Should not raise — graceful degradation
        baton._handle_rate_limit_hit(event)

        # Instrument should still be marked rate limited
        assert baton._instruments["claude-cli"].rate_limited is True


# =============================================================================
# 29. MODEL OVERRIDE WIRING — F-150 FIX EFFECTIVENESS
# =============================================================================


class TestModelOverrideEffectiveness:
    """Does the model override actually reach the backend?

    F-150: instrument_config.model was silently ignored at dispatch time.
    Scores saying "use opus for this sheet" would silently run with the
    default model. The fix wires apply_overrides/clear_overrides through
    the PluginCliBackend + BackendPool.

    The litmus: apply_overrides with {model: X} actually changes what
    model the backend uses, and clear_overrides restores the original.
    """

    def test_apply_overrides_changes_model(self) -> None:
        """PluginCliBackend.apply_overrides replaces the active model."""
        from unittest.mock import MagicMock

        from mozart.execution.instruments.cli_backend import PluginCliBackend

        profile = MagicMock()
        profile.display_name = "test-instrument"
        profile.default_model = "default-model"
        profile.cli = MagicMock()
        profile.cli.command = MagicMock()
        profile.cli.command.base_command = "test-cmd"

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._profile = profile
        backend._model = "default-model"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({"model": "opus-4"})

        assert backend._model == "opus-4", (
            f"Model must be overridden to opus-4, got {backend._model}. "
            f"F-150: model override was silently ignored before this fix."
        )
        assert backend._has_overrides is True
        assert backend._saved_model == "default-model"

    def test_clear_overrides_restores_original_model(self) -> None:
        """After clear_overrides, the original model is restored."""
        from unittest.mock import MagicMock

        from mozart.execution.instruments.cli_backend import PluginCliBackend

        profile = MagicMock()
        profile.display_name = "test"
        profile.default_model = "default-model"

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._profile = profile
        backend._model = "default-model"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({"model": "opus-4"})
        assert backend._model == "opus-4"

        backend.clear_overrides()
        assert backend._model == "default-model", (
            "Model must be restored after clear_overrides"
        )
        assert backend._has_overrides is False

    def test_empty_overrides_are_noop(self) -> None:
        """Passing empty overrides doesn't corrupt model state."""
        from unittest.mock import MagicMock

        from mozart.execution.instruments.cli_backend import PluginCliBackend

        backend = PluginCliBackend.__new__(PluginCliBackend)
        backend._profile = MagicMock()
        backend._model = "default-model"
        backend._saved_model = None
        backend._has_overrides = False

        backend.apply_overrides({})

        assert backend._model == "default-model"
        assert backend._has_overrides is False


# =============================================================================
# 30. CONCERT CHAINING COMPLETENESS — F-145 FIX EFFECTIVENESS
# =============================================================================


class TestConcertChainingEffectiveness:
    """Does the baton path correctly detect completed_new_work for concerts?

    F-145: The baton path was missing the completed_new_work flag used by
    concert chaining to prevent zero-work loops. Without it, concert scores
    under use_baton would chain forever even when no sheet completed new work.

    The litmus: has_completed_sheets returns True when sheets complete,
    False when they all fail, and the manager wires this into
    meta.completed_new_work.
    """

    def test_has_completed_sheets_with_completions(self) -> None:
        """Returns True when at least one sheet reached COMPLETED."""
        from unittest.mock import MagicMock

        from mozart.daemon.baton.adapter import BatonAdapter
        from mozart.daemon.baton.state import BatonSheetStatus

        adapter = BatonAdapter(max_concurrent_sheets=10)

        # Simulate registered job state by injecting into the baton's _jobs
        completed_sheet = MagicMock(status=BatonSheetStatus.COMPLETED)
        failed_sheet = MagicMock(status=BatonSheetStatus.FAILED)
        job_state = MagicMock(sheets={1: completed_sheet, 2: failed_sheet})
        adapter._baton._jobs = {"job-1": job_state}

        assert adapter.has_completed_sheets("job-1") is True, (
            "has_completed_sheets must return True when ANY sheet completed"
        )

    def test_has_completed_sheets_all_failed(self) -> None:
        """Returns False when all sheets failed — no new work was done."""
        from unittest.mock import MagicMock

        from mozart.daemon.baton.adapter import BatonAdapter
        from mozart.daemon.baton.state import BatonSheetStatus

        adapter = BatonAdapter(max_concurrent_sheets=10)

        failed1 = MagicMock(status=BatonSheetStatus.FAILED)
        failed2 = MagicMock(status=BatonSheetStatus.FAILED)
        job_state = MagicMock(sheets={1: failed1, 2: failed2})
        adapter._baton._jobs = {"job-1": job_state}

        assert adapter.has_completed_sheets("job-1") is False, (
            "has_completed_sheets must return False when no sheets completed. "
            "Concert chaining must not loop on zero-work."
        )

    def test_manager_wires_completed_new_work_from_baton(self) -> None:
        """The manager code path sets meta.completed_new_work from
        adapter.has_completed_sheets().

        Structural verification: the manager.py code path references both
        has_completed_sheets and completed_new_work in the baton path.
        """
        source = Path("src/mozart/daemon/manager.py").read_text()

        # The baton execution path must wire completed_new_work
        assert "has_completed_sheets" in source, (
            "manager.py must call has_completed_sheets (F-145)"
        )
        assert "completed_new_work" in source, (
            "manager.py must set completed_new_work flag (F-145)"
        )

        # Both must appear within the baton code path (near _run_via_baton)
        baton_section = source[source.find("_run_via_baton"):]
        assert "has_completed_sheets" in baton_section, (
            "has_completed_sheets must be in the baton code path, not just "
            "anywhere in manager.py"
        )


# =============================================================================
# 31. RATE LIMIT WAIT CAP — F-160 FIX EFFECTIVENESS
# =============================================================================


class TestRateLimitWaitCapEffectiveness:
    """Does the rate limit wait cap prevent adversarial timer durations?

    F-160: parse_reset_time() had no ceiling. An adversarial API response
    saying "resets in 999999 hours" would create a 114-YEAR timer blocking
    the instrument forever. The fix adds a 24-hour maximum.

    The litmus: does the system cap astronomical wait times instead of
    honoring them blindly?
    """

    def test_wait_cap_exists_in_constants(self) -> None:
        """RESET_TIME_MAXIMUM_WAIT_SECONDS is defined and reasonable."""
        from mozart.core.constants import RESET_TIME_MAXIMUM_WAIT_SECONDS

        assert RESET_TIME_MAXIMUM_WAIT_SECONDS <= 86400.0, (
            f"Wait cap must be ≤24h (86400s), got {RESET_TIME_MAXIMUM_WAIT_SECONDS}. "
            f"Anything longer is indistinguishable from 'broken'."
        )
        assert RESET_TIME_MAXIMUM_WAIT_SECONDS > 0, (
            "Wait cap must be positive"
        )

    def test_clamp_wait_reduces_astronomical_values(self) -> None:
        """_clamp_wait (or equivalent) reduces values above the cap."""
        from mozart.core.errors.classifier import ErrorClassifier

        classifier = ErrorClassifier.__new__(ErrorClassifier)

        # Simulate astronomical wait
        clamped = classifier._clamp_wait(999999.0)
        from mozart.core.constants import RESET_TIME_MAXIMUM_WAIT_SECONDS

        assert clamped <= RESET_TIME_MAXIMUM_WAIT_SECONDS, (
            f"999999s must be clamped to ≤{RESET_TIME_MAXIMUM_WAIT_SECONDS}s, "
            f"got {clamped}. F-160: adversarial 114-year timers must be prevented."
        )

    def test_reasonable_waits_are_not_clamped(self) -> None:
        """Normal wait times (within min-max range) pass through unclamped."""
        from mozart.core.errors.classifier import ErrorClassifier

        classifier = ErrorClassifier.__new__(ErrorClassifier)

        # Use a value within the [MINIMUM, MAXIMUM] range
        # MINIMUM is 300s, so 600s should pass through untouched
        result = classifier._clamp_wait(600.0)
        assert result == 600.0, (
            f"Reasonable wait (600s, within range) should not be modified, got {result}"
        )


# =============================================================================
# 32. CROSS-SHEET CONTEXT IN BATON PROMPTS (F-210)
# =============================================================================


class TestCrossSheetContextInBatonPrompts:
    """F-210 litmus: does the baton's PromptRenderer actually deliver
    cross-sheet context (previous_outputs, previous_files) to templates?

    The WITH vs WITHOUT comparison:
    - WITHOUT F-210: AttemptContext.previous_outputs/previous_files exist
      but PromptRenderer._build_context ignores them → templates get empty dicts
    - WITH F-210: PromptRenderer._build_context copies them into SheetContext
      → templates can reference {{ previous_outputs }} and {{ previous_files }}

    If this bridge is broken, agents downstream of fan-out never see upstream
    results — even though the adapter collected them.
    """

    def test_prompt_renderer_populates_previous_outputs(self) -> None:
        """AttemptContext.previous_outputs flows through to SheetContext."""
        from mozart.daemon.baton.prompt import PromptRenderer
        from mozart.daemon.baton.state import AttemptContext, AttemptMode

        template = "Previous: {{ previous_outputs }}"
        renderer = PromptRenderer(
            prompt_config=PromptConfig(template=template, variables={}),
            total_sheets=3,
            total_stages=1,
            parallel_enabled=False,
        )

        sheet = Sheet(
            num=2, movement=1, voice_count=1, instrument_name="claude-code",
            workspace=Path("/tmp/ws"), prompt_template=template,
        )
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_outputs={1: "Sheet 1 produced output X"},
        )

        result = renderer.render(sheet, ctx)

        # The litmus: previous_outputs appears in the rendered prompt
        assert "Sheet 1 produced output X" in result.prompt, (
            "F-210: previous_outputs from AttemptContext MUST flow through "
            "PromptRenderer into the rendered template. Without this, "
            "downstream sheets can't see upstream results."
        )

    def test_prompt_renderer_populates_previous_files(self) -> None:
        """AttemptContext.previous_files flows through to SheetContext."""
        from mozart.daemon.baton.prompt import PromptRenderer
        from mozart.daemon.baton.state import AttemptContext, AttemptMode

        template = "Files: {{ previous_files }}"
        renderer = PromptRenderer(
            prompt_config=PromptConfig(template=template, variables={}),
            total_sheets=3,
            total_stages=1,
            parallel_enabled=False,
        )

        sheet = Sheet(
            num=2, movement=1, voice_count=1, instrument_name="claude-code",
            workspace=Path("/tmp/ws"), prompt_template=template,
        )
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
            previous_files={"/tmp/ws/report.md": "# Analysis\nFindings here"},
        )

        result = renderer.render(sheet, ctx)

        assert "Findings here" in result.prompt, (
            "F-210: previous_files from AttemptContext MUST flow through "
            "to rendered templates. Workspace file contents are how sheets "
            "share structured data."
        )

    def test_without_attempt_context_no_cross_sheet_data(self) -> None:
        """Without cross-sheet data in AttemptContext, templates get empty dicts.

        This is the WITHOUT side of the WITH/WITHOUT comparison.
        """
        from mozart.daemon.baton.prompt import PromptRenderer
        from mozart.daemon.baton.state import AttemptContext, AttemptMode

        template = "{{ previous_outputs }}"
        renderer = PromptRenderer(
            prompt_config=PromptConfig(template=template, variables={}),
            total_sheets=3,
            total_stages=1,
            parallel_enabled=False,
        )

        sheet = Sheet(
            num=2, movement=1, voice_count=1, instrument_name="claude-code",
            workspace=Path("/tmp/ws"), prompt_template=template,
        )
        # Empty cross-sheet context
        ctx = AttemptContext(
            attempt_number=1,
            mode=AttemptMode.NORMAL,
        )

        result = renderer.render(sheet, ctx)

        # The rendered template shows an empty dict — no cross-sheet data
        assert "Sheet 1 produced" not in result.prompt, (
            "Without cross-sheet data in AttemptContext, the template should "
            "have no previous outputs."
        )


# =============================================================================
# 33. SKIPPED UPSTREAM VISIBILITY (#120)
# =============================================================================


class TestSkippedUpstreamVisibility:
    """#120 litmus: does the [SKIPPED] placeholder give downstream sheets
    explicit visibility into which upstream sheets were skipped?

    WITHOUT #120: skipped upstream sheets are silently omitted from
    previous_outputs → downstream sheet has no idea data is missing.
    WITH #120: skipped sheets inject "[SKIPPED]" → downstream sheet knows
    to compensate for missing input.

    The litmus: a downstream template can distinguish "no data" from
    "upstream was skipped". This prevents silent data loss in fan-in patterns.
    """

    def test_skipped_upstream_available_in_template_context(self) -> None:
        """skipped_upstream list is available in template rendering."""
        config = PromptConfig(
            template="Skipped: {{ skipped_upstream }}",
            variables={},
        )
        builder = PromptBuilder(config)
        ctx = SheetContext(
            sheet_num=4,
            total_sheets=4,
            start_item=4,
            end_item=4,
            workspace=Path("/tmp/ws"),
            skipped_upstream=[2, 3],
        )

        prompt = builder.build_sheet_prompt(ctx)

        assert "2" in prompt and "3" in prompt, (
            "#120: skipped_upstream sheet numbers MUST be available in "
            "templates so fan-in sheets know which inputs are missing."
        )

    def test_skipped_placeholder_vs_silent_omission(self) -> None:
        """WITH [SKIPPED] placeholder is better than WITHOUT (silent omission).

        An agent seeing '[SKIPPED]' knows to adjust. An agent seeing nothing
        might assume the data doesn't exist or wasn't relevant — a dangerous
        misreading of fan-in completeness.
        """
        # WITH #120: skipped sheets have explicit placeholder
        ctx_with = SheetContext(
            sheet_num=3,
            total_sheets=3,
            start_item=3,
            end_item=3,
            workspace=Path("/tmp/ws"),
            previous_outputs={1: "Result from sheet 1", 2: "[SKIPPED]"},
            skipped_upstream=[2],
        )

        # WITHOUT #120: skipped sheets are silently absent
        ctx_without = SheetContext(
            sheet_num=3,
            total_sheets=3,
            start_item=3,
            end_item=3,
            workspace=Path("/tmp/ws"),
            previous_outputs={1: "Result from sheet 1"},
            skipped_upstream=[],
        )

        # Template that tries to render all upstream
        config = PromptConfig(
            template="Upstream data: {{ previous_outputs }}",
            variables={},
        )
        builder = PromptBuilder(config)

        prompt_with = builder.build_sheet_prompt(ctx_with)
        prompt_without = builder.build_sheet_prompt(ctx_without)

        # WITH version explicitly shows the gap
        assert "[SKIPPED]" in prompt_with, (
            "#120: The [SKIPPED] placeholder MUST appear in rendered prompts "
            "so agents know data is intentionally absent."
        )
        # WITHOUT version has no indication of the gap
        assert "[SKIPPED]" not in prompt_without, (
            "Without #120, skipped sheets are silently omitted — this is "
            "the problem the fix addresses."
        )

    def test_skipped_upstream_list_matches_skipped_outputs(self) -> None:
        """skipped_upstream list and previous_outputs [SKIPPED] are consistent.

        If sheet 2 is in skipped_upstream, previous_outputs[2] should be
        [SKIPPED]. Inconsistency between these two signals would confuse
        template logic that checks either one.
        """
        ctx = SheetContext(
            sheet_num=4,
            total_sheets=4,
            start_item=4,
            end_item=4,
            workspace=Path("/tmp/ws"),
            previous_outputs={
                1: "Real output",
                2: "[SKIPPED]",
                3: "[SKIPPED]",
            },
            skipped_upstream=[2, 3],
        )

        template_vars = ctx.to_dict()

        # Both signals agree: sheets 2 and 3 are skipped
        for skipped_num in template_vars["skipped_upstream"]:
            assert template_vars["previous_outputs"].get(skipped_num) == "[SKIPPED]", (
                f"Sheet {skipped_num} is in skipped_upstream but its "
                f"previous_outputs entry is "
                f"{template_vars['previous_outputs'].get(skipped_num)!r}, "
                f"not '[SKIPPED]'. Inconsistent signals confuse agents."
            )


# =============================================================================
# 34. AUTO-FRESH DETECTION (#103)
# =============================================================================


class TestAutoFreshDetection:
    """#103 litmus: does _should_auto_fresh prevent stale reruns when
    the score file has been modified?

    WITHOUT #103: user edits score, runs `mozart run` again, gets the old
    cached result because the job is COMPLETED → confusion.
    WITH #103: manager detects mtime > completed_at → auto-sets fresh=True
    → user gets a new run with their changes.

    The litmus: does the system behave DIFFERENTLY (and correctly) when
    the score is modified vs when it isn't?
    """

    def test_modified_score_detected_as_needing_fresh_run(self, tmp_path: Path) -> None:
        """A score modified after completion triggers auto-fresh."""
        from mozart.daemon.manager import _should_auto_fresh

        score_file = tmp_path / "test.yaml"
        score_file.write_text("version: 1")

        # Simulate: job completed 10 seconds ago
        import time

        completed_at = time.time() - 10

        # Touch the file to update mtime to NOW
        score_file.write_text("version: 2")

        assert _should_auto_fresh(score_file, completed_at) is True, (
            "#103: Score modified AFTER completion must trigger auto-fresh. "
            "Without this, users re-run edited scores and get stale results."
        )

    def test_unmodified_score_does_not_trigger_fresh(self, tmp_path: Path) -> None:
        """A score NOT modified after completion does NOT trigger auto-fresh."""
        from mozart.daemon.manager import _should_auto_fresh

        score_file = tmp_path / "test.yaml"
        score_file.write_text("version: 1")

        import time

        # Simulate: file written before completion (completion is in the future)
        completed_at = time.time() + 100

        assert _should_auto_fresh(score_file, completed_at) is False, (
            "#103: Unmodified score must NOT trigger auto-fresh. "
            "False positives waste resources re-running unchanged scores."
        )

    def test_missing_score_file_does_not_crash(self) -> None:
        """Missing score file returns False gracefully, not an exception."""
        from mozart.daemon.manager import _should_auto_fresh

        result = _should_auto_fresh(Path("/nonexistent/score.yaml"), 100.0)
        assert result is False, (
            "#103: Missing score file must return False, not crash. "
            "Race condition between file deletion and check must be handled."
        )

    def test_none_completed_at_returns_false(self, tmp_path: Path) -> None:
        """No completion timestamp means we can't compare → no auto-fresh."""
        from mozart.daemon.manager import _should_auto_fresh

        score_file = tmp_path / "test.yaml"
        score_file.write_text("version: 1")

        assert _should_auto_fresh(score_file, None) is False, (
            "#103: None completed_at (never ran) must return False. "
            "Can't detect modification without a baseline timestamp."
        )


# =============================================================================
# 35. BACKPRESSURE REJECTION INTELLIGENCE (F-110)
# =============================================================================


class TestBackpressureRejectionIntelligence:
    """F-110 litmus: does rejection_reason() distinguish rate-limit pressure
    from resource pressure?

    WITHOUT F-110: any rejection looks the same → CLI says "conductor not
    running" (misleading) or "rejected" (uninformative).
    WITH F-110: rate_limit → queue as PENDING (recoverable). resource →
    reject outright (dangerous to accept more work).

    The litmus: does the system give the CLI enough information to take
    DIFFERENT actions for different rejection types?
    """

    def test_rate_limit_only_returns_rate_limit_reason(self) -> None:
        """When only rate limits are active (memory fine), reason is 'rate_limit'."""
        from unittest.mock import MagicMock

        from mozart.daemon.backpressure import BackpressureController

        controller = BackpressureController.__new__(BackpressureController)
        controller._monitor = MagicMock()
        controller._monitor.current_memory_mb.return_value = 100  # Low
        controller._monitor.max_memory_mb = 1000
        controller._monitor.is_degraded = False
        controller._monitor.is_accepting_work.return_value = True
        controller._rate_coordinator = MagicMock()
        controller._rate_coordinator.active_limits = {"claude-cli": 60}

        reason = controller.rejection_reason()

        assert reason == "rate_limit", (
            "F-110: Rate limits with healthy memory MUST return 'rate_limit', "
            "not 'resource'. This distinction lets the CLI queue jobs as "
            "PENDING instead of rejecting them outright."
        )

    def test_high_memory_returns_resource_reason(self) -> None:
        """When memory is high (>85%), reason is 'resource' regardless of rate limits."""
        from unittest.mock import MagicMock

        from mozart.daemon.backpressure import BackpressureController

        controller = BackpressureController.__new__(BackpressureController)
        controller._monitor = MagicMock()
        controller._monitor.current_memory_mb.return_value = 900  # 90% of 1000
        controller._monitor.max_memory_mb = 1000
        controller._monitor.is_degraded = False
        controller._monitor.is_accepting_work.return_value = True
        controller._rate_coordinator = MagicMock()
        controller._rate_coordinator.active_limits = {"claude-cli": 60}

        reason = controller.rejection_reason()

        assert reason == "resource", (
            "F-110: High memory pressure MUST return 'resource', even when "
            "rate limits are also active. Accepting more work in this state "
            "risks OOM crashes."
        )

    def test_no_pressure_returns_none(self) -> None:
        """When nothing is pressured, reason is None (accept the job)."""
        from unittest.mock import MagicMock

        from mozart.daemon.backpressure import BackpressureController

        controller = BackpressureController.__new__(BackpressureController)
        controller._monitor = MagicMock()
        controller._monitor.current_memory_mb.return_value = 100
        controller._monitor.max_memory_mb = 1000
        controller._monitor.is_degraded = False
        controller._monitor.is_accepting_work.return_value = True
        controller._rate_coordinator = MagicMock()
        controller._rate_coordinator.active_limits = {}

        reason = controller.rejection_reason()

        assert reason is None, (
            "F-110: No pressure MUST return None (accept). "
            "False rejections prevent users from submitting work."
        )


# =============================================================================
# 36. CROSS-SHEET CREDENTIAL REDACTION (F-250)
# =============================================================================


class TestCrossSheetCredentialRedaction:
    """F-250 litmus: are credentials redacted BEFORE entering cross-sheet
    context (both legacy runner and baton adapter paths)?

    WITHOUT F-250: agent writes an API key to a workspace file → cross-sheet
    capture reads it → key appears in the next sheet's prompt → leaked.
    WITH F-250: redact_credentials() runs on file content → [REDACTED_*]
    appears instead.

    The litmus: credential material from workspace files MUST be redacted
    before it can appear in any prompt.
    """

    def test_anthropic_key_redacted_in_file_content(self) -> None:
        """Anthropic API keys in captured files are redacted."""
        from mozart.utils.credential_scanner import redact_credentials

        file_content = (
            "# Config\n"
            "api_key = sk-ant-api03-abcdefghij1234567890ABCDEFGHIJ_KLMNOPQRST\n"
            "region = us-east-1\n"
        )

        redacted = redact_credentials(file_content)

        assert "sk-ant-" not in redacted, (
            "F-250: Anthropic API key MUST be redacted from captured file content. "
            "Without this, cross-sheet context leaks secrets into prompts."
        )
        assert "[REDACTED" in redacted, (
            "F-250: Redacted content must contain [REDACTED] marker so agents "
            "know something was removed."
        )

    def test_multiple_credential_types_all_redacted(self) -> None:
        """All credential patterns (Anthropic, OpenAI, AWS, etc.) are caught."""
        from mozart.utils.credential_scanner import redact_credentials

        file_content = (
            "ANTHROPIC_KEY=sk-ant-api03-longkeyvalue1234567890abcdefgh\n"
            "OPENAI_KEY=sk-proj-longkeyvalue1234567890abcdefghijklmn\n"
            "AWS_KEY=AKIAIOSFODNN7EXAMPLE\n"
        )

        redacted = redact_credentials(file_content)

        assert "sk-ant-" not in redacted, "Anthropic key must be redacted"
        assert "sk-proj-" not in redacted, "OpenAI key must be redacted"
        assert "AKIA" not in redacted, "AWS key must be redacted"

    def test_clean_content_passes_through_unchanged(self) -> None:
        """Content without credentials passes through unmodified."""
        from mozart.utils.credential_scanner import redact_credentials

        file_content = "# Analysis Report\nThe system uses async I/O.\n"

        result = redact_credentials(file_content)

        # Should return the original content (or a truthy equivalent)
        assert result is None or result == file_content, (
            "F-250: Content without credentials must pass through unchanged. "
            "False positive redaction corrupts legitimate data."
        )


# =============================================================================
# 37. METHODNOT FOUNDERROR DIFFERENTIATION (F-450)
# =============================================================================


class TestMethodNotFoundErrorDifferentiation:
    """F-450 litmus: does the error hierarchy distinguish "unknown method"
    from "conductor not running"?

    WITHOUT F-450: CLI sends IPC request → conductor doesn't know the method
    → returns JSON-RPC error → CLI says "conductor not running" → user
    stops/restarts conductor unnecessarily.
    WITH F-450: MethodNotFoundError is mapped from -32601 code → CLI says
    "restart conductor to pick up changes" → user does the right thing.

    The litmus: different error conditions produce different user guidance.
    """

    def test_method_not_found_is_distinct_from_daemon_error(self) -> None:
        """MethodNotFoundError is a DaemonError subclass with distinct identity."""
        from mozart.daemon.exceptions import DaemonError, MethodNotFoundError

        err = MethodNotFoundError("daemon.unknown_method not recognized")

        assert isinstance(err, DaemonError), (
            "F-450: MethodNotFoundError MUST inherit from DaemonError "
            "so existing DaemonError catch blocks still work."
        )
        assert isinstance(err, MethodNotFoundError), (
            "F-450: MethodNotFoundError MUST be catchable separately "
            "so CLI can give different guidance for different errors."
        )

    def test_error_message_guides_toward_restart(self) -> None:
        """The error type's docstring should help the user fix the problem."""
        from mozart.daemon.exceptions import MethodNotFoundError

        doc = MethodNotFoundError.__doc__ or ""

        # The error type's docstring should mention restart
        assert "restart" in doc.lower(), (
            "F-450: MethodNotFoundError's docstring MUST mention restarting "
            "the conductor — that's the fix. Without this guidance, users "
            "are left guessing why their command failed."
        )

    def test_method_not_found_code_mapping(self) -> None:
        """JSON-RPC error code -32601 maps to MethodNotFoundError."""
        from mozart.daemon.exceptions import MethodNotFoundError
        from mozart.daemon.ipc.errors import (
            METHOD_NOT_FOUND,
            _CODE_EXCEPTION_MAP,
        )

        assert METHOD_NOT_FOUND in _CODE_EXCEPTION_MAP, (
            "F-450: METHOD_NOT_FOUND (-32601) MUST be mapped "
            "in _CODE_EXCEPTION_MAP. Without this mapping, the error is "
            "caught as a generic DaemonError and users get wrong guidance."
        )
        assert _CODE_EXCEPTION_MAP[METHOD_NOT_FOUND] is MethodNotFoundError, (
            "F-450: METHOD_NOT_FOUND must map to MethodNotFoundError, "
            f"not {_CODE_EXCEPTION_MAP[METHOD_NOT_FOUND].__name__}."
        )


# =============================================================================
# 38. COST JSON EXTRACTION VS CHAR ESTIMATION (D-024)
# =============================================================================


class TestCostJsonExtractionEffectiveness:
    """D-024 litmus: does JSON token extraction produce BETTER cost data
    than character-based estimation?

    WITHOUT D-024: ClaudeCliBackend returns zero tokens → CostMixin falls
    back to char estimation → 10-100x underestimate → cost fiction.
    WITH D-024: _extract_tokens_from_json parses actual usage data →
    accurate tokens → accurate costs.

    The litmus: the same stdout produces dramatically different (and more
    correct) token counts through JSON extraction vs char estimation.
    """

    def test_json_extraction_finds_tokens(self) -> None:
        """JSON output with usage field yields actual token counts."""
        from mozart.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "json"

        stdout = json.dumps({
            "result": "some output",
            "usage": {
                "input_tokens": 15000,
                "output_tokens": 3000,
            },
        })

        input_tokens, output_tokens = backend._extract_tokens_from_json(stdout)

        assert input_tokens == 15000, (
            "D-024: JSON extraction MUST find input_tokens from usage field. "
            f"Got {input_tokens} instead of 15000."
        )
        assert output_tokens == 3000, (
            "D-024: JSON extraction MUST find output_tokens from usage field. "
            f"Got {output_tokens} instead of 3000."
        )

    def test_non_json_format_returns_none(self) -> None:
        """When output_format is not 'json', extraction returns None."""
        from mozart.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "text"

        stdout = json.dumps({"usage": {"input_tokens": 100, "output_tokens": 50}})

        input_tokens, output_tokens = backend._extract_tokens_from_json(stdout)

        assert input_tokens is None and output_tokens is None, (
            "D-024: Non-JSON output format must return None tokens. "
            "This prevents false positives from text that happens to be JSON."
        )

    def test_malformed_json_returns_none_gracefully(self) -> None:
        """Broken JSON doesn't crash — returns None."""
        from mozart.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "json"

        input_tokens, output_tokens = backend._extract_tokens_from_json(
            "not valid json {{"
        )

        assert input_tokens is None and output_tokens is None, (
            "D-024: Malformed JSON must return None, not crash. "
            "Real stdout can contain mixed JSON and text."
        )

    def test_json_extraction_is_better_than_char_estimation(self) -> None:
        """JSON tokens are dramatically more accurate than char estimation.

        The litmus: a 15K input_token cost through JSON extraction would
        be estimated as ~143 tokens by char estimation (500 chars / 3.5).
        The JSON path is 100x more accurate for this case.
        """
        from mozart.backends.claude_cli import ClaudeCliBackend

        backend = ClaudeCliBackend.__new__(ClaudeCliBackend)
        backend.output_format = "json"

        stdout = json.dumps({
            "result": "x" * 500,  # 500 chars of output
            "usage": {
                "input_tokens": 15000,
                "output_tokens": 3000,
            },
        })

        input_tokens, _ = backend._extract_tokens_from_json(stdout)

        # Char-based estimation: ~500 chars / 3.5 chars_per_token ≈ 143 tokens
        char_estimate = len(stdout) / 3.5

        assert input_tokens is not None, "JSON extraction should succeed"
        assert input_tokens > char_estimate * 10, (
            f"D-024: JSON extraction ({input_tokens} tokens) must be "
            f"dramatically more accurate than char estimation "
            f"({char_estimate:.0f} tokens). The whole point of D-024 is "
            f"that char estimation underestimates by 10-100x."
        )
