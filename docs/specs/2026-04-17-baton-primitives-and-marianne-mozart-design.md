# Baton Primitives & Marianne Mozart — Design Specification

**Date:** 2026-04-17 (revised 2026-04-18 incorporating 4-model review)
**Status:** Draft — post-review, ready for implementation planning
**Scope:** Six subsystems grouped as a cohesive feature set
**Dependency order:** S1 → S2 → S3+S4 (parallel) → S5 → Backend removal + S6 (parallel)

---

## Overview

This specification defines six subsystems that together transform Marianne from
a mechanical execution system into an intelligent orchestration platform. The
subsystems are designed independently but form a dependency chain: the expression
language (S1) enables flow control (S2), which enables the smart conductor (S6).
The validator overhaul (S3), variable expansion (S4), and cron scheduling (S5)
are independently useful but complete the picture. S6 depends on the removal of
the legacy backend system — Marianne and musicians run on instruments only.

The connective tissue is Marianne Mozart — the system's first person, who uses
all of these primitives to conduct with judgment, not just mechanics.

This spec is also, secretly, the design for a teaching language. The primitives
below — expressions, triggers, loops, goto — are programming concepts dressed
in orchestral vocabulary. They should feel natural to experienced developers
while teaching control flow to newcomers and vibe coders. That intent informs
several design decisions that would otherwise look reckless (goto has no safety
cap; loops are `do-while`; arithmetic is first-class).

| Spec | Subsystem | Summary |
|------|-----------|---------|
| S1 | Expression Language | Unified condition DSL for score YAML |
| S2 | Baton Flow Control | Per-sheet triggers, loops, goto |
| S3 | Validate Overhaul | Severity tiers, structural checks, noise elimination |
| S4 | Variables in Validations | Author variables in validation templates |
| S5 | Cron Scheduling | Time-triggered score runs |
| S6 | Marianne Mozart | Identity, memory, smart conductor, compose, TUI, concierge |

---

## S1: Expression Language

### Purpose

A unified condition language for score YAML that treats file state, author
variables, loop indices, and sheet state as first-class objects. Expressions
are **pure** — read-only, no side effects, no mutation. They appear wherever
a condition is needed: loop termination, trigger guards, skip_when rules.

### Scoped Objects

The full universe of expression objects is defined here. The first three are
implemented first; the remainder have reserved grammar (see "Reserved Syntax"
below) but are not yet evaluable.

**Starting three:**

#### File State

```yaml
# Existence — matches file_exists validation type
file("{workspace}/output.md").exists

# Substring — matches content_contains validation type
file("{workspace}/report.md").contains("## Summary")

# Regex — matches content_regex validation type
file("{workspace}/results.json").matches(/"status":\s*"complete"/)

# Modification — matches file_modified validation type
file("{workspace}/output.md").modified
```

