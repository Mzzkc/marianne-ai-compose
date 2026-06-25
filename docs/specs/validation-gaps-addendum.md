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
| **Suggested capability** | A score-level `preconditions:` block with `command_succeeds`-style checks evaluated before any sheet runs. Or extend `skip_when` to support `fail_when_command` (skip = success today, but precondition violation is a failure not a skip). |
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

## 2026-05-12 — corpus-build stage 7 fast-fail (cli/bash sheet)

Static-analysis-level gap (not a sheet `validations:` gap). `mzt validate`
ran clean against the score before the run; the sheet still failed in
~3 ms with bare `Exit code 2` and no captured output. Diagnosed by
manually rendering the Jinja template and feeding it to `bash -c`.

### Gap 13 — Bash `${#var}` / `${#arr[@]}` opens a Jinja comment

| | |
|---|---|
| **Score & stage** | `scores/corpus-build.yaml` stage 7 (`cli` font install). Same pattern previously found in `import-mill` stage 2. |
| **The claim** | The rendered Jinja template is a valid bash script. `mzt validate` says "YAML syntax valid / Schema validation passed". |
| **Why current validation can't catch it** | V001 (`JinjaSyntaxCheck`) parses the template with `jinja2.Environment().parse()`. Jinja's lexer happily consumes `${#SRC_FONTS[@]}` as the start of a `{# ... #}` comment, scans ahead to the next `#}` (often a divider line above the next stage), and treats the eaten span as a valid comment. The parser returns success — no syntax error to report. The agent then receives a truncated bash script and fails at the shell level. The memory entry `feedback_jinja_bash_array_length_landmine.md` documents the full failure signature: `Exit code 2`, no stderr, sub-5 ms duration. |
| **Suggested capability** | A new check (proposed V002 / `BashJinjaCollisionCheck`) that scans the raw template **before** Jinja parsing for the literal sequence `${#`. ERROR severity with a clear fix suggestion (rewrite as `$(printf '%s\n' "${arr[@]}" \| wc -l)` or wrap the bash body in `{% raw %}...{% endraw %}`). **Implementation note from an aborted attempt**: do NOT also flag `${{` or `${%` — those are false-positive prone (`${{ amount }}` is the canonical way to render a literal `$` in front of a Jinja expression, common for currency in invoice/finance scores). Verified against `examples/product/invoice-analysis.yaml` — 6 false positives if the check naively flags all `${...` followed by Jinja markers. Scope V002 strictly to `${#`. |
| **Status** | open — mitigated by composer awareness + existing memory entry. Two production sheets have been bitten; both took >30 minutes to root-cause due to the silent-truncation failure mode. |
| **Reviewer concurrence** | Single-composer-found; corroborated by Legion memory entry from prior session. |

---

## 2026-05-12 — emzihypno-site v1a stage 28 markdown-in-cli-template

Different incident, same date as Gap 13. Different failure class. Production score
`scores-internal/build-emzihypno-site-v1a.yaml` stage 28 (mid-build integration
gate, `cli` instrument). The path-discipline preamble fix earlier this session
moved the SHARED markdown out of `sheet.prelude:`. That solved the per-prelude leak.
But stage 28's **per-stage template body itself** is structured-AI-prompt
markdown — section headers, prose, triple-backtick code fences, em dashes —
authored for AI consumption but routed to `cli` instrument late in the lifecycle
(round-14 mid-build gate addition). When `cli` executes, bash receives the markdown
as input and fast-fails. Fallback chain advances through gemini-cli / codex-cli /
opencode until an LLM agent reinterprets the prose as instructions and "completes"
the work in ~130s. The `command_succeeds` validation then runs and passes because
it independently executes the pnpm chain — but the cli sheet itself never ran the
bash. Architectural fakery; correct outcomes.

### Gap 14 — Markdown-style `prompt.template` body in a `cli`-instrument stage

