# Mozart AI Compose: Four Disciplines Gap Analysis & Architecture Design

**Date:** 2026-03-01
**Status:** Design complete, awaiting approval
**Scope:** Full gap analysis of Mozart against the Four Disciplines of AI Input Engineering
**Method:** 7 parallel deep-dive agents analyzed every subsystem

---

## Executive Summary

Mozart is an 83K-line orchestration system with sophisticated execution infrastructure: DAG dependencies, parallel fan-out, checkpoint/resume, validation gates, 24 self-evolution cycles, daemon mode, and a learning system with pattern detection, trust scoring, entropy analysis, and semantic LLM analysis.

**But Mozart operates primarily at Discipline 1 (Prompt Craft) and partially at Discipline 2 (Context Engineering).** The system lacks the infrastructure for Discipline 3 (Intent Engineering) and Discipline 4 (Specification Engineering) that would enable it to replace entire software teams.

### Gap Counts by Dimension

| Dimension | Gaps Found | Critical | High | Medium | Low |
|-----------|-----------|----------|------|--------|-----|
| D1: Prompt Craft | 12 | 0 | 5 | 5 | 2 |
| D2: Context Engineering | 20 | 3 | 4 | 7 | 6 |
| D3: Intent Engineering | 13 | 1 | 5 | 4 | 3 |
| D4: Specification Engineering | 22 | 4 | 7 | 8 | 3 |
| Dashboard | 17 | 0 | 4 | 6 | 7 |
| Learning/Self-Improvement | 12 | 0 | 4 | 5 | 3 |
| Model Selection | 10 | 0 | 3 | 5 | 2 |
| **Total** | **106** | **8** | **32** | **40** | **26** |

### The Core Thesis

Mozart validates **syntax** but not **semantics**. It checks "does the file exist?" but not "is the code correct?" It detects patterns but doesn't know WHY they work. It executes scores but can't write them. It has no specification corpus, no intent layer, no planner, and no adaptive decomposition.

To replace software teams, Mozart needs four new systems layered on top of the existing execution engine:

1. **Specification Corpus** — Shared project knowledge that all scores reference
2. **Intent Infrastructure** — Goals, trade-offs, constraints, escalation triggers
3. **Planner Layer** — Generates scores from high-level goals
4. **Semantic Evaluation** — LLM-based quality assessment beyond pass/fail

---

## Part 1: Discipline 1 — Prompt Craft Gaps

### D1-01: No Guardrails (Must-Not Rules)

Mozart tells agents what TO do but never what NOT to do. No mechanism for negative constraints ("never modify files outside workspace", "never commit directly to main").

**Spec:**
```yaml
prompt:
  guardrails:
    - "NEVER modify files outside the workspace directory"
    - "NEVER commit directly to main branch"
    - "NEVER skip tests to make validations pass"
    - "NEVER hardcode credentials or secrets"
```

Config model: `PromptConfig.guardrails: list[str]`
Injection: Before validation requirements section, wrapped in `## CONSTRAINTS (MUST NOT)`

### D1-02: No Examples/Counter-Examples

Agents can't see what good or bad output looks like. No mechanism to provide exemplars.

**Spec:**
```yaml
prompt:
  examples:
    positive:
      - description: "Well-structured security report"
        content: |
          ## Vulnerability: SQL Injection in /api/users
          **Severity:** High
          **Location:** src/api/users.py:42
          ...
    negative:
      - description: "Vague report without actionable details"
        content: |
          Found some security issues. Please fix them.
```

Config model: `PromptConfig.examples: ExamplesConfig` with `positive` and `negative` lists.

### D1-03: No Output Format Specification

No declarative way to specify expected output structure (markdown, JSON, specific sections).

**Spec:**
```yaml
prompt:
  output_format:
    type: "markdown"
    required_sections:
      - "## Summary"
      - "## Changes Made"
      - "## Test Results"
    max_length_chars: 50000
```

### D1-04: No Ambiguity Pre-Resolution

