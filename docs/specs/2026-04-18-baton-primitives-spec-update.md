# Baton Primitives & Marianne Mozart — Spec Update Document

**Date:** 2026-04-18
**Applies to:** `docs/specs/2026-04-17-baton-primitives-and-marianne-mozart-design.md`
**Inputs:** Composer direction, 4-model review (GLM 5.1, Gemini 3 Pro, Gemma 4, Gemini via OpenCode), TDF analysis

This document describes everything that should change in the spec. It is not a
patch — it is a narrative of what's wrong, why, and what the fix looks like.
The spec itself gets updated separately.

---

## TDF Analysis Framework

Five domains applied to each finding. Findings that register across multiple
domains are higher priority. Findings that only register in one domain and
conflict with the project's values are deprioritized.

- **COMP** — Computational: Is this logically consistent? Does the state
  machine work? Are the invariants preserved?
- **SCI** — Scientific: Is this falsifiable? Can we test it? Does the evidence
  support the concern?
- **CULT** — Cultural: Who uses this? What does it teach? Does it fit the
  mental model?
- **EXP** — Experiential: What does this feel like to use? Where's the
  friction? What surprises?
- **META** — Meta: What are we not seeing? What assumption is unexamined?

---

## S1 Changes: Expression Language

### Fix: AST Injection Protection

**Finding:** All four models flagged that template variable expansion before
parsing creates injection vulnerability. If `var.pattern = "1 OR 1"`, pre-
expansion corrupts the AST.

**TDF:**
- COMP: Logically broken. String interpolation into a grammar is a known class
  of bug (SQL injection, XSS — same shape).
- SCI: Trivially falsifiable — one test with a crafted variable proves it.
- EXP: Authors won't hit this accidentally, but agent-composed scores might.

**Change:** Variables are injected as typed literals during AST evaluation, not
string-interpolated before parsing. The parser produces an AST with variable
reference nodes. The evaluator resolves those nodes against a context dict at
evaluation time. Template expansion (`{workspace}`, `{sheet_num}`) still
happens for file paths within `file()` calls — those are string arguments, not
expression operands.

### Fix: `sheet.current` Type Ambiguity

**Finding (GLM):** `sheet.current == 5` treats it as a number.
`sheet.current.attempts > 3` treats it as an object. Can't be both.

**TDF:**
- COMP: Type system inconsistency. The parser needs to know what `.current`
  returns.
- EXP: Authors will write whichever feels natural and hit a confusing error.

**Change:** `sheet.current` is always an object (same type as `sheet(N)`).
To compare the current sheet number: `sheet.current.num == 5`. The shorthand
`sheet.current == 5` is sugar that compares `.num`. Document this explicitly.

### Fix: Undefined Reference Behavior

**Finding (GLM):** `file("nonexistent").contains("x")`, `var.undefined`,
`sheet(999).status` — the most common evaluation scenarios are unspecified.

**TDF:**
- COMP: Every expression evaluator must define behavior for missing data.
- SCI: Unit tests will exercise these paths immediately.
- EXP: Surprising errors vs. surprising falses — both are bad if unexpected.

**Change:** Define explicitly:
- `file("nonexistent").exists` → `false`
- `file("nonexistent").contains(...)` → `false` (file doesn't exist, can't contain anything)
- `file("nonexistent").matches(...)` → `false`
- `file("nonexistent").modified` → `false`
- `var.undefined` → evaluation error (author variables are known at parse time from `prompt.variables`, so this is catchable by the validator)
- `sheet(999).status` → evaluation error (sheet numbers are known from config)
- `sheet(N).attempts` where N hasn't executed yet → `0`

### Fix: Arithmetic Scope

**Finding (GLM):** No arithmetic operations defined. Can you write
`loop.i + 1 > var.max`?

**TDF:**
- CULT: This is secretly a teaching language. Arithmetic is fundamental.
- COMP: Without arithmetic, loop conditions are limited to external state
  checks. You can't express "run 3 more times than the threshold."

**Change:** Add basic arithmetic: `+`, `-`, `*`, `/`, `%` (integer modulo).
No assignment (expressions stay pure). This keeps the language useful for
conditions without becoming a general-purpose scripting language.