| | |
|---|---|
| **Score & stage** | `scores-internal/build-emzihypno-site-v1a.yaml` stage 28 (S26 mid-build integration gate). Discovered 2026-05-12 during the v1a build run. Pattern likely present anywhere a CLI stage was authored using the same AI-prompt template style as nearby AI stages. |
| **The claim** | A `cli`-instrument stage's `prompt.template` body renders to valid bash that the cli instrument can `bash -c` execute directly. `mzt validate` says "YAML syntax valid / Schema validation passed / Jinja syntax valid". |
| **Why current validation can't catch it** | V001 (`JinjaSyntaxCheck`) validates Jinja syntax, not bash-validity of the rendered output. The template renders cleanly to a string; the string just happens to be markdown (with `## PURPOSE`, English prose, ` ```bash ` fences, em dashes) instead of bash. Bash parses the rendered string at startup, fails on the first construct that looks like nested command substitution or non-ASCII typography, exits with status 2 in ~1.5 ms with no stdout/stderr. Conductor logs the failure as `success: false, pass_rate: 0.0, duration: 0.001-0.002` then advances the fallback chain. If a downstream LLM-based fallback is available (`gemini-cli`, `codex-cli`, `opencode`), it interprets the markdown prose as natural-language instructions and runs the work some other way — silently bypassing the CLI sheet semantics. Validation passes because the `command_succeeds` block was independently executable. Score "completes successfully" while concealing that the deterministic CLI sheet never actually ran. **This is the second-class bash-Jinja problem**: Gap 13 truncates the bash; Gap 14 replaces it entirely. |
| **Failure signature** | Conductor log shows multiple `cli` attempts with `duration: 0.001-0.005` and `success: false` (the bash-parse-fail signature). Fallback chain advances. Eventually an LLM agent succeeds in 60-150s. The marker file and validation outputs are produced by the LLM, not the CLI. |
| **Suggested capability** | A new check (proposed V003 / `CliTemplateBashCleanCheck`) that, for any stage whose resolved instrument has `raw_prompt: true` AND whose movement instrument is `cli` (or any instrument that bash-execs), renders the Jinja template for that stage with realistic variable bindings and then pipes the output through `bash -n` (syntax check). If `bash -n` exits non-zero, fail validation with the bash error message. **Pre-implementation discipline**: per the false-positive-sweep section at the bottom of this addendum, run the new check against every score in the repo before merging — verify that no existing CLI sheet that legitimately uses non-trivial bash constructs (e.g., heredocs, escaped quotes) gets a false positive. The check must distinguish "the bash is wrong" from "the template body has structured-AI-prompt content". A simpler proxy: scan the rendered template for: any `##` line, any line starting with markdown bullet `- ` at column 0, any ` ``` ` triple-backtick, any em dash `—` / curly quote / non-ASCII punctuation. ERROR if found in a stage marked for a `raw_prompt: true` cli instrument. |
| **Status** | open — mitigated by per-author awareness + memory entry. Documented in `~/Projects/emzihypno-concert/priming/cli-sheet-templates-must-be-bash.yaml` as the 10th primer for the emzihypno.com concert. v1a stage 28 was patched in-place 2026-05-12 (markdown body → clean bash announce + marker write). The validator-level check is still wanted for future authoring discipline. |
| **Reviewer concurrence** | Single-composer-found; corroborated by conductor-log evidence (sheet 28: cli attempts 1.6ms each → gemini-cli rate-limited → codex-cli failed → opencode succeeded in 129s — the canonical LLM-fakery cascade). |

### Defense-in-depth at the engine level (a second possible answer)

Independent of any new validate check: Marianne could refuse to fall back from a
`cli` instrument to an LLM-based instrument unless the score explicitly opts in.
The current fallback chain advances `cli → gemini-cli → codex-cli → opencode`
when `instrument_fallbacks` resolves to LLM instruments. That semantic mismatch is
the root cause of LLM-fakery: deterministic intent (cli) silently becomes
interpretive (LLM). A `cli_fallback_policy: strict` option that fails the sheet
rather than advancing to an LLM-based fallback would make the failure loud
instead of papered-over. Tracked separately from V003; both fixes are
complementary.

---

## 2026-06-21 — generic fleet compiler lifecycle drift

Compiler-generated generic fleet scores validated cleanly as YAML and schema, but
the generated lifecycle shape was wrong: an intended 12-sheet cycle could expand
into a larger runtime shape when the compiler mixed explicit sheets with
`fan_out`-style semantics. The score was structurally valid, so `mzt validate`
could not know the compiler's intended invariant had been broken.

### Gap 15 — Generated-score family invariants

| | |
|---|---|
| **Score & stage** | `mzt compile --preset generic-fleet`, generated agent scores such as `canyon.yaml` and `forge.yaml`. |
| **The claim** | The compiler emits exactly the generic fleet lifecycle it promises: 12 concrete sheets, dependency-parallel phases, technique manifests attached to the same phase names/sheet numbers the baton resolves at runtime, and no stale lifecycle keys. |
| **Why current validation can't catch it** | `mzt validate` checks whether an individual score is well-formed. It does not know that a score came from the compiler, which preset generated it, or which family-level invariants should hold after expansion. A YAML-valid score can therefore pass while violating the compiler contract: wrong sheet count, stale `skip_when_command`, Jinja-style `{{workspace}}` in command fields that require `{workspace}`, sidecar files outside the score schema, or phase names that never match runtime sheet movement. |
| **Suggested capability** | A compiler self-check or `generated_family_invariant` validator that records generation metadata (`compiler_preset`, expected sheet count, expected phase map, allowed top-level keys) and verifies the emitted score against it. For presets, `mzt compile --preset ... --check` should validate every generated score before writing or before reporting success. |
| **Status** | open at validator level; mitigated in compiler tests on 2026-06-21 by asserting the 12-sheet dependency shape, live `skip_when`, runtime technique phase expansion, and clean validation sweeps for every generated generic-fleet agent score. |
| **Reviewer concurrence** | Single-composer-found during generic fleet compiler hardening. |

---

## 2026-06-21 — fan-out instrument assignment drift

The `generic-fleet-technique-research` score validated cleanly, but its
`per_sheet_instruments` map used logical stage numbers after declaring
`fan_out: {1: 8}`. Marianne expands fan-out at parse time, so those keys target
concrete sheet numbers. The intended assignment was "all 8 recon tracks use
Gemini Flash, later synthesis/audit sheets use GLM 5.2 via Claude Code"; the
valid score actually sent only concrete sheet 1 to Gemini, sheets 2-5 to GLM,
and left sheets 6-12 on the score default.

### Gap 16 — Fan-out-aware instrument intent checks

| | |
|---|---|
| **Score & stage** | `scores/generic-fleet-technique-research.yaml`, fan-out recon stage and downstream synthesis stages. |
| **The claim** | Instrument assignment expresses semantic stages: all recon fan-out instances on one model family, synthesis/audit/final on another. |
| **Why current validation can't catch it** | `mzt validate` expands `fan_out` and accepts the resulting concrete sheet map, but it has no way to know the author's semantic intent. A five-entry `per_sheet_instruments` map is legal even when the expanded score has 12 concrete sheets and the descriptions/prompts imply a different model-family split. |
| **Suggested capability** | A `fan_out_assignment_coverage` warning that detects `fan_out` plus partial `per_sheet_instruments`/`per_sheet_fallbacks` coverage when descriptions or dependencies imply stage-level assignment. Longer term, support a stage-level `instrument_map_by_stage` or document movement-level instruments as the preferred semantic-stage API. |
| **Status** | open at validator level; mitigated in the score on 2026-06-21 by using `instrument_map` for concrete recon sheets 1-8, complete per-sheet fallback coverage 1-12, and runtime descriptions keyed to the expanded sheet numbers. |
| **Reviewer concurrence** | Single-composer-found during live research score monitoring; confirmed by `JobConfig.from_yaml` showing 12 expanded sheets and the stale five-entry map. |

### Gap 17 — Runtime integration proof for technique claims

| | |
|---|---|
| **Score & stage** | Generic fleet technique stack, especially A2A and shared MCP technique claims. |
| **The claim** | Passing model/config/router tests proves a technique is live in the conductor runtime. |
| **Why current validation can't catch it** | `mzt validate` and focused unit tests can prove schema, manifest generation, output classification, and isolated helper behavior without proving the daemon manager starts supporting services, dispatch passes runtime config, or the baton run loop moves data between jobs. This allowed A2A and shared MCP to be described as complete when A2A had no adapter routing and the shared MCP pool had no stdio-to-socket bridge or dispatch integration. |
| **Suggested capability** | Add a technique runtime proof suite keyed by feature: A2A requires a baton-adapter run-loop smoke that emits `@delegate` and observes a target inbox injection; shared MCP requires a live end-to-end score or daemon smoke that starts the pool, exposes a real endpoint, passes direct MCP config to an MCP-native instrument (`--mcp-config` or a verified workspace config path), and successfully invokes a tool where the instrument exposes a deterministic path to do so. Documentation should distinguish helper/proxy tests from conductor integration tests. |
| **Status** | open at validator level; runtime gaps closed for the named paths on 2026-06-21. A2A now has a two-agent baton run-loop smoke in `tests/test_a2a_wiring.py`. Shared MCP now has live socket multiplexing coverage in `tests/test_mcp_pool_manager.py` and baton dispatch config-injection coverage in `tests/test_mcp_conductor_dispatch.py`. |
| **Reviewer concurrence** | Composer challenged the overclaim on 2026-06-21; confirmed by code search showing adapter/manager did not register A2A cards, route A2A requests, start `McpPoolManager`, or call `set_mcp_config()`. Follow-up implementation added those runtime paths plus proof tests. |

### Gap 18 — Report-to-artifact factual consistency

| | |
|---|---|
| **Score & stage** | `/home/emzi/Projects/hoard-mill/scores/post-mill.yaml`, stage 9 composer-facing report after a Buffer rate-limit deferral. |
| **The claim** | The report summarizes the stage artifacts truthfully, including counts such as scheduled posts, failed posts, archived clips, and staged clips left untouched. |
| **Why current validation can't catch it** | Existing validations can require that a report file exists and contains section headers or a completion marker. They cannot assert that numbers in prose match source artifacts or live filesystem state. In the incident, the JSON artifacts correctly said `posted_count=0`, `moved_count=0`, and the filesystem had 165 staging directories untouched, but the LLM report stated "17 clips remain in staging" after confusing planned entries with all staged clips. Content checks passed because the marker and headings were present. |
| **Suggested capability** | A report consistency validator that compares declared prose/table counts to source artifacts, or a structured-report pattern where the report stage first writes machine-readable `report-facts.json` and validations check exact fields before prose is generated. For operational reports, prefer deterministic CLI rendering from artifacts when feasible. |
| **Status** | open at validator level; mitigated in hoard-mill on 2026-06-21 by replacing the LLM report sheet with a deterministic `cli` report generator that reads JSON artifacts and the real staging directory listing. |
| **Reviewer concurrence** | Single-live-run incident found during post-mill Buffer 24h rate-limit testing. |

### Gap 19 — Dashboard read-only IPC responsiveness

| | |
|---|---|
| **Score & stage** | Marianne dashboard live conductor surfaces: `/health`, `/api/jobs/daemon/status`, daemon-backed job lists/details, dashboard system partials, system APIs, and job action endpoints. |
| **The claim** | Read-only dashboard probes and lifecycle controls remain usable when the conductor is running, unavailable, or partially stale. |
| **Why current validation can't catch it** | Unit tests with mocked daemon clients return immediately, and page rendering tests usually use fixture state backends. They do not prove that dashboard routes bound live daemon IPC calls. During live dashboard smoke on 2026-06-21, `mzt status`, `/api/jobs`, `/api/dashboard/system/partial`, `/health`, and action endpoints could hang behind stale daemon IPC retry paths even though the UI page itself was reachable. |
| **Suggested capability** | A dashboard live-staleness smoke that runs against a fake or controlled slow daemon client and asserts every read-only/status/action route returns a degraded, 404, 409, or 503 response inside a short UI budget. Include route tests for timeout wrappers and a browser test that page loads do not wait indefinitely on header/system probes. |
| **Status** | open at validator level; mitigated in dashboard code on 2026-06-21 by bounding health/status/job-control/state-adapter/system-view IPC calls, falling back to roster-derived job summaries, and adding timeout regression tests plus live dashboard smoke evidence. |
| **Reviewer concurrence** | Found during live dashboard smoke after automated dashboard tests were green; direct `mzt status` also hung, confirming stale conductor IPC rather than a template-only failure. |

### Gap 20 — Dashboard log-download affordance truthfulness

| | |
|---|---|
| **Score & stage** | Dashboard job log viewer for completed/running jobs with large combined log sources. |
| **The claim** | The “Download” control in the log viewer offers a usable full-log download. |
| **Why current validation can't catch it** | Route tests covered small static log downloads and log metadata, but browser/UI tests did not combine them with large real log sources. In the live `post-mill` case, combined log sources were about 53 MB, above the backend's 50 MB static-download cap, so the toolbar offered “Download” but the route returned a 413 JSON error telling the user to stream instead. |
| **Suggested capability** | A UI contract test that mocks or seeds oversized log metadata and verifies the viewer disables/relabels static download affordances, while the streaming endpoint still works. The log metadata endpoint should advertise `download_available` and `download_limit_bytes` so UI and backend limits cannot drift silently. |
| **Status** | open at validator level; mitigated in dashboard code on 2026-06-21 by exposing download availability/limit in `/logs/info`, relabeling oversized logs as “Stream only,” and adding API and Playwright regressions. |
| **Reviewer concurrence** | Found during live `post-mill` dashboard smoke after static download returned `413 Request Entity Too Large`; verified with real post-mill log streaming and desktop/mobile screenshots. |

### Gap 21 — Terminal job/checkpoint sheet-status drift

| | |
|---|---|
| **Score & stage** | Daemon status/diagnose/dashboard detail views for cancelled jobs, observed on historical `verify-mill`. |
| **The claim** | A terminal cancelled job does not display any stale active sheet as currently playing/running. |
| **Why current validation can't catch it** | Existing cancel tests verified daemon metadata and task cancellation, but did not simulate a persisted checkpoint written before the registry's terminal `cancelled` status. After live `verify-mill` was cancelled, `mzt status verify-mill` correctly showed job status `CANCELLED` while sheet 1 still rendered as `playing` because the checkpoint still had `dispatched`. There was no live process or sleeper, but the status surface implied active work. |
| **Suggested capability** | A daemon status contract test that loads terminal registry status plus stale active checkpoint sheet states and asserts every status consumer receives a normalized non-active sheet status. Dashboard and diagnose tests should include the same fixture so terminal jobs cannot show "Now Playing" from stale checkpoint data. |
| **Status** | open at validator level; mitigated in daemon code on 2026-06-21 by normalizing active-ish sheet statuses to `cancelled` when the authoritative job status is `cancelled`, preserving completed/failed/skipped and untouched pending sheets. Added `tests/test_daemon_manager.py::TestCancelledCheckpointStatusNormalization` and verified live `mzt status verify-mill` JSON/rich output after conductor restart. |
| **Reviewer concurrence** | Found during verify-mill operational re-check after process checks showed no active `verify-mill` or `sleep 300` process; confirmed as observability drift rather than runtime activity. |

## 2026-06-22 — fleet execution truthfulness

### Gap 22 — Fleet configs validate but `mzt run` parsed them as scores

| | |
|---|---|
| **Score & stage** | Generated compiler fleet configs such as `mzt compile --preset generic-fleet` output `fleet.yaml`. |
| **The claim** | Fleet configs are runnable through the documented `mzt run fleet.yaml` path. |
| **Why current validation can't catch it** | `mzt validate fleet.yaml` correctly identified fleet configs as non-score artifacts, but `mzt run --dry-run fleet.yaml` and live `mzt run fleet.yaml` parsed the file as `JobConfig` before reaching daemon fleet detection. Schema tests for `FleetConfig` and daemon tests for `submit_fleet()` did not exercise the CLI entrypoint. |
| **Suggested capability** | CLI journey tests for fleet dry-run, JSON dry-run, and live daemon submission should be part of the fleet contract. Validation should cross-reference the actual run path when it tells users a file is a fleet config. |
| **Status** | open at validator level; mitigated in CLI code on 2026-06-22 by routing fleet configs before score parsing, adding fleet dry-run output, and rejecting unsupported score-specific options honestly. Added `tests/test_cli_run_resume.py` fleet coverage and live-smoked `mzt run --dry-run` for a generated 32-score fleet. |
| **Reviewer concurrence** | Found while preparing a live generic fleet acceptance drill after `mzt validate fleet.yaml` passed but `mzt run --dry-run fleet.yaml` failed with missing `sheet`/`prompt` JobConfig errors. |

### Gap 23 — Fleet `depends_on` ordered submission, not dependency completion

| | |
|---|---|
| **Score & stage** | Fleet manager group dependency execution, especially generated generic fleet groups where implementation and verification groups depend on leadership/architecture/security groups. |
| **The claim** | A dependent fleet group does not begin until its declared dependency group has completed a successful cycle. |
| **Why current validation can't catch it** | Existing fleet tests verified topological sorting and that `submit_fleet()` submitted member scores, but not that dependent groups waited for dependency jobs to reach terminal success. The old implementation submitted each layer immediately after the previous layer was accepted, so a dependent score could run before dependency artifacts existed. |
| **Suggested capability** | A live or simulated fleet dependency drill where the dependent score fails unless the root score has completed and written a marker. The test should assert dependent submission happens after the dependency job status becomes `completed` or `paused_at_chain`, not merely after acceptance. |
| **Status** | open at validator level; mitigated in daemon fleet code on 2026-06-22 by submitting the initial dependency-free layer immediately and launching later layers from a background coordinator after dependency groups reach successful terminal status. Added `tests/test_fleet_config.py::TestFleetSubmission::test_submit_fleet_waits_for_dependency_group` and live-smoked a two-score CLI fleet whose dependent score checked for the root marker before writing its own marker. |
| **Reviewer concurrence** | Found by designing the live fleet drill to catch false dependency claims instead of launching independent scores only. |

### Gap 24 — Built-in fleet live launches needed a bounded first-cycle compile path

| | |
|---|---|
| **Score & stage** | `mzt compile --preset generic-fleet` followed by `mzt run <generated>/fleet.yaml` for a real target project. |
| **The claim** | A composer can safely point the built-in generic fleet at a project for a first live proof without accidentally starting unbounded self-chain cycles. |
| **Why current validation can't catch it** | Static score validation accepts both `pause_before_chain: false` and `pause_before_chain: true`. The bug is operational: the shipped preset intentionally self-chains, but the CLI had no direct way to request a one-cycle, pause-at-chain launch for a target project. |
| **Suggested capability** | Compile/runtime smoke that asserts built-in preset output can be generated with `pause_before_chain: true` across every emitted score, and that the generated hooks enter `paused_at_chain` instead of immediately launching the next cycle. |
| **Status** | open at validator level; mitigated in CLI/compiler code on 2026-06-22 with `mzt compile --pause-before-chain`, targeted CLI tests, and artifact validation showing 32/32 BC9K generated agent hooks were bounded. |
| **Reviewer concurrence** | Found while preparing to launch the generic fleet at Backyard Capitalism 9000. Starting the default preset would have been too broad for a first proof because every generated score carried an immediate self-chain hook. |

### Gap 25 — Generic fleet score filenames/job IDs were not project-repeatable

| | |
|---|---|
| **Score & stage** | Generated generic fleet score files (`north.yaml`, `forge.yaml`, etc.) submitted through conductor fleet runtime. |
| **The claim** | The same generic fleet can be compiled and run for multiple target projects without conductor job identity collisions. |
| **Why current validation can't catch it** | `mzt validate` checks individual score validity and fleet shape, but conductor job IDs are derived from score file stems. Multiple project fleets with `north.yaml`/`forge.yaml` collide at runtime even if their workspaces and fleet files differ. |
| **Suggested capability** | A compile/run contract test that generates two project-scoped fleets with distinct job prefixes, dry-runs both, and asserts emitted score filenames, top-level score names, self-chain hooks, fleet name, and fleet entries are prefix-scoped while prompt `agent_name` and identity directories remain unprefixed. |
| **Status** | open at validator level; mitigated in compiler code on 2026-06-22 with `defaults.job_name_prefix` and `mzt compile --job-prefix`. Targeted tests prove filenames/job IDs and fleet names are prefixed while agent identity remains stable, and the BC9K fleet was regenerated as `bc9k-*.yaml`. |
| **Reviewer concurrence** | Found before BC9K launch by tracing `JobManager.submit_job()` and `fleet._submit_score_batch()`: fleet members are submitted by score path, but the conductor job ID comes from `Path.stem`. |

### Gap 26 — Validation conditions failed open when context variables were absent

| | |
|---|---|
| **Score & stage** | Generated generic fleet scores whose validations use conditions like `stage == 1` through `stage == 12`. |
| **The claim** | A sheet runs only the validations scoped to its current sheet/movement. |
| **Why current validation can't catch it** | Static score validation checks that validation rules are syntactically valid, but did not execute the live validation engine with a missing or malformed condition context. The engine treated unknown comparison variables as true, so a context missing integer `stage` could activate every stage-scoped validation. |
| **Suggested capability** | Engine and runtime journey tests that feed generated fleet validation lists through the exact baton validation context and assert sheet 1 checks only recon rules, sheet 2 checks only plan rules, and missing comparison variables fail closed. |
| **Status** | open at validator level; mitigated in `src/marianne/execution/validation/engine.py` on 2026-06-22 by making missing/non-numeric comparison variables false while preserving malformed-condition-as-unconditional behavior. Added focused validation-engine regressions and direct generated-score checks. |
| **Reviewer concurrence** | Found during BC9K proof 1: sheet 1 agents were forced to produce whole-cycle artifacts and one agent planned all 13 files before completion. The proof was cancelled and the conductor restarted after the fix. |

### Gap 27 — Prompt validation checklist ignored `stage == N` scope

| | |
|---|---|
| **Score & stage** | Prompt assembly for generated generic fleet scores, especially interactive fallback sheets using the validation checklist at the end of the prompt. |
| **The claim** | The prompt tells the agent only the validation requirements that will actually be checked for the current sheet. |
| **Why current validation can't catch it** | The execution engine and prompt builder had separate condition evaluators. Prompt formatting only understood simple `sheet_num` conditions; generated `stage == N` rules were treated as "new on sheet 1" and shown in the prompt even after execution filtering was fixed. Static score validation cannot see rendered prompt content. |
| **Suggested capability** | Prompt-render contract tests using a real compiled generic-fleet score: render sheets 1, 2, and 3, then assert the success-requirements section contains only recon, plan, and work rules respectively, with all path placeholders expanded. |
| **Status** | open at validator level; mitigated in `src/marianne/prompts/templating.py` on 2026-06-22 by applying condition filtering before formatting prompt requirements and expanding command validation placeholders. Added prompt-builder regressions and rendered a generated BC9K fleet prompt to verify sheet 1 contains only recon requirements. |
| **Reviewer concurrence** | Found during BC9K proof 2: Tempo passed sheet 1 from recon only, proving execution filtering worked, but other agents still wrote later-phase artifacts because prompt text exposed whole-cycle requirements. Proof 2 was cancelled before relaunching. |

### Gap 28 — Generated phase-output wording drifted from section validations

| | |
|---|---|
| **Score & stage** | Generated generic fleet prompt templates for artifact-producing sheets, observed live on BC9K proof 3 sheet 1. |
| **The claim** | The prompt's required-output instructions teach the exact artifact section labels that `content_contains` validations require. |
| **Why current validation can't catch it** | Static score validation checks that prompt templates and validation rules exist, but did not compare their contracts. The compiler prompt said to include sections like `OBSERVED, CHANGED, ...` while validations required colon-bearing substrings like `OBSERVED:`. Several agents wrote markdown headings without colons, created completion markers, and then entered completion mode because the artifact existed but five section checks failed. |
| **Suggested capability** | Compiler contract tests should render every phase branch and compare it against `ValidationGenerator`'s `content_contains` patterns for that stage. Live generated-fleet smokes should inspect validation pass rates, not only completion markers. |
| **Status** | open at validator level; mitigated in compiler prompt generation on 2026-06-22 by emitting exact colon-bearing section labels and adding a compiler regression that compares phase prompt branches against validation patterns. |
| **Reviewer concurrence** | Found during BC9K proof 3: `bc9k3-captain` produced `cycle-state/captain-recon.md` and `s1-a1.complete`, but the conductor reported only 28.57% validation pass rate because `OBSERVED:`, `CHANGED:`, `CANDIDATES:`, `RISKS:`, and `EVIDENCE:` were absent. |

### Gap 29 — Dispatch capacity waits were visible only in logs

| | |
|---|---|
| **Score & stage** | Live multi-agent generic fleet execution, observed on BC9K proof 3 when several ready sheets were held behind the `claude-code:glm-5-Turbo` model concurrency limit. |
| **The claim** | A running score's status/diagnose surfaces explain why work is not dispatching, without requiring log spelunking. |
| **Why current validation can't catch it** | Unit tests covered the dispatch skip counters and conductor logs contained `dispatch.skip.model_concurrency`, but persisted sheet state, `mzt status --json`, and `mzt diagnose` had no per-sheet wait reason. Operators saw pending/running jobs with no distinction between dependency wait, model-capacity wait, rate-limit wait, circuit-breaker wait, or an actual stall. |
| **Suggested capability** | Runtime scheduler observability should persist stable dispatch wait categories and structured details on ready sheets, clear them on dispatch/reset, and expose them through CLI/dashboard status and diagnose surfaces. Live fleet smokes should inspect status output while jobs are capacity-limited, not only logs. |
| **Status** | open at dashboard/live-restart level; mitigated in code on 2026-06-22 by adding per-sheet `dispatch_blocked_*` checkpoint fields, recording scheduler skip reasons in baton dispatch, persisting changed blocked sheets, and exposing the data through `mzt status --json` and `mzt diagnose`. A running conductor must be restarted after active jobs drain before live status can prove the new fields in production. |
| **Reviewer concurrence** | Found during BC9K proof 3: conductor logs repeatedly reported `dispatch.skip.model_concurrency` with `model_count: 4` and `model_limit: 4`, while `mzt status bc9k3-bedrock --json` only showed plain pending sheets and no capacity explanation. |

### Gap 30 — Workspace path scope was not a first-class validation

| | |
|---|---|
| **Score & stage** | Generic fleet and shared-MCP/file-producing scores where agents or generated validations reference paths under `{workspace}`. |
| **The claim** | A score can assert that a path resolves inside the intended workspace or narrower allowed root after canonical resolution, including symlink and `..` handling. |
| **Why current validation can't catch it** | Existing file validations check existence, content, modification, or command success. They intentionally allow absolute paths outside the workspace for project-root workflows, so they cannot express "this particular path must stay inside the workspace." Hook/path fixes caught unresolved braces and missing targets, but not a reusable validation rule for traversal or symlink escape. |
| **Suggested capability** | Add a first-class `path_in_scope` validation rule with optional `path_scope`, resolve paths canonically before comparison, deny paths outside scope, and add a static `mzt validate` preflight for resolvable rules. Runtime should still enforce templated or late-created paths that preflight cannot resolve. |
| **Status** | mitigated in code on 2026-06-22 by adding `ValidationRule.type: path_in_scope`, optional `path_scope`, execution-time canonical scope checks in `ValidationEngine`, and V306 static preflight checks for resolvable paths. Focused tests cover in-root paths, traversal, symlink escape, custom scopes, and default-check registration. A real CLI smoke confirmed an in-workspace rule passes `mzt validate` and `{workspace}/../escape.txt` fails with V306. False-positive sweep: 187 YAML files under `scores/`, `scores-internal/`, and `examples/` produced 0 V306 hits; 62 unrelated pre-existing non-V306 validation failures remain. |
| **Reviewer concurrence** | Derived from the generic fleet technique research A10 `workspace-path-validator`, then implemented after the compiler/runtime path failures exposed by canyon/forge and BC9K proof work. |

### Gap 31 — Learned-pattern feedback stopped at prompt injection

| | |
|---|---|
| **Score & stage** | Runtime learning paths that inject global `PatternRecord` guidance into rendered sheet prompts and later aggregate `SheetOutcome` data. |
| **The claim** | When Marianne injects a learned pattern into a sheet prompt, the eventual sheet outcome can update that exact pattern's application/effectiveness counters. |
| **Why current validation can't catch it** | Prompt-render tests could prove pattern text appeared in the prompt, and checkpoint tests could prove pattern fields existed, but neither followed the ID through baton state, outcome serialization, and `GlobalLearningStore.record_pattern_application`. `PatternAggregator._record_pattern_applications` was still a stub, so successful prompt injection never closed the global feedback loop. |
| **Suggested capability** | Runtime learning tests should assert stable pattern IDs survive injection, are persisted on sheet state, are copied to `SheetOutcome.applied_pattern_ids`, and create rows in `pattern_applications` with success/failure counters updated. Tests should also verify no-ID legacy outcomes are skipped rather than reverse-matched by text. |
| **Status** | mitigated in code on 2026-06-22 by pairing learned prompt strings with `PatternRecord.id` in the baton, recording structured `applied_patterns` on the live sheet state, adding `SheetOutcome.applied_pattern_ids`, preserving the field in JSON outcome stores and legacy migration, and implementing `PatternAggregator._record_pattern_applications`. Focused tests cover ID/text pairing, stale-state clearing, no-ID legacy skip, store application writes, and priority/effectiveness counter updates. |
| **Reviewer concurrence** | Found while transforming the generic fleet technique research A1/A2 memory/feedback designs into Marianne-native runtime behavior. The prior code had ranking, trust filtering, and prompt injection, but the application/effectiveness loop was not actually connected. |

### Gap 32 — Generated fleet manifests could hard-code absolute score paths

| | |
|---|---|
| **Score & stage** | Compiler-generated `fleet.yaml` manifests for multi-agent presets, including the shipped `generic-fleet` preset. |
| **The claim** | A compiled fleet can be moved with its workspace/scores directory and still launch the same member scores. |
| **Why current validation can't catch it** | `mzt validate` accepted absolute fleet member paths because they are syntactically valid and runnable on the generating machine. It did not distinguish portable relative paths from hard-coded local paths. This escaped even after individual score self-chain hooks were made `{workspace}`-portable. |
| **Suggested capability** | Compiler contract tests should assert generated `fleet.yaml` entries are relative to the fleet config location. A future validator could warn when a compiler-generated fleet manifest contains absolute member paths unless explicitly opted in. |
| **Status** | mitigated in code on 2026-06-22 by making `FleetGenerator` emit score paths relative to the fleet config directory. Focused tests cover direct generator output, `write()`, the generic preset contract, and the concrete proof4 compile. Proof4 generated 32 fleet score paths, 0 absolute paths, 0 missing relative targets; `mzt run --dry-run --json` and `mzt validate` over 33 generated YAMLs both passed. |
| **Reviewer concurrence** | Surfaced by the live BC9K proof 3 Ghost agent while auditing compiler portability. The issue matched the composer's earlier warning against hard-coded absolute generated paths. |

### Gap 33 — Binary-ready instruments can be provider-blocked at execution time

| | |
|---|---|
| **Score & stage** | Generic fleet preset compilation and runtime dispatch for provider-backed CLI instruments. |
| **The claim** | An instrument included because `mzt doctor` found its binary can actually execute the generated sheet prompt. |
| **Why current validation can't catch it** | `mzt doctor` and the generic preset filter historically checked whether a CLI executable existed on `PATH`, not whether that executable's current authenticated provider tier could run a headless prompt. On 2026-06-22, `gemini-cli` 0.46.0 passed the binary check but every live proof4 sheet exited with `IneligibleTierError` / `UNSUPPORTED_CLIENT`, causing fallback churn before useful work began. A cheap `gemini mcp list` also returned 0 but did not prove prompt execution. |
| **Suggested capability** | Separate binary readiness from execution readiness. Provider-backed CLI profiles should support an explicit, bounded live smoke/cache path that can mark a profile as prompt-execution unavailable without consuming significant budget, and compiler environment filtering should prefer the execution-ready set when present. |
| **Status** | partially mitigated on 2026-06-22 for the shipped generic fleet by moving Gemini Flash tiers from obsolete `gemini-cli` to the installed/live-smoked `antigravity` profile and adding compiler contract tests that reject `gemini-cli` in the preset. A general doctor execution-readiness cache remains open. |
| **Reviewer concurrence** | Found during live BC9K proof4 dispatch; unit tests and binary doctor output were both green while the first real provider call failed before producing sheet work. |

### Gap 34 — Global cadenza row IDs collide under parallel agents

| | |
|---|---|
| **Score & stage** | Generic fleet cadenza coordination in `shared/active/01-task-board.md` and related active ledgers during parallel recon/plan sheets. |
| **The claim** | Agents can safely claim work and record findings/decisions/handoffs in shared active files during parallel execution. |
| **Why current validation can't catch it** | Structural cadenza tests proved the files were seeded and injected, but they did not run multiple live agents writing the same table concurrently. Proof5 produced duplicate `T-005` rows from Tempo and North because the shipped examples used global incrementing IDs. Both rows were syntactically valid, so ordinary content checks saw no failure. |
| **Suggested capability** | Active cadenza seed and technique docs should require owner-scoped IDs (`{agent}-T-001`, `{agent}-F-001`, `{agent}-D-001`, `{agent}-H-001`). A future cadenza validator could scan active tables for legacy global IDs and duplicate row IDs. |
| **Status** | partially mitigated on 2026-06-22 by updating shared seed files and coordination technique docs to use owner-scoped IDs, plus contract tests for seeded files and rendered prompt text. A static validator for legacy global IDs remains open. |
| **Reviewer concurrence** | Found during live BC9K proof5 after six Antigravity recon sheets wrote to the shared active cadenza concurrently. |

### Gap 35 — Validation-triggered retries can lose the failure evidence from status

| | |
|---|---|
| **Score & stage** | Baton runtime status/diagnose/dashboard detail views for generated fleet sheets that execute successfully but fail sheet validations and retry. |
| **The claim** | When a sheet retries because validations failed, operators can see which validation failed without waiting for terminal failure or grepping conductor logs. |
| **Why current validation can't catch it** | Unit tests covered retry scheduling and the musician's pass-rate summary, but live BC9K proof6 showed `mzt diagnose --include-logs` reporting only "playing, attempt 2" while conductor logs showed `pass_rate: 0.0`. The musician sent `validation_details`, but `SheetState.attempt_results` is transient and dispatch cleared current `validation_details` before the next attempt, so status/diagnose lost the explanation while the retry was active. |
| **Suggested capability** | Persist per-rule validation result summaries from every attempt onto `SheetState` (`validation_details`, `passed_validations`, `failed_validations`, `last_pass_percentage`) and expose a validation-specific section in `mzt diagnose`, `mzt status`, and dashboard sheet detail. Retry prompts may clear current-attempt fields, but durable prior failure summaries must remain visible until a later successful attempt replaces them. |
| **Status** | mitigated in code on 2026-06-22 by preserving per-rule validation results from the musician, populating durable sheet validation summary fields in `record_attempt()`, and surfacing validation failures in diagnose/status. Live conductor proof still requires restart or a fresh run because proof6 began on the old in-memory conductor code. |
| **Reviewer concurrence** | Found during live BC9K proof6: Captain, Tempo, Bedrock, North, and Atlas Antigravity sheets returned success with 0% validation pass rate, but `diagnose` did not show the missing-artifact validations. |

### Gap 36 — Shared active cadenza files are hot-write contention points

| | |
|---|---|
| **Score & stage** | Live generic fleet execution using `shared/active/01-task-board.md`, `02-agent-status.md`, and other active cadenza tables during parallel recon, plan, and work phases. |
| **The claim** | Owner-scoped row IDs are enough for parallel agents to coordinate safely through shared active files. |
| **Why current validation can't catch it** | Compiler tests prove the active files are seeded, injected, and use owner-scoped IDs, but they do not run multiple real interactive agents editing the same markdown table at once. BC9K proof6 showed repeated "file modified since read" failures even without duplicate IDs because the whole markdown file is still a shared write hotspot. |
| **Suggested capability** | Coordination technique docs and seeded active contracts must define a conflict protocol: re-read latest, retry the smallest owner-row change once, prefer append-only owner rows, and after a second conflict move detail into an agent-local or shared detail artifact with a blocked coordination note. Future validation could lint active cadenza files for missing conflict guidance and live fleet smokes should check for repeated write-conflict loops. |
| **Status** | partially mitigated on 2026-06-22 by adding concurrent-write safety guidance to packaged coordination techniques, seeded `shared/active` contracts, generated prompt required-output text, and compiler/generic-fleet contract tests. A true append-only cadenza ledger or file-locking helper remains open. |
| **Reviewer concurrence** | Found during live BC9K proof6: Captain, Tempo, Bedrock, Atlas, and Ghost transcripts repeatedly hit "File has been modified since read" while editing shared active files; several recovered, but the retries consumed provider time and delayed sheet completion. |

### Gap 37 — Interactive attempt sessions can outlive conductor attempt state

| | |
|---|---|
| **Score & stage** | Live generic fleet execution with interactive Claude Code sheets that retry or advance after an attempt reports completion/failure. |
| **The claim** | Once a sheet attempt finishes, retries, or the job is cancelled, its interactive tmux session and provider CLI process are gone. |
| **Why current validation can't catch it** | Unit tests asserted that the driver calls `kill_session()` and that cancellation kills tracked process groups, but BC9K proof6 showed stale tmux sessions for earlier attempts still running after the conductor had advanced and later after `mzt cancel` reported the jobs cancelled. The stale sessions had already cleared active PID tracking, so process-group cancellation alone had nothing left to signal. |
| **Suggested capability** | Interactive cleanup must verify session disappearance before untracking the attempt, and job deregistration must sweep deterministic `mzt-{job}-s*` tmux sessions as a second teardown path. Live fleet smokes should inspect `tmux -L marianne list-sessions` and the process table after retries/cancellations, not only `mzt status`. |
| **Status** | mitigated in code on 2026-06-22 by adding verified kill retries in `InteractiveSessionDriver._cleanup()`, a backend-level final teardown check before pane PID untracking, and a per-job tmux-session sweep in `BatonAdapter.deregister_job()`. Live cancellation proof removed a stuck Antigravity session through the adapter sweep; a fresh restarted-conductor proof is required to verify the backend final guard without explicit cancel. |
| **Reviewer concurrence** | Found during BC9K proof6: after cancelling six active fleet jobs, `mzt status` showed zero active jobs but four `mzt-bc9k6-*` tmux sessions and Claude processes survived until manually killed with `tmux -L marianne kill-session`. |

### Gap 38 — Interactive provider quota screens can masquerade as idle agent failures

| | |
|---|---|
| **Score & stage** | Interactive instrument execution for TUI-backed providers, especially Antigravity sheets in the generic fleet. |
| **The claim** | If a provider/account quota screen blocks the TUI, Marianne classifies the attempt as provider-limited and does not keep nudging, retrying, or treating missing artifacts as ordinary agent noncompliance. |
| **Why current validation can't catch it** | Headless CLI classifiers scan subprocess stderr/stdout, while interactive runs capture a rendered terminal screen. BC9K proof7 and a disposable Antigravity smoke showed `agy` reaching an interactive quota screen (`Individual quota reached`) and a feedback prompt while the tmux session stayed alive. The driver would eventually report timeout or nudge exhaustion, and downstream validations would only see missing artifacts. |
| **Suggested capability** | Instrument profiles need verified interactive final-screen failure patterns, separate from generic CLI output patterns, so vendor/account UI failures can be classified without scanning arbitrary agent prose for words like "rate limit." Live provider smokes should assert both the failure classification and the absence of orphaned tmux/provider processes after cancellation. |
| **Status** | partially mitigated in code on 2026-06-22 by adding interactive screen error patterns to instrument profiles and wiring Antigravity's live quota sample through `rate_limited=True`. The shared reset parser handles compact provider durations such as `3h19m33s`, so `rate_limit_wait_seconds` does not fall back to 60 seconds when the screen gives a longer reset. BC9K proof9 reopened the gap for Claude Code: a prompt-returning GLM gateway 429 (`Usage limit reached... Your limit will reset at ...`) was not caught before the idle path, so Marianne sent continuation nudges and left jobs `running`. The mitigation now moves explicit profile-owned provider-screen checks into the interactive driver before idle/nudge handling and adds Claude Code patterns for the observed 429 timestamp screen. A restarted-conductor live proof is still required after this change. A future non-quota Antigravity run is still required before declaring Antigravity operational for fleet work. |
| **Reviewer concurrence** | Found first during live Antigravity default-interactive smoke after changing the profile away from unreliable non-TTY print mode, then reproved during live BC9K proof9 with Claude Code/GLM. In both cases the provider was installed and launchable, but the account/provider surface was quota-blocked for this workload. |

### Gap 39 — Rate-limit mirrors can shorten provider cooldown truth

| | |
|---|---|
| **Score & stage** | Provider-backed instrument execution followed by daemon status/doctor/compiler availability filtering, observed with the disposable Antigravity interactive smoke and generic-fleet compile. |
| **The claim** | A provider reset duration parsed from the live error surface remains the same across Baton timers, daemon rate-limit mirrors, `mzt doctor`, and compiler provider filtering. |
| **Why current validation can't catch it** | Classifier tests and Baton timer tests can pass while a downstream observability mirror clamps or rewrites the same wait. On 2026-06-22, Antigravity's screen said `Resets in 3h8m9s` and Baton scheduled `11289s`, but `RateLimitCoordinator` capped the mirror to `3600s`, so `mzt doctor` reported only about one hour. A compiler using doctor would still exclude the provider during that hour, but the public availability surface was false for the remaining provider block. |
| **Suggested capability** | End-to-end provider-cooldown tests should assert equality within tolerance from parsed screen text through Baton `RateLimitHit`, daemon `rate_limits`, doctor JSON, and compiler availability filtering. Mirror components should share the same upper bound as the parser (`RESET_TIME_MAXIMUM_WAIT_SECONDS`) rather than keeping local caps. |
| **Status** | mitigated in code on 2026-06-22 by changing `RateLimitCoordinator.MAX_WAIT_SECONDS` to `RESET_TIME_MAXIMUM_WAIT_SECONDS` and adding a regression for provider reset windows above one hour. Restarted live smoke proved Baton and the coordinator both reported `11079s`, doctor showed Antigravity rate-limited for about `3h4m`, and a BC9K proof8 generic-fleet compile while that limit was active emitted 32 scores with zero Antigravity references. |
| **Reviewer concurrence** | Found during live Antigravity quota proof after the first classifier fix. This is why unit tests for parser behavior alone were not treated as sufficient. |

### Gap 40 — Disabled learning can still inject global prompt patterns

| | |
|---|---|
| **Score & stage** | Baton prompt rendering for any score that sets `learning.enabled: false` or `learning.use_global_patterns: false`, observed during disposable interactive quota live proofs. |
| **The claim** | Score-level learning switches control whether global learned patterns are injected into prompts. |
| **Why current validation can't catch it** | Existing prompt-renderer and learned-pattern tests proved that supplied pattern lists render and that store queries work, but they did not run through `BatonAdapter.register_job()` with a parsed `LearningConfig`. During a live disposable proof, a score with learning disabled still received a `## Learned Patterns` section because the adapter queried the global store whenever a store handle existed. This can contaminate interactive panes with stale provider-error strings and weakens the meaning of score-local learning opt-outs. |
| **Suggested capability** | Adapter-level tests and live smokes should assert both the positive path and the disabled path: disabled learning produces no store query, no `Learned Patterns` prompt section, and no `applied_patterns` on the sheet state. Provider-error smokes should remove or disable unrelated learned-pattern injection when testing screen classification. |
| **Status** | mitigated in code on 2026-06-23 by threading `LearningConfig` into Baton registration/recovery, gating `_build_learned_patterns()` on `enabled` and `use_global_patterns`, and adding regressions for both disabled switches. A restarted-conductor live proof with an ignored disposable profile showed no `Learned Patterns` section in the transcript, `prompt_length` dropped from 2747 to 1644, and provider-error classification still reported `outcome=provider_error`, `nudges=0`, and a 24h rate-limit mirror. |
| **Reviewer concurrence** | Found while proving Gap 38's prompt-returning quota-screen fix. The first disposable live proof accidentally echoed old learned provider-error text into the prompt, showing that score-local learning settings were not respected by the baton path. |