When prompts contain terms like "fix the bug" or "improve performance," there's no pre-decided interpretation. Agents guess differently on each retry.

**Spec:**
```yaml
prompt:
  ambiguity_resolution:
    - term: "fix the bug"
      interpretation: "Write a failing test, apply minimal fix, verify no regressions"
    - term: "improve performance"
      interpretation: "Optimize wall-clock latency, measured by timing before/after"
```

### D1-05: Completion Prompt Truncation

Original prompt truncated to 3000 chars in completion mode — agents lose validation rules, patterns, and failure history.

**Fix:** Increase `completion_context_budget` to 10K, implement intelligent section-aware truncation that preserves critical sections (validation rules, patterns) and truncates verbose task descriptions.

### D1-06: No Prompt Quality Gates

Mozart checks token count but not prompt quality. No linting for clarity, precision, completeness, or coherence.

**Spec:** New `PromptQualityChecker` class that evaluates prompts before execution. Configurable quality floor. Integration in preflight checks.

### D1-07: Preamble Lacks Authority Model

Current preamble tells agents their sheet number and workspace but not what they CAN or CANNOT do, their dependencies, success criteria, or resource constraints.

**Spec:** Enhanced preamble with permissions model, dependency context, success criteria, and resource constraints.

### D1-08: No Structured Failure Recovery Guidance

Retries get the same prompt without structured guidance on WHY the previous attempt failed or HOW to recover differently.

**Spec:** `RecoveryStrategyGenerator` that produces structured recovery plans based on failure type, with root cause hypothesis, recovery steps, diagnostics, and anti-patterns.

### D1-09: Validation Errors Not Actionable

Validation failures report what failed but not how to fix it.

**Spec:** Extend `ValidationRule` with `when_fails_check_for`, `likely_causes`, `recovery_steps`, and `auto_diagnostic_command` fields.

### D1-10: No Agent Self-Check Mechanism

Agents don't verify their own work before finishing.

**Spec:** Self-check requirements section appended to prompts, asking agents to run validation commands and report confidence level.

### D1-11: No Dynamic Guardrails

Guardrails are static — don't adapt based on retry count, failure patterns, or learned context.

**Spec:** `DynamicGuardrailInjector` that generates escalating guidance: first retry gets "don't repeat X", second retry gets root cause hypothesis, third retry gets "debug systematically."

### D1-12: No Completion Priorities

When partially completing, agents don't know which validations to fix first.

**Spec:** Extend `ValidationRule` with `priority: Literal["critical", "important", "optional"]`. Completion mode formats failed validations by priority with clear success criteria.

---

## Part 2: Discipline 2 — Context Engineering Gaps

### D2-01: No Token Budget Management (CRITICAL)

Mozart has no awareness of context window limits. No tracking of how many tokens system prompt + preamble + prelude + patterns + validations + cross-sheet context consume.

**Spec:**
```python
class TokenBudgetTracker:
    def __init__(self, window_size: int = 200_000, reserve: int = 30_000):
        self.available = window_size - reserve
        self.allocated: dict[str, int] = {}

    def allocate(self, text: str, component: str) -> bool:
        tokens = estimate_tokens(text)
        if self.accumulated + tokens > self.available:
            return False
        self.allocated[component] = tokens
        return True
```

Integration: PromptBuilder tracks budget as it assembles. Components added in priority order: system prompt → preamble → validation rules → template → patterns → cross-sheet → prelude/cadenza. Lower-priority components trimmed if budget exceeded.

### D2-02: No Intelligent Cross-Sheet Context Selection (CRITICAL)

`lookback_sheets` dumps last N sheets' stdout — no relevance filtering. Sheet 10 gets output from Sheet 9 whether relevant or not.

**Spec:**
```yaml
cross_sheet:
  mode: "relevant"  # "all" (current), "relevant" (new), "tagged"
  relevance_config:
    strategy: "keyword"  # or "semantic" (LLM-based)
    keywords_from: "validation_rules"  # extract keywords from current sheet's rules
    max_context_tokens: 5000
```