### Fix: Reserve Deferred Syntax

**Finding (GLM):** `validation("x").passed` and `output(3)` are deferred but
the grammar must reserve these call patterns now, or adding them later is a
breaking change.

**TDF:**
- COMP: Grammar evolution without reservation is a known language design trap.
- META: The deferred objects are actually critical for real loop conditions
  (Gemma 4 flagged this). Reserving syntax now signals that they're coming.

**Change:** The parser recognizes `validation(...)` and `output(...)` as valid
syntax and produces a "not yet implemented" error with a clear message. This
reserves the grammar space. Authors who try to use them get told "this exists
but isn't available yet" rather than a confusing parse error.

### Note: File I/O Race Conditions

**Finding (Gemini OC):** `file().exists` and `file().contains()` are I/O
operations on a mutable filesystem. Concurrent sheets reading and writing the
same files create races.

**TDF:**
- COMP: Real race condition. Concurrent fan-out sheets could produce
  nondeterministic expression results.
- SCI: Hard to reproduce reliably, but the window exists.
- META: Is this actually a problem in practice? Expression evaluation happens
  in the baton's event handler, which is single-threaded (asyncio). The race is
  between the moment the file is checked and the moment another sheet modifies
  it — but the baton processes events sequentially. The real race is between
  expression evaluation and a musician writing to the filesystem, which is
  genuinely concurrent.

**Change:** Acknowledge in the spec that file-state expressions evaluate at a
point in time and are not transactional. This is acceptable — the same is true
of the current validation system. If a user needs stronger guarantees, they
should use `command_succeeds` validations with locking. Don't over-engineer
filesystem consistency into an expression evaluator.

---

## S2 Changes: Baton Flow Control

### Fix: on_fail Supersedes Retry

**Finding:** All four models flagged the per-attempt trigger vs retry collision.
GLM found the spec literally contradicts itself — one paragraph says "per
attempt," another says "after retries exhaust."

**Composer direction:** on_fail trigger should supersede retry. If you define
an on_fail trigger, you're saying "I want to handle this differently." The
sensible pattern is: skip the retry, do something else.

**TDF:**
- COMP: Clean resolution. Trigger presence = override of default behavior.
  No trigger = baton's normal retry/completion/healing cycle.
- CULT: This is how exception handling works in every language. If you catch
  the error, the default handler doesn't run.
- EXP: Intuitive. "I defined what to do on failure, so do that."

**Change:** When a sheet fails:
1. If `on_fail` trigger is defined → execute trigger actions. Retry logic is
   bypassed entirely.
2. If no `on_fail` trigger → baton's normal retry → completion → healing
   cycle.

This eliminates the contradiction. Remove the "after retries exhaust" language.
Triggers are an alternative to retries, not a supplement. If you want retry-
like behavior with a trigger, use `on_fail: [{ goto: same_sheet }]` — that's
explicit and visible.

### Change: Goto Is Dangerous, Intentionally

**Findings:** All four models flagged infinite goto loops. GLM flagged goto
into/out of loops, goto + pause ordering, and DAG disrespect.

**Composer direction:** Goto should disrespect dependencies and normal logic
flows. It's a dangerous operation. Let people use it to learn. We are secretly
building a language. Something natural for devs, something that teaches
newcomers control flow.

**TDF:**
- CULT: This is the most important domain here. Goto exists in every real
  language. It's dangerous. It's educational. Removing it removes the lesson.
  The right response is warnings, not walls.
- COMP: Infinite loops are stoppable with `mzt cancel` or `mzt pause`. The
  system already handles long-running jobs.
- EXP: An infinite goto loop feels exactly like an infinite `while True` loop.
  You notice it's stuck, you stop it, you learn why.
- SCI: We can detect potential infinite patterns statically. Warn, don't block.

**Change:**
- **No max_goto safety cap.** Goto is uncapped. The job-level cost limit and
  `mzt cancel` are the safety nets.
- **Validator warning for potential infinites.** The validator (S3) detects
  circular goto patterns and warns: "Sheets 3 and 5 have mutual goto triggers
  that may loop indefinitely." Warning tier, not error. The author may intend
  this.