### Gap 41 — Completed sheets could leave shared cadenza rows stale

| | |
|---|---|
| **Score & stage** | Compiler-generated `generic-fleet` sheets that inject `shared/active` during recon, plan, work, integration, and inspect. |
| **The claim** | If a generated sheet completes, the shared active task board and agent status board reflect that completion or explicitly document a bounded coordination-write block. |
| **Why current validation can't catch it** | Artifact validations checked only that `cycle-state/{agent}-{phase}.md` existed, had enough words, and contained required section labels. BC9K10 proved sheet 2 could pass while `shared/active/01-task-board.md` still showed some plan rows as `claimed` and `shared/active/02-agent-status.md` still showed `state=claimed`, despite the plan artifact existing. The cadenza coordination text asked agents to update state, but no generated validation enforced the durable coordination surface. |
| **Suggested capability** | Generated scores with shared-active cadenza phases should include a command validation that parses `01-task-board.md` for an owner row marked `done` with evidence pointing at the required artifact, parses `02-agent-status.md` for the agent/phase row marked `complete`, and only permits missing terminal rows when the required artifact contains an explicit `COORDINATION UPDATE BLOCKED:` marker naming the blocked file and reason. |
| **Status** | mitigated in code on 2026-06-23 by adding opt-in `cadenza_completion_validation` to the generic fleet preset, prompt instructions for terminal task/status rows and the blocked marker, generated command validations for shared-active phases, compiler unit coverage, and a generated-score validation-engine regression that fails stale `claimed` rows and passes terminal rows or documented blocked conflicts. Proof11 compile generated 32 prefixed scores plus `fleet.yaml`, validated 33/33 YAMLs, dry-ran the fleet, and confirmed the new checks on recon/plan/work/integration/inspect. Live proof11 launched with doctor-ready Claude/Codex instruments and, by 2026-06-23T20:32Z, all six initial agents had passed recon and plan validation; current-phase cadenza validators passed for all completed plan sheets, and North's recon used the explicit blocked-marker fallback after shared-file write contention. After the later timestamp/seed/inspect-path fixes, patched `proof12-canyon` ran live through a bounded full cycle and reached `PAUSED_AT_CHAIN`; recon, plan, work, integration, and inspect cadenza validations passed, including `canyon-T-005 | done | ... | cycle-state/canyon-inspection.md`. The current `02-agent-status.md` is intentionally rolling current state, so old-phase status rows are not durable history. |
| **Reviewer concurrence** | Found during live BC9K10 monitoring while comparing conductor sheet completion against the shared cadenza files. The lesson is distinct from write contention: even when artifacts exist, completion is not proven unless the durable coordination surface is terminal or explicitly blocked. |