### D2-03: No Context Profiles (CRITICAL)

All sheets get the same context treatment. No way to say "setup sheets need full prelude, processing sheets need patterns, finalization sheets need everything."

**Spec:**
```yaml
context_profiles:
  setup:
    include_prelude: true
    include_patterns: false
    max_cross_sheet: 0
  processing:
    include_prelude: false
    include_patterns: true
    max_cross_sheet: 3
  finalization:
    include_prelude: false
    include_patterns: true
    max_cross_sheet: 5

sheet:
  sheet_profiles:
    1: "setup"
    "2-48": "processing"
    "49-50": "finalization"
```

### D2-04: No RAG / Document Retrieval

Mozart has no mechanism to retrieve relevant project docs dynamically. Prelude/cadenza uses static file paths.

**Spec:** New `DocumentStore` class for TF-IDF based retrieval. Config: `rag.enabled: true`, `rag.doc_patterns: ["docs/**/*.md"]`, `rag.max_docs_per_query: 3`.

### D2-05: No Dynamic Context Adjustment

Context assembled before execution, not adapted during execution. Early retries might need simple context; later retries need exhaustive context.

**Spec:** `DynamicContextLoader` that adjusts context based on retry attempt number, error type, and time pressure.

### D2-06: Unmanaged Pattern Injection

All patterns treated equally — no relevance filtering, no deduplication, no token budget.

**Spec:** Enhanced `PatternApplicator.select_patterns_for_context()` with relevance scoring, deduplication, and token budget enforcement.

### D2-07: Static System Prompt

`system_prompt_file` is one file for all models and tasks. No model-aware or task-aware selection.

**Spec:** `system_prompt_files: dict[str, Path]` with keys like "default", "compact", "opus", "sonnet". Selection strategy: "default", "model", "task", or "window_aware".

### D2-08–D2-20: Secondary Gaps

Including: context awareness tracking, intelligent truncation strategies, token estimation, context window validation, cross-job context reuse, compression strategies, tool hint filtering, model-specific optimization, prompt caching integration, dynamic prelude skipping, context version tracking, batch context pooling, context impact analysis.

---

## Part 3: Discipline 3 — Intent Engineering Gaps

### D3-01: No Goal Hierarchy (CRITICAL)

Mozart has no mechanism for encoding what the project optimizes for. No goals, no priorities, no hierarchy.

**Spec:**
```yaml
# .mozart/spec/intent.yaml or inline in score
intent:
  goals:
    primary: "correctness"
    secondary: ["completeness", "maintainability"]
    tertiary: ["performance", "cost_efficiency"]

  trade_offs:
    correctness_vs_speed: "correctness"
    completeness_vs_cost: "completeness_within_budget"
    innovation_vs_proven: "proven_unless_no_alternative"
```

Config model: `IntentConfig` with `GoalHierarchy`, propagated to sheets via context engineering.

### D3-02: No Trade-Off Framework

When objectives conflict (quality vs speed, cost vs thoroughness), agents have no framework for resolving the conflict.

**Spec:** `TradeOffFramework` with `conflicts` rules defining priority order and permitted overrides.

### D3-03: No Quality Standards Beyond Validation

Validation checks syntax (file exists, regex matches). No enforcement of code quality, test coverage, or architectural consistency.

**Spec:**
```yaml
quality_standards:
  test_coverage_minimum: 0.80
  critical_path_coverage: 1.0
  documentation_required: "public_api"
  error_handling: "all_external_calls"
```

### D3-04: No Constraint Architecture

No declarative musts/must-nots/preferences/escalation-triggers.

**Spec:**
```yaml
constraints:
  musts:
    - name: "all_tests_pass"
      enforcement: "block_on_violation"
    - name: "no_security_vulnerabilities"
      enforcement: "block_on_violation"
  must_nots:
    - "Never commit directly to main"
    - "Never bypass the licensing system"
  preferences:
    - "Prefer composition over inheritance"
    - "Prefer explicit over implicit"
  escalation_triggers:
    - "Security decisions involving user data"
    - "API contract changes"
```