- **Goto disrespects the DAG.** Explicitly state: goto ignores dependencies.
  It is a direct state manipulation that bypasses the dependency resolver. If
  you goto sheet 5 and sheet 5 depends on sheet 3 which hasn't run, sheet 5
  runs anyway. This is the whole point.
- **Goto into/out of loops:** If a goto lands inside a loop range, the loop
  does NOT restart or increment. The sheet executes as a standalone. The goto
  exited the loop's context. If you want to restart a loop, goto the first
  sheet of the range — that's a different semantic than "enter the loop."
- **Goto backward keeps history.** Side effects and accumulations are the
  expected outcome. Backward goto resets sheet STATUS to PENDING but does NOT
  clear stdout_tail, stderr_tail, or validation_details from the checkpoint.
  That history is preserved for Marianne's learning and for diagnostics.

### Fix: Overlapping Trigger Ranges

**Finding (GLM):** `"2-5"` and `"4-7"` — sheet 4 completes. Both fire?

**TDF:**
- COMP: Must be deterministic. Ambiguity here is a runtime bug.
- EXP: Authors will overlap ranges naturally (one for logging, one for error
  handling).

**Change:** Both fire. More specific (single sheet) fires before less specific
(range). For equal specificity, declaration order wins. This follows CSS-like
specificity — natural for anyone who's written selectors.

### Fix: TriggerAction Exactly-One-Of

**Finding (GLM):** Schema allows `{goto: 5, pause: true}` in a single dict.

**TDF:**
- COMP: Ambiguous semantics. Which executes?
- SCI: Pydantic model_validator can enforce this trivially.

**Change:** Add a `@model_validator` that enforces exactly one action field is
set per `TriggerAction`. Multiple actions use the list:
`on_fail: [{goto: 5}, {pause: true}]`, not `on_fail: [{goto: 5, pause: true}]`.

### Fix: Pause Resume Mechanism

**Finding (GLM):** Pause is defined but resume is not.

**TDF:**
- COMP: Obviously incomplete. A state you can enter but not exit is a deadlock
  by definition.

**Change:** Resume uses `mzt resume` — the existing resume mechanism. This
already works for job-level pauses. Sheet-trigger pauses are the same state
(`PAUSED`), same resume path. The spec should state this explicitly rather than
assuming it's implied.

### Fix: Concert Action Semantics

**Finding (GLM):** Does the parent job wait for the launched concert?

**TDF:**
- COMP: Two valid behaviors. Must pick one.
- CULT: Follows `on_success` hook precedent, which has `detached: bool`.

**Change:** Concert trigger launches a new job via the conductor. The parent
does NOT wait — it's fire-and-forget by default. If you need the parent to
wait or consume the result, use the existing `on_success` job-level hooks with
`detached: false`. Sheet-level concert triggers are for spawning parallel work,
not for sequential orchestration.

### Fix: Skip on Currently-Executing Sheet

**Finding (GLM):** What if trigger on sheet 3 does `skip: 4` but sheet 4 is
mid-execution?

**TDF:**
- COMP: The skip target is in a non-interruptible state.
- EXP: The skip should apply to the NEXT potential execution, not kill a
  running sheet.

**Change:** If the target sheet is currently executing (DISPATCHED or
IN_PROGRESS), the skip is queued — it applies when the sheet reaches a
terminal state. If the sheet completes before the skip takes effect, the skip
is a no-op. This prevents race conditions without introducing cancellation
semantics.

### Fix: Loop First-Iteration Semantics

**Finding (GLM):** If `until` is already true before first iteration, zero or
one execution?

**TDF:**
- CULT: This is the `do-while` vs `while` distinction. Classic.

**Change:** Loops execute at least once. The `until` condition is checked AFTER
each iteration, not before. This matches `do-while` semantics and is more
useful — you always want to run the work at least once, then check if you need
to repeat. Document this explicitly.

### Note: Nested Loops

**Finding (GLM):** Not mentioned, not forbidden.