### Gap 42 — Status-board timestamps could label local time as UTC

| | |
|---|---|
| **Score & stage** | Compiler-generated `generic-fleet` sheets that update `shared/active/02-agent-status.md`, observed in BC9K10 and proof11 live cadenza files. |
| **The claim** | A status-board timestamp ending in `Z` is UTC, not local wall-clock time with a UTC suffix. |
| **Why current validation can't catch it** | Before this fix, generated prompts and cadenza seed files asked agents to update the status board but did not specify how to produce the `updated` value. In the Europe/Berlin runtime, proof11 agents wrote values such as `2026-06-23T22:35Z` while the actual UTC time was about `2026-06-23T20:35Z`. The string matched the expected shape, so artifact and cadenza validations could pass while the coordination clock was two hours in the future. |
| **Suggested capability** | Generated prompts, shared cadenza seed files, and coordination technique docs should require `date -u +%Y-%m-%dT%H:%MZ`. Generated cadenza completion validation should parse the matching agent-status row and reject timestamps that are more than a small skew window in the future. |
| **Status** | mitigated in code on 2026-06-23 by adding explicit UTC timestamp instructions to the generated phase prompt, shared active coordination seed, agent-status seed, and coordination technique, and by extending generated cadenza completion validation to reject future status timestamps. Focused verification passed: Ruff on touched compiler/test files, mypy on touched compiler modules, and `pytest compiler/tests/test_compose_validations.py tests/test_generic_fleet_preset_contract.py -q` (29 passed). Existing proof11 scores were compiled before this timestamp validator, so they prove the old failure mode rather than the new runtime rejection. Patched `proof12-canyon` then live-ran with UTC-minute status rows including `2026-06-23T21:03Z` for inspect and `2026-06-23T21:10Z` for resurrect while the local clock was UTC+2, and all generated validations passed. |
| **Reviewer concurrence** | Found while monitoring proof11 after Gap 41's cadenza validation was added. Disk time from `date -u` and live status-board rows disagreed by the local UTC+2 offset. |