### D3-05: No Escalation Trigger Framework

Escalation is reactive (after failure) not proactive. No formalized trigger conditions.

**Spec:** `EscalationTriggerRegistry` with proactive triggers (before execution: destructive operations) and reactive triggers (after failure: low confidence, repeated errors). Severity levels with response timeouts and default actions.

### D3-06: Learning Doesn't Encode Why Patterns Work

Patterns record success_rate but not causal understanding or goal alignment.

**Spec:** Extend `Pattern` with `intent_alignment` (goals it supports/conflicts with), `safe_to_apply` conditions, and `risky_in_contexts`.

### D3-07: No Per-Sheet Intent Profiles

All sheets inherit the same retry policy and escalation rules. No way to express "Sheet 1 is low-risk, Sheet 2 is critical."

**Spec:** `IntentProfile` per sheet with criticality, role, retry policy, and quality standards.

### D3-08: Self-Healing Has No Intent Context

Remedies don't consider whether their fix contradicts organizational goals.

**Spec:** Extend remedies with `impact_on_goals` scoring. Filter remedies by constraint compliance before applying.

### D3-09: No Cost/Budget Tied to Intent

`CostLimitConfig` enforces ceilings but doesn't express WHY cost matters or what trade-offs are acceptable.

**Spec:** `IntentBudgets` with separate budgets for exploration, production, and critical work.

### D3-10–D3-13: Secondary Gaps

Including: learning doesn't encode why patterns work, scheduler has no intent-based prioritization, no decision authority boundaries, no escalation ROI analysis.

---

## Part 4: Discipline 4 — Specification Engineering Gaps

### D4-01: No Self-Contained Problem Statements (CRITICAL)

Sheets rely on implicit knowledge. Agents must infer context not present in the prompt. Specs can't be reused across projects.

**Spec:** Problem statement schema with explicit context dependencies, required knowledge, and "if unclear, ask" protocols.

### D4-02: Acceptance Criteria Are Validation-Only (CRITICAL)

Validates syntax ("file exists"), not semantics ("is the fix correct?").

**Spec:**
```yaml
validations:
  # Existing syntactic
  - type: file_exists
    path: "{workspace}/fix.py"

  # NEW: Semantic evaluation
  - type: llm_eval
    evaluator_model: "claude-haiku-4-5"
    criteria:
      - "Code follows conventions in .mozart/spec/conventions.yaml"
      - "Error handling covers all external call paths"
      - "No TODO/FIXME without linked issue"
    threshold: 0.8

  # NEW: Benchmark regression
  - type: benchmark_regression
    command: "pytest --benchmark-json results.json"
    baseline: ".mozart/state/benchmarks/latest.json"
    max_regression: 0.20

  # NEW: Coverage gate
  - type: coverage_threshold
    command: "pytest --cov --cov-report=json"
    minimum: 0.80
    critical_paths:
      - "src/payment/"
```

### D4-06: No Specification Corpus (CRITICAL)

No shared knowledge base. Each score hardcodes project conventions. No `.mozart/spec/` directory.

**Spec:**
```
.mozart/
├── spec/
│   ├── intent.yaml           # Goals, trade-offs, constraints
│   ├── architecture.yaml     # System design decisions
│   ├── conventions.yaml      # Coding standards, patterns
│   ├── contracts/            # API contracts, interfaces
│   ├── quality.yaml          # Quality definitions
│   └── constraints.yaml      # Musts/must-nots/preferences
├── state/
│   ├── progress.yaml         # What's done, what's next
│   ├── decisions.log         # Decisions made and rationale
│   └── learnings.yaml        # Cross-session learning
└── evals/
    ├── unit-quality.yaml     # Code quality criteria
    ├── architecture.yaml     # Structural quality
    └── integration.yaml      # System-level quality
```

Bootstrappable via `mozart init` command. Living — updated by sheets during execution.

### D4-11: No Planner Layer (CRITICAL)

Cannot generate scores from goals. Users must manually write YAML.