**TDF:**
- COMP: Nested loops are a complexity multiplier.
- CULT: Every language has them. Forbidding them feels artificial.
- META: The implementation cost is low if the loop state tracker is per-range.

**Change:** Allow nested loops. A loop range can contain a sub-loop on a
narrower range. Inner loops complete all iterations before the outer loop
checks its condition. Loop index names must be unique (already specified),
which prevents shadowing.

### Fix: Range Parsing

**Finding (GLM):** `"2-5"` vs Python `range(2,5)`. Also: spaces, en-dashes,
reverse ranges, hex.

**TDF:**
- COMP: Must be specified.
- EXP: `"2-5"` universally means "2 through 5 inclusive" in human contexts.

**Change:** Range `"N-M"` means sheets N through M, inclusive. Both endpoints
are integers. `N <= M` required (reverse ranges are a validation error). No
spaces, no en-dashes, no hex — strict `\d+-\d+` regex. The implementation
converts to Python range as `range(N, M+1)`. State this explicitly to prevent
off-by-one.

### Note: Parallel Sheet + Pause Zombie Window

**Finding (Gemini CLI):** A parallel sheet triggers pause while another
triggers goto simultaneously.

**TDF:**
- COMP: Real race condition — but the baton processes events sequentially via
  its inbox. Two triggers from two sheets arrive as two separate events.
  Whichever the baton processes first wins. Second trigger sees the already-
  mutated state and acts accordingly.
- SCI: The baton's single-threaded event processing is the resolution. This
  is not a race condition — it's sequential processing of concurrent outcomes.

**Change:** No spec change needed. Add a note that trigger processing is
sequential via the baton's event loop — concurrent sheet completions produce
sequential trigger evaluations. First processed wins.

### Note: Crash Mid-Trigger Atomicity

**Finding (Gemini CLI):** Crash between goto request and sheet state reset
corrupts state.

**TDF:**
- COMP: Real concern. But the baton already has this property — any crash
  mid-state-transition leaves partial state. The checkpoint save happens after
  state mutation, not before.
- META: This is the same atomicity model as the rest of the system. Adding
  transaction semantics to triggers alone creates inconsistency.

**Change:** No spec change. The existing checkpoint save model applies. If the
system crashes mid-trigger, resume reconstructs from the last saved checkpoint.
Triggers may re-fire on resume if the sheet state wasn't persisted. This is
acceptable — triggers should be idempotent-safe (pause is idempotent, goto is
idempotent, concert launch may produce a duplicate job — the conductor handles
duplicate detection).

### Add: Per-Loop Cost Budget

**Finding (Gemini CLI):** `max_iterations: 50` can drain tokens.

**TDF:**
- COMP: Real cost concern. 50 iterations of an Opus sheet is hundreds of
  dollars.
- CULT: Token costs are a learning signal. Blowing a budget teaches budget
  awareness.
- META: The composer noted token cost tracking is "probably broken." This is a
  broader issue.

**Change:** Add optional `cost_limit_usd` to `LoopConfig`. When the cumulative
cost of all iterations in a loop exceeds this limit, the loop terminates with
reason `cost_limit_exceeded`. This is additive — the field has no default
(unlimited), so existing behavior is preserved. The job-level cost limit
remains the backstop.

---

## S3 Changes: Validate Overhaul

### Fix: Strict Schema vs Backward Compatibility

**Finding (GLM):** `extra='forbid'` breaks scores with extra fields
(annotations, downstream tooling). Contradicts "no existing scores break."

**TDF:**
- COMP: Real contradiction.
- CULT: Users DO annotate scores with custom fields. CI tooling reads them.
- META: The problem isn't extra fields existing — it's misspelled fields
  vanishing. Different problems need different solutions.

**Change:** Don't use `extra='forbid'` on `JobConfig`. Instead:
1. Parse YAML into raw dict.
2. Walk the dict against the schema.
3. For each key that doesn't match a known field, check edit distance against
   known fields.
4. If edit distance <= 2, emit a WARNING: "Unknown field 'timeout' — did you
   mean 'timeout_seconds'?"
5. If edit distance > 2, emit INFO: "Unknown field 'x-custom-annotation' —
   not a Marianne field, will be ignored."