### Gap 43 — Active cadenza format examples can reserve real owner IDs

| | |
|---|---|
| **Score & stage** | Packaged `generic-fleet` shared seed files and coordination technique examples, observed during BC9K proof11 Bedrock work/cadenza-hygiene pass. |
| **The claim** | Owner-scoped row IDs prevent live cadenza collisions. |
| **Why current validation can't catch it** | Gap 34 fixed global IDs by using owner-scoped examples, but the seed still shipped concrete plausible examples such as `north-D-001`, `canyon-T-001`, `sentinel-F-001`, `composer-DIR-001`, and `canyon-H-001`. These are syntactically owner-scoped, so the existing contract tests explicitly accepted them. Proof11 then produced a real `north-D-001` row while the seeded Decision Format section still contained the same ID, creating duplicate IDs inside one active file. |
| **Suggested capability** | Seeded active-file format examples should use non-reserving placeholders (`{agent}-T-001`, `{agent}-D-001`, `{source}-DIR-001`, etc.) plus explicit "replace placeholders before writing" text. Contract tests should assert that concrete sample IDs are absent from seeded active files and rendered coordination prompts. A future live cadenza linter should scan active tables for duplicate IDs across both live and format sections. |
| **Status** | mitigated in code on 2026-06-23 by replacing concrete examples in shared active seed files and the coordination technique with placeholder IDs and updating `tests/test_generic_fleet_preset_contract.py` to require placeholders while rejecting the old concrete sample IDs. Focused verification passed: `ruff check tests/test_generic_fleet_preset_contract.py` and `pytest tests/test_generic_fleet_preset_contract.py -q` (8 passed). Existing proof11 was compiled before this seed fix, so its active files still demonstrate the old collision. Patched proof12 seed files compiled with placeholder examples, and the live `proof12-canyon` run safely created real owner rows such as `canyon-T-001` through `canyon-T-008` beside the non-reserving `{agent}-T-001` format row. |
| **Reviewer concurrence** | Found while monitoring proof11: Bedrock's cadenza hygiene pass reported `north-D-001` twice in `shared/active/04-decision-log.md`, one real decision row and one seeded format example. |