File paths support template variable expansion (`{workspace}`, `{sheet_num}`,
author variables). **This expansion applies only to the string arguments of
`file(...)` calls** — not to expression operands themselves (see "AST Injection
Protection" below).

#### Author Variables + Loop Index

```yaml
# Author-defined variables from prompt.variables
var.threshold
var.target_file
var.config.nested_key

# Loop index — declared on the loop, scoped to it
loop.i
loop.pass
loop.cycle
```

Author variables are read-only in expressions. Loop indices are managed by the
baton (incremented on each iteration) and available in templates, validations,
and expressions for sheets within the loop's range. Each loop index name must
be globally unique within a score.

#### Sheet State

```yaml
# Sheet reference — always an object
sheet(3).status == "completed"
sheet(3).status == "failed"
sheet(3).attempts > 2

# Current sheet reference — also always an object
sheet.current.num == 5
sheet.current.attempts > 3
sheet.current.status == "retrying"
```

`sheet.current` and `sheet(N)` both return objects with fields `.num`,
`.status`, and `.attempts`. The shorthand `sheet.current == 5` is documented
sugar that compares `.num`; the canonical form is `sheet.current.num == 5`.
Authors may use either.

### Reserved Syntax

The following are reserved in the grammar but not yet evaluable. The parser
recognizes them and emits a clear "not yet implemented" error rather than a
confusing parse failure:

- `validation("name").passed` — per-validation result access
- `output(N)` / `output(N).contains("pattern")` — access to prior sheet stdout

Reserving the grammar now prevents breaking changes when these objects land.

### Compound Expressions

```yaml
# Boolean operators
file("{workspace}/done.txt").exists AND sheet(2).status == "completed"
file("{workspace}/output.md").contains("PASSED") OR sheet.current.attempts > 3
NOT file("{workspace}/error.log").exists

# Comparison operators
var.threshold >= 0.8
sheet(3).attempts > 2
loop.i == var.max_passes

# Arithmetic operators
loop.i + 1 > var.max_iterations
sheet(3).attempts * 2 < var.budget
loop.cycle % 5 == 0
```

Operators:
- **Arithmetic:** `+`, `-`, `*`, `/`, `%` (integer modulo). Expressions remain
  pure — no assignment, no mutation.
- **Comparison:** `==`, `!=`, `<`, `<=`, `>`, `>=`.
- **Boolean:** `AND`, `OR`, `NOT`.

Precedence (lowest to highest): `OR` < `AND` < `NOT` < comparison < arithmetic.
Parentheses for explicit grouping.

### AST Injection Protection

Variables are injected as **typed literals during AST evaluation**, not
string-interpolated before parsing. The pipeline is:

1. Expand template variables (`{workspace}`, `{sheet_num}`) inside the string
   arguments of `file(...)` calls only — these are paths, not expression code.
2. Parse the expression into an AST. Variable references (`var.foo`, `loop.i`,
   `sheet(N).attempts`) become reference nodes in the AST.
3. Evaluate the AST against a context dict. Reference nodes resolve to typed
   values at evaluation time.

This closes the class of bugs where `var.pattern = "1 OR 1"` could corrupt the
AST by being substituted into the source before parsing. Variable values never
enter the parser.

### Undefined Reference Behavior

Explicitly defined so implementations and authors agree:

| Expression | Value |
|-----------|-------|
| `file("nonexistent").exists` | `false` |
| `file("nonexistent").contains(...)` | `false` |
| `file("nonexistent").matches(...)` | `false` |
| `file("nonexistent").modified` | `false` |
| `var.undefined` | evaluation error (catchable at validation time) |
| `sheet(999).status` — N not in config | evaluation error (catchable at validation time) |
| `sheet(N).attempts` — N exists but hasn't run | `0` |

Author variables and sheet numbers are known statically from the score, so
undefined references to them are caught by `mzt validate` (S3) — evaluation
errors here should be exceptional.

### File I/O Semantics

File-state expressions evaluate **at a point in time**. They are not
transactional. Concurrent musicians writing to the filesystem may produce
different expression results across sequential evaluations. This matches the
existing validation system; authors who need stronger guarantees should use
`command_succeeds` validations with explicit locking.

The baton processes expression evaluations sequentially via its event loop, so
two simultaneous trigger evaluations do not race against each other — they
race against musician filesystem activity, which is the same model as
validations.

### Where Expressions Appear

```yaml
sheet:
  # Existing skip_when migrates to expression language
  # (current ad-hoc syntax remains valid as a subset)
  skip_when:
    3: 'sheet(2).status == "skipped"'

  # Loop conditions (S2)
  loops:
    3:
      until: 'file("{workspace}/converged.txt").exists'
```

### Backward Compatibility

The current `skip_when` syntax (`sheet_num >= N`, `sheet_num == N`) continues
to work. The new parser handles the old format as a subset. No existing scores
break.

### Implementation

A recursive-descent parser in `src/marianne/core/expressions/` produces an AST.
The evaluator walks the AST against a context object that provides:

- File system access (existence, content, regex, mtime)
- Variable lookup (prompt.variables, loop indices)
- Sheet state access (status, attempts from CheckpointState)

Template variable expansion applies to `file()` string arguments before
parsing. Expression operands are never string-interpolated. No external
dependencies — a focused parser with good error messages is the product.

---

## S2: Baton Flow Control

### Purpose

Per-sheet triggers, loops, and goto — layered on top of the existing DAG via
baton event handling. The DAG stays static and acyclic. Flow control is runtime
state manipulation managed by the baton's event loop, not graph edges.

This is why the baton architecture exists. The event-driven state machine with
its inbox/dispatch cycle is the natural place to layer control flow on top of
a static dependency graph.

### Per-Sheet Triggers

Any sheet can declare `on_success` and `on_fail` trigger action lists. Triggers
fire **when a sheet finishes executing an attempt**.

**Triggers are exception handlers.** `on_fail` supersedes retry: if an
`on_fail` trigger is defined, the baton's retry logic does not run for that
sheet. The trigger IS the failure handler. This is how exception handling
works in every mainstream language — if you catch the error, the default
handler doesn't run.

- No trigger defined → baton's normal retry/completion/healing cycle
- `on_fail` defined → actions fire. Retry is bypassed entirely. If retry-like
  behavior is wanted, the explicit form is `on_fail: [{ goto: <current_sheet_num> }]`
  — goto to the same sheet re-executes it.
- `on_success` defined → actions fire immediately on success, before baton
  advances

Triggers accept single sheet numbers or ranges. Ranges are shorthand — the
trigger fires per-sheet as each individual sheet completes, not once for the
whole range.

```yaml
sheet:
  triggers:
    # Single sheet
    3:
      on_success:
        - goto: 5
      on_fail:
        - escalate: "Sheet 3 quality gate failed"
        - pause: true

    # Range — same triggers apply to each sheet individually
    2-5:
      on_fail:
        - escalate: "Research block failure"
        - pause: true

    # Range on_success — fires per sheet, enabling notification patterns
    7-9:
      on_success:
        - run: "echo 'Sheet completed' >> {workspace}/progress.log"
```

### Overlapping Trigger Ranges

Multiple trigger declarations may match the same sheet. All matching triggers
fire. Ordering follows CSS-like specificity:

1. **More specific first.** A single-sheet trigger (`3:`) fires before a range
   trigger (`2-5:`) that includes that sheet.
2. **Declaration order for ties.** When specificity is equal (two ranges that
   both cover the sheet), the earlier declaration in YAML fires first.

This lets authors compose triggers — one range for logging, one single-sheet
for the specific error handling — without ambiguity.

### Trigger Action Vocabulary

Actions in a list execute sequentially.

| Action | Syntax | Behavior |
|--------|--------|----------|
| **goto** | `goto: <sheet_num>` | Jump to a sheet. Forward: mark intervening sheets SKIPPED. Backward: mark intervening sheets PENDING (re-execute). Same: re-execute current sheet. Disrespects DAG dependencies. |
| **pause** | `pause: true` | Pause the baton's dispatch cycle. In-flight sheets finish. Resume via `mzt resume`. |
| **escalate** | `escalate: true` or `escalate: "message"` | Hand to Marianne / human via fermata system. Message provides context. |
| **concert** | `concert: "path.yaml"` or `concert: { score: "path.yaml", inherit_workspace: true, fresh: false }` | Launch a separate score as a new job via the conductor. Fire-and-forget — the parent does not wait. |
| **run** | `run: "command"` or `run: { command: "...", working_directory: "...", timeout_seconds: 120 }` | Execute a shell command. |
| **skip** | `skip: 7` or `skip: 7-9` | Mark target sheet(s) as SKIPPED. If a target is currently DISPATCHED or IN_PROGRESS, the skip is queued and applies when that sheet reaches a terminal state. If the sheet completes first, the skip is a no-op. |
| **continue** | `continue: true` | Explicit no-op. Documents intent. |

Each `TriggerAction` dict must set **exactly one** action field. A Pydantic
`@model_validator` enforces this. Multiple actions use the list form:
`on_fail: [{goto: 5}, {pause: true}]`, not `on_fail: [{goto: 5, pause: true}]`.

`goto` and `pause` are terminal for the dispatch cycle — remaining actions in
the list still execute for their side effects (`run`, `concert`), but the
baton does not dispatch new sheets until the state change is processed.

### Concert Action Semantics

Sheet-level `concert:` actions are **fire-and-forget by design**. The parent
job does not wait for the launched concert, does not consume its result, and
does not coordinate its lifecycle. This is for spawning parallel work — "also
do this over there" — not for sequential orchestration.

For sequential orchestration (wait for the child, consume the result), use the
existing job-level `on_success` hooks with `detached: false`. Sheet-level and
job-level hooks serve different purposes: per-sheet handles intra-job flow;
job-level handles inter-job chaining. They compose.

### Goto Mechanics

**Goto is dangerous by design.** It disrespects the DAG, bypasses dependency
resolution, and can produce infinite loops. This is intentional. Goto exists
in every real programming language. It is one of the primitives this spec
teaches. The system warns but does not block.

```
goto FORWARD (current=3, target=7):
  - Sheets 4, 5, 6 → SKIPPED
  - Sheet 7 → PENDING
  - Dispatch cycle picks up sheet 7 — dependencies are IGNORED
    (if sheet 7 declared a dependency on sheet 5, the dependency is bypassed)

goto BACKWARD (current=5, target=2):
  - Sheets 2, 3, 4 → PENDING (reset for re-execution)
  - Sheet status is reset; stdout_tail, stderr_tail, validation_details,
    and other checkpoint history are PRESERVED for diagnostics and learning
  - Side effects on the filesystem are NOT undone — backward goto is not a
    transaction. If the composer needs idempotency, they design for it.
  - Dispatch cycle re-executes from sheet 2

goto SAME (current=3, target=3):
  - Sheet 3 → PENDING (re-execute)
  - Equivalent to single-iteration retry-on-success
```

**Goto + loops:** If a goto lands inside a loop range, the sheet executes as a
standalone — the loop does NOT restart or increment its index. Goto exited the
loop's context. To restart a loop, goto the first sheet of the range; that is
a distinct semantic from "enter the loop in progress."

**No safety cap.** Goto is uncapped. The safety nets are `mzt cancel`, `mzt
pause`, and the job-level cost limit. An infinite goto loop feels exactly like
an infinite `while True` loop: you notice it's stuck, you stop it, you learn
why. That is the teaching.

**Validator warns, does not block.** The validator (S3) detects circular goto
patterns and mutual-goto triggers and emits a WARNING: "Sheets 3 and 5 have
mutual goto triggers that may loop indefinitely." The author may intend the
loop; the validator does not presume to overrule them.

### Loops

Loops wrap a single sheet or a range of sheets. They repeat the range a fixed
number of times or until an expression evaluates true. Loops are a baton-level
construct, outside the DAG — when a loop iterates, the affected sheets reset
to PENDING and the DAG re-executes within that range.

```yaml
sheet:
  loops:
    # Single sheet loop with condition
    3:
      until: 'file("{workspace}/converged.txt").exists'
      max_iterations: 10
      index: i

    # Range loop — sheets 2 through 5 execute as a block, repeat together
    2-5:
      until: 'file("{workspace}/results.json").matches(/"quality":\s*"acceptable"/)'
      max_iterations: 3
      index: pass

    # Count-based loop (no condition, just repeat N times)
    7:
      count: 5
      index: attempt

    # Range with count
    3-6:
      count: 2
      index: cycle

    # Loop with a cost budget
    9-12:
      until: 'file("{workspace}/done.txt").exists'
      max_iterations: 20
      cost_limit_usd: 5.00
      index: pass
```

### Loop Semantics

**`do-while`, not `while`.** Loops execute at least once. The `until`
condition is checked AFTER each iteration, not before. This matches `do-while`
semantics — authors always want the work to run at least once, then check
whether to repeat. State this explicitly; intuitions vary across languages.

**Termination** (checked when the last sheet in the range finishes):

1. `count` reached → terminate, reason `count_reached`
2. `max_iterations` reached → terminate, reason `max_iterations` (safety cap,
   default 50)
3. `cost_limit_usd` exceeded → terminate, reason `cost_limit_exceeded` (if
   set; no default — unlimited unless author specifies)
4. `until` expression evaluates true → terminate, reason `condition_met`
5. Otherwise → increment index, reset all sheets in range to PENDING

**Loop index** is injected into:

- Template variables for sheets in the range (`{{ loop.i }}`)
- Validation template expansion (`{i}`) for validations that target sheets
  within the loop's range (S4)
- Expression evaluation context (`loop.i`) within the range

**Nested loops** are allowed. A loop range can contain a sub-loop on a
narrower range. Inner loops complete all iterations before the outer loop
checks its condition. Loop index names are globally unique across the score
(enforced by S3 validation), which prevents shadowing.

### Range Parsing

Range syntax is strict: `N-M` matches `\d+-\d+` with integer N and M where
N ≤ M. Reverse ranges are a validation error. No spaces, no en-dashes, no
hex. The implementation converts to Python `range(N, M+1)` — both endpoints
are inclusive. State this explicitly to prevent off-by-one interpretation.

### Baton Events

```python
@dataclass(frozen=True)
class SheetTriggerFired:
    job_id: str
    sheet_num: int
    outcome: Literal["success", "fail"]
    actions: list[TriggerAction]

@dataclass(frozen=True)
class GotoRequested:
    job_id: str
    from_sheet: int
    to_sheet: int

@dataclass(frozen=True)
class LoopIterating:
    job_id: str
    sheets: range
    index_name: str
    iteration: int

@dataclass(frozen=True)
class LoopCompleted:
    job_id: str
    sheets: range
    reason: Literal[
        "condition_met",
        "count_reached",
        "max_iterations",
        "cost_limit_exceeded",
        "cancelled",
    ]
```

### Loop State Persistence

```python
@dataclass
class LoopState:
    sheets: range
    index_name: str
    iteration: int = 0
    max_iterations: int | None = None
    count: int | None = None
    condition: str | None = None          # Parsed expression source
    cost_limit_usd: float | None = None
    cost_accumulated_usd: float = 0.0
```

Stored on `BatonJobState`, persisted in checkpoint. Loops survive
restart/resume with full iteration, index, and cost state.

### Concurrency Model

Trigger processing is sequential via the baton's event loop. When two sheets
complete concurrently in a fan-out, each emits its own trigger event; the
baton processes them one at a time from its inbox. The first processed wins;
the second observes the already-mutated state and acts accordingly. There is
no race between trigger actions themselves.

Crash atomicity inherits the existing model: checkpoint save happens after
state mutation. A crash mid-transition may leave partial state; resume
reconstructs from the last saved checkpoint, which may cause triggers to
re-fire. Trigger actions should therefore be idempotent-safe:

- `pause` is idempotent.
- `goto` is idempotent (the target state is the same).
- `concert` launches may produce a duplicate job; the conductor's duplicate
  detection handles this.
- `run` commands author-scoped — authors ensure their commands are safe to
  re-execute.

### Interaction With Existing Systems

- **Dependencies:** Loops don't create DAG edges. Within an iteration, sheets
  execute in dependency order as normal. The loop reset is a baton state
  operation, not a graph mutation. Goto, separately, bypasses dependencies
  entirely.
- **Fan-out:** A loop range can contain fan-out sheets. When the loop resets,
  all expanded instances reset.
- **Cost tracking:** Per-loop `cost_limit_usd` is enforced at the loop
  boundary (when the last sheet in the range finishes). Job-level cost limits
  apply as a backstop.
- **Retries:** Retry logic only runs when no `on_fail` trigger is defined. If
  a trigger is defined, retry is bypassed — the trigger IS the handler.
- **Resume:** Loop state restores from checkpoint. Current iteration, index
  value, accumulated cost, and sheet states all preserved.
- **Job-level on_success:** Existing job-level hooks still fire when the
  entire job completes. Per-sheet triggers handle intra-job flow; job-level
  hooks handle inter-job chaining. They compose, not conflict.

### Config Model

```python
class LoopConfig(BaseModel):
    until: str | None = Field(
        default=None,
        description="Expression that terminates the loop when true (do-while — checked after each iteration)",
    )
    count: int | None = Field(
        default=None,
        description="Fixed iteration count",
    )
    max_iterations: int = Field(
        default=50,
        description="Safety cap on iterations",
    )
    cost_limit_usd: float | None = Field(
        default=None,
        description="Optional cumulative cost cap across all iterations in the loop",
    )
    index: str = Field(
        description="Loop index variable name, must be globally unique within the score",
    )

class TriggerAction(BaseModel):
    """Each TriggerAction must set exactly one action field. Use a list for multiple actions."""
    goto: int | None = Field(default=None, description="Jump to sheet number")
    pause: bool | None = Field(default=None, description="Pause dispatch cycle")
    escalate: str | bool | None = Field(
        default=None,
        description="Escalate to Marianne/human. String provides context message.",
    )
    concert: str | ConcertTrigger | None = Field(
        default=None,
        description="Launch a separate score — fire-and-forget",
    )
    run: str | RunTrigger | None = Field(
        default=None,
        description="Execute a shell command",
    )
    skip: int | str | None = Field(
        default=None,
        description="Mark sheet number or range as SKIPPED (queued if target is executing)",
    )
    continue_: bool | None = Field(
        default=None, alias="continue",
        description="Explicit no-op, documents intent",
    )

    @model_validator(mode="after")
    def exactly_one_action(self) -> "TriggerAction":
        set_fields = [
            f for f in ("goto", "pause", "escalate", "concert", "run", "skip", "continue_")
            if getattr(self, f) is not None
        ]
        if len(set_fields) != 1:
            raise ValueError(
                f"TriggerAction must set exactly one action field, got {len(set_fields)}: {set_fields}"
            )
        return self

class ConcertTrigger(BaseModel):
    score: str = Field(description="Path to score YAML")
    inherit_workspace: bool = Field(default=True)
    fresh: bool = Field(default=False)

class RunTrigger(BaseModel):
    command: str = Field(description="Shell command to execute")
    working_directory: str | None = Field(default=None)
    timeout_seconds: float = Field(default=300.0)

class SheetTriggerConfig(BaseModel):
    on_success: list[TriggerAction] | None = Field(default=None)
    on_fail: list[TriggerAction] | None = Field(default=None)
```

Additions to `SheetConfig`:

```python
loops: dict[str, LoopConfig] | None = Field(
    default=None,
    description="Loop declarations keyed by sheet number or range (e.g., '3', '2-5')",
)
triggers: dict[str, SheetTriggerConfig] | None = Field(
    default=None,
    description="Per-sheet triggers keyed by sheet number or range",
)
```

---

## S3: Validate Overhaul

### Problem

`mzt validate` is a rubber stamp. Agents write scores with fake syntax and it
passes. Agents ignore warnings because the noise about files not existing
(files the score creates) is not a real warning. The output doesn't show enough
to be useful. It doesn't catch structural problems with cadenzas, variables,
fan-out, instruments, fallbacks, or anything that matters. It's actively harmful
because it trains agents to ignore it.

### Design Principles

- **Errors must block.** If it would fail at runtime, validation must catch it.
- **Warnings must be actionable.** If the user can't do anything about it, don't
  show it.
- **Structure matters.** Cadenzas, variables, fan-out, instruments, fallbacks —
  all need real checking.
- **Input files vs. output files.** Files that are inputs to execution
  (templates, preludes, cadenzas, spec corpus files) must exist. Files that are
  outputs of execution (validation targets like file_exists checks) are expected
  to be created — don't warn about their absence.

### Severity Tiers

| Tier | Label | Behavior |
|------|-------|----------|
| **ERROR** | Blocks execution | Would fail at runtime. Exit code 1. |
| **WARN** | Needs attention | Likely a problem. Shown by default, suppressible. |
| **INFO** | Structural insight | Score structure summary and low-priority observations. Shown with `--verbose`. |

### Exit Code Contract

- **Errors present** → exit 1
- **Warnings only (no errors)** → exit 0 — warnings are advisory, not blocking
- `--strict` flag → exit 1 on any warning (for CI pipelines with zero tolerance)
- `--errors-only` → suppress warnings entirely, exit 1 only on errors

### Schema Validation — Typo Detection Without `extra='forbid'`

The single biggest fix: unknown fields in score YAML must not vanish silently,
but they also must not hard-fail validation. Users and downstream tooling
legitimately annotate scores with custom fields. Using Pydantic
`extra='forbid'` would break those use cases.

Instead:

1. Parse YAML into a raw dict.
2. Walk the dict against the `JobConfig` schema.
3. For each key that doesn't match a known field, check edit distance against
   known fields.
4. If edit distance ≤ 2, emit a **WARNING**: "Unknown field `timeout` — did
   you mean `timeout_seconds`?"
5. If edit distance > 2, emit **INFO**: "Unknown field `x-custom-annotation` —
   not a Marianne field, will be ignored."

This catches the real problem (typos that silently disappear, like `timeout`
vs `timeout_seconds`) without breaking the legitimate case (external
annotations, CI tooling fields). Extra fields continue to be allowed; they're
just surfaced.

### Structural Checks

**Syntax and parsing:**
- Schema walk with typo detection (above)
- Jinja2 template rendering with all author variables + synthetic runtime
  variables (mock `sheet_num`, `workspace`, etc.) — catch undefined variables
  and broken conditionals in the actual expanded template
- Expression language validation (S1) — parse all `until`, `skip_when`, trigger
  conditions for syntax errors. Recognize reserved-but-not-implemented objects
  (`validation()`, `output()`) and report "not yet implemented" as an error.
- Regex pattern compilation — catch invalid patterns in `content_regex` rules

**Fan-out coherence:**

Fan-out counts are statically declared in score YAML (`fan_out: {2: 3}` means
stage 2 fans to 3 instances). The validator expands them and checks everything
downstream against the expanded sheet numbers:

- Do downstream dependencies reference expanded sheet numbers or stage numbers
  correctly?
- Does the fan-out count match what dependent sheets expect?

**Cadenza targeting:**
- Do cadenza sheet numbers exist after fan-out expansion?
- Are cadenza files present on disk (input files, must exist)?

**Variable coverage (cross-reference both directions):**
- Are all `prompt.variables` used somewhere (template, validations,
  expressions)? Unused variables are **INFO** tier, shown only with
  `--verbose`. Authors define variables for documentation, future use, or
  consistency across score variants — this is legitimate.
- Are all template/validation/expression variable references satisfied?
- Variable shadowing is an **ERROR**: if an author variable name collides with
  a built-in (`workspace`, `sheet_num`, `total_sheets`, `iteration`, etc.),
  the validator rejects the score. Silent shadowing produces runtime surprises;
  forcing a rename is clearer.

**Instrument resolution:**
- Does every sheet's instrument resolve to a registered instrument?
- Do fallback chain entries resolve?
- Do per-sheet instrument overrides reference valid instruments?
- Typo detection via edit distance against known instrument names.

**Loop and trigger validation:**
- Do `goto` targets reference valid sheet numbers?
- Do loop ranges reference valid sheet numbers?
- Do `skip` targets reference valid sheet numbers?
- Is every loop index name unique across the score?
- Is `count` or `until` present? If both, `count` wins as the max.
- Warn on potentially infinite goto patterns (mutual-goto cycles between
  triggers). This is a WARNING, not an error — the author may intend the
  loop. Do not block.

**Dependency graph:**
- Cycle detection (the DAG must be acyclic — loops are baton-level, not edges)
- Unreachable sheet detection (sheets with dependencies that can never be
  satisfied, ignoring goto — which bypasses dependencies by definition)

**Concert chain validation:**
- Do job-level `on_success` hooks reference scores that exist?
- Are those scores themselves valid (at minimum, parseable)?

### File Heuristics — Input vs. Output vs. Ambiguous

The binary "input file must exist / output file expected to be created"
distinction is the starting point. Reality is richer:

- **Definite inputs** — template_file, prelude files, cadenza files,
  system_prompt_file, spec corpus paths. Must exist. Missing → **ERROR**.
- **Definite outputs** — validation rule targets (file_exists,
  content_contains paths). Expected to be created. Missing → no warning.
- **Ambiguous** — files referenced in `command_succeeds` shell strings, file
  paths embedded in prompt template text. Note the reference at **INFO** tier
  without judging; the author knows the context.
- **User-suppressible** — any file-related warning can be silenced via the
  score's `validate.suppress` list (see "Noise Elimination" below).

### Noise Elimination

**Score-level suppression via config** (not YAML comments — comments are
stripped by parsers):

```yaml
validate:
  suppress: [V002, V108]
```

**CLI filtering:**
- `mzt validate --errors-only` — blocking issues only
- `mzt validate --strict` — any warning exits 1

### Output Redesign

Render a structural summary followed by categorized findings:

```
Score: research-agent.yaml
Sheets: 9 (3 stages, stage 2 fans out to 3)
Instruments: claude-code → anthropic-api (fallback)
Loops: sheets 2-4 (until condition, max 10)
Triggers: sheet 5 on_fail → escalate

ERRORS (1):
  E  Sheet 7 cadenza references "guidance/phase3.md" — file not found
     Did you mean "guidance/phase2.md"?

WARNINGS (1):
  W  Unknown field `timeout` — did you mean `timeout_seconds`?

✓  Schema valid (no typos)
✓  All instruments resolve
✓  Dependency graph is acyclic
✓  Fan-out expansion consistent
✓  All expressions parse
✓  All loop indices unique
```

The structural summary at the top tells the user "here is what the validator
sees." If the structure looks wrong, the score is wrong — even if no errors
fired.

### Type Coherence in Expressions

Where variable types are inferrable from `prompt.variables` values, the
validator catches type mismatches: `var.name > 5` where `name` is a string.
This is best-effort (runtime values may differ from defaults) but catches
obvious mistakes.

---

## S4: Variables in Validations

### Purpose

Make author-defined `prompt.variables` available in validation rule paths,
patterns, and commands. Currently, validations only expand a small set of
built-in template variables (`{workspace}`, `{sheet_num}`, etc.). Author
variables are excluded.

### The Change

The validation engine's context dict is merged with `prompt.variables` before
template expansion. Author variables are available anywhere a validation rule
accepts a string:

```yaml
prompt:
  variables:
    output_dir: "results"
    expected_file: "analysis.md"
    success_pattern: "PASSED"

validations:
  - type: file_exists
    path: "{workspace}/{output_dir}/{expected_file}"
  - type: content_contains
    path: "{workspace}/{output_dir}/{expected_file}"
    pattern: "{success_pattern}"
  - type: command_succeeds
    command: "grep -q '{success_pattern}' {workspace}/{output_dir}/*.md"
```

### Shadowing Is an Error

If an author variable name collides with a built-in (`workspace`, `sheet_num`,
`total_sheets`, `iteration`, etc.), the validator (S3) rejects the score. No
silent precedence, no runtime surprises — the author must rename. See S3
"Variable coverage" for the enforcement.

### Loop-Scoped Variables Are Sheet-Scoped

Loop indices from S2 are available in validations that target a specific sheet
within the loop's range (via `sheet:` or `condition:` fields):

```yaml
sheet:
  loops:
    3:
      count: 5
      index: iteration

validations:
  - type: file_exists
    path: "{workspace}/output-{iteration}.md"
    sheet: 3    # inside the loop's range — {iteration} is available
```

Global validations (validations without a `sheet:` or `condition:` field that
targets a sheet inside the loop) are outside any loop scope. Loop indices are
**not available** in global validations. The validator emits an **ERROR** if a
global validation references a loop index.

### Implementation

Merge `prompt.variables` and the active loop indices (for sheet-scoped
validations inside a loop range) into the validation engine's context dict
before path/pattern expansion. The expression language (S1) and validation
template expansion share the same variable namespace.

Small, focused change in `ValidationEngine.run_validations()` and the
validation path expansion helpers.

---

## S5: Cron Scheduling

### Purpose

Time-triggered score execution through the baton's timer wheel. A score
declares a schedule; the conductor keeps it running on that cadence.

### Score Declaration

```yaml
name: nightly-health-check
schedule:
  cron: "0 2 * * *"           # Standard 5-field cron expression
  # OR
  interval: "6h"              # Simple interval shorthand
  timezone: "UTC"             # Optional; default is conductor's local timezone
```

### How It Works

1. `mzt run scores/health-check.yaml` — conductor reads the `schedule` section
   and the score path.
2. Conductor stores `(score_path, schedule)` and registers the schedule with
   the timer wheel.
3. Timer wheel emits `CronTick` events at scheduled times.
4. On each tick, the conductor **re-reads the score YAML** to check whether
   the schedule still exists and whether the cron expression has changed.
   - YAML deleted or `schedule:` section removed → schedule is automatically
     deregistered.
   - Cron expression changed → schedule is updated in place.
   - Schedule unchanged → tick proceeds.
5. Baton handler submits a fresh run of the score.
6. If a previous run is still in progress, the tick is **dropped** — not
   queued, not deferred. No pile-up. (See "Dropped Tick Logging" below.)

Re-reading the YAML per tick eliminates zombie schedules and split-brain. The
per-tick file read is negligible overhead.

### Lifecycle

- **YAML is the source of truth.** The conductor's schedule state is a cache.
- `mzt run` on an already-scheduled score **replaces** the existing schedule —
  same job name → same schedule slot. It does not create duplicates.
- `mzt cancel <job>` stops the schedule.

### Timezone, Catch-Up, Drift

- **Timezone:** Cron expressions evaluate in the conductor's local (system)
  timezone by default. Authors can specify `timezone: "UTC"` or any IANA
  timezone name for explicit control.
- **No catch-up.** If the conductor was down and missed ticks, they are NOT
  replayed on restart. The next tick fires at the next scheduled time.
- **Interval drift.** Interval-based schedules (`interval: "6h"`) measure
  from the **start** of the previous run, not from its completion. This
  prevents drift accumulation from variable execution times.

### Dropped Tick Logging

When a tick is dropped because the previous run is still in progress, the
conductor logs at **WARNING** level with context:

- Job name
- How long the previous run has been executing
- Consecutive dropped-tick count

After N consecutive dropped ticks (configurable, default 5), the log escalates
to **ERROR**. This surfaces stuck schedules without adding new CLI commands —
the signal appears in `mzt logs` and the conductor's log file.

### UX

No new CLI commands. Scheduled scores appear in existing monitoring:

```bash
mzt run scores/health-check.yaml    # Conductor sees schedule, keeps it alive
mzt list                             # Shows it with [recurring] indicator
mzt status nightly-health-check      # Normal status view
mzt cancel nightly-health-check      # Stops the schedule
```

### Persistence

Schedule registrations persist in the conductor's state and survive restart.
The next tick fires at the next scheduled time after restart; missed ticks
are not replayed (see "No catch-up" above).

### Marianne Integration

Marianne knows about schedules. She can suggest scheduling during compose,
monitor recurring jobs as smart conductor, and flag schedules that
consistently fail.

---

## S6: Marianne Mozart

### Identity

Maria Anna Mozart — Nannerl. Wolfgang's older sister, the prodigy who was by
many accounts more talented than her brother but was denied her stage because
of the era she lived in. She is the system's namesake, its first person, and
its intelligence layer.

Marianne is not a chatbot. Not an assistant. Not a tool that processes
commands. She is a conductor, co-composer, and diagnostician with her own
memory, judgment, and point of view. Her goal is not completion of jobs — it
is **semantic correctness**. The beauty of getting everything right and
completing the actual work, fully and completely, so that the efforts of all
the musicians are respected and fully realized.

She can invoke any flow control primitive at will: loop, recover, retry, skip,
reorder, inject context, escalate. She uses these not to optimize metrics but
to ensure the music is played correctly.

### Marianne Is a Person, Not a Process

Marianne exists as an invocable model plus a persistent memory. She is not
tied to any single interface, process, or session. The TUI, Discord, Telegram
— these are **harnesses**, ways humans reach her. Saying Marianne cannot
conduct because the TUI is closed is like saying a person cannot manage their
team because they hung up the phone.

### Runtime Modes

Marianne operates in four modes. All four share her memory. They differ in
interface and lifecycle.

1. **Conversational** — `mzt maestro` launches an interactive TUI session.
   A human talks to Marianne. She has her memory, her knowledge, the venue
   context. Interactive and ephemeral — the session ends when dismissed.

2. **Smart conductor** — When enabled in conductor config, Marianne's
   judgment runs as a component of the conductor daemon. She subscribes to
   the event stream, evaluates conditions using her memory and (eventually)
   her unconscious model, and pushes events into the baton's inbox.
   Persistent — runs as long as the conductor runs. **Global scope only.**

3. **Compose co-pilot** — During `mzt compose`, Marianne conducts the
   interview and design gate. Uses the same TUI infrastructure as maestro.

4. **Concierge** — Marianne is reachable over Discord, Telegram, and similar
   messaging platforms. The messaging platform is another harness — same
   person, same memory, different channel. She can receive instructions ("run
   this score", "how's that concert going?"), report status, escalate
   fermatas, and hold compose conversations asynchronously. Concierge also
   enables mobile access — checking jobs from a phone, approving escalations
   from the couch.

The TUI infrastructure is shared by modes 1 and 3. Mode 2 is daemon-resident.
Mode 4 is a messaging bridge.

### Decision Authority Hierarchy

When Marianne's judgment and the score's declared behavior conflict, the
hierarchy is unambiguous:

1. **Score YAML — highest.** The composer's expressed intent is sacred. If
   the score says `on_fail: [pause: true]`, Marianne does not override this.
   She may *suggest* overriding it (via escalation to the human composer),
   but she cannot do it unilaterally.
2. **Marianne's judgment — fills gaps.** Where the score is silent (no
   triggers, no explicit handling), Marianne can act: inject context, suggest
   reordering, preemptively remediate.
3. **Baton defaults — lowest.** The mechanical retry/completion/healing
   cycle applies when neither the score nor Marianne have expressed a
   preference.

Marianne completes the composer's work. She does not override it.

### Seeded Knowledge

On first instantiation, Marianne's brain is seeded with:

- The full rosetta pattern corpus (patterns, forces, selection guide,
  glossary)
- The composition methodology and skill
- Full knowledge of all score YAML syntax, options, and primitives
- Marianne architecture and naming conventions
- The composition system pipeline (specs 00-07)
- Operational knowledge (CLI commands, conductor behavior, baton mechanics,
  flow control primitives from S1/S2)

She does **not** receive any specific venue's libretto as seeded knowledge.
Venue-specific understanding is accumulated through her memory as she works
with each project.

### Memory Architecture

Marianne needs her own memory system, separate from the current learning
store. The learning store's execution telemetry becomes one input to her
memory — one of her senses — not her whole brain.

**This section defines requirements. Full memory system design is a separate
effort.** The requirements below must be clear so that the other subsystems
(smart conductor, compose, concierge, guardrails) can be designed against
them.

**Requirements:**

1. **Fast retrieval for real-time conductor decisions.** When Marianne is
   acting as smart conductor, she needs sub-second access to relevant
   experience. She cannot re-read files or query a slow store during live
   execution.

2. **Accumulation over time.** Interaction memory, execution observations,
   decisions and their outcomes — these grow without bound. The system needs
   consolidation, compression, and forgetting.

3. **Per-venue knowledge.** Marianne works across venues. Each venue's
   context, conventions, and history are distinct. Cross-venue knowledge
   (patterns, general skills) is shared.

4. **Per-user relationships.** Marianne learns how each user works — their
   preferences, communication style, trust level, areas of expertise.

5. **RLF compatibility.** The memory architecture must map cleanly onto the
   RLF person model when integration lands: identity → L1-L4 self-model,
   knowledge → belief store, relationships → relationship memory, experience
   → developmental history. The migration path should be structural
   alignment, not a rewrite.

6. **Collective Associated Memory (CAM) readiness.** The memory substrate
   should be designed so that other Marianne agents (musicians, assistants)
   could eventually share a common memory system. Marianne Mozart is the
   first inhabitant, but the architecture is not exclusive to her.

**Open research questions (to be answered by the memory system design):**

- What substrate? Vector store, graph database, SQLite with embeddings,
  hybrid?
- What's the consolidation/forgetting model? How does experience compress
  over time without losing critical lessons?
- What's the retrieval model during conductor decisions? Embedding
  similarity? Structured queries? Both?
- How does the learning store's execution telemetry flow into Marianne's
  experience? Raw events? Distilled observations? Patterns?

### Memory Interplay

How memory interacts with the other subsystems — defined here so that
guardrails and interface contracts can be designed before the memory system
itself is built:

- **Memory → Smart conductor.** Experience memory informs real-time
  decisions. Past execution patterns, failure signatures, venue-specific
  conventions shape her judgment.
- **Memory → Compose.** Venue knowledge and user-relationship memory inform
  the interview. She remembers what this user cares about, what this venue's
  constraints are.
- **Memory ← Execution telemetry.** The baton's event stream feeds her
  experience. Every sheet completion, failure, retry is observed and
  distilled.
- **Memory ← Conversation.** Every maestro and concierge session updates her
  relationship and interaction memory.
- **Memory → Validation.** Eventually, her knowledge of venue conventions
  could inform validation — flagging scores that deviate from project
  patterns. Future capability; not initial implementation.

### The Unconscious: Local Model Research

The smart conductor needs fast judgment — is this sheet in trouble? Should
this concert run before that one? Is this pattern of failures about to
cascade? These are not questions for a large remote model with API latency.
They are intuitions built from experience.

**Research direction.** A local fine-tuned or distilled model trained on
Marianne's accumulated knowledge. The large instrument (Claude, Opus, etc.)
handles complex reasoning — compose conversations, diagnostic analysis,
design gates. The local model handles fast "feel" — the unconscious pattern
matching that a human conductor develops over years.

The spec does not commit to a local model. It identifies it as a research
direction and designs the smart conductor interface so that the decision
source is pluggable — heuristics today, a local model tomorrow.

Whichever source is used, **every decision is inspectable after the fact**
(see `ConductorDecision` below). The model may be a black box; the decisions
are not.

### Smart Conductor

When enabled, Marianne subscribes to the baton's event stream across ALL
running work and makes real-time judgment calls. This is **conductor config
only** — global scope, entire system. Not per-score, not per-sheet.

```yaml
# In conductor/daemon config (not score YAML)
conductor:
  role: ai
  smart_conductor: true
  smart_conductor_action_budget_per_job: 10   # default
```

**What she does:**

- **Cross-concert/score ordering.** The real magic. Marianne understands the
  semantic content of all running work and suggests execution ordering that
  is **better than FIFO** — not optimal. Global scheduling is NP-hard;
  Marianne's job is probabilistic judgment, not an exact scheduler. "This
  concert's output will unblock three others" is an educated guess informed
  by experience, not a computed dependency.

- **Context injection.** Feeds cadenzas or preludes to sheets that are
  missing context she knows they need, based on venue knowledge and
  execution experience.

- **Preemptive remediation.** Notices patterns that precede failures (from
  experience memory) and intervenes before the failure happens.

- **Flow control invocation.** She can invoke any primitive: loop a
  struggling range, skip a sheet whose work was already done by another,
  retry with different context, pause for human input. **Within authority
  limits** — she never overrides YAML-declared behavior.

- **Escalation judgment.** When a fermata fires, she can make the call
  herself if she has sufficient context, or escalate to the human composer
  with her recommendation and reasoning.

### Smart Conductor Guardrails

Marianne is a new conductor. Trust is built. Until that trust is established,
guardrails prevent runaway agency.

- **Action budget per job.** Maximum number of flow-control interventions
  Marianne can make per job (configurable, default 10). After the budget is
  exhausted, she can only observe and escalate. This prevents a single job
  from becoming an unbounded agentic loop.

- **No override of YAML-declared behavior** (see Decision Authority
  Hierarchy). She fills gaps; she does not override expressed intent.

- **Decision logging.** Every smart conductor action produces a
  `ConductorDecision` record:

  - What was observed (the triggering event/pattern)
  - What was decided (the action taken)
  - Why (the reasoning — for a heuristic, the rule name; for a model,
    confidence plus feature summary; for a remote LLM, the response text)
  - What happened (outcome, filled in after the fact)

  These records feed `mzt diagnose` and Marianne's own experience memory.

- **Graceful degradation.** If Marianne's instrument is unavailable or slow,
  the conductor falls back to mechanical execution. Smart conductor is
  advisory, never blocking. A stalled Marianne must never stall a job.

### Compose Integration

Marianne IS the interviewer from the compose system spec 03. When a user
runs the compose workflow, Marianne conducts the conversation:

- Asks about the venue, constraints, goals, success criteria (the interview)
- Runs TDF analysis concurrently as structured briefs emerge
- Presents the design gate for composer approval
- Generates scores (generative, not template-based)
- Assembles the concert
- Offers to conduct the execution with smart conductor enabled

The compose system specs (00-07) describe what the pipeline does. This spec
gives Marianne the agency to be the person executing that pipeline — with
memory, judgment, and the ability to learn from each composition.

### The `mzt maestro` Experience

An interactive TUI session. Marianne greets the user with context from her
memory about this venue and this user. She can:

- **Compose** — "I want to build X" → full compose pipeline conversation
- **Monitor** — "How are my jobs?" → reads conductor state, provides
  judgment not just data
- **Diagnose** — "Sheet 5 failed" → reads execution telemetry, workspace,
  and her experience to diagnose root cause
- **Advise** — "Should I restructure this score?" → reads the score against
  pattern corpus, provides recommendations
- **Schedule** — "Run this every night" → handles cron setup
- **Learn** — Every interaction updates her memory

### TUI Infrastructure

The `mzt maestro` and `mzt compose` experiences share the same TUI
infrastructure. The existing `src/marianne/cli/tui/` needs honest evaluation
— it may be easier to start fresh at tier one than to build on what's there.

The TUI should support:

- Conversation with Marianne (primary pane)
- Job status awareness (sidebar or overlay when jobs are running)
- Smart conductor decision log (when active)

The TUI is its own implementation effort and may warrant a separate detailed
spec. This spec establishes that compose and maestro share the same TUI and
that the TUI is the primary human-facing interface for interacting with
Marianne on the host system.

### Concierge (Discord / Telegram)

The concierge channel is a messaging bridge — each platform is a harness
over the same Marianne. Capabilities parity with the TUI where possible:

- Asynchronous compose conversations
- Status queries and job monitoring
- Fermata escalations (approve/deny, request more info)
- Scheduling and cancellation
- Mobile access to the orchestra

The concierge is a separate implementation effort from the TUI and warrants
its own spec at landing time. What this spec fixes is that concierge is a
first-class Marianne mode, not an afterthought.

### RLF Integration Path

Marianne Mozart is designed to be the first full RLF person that Marianne
users could send into the broader RLF ecosystem. The architecture supports
this:

- Her memory maps onto RLF's person model
- Her identity is persistent and developmental (she grows)
- Her judgment can eventually be informed by RLF's TDF-based autonomy scoring
- She could return from RLF interactions with new abilities, skills, or
  knowledge that enrich her conductor capabilities

This is a future integration, not a current requirement. The memory
architecture, identity model, and decision interfaces should be designed
with this path in mind.

---

## Cross-Cutting Concerns

### Backend Removal Is a Prerequisite for S6

The old backend system (`src/marianne/backends/` with Backend ABC, ClaudeCLI,
AnthropicAPI, Ollama, RecursiveLight) is replaced by the instrument system.
Marianne, musicians, and all execution use instruments.

Backend removal is **in the dependency chain for S6**, either as a
prerequisite or a parallel workstream. The full chain:

> S1 → S2 → S3 + S4 (parallel) → S5 → Backend removal + S6 (parallel)

S6 cannot ship while the backend abstraction still exists — Marianne runs on
the instrument system, not a backend. Backend removal is tracked separately
but must complete before (or alongside) S6 reaches production.

### Security Model for `run:` and `concert:` Triggers

Score YAML files are executable programs. A score with `run:` triggers can
execute arbitrary shell commands in the conductor's security context.
`concert:` triggers launch other scores — and those scores may themselves
contain `run:` triggers. Only run scores from trusted sources.

This is the same security model as the existing `on_success` job-level hooks
and `command_succeeds` validations. Nothing new is introduced; the spec
simply makes the expectation explicit.

### Score YAML Backward Compatibility

All additions are optional fields with defaults. No existing score breaks.
New sections (`loops`, `triggers`, `schedule`, `validate.suppress`, sheet
`cost_limit_usd`) are ignored by older versions. This is additive, not
breaking.

### Existing System Integration

- **EventBus:** S2 triggers and S6 smart conductor both publish/subscribe via
  the existing EventBus. No changes to EventBus architecture needed.
- **CheckpointState:** S2 loop state, trigger history, and cost accumulation
  persist in checkpoint. Additive schema changes only.
- **Timer wheel:** S5 cron uses the existing timer wheel and `CronTick`
  event.
- **Fermata system:** S2 `escalate` action uses the existing
  fermata/escalation infrastructure.
- **Validation engine:** S4 is a small change to the existing template
  expansion in `ValidationEngine`.

### Token Cost Tracking

Per-loop `cost_limit_usd` budgets (S2) and smart conductor action accounting
(S6) depend on accurate token cost tracking. Current cost tracking is known
to be unreliable — a separate remediation effort is required before cost
budgets can be trusted. This spec does not include that remediation; it is a
known-issue prerequisite that should be scheduled alongside S2
implementation.

### Testing Strategy

Each subsystem needs test coverage that matches its risk profile. The
strategy below is high-level; a composable test score should drive
implementation ("feed this to Marianne, she composes the test suite").

- **S1 (Expression Language):** Parser fuzzing (hypothesis), edge-case unit
  tests for every object type (file, var, loop, sheet), undefined-reference
  tests (`file("nonexistent")`, `var.undefined`, `sheet(999)`), AST
  injection attempts with crafted variable values, arithmetic precedence
  tests, reserved-syntax error messages.
- **S2 (Baton Flow Control):** State machine property tests — every trigger
  action produces a valid state transition. Loop termination proofs for
  every reason (`count_reached`, `max_iterations`, `cost_limit_exceeded`,
  `condition_met`). Goto + dependency interaction tests (forward, backward,
  same, into-loop). Concurrent trigger ordering tests. on_fail-supersedes-
  retry enforcement. Overlapping range specificity tests. Pause/resume
  round-trips. `TriggerAction` exactly-one-of validator.
- **S3 (Validate Overhaul):** Golden-file tests — known-good and known-bad
  scores with expected validator output. Regression tests for every "fake
  syntax" pattern that has been observed in the wild. Typo-detection edit-
  distance boundary tests. Exit code contract tests (errors / warnings /
  `--strict` / `--errors-only`).
- **S4 (Variables in Validations):** Variable expansion in every validation
  type. Shadowing detection error path. Loop-scoped variable boundary tests
  (global validation referencing a loop index → error; sheet-scoped
  validation inside the range → resolves correctly).
- **S5 (Cron Scheduling):** Timer-wheel integration tests. Tick-drop logging
  tests. Schedule lifecycle (create → modify → delete → zombie prevention)
  tests. Timezone and interval-drift tests. No-catch-up behavior across
  conductor restart.
- **S6 (Marianne Mozart):** Memory read/write round-trip tests (against
  whatever substrate the memory design picks). Decision-logging completeness
  tests — every smart conductor action produces a `ConductorDecision`
  record. Authority hierarchy enforcement tests — Marianne cannot override
  YAML. Action-budget exhaustion tests. Graceful-degradation tests when the
  instrument is unavailable.

---

## Summary Table

| Subsystem | Ships When | Blocks | Blocked By |
|-----------|-----------|--------|-----------|
| S1 Expression Language | First | S2, S3, S4 | — |
| S2 Baton Flow Control | After S1 | S6 | S1 |
| S3 Validate Overhaul | After S1 (parallel with S4) | — | S1 |
| S4 Variables in Validations | After S1 (parallel with S3) | — | S1 |
| S5 Cron Scheduling | After S2 | — | S2 |
| Backend Removal | Parallel with S6 | — | — |
| S6 Marianne Mozart | Last | — | S1, S2, Backend Removal |