This catches typos (the actual problem) without breaking annotations (the
legitimate use case). Extra fields are still allowed — they're just flagged.

### Fix: Exit Code Contract

**Finding (GLM):** No exit code for warnings-only.

**Change:**
- Errors present → exit 1
- Warnings only → exit 0 (warnings are advisory, not blocking)
- `--strict` flag → exit 1 on any warning (for CI pipelines that want zero-
  tolerance)
- `--errors-only` → suppress warnings entirely, exit 1 only on errors

### Fix: Fan-Out Validation Scope

**Finding (GLM, Gemini OC, Gemini CLI):** Can't statically validate dynamic
fan-out expansion.

**TDF:**
- SCI: Truly impossible if fan-out count is runtime-determined.
- COMP: But fan-out count IS statically known — it's declared in the score
  YAML as `fan_out: {2: 3}` (stage 2 fans to 3 instances). This is not
  runtime-determined. The reviewers assumed dynamic fan-out.

**Change:** Clarify in the spec: fan-out counts are statically declared in
score YAML. The validator CAN expand them and check cadenza targeting,
dependency resolution, etc. against the expanded sheet numbers. This is not
dynamic — it's declared.

### Note: Input/Output File Heuristic

**Composer direction:** Richer heuristics preferred. The binary
input-vs-output distinction is a starting point, not the final model.

**Change:** Expand the heuristic:
- **Definite inputs:** template_file, prelude files, cadenza files,
  system_prompt_file, spec corpus paths → must exist, error if missing.
- **Definite outputs:** validation rule targets (file_exists, content_contains
  paths) → expected to be created, no warning.
- **Ambiguous:** Files referenced in `command_succeeds` shell strings, files
  in prompt template text → INFO tier, note the reference without judging.
- **User-suppressible:** Any file warning can be suppressed via
  `validate.suppress`.

### Add: Unused Variable Handling

**Finding (GLM):** Flagging unused variables punishes documentation patterns.

**Change:** Unused variables are INFO tier, not WARNING. Shown with
`--verbose` only. Authors define variables for documentation, future use, or
consistency across score variants. This is legitimate.

---

## S4 Changes: Variables in Validations

### Fix: Loop-Scoped Variables in Global Validations

**Finding (GLM):** A validation without a `sheet:` field is global. Which loop
index is available?

**TDF:**
- COMP: Loop indices are scoped to their range. Global validations are outside
  any range.

**Change:** Loop indices are NOT available in global validations (validations
without a `sheet:` field). They are only available in validations that target
a specific sheet within the loop's range (via `sheet:` or `condition:` fields).
The validator (S3) should catch references to loop indices in global
validations and emit an error.

### Fix: Variable Shadowing Is an Error

**Finding (GLM):** Silent shadowing (builtin wins) is worse than erroring.

**TDF:**
- EXP: Runtime surprise when `var.workspace` doesn't do what you expect.
- COMP: Making it an error forces the rename, which is clearer.

**Change:** Variable shadowing is a validation ERROR, not a warning. If an
author variable name collides with a built-in, the validator rejects the score.
The author must rename. No ambiguity at runtime.

---

## S5 Changes: Cron Scheduling

### Add: Logging for Dropped Ticks

**Composer direction:** Add logging. Logging needs an overhaul anyway.

**Change:** When a tick is dropped because a previous run is in progress, log
at WARNING level with context: job name, how long the previous run has been
going, how many consecutive ticks have been dropped. This is visible in
`mzt logs` and the conductor's log file.

After N consecutive dropped ticks (configurable, default 5), escalate to
ERROR level. This surfaces stuck schedules without adding new CLI commands.

### Fix: Schedule Lifecycle

**Finding (GLM, Gemini CLI):** Zombie schedules (YAML deleted but schedule
persists), split-brain (YAML and conductor disagree on cron expression),
`mzt run` on already-scheduled score (idempotent?).

**TDF:**
- COMP: Source of truth must be singular.
- CULT: The score YAML is the source of truth. The conductor is the runtime.