### Gap 44 — Inspect cadenza validation used the phase slug instead of the artifact path

| | |
|---|---|
| **Score & stage** | Compiler-generated `generic-fleet` inspect sheets with `cadenza_completion_validation`, observed while manually replaying proof11 stage 7 validators. |
| **The claim** | A generated cadenza completion validator checks the same required artifact as the sheet's structural artifact validation. |
| **Why current validation can't catch it** | The generator passed the actual `inspection_path` to `_cadenza_completion_validation()`, but that helper reconstructed evidence as `cycle-state/{agent}-{phase}.md`. For `phase=inspect`, that becomes `cycle-state/{agent}-inspect.md`, while the required file and prompt use `cycle-state/{agent}-inspection.md`. Structural artifact tests still passed, and early cadenza tests covered plan/work paths where phase and artifact slug matched. |
| **Suggested capability** | Cadenza validators should derive `ARTIFACT_REL` from the actual artifact path argument. Unit tests should cover at least one phase where the phase slug and artifact filename differ, especially inspect/inspection. |
| **Status** | mitigated in code on 2026-06-23 by deriving the cadenza evidence path from `artifact_path.removeprefix("{workspace}/")` and adding a validation-generator regression that asserts inspect cadenza checks use `cycle-state/canyon-inspection.md` and not `cycle-state/canyon-inspect.md`. Focused verification passed: Ruff on the touched generator/test files, mypy on `compiler/src/marianne_compiler/validations.py`, and `pytest compiler/tests/test_compose_validations.py tests/test_generic_fleet_preset_contract.py -q` (29 passed). Existing proof11 was compiled before this fix, so its inspect cadenza checks still expect the old `*-inspect.md` evidence path. Patched `proof12-canyon` live-ran sheet 7 successfully with validation passing, `canyon-T-005` pointing at `cycle-state/canyon-inspection.md`, and no `cycle-state/canyon-inspect.md` artifact on disk. |
| **Reviewer concurrence** | Found during live proof11 monitoring: Tempo inspect passed via the documented blocked-marker fallback, but Captain/Atlas/North inspect validators reported missing task-board evidence for `cycle-state/{agent}-inspect.md` even though their artifacts were named `*-inspection.md`. |

