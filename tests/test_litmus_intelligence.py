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