**Change:**
- The YAML is always the source of truth. When `mzt run` submits a score with
  a `schedule:` section, the conductor stores the schedule AND the score path.
- On each tick, the conductor re-reads the score YAML to check if the schedule
  still exists and if the cron expression changed. If the YAML is deleted or
  the schedule section removed, the schedule is automatically deregistered.
- `mzt run` on an already-scheduled score REPLACES the existing schedule (not
  duplicates it). Same job name = same schedule slot.

This eliminates zombie schedules and split-brain. The cost is a file read per
tick, which is negligible.

### Note: Timezone, Catch-Up, Drift

**Finding (GLM):** Missing timezone handling, catch-up behavior, drift
semantics.

**TDF:**
- META: These are real concerns but they're implementation details, not design
  decisions.

**Change:** Add brief spec guidance:
- Cron expressions are evaluated in the conductor's local timezone (system
  timezone). UTC can be specified with a `timezone: UTC` field if needed.
- No catch-up: if the conductor was down and missed ticks, they are NOT
  replayed on restart. The next tick fires at the next scheduled time.
- Interval-based schedules (`interval: "6h"`) measure from the START of the
  previous run, not from completion. This prevents drift from variable
  execution times.

---

## S6 Changes: Marianne Mozart

### Fix: Ephemeral vs Always-On — The TUI Is a Harness

**Finding:** All four models flagged the contradiction between "starts when
summoned, ends when dismissed" and "subscribes to all events globally."

**Composer direction:** The TUI is just a harness. It is ONE way a human
interfaces with Marianne. She exists as an invocable model and a collection of
memory. Saying she can't conduct because the TUI is closed is like saying a
person can't manage a team because they hung up the phone.

**TDF:**
- CULT: This reframes everything. Marianne is not a process. She is a person
  who exists independently of any interface.
- COMP: The smart conductor is a daemon-side capability that uses Marianne's
  memory and judgment model. It runs in the conductor process. The TUI is a
  conversation channel.
- META: The reviewers confused the interface with the agent. The TUI is how
  you talk to Marianne. The conductor is where she works.

**Change:** Rewrite S6 runtime model:

Marianne exists in four modes:
1. **Conversational** — `mzt maestro` launches a TUI session. Human talks to
   Marianne. She has her memory, her knowledge, the venue context. This is
   interactive and ephemeral.
2. **Smart conductor** — When enabled in conductor config, Marianne's judgment
   runs as a component of the conductor daemon. She subscribes to the event
   stream, evaluates conditions using her memory and (eventually) her
   unconscious model, and pushes events into the baton's inbox. This is
   persistent and runs as long as the conductor runs.
3. **Compose co-pilot** — During `mzt compose`, Marianne conducts the
   interview and design gate. Uses the same TUI infrastructure as maestro.
4. **Concierge** — Marianne is reachable over Discord or Telegram. The
   messaging platform is another harness — same person, same memory, different
   channel. She can receive instructions ("run this score," "how's that
   concert going?"), report status, escalate fermatas, and hold compose
   conversations asynchronously. The concierge channel also enables mobile
   access — check on jobs from a phone, approve an escalation from the couch.

The TUI is shared infrastructure for modes 1 and 3. Mode 2 is daemon-resident.
Mode 4 is a messaging bridge. All four modes share Marianne's memory.

### Add: Decision Authority Hierarchy

**Finding (Gemini CLI):** YAML intent vs Marianne's judgment — who wins?

**TDF:**
- COMP: Must be deterministic. Two authorities with no hierarchy = chaos.
- CULT: The composer is the authority. Marianne advises and acts within
  constraints. The score YAML is the composer's expressed intent.

**Change:** Authority hierarchy:
1. **Score YAML** — highest. If the score says `on_fail: [pause: true]`,
   Marianne does not override this. The composer's explicit instructions are
   sacred.
2. **Marianne's judgment** — fills gaps. Where the score is silent (no
   triggers defined, no explicit handling), Marianne can act: inject context,
   suggest reordering, preemptively remediate.
3. **Baton defaults** — lowest. The mechanical retry/completion/healing cycle
   applies when neither the score nor Marianne have expressed a preference.