### Gap 45 — Dashboard tests with explicit state backends could still bind the live conductor

| | |
|---|---|
| **Score & stage** | Dashboard app factory and Playwright/API regression suites, especially `create_app(state_backend=...)` callers. |
| **The claim** | Passing an explicit state backend creates an isolated dashboard app suitable for tests or embedding, and does not touch the live conductor unless the caller opts in. |
| **Why current validation can't catch it** | The app factory always called `_create_daemon_client()` before choosing the state backend. Tests using temp JSON backends therefore opened real daemon event-bus subscriptions whenever a conductor happened to be running. The shared Playwright server also consumed the same app-level rate-limit bucket as job-control assertions, producing suite-only 429s while single tests passed. Broader dashboard tests left live event-bus subscriptions in the conductor log, after which both `mzt status --json` and the embed bridge timed out even though the registry showed zero active jobs. |
| **Suggested capability** | App factory tests should assert that explicit-backend apps do not create a daemon client by default, that event/system globals are cleared for isolated apps, and that browser suites can disable unrelated rate limiting while dedicated rate-limit tests keep restrictive configs. SSE fallback tests should also assert an initial status frame so a silent stream cannot hide behind browser timeouts. |
| **Status** | mitigated in code on 2026-06-23 by adding `connect_daemon` to `create_app()` with the default `False` for explicit `state_backend` apps, adding `rate_limit_config` injection, clearing event/system globals when no daemon client is wired, keeping analytics derived from the provided backend, disabling rate limiting in the Playwright fixture, and emitting an initial `job_status` frame from the polling SSE path. Verification passed: Ruff on touched dashboard files, mypy on touched dashboard route files, dashboard/API suites at 258 passed and 207 passed, focused rate-limit/factory checks at 38 passed, and Playwright at 15 passed. The live conductor was recovered only after direct DB inspection proved zero active jobs; final `mzt status --json` and bridge JSON showed `active_count`/`total_jobs_active` 0. |
| **Reviewer concurrence** | Found during dashboard/runtime verification after a full suite failed with `Expected at least 1 SSE event, got []` and a job-start assertion got HTTP 429. The single tests passed, proving the failure was shared app/runtime state rather than endpoint semantics. |

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