**Spec:** Planner is a meta-score that reads spec corpus + codebase state and produces concert structures + individual score YAML files.

```
Human provides: "add semantic search to Naurva"
Planner reads: .mozart/spec/, codebase, progress.yaml, learnings.yaml
Planner produces: concert structure, score YAML files, acceptance criteria
Human reviews: the plan, not the implementation
Mozart executes: approved scores run autonomously
```

`mozart plan "add semantic search"` command. Human review/approve/reject workflow.

### D4-03: No Declarative Constraint System

Constraints exist only as natural language in prompts. Can't be validated, checked, or enforced programmatically.

### D4-05: Decomposition is Static

Can't replan mid-concert when approach fails. Frozen at write-time.

**Spec:** Adaptive re-planning triggered by structural failures. Planner generates replacement scores. Human approval required. Progress from completed sheets preserved.

### D4-08: No Quality Evaluation Beyond Pass/Fail

Can't distinguish great work from barely-passing work. Binary validations only.

**Spec:** Multi-dimensional evaluation: accuracy, completeness, clarity, each scored 0-1 with weighted overall score.

### D4-14: No Progress State Across Concerts

Each concert starts from scratch. No cross-concert progress tracking.

**Spec:** `.mozart/state/progress.yaml` persists across concerts, tracking what's been done and what's next.

### D4-18: No Spec-Driven Validation Generation

Validations hardcoded in scores. Can't be generated from specs or adapted.

**Spec:** Validation rules auto-generated from specification corpus (e.g., conventions.yaml → lint check, quality.yaml → coverage threshold).

---

## Part 5: Dashboard Gaps (17 gaps)

### High Priority
- **DASH-01:** No prompt preview — users can't see what Claude will receive
- **DASH-04:** No context assembly preview — invisible what context each sheet gets
- **DASH-05:** No token budget visualization — no cost control visibility
- **DASH-09:** No escalation workflow — no human-in-the-loop for uncertain cases

### Medium Priority
- **DASH-07:** No pattern/learning visibility — can't see what's being applied
- **DASH-08:** No intent configuration UI — goals/trade-offs not expressible
- **DASH-11:** Limited validation display — binary pass/fail only
- **DASH-13:** No plan review workflow — no approval before execution
- **DASH-16:** No integrated input engineering dashboard — craft/validate/execute/learn cycle

### Summary Insight
Dashboard is **execution-focused, not engineering-focused**. It shows what happened (logs, status, results) rather than enabling what should happen (crafting, validating, reviewing). The core missing pattern: **write-once, execute, observe** needs to become **craft, refine, validate, execute, learn**.

---

## Part 6: Learning/Self-Improvement Gaps (12 gaps)

### Critical Feedback Loops Missing

| Loop | Current | Needed |
|------|---------|--------|
| Execute → Learn Patterns | YES | — |
| Learn Patterns → Improve Prompts | NO | LEARN-01: Pattern-to-prompt feedback |
| Learn Patterns → Optimize Context | NO | LEARN-02: Context effectiveness tracking |
| Execute → Detect Intent Drift | NO | LEARN-03: Job-level drift detection |
| Validate → Improve Specs | NO | LEARN-04: Validation-to-spec feedback |
| Analyze → Assess Spec Quality | NO | LEARN-05: Semantic spec quality analysis |
| Trust Score → Explain WHY | NO | LEARN-06: Causal factor analysis |
| Learn → Inform Scheduler | NO | LEARN-07: Scheduling hints from learning |
| Learn → Cross-Project | NO | LEARN-08: Pattern export/import |
| Entropy → Signal Spec Clarity | NO | LEARN-09: Entropy as spec quality signal |
| Learn → Drive Evolution | NO | LEARN-10: Auto-generate evolution candidates |
| Detect Under-Spec vs Agent Fail | NO | LEARN-11: Ambiguity detection |
| Learn Decomposition Patterns | NO | LEARN-12: DAG optimization from execution |