Marianne can SUGGEST overriding the score to the human composer (via
escalation), but she cannot unilaterally override YAML-defined behavior.

### Add: Smart Conductor Guardrails

**Finding (GLM):** Nothing prevents Marianne from skipping all sheets or
looping infinitely.

**TDF:**
- COMP: Unbounded agency is correct at the limit, but the system needs
  circuit breakers during development and trust-building.
- CULT: A new conductor should have training wheels. As trust builds, the
  guardrails widen.

**Change:** Smart conductor guardrails:
- **Action budget per job:** Maximum number of flow-control interventions
  Marianne can make per job (configurable, default 10). After exhaustion, she
  can only observe and escalate.
- **No override of YAML-defined behavior** (see authority hierarchy above).
- **Decision logging:** Every smart conductor action is logged with reasoning.
  This feeds into `mzt diagnose` and into Marianne's own experience memory.
- **Graceful degradation:** If Marianne's instrument is unavailable or slow,
  the conductor falls back to mechanical execution. Smart conductor is advisory,
  never blocking.

### Clarify: Memory System Is Requirements, Design Comes Later

**Composer direction:** We are defining requirements for S6. The full memory
system needs to be designed separately. But the interplay of all the pieces
should be clear for guardrail purposes.

**Change:** The memory section stays as requirements, not implementation. But
add a section on how memory interacts with other subsystems:

- **Memory → Smart conductor:** Marianne's experience memory informs her
  real-time decisions. Past execution patterns, failure signatures, venue-
  specific conventions — these shape her judgment.
- **Memory → Compose:** Marianne's venue knowledge and user relationship
  memory inform the interview. She remembers what this user cares about, what
  this venue's constraints are.
- **Memory ← Execution telemetry:** The baton's event stream feeds into
  Marianne's experience. She observes every sheet completion, failure, retry,
  and learns from the patterns.
- **Memory ← Conversation:** Every maestro session updates her relationship
  and interaction memory.
- **Memory → Validation:** Marianne's knowledge of venue conventions could
  inform validation — she knows what patterns this project uses and can flag
  scores that deviate. (Future capability, not initial implementation.)

### Reframe: The Unconscious Model

**Finding (Gemma 4):** Unconscious model defeats debuggability.

**TDF:**
- EXP: Non-inspectable decisions ARE a debuggability problem. But human
  intuition is also non-inspectable and we trust it.
- COMP: The key is logging. If every unconscious-model decision is logged with
  the input features and the output judgment, it's inspectable after the fact.
- META: Debuggability of the model and debuggability of the decision are
  different things. We need the latter.