---

## Implementing a gap — false-positive discipline (READ BEFORE WRITING CODE)

When an agent implements one of the gaps above as a new `mzt validate`
check (or as a new sheet validation type), the implementation is **not**
done when synthetic test cases pass. Synthetic tests prove the check
fires on the bug you have in mind. They do **not** prove the check stays
quiet on patterns the bug shares structure with but does not actually
break.

The default failure mode of an agent implementing a new check is:
write a regex narrow enough to feel right, run it against one or two
constructed examples, declare victory, and move on. The check then
goes live and flags dozens of pre-existing scores that are working
correctly — turning every future `mzt validate` run into a noise
storm that composers learn to ignore. This degrades the entire
validation system.

To prevent this, every new check MUST go through the following
acceptance gate before being merged or recommended for merge:

### Required false-positive sweep

1. **Run the new check against every score in the repository.**
   - `for f in scores/*.yaml scores-internal/*.yaml examples/**/*.yaml; do mzt validate "$f"; done`
   - Pipe through `grep -A2 V<your-id>` to collect hits.
   - Include public examples, internal dev scores, and any active
     concert family. Do not exclude any directory.

2. **For every hit, open the score and read the line in context.**
   Do not skim. Do not assume the hit is real because the regex
   matched. Specifically ask:
   - Does this pattern actually trigger the failure the check
     describes? Run it through a manual repro if uncertain (render
     the Jinja, feed to bash, watch what happens).
   - Is this a legitimate pattern that happens to share structure
     with the landmine? Common false-positive sources:
     - `${{ var }}` — literal `$` before a Jinja expression. Used
       for currency rendering in finance / invoice scores. NOT a bug.
     - `${var:-default}` — bash parameter default. Contains `${` and
       `}` but is structurally inert to Jinja. NOT a bug.
     - `# {{ var }}` — bash comment containing a Jinja expression.
       Renders correctly. NOT a bug.

3. **Record every false-positive case in the gap entry above** under
   "Implementation note from an aborted attempt" or a similar line.
   Future implementers must see what already burned. The case
   `${{ amount }}` for currency in `examples/product/invoice-analysis.yaml`
   is the seed example for this rule — it triggered 6 false positives
   in a five-minute over-eager implementation attempt.

4. **Tighten the regex / AST query until zero false positives remain
   across the entire corpus.** If you cannot eliminate them, narrow
   the check's scope (e.g., "only inside `cli` instrument sheets",
   "only when the prompt block has no `{% raw %}` wrapper") rather
   than shipping a noisy check.

5. **Add the corpus sweep itself as a regression test.** A pytest
   that walks `examples/` + `scores/` + `scores-internal/` and
   asserts your check produces zero hits against the current corpus.
   When a future score legitimately needs the flagged pattern, the
   test breaks loudly and forces a deliberate exemption rather than
   silent normalization.

### Why this matters more for agents than for humans

A human composer who writes a noisy check feels the friction
immediately — every `mzt validate` they run buzzes with their own
false positives. An agent implementing a check works in a fresh
context, runs the synthetic test, and hands back "validation
added." The noise lands on the composer days later, when the
agent is gone. The composer either disables the check, learns
to grep past it, or stops trusting `mzt validate`. All three
outcomes erode the system.

So: if you are an agent reading this before implementing one of
the gaps above, you are required to do the corpus sweep, read
every hit in context, and report the false-positive analysis in
the gap entry **before** declaring the work complete.
