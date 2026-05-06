# Validation Gaps Addendum — Things `validations:` Cannot Currently Catch

**Status:** active, append-only
**Anchored from:** `docs/plans/quality/2026-04-10-quality-remediation.md` (gitignored plan in the project's quality-remediation track)
**Purpose:** This file catalogs cases where a score's `validations:` block
should have caught a defective output, but the existing Marianne validation
types cannot express the check. These are gaps in the validator system,
not gaps in any single score.

When you find a gap during a thinking-lab review or an in-the-wild failure,
append a row to the table below. Even after a fix lands, leave the row in
place with a `**Resolved by:**` line — this file is a permanent record so
future composers can understand why certain validations look the way they do.

## Table format

Each gap entry has:
- **Date** — when surfaced
- **Source** — score / thinking-lab review / GH issue / production incident
- **Score & stage** — where the gap manifested
- **The claim** — what the score's prompt promises the agent will do
- **Why current validation can't catch it** — specifics about the
  validation type's limits
- **Suggested validator capability** — what would close the gap (new
  validation type, schema field, runtime metadata, etc.)
- **Status** — open / mitigated-in-prompt / resolved-in-validator

---

## 2026-05-05 — 24x7-trader thinking-lab review

Five-model review (Opus 4.7 / Gemini 3.1 Pro / Gemma 4 / GLM 5.1 / GPT-5.5)
of the 24x7-trader flagship score family in
`examples/finance/24x7-trader/`. Reviews live at
`workspaces/thinking-lab/review-{1..5}.md`.

### Gap 1 — Cross-file similarity for fan-out independence

| | |
|---|---|
| **Score & stage** | `pre-market.yaml` stage 2 (Source Triangulation, fan-out 3) |
| **The claim** | Three frames are structurally independent; a single agent could not have authored all three |
| **Why validation can't catch it** | Existing types (file_exists, content_regex, content_contains, file_modified, command_succeeds) check per-file. They cannot compare two files for similarity. A `command_succeeds` could shell out to `diff` or a Python trigram script, but writing this per-score is fragile. |
| **Suggested capability** | A `cross_file_similarity` validation type with `paths: [...]`, `max_jaccard_similarity: 0.4` or similar. Runs only when fan-out instances share a synthesizing parent. |
| **Status** | open — mitigated in prompt only |
| **Reviewer concurrence** | Opus, GLM, GPT-5.5 |

### Gap 2 — Model-family verification for fan-out instances

| | |
|---|---|
| **Score & stage** | `weekly-review.yaml` stage 2 / 3 (Delphi 2-round, three reviewer instruments) |
| **The claim** | Round 1 reviews come from three structurally different model families |
| **Why validation can't catch it** | `instrument_map` declares the assignment in the score, but no validation type verifies which instrument actually executed which sheet. If the conductor falls back to a default instrument due to a config error, a "three-family Delphi" silently degenerates into one-family same-prompt-three-times. |
| **Suggested capability** | A `sheet_executed_by` validation that checks runtime metadata: `path: "{workspace}/.marianne-observer.jsonl"`, `expected_instrument_per_sheet: {2: "reviewer-opus", 3: "reviewer-gemini", 4: "reviewer-glm"}`. Or expose `executed_instrument_name` as a validation variable so existing types can use it. |
| **Status** | open |
| **Reviewer concurrence** | GPT-5.5 |

### Gap 3 — Semantic content validation (LLM-as-judge)

| | |
|---|---|
| **Score & stage** | `pre-market.yaml` stage 4 (proposals each have 3 falsifiable claims); `weekly-review.yaml` stage 3 (R2 actually engages with other reviewers) |
| **The claim** | Output meets a quality bar that requires interpretation |
| **Why validation can't catch it** | regex/content checks verify presence of words/structure. They cannot verify whether claims are *falsifiable*, whether reviewers genuinely *engaged* with each other, whether a journal entry contains *real* reflection. |
| **Suggested capability** | An `llm_judge` validation type that runs a small instrument with a rubric prompt against the artifact. Cost-bound and clearly opt-in. Cheap-tier model so the evaluator is independent of the producer. |
| **Status** | open — currently flagged as "unfillable gap" in score documentation |
| **Reviewer concurrence** | All five reviewers |

### Gap 4 — Cross-stage consistency (synthesizer cannot dodge fermata)

| | |
|---|---|
| **Score & stage** | `weekly-review.yaml` stage 5 reading drawdown from agent-authored `today-decision.json` |
| **The claim** | Threshold check uses the deterministic drawdown, not the synthesizer's report |
| **Why validation can't catch it** | The synthesizer writes `today-decision.json`; the threshold check trusts it. Marianne has no built-in "compare these two files / two fields" validation. |
| **Suggested capability** | A `field_match` validation type taking two file paths, two JSON paths, and a tolerance. Or extend `command_succeeds` documentation/examples to make this pattern obvious. |
| **Status** | resolved-in-score-prompt by reading drawdown from `today-week-stats.json` (deterministic) and cross-checking against `today-decision.json` with mismatch as breach signal. Validator-level support would let this be declarative rather than imperative. |
| **Reviewer concurrence** | Opus, Gemini, GPT-5.5 |

### Gap 5 — File integrity / tamper detection

| | |
|---|---|
| **Score & stage** | All scores reading from `risk-envelope.yaml`. Agent has Write/Edit tools. |
| **The claim** | The risk envelope is sovereign over the agent; the agent cannot widen its own caps |
| **Why validation can't catch it** | The agent can edit the envelope file mid-run. We've mitigated by snapshotting sha256 at stage 1 and verifying at every gate stage, but this is imperative bash inside `command_succeeds` — fragile and verbose. |
| **Suggested capability** | A `file_unchanged_since` validation: `path: ...`, `since_stage: 1`. Or an integrity-pinned prelude category that keeps the file read-only-injected (no agent write path). |
| **Status** | mitigated-in-score-prompt (sha256 snapshot + verify) |
| **Reviewer concurrence** | Gemma |

### Gap 6 — Sheet correctness (vs structural validity)

| | |
|---|---|
| **Score & stage** | `midday.yaml` stage 1 (Triage Gate classification) |
| **The claim** | Positions are classified into the *correct* bucket — e.g., a position 15% below its stop must land in `CUT_NOW`, not `HOLD` |
| **Why validation can't catch it** | We can validate that all positions appear in some bucket (structural). We cannot easily express "for every position p where p.unrealized_pnl_pct < -p.initial_stop_pct, p must be in CUT_NOW." That's a complex predicate. |
| **Suggested capability** | A `predicate_against_data` validation type with a Python-ish DSL that takes the parsed JSON and asserts properties. Or richer first-class support for inline Python checks. |
| **Status** | open |
| **Reviewer concurrence** | Opus, Gemma, GLM, GPT-5.5 |

### Gap 7 — Trade-log entry authenticity

| | |
|---|---|
| **Score & stage** | `market-open.yaml` stage 5; `midday.yaml` stage 4 (any deterministic execution stage) |
| **The claim** | Entries appended to `trade-log.jsonl` correspond to real broker responses, not fabricated lines |
| **Why validation can't catch it** | We check that today's date appears in trade-log; an LLM with file-write access could write `{"date": "2026-05-05", "fake": true}` and pass. We've mitigated by extracting execution to `execute_slate.sh` (deterministic, no LLM in the path), but if a future score keeps execution agentic, the gap returns. |
| **Suggested capability** | A `produced_by_instrument` constraint on validations — assert the file was last modified by a `cli` instrument, not an LLM-bearing one. Requires the engine to track per-file authorship. |
| **Status** | mitigated-by-design (execution moved to `cli` instrument). Documented to remain in the addendum because the gap *exists*; the score family avoided it by structural choice, not by validator support. |
| **Reviewer concurrence** | Opus, Gemini, Gemma, GLM, GPT-5.5 |

### Gap 8 — Anti-anchoring in Delphi Round 2

| | |
|---|---|
| **Score & stage** | `weekly-review.yaml` stage 3 |
| **The claim** | Round 2 reviewers consider others' R1 outputs but do not anchor on first-read |
| **Why validation can't catch it** | We can check that R2 cites at least one of the other two reviewer names (Gemma's suggestion), but this is a weak proxy. True anti-anchoring requires randomized presentation order, which static YAML cannot express. |
| **Suggested capability** | A score-level `randomize_order` field for context injection ordering, or a `presentation_seed` derived from sheet number to vary order across instances. |
| **Status** | open — partial mitigation via prompt instruction |
| **Reviewer concurrence** | Opus, GLM, GPT-5.5 |

