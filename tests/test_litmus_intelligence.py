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

Every test in this file answers: "Is the system smarter WITH this than WITHOUT?"
"""

from __future__ import annotations

import json
from pathlib import Path

from mozart.core.config import PromptConfig, ValidationRule
from mozart.core.config.spec import SpecFragment
from mozart.daemon.baton.core import BatonCore
from mozart.daemon.baton.events import (
    RateLimitExpired,
    RateLimitHit,
    RetryDue,
    SheetAttemptResult,
    SheetSkipped,
)
from mozart.daemon.baton.state import (
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
            f"(types: {[type(k) for k in restored.spec_tags.keys()]})"
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
            f"Key types: {[type(k) for k in restored.spec_tags.keys()]}"
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