### Key Insight
The learning system is **a dead-end, not a loop**. Patterns are detected and injected, but never feed back to improve the spec, prompt, context, or intent layers. The self-improvement cycle is: Execute → Learn Patterns → Inject Patterns → Execute Better. It needs to be: Execute → Learn → Improve Specifications → Improve Context → Improve Prompts → Execute Better.

---

## Part 7: Model Selection Gaps (10 gaps)

### Critical Gaps

| Gap | Description |
|-----|-------------|
| MODEL-01 | No model-specific prompt optimization (same prompt to Opus and Haiku) |
| MODEL-02 | No context budget awareness per model (Haiku gets same context as Opus) |
| MODEL-03 | No intent-driven model selection (no "use cheap model for exploration") |
| MODEL-04 | No cost-aware fallback chains (rate-limited Opus waits instead of falling to Sonnet) |
| MODEL-05 | No planner-worker architecture (no sheet roles: planner/worker/reviewer) |
| MODEL-06 | No automatic sheet_overrides generation from task analysis |
| MODEL-07 | No model performance profiling (no tracking of which model works best per task type) |
| MODEL-08 | No A/B testing (can't compare models on same task) |
| MODEL-09 | No learning-driven model selection (patterns don't inform model choice) |
| MODEL-10 | No temperature/parameter intelligence (static across all contexts) |

### Planner-Worker Pattern Spec

```yaml
sheet:
  roles:
    1: "planner"
    2: "worker"
    3: "worker"
    4: "worker"
    5: "reviewer"

backend:
  model_by_role:
    planner: "claude-opus-4-6"
    worker: "claude-sonnet-4-5"
    reviewer: "claude-haiku-4-5"

  model_routing:
    by_intent:
      research: { model: "claude-haiku-4-5", temperature: 0.8 }
      code_generation: { model: "claude-sonnet-4-5", temperature: 0.0 }
      planning: { model: "claude-opus-4-6", temperature: 0.3 }
      validation: { model: "claude-haiku-4-5", temperature: 0.0 }

  fallback_chains:
    claude-opus-4-6: ["claude-sonnet-4-5"]
    claude-sonnet-4-5: ["claude-haiku-4-5"]
```

---

## Unified Architecture: The Four-Discipline Stack

```
┌─────────────────────────────────────────────────────┐
│            HUMAN (CTO Role)                         │
│  Sets direction, reviews plans, decides escalations │
└───────────┬─────────────────────────┬───────────────┘
            │                         │
            ▼                         ▼
┌───────────────────┐    ┌──────────────────────┐
│  D4: SPECIFICATION │    │  D3: INTENT          │
│  CORPUS            │    │  INFRASTRUCTURE      │
│                    │    │                      │
│  .mozart/spec/     │    │  Goals, trade-offs,  │
│  intent.yaml       │◄──│  constraints,        │
│  architecture.yaml │    │  escalation triggers │
│  conventions.yaml  │    │  quality floors      │
│  quality.yaml      │    │                      │
│  constraints.yaml  │    └──────────┬───────────┘
│                    │               │
└───────────┬────────┘               │
            │                        │
            ▼                        ▼
┌─────────────────────────────────────────────────────┐
│                    PLANNER LAYER                     │
│                                                     │
│  Reads spec corpus + codebase + progress + learnings│
│  Generates concert structures + score YAML files    │
│  Human reviews plans before execution               │
│  Can re-plan when approach fails                    │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│           D2: CONTEXT ENGINEERING                    │
│                                                     │
│  Token budget management                            │
│  Context profiles per sheet type                    │
│  RAG for project docs                               │
│  Intelligent cross-sheet selection                  │
│  Model-aware context sizing                         │
│  Dynamic adjustment per retry                       │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│           D1: PROMPT CRAFT                           │
│                                                     │
│  Guardrails (must-nots)                             │
│  Examples/counter-examples                          │
│  Output format specs                                │
│  Ambiguity pre-resolution                           │
│  Dynamic guardrails per retry                       │
│  Self-check requirements                            │
│  Model-specific prompt optimization                 │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│           EXECUTION ENGINE (existing)                │
│                                                     │
│  Sheet runner, DAG, fan-out, parallel execution     │
│  Checkpoint/resume, retry/completion                │
│  Daemon mode, rate coordination                     │
│  Cost tracking, resource monitoring                 │
│  Intent-driven model selection (new)                │
│  Planner-worker architecture (new)                  │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│           EVALUATION LAYER (new)                     │
│                                                     │
│  Syntactic validation (existing)                    │
│  Semantic LLM evaluation (new)                      │
│  Benchmark regression detection (new)               │
│  Coverage threshold gates (new)                     │
│  Multi-dimensional quality scoring (new)            │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│           LEARNING SYSTEM (enhanced)                 │
│                                                     │
│  Pattern detection (existing)                       │
│  Trust scoring (existing)                           │
│  Entropy analysis (existing)                        │
│  → Feedback to prompts (new: LEARN-01)              │
│  → Feedback to context (new: LEARN-02)              │
│  → Feedback to specs (new: LEARN-04)                │
│  → Feedback to planner (new: LEARN-07)              │
│  → Cross-project (new: LEARN-08)                    │
│  → Under-spec detection (new: LEARN-11)             │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
            SPECIFICATION CORPUS UPDATES
            (progress, decisions, learnings)
```

---

## Implementation Roadmap

### Phase 1: Foundation — Specification Corpus & Token Budget (Weeks 1-3)

**Why first:** Everything else depends on having the spec corpus and knowing token limits.

| Task | Effort | Files |
|------|--------|-------|
| `.mozart/spec/` directory structure | S | New module |
| `mozart init` interview command | M | CLI |
| Spec loading in score execution pipeline | M | prompts/templating.py, runner/sheet.py |
| Token estimation utility | S | New: core/tokens.py |
| Token budget tracker | M | New: prompts/budget.py |
| Budget-aware prompt assembly | M | prompts/templating.py |

### Phase 2: Prompt Craft Hardening (Weeks 3-5)

**Why second:** Better prompts improve everything downstream.

| Task | Effort | Files |
|------|--------|-------|
| Guardrails (must-nots) in PromptConfig | S | core/config/execution.py, prompts/templating.py |
| Examples/counter-examples | S | core/config/execution.py, prompts/templating.py |
| Output format specification | S | core/config/execution.py, prompts/templating.py |
| Completion context budget increase | S | prompts/templating.py |
| Actionable validation errors | S | core/config/execution.py, prompts/templating.py |
| Self-check requirements | S | prompts/templating.py |
| Enhanced preamble | M | prompts/preamble.py |

### Phase 3: Intent Infrastructure (Weeks 5-8)

**Why third:** Intent shapes what the planner and executor optimize for.

| Task | Effort | Files |
|------|--------|-------|
| IntentConfig (goals, trade-offs, constraints) | M | New: core/config/intent.py |
| Constraint architecture (musts/must-nots/preferences) | M | New: core/constraints.py |
| Escalation trigger framework | L | daemon/escalation.py, cli/ |
| Decision authority boundaries | M | core/config/intent.py |
| Per-sheet intent profiles | M | core/config/job.py |
| Intent-aware self-healing | S | healing/ |
| Cost/budget tied to intent | M | core/config/execution.py |

### Phase 4: Context Engineering (Weeks 8-11)

**Why fourth:** Better context makes the planner and executor more effective.

| Task | Effort | Files |
|------|--------|-------|
| Context profiles per sheet type | M | New: prompts/context_profiles.py |
| Intelligent cross-sheet selection | M | execution/runner/sheet.py |
| Dynamic context adjustment per retry | M | New: execution/dynamic_context.py |
| Pattern injection budget/relevance | M | learning/patterns.py |
| Model-aware context sizing | M | prompts/templating.py |
| RAG document retrieval | L | New: rag/ module |

### Phase 5: Semantic Evaluation (Weeks 11-13)

**Why fifth:** Before the planner can generate scores, the eval system must verify planner output.

| Task | Effort | Files |
|------|--------|-------|
| `llm_eval` validation type | L | execution/validation/ |
| `benchmark_regression` validation type | M | execution/validation/ |
| `coverage_threshold` validation type | M | execution/validation/ |
| Multi-dimensional quality scoring | M | execution/validation/ |
| Eval criteria from spec corpus | M | execution/validation/ |
| Eval results in learning feedback | M | learning/ |

### Phase 6: Model Selection Intelligence (Weeks 13-15)

| Task | Effort | Files |
|------|--------|-------|
| Model-specific prompt profiles | M | prompts/templating.py, core/config/ |
| Intent-driven model routing | M | New: execution/model_selector.py |
| Planner-worker sheet roles | M | core/config/job.py |
| Cost-aware fallback chains | M | execution/recovery.py, core/config/backend.py |
| Model performance profiling | L | daemon/profiler/ |

### Phase 7: The Planner (Weeks 15-20)

**Why last:** Requires all previous layers to function.

| Task | Effort | Files |
|------|--------|-------|
| Planning meta-score template | L | New: planning/ module |
| `mozart plan` command | L | cli/, planning/ |
| Plan review/approve workflow | L | cli/, dashboard/ |
| Adaptive re-planning on structural failure | XL | planning/, execution/ |
| Spec corpus auto-update from execution | L | planning/, state/ |

### Phase 8: Learning Feedback Loops (Weeks 20-24)

| Task | Effort | Files |
|------|--------|-------|
| Pattern-to-prompt feedback (LEARN-01) | M | learning/, prompts/ |
| Context effectiveness tracking (LEARN-02) | M | learning/, prompts/ |
| Job-level intent drift (LEARN-03) | M | learning/store/ |
| Validation-to-spec feedback (LEARN-04) | L | learning/, validation/ |
| Semantic spec quality analysis (LEARN-05) | M | daemon/semantic_analyzer.py |
| Under-spec vs agent failure detection (LEARN-11) | L | learning/, daemon/ |
| Cross-project learning (LEARN-08) | L | learning/store/ |

### Phase 9: Dashboard Evolution (Ongoing)

Priority order: prompt preview → context assembly view → token budget visualization → escalation workflow → intent configuration → plan review → dependency DAG.

---

## How This Replaces a Software Team

| Team Role | Mozart Equivalent | Phase |
|-----------|------------------|-------|
| Product Manager | Specification Corpus + Human Review | Phase 1 |
| Tech Lead | Planner Layer | Phase 7 |
| Developer | Execution Layer (Coding Sheets) | Existing |
| Code Reviewer | Review Sheets + LLM Eval | Phase 5 |
| QA | Validation Gates + Eval System | Phase 5 |
| DevOps/CI | Mozart Runner + Daemon | Existing |

**The human becomes the CTO.** They set direction (intent), review plans (planner output), make decisions on escalations, and verify the system produces what's needed. They don't write code, write prompts, or manage context windows. They specify outcomes and verify results.

---

## What "Better Than Commercial Options" Means

| Capability | Current Commercial | Mozart Target |
|------------|-------------------|---------------|
| Prompt execution | Cursor, Windsurf, Devin | Same |
| Context engineering | Claude Code (.claude.md) | Spec corpus + RAG + profiles |
| Intent encoding | None | Full goal/trade-off/constraint system |
| Specification layer | None | Planner + spec corpus + adaptive decomposition |
| Self-improvement | None | 24-cycle evolution + feedback loops |
| Model selection | Manual or heuristic | Intent-driven + cost-aware + learning-driven |
| Evaluation | Basic pass/fail | Semantic LLM eval + multi-dimensional scoring |
| Human oversight | Chat-based | Escalation workflows + plan review + dashboard |

The differentiator is the **recursive loop**: specifications improve from learning, which improves execution, which improves learning, which improves specifications. No commercial tool has this loop.

---

*Generated by 7 parallel deep-dive agents analyzing 83K lines of Mozart source code against the Four Disciplines of AI Input Engineering.*