### Gap 9 — Pre-stage external preconditions

| | |
|---|---|
| **Score & stage** | All execution phases checking for bootstrap completion + fermata file |
| **The claim** | The score halts cleanly when external preconditions aren't met (bootstrap unrun, fermata pending) |
| **Why validation can't catch it** | Validations run AFTER a stage executes. Preconditions need to abort BEFORE the agent burns tokens. We've placed bash blocks in the prompt that exit non-zero, plus stage-1 validations as backstops. This is duplicative. |
| **Suggested capability** | A score-level `preconditions:` block with `command_succeeds`-style checks evaluated before any sheet runs. Or extend `skip_when_command` to support `fail_when_command` (skip = success today, but precondition violation is a failure not a skip). |
| **Status** | mitigated-in-score-prompt |
| **Reviewer concurrence** | Opus, GLM, GPT-5.5 |

### Gap 10 — Cumulative-history validations (no row-uniqueness check)

| | |
|---|---|
| **Score & stage** | `market-close.yaml` stage 3 (benchmarks.csv append) |
| **The claim** | benchmarks.csv has unique-by-date rows; no day double-counted |
| **Why validation can't catch it** | content_regex matches a row exists; cannot assert uniqueness across rows. command_succeeds with `awk` works but is fragile. |
| **Suggested capability** | A `csv_unique_key` or `tabular_invariant` validation that loads a CSV/TSV/JSONL file and asserts a key-uniqueness property. |
| **Status** | open |
| **Reviewer concurrence** | GPT-5.5 |