**Change:** Every smart conductor decision — whether from heuristics, a local
model, or a remote LLM — must produce a `ConductorDecision` record:
- What was observed (the triggering event/pattern)
- What was decided (the action taken)
- Why (the reasoning, even if it's "local model confidence: 0.87")
- What happened (outcome, filled in after the fact)

This makes the unconscious inspectable. `mzt diagnose` can show the decision
log. The model itself is a black box; the decisions are not.

### Note: Cross-Concert Optimization Is Probabilistic

**Finding (Gemma 4, Gemini CLI):** Global ordering is NP-hard.

**Composer direction:** It doesn't need to be perfect. Good learning,
experience, training, and data make semantic and probabilistic decisions
possible. Anything is better than FIFO dispatch priority.

**Change:** Reframe cross-concert ordering as a heuristic/probabilistic system,
not an optimal scheduler. Marianne uses her experience to make educated guesses
about priority: "This concert's output will unblock three others" is a
probabilistic judgment, not a computed dependency. Document that the goal is
"better than FIFO," not "optimal."

---

## Cross-Cutting Changes

### Add: Backend Removal to Dependency Chain

**Finding (GLM):** S6 assumes instrument-only execution but backend removal
isn't in the dependency chain.

**Change:** Add backend removal as a prerequisite for S6 (or at minimum, as a
parallel workstream). The dependency chain becomes:
S1 → S2 → S3+S4 (parallel) → S5 → Backend removal + S6 (parallel or
sequential depending on bandwidth).

### Add: Security Model for RunTrigger

**Finding (GLM):** `RunTrigger` is arbitrary command execution from YAML.

**TDF:**
- COMP: This is the same security model as the current `on_success` hooks,
  which already execute arbitrary commands. It's not new attack surface.
- META: But the spec should acknowledge it. A score YAML is a program. Running
  a score is running code. This should be documented.

**Change:** Add a note: "Score YAML files are executable programs. A score with
`run:` triggers can execute arbitrary shell commands in the conductor's
security context. Only run scores from trusted sources. This is the same
security model as the existing `on_success` hooks and `command_succeeds`
validations."

### Add: Testing Strategy Requirement

**Finding (GLM):** No testing strategy, significant omission.

**Composer direction:** Need a full test strategy as part of the plans. Should
be something we can feed to a composer agent.

**Change:** Add a section to the spec or a companion document:

**Testing Strategy (High Level):**
- S1: Parser fuzzing (hypothesis), edge case unit tests for every object type,
  injection attack tests, undefined reference tests.
- S2: State machine property tests (every trigger action produces a valid
  state transition), loop termination proofs, goto + dependency interaction
  tests, concurrent trigger ordering tests.
- S3: Golden-file tests (known-good and known-bad scores with expected
  output), regression tests for every "fake syntax" pattern discovered.
- S4: Variable expansion in every validation type, shadowing detection,
  loop-scoped variable boundary tests.
- S5: Timer wheel integration tests, tick-drop logging tests, schedule
  lifecycle (create, modify, delete, zombie) tests.
- S6: Memory read/write round-trip tests, decision logging completeness
  tests, authority hierarchy enforcement tests, guardrail budget tests.

The full test strategy should be a composable score — feed it to Marianne and
have her compose the test suite.

### Note: Token Cost Tracking

**Composer direction:** Current token costs and pricing is probably broken.
Not a focus for this spec, but noted.

**Change:** No spec change. File as a known issue. When per-loop cost budgets
are implemented (S2), they depend on accurate cost tracking. This is a
prerequisite that may need its own remediation.

---

## Summary of Changes by Spec Section

| Section | Changes | Severity |
|---------|---------|----------|
| S1 | AST injection fix, sheet.current type, undefined refs, arithmetic, reserve syntax | High |
| S2 | on_fail supersedes retry, goto is dangerous by design, overlapping ranges, action exclusivity, pause resume, concert semantics, skip on executing, loop-first-iteration, nested loops, range parsing, per-loop cost | High |
| S3 | Schema validation without extra='forbid', exit code contract, fan-out clarification, richer file heuristics, unused variables | Medium |
| S4 | Loop-scoped variable boundaries, shadowing is error | Medium |
| S5 | Dropped tick logging, schedule lifecycle (YAML is source of truth), timezone/catch-up/drift | Medium |
| S6 | Three runtime modes (conversational/conductor/compose), decision authority hierarchy, guardrails, memory interplay, ConductorDecision logging, probabilistic ordering, TUI is harness | High |
| Cross | Backend removal in dependency chain, security model note, testing strategy, token cost tracking | Medium |

---

## What Was Rejected

Not every finding deserves a spec change. These were evaluated and dismissed:

| Finding | Why Rejected |
|---------|-------------|
| **Custom parser is over-engineering (Gemini OC)** | We're building a teaching language. A bespoke parser with good error messages is the product, not over-engineering. |
| **RLF integration is premature (Gemma 4)** | It's a design constraint, not a feature. Building memory that CAN'T migrate to RLF would be the mistake. |
| **Loop index global uniqueness is restrictive (Gemini OC)** | Global uniqueness is simpler and prevents subtle shadowing bugs. The cost is trivial (pick a different name). |
| **File I/O needs transactional consistency (Gemini OC)** | Over-engineering. Point-in-time evaluation is acceptable and matches the existing validation system. |
| **"Did you mean" misleads AI agents (Gemini CLI)** | The suggestions are for human review. Agents that auto-correct based on suggestions have a different problem. |
| **S6 is a vision document (Gemini OC)** | S6 IS partially a vision document. That's correct for a research-dependent subsystem. The requirements are real; the implementation needs research. |