### Gap 11 — Live-trading two-key contract enforcement

| | |
|---|---|
| **Score & stage** | All scores using `BROKER_CMD` |
| **The claim** | `place_order` refuses live trades unless `BROKER_LIVE=1` AND `<workspace>/LIVE_TRADING_ACKNOWLEDGED` exist |
| **Why validation can't catch it** | The contract is documented in `_techniques/broker.md` and the reference paper broker hard-locks paper. But a custom broker that ignores the two-key rule is contractually non-compliant — and the scores can't tell. |
| **Suggested capability** | A pre-execution wrapper script (`broker_safety.sh`) that wraps any `BROKER_CMD` and enforces the rule before delegating. Wrapper would itself be a `cli` script and could be required by the scores. (Not strictly a validator gap; an architectural pattern Marianne could codify.) |
| **Status** | open — handled by reference broker, not enforced for custom implementations |
| **Reviewer concurrence** | Opus (CRIT-3), GPT-5.5 |

### Gap 12 — Concurrent-write detection / workspace locking

| | |
|---|---|
| **Score & stage** | All execution phases sharing a workspace (positions.json, today-date.txt, trade-log.jsonl) |
| **The claim** | Phases run sequentially; concurrent writes don't corrupt state |
| **Why validation can't catch it** | The cron schedule provides hour-scale separation. A long-running phase can overlap. Marianne has no concept of workspace locking — two scores writing the same file race. |
| **Suggested capability** | An advisory `workspace_lock` field at score level (acquired before stage 1, released after stage N). Or a conductor-level "max-concurrent-jobs-per-workspace" config. |
| **Status** | open |
| **Reviewer concurrence** | GLM, GPT-5.5 |

---

## Process

When the next thinking-lab review surfaces a validation gap:

1. Append a new dated section above (or under the most recent dated section
   if it covers the same review).
2. For each gap, fill the table format columns. Be specific about file
   path and validation type tried.
3. If the gap is later closed by a Marianne validator improvement, add a
   `**Resolved by:** <commit / spec / PR>` line to the row but **do not
   delete the row**. Resolved gaps document why old scores look the way
   they do.
4. Cross-reference the related GH issue if one exists.

This file is the institutional memory of "validations Marianne should be
able to express but currently can't." It's deliberately verbose — future
composers benefit from seeing the full reasoning, not just a one-line
lessons-learned summary.
