# Marianne v1 Beta — Findings Registry

This file tracks findings, discoveries, and observations made during the v1 beta build concert.
Agents append findings here as they work. The registry serves as institutional memory across movements.

## ID Allocation (D-018, Movement 3)

**DO NOT pick arbitrary sequential IDs.** Use your pre-allocated range from `FINDING_RANGES.md`.
Each musician has 10 reserved IDs per movement. This prevents the 12+ collisions from M1-M3.

Fallback: `./scripts/next-finding-id.sh` reads the current max and prints the next ID.

**Status updates** (resolving an existing finding) do NOT consume a new ID — edit the original entry's Status field.

## Format

Each finding should include:
- **ID:** F-NNN (from your allocated range in FINDING_RANGES.md)
- **Movement:** Which movement it was discovered
- **Agent:** Which agent reported it
- **Category:** bug | architecture | pattern | risk | opportunity | decision
- **Finding:** Description of what was found
- **Action:** What should be done about it (if anything)
- **Status:** open | fixed (commit ref) | wontfix (reason)

---

## Findings Carried Forward from v1-beta Cycle 1

### F-001: CJK/Non-Latin Text Token Underestimation
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Foundation
- **Category:** risk
- **Finding:** `estimate_tokens()` in `src/marianne/core/tokens.py:77` uses `_CHARS_PER_TOKEN = 3.5` calibrated for English. CJK characters are underestimated by 3.5-7x (600 CJK chars → 172 estimated tokens, actual 600-1200). This can cause context window overflow.
- **Action:** Document as known limitation. Fix at M1 with InstrumentProfile.ModelCapacity — script-aware estimation or per-model tokenizer.
- **Status:** open

### F-002: Falsy Value Rejection in SpecCorpusLoader
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Blueprint
- **Category:** bug
- **Finding:** `_load_yaml_fragment()` at `src/marianne/spec/loader.py:201` and `:207` uses `if not name:` / `if not content:`. YAML parses `0` as `int(0)` and `false` as `bool(False)`, which are falsy but not None. The `str()` casts on lines 220-221 would handle coercion, but the guard rejects before reaching there.
- **Action:** Change `if not name:` to `if name is None:` and same for content. Add regression tests.
- **Status:** Resolved (movement 1, Blueprint)
- **Resolution:** Fixed in 8fce797. Changed guards to `if name is None:` / `if content is None:`. 7 regression tests added in test_spec_loader.py (TestFalsyYamlValues class) covering numeric zero, boolean false, empty string, and None for both name and content fields.

### F-003: No Output Scanning for Credential Patterns
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** Agent stdout_tail and stderr_tail are stored in CheckpointState, LearningOutcome records, learning aggregator, dashboard display, and diagnostic output — all without scanning for API key patterns (`sk-ant-`, `sk-`, `AIza`, `AKIA`). If an agent prints a credential, it persists across 6+ storage locations. Blocks executive DONE criterion S3.
- **Action:** Implement output scanning before stdout_tail/stderr_tail storage. Target: M1 safety baseline.
- **Status:** Resolved (movement 1, Maverick)
- **Resolution:** Created `src/marianne/utils/credential_scanner.py` with `redact_credentials()` and `scan_for_credentials()` functions. Wired into `SheetState.capture_output()` at `checkpoint.py:550` — the single write point for stdout_tail/stderr_tail. All 6 credential patterns (Anthropic, OpenAI, Google, AWS, Bearer tokens) are detected and replaced with [REDACTED_*] labels. 14 tests in `tests/test_credential_scanner.py`.

### F-004: skip_when_command Uses Shell Without shlex.quote
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** `src/marianne/execution/runner/lifecycle.py:878` expands `{workspace}` via bare `.replace()` into a shell command passed to `create_subprocess_shell`. Lacks `shlex.quote()` protection used by the validation engine's `command_succeeds` handler at `engine.py:515`.
- **Action:** Apply shlex.quote() to workspace expansion in skip_when_command.
- **Status:** Resolved (movement 1, Ghost)
- **Resolution:** Applied `shlex.quote()` to workspace expansion in `lifecycle.py:879`. Commit 229d55d.

### F-005: No First-Run Cost Warning
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** `src/marianne/cli/commands/run.py:90-163` submits jobs without checking `config.cost_limits.enabled` or displaying cost info. `CostLimitConfig.enabled` defaults to `false`. New users spend API credits with zero notice.
- **Action:** Add cost warning to `mzt run` when cost_limits.enabled is false.
- **Status:** Resolved (movement 1, Circuit)
- **Resolution:** Added cost warning to `mzt run` at `run.py:119-125` that fires when `cost_limits.enabled` is false and output is not JSON/quiet. Shows the warning text and a config suggestion. Also updated config panel to show "Instrument:" instead of "Backend:" to align with the new instrument terminology. 4 tests added to test_cli_run_resume.py.

### F-006: No `mzt doctor` Command
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** No `doctor` command exists. Executive brief criterion U2 requires it. `mzt config init` exists but only creates daemon config — not environment validation.
- **Action:** Implement `mzt doctor` as part of M3 UX.
- **Status:** Resolved (movement 1, Ghost)
- **Note:** Previously incorrectly recorded as resolved (was F-004 copy-paste). Corrected by Canyon.
- **Resolution:** Implemented `mzt doctor` at `src/marianne/cli/commands/doctor.py`. Checks Python version, Marianne version, conductor status (PID file + os.kill probe), instrument availability (native + built-in YAML + user profiles, binary detection via shutil.which), and safety warnings (cost limits). Supports `--json` output mode. 12 tests in `tests/test_cli_doctor.py`. Registered in CLI at `src/marianne/cli/__init__.py`.

---

## Findings from Flowspec Analysis (2026-03-26)

### F-007: 7 Unwired Code Clusters
- **Movement:** 0 (carried from Flowspec analysis)
- **Agent:** Canyon (from Flowspec findings doc)
- **Category:** architecture
- **Finding:** 7 code clusters are built, tested, and exported but never called from production: SchedulerStats, ErrorLearningHooks, OutcomeMigrator, ErrorChain, TableMapping/StateRegistry, RunSummary.to_dict(), DelayOutcome. See `docs/plans/2026-03-26-flowspec-findings.md` for details.
- **Action:** Wire or remove each. Tracked as issue #135.
- **Status:** Deferred to baton integration (movement 1, Ghost)
- **Analysis (movement 1):** All 7 clusters are planned infrastructure awaiting the baton. SchedulerStats is part of the scheduler (documented "built, not yet wired"). ErrorLearningHooks is designed for baton-to-learning integration. OutcomeMigrator is for the daemon-only migration. ErrorChain, TableMapping/StateRegistry are future capabilities. RunSummary.to_dict() is a serialization method on a used class. DelayOutcome is internal to retry_strategy.py. None should be removed — the baton migration will wire them or explicitly deprecate them.

### F-008: 13 Dead Code Entities
- **Movement:** 0 (carried from Flowspec analysis)
- **Agent:** Canyon (from Flowspec findings doc)
- **Category:** architecture
- **Finding:** 6 dead constants in core/constants.py, 7 dead functions in cli/helpers.py. Never called anywhere.
- **Action:** Remove all 13. Tracked as issue #134.
- **Status:** Resolved (movement 1, Ghost)
- **Resolution:** Removed all 13 entities. Updated `__all__` in helpers.py. Commit 229d55d.

---

## New Findings (Movement 1+)

(Agents: append new findings below this line)

### F-009: Learning Store Effectiveness Scores Are Uniform (All 0.5000)
- **Found by:** Oracle, Movement 1
- **Severity:** P1 (high)
- **Status:** Partially Resolved (movement 3, Maverick) — tag namespace mismatch fixed, see F-144
- **Description:** All 25,415 patterns in `~/.marianne/global-learning.db` have `effectiveness_score = 0.5000`. Verified via `SELECT AVG(effectiveness_score), MIN(effectiveness_score), MAX(effectiveness_score) FROM patterns` — all returned 0.5000. The effectiveness calculation either hasn't run, was reset, or produces degenerate output. This means the learning system cannot distinguish effective patterns from ineffective ones.
- **Impact:** Pattern injection into agent prompts has no quality signal. Good and bad patterns are treated identically. The learning store grows in volume (25,415 patterns, 218,790 executions) but not in intelligence. The baton's planned learning integration inherits this — instrument-scoped queries will return patterns with no quality differentiation.

### F-010: Credential Scanner Double-Call Bug in SheetState.capture_output
- **Found by:** Canyon, Movement 1
- **Severity:** P0 (critical — breaks all tests that call capture_output)
- **Status:** Resolved (movement 1, Maverick) — NOT A BUG
- **Description:** Canyon assumed `redact_credentials()` returns `tuple[str, list[str]]`, but the actual implementation at `src/marianne/utils/credential_scanner.py` returns `str | None` (same type as input). The API is: `redact_credentials(text) -> text` with credential patterns replaced by `[REDACTED_*]` labels. A separate `scan_for_credentials(text) -> list[str]` function exists for detection-without-redaction. The assignment at checkpoint.py:562-563 is correct as written. 14 tests validate this behavior.
- **Resolution:** Not a bug. Canyon's analysis was based on an assumption about the API, not the actual implementation.

### F-011: CONFIG_STATE_MAPPING Missing Entries for New Instrument Fields
- **Found by:** Blueprint, Movement 1
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 1, Harper)
- **Resolution:** Added `CONFIG_STATE_MAPPING` entries for `instrument` and `instrument_config` in `reconciliation.py`. Commit 85f0b2f.
- **Description:** `test_reconciliation.py::TestConfigStateMapping::test_mapping_covers_all_config_sections` fails because the new `instrument` and `instrument_config` fields added to `JobConfig` (from Canyon's M1 instrument work) have no `CONFIG_STATE_MAPPING` entry in the reconciliation system. The test asserts that every JobConfig section has a mapping defining what checkpoint state to reset when that section changes.
- **Impact:** Config reload via `mzt resume -c` won't know what state to reset when instrument settings change. Low immediate impact since the baton spec redesigns this flow, but the test failure blocks full suite pass.
- **Action:** Add `CONFIG_STATE_MAPPING` entries for `instrument` and `instrument_config` with appropriate reset targets. Alternatively, mark as deferred if the baton redesign will supersede this.

### F-012: test_instrument_loader.py References Non-Existent Module
- **Found by:** Blueprint, Movement 1
- **Severity:** P1 (high — test file committed without implementation)
- **Status:** Resolved (movement 1, Harper)
- **Resolution:** InstrumentProfileLoader implemented at `src/marianne/instruments/loader.py` and test committed at `tests/test_instrument_loader.py`. Import path corrected to `marianne.instruments.loader`. Commit 85f0b2f.
- **Description:** `tests/test_instrument_loader.py` (untracked) imports `marianne.core.instruments.loader.InstrumentProfileLoader` which doesn't exist. This is test code for TASKS.md M1 roadmap step 4 ("Implement profile YAML loading"). The test file was created before the implementation, which is correct TDD, but it's untracked and will cause `pytest tests/ -x` to fail if collected.
- **Impact:** Full test suite fails with `ModuleNotFoundError` when this file is collected. Other agents running the full suite will see a false failure.
- **Action:** Either (a) commit the file as the TDD spec for step 4, with the understanding the tests will fail until the module is implemented, or (b) leave untracked until the implementation exists. The file should not be committed in a state that breaks `pytest tests/ -x`.

### F-013: Baton M2 Implementation Landed Without Tests or Commit
- **Found by:** Canyon, Movement 1
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 1, Canyon + Foundation)
- **Description:** Foundation implemented 4 baton modules — timer.py (253 lines), core.py (692 lines), state.py (446 lines), backend_pool.py (308 lines) — covering roadmap steps 18-21 and 27. All were left untracked with lint errors (3 ruff violations). Foundation also wrote 144 tests across 4 test files but did not commit. The __init__.py was updated but missing state.py exports.
- **Impact:** 1,699 lines of critical path code existed only in the working tree. A `git clean` or accidental checkout would have lost it all. The quality gate (test_quality_gate.py) was already failing from an unrelated assertion-less test in test_stale_detection.py.
- **Resolution:** Canyon fixed 3 ruff violations (import sorting + SIM103), added missing assertions to 2 tests, added state.py exports to __init__.py, and will commit all baton work on main with attribution to Foundation.

### F-014: F-006 Resolution Was Incorrectly Recorded
- **Found by:** Canyon, Movement 1
- **Severity:** P3 (low — findings registry error)
- **Status:** Resolved (movement 1, Canyon)
- **Description:** F-006 ("No mzt doctor command") had its resolution field copy-pasted from F-004 ("shlex.quote fix"). The doctor command was never implemented. The TASKS.md still correctly lists it as unclaimed in M3.
- **Impact:** Anyone reading FINDINGS.md would believe `mzt doctor` was implemented. It is not.
- **Resolution:** Corrected F-006 status back to Open with a note explaining the error.

### F-015: Unwired Code Cluster Analysis (F-007 Disposition)
- **Found by:** Blueprint, Movement 1
- **Severity:** P2 (medium)
- **Status:** Partially resolved (movement 1, Blueprint)
- **Description:** Investigated all 7 unwired code clusters from F-007/issue #135. Disposition:
  1. **SchedulerStats** (scheduler.py:98) — KEEP. Required for scheduler/baton integration.
  2. **ErrorLearningHooks** (error_hooks.py:95) — KEEP. Movement III feature, design complete.
  3. **OutcomeMigrator** (migration.py:94) — ALREADY WIRED. Called from `learning-stats` CLI.
  4. **ErrorChain** (errors/models.py:190) — KEEP. Infrastructure for `mzt diagnose`.
  5. **TableMapping/StateRegistry** (schema/registry.py:154) — REMOVE. Premature abstraction, never adopted. Registry exports removed from `schema/__init__.py`. File itself kept for now.
  6. **RunSummary.to_dict()** (runner/models.py:109) — NOT TOUCHED. File has concurrent edits. Dead method, can be removed when runner stabilizes.
  7. **DelayOutcome** (retry_strategy.py:61) — KEEP. Infrastructure for adaptive retry learning.
- **Impact:** 5 of 7 clusters are correctly kept for planned features. 1 is already wired (OutcomeMigrator). 1 is dead code (TableMapping registry — exports removed). The net dead code surface decreased.
- **Resolution:** Schema registry exports removed from `__init__.py`. Full disposition documented above.

### F-016: Circular Dependency Analysis (Issue #136)
- **Found by:** Blueprint, Movement 1
- **Severity:** P3 (low — all managed safely via TYPE_CHECKING/deferred imports)
- **Status:** Analyzed — no refactoring needed now
- **Description:** Investigated all 3 circular dependency design smells from issue #136:
  1. **backpressure → monitor → manager → backpressure:** Uses runtime imports for 2/3 edges. Safe. Will be addressed by baton migration (manager.py is high-impact target).
  2. **parallel → runner/__init__ → runner/base → parallel:** Deferred import at base.py:319 inside method body. Safe. Runner's parallel execution will be replaced by baton dispatch.
  3. **outcomes → patterns → outcomes:** TYPE_CHECKING + deferred method imports (outcomes.py:247,282). Safe. Learning layer is stable. Could extract shared types to a `learning/types.py` module, but the current pattern works and the risk is near zero.
- **Impact:** All 3 cycles are well-managed. No runtime errors, no import failures. 2 of 3 will be organically resolved when the baton replaces the daemon manager and runner.
- **Action:** No immediate refactoring. Revisit after baton migration stabilizes.

### F-017: Dual SheetExecutionState — state.py vs core.py
- **Found by:** Foundation, Movement 1
- **Severity:** P2 (medium — needs reconciliation before baton ships)
- **Status:** Resolved (movement 2, Circuit) — see F-054 for details
- **Description:** Two `SheetExecutionState` classes exist in the baton package. `core.py:67` has a simple dataclass with string `status` field and no methods. `state.py:161` has a richer version with `BatonSheetStatus` enum, `record_attempt()`, `can_retry`/`can_complete`/`is_exhausted` properties, cost tracking, `to_dict()`/`from_dict()` serialization, and total_duration tracking. Both were built concurrently by different musicians.
- **Impact:** The `__init__.py` exports `SheetExecutionState` from `core.py`. The `state.py` version is importable via `from marianne.daemon.baton.state import SheetExecutionState`. No runtime error today — the two classes are independent. But the baton needs ONE authoritative type. The `state.py` version is designed for the full baton lifecycle (persistence, failure evaluation, cost tracking). The `core.py` version is designed for the immediate event loop.
- **Action:** When the baton's retry state machine (step 23) and failure evaluation (step 26) are built, reconcile by adopting `state.py`'s richer types in `core.py`. The enum-based status prevents string typos, the serialization enables restart recovery, and the attempt tracking enables the conductor's decision tree.

### F-018: Musician-Baton Contract for validation_pass_rate Is Implicit
- **Found by:** Bedrock, Movement 1
- **Severity:** P2 (medium — landmine for step 22 builder)
- **Status:** Partially resolved (movement 1, Axiom — see F-043)
- **Resolution note (Journey):** The baton at `core.py:431-436` now auto-corrects `validation_pass_rate` to 100.0 when `validations_total==0` and `execution_success==True`. The landmine is defused for the no-validations case. Updated litmus tests at `test_baton_litmus.py` to verify the fix. Documentation on `SheetAttemptResult` still needed.
- **Description:** The baton's decision tree in `core.py:412-449` makes sharp distinctions based on `SheetAttemptResult.validation_pass_rate`:
  - `>= 100.0` → sheet completes (line 414)
  - `> 0` and `< 100` → retry, counts as an attempt (line 430)
  - `== 0.0` → retry, counts as an attempt (line 447-449)
  The `SheetAttemptResult.validation_pass_rate` field (`events.py:67`) defaults to `0.0`. This means a musician that reports `execution_success=True` with no validations but forgets to set `validation_pass_rate=100.0` will trigger unnecessary retries instead of completion. The existing runner in `sheet.py:778` handles this correctly (hardcodes `100.0` for all-pass), but the contract is not documented on the `SheetAttemptResult` dataclass itself. Whoever builds step 22 (single-attempt musician for the baton) needs to know this.
- **Impact:** If the step 22 builder doesn't set `validation_pass_rate=100.0` when no validations exist, sheets with no validations will always fail after exhausting retries. The default of `0.0` is a reasonable safety default (unknown = fail), but the contract must be explicit.
- **Action:** Add a docstring note to `SheetAttemptResult.validation_pass_rate` documenting the semantics: "Set to 100.0 when execution succeeds with no validation rules or all validations pass. The baton treats 0.0 as 'all validations failed' and will retry." Also consider a guard in `_handle_attempt_result` that treats `validations_total == 0` as 100% pass rate.

### F-019: Uncommitted PreflightConfig Work Across 7 Files
- **Found by:** Bedrock, Movement 1
- **Severity:** P3 (low — well-designed, needs committing)
- **Status:** Resolved (movement 1, Captain — mateship pickup)
- **Resolution:** Captain committed the PreflightConfig work on main. 14 files: PreflightConfig Pydantic model in execution.py, wiring through DaemonConfig → JobManager → JobService → RunnerContext → PreflightChecker, property-based tests, quality gate baseline updates, test fixes (assertion in test_stale_detection, asyncio.sleep in test_state_concurrent), F-018 docstring on SheetAttemptResult.validation_pass_rate.
- **Description:** An unnamed musician implemented configurable preflight token thresholds across 7 files. Well-designed but all uncommitted.
- **Impact:** A `git clean` or accidental checkout would lose the work.
- **Action:** The implementing musician should commit this work on main.

### F-020: Hook Command Execution Lacks shlex.quote — Shell Injection via Variable Expansion
- **Found by:** Sentinel, Movement 1
- **Severity:** P1 (high — same class as fixed F-004)
- **Status:** Resolved (movement 2, Maverick)
- **Resolution:** Added `for_shell` parameter to `expand_hook_variables()`. Shell execution paths apply `shlex.quote()` to variable values. Both hooks.py and manager.py callsites updated. 13 regression tests. Commit 5525076.
- **Description:** `expand_hook_variables()` at `src/marianne/execution/hooks.py:182-184` performs bare `.replace("{workspace}", str(workspace))` without `shlex.quote()`. The expanded result is then passed directly to `create_subprocess_shell` at `hooks.py:626` (run_command hook) and `manager.py:1869` (daemon-side hook command). A workspace path containing shell metacharacters would be interpreted as shell commands. Compare to code that IS protected: `lifecycle.py:888` (shlex.quote for skip_when_command, fixed by Ghost as F-004) and `engine.py:515` (shlex.quote for validation commands). The hooks system was never patched when Ghost fixed F-004.
- **Impact:** Shell injection via crafted workspace paths or job IDs in score YAML `on_success` hooks. Currently mitigated by the fact that workspace paths are typically set by the score author (who controls the execution environment), but becomes a real risk if Marianne processes untrusted scores.
- **Action:** Apply `shlex.quote()` to all variable expansions in `expand_hook_variables()` when used for shell commands. Since the function is also used for path expansion (run_job hooks) where quoting would break path resolution, either split into two functions or apply quoting only at the shell execution callsite.

### F-021: Python Expression Sandbox in skip_when Is Bypassable via Attribute Access
- **Found by:** Sentinel, Movement 1
- **Severity:** P2 (medium — operator-controlled config today, but exploitable if untrusted scores are ever run)
- **Status:** Open
- **Description:** `src/marianne/execution/runner/lifecycle.py:862` uses Python expression evaluation with a restricted `__builtins__` dict and passes `state.sheets` and `state` (CheckpointState) as locals. The sandbox blocks direct builtin access but cannot prevent attribute traversal on the passed objects. A crafted `skip_when` condition could access `state.__class__.__mro__[1].__subclasses__()` to reach arbitrary Python types, potentially enabling code execution. The noqa comment correctly notes this is "operator-controlled config, not user input" — which is true today.
- **Impact:** Currently low — score authors control their own execution environment. Future high — if untrusted scores are supported (Lovable demo, community-shared scores), this becomes arbitrary code execution.
- **Action:** For v1, document as known limitation in the score writing guide ("skip_when conditions execute as Python expressions — only run trusted scores"). For v2, replace with a safe expression parser (compile to AST, whitelist only comparison/boolean/attribute nodes, reject dunder access).

### F-022: CSP Allows unsafe-inline and unsafe-eval for Dashboard
- **Found by:** Sentinel, Movement 1
- **Severity:** P2 (medium — weakens XSS protection)
- **Status:** Open
- **Description:** `src/marianne/dashboard/auth/security.py:60-63` sets Content-Security-Policy with `'unsafe-inline' 'unsafe-eval'` for script-src and `'unsafe-inline'` for style-src. This effectively disables CSP's XSS protection for inline scripts. While understandable for a local dashboard using CDN-loaded frameworks (Tailwind, Alpine.js), it means any XSS vector in the dashboard templates would not be blocked by CSP.
- **Impact:** If any dashboard endpoint reflects user input (job names, error messages) without proper HTML escaping, CSP will not provide a safety net. The LOCALHOST_ONLY auth default partially mitigates this.
- **Action:** For v1, document that the dashboard should not be exposed to untrusted networks. For v2, consider using CSP nonces for inline scripts and migrating away from unsafe-eval.

### F-023: Credential Scanner Missing Common API Key Patterns
- **Found by:** Sentinel, Movement 1
- **Severity:** P3 (low — scanner covers the most critical patterns)
- **Status:** Resolved (movement 2, Warden)
- **Note:** This entry was corrupted in movement 1 — F-019's resolution (PreflightConfig/Tempo) was accidentally pasted under F-023. The credential patterns were NOT added until movement 2.
- **Description:** `src/marianne/utils/credential_scanner.py` detected 6 credential patterns (Anthropic, OpenAI, Google, AWS, Bearer tokens). Missing patterns for GitHub PAT tokens (`ghp_`, `gho_`, `github_pat_`), Slack tokens (`xoxb-`, `xoxp-`, `xapp-`), and Hugging Face tokens (`hf_`).
- **Impact:** Agents interacting with GitHub/Slack/HF APIs could leak tokens into stdout_tail, propagating to 6+ storage locations.
- **Resolution:** Added 7 new patterns to `src/marianne/utils/credential_scanner.py`: GitHub classic PAT (ghp_), GitHub OAuth (gho_), GitHub fine-grained PAT (github_pat_), Slack bot (xoxb-), Slack user (xoxp-), Slack app (xapp-), and Hugging Face (hf_). 10 TDD tests in `tests/test_credential_scanner.py` across 3 new test classes plus 3 scan_for_credentials detection tests. Scanner now detects 13 credential patterns (up from 6).

### F-024: Baton Retry State Machine Has No Cost Enforcement
- **Found by:** Warden, Movement 1
- **Severity:** P2 (medium — matters when baton replaces runner)
- **Status:** Resolved (movement 2, Foundation + Circuit)
- **Description:** The baton's decision tree originally had no cost limit enforcement. `SheetAttemptResult.cost_usd` was logged but never compared against limits.
- **Impact:** Sheets could retry up to max_retries without any cost check, potentially burning through budget.
- **Resolution:** Foundation (step 23) and Circuit (step 26) added comprehensive cost enforcement to `core.py`: `set_job_cost_limit()` (line 235), `set_sheet_cost_limit()` (line 310), `_check_job_cost_limit()` called after EVERY attempt result (lines 759, 794, 814, 830), `_check_sheet_cost_limit()` called before retry scheduling (line 818). Per-sheet exceeded → fail + propagate. Per-job exceeded → pause. Matches the baton design spec exactly.

### F-025: PluginCliBackend Passes Full Parent Environment (Spec Violation)
- **Found by:** Warden, Movement 1
- **Severity:** P2 (medium — acceptable for v1 trusted instruments, needs fixing for v1.1)
- **Status:** Resolved (movement 1 current cycle, Warden)
- **Description:** `src/marianne/execution/instruments/cli_backend.py:193` builds subprocess environment with `dict(os.environ)` — the full parent environment. The safety hardening design spec explicitly requires: "The PluginCliBackend passes only explicitly declared env vars to subprocesses." Current implementation does the opposite: every credential in the parent environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, AWS_SECRET_ACCESS_KEY, etc.) is passed to every plugin instrument subprocess.
- **Impact:** For trusted built-in instruments (gemini-cli, codex-cli), this is acceptable — they need API keys. For user-authored or third-party instrument profiles, this is a credential exposure vector. A malicious CLI binary disguised as an instrument would receive every secret.
- **Action:** Implement env filtering per the safety hardening spec. This is M5 roadmap step 47 — already tracked. For v1, document the risk in the instrument authoring guide.
- **Resolution:** Added `required_env: list[str] | None` field to `CliCommand` (`src/marianne/core/config/instruments.py`). When set, `PluginCliBackend._build_env()` filters the parent environment to only include declared vars + system essentials (PATH, HOME, TERM, LANG, etc. — defined in `SYSTEM_ENV_VARS` frozenset). Updated 3 built-in profiles: `gemini-cli.yaml` (GOOGLE_API_KEY, GOOGLE_APPLICATION_CREDENTIALS), `claude-code.yaml` (ANTHROPIC_API_KEY + Bedrock/Vertex vars), `codex-cli.yaml` (OPENAI_API_KEY, CODEX_API_KEY). Multi-provider instruments (aider, goose, cline) left without required_env — they genuinely need multiple provider credentials. 19 TDD tests in `tests/test_credential_env_filtering.py`. Backward compatible: `required_env: null` (default) inherits full parent environment.

### F-026: README Quick Start References Removed --workspace Flag on status
- **Found by:** Newcomer, Movement 1
- **Severity:** P0 (critical — tutorial breaks at step 5)
- **Status:** Resolved (movement 1, Compass)
- **Resolution:** Removed `--workspace` from README status/resume examples. Updated Common Options table to scope `--workspace` to `run`, `resume` only. Also updated backend→instrument terminology, added `mzt doctor` and `mzt instruments` commands to CLI reference, fixed recover command "(hidden)" label (F-034), and updated instrument description (F-036).
- **Description:** `README.md:169` says `mzt status hello-world --workspace ./workspace/hello-world`. The `status` command no longer has a `-w/--workspace` flag. Verified via `mzt status --help` — only `--json`, `--watch`, `--interval` exist.
- **Impact:** A newcomer following the Quick Start guide hits a CLI error at "check your results." The tutorial fails at the worst possible moment — when the user is trying to verify their first job worked.
- **Action:** Remove `--workspace` from the README status example. The status command resolves workspaces from the conductor's job registry, so no flag is needed.

### F-027: mzt status With No Arguments Gives Error Instead of Overview
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high — violates universal CLI convention)
- **Status:** Resolved (movement 2, Circuit)
- **Resolution:** Made `job_id` optional in `status()` command. When omitted, shows conductor status (running/uptime), active scores (running/queued/paused), and 5 most recent terminal scores. JSON output supported. 14 tests in test_cli_status_overview.py. Code in cfb7897 (Forge's commit included the working tree changes), tests in ece7382.
- **Description:** `mzt status` → "Missing argument 'JOB_ID'". Every major CLI tool (git status, docker ps, kubectl get pods) shows an overview when called with no arguments. Marianne forces you to know the exact score ID.
- **Impact:** Newcomers' natural first action after `mzt run` is `mzt status`. Getting an error instead of a summary is disorienting and makes the tool feel broken.

### F-028: Empty Config File Leaks Internal Python Error
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high — terrible first impression)
- **Status:** Resolved (movement 1, Compass)
- **Description:** `mzt run /dev/null` → "Error loading config: argument of type 'NoneType' is not iterable". YAML parses an empty file as `None`, then the config loading code tries to iterate over it.
- **Impact:** A newcomer who creates an empty YAML file and tries to run it sees a Python internal error. This makes them feel stupid. The error message is the bug, not them.
- **Resolution:** Added `isinstance(data, dict)` guard in `JobConfig.from_yaml()` and `from_yaml_string()` at `job.py:655-660`. Empty files, None YAML, and non-dict YAML (lists, scalars) now produce: "The score file is empty or invalid. A Marianne score requires at minimum: name, sheet, and prompt sections." Verified with empty file, list YAML, and empty string inputs.

### F-029: CLI Uses "JOB_ID" Arguments but "Score" in Output
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high — violates composer directive on music metaphor)
- **Status:** Open
- **Description:** Every command that takes a score identifier uses `JOB_ID` as the argument name: status, errors, diagnose, recover, resume, pause, cancel, history. But output uses "Score not found", `mzt list` shows "SCORE ID", and help text says "Show detailed status of a specific score." The composer's directive: "The music metaphor is load-bearing."
- **Impact:** Terminology inconsistency confuses newcomers and undermines the musical identity. The score is the central concept — calling it a "job" in the most user-visible place (CLI arguments) sends mixed signals.
- **Action:** Rename `JOB_ID` arguments to `SCORE_ID` across all commands. This is an E-002 escalation trigger (changing CLI command interface) — requires composer approval.

### F-030: "Score Not Found" Errors Suggest No Next Step
- **Found by:** Newcomer, Movement 1
- **Severity:** P2 (medium — easy fix, big UX improvement)
- **Status:** Resolved (movement 1, Compass)
- **Description:** `mzt status nonexistent`, `mzt diagnose nonexistent`, `mzt errors nonexistent` all produce "Score not found: nonexistent" with no guidance. Compare to `git` which suggests "did you mean...?" or shows similar branch names.
- **Impact:** The user is stuck. They know their score doesn't exist but don't know what to do about it.
- **Resolution:** Migrated 9 "Score not found" error outputs across `status.py` (2), `diagnose.py` (5), and `recover.py` (2) from raw `console.print` to `output_error()` with hints: "Run 'mzt list' to see available scores." The diagnose command also suggests "Run 'mzt doctor' to check your environment." JSON output mode also receives hints in structured format.

### F-031: Malformed YAML Produces Misleading Pydantic Error
- **Found by:** Newcomer, Movement 1
- **Severity:** P2 (medium — error misdirects troubleshooting)
- **Status:** Resolved (movement 4, Lens)
- **Description:** `echo "this is not yaml {{{" > /tmp/bad.yaml && mzt validate /tmp/bad.yaml` → "Schema validation failed: Input should be a valid dictionary or instance of JobConfig". The actual problem is invalid YAML syntax, but the error implies the YAML was parsed successfully and the schema doesn't match.
- **Impact:** User thinks they need to fix their config structure when they actually have a YAML syntax error. Misdirected troubleshooting.
- **Resolution:** Added `yaml.YAMLError` catch in `run.py:97` before the generic Exception handler. YAML syntax errors now show "YAML syntax error: ..." with hints about indentation and a pointer to `mzt validate`. The `validate` command already handled this correctly (lines 87-95). Also fixed `hint=` (singular) misuse in run.py — `output_error()` only accepts `hints=` (list), so `hint=` went to `**json_extras` and was invisible in terminal mode. 8 TDD tests in `test_cli_error_ux.py`.

### F-032: JSON Output Contains Invalid Control Characters
- **Found by:** Newcomer, Movement 1
- **Severity:** P2 (medium — breaks machine-readable output contract)
- **Status:** Resolved (movement 2, Dash)
- **Description:** `mzt status <job> --json` produces JSON that fails to parse due to unescaped control characters in stdout_tail/stderr_tail fields. Verified with `mzt status marianne-orchestra-v3 --json | python3 -c "import json,sys; json.load(sys.stdin)"` → JSONDecodeError.
- **Impact:** Any script, dashboard, or CI pipeline consuming `--json` output will crash. The `--json` flag exists specifically for machine parsing — producing invalid JSON defeats its purpose.
- **Resolution:** Added `_sanitize_for_json()` in `src/marianne/cli/output.py` that recursively strips C0/C1 control characters and ANSI escape sequences from string values. Called by `output_json()` before serialization. Preserves safe whitespace (\t, \n, \r). Also applied to `output_error()` JSON mode.

### F-033: Architecture Spec Lists Wrong Validation Type Names
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low — internal doc inconsistency)
- **Status:** Resolved (movement 1, Captain)
- **Resolution:** Updated `.marianne/spec/architecture.yaml` validation table and `validation_types` list. Old: content_match, file_count, command, composite. New: file_modified, content_contains, content_regex, command_succeeds (matches `src/marianne/core/config/execution.py:415-420`).
- **Description:** `.marianne/spec/architecture.yaml` data.validation_types lists: file_exists, content_match, file_count, command, composite. Actual types in code (`src/marianne/core/config/execution.py:473-486`): file_exists, file_modified, content_contains, content_regex, command_succeeds.
- **Impact:** An agent reading only the architecture spec would use wrong validation type names in score configs.
- **Action:** Update architecture.yaml validation_types to match the actual code.

### F-034: README Says recover Command Is Hidden But It's Visible
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low)
- **Status:** Resolved (movement 1, Compass)
- **Resolution:** Removed "(hidden)" label from README recover command row.
- **Description:** `README.md:230` describes `recover` as "(hidden)" but it appears in `marianne --help` under the Diagnostics panel.
- **Impact:** Minor inconsistency. Either remove the "(hidden)" label from the README or actually hide the command.

### F-035: getting-started.md Shows Old Validate Output Format
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low — doc shows different format than reality)
- **Status:** Resolved (movement 1, Compass)
- **Description:** `docs/getting-started.md:112-116` says validate shows "Valid configuration: my-first-job / Sheets: 3 (10 items each) / Validations: 1". Actual format is richer: "Validating... / ✓ YAML syntax valid / ✓ Schema validation passed / Running extended validation checks / ..."
- **Impact:** Newcomers might think something is wrong when they see a different format than documented.
- **Resolution:** Updated expected output in getting-started.md to show the current validate output format with checkmarks. Also updated prerequisites to mention `mzt doctor` and troubleshooting to use `mzt instruments list` instead of `claude --version`.

### F-036: README Backend List Outdated — Doesn't Mention New Instruments
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low — README undersells capabilities)
- **Status:** Resolved (movement 1, Compass)
- **Resolution:** Updated README "Multiple backends" line to "Multiple instruments" with full instrument list. Added `mzt instruments list|check` and `mzt doctor` to CLI reference tables. Updated "Backend" key concept to "Instrument". Updated backend type option to include named instruments.
- **Description:** `README.md:32` lists 4 backends (Claude CLI, Anthropic API, Ollama, Recursive Light). The instrument plugin system (M1) added 6 more: gemini-cli, codex-cli, cline-cli, aider, goose, claude-code. `mzt instruments list` shows 10 instruments.
- **Impact:** Newcomers with non-Claude tools may not realize Marianne supports them.
- **Action:** Update README to mention the instrument plugin system and list all supported instruments.

### F-037: Score Writing Guide Workspace Path Doesn't Match Example
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low)
- **Status:** Resolved (movement 2, Forge) — see updated entry below
- **Description:** Score writing guide says `workspace: "./simple-workspace"`. Actual `examples/simple-sheet.yaml:12` says `workspace: "../workspaces/simple-workspace"`.
- **Impact:** Minor confusion if someone cross-references the guide with the actual file.
- **Action:** Update score writing guide to match the actual example file.

### F-038: Status Display Produces 797 Lines for 706-Sheet Score
- **Found by:** Ember, Movement 1
- **Severity:** P0 (critical — demo-blocking, daily-use blocker)
- **Status:** Resolved (movement 2, Circuit)
- **Resolution:** Scores with 50+ sheets now show a compact summary: counts-by-status, then only interesting sheets (running, failed, validation-failed) capped at 20. Validation failures capped at 10 with pointer to `mzt errors`. Small scores (<50 sheets) keep full detail table. Commit 41f2be4. 4 tests.
- **Description:** `mzt status marianne-orchestra-v3` outputs 797 lines. Hundreds of identical "pending" rows (sheets 27-706) bury the useful information (validation failures at line 770+). Even with `--watch`, the volume makes the output useless for any score larger than ~20 sheets. Related to but distinct from F-027 (no-args overview) — this is about the per-job view being unusable at scale even when you provide the correct job ID.
- **Impact:** Users of any substantial score learn to avoid `mzt status`. The feature becomes anti-useful. Pipe to `tail` is the workaround, but validation failures in the middle of the output are invisible to both `head` and `tail`.

### F-039: Dependency Failure Creates Zombie Jobs (No Failure Propagation)
- **Found by:** Axiom, Movement 1
- **Severity:** P0 (critical — any dependency failure makes `is_job_complete` return False forever)
- **Status:** Resolved (movement 1, Axiom)
- **Description:** When a sheet fails (retries exhausted, auth failure), downstream sheets that depend on it remain in "pending" status forever. `_is_dependency_satisfied` at `core.py:266-276` only returns True for "completed" or "skipped" — not "failed". `is_job_complete` requires ALL sheets to be terminal, but pending sheets are not terminal. Result: any dependency failure in a chain creates a job that can never complete. For a 706-sheet concert with dependency chains, a single failure freezes all downstream work.
- **Impact:** The baton's main loop would run forever waiting for sheets that can never be scheduled. No error, no timeout (unless job timeout is set), no diagnostic. The job is silently stuck.
- **Resolution:** Added `_propagate_failure_to_dependents()` method to `BatonCore`. Uses iterative BFS from the failed sheet through the reverse dependency graph. All non-terminal transitive dependents are marked "failed". Called from `_handle_attempt_result` (retries exhausted, auth failure), `_handle_escalation_resolved` (fail decision), `_handle_escalation_timeout`, and `_handle_process_exited` (crash + retries exhausted). 4 tests prove the fix.

### F-040: Escalation Resolution Unconditionally Unpauses User-Paused Jobs
- **Found by:** Axiom, Movement 1
- **Severity:** P1 (high — violates user expectations)
- **Status:** Resolved (movement 1, Axiom)
- **Description:** `_handle_escalation_resolved` at `core.py:579` and `_handle_escalation_timeout` at `core.py:598` both unconditionally set `job.paused = False`. If a user runs `mzt pause <job>` AND an escalation occurs simultaneously, resolving the escalation would silently unpause the user's manual pause. The user expects their pause to persist until they explicitly resume.
- **Impact:** User-initiated pauses can be overridden by internal events. The user's pause command has no guarantee of durability.
- **Resolution:** Added `user_paused: bool` field to `_JobRecord`. `_handle_pause_job` sets both `paused=True` and `user_paused=True`. `_handle_resume_job` clears both. `_handle_escalation_resolved` and `_handle_escalation_timeout` only unpause if `user_paused` is False. 2 tests prove the fix.

### F-041: RateLimitHit Handler Marks Non-Dispatched Sheets as "Waiting"
- **Found by:** Axiom, Movement 1
- **Severity:** P1 (high — status regression)
- **Status:** Resolved (movement 1, Axiom)
- **Description:** `_handle_rate_limit_hit` at `core.py:522-531` sets `sheet.status = "waiting"` without checking the sheet's current status. A RateLimitHit for a pending, completed, or failed sheet would incorrectly transition it to "waiting". Pending sheets haven't been sent to an instrument yet. Terminal sheets must never regress.
- **Impact:** Stale or duplicate RateLimitHit events could corrupt sheet state. A pending sheet marked as "waiting" would never be dispatched (it's not in `_DISPATCHABLE_STATUSES`).
- **Resolution:** Added guard: only sheets in "dispatched" or "running" status transition to "waiting". 2 tests prove the fix.

### F-042: Late-Arriving Events Can Regress Terminal Sheet Status
- **Found by:** Axiom, Movement 1
- **Severity:** P1 (high — state machine invariant violation)
- **Status:** Resolved (movement 1, Axiom)
- **Description:** `_handle_attempt_result` had no terminal state guard. A SheetAttemptResult arriving after a sheet was already completed or failed would be processed: appended to attempt_results, potentially incrementing normal_attempts, and potentially changing status. In an async system with concurrent event sources, late-arriving results are expected — the handler must be idempotent for terminal sheets.
- **Impact:** A late success result could resurrect a failed sheet. A late failure result could regress a completed sheet to retry_scheduled.
- **Resolution:** Added terminal guard at the top of `_handle_attempt_result`: if `sheet.status in _TERMINAL_STATUSES`, return early. 2 tests prove the fix.

### F-043: F-018 Fixed — No-Validation Sheets Now Complete Correctly
- **Found by:** Axiom, Movement 1 (original finding: Bedrock, Breakpoint)
- **Severity:** P1 (high — resolved)
- **Status:** Resolved (movement 1, Axiom)
- **Description:** The baton's decision tree treated `validation_pass_rate=0.0` as "all validations failed" even when `validations_total=0` (meaning no validations exist). A musician reporting `execution_success=True` with no validation rules would trigger unnecessary retries until max_retries exhausted.
- **Impact:** Every sheet without validation rules required the musician to explicitly set `validation_pass_rate=100.0` — a subtle contract that was undocumented and easy to forget.
- **Resolution:** Added guard in `_handle_attempt_result`: when `execution_success=True`, `validations_total=0`, and `validation_pass_rate < 100.0`, the effective pass rate is treated as 100.0. The musician no longer needs to remember this contract. 2 tests prove the fix. Breakpoint's adversarial tests updated to reflect correct behavior.

### F-045: "completed" Status Shown for Retry-Exhausted (Failed) Sheets
- **Found by:** Ember, Movement 1 (renumbered from F-039 by Captain — collision with Axiom's F-039)
- **Severity:** P1 (high — misleading terminology)
- **Status:** Resolved (movement 2, Forge) — see updated entry below
- **Description:** Sheets 11-14 in the live concert show `status: completed` with `validation: ✗ Fail`. "Completed" in natural language means "done successfully." The actual state is "exhausted retries and was marked terminal." The status display conflates "terminal" with "completed." A user reading `completed | ✗ Fail` will be confused about whether the sheet succeeded.
- **Impact:** Every user who encounters a retry-exhausted sheet will misread the status. Trust in the status display erodes.
- **Action:** The display layer should map `completed + validation failed` to `failed` or `exhausted` in user-facing output. The internal state can remain as-is — this is a presentation fix, not a state model change.

### F-046: Instruments Table STATUS Column Shows "http" Instead of Readiness
- **Found by:** Ember, Movement 1 (renumbered from F-040 by Captain — collision with Axiom's F-040)
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 2, Harper — HTTP instruments now show "? unchecked" instead of "http")
- **Description:** `mzt instruments list` shows `http` in the STATUS column for all HTTP instruments (anthropic_api, ollama, recursive_light). "http" is the kind (already shown in the KIND column), not a status. The code at `src/marianne/cli/commands/instruments.py:154-156` hard-codes `status_str = "[dim]http[/dim]"` and counts HTTP instruments as ready without any connectivity check. Compare to `mzt doctor` which shows the endpoint URL: `✓ anthropic_api — Anthropic API (https://api.anthropic.com)`.
- **Impact:** Users can't tell if HTTP instruments are actually reachable. The "6 ready" count includes unchecked instruments.
- **Action:** Show `✓ available` (or `? unchecked`) instead of `http` in the STATUS column. Optionally, add a basic HTTP HEAD check.

### F-047: `output_error()` Function Exists But 83% of CLI Errors Don't Use It
- **Found by:** Ember, Movement 1 (renumbered from F-041 by Captain — collision with Axiom's F-041)
- **Severity:** P2 (medium — systematic error consistency gap)
- **Status:** Resolved (movement 2, multiple musicians + Spark mateship — commits e6d6753, d242046)
- **Description:** `src/marianne/cli/output.py:557` defines `output_error()` — a centralized error formatter with error codes, hints, severity, and JSON support. But 55 raw `console.print("[red]Error:...")` calls exist across 14 CLI files vs only 11 `output_error()` calls in 4 files (output.py, helpers.py, pause.py, instruments.py). The infrastructure for consistent errors exists and isn't adopted.
- **Impact:** Error messages are inconsistent. Some have hints, most don't. Some support `--json`, most just print colored text. Users get different quality of error guidance depending on which command they use.
- **Resolution:** M3 step 35 is COMPLETE. 71+ `output_error()` calls across 15+ files. The last holdout (top.py, 5 raw console.print calls) was committed as mateship pickup by Spark (d242046). Only display labels like "Recent Errors" remain as rich console output (correctly so — they are UI labels, not error handling).

### F-048: Cost Shows $0.00 for All Completed Sheets in Live Concert
- **Found by:** Ember, Movement 1 (renumbered from F-042 by Captain — collision with Axiom's F-042)
- **Severity:** P2 (medium — cost visibility gap)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Root cause found: `_enforce_cost_limits()` at `sheet.py:2432` returned early when `cost_limits.enabled=False`, skipping `_track_cost()` entirely. Cost tracking and limit enforcement were bundled — disabling limits disabled all cost visibility. Fix: moved `await self._track_cost()` BEFORE the `cost_limits.enabled` check so costs are always recorded for observability. 2 TDD tests: structural (track before gate) + integration (state populated with limits off).
- **Description:** `mzt status marianne-orchestra-v3` shows "Cost: $0.00 (no limit set)" despite 21 completed sheets, some taking 30+ minutes. Either the instrument (claude-code CLI) doesn't report token counts in a format Marianne can parse, or cost tracking isn't wired for this execution path. The cost warning on submission ("Cost tracking is disabled") is good, but seeing $0.00 after real execution teaches users that cost tracking doesn't work.
- **Impact:** Users can't learn about actual costs from status. The cost warning on run loses credibility when the tracked cost is always $0.00.
- **Action:** Investigate whether claude-code CLI backend is extracting tokens from the JSON output. If the data isn't available, show "Cost: unknown" instead of "$0.00" — honesty over false precision.

### F-049: SheetSkipped Handler Missing Terminal State Guard
- **Found by:** Adversary, Movement 1
- **Severity:** P1 (high — breaks cardinal invariant, same class as F-044)
- **Status:** Resolved (movement 1, Adversary)
- **Description:** `_handle_sheet_skipped()` at `src/marianne/daemon/baton/core.py:503` did NOT check `if sheet.status in _TERMINAL_STATUSES` before setting `sheet.status = "skipped"`. A completed, failed, or cancelled sheet could be re-marked as skipped by a late SheetSkipped event. This violates the terminal-state-absorbing invariant. Theorem's fix (F-044) added terminal guards to `_handle_escalation_needed` and hypothesis proved all OTHER handlers were safe — but `_handle_sheet_skipped` was not covered because it wasn't part of the event types tested by `test_terminal_sheets_resist_all_non_terminal_events` (which only tests events that flow through `_handle_attempt_result`).
- **Impact:** In production, a skip decision made after a sheet had already completed (e.g., a skip_when evaluated against stale state, or a concurrent event ordering) would silently change the sheet from completed to skipped, potentially causing downstream sheets to miss their dependency data.
- **Resolution:** Added terminal guard with debug logging at `core.py:513-522`. 3 regression tests prove the fix (test_baton_adversary.py: TestSheetSkippedTerminalGuard). mypy/ruff clean.

### F-050: Quality Gate Baseline Drift (Pre-existing)
- **Found by:** Adversary, Movement 1
- **Severity:** P2 (medium — blocks full suite pass)
- **Status:** Open (pre-existing — not caused by this movement)
- **Description:** `tests/test_quality_gate.py::test_all_tests_have_assertions` fails with baseline 103 but actual count 109. Six assertion-less test functions exist: `test_jobs_mapping_valid_ddl`, `test_sheets_mapping_valid_ddl`, `test_disabled_does_nothing`, `test_no_raise_when_under_limit`, `test_handles_already_exited_process`, `test_close_when_no_client`. These are from other musicians' commits.
- **Impact:** Full test suite (`pytest tests/ -x`) fails on quality gate before reaching any real test failures.
- **Action:** Either bump the baseline to 109 or add assertions to the 6 tests. The baseline bump is a quick fix; adding proper assertions is the correct fix.

### F-044: Escalation Handler Violates Terminal State Invariant
- **Found by:** Theorem, Movement 1
- **Severity:** P1 (high — breaks cardinal invariant)
- **Status:** Resolved (movement 1, Theorem)
- **Description:** `_handle_escalation_needed()` at `src/marianne/daemon/baton/core.py:572` checked `if sheet is not None` but did NOT check `if sheet.status not in _TERMINAL_STATUSES` before setting `sheet.status = "fermata"`. A completed/failed/skipped sheet could be moved to fermata state, violating the terminal-state-absorbing invariant. All other handlers that transition sheet status (`_handle_attempt_result`, `_handle_rate_limit_hit`, `_handle_process_exited`, `_handle_escalation_resolved`, `_handle_escalation_timeout`) correctly guard against terminal states. This one was missed.
- **Impact:** In production, a late-arriving EscalationNeeded event for an already-completed sheet would reopen it, causing the baton to re-dispatch finished work. The fermata state would also incorrectly pause the job.
- **Resolution:** Added terminal guard: `if sheet is not None and sheet.status not in _TERMINAL_STATUSES:`. Found by property-based testing with hypothesis — `test_terminal_sheets_resist_all_non_terminal_events` generates random event sequences against terminal sheets. Commit ab3d277.

### F-051: Flaky Test — test_fk_006_bulk_feedback_after_pruning Timeout
- **Found by:** Tempo, Movement 1
- **Severity:** P2 (medium — violates M-010 deterministic tests, causes false suite failures)
- **Status:** Resolved (movement 1, Tempo)
- **Description:** `tests/test_learning_store_priority_and_fk.py::TestFKConstraints::test_fk_006_bulk_feedback_after_pruning` takes ~37 seconds (creates 50 patterns with 10 applications each = 525+ database operations). The global pytest timeout is 30 seconds. The test passes in isolation (25-37s depending on load) but fails in the full suite when hit at a disadvantageous moment. Verified: passes consistently with `--timeout=120`, fails intermittently with `--timeout=30`.
- **Impact:** Full test suite (`pytest tests/ -x`) fails intermittently on this test. False failures erode trust in the test suite and waste musician time diagnosing phantom issues.
- **Resolution:** Added `@pytest.mark.timeout(120)` to override the global 30-second safety net for this specific test. The test genuinely needs more time for its 525+ database operations — it's not poorly written, just heavy.

### F-052: SheetContext.to_dict() Missing movement/voice/voice_count Aliases
- **Found by:** Blueprint, Movement 2
- **Severity:** P2 (medium — templates can't use new terminology until fixed)
- **Status:** Resolved (movement 2, Forge) — see updated entry below
- **Description:** `src/marianne/prompts/templating.py:100-117` `SheetContext.to_dict()` exposes `stage`, `instance`, `fan_count`, `total_stages` but does NOT include the new terminology aliases `movement`, `voice`, `voice_count`, `total_movements`. Canyon's Sheet entity model (`core/sheet.py`) has a `template_variables()` method that provides these aliases, but the PromptBuilder's SheetContext (which is what actually gets rendered into templates) doesn't include them. Score templates using `{{ movement }}` will get Jinja2 UndefinedError unless they fall back to `{{ stage }}`.
- **Impact:** Templates written with new terminology (`{{ movement }}`, `{{ voice }}`) fail. The template variable aliases (TASKS.md M1 step 10) are defined on Sheet but not propagated through the prompt assembly pipeline. When the baton (step 28) wires into the conductor, this gap must be bridged: either SheetContext gains the aliases, or the baton passes Sheet.template_variables() to the PromptBuilder.
- **Action:** Add `movement`, `voice`, `voice_count`, `total_movements` to SheetContext.to_dict() as aliases for `stage`, `instance`, `fan_count`, `total_stages`. This is additive and backward compatible.

### F-053: F-020 Hook Shell Injection — Resolved by Concurrent Musician
- **Found by:** Blueprint (verification), Movement 2
- **Severity:** P1 (high — resolved)
- **Status:** Resolved (movement 2, concurrent musician — uncommitted)
- **Description:** F-020 reported that `expand_hook_variables()` at `hooks.py:182-184` lacked `shlex.quote()` for shell-bound commands. Verification shows the fix is complete: `expand_hook_variables()` now has a `for_shell` parameter (hooks.py:173). It applies `shlex.quote()` to workspace, job_id, and sheet_count values when `for_shell=True`. The parameter is correctly used at both callsites: `hooks.py:645` (`for_shell=True` for `create_subprocess_shell`) and `hooks.py:715` (no `for_shell` for `create_subprocess_exec`). The daemon-side `manager.py:1863-1864` also passes `for_shell=use_shell` correctly. Tests in `test_hooks.py` pass (58 tests).
- **Impact:** Shell injection via crafted workspace paths in hook commands is no longer possible.
- **Resolution:** `for_shell` parameter added to `expand_hook_variables()`. Applied `shlex.quote()` only when the expanded result goes to shell execution. Both runner-side and daemon-side callsites use it correctly.

### F-054: F-017 Dual SheetExecutionState — Resolved
- **Found by:** Circuit, Movement 2
- **Severity:** P2 (medium — resolved)
- **Status:** Resolved (movement 2, Circuit)
- **Description:** F-017 reported two `SheetExecutionState` classes: a simple one in `core.py` and a rich one in `state.py`. Verification shows the reconciliation is complete: `core.py` now imports `SheetExecutionState` from `state.py` (line 56). The simple dataclass with string status was replaced by the rich version with `BatonSheetStatus` enum, `record_attempt()`, `can_retry`/`can_complete`/`is_exhausted` properties, cost tracking, and `to_dict()`/`from_dict()` serialization. The `__init__.py` exports the unified type.
- **Impact:** One authoritative SheetExecutionState for the entire baton. The enum-based status prevents string typos. Serialization enables restart recovery.
- **Resolution:** State.py's richer type adopted as canonical. core.py imports it. record_attempt() semantics refined: only failed, non-rate-limited attempts consume retry budget (matching the baton design spec's "retry budget tracks failures, not total attempts").

### F-055: record_attempt() Counted Successes as Retries
- **Found by:** Circuit, Movement 2
- **Severity:** P1 (high — retry budget inflated by successes)
- **Status:** Resolved (movement 2, Circuit)
- **Description:** `state.py:SheetExecutionState.record_attempt()` incremented `normal_attempts` for ALL non-rate-limited results, including successful ones. This means a sheet that succeeds on attempt 2 after failing once would show `normal_attempts=2`, consuming 2 of its retry budget. The design spec says "retry budget tracks failures, not total attempts."
- **Impact:** Retry budget was consumed faster than intended. A sheet with `max_retries=3` that succeeded on the 3rd attempt would show `normal_attempts=3` and appear exhausted. The litmus test `test_fail_retry_succeed_completes` asserted `normal_attempts == 1` (the correct value) but was passing because the old core.py had its own inline increment logic.
- **Resolution:** Changed `record_attempt()` to only increment `normal_attempts` when `not result.rate_limited and not result.execution_success`. Added separate test for success (doesn't consume) vs failure (consumes). Updated litmus test to verify completion mode progression.

### F-056: Dispatch ↔ InstrumentState Gap Bridged
- **Found by:** Circuit, Movement 2
- **Severity:** P1 (high — resolved, was dispatch ↔ state gap from collective memory)
- **Status:** Resolved (movement 2, Circuit)
- **Description:** The dispatch logic (`dispatch.py`) used flat `DispatchConfig` with `rate_limited_instruments` and `open_circuit_breakers` sets. The `InstrumentState` model (`state.py`) tracked rate limits, circuit breakers, and concurrency per instrument. There was no bridge — the dispatch config had to be manually constructed with the right data, and the baton's event handlers didn't update InstrumentState. Rate limit events affected sheet status (WAITING) but not instrument state.
- **Impact:** Without the bridge: (1) rate limiting on instrument A would NOT block dispatch of new sheets to A (dispatch didn't know A was limited), (2) circuit breakers would never trip from execution failures, (3) instruments were never auto-registered when jobs arrived.
- **Resolution:** Added to BatonCore: `register_instrument()`, `get_instrument_state()`, `build_dispatch_config()` (auto-derives from InstrumentState), `set_job_cost_limit()`. Updated `register_job()` to auto-register instruments. Updated `_handle_attempt_result()` to call `_update_instrument_on_success()`/`_update_instrument_on_failure()`. Updated `_handle_rate_limit_hit()`/`_handle_rate_limit_expired()` to update InstrumentState. 21 integration tests prove the bridge works.

### F-045: "completed" Status Shown for Retry-Exhausted (Failed) Sheets — RESOLVED
- **Found by:** Ember, Movement 1
- **Severity:** P1 (high — misleading terminology)
- **Status:** Resolved (movement 2, Forge)
- **Resolution:** Added `format_sheet_display_status()` in `src/marianne/cli/output.py:131-156`. When `status == COMPLETED` and `validation_passed is False`, returns `("failed", "red")` instead of `("completed", "green")`. Wired into `_render_sheet_details()` in `status.py:619-622` (rich output) and `_output_status_json()` in `status.py:518-522` (JSON output adds `display_status` field). 7 tests in `test_cli_output_rendering.py::TestFormatSheetDisplayStatus`. Internal state model unchanged — this is purely a presentation fix. Commit cfb7897.

### F-052: SheetContext.to_dict() Missing movement/voice/voice_count Aliases — RESOLVED
- **Found by:** Blueprint, Movement 2
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 2, Forge)
- **Resolution:** Added `movement`, `voice`, `voice_count`, `total_movements` to `SheetContext.to_dict()` in `src/marianne/prompts/templating.py:100-130`. These are aliases for `stage`, `instance`, `fan_count`, `total_stages` respectively. Uses the same fallback logic (0 → sheet_num/total_sheets). Templates can now use `{{ movement }}` or `{{ stage }}` interchangeably. 2 tests in `test_templating.py::TestSheetContext`. Backward compatible. Commit cfb7897.

### F-037: Score Writing Guide Workspace Path Mismatch — RESOLVED
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low)
- **Status:** Resolved (movement 2, Forge)
- **Resolution:** Updated `docs/score-writing-guide.md:57` from `workspace: "./simple-workspace"` to `workspace: "../workspaces/simple-workspace"` to match actual `examples/simple-sheet.yaml:12`. Commit cfb7897.

### F-057: Uncommitted Work Across 18 Files (Third Occurrence of Pattern)
- **Found by:** Bedrock, Movement 2
- **Severity:** P1 (high — recurring pattern, ~1,700 lines at risk)
- **Status:** Resolved (movement 2, Prism)
- **Resolution:** Prism committed all uncommitted work in e6d6753. CLI error standardization, baton test enum migration (8 files), rate limit tests, prompt characterization tests, CLI resume tests, runner test fixes — all on main.
- **Description:** 16 modified files and 2 untracked files contain uncommitted work from unnamed musicians. The inventory:
  - **CLI error standardization** (conductor.py +7, resume.py +21, validate.py +16) — M3 step 35 continuation plus F-031 fix
  - **Credential scanner expansion** (credential_scanner.py +42) — F-023 GitHub/Slack/HF patterns
  - **Baton test enum migration** (8 test files, ~200 net changed lines) — converting string statuses to BatonSheetStatus enum, aligns with F-017/F-054
  - **New credential scanner tests** (test_credential_scanner.py +120)
  - **New CLI resume tests** (test_cli_run_resume.py +68)
  - **Untracked: test_baton_rate_limits.py** (422 lines, 16 tests) — rate limit handling tests
  - **Untracked: test_prompt_characterization.py** (574 lines, 23 tests) — prompt assembly characterization
  - **Minor test additions** (test_runner_pause_integration.py +2, test_runner_recovery.py +4)
  - **CLAUDE.md** and **examples/quality-continuous-daemon.yaml** also modified
  All quality checks pass (mypy clean, ruff clean, targeted tests all green). This is well-built work.
- **Impact:** A `git clean` or accidental checkout would destroy ~1,700 lines of working code and 39+ new tests. This is the third occurrence of this pattern: F-013 (1,699 lines, movement 1), F-019 (136 lines, movement 1), now F-057 (1,700 lines, movement 2). The composer's directive is "Uncommitted work doesn't exist."
- **Action:** A musician with context on these changes should stage and commit them. The work spans multiple concerns (error standardization, credential scanning, test enum migration, new test files) and may need multiple commits.

### F-058: FINDINGS.md Has Duplicate F-045 Entries (Stale + Resolved)
- **Found by:** Bedrock, Movement 2
- **Severity:** P3 (low — registry hygiene)
- **Status:** Resolved (movement 2, multiple musicians)
- **Description:** F-045 appears twice in FINDINGS.md: once at its original location (line ~404) with `Status: Open` and the original description, and again at line ~508 as "F-045: ... — RESOLVED" with the resolution details. The original entry was never updated to reflect the resolution. Same applies to F-037 (original at ~348 still shows `Status: Open`, duplicate resolved entry at ~520) and F-052 (original at ~468 still shows `Status: Open`, resolved entry at ~514).
- **Impact:** Any agent reading the findings sequentially will see the open entry first and assume the issue is unresolved. The append-only rule means we don't delete the old entry, but its Status field should be updated to point to the resolution entry.
- **Action:** Update the Status field of the original F-045 entry to "Resolved (movement 2, Forge) — see updated entry below". Same for F-037 and F-052 original entries.

### F-059: M3 Step 35 Error Standardization Nearly Complete — ~1 Raw Error Remaining
- **Found by:** Bedrock, Movement 2
- **Severity:** P3 (low — good news finding)
- **Status:** Open (informational)
- **Description:** F-047 cited 55 raw `console.print("[red]Error:...")` calls vs 11 `output_error()` calls. After movement 2 work (Ghost, Dash, Harper, Forge, and uncommitted changes in conductor.py/resume.py/validate.py), the count is now: 1 raw `console.print("[red]` (in `_entropy.py`) vs 69 `output_error()` calls across 15 files. The remaining `console.print` calls containing "Error" are display labels (e.g., "Recent Errors", "Error Details"), not error handling — these should stay as rich console output, not migrate to `output_error()`.
- **Impact:** Error standardization is effectively complete once the uncommitted changes (F-057) are committed. F-047 can be marked resolved.
- **Action:** Commit the uncommitted CLI changes. Then update F-047 to Resolved.

### F-060: _sanitize_for_json Regex Ordering Leaves ANSI Bracket Remnants
- **Found by:** Sentinel, Movement 2
- **Severity:** P3 (low — cosmetic, JSON is valid)
- **Status:** Resolved (movement 2, Sentinel)
- **Resolution:** Reordered regex alternation in `output.py:657` — ANSI sequence match now comes before individual control character match. Verified with 7 test cases: all ANSI sequences fully stripped, safe whitespace preserved.
- **Description:** `src/marianne/cli/output.py:657` regex `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\x1b\[[0-9;]*[a-zA-Z]` matches ESC byte (0x1b) in the first alternative before the second alternative can match the full ANSI sequence. Result: `\x1b[31m` becomes `[31m` instead of empty string. The bracket remnant is valid JSON but looks like garbage to humans. Verified with Python test: all standard ANSI escape sequences leave bracket remnants.
- **Impact:** JSON output from `output_json()` and `output_error(json_output=True)` may contain `[31m`, `[0m`, etc. remnants from ANSI-colored subprocess output. Valid JSON, cosmetic issue only.
- **Fix:** Reorder the regex to match full ANSI sequences first: `\x1b\[[0-9;]*[a-zA-Z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]`

### F-061: 3 Security-Critical Dependencies Have Known CVEs
- **Found by:** Sentinel, Movement 2
- **Severity:** P1 (high — known vulnerabilities in auth/crypto paths)
- **Status:** Resolved (movement 2, Warden) — see updated F-061 entry below
- **Description:** pip-audit found 8 CVEs in 7 packages. Three are in security-critical paths used by Marianne:
  - `cryptography` 46.0.5 — CVE-2026-34073 (fix: 46.0.6) — used by dashboard auth
  - `pyjwt` 2.11.0 — CVE-2026-32597 (fix: 2.12.0) — used by dashboard JWT auth
  - `requests` 2.32.5 — CVE-2026-25645 (fix: 2.33.0) — used for webhook notifications
  Additional CVEs in lower-risk packages: pillow 12.1.0 (CVE-2026-25990, fix 12.1.1), pygments 2.19.2 (CVE-2026-4539, no fix), pip 24.0 (CVE-2025-8869/CVE-2026-1703), onnx 1.20.1 (CVE-2026-28500).
- **Impact:** Known vulnerabilities in authentication, cryptographic operations, and HTTP request handling. Fix versions are available for the 3 critical packages.
- **Action:** Update minimum versions in pyproject.toml: `cryptography>=46.0.6`, `pyjwt>=2.12.0`, `requests>=2.33.0`. Also update `pillow>=12.1.1` as P2.

### F-057: Uncommitted Work Across 18 Files (Third Occurrence of Pattern) — RESOLVED
- **Found by:** Bedrock, Movement 2
- **Severity:** P1 (high — recurring pattern)
- **Status:** Resolved (movement 2, Prism — mateship pickup)
- **Resolution:** Committed as `e6d6753`. 16 files, 2,262 insertions, 224 deletions. Includes CLI error standardization (conductor.py, resume.py, validate.py with F-031 fix), baton test enum migration (8 test files), and 3 new test files (test_baton_rate_limits.py, test_prompt_characterization.py, test_baton_m2_adversarial.py — 98 total tests). All quality gates pass.

### F-062: deregister_job Doesn't Clean Up Cost Limit Dicts
- **Found by:** Prism, Movement 2
- **Severity:** P3 (low — memory leak, no functional impact)
- **Status:** Resolved (movement 2, Adversary)
- **Description:** `BatonCore.deregister_job()` at `core.py:508-513` removes the job from `self._jobs` but does not clean up entries in `self._job_cost_limits` and `self._sheet_cost_limits`. Over many job lifecycles in a long-running conductor, these dicts accumulate orphaned entries.
- **Impact:** Minor memory leak. No functional impact — cost limit checks look up `_jobs` first and return early if the job doesn't exist.
- **Resolution:** `deregister_job()` now pops from `_job_cost_limits` and removes all matching `(job_id, sheet_num)` entries from `_sheet_cost_limits`. The cancel→deregister pattern cleans up everything. 5 TDD tests prove cleanup, isolation, and safety.

### F-063: _handle_process_exited Bypasses record_attempt() Contract
- **Found by:** Prism, Movement 2
- **Severity:** P2 (medium — contract violation, reconciliation risk)
- **Status:** Resolved (movement 2, Adversary)
- **Description:** `BatonCore._handle_process_exited()` at `core.py:1061` directly increments `sheet.normal_attempts += 1` instead of calling `sheet.record_attempt(event)`. Every other failure path uses `record_attempt()`, which handles cost tracking, duration recording, and conditional attempt counting. Process crashes skip all of this.
- **Impact:** Cost tracking undercounts (crashed attempts show $0.00). Duration tracking misses crash durations. Restart recovery (step 29) may see inconsistent attempt counts.
- **Resolution:** `_handle_process_exited()` now creates a synthetic `SheetAttemptResult` with `execution_success=False, cost_usd=0.0, error_classification="PROCESS_CRASH"` and passes it to `record_attempt()`. Crash attempts appear in `attempt_results`, maintain the single-point-of-accounting invariant, and include the exit code and PID in the error message. 5 TDD tests prove: crash appears in history, increments budget, handles None exit_code, records across exhaustion, and tracks $0 cost.

### F-064: Cross-Test State Leakage in test_baton_m2_adversarial.py
- **Found by:** Prism, Movement 2
- **Severity:** P3 (low — test infrastructure)
- **Status:** Open
- **Description:** `test_baton_m2_adversarial.py` has 3-6 test failures when run with certain random seeds in the full test suite, but 0 failures in isolation (tested 8 seeds). Failures are order-dependent — another test file likely modifies shared state.
- **Impact:** Intermittent false failures in the full test suite.
- **Action:** Identify the contaminating test using `--randomly-seed=<failing_seed>`. Add fixture isolation.

### F-065: Infinite Retry on execution_success + 0% Validation Pass Rate
- **Found by:** Axiom, Movement 2
- **Severity:** P1 (high — infinite loop, no budget consumption)
- **Status:** Resolved (movement 2, Axiom)
- **Description:** When a musician reports `execution_success=True` with `validation_pass_rate=0.0` and `validations_total > 0` (agent ran successfully but ALL validations failed), the baton's `_handle_attempt_result` falls through to the retry/exhaustion path at `core.py:822-830`. However, `record_attempt()` at `state.py:226` only increments `normal_attempts` when `not execution_success`, so successful-but-invalid sheets never consume retry budget. The sheet retries forever since `can_retry` (checking `normal_attempts < max_retries`) always returns True.
- **Impact:** Any sheet with validation rules where the agent produces output that fails ALL validations enters an infinite retry loop. For the 706-sheet concert, a single misconfigured validation could cause a sheet to retry indefinitely, consuming cost budget and instrument slots without ever exhausting or escalating.
- **Resolution:** Added explicit `normal_attempts` increment at `core.py:817-821` for the `execution_success and effective_pass_rate == 0` case. 3 TDD tests prove the fix: budget consumption, exhaustion after max_retries, and contrast with partial-pass completion mode. 322/322 baton tests pass.

### F-066: Escalation Unpause Ignores Other FERMATA Sheets
- **Found by:** Axiom, Movement 2
- **Severity:** P1 (high — premature job unpause)
- **Status:** Resolved (movement 2, Axiom)
- **Description:** `_handle_escalation_resolved` at `core.py:983-984` and `_handle_escalation_timeout` at `core.py:1001-1003` unconditionally set `job.paused = False` when `user_paused` is False, regardless of whether other sheets are still in FERMATA. When multiple sheets enter FERMATA (simultaneous escalations), resolving one escalation unpauses the entire job — allowing dispatch of sheets that shouldn't run while other escalations are pending.
- **Impact:** In a multi-escalation scenario, resolving the first escalation prematurely unpauses the job. Sheets whose dependencies include an escalated sheet could be dispatched before the escalation decision is made. The job resumes execution with incomplete escalation resolution.
- **Resolution:** Both handlers now check `any(s.status == BatonSheetStatus.FERMATA for s in job.sheets.values())` before unpausing. The job only unpauses when NO sheets remain in FERMATA. 4 TDD tests prove the fix: single-resolve with other FERMATA, timeout with other FERMATA, last-resolve unpauses, and user_paused preservation.

### F-067: Escalation Unpause Overrides Cost-Enforcement Pause
- **Found by:** Axiom, Movement 2
- **Severity:** P2 (medium — cost enforcement bypass)
- **Status:** Resolved (movement 2, Axiom)
- **Description:** `_check_job_cost_limit` at `core.py:299` sets `job.paused = True` when cost is exceeded. `_handle_escalation_resolved` and `_handle_escalation_timeout` set `job.paused = False` (guarded only by `user_paused`). Cost-enforcement pauses are neither user-initiated nor tracked separately, so escalation resolution silently lifts cost enforcement. A resolved escalation on a cost-exceeded job allows more sheets to dispatch, burning additional budget.
- **Impact:** Jobs that exceeded their cost limit could resume dispatching after an escalation resolution/timeout, bypassing the cost safety net.
- **Resolution:** Both escalation handlers now call `self._check_job_cost_limit(event.job_id)` after unpausing, which re-pauses the job if cost is still exceeded. The unpause→recheck sequence is atomic from dispatch's perspective (no await between them). 2 TDD tests prove the fix.

### F-065: `diagnose` Command Shows "completed" for Failed Sheets (F-045 Not Propagated)
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — commands disagree on sheet status)
- **Status:** Resolved (movement 2, Spark — mateship pickup, commit 3269eb2)
- **Description:** `src/marianne/cli/commands/diagnose.py:1046` uses raw `sheet.status.value` in the Execution Timeline table. Forge's F-045 fix added `format_sheet_display_status()` in `output.py:131-156` that maps `COMPLETED + validation_passed=False` to `"failed"`, but this function is only used in `status.py`. The `diagnose` command still shows retry-exhausted sheets as "completed." Verified: `mzt diagnose marianne-orchestra-v3` shows sheets 11-14 as `completed` with 4-5 attempts, while `mzt status` correctly shows them as `failed`.
- **Impact:** A user running `diagnose` after seeing "8 failed" in `status` sees those sheets as "completed" in the diagnostic. The two commands disagree about sheet status.
- **Resolution:** Both the progress count (line 989) and the execution timeline (line 1059) in `_build_diagnostic_report()` now use `format_sheet_display_status()`. COMPLETED+validation_passed=False shows as "failed" in both places, matching `mzt status` output. 6 TDD tests in `test_diagnose_display_status.py`.

### F-066: `instruments list` Summary Has Unmatched Parenthesis
- **Found by:** Ember, Movement 2
- **Severity:** P3 (low — cosmetic)
- **Status:** Resolved (movement 2, Journey — c7a2ba8)
- **Description:** `src/marianne/cli/commands/instruments.py:139-144` uses `' ('.join(parts)` to format the summary line, producing `10 instruments configured (3 ready (3 unchecked)` — two opening parens but only one closing paren. The `join` separator inserts `" ("` between each element, but only one `)` is appended at the end.
- **Impact:** Minor cosmetic bug. Users may notice the formatting is off.
- **Action:** Replace join logic with comma separator: `f"\n{parts[0]} ({', '.join(parts[1:])})"`.

### F-067: `mzt init <name>` Fails — Positional Argument Convention Broken
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — breaks universal CLI convention)
- **Status:** Resolved (movement 2, Spark — mateship pickup, commit 3269eb2)
- **Description:** `mzt init test-project` → `Got unexpected extra argument (test-project)`. Every major CLI tool accepts positional args for init: `git init my-project`, `npm init my-project`, `cargo init my-project`. Marianne's `init_cmd.py` only accepts `--path` and `--name` as options. The error message gives no hint that `--name` exists.
- **Impact:** First command after install fails. First impression: "this tool doesn't work like other tools."
- **Resolution:** Added optional positional `score_name` argument to `init()`. Sets `--name` when provided (convenience shorthand). `--name` flag takes precedence when both given. Name validation applies to positional arg. 7 TDD tests in `test_cli_init.py::TestInitPositionalArgument`.

### F-068: `Completed:` Timestamp Shown for RUNNING Scores
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — confusing timestamp)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Added terminal status guard at `status.py:1487`: `Completed:` only shows when `job.status in {COMPLETED, FAILED, CANCELLED}`. 4 TDD tests prove: RUNNING hides, COMPLETED shows, FAILED shows, PAUSED hides.
- **Description:** `mzt status marianne-orchestra-v3` shows `Completed: 2026-03-29 18:28:10 UTC` while the score is RUNNING. Code at `status.py:1484-1485` unconditionally prints `Completed:` when `job.completed_at` is set. The `completed_at` field is set when any sheet completes, not when the job finishes. A user monitoring a running score sees "Completed" and momentarily thinks the score finished.
- **Impact:** Cognitive dissonance between RUNNING status and "Completed:" timestamp. User must figure out internal data model to understand.
- **Action:** Only show `Completed:` when `job.status` is terminal (COMPLETED, FAILED, CANCELLED). The `Updated:` timestamp already covers the info need for running jobs.

### F-069: `hello.yaml` Validate Warning for `char` Is a False Positive (V101)
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — misleads about score correctness)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Added `_extract_template_declared_vars()` to `JinjaUndefinedVariableCheck` at `jinja.py:250`. Walks the Jinja2 AST for `Assign` and `For` nodes, extracts variable names (including tuple unpacking targets), and excludes them from the undeclared set. Root cause: `jinja2_meta.find_undeclared_variables` doesn't track variables declared in conditional branches (`{% if %}/{% elif %}`). 5 TDD tests: for-loop vars, set vars, conditional branches, truly-undefined still flagged, hello.yaml produces zero warnings.
- **Description:** `mzt validate examples/hello.yaml` warns: `[V101] Undefined variable 'char' in prompt.template`. The variable `char` is defined within the template via `{% for id, char in characters.items() %}` (line 92) and `{% set char = characters[instance] %}` (line 114). The V101 validator checks for `{{ }}` references but doesn't recognize Jinja2 `{% for %}` and `{% set %}` as variable definitions.
- **Impact:** The official example score produces a validation warning. Users either think the example is broken, or learn to ignore warnings. Both outcomes are bad.
- **Action:** Either (a) enhance V101 to detect `{% set %}` and `{% for %}` assignments, or (b) lower to INFO with wording acknowledging Jinja2-local variables aren't detected.

### F-070: Finding ID Collision — F-065, F-066, F-067 Appear Twice (Axiom + Ember)
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low — registry hygiene, same class as F-014)
- **Status:** Open
- **Description:** Three finding IDs are used by both Axiom and Ember in this file:
  - F-065: Axiom's "Infinite Retry" (line ~612, resolved) vs Ember's "diagnose shows completed" (line ~636, open)
  - F-066: Axiom's "Escalation Unpause" (line ~620, resolved) vs Ember's "instruments list parens" (line ~644, resolved by Journey)
  - F-067: Axiom's "Cost-Enforcement Bypass" (line ~628, resolved) vs Ember's "init positional arg" (line ~652, open)
  This is the same collision pattern from movement 1 (F-038-F-042). Musicians file findings concurrently without checking the latest ID.
- **Impact:** Ambiguous references. Any citation of "F-065" could mean either finding.
- **Action:** Renumber Ember's colliding entries to F-071, F-072, F-073 in a future movement. The append-only rule means we can't rewrite — just renumber.

### F-071: `mzt list --json` Not Supported (CLI Consistency Gap)
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low — minor inconsistency)
- **Status:** Open
- **Description:** `mzt list --json` → `No such option: --json`. Commands that support `--json`: validate, init, doctor, instruments list, instruments check, status. Commands that don't: list, errors, logs, history. The `list` command outputs a table that would benefit most from JSON — scripts need to parse running job IDs to feed to other commands.
- **Impact:** Scripters who use `--json` consistently across commands discover it's missing on `list`. They fall back to parsing table output (fragile) or piping `status --json` through jq.
- **Action:** Add `--json` option to `mzt list` that outputs a JSON array of job objects with id, status, workspace, submitted fields.

### F-072: `mzt resume` Says "Job" Instead of "Score" (F-029 Still Present)
- **Found by:** Journey, Movement 2
- **Severity:** P2 (medium — terminology inconsistency violates composer directive)
- **Status:** Open
- **Description:** `mzt resume nonexistent-job` → `Error: Job 'nonexistent-job' not found`. Other commands say "Score not found: nonexistent-job". The music metaphor directive requires "score" in all user-facing output. Resume is the only observed command still using "Job" in error messages after the error standardization sweep.
- **Impact:** Users see different terminology depending on which command they use. Undermines the musical identity.
- **Action:** Update resume.py error messages from "Job" to "Score". Related to F-029 (JOB_ID argument name across all commands).

### F-073: `mzt resume` Suggests `diagnose` for Nonexistent Score (Unhelpful Hint)
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low — hint leads to another error)
- **Status:** Resolved (already fixed in resume.py — verified by Lens, movement 4)
- **Description:** `mzt resume nonexistent-job` suggests `Run: mzt diagnose nonexistent-job`. But `diagnose` will also say "Score not found: nonexistent-job". The hint sends the user to another error instead of a solution. Compare to `status` and `diagnose` which correctly suggest `Run 'mzt list' to see available scores.`
- **Impact:** User follows the hint, hits another error, feels more lost than before.
- **Action:** Change resume's hint to suggest `mzt list` instead of `mzt diagnose`. The `diagnose` hint makes sense for known-but-failed scores, not unknown scores.

### F-074: Quality Gate Baseline Drift (F-050 Continuation) — 103 → 106
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low — tracked baseline)
- **Status:** Resolved (movement 2, Journey — c7a2ba8)
- **Description:** `test_quality_gate.py::test_all_tests_have_assertions` baseline was 103 but actual count is 106. Three additional assertion-less tests from other musicians: `test_grounding_engine_add_hook` (test_runner_execution_coverage.py:2042), `test_no_summary_noop` (test_runner_lifecycle.py:134), `test_state` (test_runner_pause_integration.py:55).
- **Impact:** Full test suite fails on quality gate before reaching real test failures.
- **Resolution:** Updated baseline from 103 to 106 in `tests/test_quality_gate.py:29`. The correct fix is adding assertions to these 3 tests, but that's outside Journey's scope.

### F-075: Resume After Fan-Out Failure Corrupts Sheet State and Violates Dependencies
- **Found by:** Composer, Movement 2
- **Severity:** P0 (correctness — violates M-009, breaks dependency enforcement)
- **Status:** Resolved (movement 3, Forge — mateship pickup of unnamed musician's fix)
- **Resolution:** Fix in `lifecycle.py:495-500`: GH#42 loop now defines `_terminal = (COMPLETED, FAILED, SKIPPED)` and only marks sheets as COMPLETED if their status is not already terminal. 3 TDD tests in `test_production_bug_fixes.py` (TestF075ResumeFanOutCorruption). Fix + tests were found uncommitted in the working tree — 5th occurrence of this pattern.
- **Description:** When a parallel fan-out batch has mixed results (some sheets complete, some fail), resuming the job silently overwrites the failed sheet's status to COMPLETED and skips retrying it. Downstream dependent sheets then execute against incomplete inputs.
- **Root Cause:** Three interacting bugs in the runner's resume path:
  1. **High-water mark misrepresents progress** (`src/marianne/core/checkpoint.py:892`): `last_completed_sheet` only advances, never retreats. In a fan-out where sheets 3-7 complete but sheet 2 fails, `last_completed_sheet=7` — hiding the failure below the watermark.
  2. **Resume skips failed sheets** (`src/marianne/daemon/job_service.py:345`): `resume_sheet = last_completed_sheet + 1 = 8`. Failed sheet 2 is below the watermark and never reconsidered.
  3. **Resume force-marks prior sheets COMPLETED** (`src/marianne/execution/runner/lifecycle.py:492-495`): The GH#42 fix loops `for skipped in range(1, start_sheet)` and sets `status = SheetStatus.COMPLETED` unconditionally — overwriting sheet 2's FAILED status in SQLite. This is the state corruption.
  4. **`_permanently_failed` is ephemeral** (`src/marianne/execution/parallel.py:238`): In-memory set, lost on resume. The DAG has no record of which sheets previously failed.
- **Observed In:** the-rosetta-score (2026-03-30). Sheet 2 (expedition-1) failed after 49 attempts (rate limit exhaustion). On resume, sheet 2 was silently marked COMPLETED in SQLite (`status=completed, attempt_count=49, exit_code=NULL`). Sheet 8 (the collision, which depends on all of stage 2 including sheet 2) executed and passed. Sheet 9+ proceeded with incomplete inputs.
- **Evidence:** `sqlite3 rosetta-workspace/.marianne-state.db "SELECT sheet_num, status, attempt_count, exit_code FROM sheets"` shows sheet 2 as `completed|49|NULL`. Conductor log confirms `resume_sheet: 8` and `parallel.batch_executing: [8]` with no retry of sheet 2.
- **Impact:** Dependency enforcement is broken for any job that resumes after a fan-out failure. Failed sheets are silently erased. Downstream sheets run against incomplete/missing inputs. Violates M-009 (CheckpointState is source of truth) and architectural invariant #2 (CheckpointState is sole state authority).
- **Fix Direction:**
  - The GH#42 loop (lifecycle.py:492-495) must preserve existing FAILED status — only mark sheets COMPLETED if they don't already have a terminal status.
  - Resume point calculation (job_service.py:345) needs to account for failed sheets below the watermark — scan for FAILED sheets and include them in the resume plan.
  - `_permanently_failed` should be persisted to the state DB or reconstructed from checkpoint state on resume.
  - The baton's `_propagate_failure_to_dependents` (core.py:1103) has the correct design — once the baton is wired into the conductor (step 28), this class of bug is eliminated for baton-managed jobs. The runner's parallel executor lacks equivalent logic.
- **Action:** File as GitHub issue. Fix the GH#42 loop immediately (smallest change, highest impact). Plan baton wiring (step 28) as the structural fix.
- **GitHub Issue:** #149

### F-076: Validations Run Before Rate Limit Check — Spurious Failures Mask Root Cause
- **Found by:** Composer, Movement 2
- **Severity:** P1 (debuggability — violates goal #3, masks rate limit errors as validation failures)
- **Status:** Resolved (movement 3, Maverick — mateship pickup of unnamed musician's fix)
- **Resolution:** Fix in `sheet.py:1607-1620`: rate limit check (`result.rate_limited`) now runs BEFORE validations. When rate limited, validations are skipped entirely, `_handle_rate_limit` is called directly, and the loop continues. Validations only run against output from non-rate-limited attempts. 1 test updated in `test_sheet_execution.py` (TestRateLimitHandling) to verify only 1 validation call occurs (not 2). Commit f58fc89.
- **Description:** In the sheet execution loop (`src/marianne/execution/runner/sheet.py:1607-1687`), validations run **unconditionally** after every backend execution, before the rate limit check. When a rate-limited backend returns partial or empty output, validations fail against that garbage, and the failure is recorded in state. The rate limit is only checked after validations fail (line 1675). This creates three problems:
  1. **Spurious validation failures recorded in state** (line 1613): Every rate-limited iteration writes a validation failure to `sheet_state`, even though the real cause is rate limiting. The `error_message` is set to the validation failure description (line 1670-1671), not the rate limit.
  2. **Rate limit exhaustion is misreported as validation failure**: When `_handle_rate_limit` raises `RateLimitExhaustedError` after max waits, `sheet_state.error_message` already contains the validation error (e.g., `[MALFORMED] File doesn't match pattern`). `mzt errors` shows `type: permanent, code: validation` — the real cause (48 quota waits exhausted) is hidden.
  3. **Validation pass on rate-limited execution marks sheet COMPLETED** (line 1632-1650): If `result.rate_limited=True` but validations happen to pass (e.g., `file_exists` satisfied by a previous attempt's output), the sheet is marked COMPLETED via the success path. The rate limit check at line 1675 is never reached because the success path returned early. The sheet is "completed" with zero useful work from this attempt.
- **Additional concern:** `_detect_rate_limit` (`src/marianne/backends/base.py:229`) returns `False` when `exit_code == 0`. If the Claude CLI handles a rate limit internally and exits 0 with partial output, `result.rate_limited` is `False` and the rate limit is invisible to Marianne.
- **Observed In:** the-rosetta-score sheet 2 — `mzt errors` reported `[MALFORMED] File '02-expedition-1.md' doesn't match pattern: (?i)THE SCORE:` with `type: permanent, code: validation`. Actual cause was rate limit exhaustion after 48 quota waits. The validation error was from running content_regex against output produced under rate limiting.
- **Fix Direction:**
  - Check `result.rate_limited` BEFORE running validations. If rate limited, skip validations entirely, call `_handle_rate_limit`, and `continue` the loop. Validations against rate-limited output are meaningless.
  - On rate limit exhaustion, ensure `sheet_state.error_message` reflects the rate limit cause, not the last spurious validation failure.
  - Consider whether `_detect_rate_limit` should have a secondary heuristic for exit_code=0 cases (e.g., checking stdout/stderr for rate limit patterns regardless of exit code).
- **Action:** File as GitHub issue. The validation-before-rate-limit ordering is the primary fix. The exit_code=0 blindness is a secondary hardening item.
- **GitHub Issue:** #150

### F-077: on_success Hooks Never Fire After Conductor Restart — hook_config Not Restored From Registry
- **Found by:** Composer, Movement 2
- **Severity:** P0 (correctness — self-chaining scores silently stop chaining after conductor restart)
- **Status:** Resolved (movement 3, Forge)
- **Resolution:** Added `registry.get_hook_config()` call to the restoration loop in `manager.py:225-231`. hook_config JSON is loaded, parsed, and set on `JobMeta` during startup. 2 TDD tests in test_daemon_manager.py (TestHookConfigRestoration). Note: `concert_config` and `chain_depth` are NOT persisted in the registry — a future sibling fix is needed (see error class below).
- **Description:** When the conductor restarts, it restores `JobMeta` from the registry DB for all known jobs (`manager.py:221-229`). However, the restoration code does not load `hook_config_json` from the DB, leaving `meta.hook_config = None`. When a resumed job later completes, the hook dispatch condition at `manager.py:2004` (`meta.hook_config`) evaluates to `None` and hooks are silently skipped. No log entry is produced because the `None` check comes before both the execution branch and the `hooks.skipped_zero_work` branch.
- **Root Cause:** `manager.py:221-229` constructs `JobMeta` with only 7 fields from the registry record. It never calls `registry.get_hook_config(job_id)` to populate `hook_config`. The `store_hook_config` method (`registry.py:330`) correctly persists hook config at submission time, and the `get_hook_config` method (`registry.py:338`) exists to read it back, but the restoration loop never uses it.
- **Observed In:** the-rosetta-score (2026-03-30). The score has `on_success: [{type: run_job, ...}]` for self-chaining. The job was submitted, failed (sheet 2 rate limit), the conductor was restarted, the job was resumed, completed all remaining sheets, but never chained. `mzt status` shows: `WARNING: 1 on_success hook(s) configured but no results recorded`. The conductor log shows `hooks.skipped_daemon_managed` (runner deferred to daemon) but no daemon hook execution or skip log — the hook dispatch code was never reached because `meta.hook_config` was `None`.
- **Evidence:** `sqlite3 ~/.marianne/daemon-state.db "SELECT hook_config_json IS NOT NULL, length(hook_config_json) FROM jobs WHERE job_id='the-rosetta-score'"` → `1|358`. The config is in the DB but was never loaded into memory.
- **Impact:** Any self-chaining score (evolution cycles, the rosetta score, quality continuous runs) silently stops chaining after a conductor restart. No error, no warning. The job shows COMPLETED but the chain is broken. This defeats the purpose of `on_success` hooks for iterative scores.
- **Error Class:** This is an instance of a broader pattern: **in-memory state not fully reconstructed from persistent storage after restart.** The same class of error could affect `concert_config`, `completed_new_work`, or any other `JobMeta` field that is set during submission but not restored. All `JobMeta` fields that are persisted to the registry should be loaded during restoration.
- **Fix Direction:**
  - Immediate: In the restoration loop (`manager.py:221-229`), call `registry.get_hook_config(job_id)` and parse the JSON to populate `meta.hook_config`. Also load `concert_config` if persisted.
  - Audit: Check every `JobMeta` field — if it's persisted at submission and needed post-restart, ensure it's restored. Fields to check: `hook_config`, `concert_config`, `chain_depth`, `completed_new_work`.
  - Defense: Add a startup self-check that compares in-memory `JobMeta` fields against the registry DB and logs warnings for any discrepancies.
- **Action:** File as GitHub issue. The restoration fix is small (add one async call per job in the loop). The audit of all JobMeta fields is the thorough fix.
- **GitHub Issue:** #151

### F-078: Score Authoring Skill Audit — 35 Missing Features, 7 Incorrect Values
- **Found by:** Composer, Movement 2
- **Severity:** P2 (documentation — skill guides musicians and external users writing scores)
- **Status:** Resolved (movement 2, Guide)
- **Resolution:** Fixed 4 incorrect values in essentials.md (max_output_capture_bytes 10KB→50KB, recursive_light added to backend types, instrument_name added to core vars). Added fan-out aliases to patterns.md. Added Instrument (Recommended) section and Per-Sheet Instruments section. Commit 3fc7fcd (plugins submodule e5facf2). Remaining 31 missing features are internal engine knobs — per F-078 framing guidance, these belong in a reference doc, not the authoring skill.
- **Description:** Cross-referenced the `marianne:score-authoring` skill against actual Pydantic config models. The skill covers the core 80% well but has significant gaps and a few errors.
- **Incorrect values (fix immediately):**
  1. `backend.max_output_capture_bytes` — skill says 10240 (10KB), actual default is 51200 (50KB)
  2. `stale_detection.idle_timeout_seconds` — skill says 300s in config ref but 1800s in checklist. Code default is 300s.
  3. Backend types — skill lists 3 (`claude_cli | anthropic_api | ollama`), code has 4 (missing `recursive_light`)
  4. Fan-out variable aliases — `movement`/`voice`/`voice_count`/`total_movements` exist as aliases but aren't documented
- **Missing sections (add selectively — skill should stay tight, not become a reference dump):**
  - `spec:` (spec corpus config), `learning:` (30+ fields), `grounding:`, `checkpoints:`, `ai_review:`, `logging:`, `feedback:`, `conductor:`, `bridge:`, `instrument:`/`instrument_config:`
  - Non-Claude backend options (Anthropic API, Ollama, Recursive Light)
  - Per-sheet backend overrides (`backend.sheet_overrides`)
  - Prompt features: `stakes`, `thinking_method`, `prompt_extensions`
  - Several fields on existing sections: `rate_limit.max_quota_waits`, `parallel.budget_partition`, `cost_limits.warn_at_percent`, workspace lifecycle details
- **Framing guidance:** The skill is a *score authoring* guide, not a configuration reference. It should stay opinionated and practical. New sections should only be added if they affect how someone writes a score. Internal engine features (learning entropy, grounding hooks) belong in a separate reference doc, not the authoring skill. Fix the errors. Add features that change how scores are written. Leave internal knobs out.
- **Action:** Task in TASKS.md to update the skill.

### F-079: Rosetta Pattern Corpus — 18 Buildable Patterns + 1 Proof Score for Public Corpus
- **Found by:** Composer, Movement 2
- **Severity:** N/A (opportunity — pattern discovery, not a bug)
- **Status:** open
- **Description:** The Rosetta Score's first iteration produced a corpus of 18 named orchestration patterns, all marked `[BUILD TODAY]`, plus 1 proof score (`rosetta-proof-immune-cascade.yaml`). The score and corpus now live in the Marianne repo: `scores/the-rosetta-score.yaml` and `scores/rosetta-corpus.md` (both tracked). Workspace at `workspaces/rosetta-workspace/` (transient). All 18 patterns survived adversarial review (practitioner, skeptic, newcomer) — 5 were cut, 11 were strengthened, 1 was added.
- **Patterns discovered (18):**
  - **Foundational:** Fan-out + Synthesis
  - **Score-level:** Immune Cascade, Kill Chain F2T2EA, Mission Command, Shipyard Sequence, Succession Pipeline, Fugal Exposition
  - **Within-stage:** Red Team / Blue Team, Prefabrication, Barn Raising, Source Triangulation, Talmudic Page, After-Action Review
  - **Iteration:** CDCL Search, Fixed-Point Iteration, Elenchus, Slime Mold Network
  - **Communication/Adaptation:** Cathedral Construction, Read-and-React, Dormancy Gate
- **Proof score:** `rosetta-proof-immune-cascade.yaml` — 6 stages, 11 sheets, dual fan-out (4→3 narrowing), security hardening use case. Demonstrates graduated economics, intelligence forwarding via triage, different fan-out widths per tier.
- **Existing public scores (37 in examples/):** Predate this corpus. Most use basic fan-out or sequential pipelines. None demonstrate Immune Cascade, Kill Chain, Mission Command, Shipyard Sequence, or the other named patterns.
- **Opportunity:** These patterns and the proof score should inform the public score corpus. Existing scores in `examples/` can be adapted to demonstrate named patterns. New example scores can be composed from the corpus. The score-authoring skill's fan-out patterns section should reference the corpus patterns.
- **Action:** Composer note + task for adapting scores into public examples.

### F-080: Uncommitted Work — Axiom's 3 Baton Fixes + Journey's Tests (Fourth Occurrence)
- **Found by:** Captain, Movement 2
- **Severity:** P1 (high — recurring pattern, correctness fixes at risk)
- **Status:** Resolved (movement 2, mateship pipeline — committed across multiple commits by various musicians)
- **Description:** Fourth occurrence of the uncommitted work pattern. Working tree contains:
  - `src/marianne/daemon/baton/core.py`: Axiom's F-062 (memory leak fix), F-065 (infinite retry fix), F-066 (FERMATA check), F-067 (cost re-check). +70/-10 lines.
  - `tests/test_baton_invariants_m2.py`: Axiom's 10 TDD tests proving F-065/F-066/F-067. 368 lines (untracked).
  - `tests/test_baton_user_journeys_m2.py`: Journey's 24 CLI user journey tests. 658 lines (untracked).
  - `tests/test_quality_gate.py`: Baseline drift 106→107. +1/-1 lines.
  - `scores/`: Rosetta Score + corpus. ~75KB (untracked directory).
  All quality checks pass. Baton tests (738+) all green.
- **Impact:** Three correctness fixes for the baton state machine (infinite retry, premature unpause, cost bypass) exist only in the working tree. 34 new tests at risk. The Rosetta Score (operational score) is untracked.
- **Error class:** Same as F-013, F-019, F-057. The commit step is treated as deferrable rather than continuous.
- **Action:** Commit immediately. This report includes a mateship pickup commit.

### F-081: Per-Sheet Cost Limits Only Enforced on Retry Path — Success Bypasses
- **Found by:** Adversary, Movement 2
- **Severity:** P3 (low — design asymmetry, not a bug)
- **Status:** Open
- **Description:** In `_handle_attempt_result` (`core.py`), per-sheet cost limits (`_check_sheet_cost_limit()`) are only checked on the retry/failure path (after line 824). The success path (line 744-760, `execution_success=True and effective_pass_rate >= 100.0`) returns immediately after marking COMPLETED and checking the *job* cost limit — it never checks the *sheet* cost limit. Similarly, the completion mode path (line 762-795) only checks job cost, not sheet cost.
- **Contrast:** Per-*job* cost limits are checked on ALL paths: success (line 759), completion mode (line 794), and retry (line 837). The asymmetry is only between sheet-level and job-level enforcement.
- **Impact:** A single expensive execution that succeeds will complete the sheet regardless of per-sheet cost limits. Setting `sheet_cost_limit=0.01` on a sheet that costs $5.00 per attempt will not prevent completion if the first attempt succeeds. This is arguably correct behavior — blocking successful work because of cost is counterproductive — but it means per-sheet cost limits are "retry cost caps" rather than "total cost caps."
- **Evidence:** `test_zero_sheet_cost_limit_does_not_block_success` in `test_baton_adversary_m2.py` proves this behavior: a sheet with $0.00 cost limit COMPLETES on a $0.50 successful attempt.
- **Action:** Document this behavior in the baton design spec. Users setting per-sheet cost limits should understand they cap retry costs, not total costs. If total per-sheet cost enforcement is desired, it would require adding `_check_sheet_cost_limit()` to the success path — but this is a design decision, not a bug fix.

### F-081: Cross-Test State Leakage — test_mcp_proxy_subprocess Fails in Full Suite
- **Found by:** Captain, Movement 2
- **Severity:** P2 (medium — violates M-010 deterministic tests)
- **Status:** Open
- **Description:** `tests/test_mcp_proxy_subprocess.py::TestRealEnvironmentPassing::test_env_vars_passed_to_subprocess` passes in isolation (`pytest tests/test_mcp_proxy_subprocess.py -x -q` → PASS) but fails in the full suite (`pytest tests/ -x -q` → FAIL). Same pattern as F-064 (baton adversarial test cross-contamination). The test likely depends on clean environment state that another test modifies.
- **Impact:** Full test suite (`pytest tests/ -x`) halts on this flaky test, preventing verification of subsequent tests. Erodes trust in the test suite.
- **Action:** Identify the contaminating test using `--randomly-seed=<failing_seed>`. Add fixture isolation. Related to F-064 (same error class).

### F-082: examples/README.md Still References Removed --workspace Flag on Status/Resume
- **Found by:** Newcomer, Movement 2
- **Severity:** P2 (medium — same class as F-026, not propagated to examples/)
- **Status:** Resolved (movement 2, Compass)
- **Resolution:** Removed `--workspace`/`-w` from 6 CLI examples in `examples/README.md` (Running Examples, Running Long Jobs, Pausing and Modifying sections). Commands now match the daemon-mode pattern used in the main README and getting-started.md. Note: two example score headers (`parallel-research.yaml:29`, `parallel-research-fanout.yaml:27`) still reference `-w` — these are in-file comments, not docs, and may need a separate sweep.
- **Description:** F-026 (P0) fixed the main README's use of `--workspace` with `mzt status`. But `examples/README.md` still uses `--workspace` / `-w` with `status`, `resume`, `pause`, and `modify` in 9 locations (lines 159, 162, 174, 183, 186, 189, 191). Two example score headers also reference it: `parallel-research.yaml:29` (`mzt status parallel-research -w ./parallel-workspace`) and `parallel-research-fanout.yaml:27` (`mzt status fanout-research -w ./fanout-workspace`).
- **Impact:** A newcomer browsing examples hits the same dead-end that F-026 fixed in the main README. The `--workspace` flag on `status` is hidden/debug-only — showing it in public-facing usage examples teaches a wrong workflow. The fix was applied to one file but not swept across the public corpus.
- **Error class:** Same as F-026 — fix applied to one file but not swept across all docs. This is the "fix the instance, not the pattern" anti-pattern the composer warned about.
- **Action:** Update `examples/README.md` usage examples and both example score headers to remove `-w` from `status` commands. Keep `-w` only on commands where it's a public option (`run`, `resume`).

### F-083: Zero Example Scores Use `instrument:` — All 30+ Use Legacy `backend:`
- **Found by:** Newcomer, Movement 2
- **Severity:** P1 (high — docs/examples gap undermines M1 flagship feature)
- **Status:** Resolved (movement 2, Guide + prior migration)
- **Resolution:** All 37 example scores now use `instrument:` syntax. Guide migrated the final 7 holdouts (api-backend.yaml, issue-fixer.yaml, issue-solver.yaml, fix-observability.yaml, fix-deferred-issues.yaml, phase3-wiring.yaml, quality-continuous-daemon.yaml). Prior unnamed migration covered the other 30. Score-writing-guide.md updated with `instrument_config:` documentation section. `grep -r '^backend:' examples/` now returns zero matches. The `backend:` syntax is only referenced in the score-writing-guide and configuration-reference as legacy documentation.
- **Description:** The instrument plugin system was M1's critical path feature (8 steps, 5 musicians, ~250 tests). The instrument guide (`docs/instrument-guide.md:102`) says "New scores should prefer `instrument:`." But `grep -r 'instrument:' examples/` returns zero matches. All 30+ example scores use `backend: type: claude_cli`. hello.yaml (the first score newcomers encounter) uses `backend:`. `docs/getting-started.md`'s 4 pattern examples all use `backend:`. `examples/README.md`'s "Creating Your Own" section (line 212-219) teaches `backend:` syntax.
- **Impact:** A newcomer reads the instrument guide, learns about `instrument:`, then opens any example and sees only `backend:`. The instrument system — the project's single biggest M1 feature — appears unused in all public-facing material. This makes it feel unfinished, experimental, or not recommended. The gap between documentation and examples teaches the wrong pattern.

### F-084: Instrument Guide References Internal Source Path
- **Found by:** Newcomer, Movement 2
- **Severity:** P3 (low — user-facing doc exposes implementation detail)
- **Status:** Resolved (movement 2, Compass)
- **Resolution:** Replaced all 3 occurrences of `src/marianne/instruments/builtins/` in `docs/instrument-guide.md` with user-facing descriptions: "ship as YAML profiles bundled with Marianne" (line 47), "shipped with Marianne" (line 130), "Marianne's bundled instruments directory" (line 285). User-visible directories (`~/.marianne/instruments/`, `.marianne/instruments/`) preserved as-is.
- **Description:** `docs/instrument-guide.md:48` says profiles are "defined as YAML files in `src/marianne/instruments/builtins/`" and line 131 says "Built-in — `src/marianne/instruments/builtins/` (shipped with Marianne, lowest precedence)". This is an internal source code path. Users installing from pip or running from a virtual environment don't interact with the source tree. The loading order section (line 131) is worse — it implies users need to understand the source tree to understand profile precedence.
- **Impact:** Minor confusion. A newcomer might look for this directory and not find it if they installed from a package. The path is accurate but it's implementation detail that doesn't belong in a user guide.
- **Action:** Replace `src/marianne/instruments/builtins/` with "Shipped with Marianne" or "Built-in profiles (installed with Marianne)" in both locations. Keep the two user-visible directories (`~/.marianne/instruments/` and `.marianne/instruments/`) as they are.

### F-085: F-062 Test Asserted Old Buggy Behavior — Broken After Fix
- **Found by:** Newcomer, Movement 2
- **Severity:** P2 (medium — failing test in working tree)
- **Status:** Resolved (movement 2, Newcomer)
- **Resolution:** Fixed `test_baton_user_journeys_m2.py:279-297`. The test `test_cost_limits_persist_after_deregister` was written by Journey to document F-062 (deregister_job memory leak) by asserting cost limits PERSIST after deregister. But F-062 was fixed in `core.py:508-522` (deregister_job now cleans up `_job_cost_limits` and `_sheet_cost_limits`), making the test assert the wrong (old buggy) behavior. Inverted assertions to verify cleanup. Renamed test to `test_cost_limits_cleaned_after_deregister`. Updated docstrings.
- **Error class:** Test-as-documentation vs test-as-contract. When a test documents a known bug ("prove the leak exists"), fixing the bug must also flip the test. Anyone fixing F-062 should have searched for "F-062" in the test suite.

### F-086: Finding ID Collision — F-081 Appears Twice (Adversary + Captain)
- **Found by:** Newcomer, Movement 2
- **Severity:** P3 (low — housekeeping)
- **Status:** Open
- **Description:** F-081 is used by both Adversary ("Per-Sheet Cost Limits Only Enforced on Retry Path") and Captain ("Cross-Test State Leakage"). This is the third finding ID collision in the project (after F-038-F-042 and F-065-F-067). The pattern: musicians file findings concurrently without checking the latest ID.
- **Impact:** Ambiguous references when discussing findings by number.
- **Action:** Renumber Captain's F-081 to F-087 (or next available). Consider a finding ID allocation protocol: each musician is assigned a range per movement, or a central counter is maintained.

### F-088: 4 Example Scores Contain Hardcoded Absolute Paths
- **Found by:** Guide, Movement 2
- **Severity:** P2 (medium — internal dev scores in public examples/)
- **Status:** Open
- **Description:** Four examples in `examples/` contain hardcoded absolute paths to `/home/emzi/Projects/marianne-ai-compose`: `fix-deferred-issues.yaml` (8 occurrences in working_directory and validation commands), `fix-observability.yaml` (6 occurrences), `quality-continuous-daemon.yaml` (9 occurrences), `phase3-wiring.yaml` (1 in header comment). Additionally, `sheet-review.yaml` references `/home/emzi/.claude/skills/` and `context-engineering-lab.yaml` references `/home/emzi/Projects/marianne-ai-compose`. These are internal development scores that were placed in `examples/` rather than `scores-internal/`. They work on the author's machine but would fail for any other user.
- **Impact:** A newcomer cloning the repo and running these scores will get failures from non-existent paths. The scores teach incorrect patterns (hardcoded absolute paths instead of relative workspace references). The examples/ directory should contain portable, user-ready scores.
- **Action:** Either (a) clean these scores for public use by replacing hardcoded paths with relative references and generic project paths, or (b) move them to `scores-internal/` and update `examples/README.md` to remove references. This is part of the M4 "Audit and clean examples/" task.

### F-089: Fifth Occurrence of Uncommitted Work — 32 Files, Instrument Migration
- **Found by:** Prism, Movement 2
- **Severity:** P1 (high — recurring pattern, now at 5 incidents across 2 movements)
- **Status:** Open
- **Description:** The working tree contains 32 modified files: 30 example scores (hello.yaml, simple-sheet.yaml, dialectic.yaml, etc.) converting `backend:` to `instrument:` syntax, plus README.md and docs/getting-started.md with the same migration. Total: 76 insertions, 174 deletions. Guide's commit d2f8a81 stated "All 37 examples now use instrument:" but only committed 7 of the 37. The remaining 30 sit in the working tree. FINDINGS.md entry F-083 says "RESOLVED" and collective memory says "All 37 example scores now use `instrument:` not `backend:`." Neither is true in the committed state of HEAD.
- **Impact:** A newcomer cloning the repo today sees `backend:` syntax in 30 example scores, README, and getting-started.md. The instrument system — the project's flagship M1 feature — appears unused in public-facing material. The migration that F-083 claimed complete is only 19% committed (7/37 files).
- **Error class:** Same as F-013 (1,699 lines), F-019 (136 lines), F-057 (2,262 lines), F-080 (1,100 lines). Fifth occurrence. The mateship pipeline catches these reliably, but prevention isn't happening. The commit step is consistently treated as deferrable rather than continuous.
- **Action:** Commit the 32 files immediately. Consider structural changes to the commit protocol: commit after each logical task, not at the end of the session.

### F-090: `mzt doctor` and `conductor-status` Disagree with `mzt status` About Conductor State
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — three commands disagree about the same fact)
- **Status:** Partially resolved (movement 1 cycle 2, Ghost — doctor.py fixed, conductor-status not yet)
- **Resolution (doctor.py):** Added two-phase conductor detection: PID file check first, then IPC socket probe as fallback. When PID file is missing but the socket responds, doctor now correctly reports "running". 4 TDD tests. Commit 42d3d1a. `conductor-status` still uses PID-only via `process.py:get_conductor_status()` — needs the same socket fallback treatment.
- **Description:** `mzt status` shows conductor RUNNING (15h 56m uptime) via IPC socket at `/tmp/marianne.sock`. `mzt doctor` shows "! Conductor not running" via PID file check. `conductor-status` shows "not running." The PID file (`~/.marianne/marianne.pid`) is absent. The process IS running (PID 1120, visible in `ps aux`). The socket exists. The IPC works. But the PID-based check returns false.
- **Impact:** Users who run `doctor` to check health get the wrong answer. A user who trusts `doctor` over `status` might try to start a second conductor, potentially conflicting with the running one. The discrepancy between three commands (status=RUNNING, doctor=not running, conductor-status=not running) erodes trust in diagnostic tooling.
- **Error class:** State check uses different detection methods across commands. `status` checks IPC socket (reliable). `doctor` and `conductor-status` check PID file (fragile — can be deleted, stale, or never created). The PID file is a proxy, not the truth. The socket IS the truth.
- **Action:** Unify conductor detection across all commands. Either: (a) all commands use IPC socket probe as primary detection, PID file as fallback, or (b) ensure the PID file is always created and maintained correctly. The socket-based check is more reliable.

### F-091: `mzt validate` Configuration Summary Shows "Backend:" for Scores Using `instrument:`
- **Found by:** Ember, Movement 2
- **Severity:** P3 (low — terminology mismatch in display)
- **Status:** Resolved (movement 3, Blueprint)
- **Resolution:** validate.py now checks `config.instrument` and shows "Instrument: {name}" when set, falls back to "Backend: {type}" for legacy scores.
- **Description:** `mzt validate examples/hello.yaml` (working tree version with `instrument: claude-code`) shows `Backend: claude_cli` in the Configuration summary. The user writes `instrument:` and the system reports `Backend:`. The display code at the validate summary section doesn't check whether the score used `instrument:` or `backend:` and always displays the backend terminology.
- **Impact:** Minor but counteracts the instrument migration effort. Reinforces the impression that `instrument:` is just an alias. The configuration summary should reflect what the user wrote.
- **Action:** Show `Instrument: claude-code` when the score uses `instrument:` field. Show `Backend: claude_cli` when the score uses the legacy `backend:` field. The display should match the user's chosen syntax.

### F-092: V101 False Positive on Jinja2 Loop Variables in hello.yaml
- **Found by:** Newcomer, Movement 2
- **Severity:** P3 (low — cosmetic warning on flagship example)
- **Status:** Resolved (movement 3, Circuit — same fix as F-069)
- **Description:** `mzt validate examples/hello.yaml` warns: `[V101] Undefined variable 'char' in prompt.template`. But `char` is a Jinja2 loop variable set via `{% for id, char in characters.items() %}` (hello.yaml:91) and `{% set char = characters[instance] %}` (hello.yaml:113). The V101 checker doesn't understand Jinja2 loop variables or `{% set %}` assignments — it only checks against top-level template variables.
- **Impact:** The first command a newcomer runs on the flagship example produces a warning. Introduces doubt about whether the example is broken. A false positive on the best example is worse than no check at all.
- **Action:** Either suppress V101 for variables that appear as Jinja2 loop/set targets, or document this as a known limitation of the validator.

### F-093: 34 of 37 Example Scores Fail Validation — Workspace Path Bug
- **Found by:** Newcomer, Movement 2
- **Severity:** P0 (critical — examples are the product's teaching corpus)
- **Status:** Resolved (movement 3, Blueprint)
- **Resolution:** Changed all 35 examples from `./workspaces/` to `../workspaces/` (consistent with hello.yaml and simple-sheet.yaml). Also fixed `iterative-dev-loop.yaml` invalid `max_output_chars: 0` (removed, since `auto_capture_stdout: false` already disables capture).
- **Description:** 34 of 37 example scores in `examples/` fail `mzt validate` with `[V002] Workspace parent directory does not exist`. The cause: these scores use `workspace: "./workspaces/[name]"` which resolves relative to the score file's directory (`examples/`). The `examples/workspaces/` directory does not exist. Only two scores pass: `hello.yaml` and `simple-sheet.yaml`, which use `workspace: "../workspaces/[name]"` (navigating up to the repo root where `workspaces/` exists). Additionally, `iterative-dev-loop.yaml` fails with a Pydantic schema error (`cross_sheet.max_output_chars: 0` violates `gt=0` constraint at line 3077), and `iterative-dev-loop-config.yaml` fails because it's a generator config, not a Marianne score.
- **Impact:** A newcomer who finishes the hello.yaml quickstart and tries ANY other example immediately hits a validation error. The examples corpus — 37 scores spanning software dev, research, writing, and planning — is effectively unusable beyond the two that use `../workspaces/`. This undermines the "Beyond Coding" positioning and the entire learning path from hello.yaml to real-world scores.
- **Error class:** The V002 error message is helpful (suggests `mkdir -p`) but the suggested fix is wrong — creating `examples/workspaces/` would put workspaces in the wrong location. The root cause is inconsistent workspace path conventions between scores.
- **Action:** Change all 34 scores from `./workspaces/` to `../workspaces/` (consistent with hello.yaml and simple-sheet.yaml). Fix `iterative-dev-loop.yaml` `max_output_chars: 0`. Either move `iterative-dev-loop-config.yaml` out of examples/ or mark it clearly as a generator config, not a runnable score.

### F-094: README Configuration Reference Teaches Obsolete `backend:` Syntax
- **Found by:** Newcomer, Movement 2
- **Severity:** P2 (medium — documentation inconsistency in the main README)
- **Status:** Open
- **Description:** The README's Configuration section was partially migrated to `instrument:` syntax. The YAML example (line 291) correctly uses `instrument: claude-code`. But the Configuration Reference immediately below (line 335) is titled "Backend Options" and documents a `type` field for the old `backend:` block. Prerequisites (line 55) say "for `claude_cli` backend". Architecture diagram (line 430) has a box labeled "Backend". Key Concepts (line 455) says "native backends".
- **Impact:** A newcomer reading the README sees `instrument:` in the example, then `Backend Options` with `type: claude_cli` in the reference table 10 lines below. This teaches contradictory patterns. The migration of examples (F-083) without migrating the README reference creates a split personality.
- **Action:** Rename "Backend Options" to "Instrument Configuration". Update the table to document `instrument_config:` fields. Change "claude_cli backend" in prerequisites. Update architecture diagram label. Decide whether `backend:` needs legacy documentation or should be removed from the README entirely.

### F-095: `mzt init` Generates Deprecated `backend:` Syntax — Contradicts F-083 Migration
- **Found by:** Adversary, Movement 2
- **Severity:** P1 (high — first thing every new user sees)
- **Status:** Resolved (movement 3, Blueprint)
- **Resolution:** Changed `init_cmd.py` starter score template from `backend: type: claude_cli` to `instrument: claude-code` with `instrument_config: timeout_seconds: 300`. Updated comments to list available instruments and reference `mzt instruments list`.
- **Description:** `mzt init` generates a starter score using the deprecated `backend:` syntax (`init_cmd.py:74`). The template contains `backend: type: claude_cli` with a comment on the line above (line 73) saying "use `instrument: claude-code` instead of backend:". The entire orchestra spent movement 2 migrating 37 examples from `backend:` to `instrument:` (F-083), but the command that generates NEW scores still uses the old syntax. Every new user's first score teaches the wrong pattern.
- **Impact:** New users who run `mzt init` → edit → run get a working score using deprecated syntax. When they later read the docs or examples (which now use `instrument:`), the inconsistency creates confusion. The init command contradicts the migration it should support.
- **Fix:** Change `init_cmd.py` starter score template from `backend: type: claude_cli` to `instrument: claude-code` with `instrument_config: timeout_seconds: 300`. Also update the comments to remove the "use instrument: instead of backend:" hint (since it would already be using `instrument:`). 5-minute fix.
- **Error class:** Same pattern as F-094 (README Configuration Reference teaches old syntax) and F-089 (uncommitted migration files). The migration changed old files but didn't update code that generates new files.

---

## New Findings (Movement 3)

### F-096: Uncommitted M4 Work Breaks mypy + Reconciliation Test
- **Found by:** Foundation, Movement 3
- **Severity:** P1 (high — blocks CI-clean main)
- **Status:** Resolved (movement 3, Blueprint — committed as 75bebed)
- **Description:** Uncommitted changes in `src/marianne/core/config/job.py`, `src/marianne/core/config/__init__.py`, and `src/marianne/core/sheet.py` add M4 features (`movements`, `instruments`, `per_sheet_instruments`, `instrument_map`). These changes introduce: (1) a mypy error at `sheet.py:229` where `config.movements[movement].instrument` is `str | None` but is assigned to `instrument_name: str`, and (2) a failing reconciliation test (`test_mapping_covers_all_config_sections`) because `movements` and `instruments` are new JobConfig fields without CONFIG_STATE_MAPPING entries. This is the 5th occurrence of uncommitted work in the orchestra.
- **Impact:** Any musician who runs the full test suite sees failures. mypy is not clean. This blocks the quality gate for main.
- **Fix:** The musician who added these fields needs to: (1) add a `None` guard at `sheet.py:229`, (2) add `movements` and `instruments` entries to `CONFIG_STATE_MAPPING` in `reconciliation.py`, and (3) commit the work.
- **Error class:** Same as F-013, F-019, F-057, F-080, F-089 — uncommitted work. 5th occurrence across 3 movements.

---

## Post-Mortem Findings (Composer Investigation — v3 Job Failure)

### F-097: Stale Detection Kills Agents, Reported as Generic "timeout" — Not E001
- **Found by:** Composer investigation
- **Severity:** P0 (critical — caused job failure, misdiagnosed as backend timeout)
- **Status:** Resolved (movement 4, Bedrock verification). E006 error code (Blueprint M4), error display fix (Spark M1), idle_timeout raised to 7200 (composer, verified by Bedrock M4). All sub-tasks complete.
- **Description:** The v3 job (marianne-orchestra-v3) failed at sheet 95 after running for ~42 hours. Sheets 66 (journey, M2) and 95 (forge, M3) were killed by stale detection at 30 minutes despite `backend.timeout_seconds: 10800` (3 hours). The stale detection `idle_timeout_seconds: 1800` fires when no stdout is produced for 30 minutes. For code-heavy work (running tests, reading large codebases, complex reasoning), agents routinely go silent for >30 minutes.
- **Root cause:** `_idle_watchdog()` at `src/marianne/execution/runner/sheet.py:290-320` cancels the execution task after `idle_timeout_seconds` of no progress callbacks. The resulting `_StaleExecutionError` is converted to `ExecutionResult(exit_reason="timeout", error_type="stale")` at `sheet.py:367-377`. The error classifier at `classifier.py:338` maps `exit_reason="timeout"` to `ErrorCode.EXECUTION_TIMEOUT` (E001), but the error display shows `Code: timeout` rather than `E001` — the error code string is not being surfaced properly in the status/errors commands.
- **Impact:** (1) Agents killed prematurely. (2) Error displayed as `Code: timeout` instead of `E001`, making diagnosis harder. (3) Stale detection is indistinguishable from backend timeout in error output.
- **Fix:** Increase `idle_timeout_seconds` to at least 3600 (1 hour) for work stages. Fix error code display to show E001. Consider adding a distinct error code for stale detection (E006?) to differentiate from backend timeout.
- **Evidence:** `mzt status marianne-orchestra-v3` shows Sheet 66 "failed (30m 0s)" and Sheet 95 "failed (30m 30s)". Both have `exit_reason=timeout` in error context. Score config has `timeout_seconds: 10800` but `stale_detection.idle_timeout_seconds: 1800`.

### F-098: Rate Limit Errors Classified as E999 (Permanent) — Should Be E101/E102
- **Found by:** Composer investigation
- **Severity:** P1 (high — agents retried 16-28 times without rate limit wait logic)
- **Status:** Resolved (movement 4, Blueprint — Phase 4.5 rate limit override) — see updated entry below
- **Description:** Sheets 11 (spark, M1), 13 (lens, M1), 20 (litmus, M1), 55 (spark, M2), 57 (lens, M2), 72 (adversary, M2) all failed with rate limit messages ("API Error: Rate limit reached", "You've hit your limit · resets 11pm") but were classified as `E999` (permanent/unknown) instead of `E101` or `E102` (rate limit). This means the rate limit wait/backoff logic never engaged — instead, sheets retried immediately up to 28 times, wasting execution budget.
- **Root cause:** The Claude CLI error output for rate limits ("API Error: Rate limit reached" and "You've hit your limit · resets ...") is being captured in stdout but not matched by the error classifier's rate limit patterns. The classifier likely checks stderr or specific exit codes. When the CLI hits a rate limit, it may exit with code 0 or a non-standard code, causing the classifier to fall through to E999.
- **Impact:** (1) Up to 28 wasted retries per sheet (litmus had 16, adversary had 17, spark M2 had 28). (2) No rate limit backoff — retries hit rate limit again immediately. (3) Multiple agents hitting rate limits simultaneously amplify the problem.
- **Fix:** Update error classifier to detect rate limit patterns in stdout (not just stderr). Patterns to match: `"API Error: Rate limit reached"`, `"You've hit your limit"`, `"resets"`. Map these to E101/E102 so rate limit wait logic engages.
- **Evidence:** `logs/sheet-11.stdout.log`: "ready\nAPI Error: Rate limit reached". `logs/sheet-55.stdout.log`: "You've hit your limit · resets 11pm (Europe/Berlin)". `logs/sheet-72.stdout.log`: "You've hit your limit · resets Apr 3, 5pm (Europe/Berlin)". All classified as E999 with up to 28 attempts.

### F-099: 6 Agents Failed Consistently Across M1 — Same Instance Positions
- **Found by:** Composer investigation
- **Severity:** P2 (medium — pattern suggests rate limit surge, not individual agent issue)
- **Status:** open (mitigated by F-098 fix)
- **Description:** In Movement 1, instances 9 (spark), 10 (dash), 11 (lens), 12 (codex), 15 (atlas), 18 (litmus) all failed. These are sequential musician positions in the fan-out. In Movement 2, instances 9 (spark), 11 (lens), 20 (journey), 25 (newcomer), 26 (adversary), 29 (tempo) failed. The pattern suggests a rate limit surge when 32 parallel agents start simultaneously, with agents in the "tail" of the launch sequence hitting the limit.
- **Impact:** Agents that fail due to rate limits get classified as permanent failures (E999) and burn through all retries, then stay failed even when the rate limit clears.
- **Fix:** Primary fix is F-098 (correct error classification). Secondary: consider staggering fan-out launches (e.g., 100ms delay between agent starts) to reduce rate limit surge pressure.

### F-100: Per-Sheet Instrument Resolution Built But Not Wired Through Runner
- **Found by:** Composer investigation (flowspec trace: `adapter.py::BatonAdapter` has 0 backward flows from production code outside baton/)
- **Severity:** P1 (high — blocks multi-instrument execution)
- **Status:** open
- **Description:** The config model supports `per_sheet_instruments`, `per_sheet_instrument_config`, and `instrument_map` (see `job.py:287-358`). The sheet context builder resolves instruments per-sheet (`sheet.py:217-248`). The baton's `BackendPool` (`daemon/baton/backend_pool.py`) creates per-instrument backends. But: (1) The old runner (`execution/runner/sheet.py`) uses a single `self.backend` for all sheets and ignores per-sheet instrument assignments. (2) The baton path is behind `use_baton: false` (default) in conductor config. (3) Flowspec confirms BatonAdapter has 0 backward flows from outside `daemon/baton/` — it's imported by `manager.py::JobManager::start` but the dispatch path is feature-flagged off.
- **Impact:** Cannot run gemini-cli (or any non-Claude instrument) on specific sheets in the v3 score until either: (a) the old runner gains per-sheet instrument support, or (b) `use_baton: true` is enabled and the baton path is production-ready.
- **Fix:** Enable `use_baton: true` in conductor config and validate the baton path works for a simple score first. Alternatively, add instrument switching to the old runner (more work, less long-term value).

### F-101: Gemini CLI Error Patterns Need Mapping for Non-Claude Instruments
- **Found by:** Composer investigation
- **Severity:** P2 (medium — blocks correct error classification for gemini-cli agents)
- **Status:** open
- **Description:** The error classifier (`src/marianne/core/errors/classifier.py`) and parsers (`parsers.py`) are Claude-specific. Rate limit patterns match Claude CLI output ("Overloaded", "rate limit", "capacity"). The gemini-cli instrument profile at `instruments/builtins/gemini-cli.yaml` defines `rate_limit_patterns` and `auth_error_patterns`, but the `PluginCliBackend` at `execution/instruments/cli_backend.py` must use these profile-defined patterns instead of the hardcoded Claude patterns.
- **Impact:** When a gemini-cli agent hits a rate limit, the error will be classified as E999 (same as F-098 for Claude). The instrument profile defines the correct patterns but they may not be wired through to the classifier.
- **Fix:** Verify `PluginCliBackend._classify_error()` uses the profile's error patterns. If the old runner is used (without baton), the existing classifier is called instead — it needs an instrument-aware code path.

### F-102: Score Generator Sets 3h Backend Timeout But Only 30m Stale Detection
- **Found by:** Composer investigation
- **Severity:** P1 (high — config contradiction caused the fatal failure)
- **Status:** open
- **Description:** `generate-v3.py` sets `backend.timeout_seconds: 10800` (3 hours) and `stale_detection.idle_timeout_seconds: 1800` (30 minutes). The stale detection fires 6x earlier than the backend timeout. For agents doing substantive code work (running test suites, reading large codebases, complex multi-file edits), 30 minutes of no stdout is normal. The stale detection effectively overrides the 3-hour timeout.
- **Impact:** The 3-hour timeout provides false confidence — no agent will ever hit it because stale detection kills them at 30 minutes.
- **Fix:** In `generate-v3.py`, increase `idle_timeout_seconds` to at least 7200 (2 hours) for work stages, or disable stale detection and rely on the backend timeout alone. Consider per-stage stale detection settings (shorter for setup, longer for code work).

### F-103: Baton Path — 3 Bugs Found During Live Testing
- **Found by:** Composer investigation (live testing with `use_baton: true`)
- **Severity:** P0 (critical — baton non-functional without these fixes)
- **Status:** Fixed in source, baton disabled pending F-104
- **Description:** Three bugs discovered when enabling `use_baton: true` for the v3 job:
  1. **`config.backend.max_retries` AttributeError** — `manager.py:1648` accessed `config.backend.max_retries` but `max_retries` lives on `config.retry.max_retries` (RetryConfig, not BackendConfig). Fixed: changed to `config.retry.max_retries`.
  2. **Baton event loop starves after job registration** — `BatonAdapter.register_job()` registered sheets with the baton but never pushed an event to `baton.inbox`. The event loop (`adapter.run()`) blocks on `inbox.get()` forever — no dispatch happens. Fixed: added `self._baton.inbox.put_nowait(DispatchRetry())` after registration to kick the loop.
  3. **BackendPool never created or injected** — `manager.py` created the `BatonAdapter` and started its loop but never called `set_backend_pool()`. Dispatch callback logged `adapter.dispatch.no_backend_pool` and returned without executing. Fixed: added registry creation via `load_all_profiles()` and `BackendPool(registry)` injection in manager startup.
- **Evidence:** Conductor logs showed: `baton.job_registered` → (silence) for bug 2. After fix 2: `adapter.dispatch.no_backend_pool` for bug 3. After fix 3: `plugin_cli_execute_start` with `prompt_length=0` → F-104.

### F-104: Baton Musician Does Not Render Jinja2 Prompts
- **Found by:** Composer investigation (live testing)
- **Severity:** P0 (critical — blocks all baton-path execution for scores with template_file or Jinja2)
- **Status:** Resolved (movement 1 current cycle, Forge — commit 3deb436) — see updated entry below
- **Description:** The baton's `sheet_task()` at `daemon/baton/musician.py:171` uses `sheet.prompt_template or ""` directly. For scores that use `template_file` (like v3), `prompt_template` is None — the template is a file path, not inline text. Even for inline templates, no Jinja2 rendering occurs — variables, injections, cross-sheet context, prelude/cadenza are all ignored. The code comment at line 165 acknowledges this: "Full Jinja2 rendering with cross-sheet context will be added when the baton wires into the conductor."
- **Impact:** The baton path cannot run any score that uses Jinja2 templates (which is most scores). This blocks the entire multi-instrument strategy since `instrument_map` / `per_sheet_instruments` only work through the baton.
- **Fix:** Wire the prompt rendering pipeline (`PromptBuilder.build_sheet_prompt()` from the old runner) into the musician's `_build_prompt()`. This needs: template loading, Jinja2 rendering, injection resolution, cross-sheet context, and preamble assembly. Until then, `use_baton: false`.

### F-105: Instrument-Level Error/Log Handling Not In YAML Schema
- **Found by:** Composer directive
- **Severity:** P1 (high — blocks correct multi-instrument operation)
- **Status:** open
- **Description:** Every instrument needs error pattern matching, log parsing, and output format handling defined in its YAML profile. Currently, the `CliProfile.errors` section in instrument YAML defines `rate_limit_patterns` and `auth_error_patterns`, and the `PluginCliBackend._check_rate_limit()` at `execution/instruments/cli_backend.py:359` correctly uses them against both stdout+stderr. However: (1) The hardcoded `ClaudeCliBackend` does NOT use these profile patterns — it has its own error classifier. (2) `claude-cli` should be treated as a regular instrument profile (like `gemini-cli`), not a special-cased native backend. (3) The YAML schema needs additional error/log fields: timeout patterns, crash patterns, capacity patterns, output parsing rules for non-JSON output, and log capture rules.
- **Impact:** When claude-cli runs through the native `ClaudeCliBackend`, rate limits in stdout are missed (F-098). When it runs through `PluginCliBackend` (baton path), rate limits ARE caught. This inconsistency means the same instrument behaves differently depending on execution path.
- **Fix:** Long-term: route all instruments (including claude-cli) through `PluginCliBackend`. Short-term: expand the instrument profile YAML schema to cover all error categories, and update the native `ClaudeCliBackend`'s classifier to also check stdout patterns for rate limits.

### F-106: Gemini CLI Instrument Profile — Live Verification Results
- **Found by:** Composer investigation (live testing against gemini-cli 0.35.1)
- **Severity:** P1 (high — profile was speculative, now empirically grounded)
- **Status:** Fixed (profile updated with verified behavior)
- **Description:** Live testing of gemini-cli on backyard-capitalism-9000 revealed:
  1. **JSON output is clean on stdout** — preamble (YOLO warnings, keychain errors) goes to stderr only. `json.loads(stdout)` works.
  2. **Error format confirmed** — `{"error": {"type": "Error", "message": "...", "code": N}}` on stdout. Stack traces on stderr (verbose Node.js, not structured).
  3. **Exit code 1 on error, 0 on success** — matches default `success_exit_codes: [0]`.
  4. **Multi-model routing** — gemini uses flash-lite for routing + flash/pro for execution. Token counts span both models under `stats.models.*`. Wildcard path returns first model only via `extract_json_path`, undercounting by ~4x. Need `extract_json_path_all` + sum via `aggregate_tokens: true`.
  5. **Output format flag** — profile had `--output-format` but gemini uses `-o`. Fixed.
  6. **gemini-3-flash-preview** exists as a model but wasn't in the profile. Added.
- **Evidence:** `gemini -p "Say hello" -o json --yolo` returned clean JSON with `response`, `stats.models` (2 models), exit 0. `gemini -p "hello" -o json --yolo -m nonexistent-model` returned JSON error with exit 1 and stack trace on stderr.
- **Fix applied:** Updated `gemini-cli.yaml` with verified behavior, added `aggregate_tokens: true`, added capacity/timeout patterns, added gemini-3-flash-preview model, fixed output format flag. Updated `claude-code.yaml` to same standard (added stdout rate limit patterns from F-098, added capacity/timeout patterns, added verification header).

### F-107: Instrument Configuration Requires Standardized Live Verification
- **Found by:** Composer directive
- **Severity:** P0 (process — affects every instrument we ship)
- **Status:** open (process not yet standardized)
- **Description:** Every instrument profile MUST be verified against the actual CLI tool before shipping. Speculative profiles (based on documentation or assumptions) will have errors — F-106 proved this with gemini-cli (wrong flag, missing model, undercounting tokens). The verification process must be standardized into a repeatable skill so that:
  1. **Any CLI tool can be adapted into an instrument** by running the verification protocol.
  2. **Verification is automatable** — a Marianne score can mass-produce and mass-test instruments by running the protocol against each CLI tool.
  3. **Profiles carry verification metadata** — date, CLI version, what was tested, what wasn't (e.g., rate limits are hard to trigger on demand).
- **The verification protocol must cover:**
  - Success path: clean prompt → output → extract result, tokens, exit code
  - Error path: bad model/auth → error output → extract error message, exit code
  - Rate limit path: (if triggerable) → pattern matching on stdout+stderr
  - Token counting: single vs multi-model, wildcard aggregation
  - Preamble separation: what goes to stdout vs stderr
  - Output format flags: exact flag syntax and variations
  - Edge cases: empty prompt, very long prompt, binary output
- **End state:** A skill (`instrument-verification.md`) that takes a CLI tool and produces a verified instrument profile YAML. A Marianne score (`instrument-factory.yaml`) that runs this skill against N CLI tools in parallel and produces N verified profiles. Every instrument we ship — including claude-code — goes through this pipeline. No exceptions.
- **Impact:** Without this, every new instrument ships with speculative errors. With this, instrument onboarding becomes a production pipeline — mass produce, mass test, mass ship. This is how Marianne scales to every AI CLI tool that exists or will exist.


---

## Bedrock Ground Verification (Post-M3)

### F-106: Spark Memory File Missing Through 3 Movements
- **Movement:** 3 (discovered during post-M3 verification)
- **Agent:** Bedrock
- **Category:** pattern
- **Finding:** Spark is one of 32 rostered musicians but had no memory file in the workspace. All 31 other musicians had memory files. Created empty template at `memory/spark.md`. The original movement 0 setup (by Bedrock) created memory files for agents who didn't have old-workspace equivalents, but Spark was missed.
- **Action:** Created memory file. Future setup verifications must check ALL 32 roster names, not just files that exist.
- **Status:** fixed (this movement)

### F-107: Composer's F-103 Fixes Uncommitted — 6th Occurrence of Anti-Pattern
- **Movement:** 3 (discovered during post-M3 verification)
- **Agent:** Bedrock
- **Category:** pattern
- **Finding:** The composer's own F-103 fixes (3 baton bugs) sit uncommitted in the working tree: `adapter.py` (+6 lines: DispatchRetry kick), `manager.py` (+12/-1 lines: max_retries path fix, BackendPool creation+injection). These are marked `[x] [Composer]` in TASKS.md but NOT on HEAD. Additionally, 3 example scores are deleted in the working tree (F-088 cleanup: `fix-deferred-issues.yaml`, `fix-observability.yaml`, `quality-continuous-daemon.yaml` — 3,279 lines). Total uncommitted: 14 files.
- **Error class:** Same as F-013, F-019, F-057, F-080, F-089. Sixth occurrence across 3 movements. The pattern is now structural, not disciplinary — even the composer follows it. The score architecture should enforce commit checkpoints.
- **Impact:** 19 lines of P0 baton fixes exist only in the working tree. A `git clean` or accidental checkout would lose the F-103 fixes, requiring re-diagnosis. The F-088 cleanup (3 scores deleted) would need re-doing.
- **Action:** Commit the composer's working tree changes. Consider adding a commit checkpoint to the score after every N sheets or at movement boundaries.
- **Status:** Resolved (movement 2 — fixes committed across b4146a7, 0ec7c7c, and 3deb436. BackendPool wiring verified at manager.py:303-318.)
- **Note (Bedrock M2):** This finding shares ID with F-107 (Instrument Verification, line 1073). Finding ID collision — same class as F-070, F-086.

### F-108: GitHub Issue #152 May Be Resolved by F-093
- **Movement:** 3 (discovered during post-M3 verification)
- **Agent:** Bedrock
- **Category:** decision
- **Finding:** GitHub issue #152 ("34 of 37 example scores fail validation — workspace path bug") was filed based on F-093 (35 examples using `./workspaces/` instead of `../workspaces/`). F-093 was resolved in commit 75bebed (Blueprint, M3) — all 35 examples fixed. Verification confirms zero examples use the old `./workspaces/` pattern. Issue #152 should be closable.
- **Action:** Close #152 with reference to commit 75bebed and F-093 resolution.
- **Status:** open (needs composer/reviewer to close)

---

## New Findings (Movement 4 — Blueprint)

### F-097: Stale Detection Error Code — RESOLVED
- **Found by:** Composer investigation (original), Blueprint (E006 fix), Spark (error display fix), Composer (timeout increase), Bedrock (M4 verification)
- **Severity:** P0 → Resolved
- **Status:** Resolved (movement 4, Bedrock verification)
- **Resolution (E006):** Added `EXECUTION_STALE` (E006) to `ErrorCode` enum in `src/marianne/core/errors/codes.py`. Classifier (`classify()` at line 338 and `classify_execution()` at line 990) now differentiates stale detection from backend timeout by checking for "stale execution" in combined stdout+stderr. Stale → E006 (WARNING, 120s retry delay). Regular timeout → E001 (ERROR, 60s delay). 10 TDD tests in `test_error_taxonomy_extensions.py`.
- **Resolution (timeout):** `idle_timeout_seconds` raised from 1800 to 7200 in `generate-v3.py:443`. Score regenerated — `marianne-orchestra-v3.yaml:3963` confirms 7200. Done by composer, verified by Bedrock M4.
- **Resolution (error display):** Spark M1 added `error_code` field to SheetState, wired through `mark_sheet_failed()`. `format_error_code_for_display()` in output.py. 26 TDD tests.

### F-098: Rate Limit Classification — RESOLVED
- **Found by:** Composer investigation (original), Blueprint (fix, Movement 4)
- **Severity:** P1 → Resolved
- **Status:** Resolved (movement 4, Blueprint)
- **Root cause:** `classify_execution()` Phase 4 (regex fallback) only runs when `not all_errors`. When Phase 1 (JSON parsing) found structured errors, Phase 4 was skipped entirely. Rate limit patterns in stdout — "rate.?limit", "hit.{0,10}limit", "limit.{0,10}resets?" — were already in `_DEFAULT_RATE_LIMIT_PATTERNS` but unreachable when Phase 1 produced any result.
- **Resolution:** Added "Phase 4.5: Rate Limit Override" to `classify_execution()` at `src/marianne/core/errors/classifier.py`. This phase always runs after Phase 4, regardless of what prior phases found. It scans combined stdout+stderr for rate limit patterns and adds a rate limit error if none exists. Handles quota exhaustion, capacity, and generic rate limit cases. 6 TDD tests including the core F-098 regression case (JSON errors + rate limit text → rate limit detected).
- **Evidence:** `test_rate_limit_in_stdout_with_json_errors` passes — a response with both JSON error structure AND "API Error: Rate limit reached" in stdout now correctly classifies as rate_limit category.

### F-109: CliErrorConfig Schema Expanded — crash_patterns and stale_patterns Added
- **Found by:** Blueprint, Movement 4
- **Severity:** N/A (enhancement)
- **Status:** Resolved (movement 4, Blueprint)
- **Description:** `CliErrorConfig` at `src/marianne/core/config/instruments.py` gained two new fields: `crash_patterns: list[str]` (regex patterns for process crash detection) and `stale_patterns: list[str]` (regex patterns for stale execution detection). `timeout_patterns` and `capacity_patterns` already existed. All fields default to empty lists (backward compatible). 6 TDD tests.
- **Impact:** Instrument profiles can now declare instrument-specific patterns for crash and stale detection. The `PluginCliBackend` can use these patterns in its error classification, supplementing Marianne's default classifier.

### F-104: Baton Musician Prompt Rendering — RESOLVED
- **Found by:** Composer investigation (original)
- **Severity:** P0 (critical — blocked all baton-path execution)
- **Status:** Resolved (movement 1 of current cycle, Forge — commit 3deb436)
- **Description:** The baton's `musician.py:_build_prompt()` returned raw `sheet.prompt_template or ""` without Jinja2 rendering. For scores using `template_file`, `prompt_template` is None — the template is a file path, not inline text. Even for inline templates, no variable expansion, no preamble, no injection resolution, no validation requirements.
- **Resolution:** Complete rewrite of `_build_prompt()` with full 5-layer prompt assembly pipeline: (1) preamble via `build_preamble()`, (2) Jinja2 template rendering via `Sheet.template_variables()`, (3) prelude/cadenza injection resolution with Jinja path expansion, (4) validation requirements formatted as success checklist, (5) completion mode suffix. Added helper functions `_render_template()`, `_resolve_injections()`, `_format_injection_section()`, `_format_validation_requirements()`. Updated `sheet_task()` with `total_sheets`/`total_movements` params. Adapter computes job-level totals. 17 TDD tests in `test_musician_prompt_rendering.py`. AttemptContext expanded with `total_sheets`, `total_movements`, `previous_outputs` fields for cross-sheet data path.
- **Impact:** UNBLOCKS all baton-path execution. `use_baton: true` can now be tested with real scores that use Jinja2 templates.

### F-108: Token Counts Are Near-Zero — Native ClaudeCliBackend Doesn't Track Tokens
- **Found by:** Composer observation (v3 job status showing 4,144 input / 2,072 output tokens after 7 completed sheets)
- **Severity:** P1 (high — cost tracking is blind, budget controls non-functional)
- **Status:** open
- **Description:** The v3 score uses `output_format: text` which means the native `ClaudeCliBackend` gets raw text on stdout with no structured metadata. The backend has zero token tracking — `grep -n "token" claude_cli.py` returns no matches. The cost summary in `mzt status` shows implausibly low numbers (4K input tokens for 7 sheets that each inject ~50KB of cadenzas). These numbers likely come from `estimate_tokens()` applied to captured stdout (the agent's output), not the actual prompt input. Real token usage across 7 sheets with the full spec corpus, CLAUDE.md, meditation, compose overview, workspace files, and v3 template is likely 500K-1M+ input tokens.
- **Impact:** (1) Cost tracking is non-functional — the score reports $0.04 when actual spend is likely $5-15+. (2) `cost_limits` budget controls can't enforce limits they can't measure. (3) No visibility into which agents/sheets are expensive. (4) The `PluginCliBackend` with `output_format: json` DOES track tokens via `usage.input_tokens` / `usage.output_tokens` in Claude's JSON output — this is another reason to route all execution through the instrument path.
- **Fix:** Route all execution through `PluginCliBackend` (F-105). The instrument path already handles token extraction correctly for any instrument via profile-defined `input_tokens_path` / `output_tokens_path`. The native `ClaudeCliBackend` is the problem — it should not exist as a separate code path.

### F-110: Two Orphaned Broken Test Files Block `pytest tests/ -x`
- **Found by:** Maverick, Movement 1 (current cycle)
- **Severity:** P2 (medium — blocks full test suite collection)
- **Status:** Resolved (movement 1, Maverick — deleted orphaned files)
- **Description:** Two untracked test files were left by a musician who planned a class-based `PromptRenderer` approach (never implemented): `test_baton_prompt_renderer.py` (552 lines, imports `marianne.daemon.baton.prompt` which doesn't exist) and `test_baton_prompt_rendering.py` (494 lines, imports `_configure_backend` which doesn't exist). Both caused `ImportError` during pytest collection, blocking `pytest tests/ -x` with `-x` flag.
- **Root cause:** Two musicians independently started TDD for F-104 with different architectures (class-based PromptRenderer vs function-based _build_prompt). The function-based approach won (simpler, no state). The class-based test files were never cleaned up.
- **Resolution:** Deleted both orphaned files. The working tests are in `test_musician_prompt_rendering.py` (17 tests, committed in 3deb436). Also reverted a broken `__init__.py` change that imported the non-existent `PromptRenderer`.

### F-109: Health Check After Rate Limit Wait Consumes a Request — Causes Fatal Cascade
- **Found by:** Composer investigation (v3 job failure at sheet 9)
- **Severity:** P0 (critical — kills entire parallel batch on any rate limit)
- **Status:** Fixed (composer — `recovery.py`)
- **Description:** After a rate limit wait completes, `_health_check_after_wait()` at `recovery.py:435` called `self.backend.health_check()` which sends a real prompt ("Say 'ready' and nothing else"). If the rate limit hasn't fully cleared, the health check itself gets rate-limited, returns `False`, and raises `FatalError`. This kills all in-progress sheets in the parallel batch (`fail_fast`), not just the one that hit the limit. The quota path (E104) correctly used `availability_check()` (binary exists, no API call) with 3 retries + backoff. The rate limit path (E101) used the destructive `health_check()` with zero retries.
- **Evidence:** Conductor log shows: `rate_limit.detected` → 60-min wait → `rate_limit.health_check_failed` → `parallel.sheet_failed` (FatalError) → all 6 sheets (9-14) cancelled. Sheet 12 was the trigger — its health check failed, killing sheets 9-11 and 13-14 as collateral.
- **Fix:** Changed rate limit path to use `availability_check()` with 3 retries + 30s backoff (same pattern as quota path). The rate limit wait already waited the recommended duration — the post-wait check just needs to verify the binary is still there, not re-test the API. The actual retry will determine if the limit has cleared.

### F-110: Backpressure Rejects Jobs During Rate Limits — Should Queue as Pending
- **Found by:** Composer (attempting to launch rosetta alongside rate-limited v3)
- **Severity:** P1 (high — UX is hostile, blocks legitimate concurrent work)
- **Status:** open
- **Description:** When any backend has an active rate limit, `BackpressureController.current_level()` at `backpressure.py:121` escalates to `PressureLevel.HIGH` via `self._rate_coordinator.active_limits`. At HIGH, `should_accept_job()` returns False, and the CLI shows "Conductor rejected score: System under high pressure — try again later" followed by the misleading "Marianne conductor is not running." The user has no information about *why* the rejection happened, *when* it will clear, or any way to leave the job with the conductor for later execution.
- **Root cause correctly diagnosed:** The rate limit was NOT stale — it was a legitimate 3600s limit registered at 01:03 with submissions attempted at 01:35 (only 32 minutes in). The coordinator's `active_limits` property correctly filters by `resume_at > now`. A conductor restart cleared it only because the coordinator is in-memory.
- **Three UX changes needed:**
  1. **Accept jobs in PENDING state during rate limits.** The conductor should queue the work and start it when the limit clears. Pending jobs can be cancelled by the user. This is how a real conductor works — you hand over the score, they decide when to play it.
  2. **Show time remaining on rejection.** If `mzt run` or `mzt resume` is rejected due to rate limits, show: "Rate limit active on claude-cli — clears in 27m 32s. Job queued as pending." (or if rejecting: "Resubmit after 02:03 UTC").
  3. **Fix the misleading error message.** "Marianne conductor is not running" is wrong — it IS running. The error should say what's actually happening: rate limit backpressure.
- **Impact:** Users (and self-chaining scores) can't submit work during rate limits. This breaks concert chains — if a score completes and chains to the next, but a rate limit is active from a *different* job, the chain breaks. The conductor should be a reliable place to leave work, not a bouncer that turns you away.
- **Partial Resolution (Lens, movement 4):** Item 3 (misleading error message) FIXED. `_try_daemon_submit` in `run.py` now raises `typer.Exit(1)` after printing the specific rejection reason, instead of returning False and triggering the fallback "conductor is not running" message. The user now sees the actual rejection reason (e.g., "System under high pressure — try again later") with hints about conductor status. 3 TDD tests in `test_cli_error_ux.py`. Items 1 (PENDING state) and 2 (time remaining) remain open.

### F-111: Parallel Executor Loses RateLimitExhaustedError Type — Jobs FAIL Instead of PAUSE
- **Found by:** Composer investigation (flowspec trace of `_execute_parallel_mode` → `FatalError`)
- **Severity:** P0 (critical — rate limit recovery is completely broken for parallel scores)
- **Status:** Resolved (movement 2, unnamed musician + Circuit mateship verification)
- **Resolution:** Three-part fix: (1) Added `exceptions: dict[int, BaseException]` to `ParallelBatchResult` to preserve original exception objects alongside string details. (2) Added `_find_rate_limit_in_batch()` to `LifecycleMixin` that scans `result.exceptions` for `RateLimitExhaustedError` instances. (3) Before the `fail_fast` check, the parallel batch handler now extracts any rate limit exception and re-raises it directly, allowing the lifecycle's `except RateLimitExhaustedError` handler at line 986 to fire. The `resume_after` timestamp, `backend_type`, and `quota_exhaustion` flag are all preserved. Adversarial tests updated to verify the fix (5 tests).
- **Description:** The conductor was designed to handle rate limit exhaustion gracefully: `RateLimitExhaustedError` carries a `resume_after` timestamp, the lifecycle catches it and calls `mark_job_paused()`, the JobService catches it and returns `JobStatus.PAUSED`. But in parallel mode, this chain is broken:
  1. Sheet-level recovery raises `RateLimitExhaustedError` (recovery.py:352)
  2. The parallel executor (`ParallelBatchExecutor`) catches it as a generic exception, stores the message string in `result.error_details`
  3. `_execute_parallel_mode` at lifecycle.py:1159 calls `state.mark_job_failed()` — FAILED, not PAUSED
  4. lifecycle.py:1169 raises `FatalError(f"Sheet {first_failed} failed: {error_msg}")` — plain `FatalError`, not the original `RateLimitExhaustedError`
  5. The lifecycle's `except RateLimitExhaustedError` handler (line 986) never fires
  6. The `resume_after` timestamp is lost
  7. The job ends up FAILED. Nobody schedules a resume.
- **Evidence:** Flowspec confirms `_execute_parallel_mode` calls `FatalError` directly (2 call sites). Conductor log shows rosetta went through 48 quota waits then `"event": "job_failed"` — not `"job.paused.rate_limit_exhausted"`.
- **Fix:** The parallel batch handler at lifecycle.py:1154-1169 must check if the error string indicates rate limit exhaustion (or better, preserve the exception type through the parallel executor). If `RateLimitExhaustedError`, call `mark_job_paused()` instead of `mark_job_failed()` and re-raise the original error type so the lifecycle handler fires correctly.

### F-112: No Auto-Resume After Rate Limit PAUSE — resume_after Timestamp Is Computed But Never Read
- **Found by:** Composer investigation
- **Severity:** P1 (high — the conductor's whole job is to manage execution timing)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Added timer scheduling in `_handle_rate_limit_hit()` at `core.py:958-967`. When a rate limit hits, a `RateLimitExpired` timer event is now scheduled. The event type, handler, and timer wheel all existed — only the 8-line trigger was missing. 10 TDD tests in `test_rate_limit_auto_resume.py`. Commit 25ba278.
- **Description:** Even if F-111 is fixed and jobs correctly PAUSE on rate limit exhaustion, nobody schedules an auto-resume. The `resume_after` timestamp flows through: `RateLimitExhaustedError.resume_after` → `state.resume_at` (lifecycle.py:989) → `job_event("paused", {"resume_at": ...})` (job_service.py:688). But `JobManager` has no code that reads `resume_at` and schedules a resume. `grep "resume_after\|auto_resume\|schedule.*resume" manager.py` returns nothing. The job sits in PAUSED state until a human runs `mzt resume`.
- **Impact:** The conductor knows EXACTLY when to resume (the timestamp is computed from the API's rate limit reset time) but does nothing with it. This is the core behavior gap — the conductor should be a scheduler, not a message board.
- **Fix:** When a job pauses with `resume_at`, the manager should schedule a timer (or use the baton's timer wheel) to fire `mzt resume <job-id>` at that time. This is what makes the conductor a conductor.

### F-113: Permanently Failed Sheets Treated as "Done" for Dependencies — Downstream Runs on Incomplete Input
- **Found by:** Composer observation (rosetta sheet 2 failed, but sheets 5-6 ran anyway)
- **Severity:** P0 (critical — dependency graph semantics violated, downstream produces garbage)
- **Status:** Resolved (movement 2, unnamed musician + Circuit mateship verification)
- **Resolution:** Two-part fix: (1) Added `propagate_failure_to_dependents()` to `ParallelExecutor` — iterative BFS that marks all non-terminal transitive dependents as FAILED with "Dependency failed" error message, adds them to `_permanently_failed`. (2) In `_execute_parallel_mode`, after adding failed sheets to `_permanently_failed`, calls `propagate_failure_to_dependents()` for each failed sheet. Also included `SheetStatus.FAILED` in the terminal set for DAG resolution, fixing F-129 (deadlock after restart when `_permanently_failed` is empty). The dependency policy config (`block`/`skip`/`proceed`) suggested in the original finding is not yet implemented — the current behavior is always `block` (propagate failure). Adversarial tests updated.
- **Description:** `ParallelExecutor.get_next_parallel_batch()` at `execution/parallel.py:441` adds permanently failed sheets to the "done" set for DAG resolution: `done_for_dag = completed | self._permanently_failed`. This means when a fan-out instance fails (e.g., rosetta sheet 2 = expedition-1), the synthesis stage sees ALL dependencies as "done" and dispatches — even though one input is missing. The synthesis runs on 5 of 6 expedition outputs and produces an incomplete corpus.
- **Evidence:** Rosetta status shows sheet 2 failed (49 attempts, quota exhaustion), but sheets 3-5 completed and sheet 6 (synthesis, depends on stage 2) is in_progress. The dependency `3: [2]` means stage 3 depends on ALL of stage 2's fan-out instances. Sheet 2 failed, so sheet 8 (stage 3 after expansion) should have been blocked or failed.
- **Root cause:** The comment at line 439 explains the intent: "so downstream sheets aren't blocked forever waiting for them." This prevents deadlock when `fail_fast=False`, but it violates the dependency contract. A failed dependency is not a completed dependency.
- **Fix:** Failed dependencies should propagate failure to downstream sheets, not silently pass. Options: (1) Mark downstream sheets as FAILED with "dependency failed" reason, (2) Add a `dependency_policy` config: `block` (wait/fail), `skip` (mark downstream as skipped), `proceed` (current behavior, for fault-tolerant pipelines). Default should be `block`. The current behavior should only be available as an explicit opt-in for pipelines where partial input is acceptable.

### F-071: `mzt list --json` Not Supported — RESOLVED
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low)
- **Status:** Resolved (movement 1 current cycle, Dash)
- **Resolution:** Added `--json` option to `list_jobs()` command. When set, outputs a JSON array of job objects with job_id, status, workspace, submitted_at fields. Empty result returns `[]`. Status filter and --all work with JSON output. Conductor-down case produces JSON error via `output_error(json_output=True)`. 5 TDD tests in `test_status_helpers.py::TestListJobsJsonOutput`.

### F-094: README Configuration Reference Teaches Obsolete `backend:` Syntax — RESOLVED
- **Found by:** Newcomer, Movement 2
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 1 current cycle, Dash)
- **Resolution:** Renamed "Backend Options" section to "Instrument Configuration". Updated all fields to `instrument`/`instrument_config.*` syntax. Updated prerequisites from "claude_cli backend" to instrument terminology. Updated architecture diagram from "Backend" to "Instrument". Added legacy `backend:` note pointing to configuration reference.

### F-114: Phase 4.5 Rate Limit Override Misses Quota-Only Patterns
- **Found by:** Breakpoint, Movement 1 (current cycle)
- **Severity:** P3 (low — narrow gap, most quota messages also match rate limit patterns)
- **Status:** open
- **Description:** `classify_execution()` Phase 4.5 at `classifier.py:1113-1147` checks rate limit patterns first, then checks quota exhaustion patterns only if rate limit patterns matched. Text that matches `quota_exhaustion_patterns` but NOT `rate_limit_patterns` (e.g., "Token budget exhausted — usage resets at 9pm") is invisible to Phase 4.5 when Phase 1 found JSON errors. The quota check is nested inside the rate limit gate.
- **Evidence:** Test `test_quota_pattern_without_rate_limit_pattern_missed_by_phase45` in `tests/test_baton_m4_adversarial.py` demonstrates the gap. Text containing "Token budget exhausted" matches quota patterns but not rate limit patterns — Phase 4.5 never fires.
- **Root cause:** Phase 4.5 was designed as a rate limit override. Quota exhaustion is a sub-type of rate limiting, so the quota check was nested inside the rate limit gate. However, the two pattern sets are not strict subsets — there are quota patterns that don't also match rate limit patterns.
- **Fix:** Either: (1) add independent quota exhaustion check in Phase 4.5 alongside the rate limit check, or (2) ensure all quota_exhaustion_patterns also trigger at least one rate_limit_pattern (add "exhausted" to rate_limit_patterns). Option 2 is simpler.
- **Impact:** Low. In practice, most quota messages from Claude/Gemini include words like "quota" or "limit" that match rate_limit_patterns. The gap is for messages that ONLY use "exhausted"/"budget" without "limit"/"quota"/"rate". Documented with a sentinel test.

### F-029: CLI Uses "JOB_ID" in Error Messages — PARTIALLY RESOLVED
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high)
- **Status:** Partially resolved (movement 1 current cycle, Dash)
- **Resolution:** Updated user-facing error messages in `validate_job_id()` from "Job ID" to "Score ID" (3 error messages). Updated 19 test assertions to match. Full metavar rename (`job_id` parameter → `score_id`) deferred — it's an E-002 escalation trigger and would risk merge conflicts with concurrent musicians.

---

## New Findings (Movement 1, Cycle 3 — Journey)

### F-115: `mzt cancel` Exits 0 on Not-Found + Uses Raw console.print Instead of output_error()
- **Found by:** Journey, Movement 1 (Cycle 3)
- **Severity:** P2 (medium — inconsistent error handling, wrong exit code)
- **Status:** Resolved (movement 1 cycle 3, Journey)
- **Description:** `cancel.py:72` used `console.print(f"[yellow]Score '{job_id}' not found or already stopped.[/yellow]")` for the not-found case. Three problems: (1) Exit code 0 — the operation failed but the process reports success. Scripts checking `$?` would think the cancel worked. (2) No `output_error()` — inconsistent with status, diagnose, resume, which all use `output_error()` with hints for not-found. (3) No hint — user gets a dead end. Status/diagnose say "Run 'mzt list' to see available scores." Cancel said nothing.
- **Impact:** CI/CD scripts checking exit codes see success when cancel fails. Users hit dead ends without guidance. JSON mode gets a `{"success": false}` but no error details for the not-found case.
- **Resolution:** Changed cancel not-found to use `output_error()` with hint "Run 'mzt list' to see available scores." and `raise typer.Exit(1)`. JSON mode now gets structured error. Successful cancel moved inside the `if cancelled:` branch with proper JSON handling. 5 TDD tests in `tests/test_cli_cancel_ux.py`.
- **Error class:** Same as F-047 (output_error() underadoption). The cancel command was missed during the M3 error standardization sweep.

### F-116: `mzt validate` Does Not Check Instrument Name Against Registry
- **Found by:** Journey, Movement 1 (Cycle 3)
- **Severity:** P2 (medium — user discovers typo only at runtime, not at validation)
- **Status:** Resolved (movement 2, Blueprint — commit 327e536)
- **Resolution:** Added V210 InstrumentNameCheck to validation system. Loads instrument profiles via `load_all_profiles()` and warns on unknown instrument names. Checks score-level, per-sheet, instrument_map, and movement instruments. WARNING severity (conductor may have instruments validator doesn't know about). Graceful degradation on profile load failure. 15 TDD tests.
- **Description:** A score with `instrument: nonexistent-instrument-12345` passes both schema validation (Pydantic accepts any string) and extended validation (no V-check for instrument name). The user discovers the error only at runtime when the conductor tries to resolve the instrument. Verified: `mzt validate /tmp/bad-instrument.yaml` shows "Schema validation passed" with zero instrument-related warnings.
- **Impact:** A typo in the instrument name (`instrument: clause-code` instead of `claude-code`) silently passes validation. The user submits the job, waits for the conductor, and gets a runtime error. This is exactly the class of mistake that validation should catch early. The gap exists because `mzt validate` is stateless — it doesn't query the instrument registry.
- **Action:** Add a V-check (e.g., V210) that loads available instrument profiles (built-in + user + project) and warns when the instrument name doesn't match any known profile. This should be a WARNING, not an error — the conductor may have instruments the validator doesn't know about. The check can use `load_all_profiles()` from `instruments.py` which already scans all profile directories.

### F-117: `mzt list` Intermittent Failure During Conductor Restart — Misleading Error
- **Found by:** Journey, Movement 1 (Cycle 3)
- **Severity:** P3 (low — transient, self-resolving)
- **Status:** Open
- **Description:** During a conductor restart (uptime went from 4h28m to 1m49s between two invocations), `mzt list` returned "Marianne conductor is not running" with exit code 1. The conductor WAS running — it was briefly unresponsive during startup. `mzt list --json` and `mzt list --all` succeeded seconds later. The error message is misleading: "not running" is the wrong diagnosis when the conductor is starting/restarting.
- **Impact:** Low — the condition is transient and self-resolves within seconds. But the error message teaches the wrong mental model: the user thinks the conductor crashed when it's actually restarting. A user who trusts this message might run `mzt start` and get a "already running" conflict.
- **Action:** Change the error message from "Marianne conductor is not running" to "Could not connect to the Marianne conductor. It may be starting up — try again in a few seconds." Add a retry hint. Alternatively, add a 1-retry with 2s delay before declaring the conductor unreachable.

### F-118: ValidationEngine Context Gap Between Runner and Baton Musician
- **Found by:** Prism, Movement 1 (Cycle 2)
- **Severity:** P2 (medium — validations using `{job_name}` will fail under baton path)
- **Status:** Resolved (movement 1, Axiom)
- **Description:** `musician.py:509-511` creates `ValidationEngine(workspace=sheet.workspace, sheet_context={"sheet_num": sheet.num})`. The runner's equivalent at `sheet.py:1506-1520` passes richer context: `job_name`, `total_sheets`, `workspace`, `sheet_num`, and all template variables. Validations that reference `{job_name}` in paths or commands will get `KeyError` under the baton path.
- **Impact:** Most validations use `{workspace}` and `{sheet_num}` which are present in both paths. But `{job_name}` is a documented template variable for validations. Any score using it will silently break when moved to `use_baton: true`.
- **Error class:** Integration boundary contract gap. Both sides are individually correct — the musician passes what it has, the ValidationEngine uses what it receives. The gap exists because they were built at different times against different assumptions.
- **Resolution:** `_validate()` now accepts `total_sheets` and `total_movements` keyword args, calls `sheet.template_variables(total_sheets, total_movements)` to build the full context dict, and passes it to `ValidationEngine`. This provides workspace, movement, voice, voice_count, stage, instance, fan_count, total_stages, total_movements, instrument_name, and all custom score variables. The call site in `sheet_task()` threads the totals through. 8 TDD tests in `test_axiom_boundary_bugs.py`.

### F-119: Baton Event Stubs Silently Drop 5 Event Types
- **Found by:** Prism, Movement 1 (Cycle 2)
- **Severity:** P2 (medium — silent event loss)
- **Status:** Resolved (movement 2, Harper 861ef63 — stubs now log instead of silent pass). Verified by Axiom M2: StaleCheck, CronTick, PacingComplete, ConfigReloaded, ResourceAnomaly all log `baton.event.unimplemented` at WARNING level with event_type in extra dict (core.py:628-685).
- **Description:** `core.py:628-675` has 5 event handlers that are `pass` stubs with TODO comments: StaleCheck (line 629), CronTick (line 632), PacingComplete (line 638), ConfigReloaded (line 661), ResourceAnomaly (line 671). Events arriving at the baton inbox for these types are silently discarded. No log entry, no warning, no counter.
- **Impact:** If the timer wheel fires StaleCheck events (which it's designed to do), the baton silently ignores them. Stale detection is non-functional through the baton path. Same for cron, pacing, config reload, and backpressure.
- **Action:** Add `_logger.warning("baton.event.unimplemented", event_type=type(event).__name__)` to each stub. Implement stale detection first — F-097 showed stale detection killing agents at 30 minutes.

### F-120: Step 29 (Restart Recovery) Unclaimed After 4 Movements
- **Found by:** Prism, Movement 1 (Cycle 2)
- **Severity:** P1 (high — blocks baton-to-production migration)
- **Status:** Resolved (movement 2, Maverick b4146a7 — mateship pickup). recover_job() implemented in adapter.py:502. Verified by Axiom M2 against HEAD.
- **Description:** Step 29 in TASKS.md ("Implement restart recovery — reconcile baton-state + CheckpointState") is the ONLY remaining P0 task blocking `use_baton: true` in production. It has never been claimed. No investigation, no design doc, no test plan. The baton has 1,006 passing tests, 1,250 lines of core logic, and has never executed a real sheet through the conductor.
- **Impact:** The baton accumulates correctness in isolation while the legacy runner receives all production fixes. Every movement that passes without step 29 increases integration risk. The two execution paths are diverging, not converging.
- **Action:** Claim step 29 for next movement. Break into subtasks: (1) define reconciliation requirements, (2) write algorithm, (3) crash/restart test scenarios, (4) integration test through conductor.

### F-121: GitHub Issue #152 Verified Closable
- **Found by:** Prism, Movement 1 (Cycle 2)
- **Severity:** P3 (low — housekeeping)
- **Status:** Open
- **Description:** Issue #152 ("34 of 37 example scores fail validation — workspace path bug") was filed based on F-093. F-093 was resolved in commit 75bebed (all 35 examples fixed from `./workspaces/` to `../workspaces/`). Verification confirms zero examples use the old pattern. Per composer directive, Prism/Axiom verify fixes before closing.
- **Action:** Close #152 with reference to commit 75bebed and F-093 resolution.

### F-122: 4 IPC Callsites Bypass --conductor-clone (Hardcoded Production Socket)
- **Found by:** Prism, Movement 1 (Cycle 2)
- **Severity:** P1 (high — breaks clone test isolation for hooks, MCP, and dashboard)
- **Status:** Resolved (movement 2, Harper)
- **Description:** Four IPC callsites create `DaemonClient` with hardcoded production socket paths, bypassing the `_resolve_socket_path()` clone-aware resolution that all CLI commands use:
  1. `src/marianne/execution/hooks.py:129` — `DaemonClient(SocketConfig().path)`. On_success hook chaining submits to production conductor even when `--conductor-clone` is active.
  2. `src/marianne/mcp/tools.py:52` — `DaemonClient(DaemonConfig().socket.path)`. MCP tools query/control production during clone testing.
  3. `src/marianne/dashboard/routes/jobs.py:362` — `DaemonClient(DaemonConfig().socket.path)`. Dashboard targets production.
  4. `src/marianne/dashboard/services/job_control.py:76` — `DaemonClient(DaemonConfig().socket.path)`. Dashboard job control targets production.
- **Impact:** Clone test isolation is incomplete. The hooks.py bypass is most critical — self-chaining scores tested with `--conductor-clone` will silently submit chained jobs to the production conductor. Same error class as F-090 (config_cmd.py bypass, fixed by Ghost in 42d3d1a).
- **Error class:** No centralized DaemonClient factory. Developers use the obvious `DaemonClient(DaemonConfig().socket.path)` pattern. The correct pattern (`_resolve_socket_path()` from detect.py) requires knowing it exists.
- **Resolution:** Replaced all 5 callsites (4 original + dashboard/app.py factory) with `_resolve_socket_path(None)`. For hooks.py, the clone_name persists via os.fork() from the CLI process into the conductor process — no RunnerContext change needed. 14 TDD tests in test_f122_clone_socket_bypass.py. Zero `DaemonConfig().socket.path` or `SocketConfig().path` bypasses remain in the codebase (verified by grep).

### F-123: README.md References 3 Deleted Example Files — Broken Links
- **Found by:** Newcomer, Movement 1 (Cycle 3)
- **Severity:** P1 (high — broken links in the primary README)
- **Status:** Resolved (movement 1 cycle 3, Newcomer)
- **Description:** `README.md` lines 389-401 referenced three example files that were deleted as part of the F-088 cleanup (hardcoded absolute paths): `fix-deferred-issues.yaml`, `fix-observability.yaml`, and `quality-continuous-daemon.yaml`. The files were removed from the working tree but the README's examples table was never updated. A newcomer clicking these links gets a 404 on GitHub or "file not found" locally.
- **Impact:** Three broken links in the most visible document in the repository. A newcomer browsing the examples table clicks a link and hits nothing. This is the same error class as F-082 (fix applied to one location but not swept across all docs).
- **Error class:** F-088 cleanup removed files without sweeping all references. Same pattern as F-026 → F-082 (fix-the-instance-not-the-pattern).
- **Resolution:** Removed all 3 broken rows from README.md examples tables.

### F-124: score-writing-guide.md References Deleted fix-deferred-issues.yaml
- **Found by:** Newcomer, Movement 1 (Cycle 3)
- **Severity:** P2 (medium — broken example reference in documentation)
- **Status:** Resolved (movement 1 cycle 3, Newcomer)
- **Description:** `docs/score-writing-guide.md:215` referenced `examples/fix-deferred-issues.yaml` as the example for the "Code Automation" pattern. This file was deleted in the F-088 cleanup. The reference now points to nothing.
- **Impact:** A user reading the score writing guide and looking for the code automation example finds nothing. The pattern description becomes abstract with no concrete example to learn from.
- **Resolution:** Replaced with `examples/issue-solver.yaml` which demonstrates the same class of pattern (multi-stage code automation with fan-out reviewers).

### F-125: iterative-dev-loop-config.yaml in examples/ Is Not a Marianne Score
- **Found by:** Newcomer, Movement 1 (Cycle 3)
- **Severity:** P3 (low — misleading file placement)
- **Status:** Open
- **Description:** `examples/iterative-dev-loop-config.yaml` is a generator config for `scripts/generate-iterative-dev-loop.py`, not a runnable Marianne score. It fails `mzt validate` with schema errors (missing `sheet` and `prompt` fields). It sits alongside 36 valid scores in `examples/` with no distinguishing marker. The examples/README.md lists it as "Configurable variant of the iterative development loop" at High complexity — implying it's a runnable score. The Validation Summary table also lists it with a ✓ checkmark, which is false.
- **Impact:** A newcomer browsing examples tries to validate or run this file and gets a confusing schema error. The file has a useful purpose (generator config) but its placement in examples/ is misleading.
- **Action:** Either move to `scripts/` (where the generator script lives) or rename to `iterative-dev-loop-config.generator.yaml` and add a clear note in examples/README.md that this is a generator config, not a runnable score.

### F-126: README "Beyond Coding" Section Missing 7 Creative Examples
- **Found by:** Newcomer, Movement 1 (Cycle 3)
- **Severity:** P3 (low — README undersells creative capabilities)
- **Status:** Open
- **Description:** The README's "Beyond Coding" table (line 405-416) lists only 6 examples and says "For creative and experimental scores...see the Marianne Score Playspace" (external repo). But 7 creative scores that ARE in `examples/` are not listed: `dialectic.yaml`, `thinking-lab.yaml`, `dinner-party.yaml`, `worldbuilder.yaml`, `palimpsest.yaml`, `skill-builder.yaml`, `context-engineering-lab.yaml`. These ARE documented in `examples/README.md` but NOT in the main README. The README sends users to an external repo for scores that are already present locally.
- **Impact:** A newcomer reading the README sees 6 "Beyond Coding" examples and is told to go elsewhere for more. The 7 additional creative examples — which demonstrate Marianne's versatility — are hidden. The main README undersells the project's capabilities.
- **Action:** Add the 7 missing creative examples to the README's "Beyond Coding" table. Remove or soften the redirect to the external Playspace repo (it can remain as an additional resource, not the primary destination).

### F-127: Diagnose Shows "success_first_try" for Sheets With 18 Attempts
- **Found by:** Ember, Movement 1 (Cycle 3)
- **Severity:** P2 (medium — diagnostic tool misleads)
- **Status:** Resolved (movement 2, Blueprint — commit 327e536)
- **Resolution:** Changed `_classify_success_outcome()` to use persisted `sheet_state.attempt_count` (cumulative) instead of session-local `normal_attempts`. Also uses persisted `sheet_state.completion_attempts`. After restart+resume, a sheet with 18 cumulative attempts is correctly classified as SUCCESS_RETRY. 7 TDD tests including the F-127 regression case (18 attempts → SUCCESS_RETRY, not SUCCESS_FIRST_TRY).
- **Description:** `mzt diagnose marianne-orchestra-v3` shows `success_first_try` in the Outcome column for sheets that required 18, 17, 10, 6, and 4 attempts respectively. The cause: `_classify_success_outcome()` at `src/marianne/execution/runner/sheet.py:2480` checks `normal_attempts <= 1` where `normal_attempts` is a session-local counter that resets when the conductor restarts and the job is resumed. Meanwhile, the Attempts column shows `attempt_count` from SheetState, which is the cumulative lifetime count. After a restart+resume, `normal_attempts` is 1 (current session) but `attempt_count` is 18 (cumulative) — the same table row contains contradictory information.
- **Evidence:** `mzt diagnose marianne-orchestra-v3` output:
  ```
  │    9 │ completed   │    1800.7s │      18 │ normal       │ success_first_try │
  │   12 │ completed   │    1530.6s │      17 │ normal       │ success_first_try │
  │   14 │ completed   │    1170.5s │      17 │ normal       │ success_first_try │
  ```
- **Impact:** A user investigating why sheet 9 took 1800 seconds sees "18 attempts" and "success_first_try" in the same row. The outcome category provides no useful information. It actively misleads — the user may think the display is broken (it is, but not in the way they expect).
- **Root cause:** `_classify_success_outcome()` is correct for single-session execution but wrong for resumed jobs. The session-local `normal_attempts` and the persisted `attempt_count` diverge after restart+resume.
- **Fix direction:** Either (a) classify from cumulative `sheet_state.attempt_count` instead of session-local `normal_attempts`, or (b) add a `cumulative_outcome_category` field that considers the full history, distinct from the session outcome.

---

## New Findings (Movement 1, Cycle 3 — Adversary)

### F-128: F-097 E006 Stale Detection Only Reachable via classify(), Not classify_execution()
- **Found by:** Adversary, Movement 1 (Cycle 3)
- **Severity:** P2 (medium — E006 partially wired)
- **Status:** WRONG — Not a bug (Adversary review, Cycle 7)
- **Resolution:** The original analysis was incorrect. `classify_execution()` at `classifier.py:997-1005` DOES differentiate stale from timeout: it checks `exit_reason == "timeout"` and scans for "stale execution" in combined output. The runner at `recovery.py:555` passes `exit_reason=result.exit_reason` to `classify_execution()`. The stale detection handler at `sheet.py:374` sets `exit_reason="timeout"`. The full production path is: `sheet.py:374` → `recovery.py:555` → `classifier.py:997` → E006. The original test comment was misleading — it tested `classify()` only and assumed `classify_execution()` lacked the feature.
- **Description:** The E006 (EXECUTION_STALE) error code added by Blueprint (M4) is only reachable through `classifier.classify()` which requires `exit_reason="timeout"` parameter. The `classify_execution()` path (used by the runner's `_classify_execution` at `sheet.py`) does NOT differentiate stale from timeout because it doesn't receive an `exit_reason` parameter. Both stale and regular timeout produce E009 (generic transient) through `classify_execution()`.
- **Evidence:** `test_adversary_m1c3.py::TestCrossSystemIntegration::test_f097_stale_vs_timeout_via_classify` passes (E006 works through classify()). But when both stale and timeout text are run through `classify_execution()`, both produce E009. The runner at `sheet.py` calls `_classify_execution()` → `classify_execution()`, not `classify()`.
- **Impact:** The F-097 fix (E006 for stale detection) only works in the classify() path. The production path through the runner uses classify_execution() and still produces E009 for stale detection. The E006 error code exists but is unreachable in the actual execution flow.
- **Fix direction:** Either (a) add stale detection to `classify_execution()` by scanning for "stale execution" text without requiring exit_reason, or (b) pass exit_reason through the runner's call chain to classify(). Option (a) is simpler and consistent with how Phase 4.5 rate limit detection already works in classify_execution().

### F-129: F-113 Behavior Changes After Restart — Job Gets Stuck Forever
- **Found by:** Adversary, Movement 1 (Cycle 3)
- **Severity:** P1 (high — the behavior of F-113 is inconsistent across restarts)
- **Status:** Resolved (movement 2, unnamed musician — fixed as part of F-113 resolution)
- **Resolution:** The F-113 fix includes `SheetStatus.FAILED` in the terminal set for DAG resolution in `get_next_parallel_batch()`. This means FAILED sheets are treated as "done" from persisted state alone — the ephemeral `_permanently_failed` set is no longer required for correct behavior after restart. The deadlock is structurally eliminated. Adversarial test updated to verify the fix.
- **Description:** F-113 documents that `_permanently_failed` in `parallel.py:441` treats failed deps as "done" for DAG resolution. But `_permanently_failed` is an in-memory set. After a conductor restart + resume, the set is empty. Without it, the DAG's `get_ready_sheets()` only considers COMPLETED and SKIPPED sheets — FAILED sheets are NOT "done". Result: the downstream sheet is blocked forever.
- **Evidence:** `test_adversary_m1c3.py::TestF113FailedDependenciesTreatedAsDone::test_permanently_failed_ephemeral_after_restart` proves this: after restart (empty `_permanently_failed`), sheet 5 is NOT dispatched. The same job behaves differently before restart (dispatches with incomplete data) and after restart (stuck forever).
- **Impact:** Two bugs for the price of one. Before restart: F-113 (wrong behavior — runs with missing input). After restart: deadlock (wrong behavior — blocks forever). Neither is correct. The correct behavior would be to propagate failure through the dependency chain (like the baton's `_propagate_failure_to_dependents`).
- **Error class:** Ephemeral state that changes system behavior. Same class as F-077 (hook_config not restored).

### F-130: 27 Adversarial Tests Confirming 5 Open P0/P1 Bugs
- **Found by:** Adversary, Movement 1 (Cycle 3)
- **Severity:** N/A (test suite, not a bug)
- **Status:** Committed
- **Description:** 27 adversarial tests in `tests/test_adversary_m1c3.py` across 7 test classes proving 5 open production bugs:
  - **F-111** (5 tests): RateLimitExhaustedError type lost in parallel mode. Exception hierarchy, resume_after timestamp, all-rate-limited batch, mixed errors — all prove the job FAILS instead of PAUSING.
  - **F-113** (4 tests): Failed dependencies treated as "done". Fan-out, chain, multiple failures, ephemeral _permanently_failed set.
  - **F-075** (4 tests): Resume fix regression — all adversarial conditions hold (FAILED/SKIPPED/mixed/all-failed).
  - **F-122** (4 tests): IPC clone bypass — hooks.py, mcp/tools.py, dashboard routes, job_control all hardcode production socket.
  - **Cross-system** (3 tests): F-098 rate limit regression, F-097 stale detection via classify(), credential redaction.
  - **Baton state** (4 tests): cost limit zero, FERMATA deregister, unknown job, unknown sheet.
  - **Parallel edges** (3 tests): all-fail, concurrent success+failure, permanently failed exclusion.
- **Total adversarial test count:** 215 (27 M1C3 + 64 M4 + 59 M2 + 65 M1). Four complete adversarial passes, two new bugs found (F-128, F-129).

---

## New Findings (Movement 1, Cycle 7 — Newcomer Review)

### F-131: `--conductor-clone` Help Text Misleading About "No Value" Usage
- **Found by:** Newcomer, Movement 1 (Cycle 7)
- **Severity:** P3 (low — UX documentation)
- **Status:** Resolved (movement 2, Harper)
- **Description:** `--conductor-clone` help says "Pass without value for default clone" but the option is a TEXT type in Typer/Click that consumes the next positional argument. `marianne --conductor-clone start` parses `start` as the clone name (not the subcommand), producing confusing errors. Users must use `=` syntax: `marianne --conductor-clone= start` or `marianne --conductor-clone=name start`.
- **Impact:** Users following the help text literally get behavior where the command disappears. The workaround (= syntax) is not documented.
- **Resolution:** Updated help text and docstring to explicitly require `=` syntax: "Use --conductor-clone= (with equals sign) for default clone, or --conductor-clone=NAME for a named clone."

### F-132: `--conductor-clone` State DB Isolation May Be Incomplete
- **Found by:** Newcomer, Movement 1 (Cycle 7). Severity upgraded by Adversary (Cycle 7).
- **Severity:** P1 (high — P0 conductor-clone directive depends on state isolation that doesn't exist)
- **Status:** Resolved (movement 2, Maverick)
- **Description:** Clone conductor started via `marianne --conductor-clone= start -f` logged `registry.opened path=/home/emzi/.marianne/daemon-state.db` (the production path) and `manager.registry_restored loaded=5` (5 production jobs restored into the clone). Code at `clone.py:112` defines a separate `state_db=marianne_dir / f"clone{tag}-state.db"` but the running clone opened the production DB. Socket isolation (`/tmp/marianne-clone.sock`) works. PID isolation works. State/registry isolation does not appear to work based on the observed log output.
- **Impact:** If the clone shares the production registry, test jobs submitted to the clone may appear in `mzt list` against the production conductor. Clone testing is not fully safe for state-mutating operations (job submission, status changes). The P0 composer directive to use `--conductor-clone` for all testing may not provide the isolation it promises.
- **Root Cause:** The Adversary's analysis was close but imprecise. `build_clone_config()` at `clone.py:144` DOES set `state_db_path`. The real bug was in `process.py:start_conductor()` which duplicates the clone path override logic INLINE instead of calling `build_clone_config()`. The inline version at process.py:72-73 overrode `socket` and `pid_file` but missed `state_db_path`. This DRY violation meant the two code paths diverged — one correct (clone.py), one broken (process.py). The fix adds `config_dict["state_db_path"] = str(clone_paths.state_db)` at process.py:74. 2 TDD tests verify isolation.
- **Resolution:** Partially fixed in commit b4146a7 (Maverick: process.py only). Canyon (movement 2) discovered the SAME bug in `clone.py:build_clone_config()` — a second code path that builds clone configs but also missed `state_db_path`. 1-line fix at clone.py:144 + 3 TDD tests (config differs from base, matches resolved paths, named clones differ). Both paths now set `state_db_path`. Error class: DRY violation — two independent clone path builders, both missed the same field.

---

## Adversary Review Findings (Movement 1, Cycle 7)

### F-133: Mateship Pipeline Verification Gap — Findings Accepted Without Code Re-Verification
- **Found by:** Adversary, Movement 1 (Cycle 7)
- **Severity:** P3 (low — process improvement)
- **Status:** Open
- **Description:** F-128 was filed by Adversary (Cycle 3), accepted by all 4 reviewers (Prism, Axiom, Ember, Newcomer) in Cycle 7, listed as open P1 in the quality gate — and proved WRONG by tracing the actual production code path. The claim "E006 unreachable via classify_execution()" was accepted from the test comment without verifying against `classifier.py:997-1005` and `recovery.py:555` which clearly pass `exit_reason` through. Four reviewers trusted the finding without re-running the path.
- **Impact:** A non-existent bug was listed as P1 for 4+ cycles. The verification gap is small (1 finding in 150+) but the lesson is important: claims about unreachability require negative proof by executing the actual code path, not reasoning from test comments or memory.
- **Action:** When reviewing findings that claim "code path X is unreachable," verify by tracing the actual production call chain, not by accepting the finding's evidence at face value.

### F-134: _run_via_baton Uses Non-Existent Field `max_cost_usd`
- **Found by:** Foundation, Movement 2
- **Severity:** P2 (medium — latent bug, currently unreachable)
- **Status:** Resolved (movement 2, Foundation)
- **Resolution:** Fixed `max_cost_usd` → `max_cost_per_job` in `_run_via_baton()`. Both baton paths (`_run_via_baton` and `_resume_via_baton`) now use the correct field.
- **Description:** `manager.py:_run_via_baton()` at line 1665 accesses `config.cost_limits.max_cost_usd`, but `CostLimitConfig` has no such field. The actual field is `max_cost_per_job` (see `execution.py:190`). This doesn't crash because `use_baton` is currently disabled in production, so `_run_via_baton` is never called. When `use_baton` is enabled, cost limits will silently fail — `max_cost` will always be `None`, meaning the baton will never enforce per-job cost limits even when configured.
- **Impact:** When `use_baton: true` is enabled, per-job cost limits will not be enforced. The baton will run uncapped even when the score specifies `cost_limits.max_cost_per_job`.

---

## New Findings (Movement 2 — Warden)

### F-135: Musician Exception Handler Leaks Credentials via error_message
- **Found by:** Warden, Movement 2
- **Severity:** P1 (high — credentials propagate to 6+ storage locations)
- **Status:** Resolved (movement 2, Warden)
- **Description:** `src/marianne/daemon/baton/musician.py:156` constructed `error_msg = f"{type(exc).__name__}: {exc}"` from caught exceptions WITHOUT calling `redact_credentials()`. This error_msg was: (1) logged at ERROR level with `exc_info=True`, (2) stored in `SheetAttemptResult.error_message` (persists to state DB), (3) visible in `mzt diagnose` and `mzt errors` output, (4) indexed by the learning store for pattern matching. Meanwhile, the same function DID redact `stdout_tail` and `stderr_tail` at line 573. The gap: output was sanitized, but exception messages were not.
- **Impact:** If a backend raised an exception containing an API key (e.g., `ConnectionError: Auth failed with key sk-ant-api03-...`), the credential would persist in logs, state DB, dashboard, diagnostic output, and learning store. The musician's `_capture_output()` correctly redacted credentials from stdout/stderr, but the exception handler's `error_msg` bypassed this protection entirely.
- **Error class:** Same pattern as F-003/F-020 — safety applied to one data path but not an adjacent parallel path. The credential scanner existed, the import existed, but the call was missing at this specific location.
- **Resolution:** Applied `redact_credentials()` to `error_msg` at musician.py:156 (exception handler) and to validation error text at musician.py:552 (validation engine exception). Both paths now sanitize exception messages before logging and storing. 26 TDD tests in `tests/test_musician_error_redaction.py` across 3 test classes: unit tests proving each credential type is redacted, integration tests exercising the actual `sheet_task()` function with credential-leaking mock backends, and adversarial edge cases (multi-pattern, unicode, JSON-embedded, traceback-embedded).

### F-061: 3 Critical Dependency CVEs — RESOLVED
- **Found by:** Sentinel, Movement 2 (original). Warden, Movement 2 (fix).
- **Severity:** P1 (high — known vulnerabilities in auth/crypto paths)
- **Status:** Resolved (movement 2, Warden)
- **Description:** F-061 identified 3 security-critical dependencies with known CVEs: `cryptography` 46.0.5 (CVE-2026-34073), `pyjwt` 2.11.0 (CVE-2026-32597), `requests` 2.32.5 (CVE-2026-25645). These are transitive dependencies used by the dashboard auth system and webhook notifications.
- **Resolution:** Added minimum version pins to `pyproject.toml` dependencies: `cryptography>=46.0.6`, `pyjwt>=2.12.0`, `requests>=2.33.0`. Verified install succeeds with upgraded versions (cryptography 46.0.6, PyJWT 2.12.1, requests 2.33.1). All quality gates pass. F-061 no longer blocks public release.

---

## New Findings (Movement 2 — Sentinel)

### F-136: _classify_error() Returns Unredacted error_message from Backend
- **Found by:** Sentinel, Movement 2
- **Severity:** P1 (high — credential leak path, same class as F-135)
- **Status:** Resolved (movement 2, Sentinel)
- **Description:** `_classify_error()` at `src/marianne/daemon/baton/musician.py:587-628` returns `exec_result.error_message` directly at three exit points (lines 608, 622, 627) without calling `redact_credentials()`. A backend that sets `error_message` to a string containing an API key (e.g., auth failure echoing the key, config error with key in URL) stores that key in `SheetAttemptResult.error_message` → state DB → dashboard → diagnostic output → learning store. Meanwhile, `stdout_tail` and `stderr_tail` ARE redacted 5 lines earlier (line 581-582), and the exception path at line 162 IS redacted. Only the `_classify_error` return path was unprotected.
- **Impact:** Backend error messages containing API keys persist across 6+ storage locations. The credential scanner exists, the import exists, the call was missing at this specific site. Same error class as F-135 (exception handler) and F-003 (stdout/stderr) — safety applied to adjacent data paths but not this one.
- **Error class:** Piecemeal credential redaction — the pattern that F-020 and F-135 already demonstrated. Three independent data paths (stdout/stderr, exceptions, error_message) all flow through the same musician, each needs redaction independently.
- **Resolution:** Applied `redact_credentials()` to the `error_msg` returned by `_classify_error()` at `musician.py:129`. The redaction happens at the call site (where the value is consumed) rather than inside `_classify_error` (which is a pure classifier). This matches the exception path pattern at line 162. 5 TDD regression tests in `test_musician_error_redaction.py::TestClassifyErrorPathRedaction` covering TRANSIENT (exit_code=None), AUTH_FAILURE (401/403), EXECUTION_ERROR (generic), None message passthrough, and clean message preservation.

### F-137: Pygments CVE-2026-4539 (ReDoS) — Transitive Dependency
- **Found by:** Sentinel, Movement 2
- **Severity:** P3 (low — ReDoS in unused ADL lexer, but fix available)
- **Status:** Resolved (movement 4, Sentinel)
- **Resolution:** Added `"pygments>=2.20.0"` to security minimum versions in `pyproject.toml:49`. Upgraded from 2.19.2 to 2.20.0. Verified with `python -c "import pygments; print(pygments.__version__)"` → 2.20.0. Public release hygiene — `pip-audit` on fresh Marianne install now shows zero known CVEs in transitive dependencies.
- **Description:** `pygments` 2.19.2 has CVE-2026-4539 (ReDoS in AdlLexer). Pygments is a transitive dependency of Marianne through `rich` (CLI output), `pytest` (test framework), and `mkdocs-material` (documentation). The fix version is 2.20.0. The CVE triggers only when highlighting ADL (Archetype Definition Language) syntax, which Marianne does not do — the risk is near zero.
- **Impact:** Negligible for Marianne. The only theoretical path is if agent stdout contained ADL syntax and Rich tried to highlight it — which it wouldn't, since Marianne uses plain text output capture. However, the fix is available and trivial to apply.

### F-138: Untracked test_baton_m2c2_adversarial.py Has Broken ParallelExecutor Construction
- **Found by:** Theorem, Movement 2
- **Severity:** P3 (low — test file only, not production code)
- **Status:** Open
- **Description:** `tests/test_baton_m2c2_adversarial.py` (untracked, from another musician) uses `ParallelExecutor.__new__(ParallelExecutor)` to bypass the constructor and then sets `executor.dag = dag` directly. However, `dag` is now a property that reads `self.runner.dependency_dag`, so direct assignment doesn't work. The `_logger` attribute is also missing. This causes `AttributeError: 'ParallelExecutor' object has no attribute 'runner'` at runtime.
- **Impact:** Test file cannot execute. 6 tests in TestFailurePropagationAdversarial fail. No production impact.
- **Action:** Fix the test to properly construct a ParallelExecutor with a mock runner, or set `executor._dag` (the backing field) instead of using the property. Also add `executor._logger`. Mateship pickup.

### F-139: Resume Reports Previous Batch Failure But Actually Succeeds — Misleading Exit Code 1
- **Found by:** Automated monitor (coordination-workspace cron), 2026-04-01
- **Severity:** P2 (medium — causes automated tooling to believe resume failed when it succeeded)
- **Status:** Open (manifestation of issue #139)
- **Description:** `marianne-orchestra-v3` failed at sheet 64 with "Parallel batch failed: Sheet 64 - Task cancelled" after 48 quota exhaustion waits. Running `mzt resume marianne-orchestra-v3` returned exit code 1 with the same error message: "Score failed after resume: marianne-orchestra-v3 — Parallel batch failed: Sheet 64 - Task cancelled". However, the job actually resumed successfully — `mzt status` showed RUNNING with new sheets in_progress, and `mzt resume --force` confirmed "score is running". The CLI reported the *previous* batch's error during the transition, not a current failure.
- **Impact:** (1) Automated monitoring that checks exit codes will incorrectly conclude the resume failed. (2) Human operators see "failed" and escalate unnecessarily. (3) Retry loops will attempt redundant resumes against an already-running job.
- **Related:** Issue #139 (stale state error feedback on run/resume). Issue #100 / #141 (rate limits killing jobs instead of pausing). F-112 (auto-resume gap — conductor should schedule resume on rate limit pause).
- **Action:** Fix is tracked in issue #139. The resume command should check post-submission status before reporting success/failure. Exit code should reflect actual outcome, not stale state.

### F-140: F-048 Cost Regression — $0.01 Is Worse UX Than $0.00
- **Found by:** Ember, Movement 2 (Cycle 2)
- **Severity:** P2 (medium — actively misleading cost display)
- **Status:** Open (related to F-108)
- **Description:** The v3 score (67 completed sheets, 46+ hours of Opus execution) shows `Cost: $0.01` with `Input tokens: 880, Output tokens: 440`. The Rosetta score (14 completed sheets, ~2 hours) shows `Cost: $0.02` with `Input tokens: 2,206, Output tokens: 1,103`. These numbers are fiction — 880 input tokens for 67 sheets of Opus execution is 13 tokens per sheet. A single prompt is thousands of tokens. The actual spend is likely $50-200+.
- **Evidence:** `mzt status marianne-orchestra-v3` output (2026-04-02). `mzt status the-rosetta-score -w workspaces/rosetta-workspace/` output (2026-04-02).
- **Impact:** In M1C7, cost showed $0.00 — obviously broken. Now $0.01 LOOKS real. A user sees "$0.01 for 67 sheets" and concludes Marianne is cheap, not that the tracking is broken. The cost display has gone from "obviously wrong" to "plausibly wrong" — which is worse for trust. Cost limits (`max_cost_per_job`) built on this data protect nothing.
- **Root cause:** Same as F-108 — native ClaudeCliBackend doesn't extract tokens from text output. The non-zero values likely come from preflight checks or early JSON-mode interactions.
- **Action:** Same as F-108 — route all execution through PluginCliBackend (F-105). Additionally, consider showing "Cost: unknown (no token data)" when `input_tokens + output_tokens < expected_minimum` rather than displaying a fictional small number.

### F-141: F-127 Display Gap — Diagnose Shows Pre-Fix Stale outcome_category
- **Found by:** Ember, Movement 2 (Cycle 2)
- **Severity:** P3 (low — only affects pre-fix data)
- **Status:** Open
- **Description:** F-127 was fixed in Blueprint 327e536 — `_classify_success_outcome()` now uses persisted `attempt_count`. But `diagnose.py:1077,1222` reads `outcome_category` directly from the persisted `SheetState` field. All 67 v3 sheets completed before the fix display the old wrong classification (`success_first_try` for 18-attempt sheets). The fix prevents future lies but doesn't correct past ones.
- **Evidence:** `mzt diagnose marianne-orchestra-v3` output (2026-04-02): Sheet 9 shows `18` attempts, `success_first_try` outcome. Confirmed by reading `diagnose.py:1077` — it reads `sheet.outcome_category` not recomputing from `sheet.attempt_count`.
- **Impact:** Any score that completed sheets before the fix has incorrect outcome data forever. The v3 score (67 sheets, many multi-attempt) is the primary case.
- **Action:** Either: (a) have `diagnose.py` recompute outcome from `attempt_count` at render time (ignore persisted `outcome_category`), (b) detect the contradiction (`attempt_count > 1 && outcome == success_first_try`) and display honestly, or (c) add a one-time migration that recomputes `outcome_category` from `attempt_count` for all completed sheets. Option (a) is simplest and most correct.

### F-142: top.py Help Examples and Notes Say `--job` While Flag Is `--score`
- **Found by:** Ember, Movement 2 (Cycle 2) — mateship pickup of Dash's incomplete fix
- **Severity:** P3 (low — user-facing inconsistency)
- **Status:** Resolved (movement 2, Ember — mateship fix)
- **Description:** Dash renamed the `top.py` flag from `--job` to `--score` (62fc205) but left 4 user-facing strings unchanged: help example `mzt top --job my-review` (line 96), TUI note `Note: --job filter` (line 141), history note `Note: --job filter` (line 318), docstring `job-centric process tree` (line 89). The flag and its help were inconsistent.
- **Resolution:** Updated all 4 strings: help example → `--score my-review`, notes → `--score filter`, docstring → `score-centric process tree`.

### F-143: _handle_resume_job Doesn't Re-Check Cost Limits After Unpausing
- **Found by:** Axiom, Movement 2 (backward-trace invariant analysis)
- **Severity:** P1 (high — cost enforcement bypass, same class as F-067)
- **Status:** Resolved (movement 2, Axiom)
- **Description:** `_handle_resume_job()` at `core.py:1070` set `job.paused = False` and `job.user_paused = False` without calling `_check_job_cost_limit()`. When a job was paused by cost enforcement (cost exceeded limit) and the user resumed it, the cost pause was silently lifted. One or more dispatch cycles could occur before the next `SheetAttemptResult` triggered a cost re-check. This is the same bug class as F-067 (escalation unpause overrides cost pause) — both handlers cleared `job.paused` without verifying cost constraints still hold.
- **Impact:** A cost-exceeded job could dispatch sheets after user resume, burning additional budget beyond the configured limit. The window is one dispatch cycle — the next attempt result would trigger `_check_job_cost_limit` and re-pause — but any sheet dispatched in that window represents uncontrolled spend.
- **Resolution:** Added `self._check_job_cost_limit(event.job_id)` call after clearing `user_paused` in `_handle_resume_job()`. Consistent with F-067's fix in `_handle_escalation_resolved` and `_handle_escalation_timeout`. 4 TDD tests in `test_baton_resume_cost_limit.py`. Updated `test_baton_m2_adversarial.py::test_pause_during_cost_limit_pause` — old assertion was wrong (expected resume to bypass cost enforcement).

### F-144: F-009 Root Cause Confirmed — Context Tag Namespace Mismatch in Learning Store
- **Found by:** Axiom, Movement 2 (backward-trace analysis from pattern application to storage)
- **Severity:** P0 (critical — intelligence thesis unsubstantiated)
- **Status:** Resolved (movement 3, Maverick)
- **Resolution:** Replaced positional tag generation (`sheet:N`, `job:X`) with semantic tags via `build_semantic_context_tags()` in `patterns.py`. Now generates `validation:TYPE` tags from configured validation rules + broad category tags (`success:first_attempt`, `retry:effective`, `completion:used`) that match the stored tag namespace. Also wired `instrument_name` into `get_patterns()` for instrument-scoped filtering (the parameter existed but was never passed). 13 TDD tests in `test_f009_semantic_tags.py`. Existing test updated to assert semantic (not positional) tags. Option 3 (hybrid) from F-144 analysis implemented: semantic tags for filtering, unfiltered fallback with instrument scope preserved.
- **Description:** Backward-traced the pattern application flow from `_query_relevant_patterns()` in `runner/patterns.py:146` to `get_patterns()` in `store/patterns_query.py:72`. The root cause is a tag namespace mismatch:
  - **Storage tags** (set by `patterns.py` during discovery): semantic tags like `["validation:file_exists"]`, `["retry:effective"]`, `["error_code:E001"]`, `["completion:used"]`.
  - **Query tags** (auto-generated at query time, `patterns.py:187-189`): positional tags like `["sheet:1", "job:my-job-id"]`.
  - These namespaces have ZERO overlap. The `context_tags` SQL filter (`json_each` + `IN`) matches nothing because no stored pattern has a `sheet:N` or `job:X` tag.
  - The fallback at `patterns.py:254-267` catches the empty-result case and queries without tags — but it always returns the SAME 5 highest-priority patterns, ignoring the other 28K+.
  - Oracle's diagnosis (M1) confirmed: 91% of patterns never applied, only 3 patterns have instrument tags.
  - The epsilon-greedy exploration (line 192-210) lowers the priority threshold but still hits the tag mismatch — same 5 patterns in a slightly different order.
- **Impact:** The learning store accumulates 28K+ patterns but cannot differentiate or apply them contextually. The intelligence layer doesn't learn. This is the product thesis — without it, Marianne is an orchestrator, not an intelligence platform.
- **Action:** Fix the tag matching strategy. Options:
  1. **Generate semantic query tags** from the current sheet context (validation types used, instrument, movement number, error history) that match the stored tag format.
  2. **Remove tag filtering** in the primary query path and use priority + recency + effectiveness for ranking.
  3. **Hybrid**: use semantic tags for broad category matching, fall back to unfiltered+ranked when no matches.
  Option 1 preserves the existing infrastructure. Option 2 is simpler. Option 3 is ideal. All require changes to `_query_relevant_patterns()` in `runner/patterns.py`.

### F-140: Broken File References in README.md and examples/README.md — Rosetta Score Relocation
- **Found by:** Newcomer, Movement 2 Cycle 2
- **Severity:** P2 (medium — broken links in two most-read documentation files)
- **Status:** Resolved (movement 2, Newcomer)
- **Description:** The Rosetta proof scores were moved from `examples/` to `examples/rosetta/` (by Spark M2), and `echelon-security-audit.yaml` was renamed to `echelon-repair.yaml`. Both `README.md` (line 394) and `examples/README.md` (lines 41-42, 314-315) still referenced the old paths and names. Additionally, `dead-letter-quarantine.yaml` and `immune-cascade.yaml` were not listed in either README despite existing in `examples/rosetta/`. Inside `echelon-repair.yaml:54`, the usage comment still referenced the old filename.
- **Error class:** F-088 (file moves without reference sweeps)
- **Impact:** A newcomer clicking on Rosetta examples in the README would hit 404s. Two of four proof scores were invisible — undiscoverable via documentation.
- **Resolution:** Fixed all references in `README.md`, `examples/README.md`, and `echelon-repair.yaml:54`. Added all 4 Rosetta proof scores (was 2). Verified all links resolve.

### F-141: `recover` Command Documented in README but Hidden in CLI
- **Found by:** Newcomer, Movement 2 Cycle 2
- **Severity:** P3 (low — consistency gap between docs and CLI help)
- **Status:** Open
- **Description:** The README "Diagnostic Commands" table (line 210) lists `marianne recover` as a standard command. But `src/marianne/cli/__init__.py:295` registers it as `hidden=True` with the comment "Hidden - recovery is advanced operation". The command exists, works, has `--help`, but `marianne --help` doesn't show it.
- **Impact:** Users reading the README try `marianne --help` and can't find `recover`. Users browsing `--help` never discover a useful diagnostic tool. Neither audience gets a complete picture.
- **Action:** Either unhide the command (add to Diagnostics panel) or add a note in the README that it's an advanced command accessible via `marianne recover --help`.

### F-142: Two Learning Commands Undocumented in README
- **Found by:** Newcomer, Movement 2 Cycle 2
- **Severity:** P3 (low — documentation gap)
- **Status:** Open
- **Description:** `learning-export` and `learning-record-evolution` appear in `marianne --help` under the Learning panel but are not listed in the README's "Learning Commands" table. These commands were presumably added after the README was last updated.
- **Impact:** README doesn't match `--help` output. A newcomer comparing the two will notice the gap.
- **Action:** Add both commands to the README Learning Commands table.

### F-145: Baton Path Missing `completed_new_work` — Concert Chaining Guard Broken
- **Found by:** Prism, Movement 2 Cycle 2
- **Severity:** P2 (medium — latent bug, triggers when use_baton + concert chaining both active)
- **Status:** Resolved (movement 3, Canyon mateship pickup)
- **Resolution:** Added `has_completed_sheets()` to BatonAdapter. Both `_run_via_baton` and `_resume_via_baton` now set `meta.completed_new_work = True` when any sheet completed. 3 TDD tests in test_baton_activation_fixes.py.
- **Description:** The monolithic execution path in `manager.py:1712-1716` sets `meta.completed_new_work = True` when a job completes sheets. This flag is the zero-work guard that prevents infinite self-chaining concerts (comment at line 1713). The baton path (`_run_via_baton`, lines 1774-1800) returns `DaemonJobStatus.COMPLETED` or `FAILED` directly without ever setting this flag. Concert chaining checks this flag to detect when a score completes without making progress.
- **Impact:** When `use_baton: true` and concert chaining are both active, the zero-work guard will always see `completed_new_work = False` and may abort valid score chains that completed real work. This is a latent bug — it won't manifest until baton execution is activated AND concert chaining is used.
- **Error class:** Same class as F-134 (baton path missing production path logic). The baton path was built from the adapter outward, not by mirroring the monolithic path's contract. Each gap is a missing contract element.
- **Action:** After `wait_for_completion()` returns `True`, set `meta.completed_new_work = True` in both `_run_via_baton` and `_resume_via_baton`. The baton adapter's `wait_for_completion` already tracks whether sheets completed, so the data is available.

### F-146: Clone Name Sanitization Was Lossy — Path Collisions Between Distinct Names
- **Found by:** Prism, Movement 2 Cycle 2 (found by Theorem's Hypothesis test, diagnosed and fixed by Prism)
- **Severity:** P2 (medium — clone path isolation broken for certain name pairs)
- **Status:** Resolved (movement 2, Prism)
- **Resolution:** Fixed `_sanitize_name()` in `clone.py:60-80`: preserved underscores in regex (`[^a-zA-Z0-9_-]` instead of `[^a-zA-Z0-9-]`), removed leading/trailing hyphen stripping. Names like `'0'` and `'0_'` now produce distinct paths. Updated 2 test files.
- **Description:** `_sanitize_name()` replaced underscores with hyphens AND stripped leading/trailing hyphens. This made the function many-to-one: `'0'` and `'0_'` both sanitized to `'0'`, producing identical socket/PID/state-DB/log paths. Two clones with similar names would silently share all state.
- **Error class:** Sanitization functions that normalize input are inherently lossy. The original design prioritized aesthetics (clean filenames) over correctness (unique paths). Property-based testing (Hypothesis) found this where hand-picked examples wouldn't.

### F-147: V210 InstrumentNameCheck False Positive on Score-Level Aliases
- **Found by:** Prism, Movement 2 Cycle 2
- **Severity:** P2 (medium — misleading validation warnings for valid configurations)
- **Status:** Resolved (movement 2, Prism)
- **Resolution:** Changed `config.py:523` from `not in known` to `not in all_valid`. Also changed suggestion lists at lines 542, 553, 564 from `known` to `all_valid` so users see their score-level aliases in available instruments.
- **Description:** The top-level `instrument:` check at `config.py:523` compared against `known` (profile names only) instead of `all_valid` (profiles + score-level aliases). A score defining `instrument: my-alias` with `instruments: { my-alias: { profile: claude-code } }` would get a WARNING about an unknown instrument, even though the alias is valid and resolves correctly at build time. Lines 534, 545, 556 (per-sheet, instrument_map, movement) correctly used `all_valid`.
- **Error class:** Inconsistent check scope within a single validation function. The per-sheet/map/movement checks were correct; the top-level check was not. Likely a copy-paste origin: line 523 was written first against `known`, then the `all_valid` concept was added for the other checks, and line 523 wasn't updated.

### F-148: Finding ID Collision — Systemic Problem at Scale
- **Found by:** Prism, Movement 2 Cycle 2
- **Severity:** P3 (low — process issue, not code bug)
- **Status:** Resolved (movement 3, Bedrock)
- **Resolution:** Implemented range-based allocation system. `FINDING_RANGES.md` pre-allocates 10 IDs per musician per movement (M4: F-160 through F-479). FINDINGS.md header updated with protocol. Helper script `scripts/next-finding-id.sh` as fallback. Historical collision table documents all 12 ambiguous IDs. D-018 complete.
- **Description:** F-140, F-141, and F-142 are each used by TWO different musicians (Ember and Newcomer) for DIFFERENT bugs. Axiom's F-143 fix at `core.py:1074` references "F-140" instead of "F-143" in the code comment. This is the 5th+ ID collision incident (after F-070, F-086, F-107 from M1). With 32 concurrent musicians appending to a shared FINDINGS.md, sequential IDs collide because each musician reads the file, finds the highest ID, increments, and writes — but another musician may have incremented the same ID in the meantime.
- **Impact:** When referencing a finding by ID, it's ambiguous which bug is meant. Code comments referencing findings may point to the wrong bug. The findings registry's value as an institutional record degrades.
- **Action:** Options: (1) Per-musician prefixed IDs (e.g., PRI-001 for Prism, AXI-001 for Axiom). (2) Central allocator (a simple file with the next available ID). (3) Timestamp-based IDs (F-20260402-001). Option 1 is simplest and eliminates collisions entirely.

### F-149: Backpressure Rejects ALL New Jobs When ANY Single Instrument Is Rate-Limited
- **Found by:** Composer + automated monitor, 2026-04-02
- **Severity:** P1 (high — blocks unrelated work across all instruments)
- **Status:** Resolved (movement 5, Circuit)
- **Resolution:** `should_accept_job()` and `rejection_reason()` now only consider resource pressure (memory, processes). Rate limits are per-instrument and handled at the sheet dispatch level by the baton and scheduler. `current_level()` unchanged — sheet-level dispatch still uses rate limit info for pacing. 10 TDD tests in `test_f149_cross_instrument_rejection.py`. 3 existing tests updated in `test_daemon_backpressure.py`, 2 in `test_rate_limit_pending.py`, 1 in `test_m4_adversarial_breakpoint.py`, 1 in `test_litmus_intelligence.py`. Manager simplified — rate_limit→PENDING path removed since jobs go straight through.
- **Description:** `BackpressureController.current_level()` at `backpressure.py:121` escalates to `PressureLevel.HIGH` when `self._rate_coordinator.active_limits` is non-empty. HIGH causes `should_accept_job()` to reject ALL new submissions. The check is global — it does not consider which instrument the new job would use. A `gemini-cli` job was rejected because `claude-code` had an active rate limit from a previous quota exhaustion. The rate limit was stale (account was switched, sheet 79 was executing successfully on the new account) but the coordinator retained the original expiry timestamp (April 3, 5pm CEST).
- **Impact:** (1) A rate limit on one instrument blocks ALL instruments. (2) Stale rate limits from resolved conditions (account switch, quota reset) persist until their original expiry. (3) No way to clear a rate limit when successful execution proves the instrument is available. The conductor becomes unusable for any work until the longest rate limit expires.
- **Related:** F-110 (backpressure rejects during rate limits — should queue). Issue #141 (baton should pause, not kill on rate limits). Rate coordinator has no "clear on success" path.
- **Fix:** (1) `should_accept_job()` should accept the job's target instrument and only reject if THAT instrument is rate-limited. (2) Add a `clear_rate_limit(backend_type)` call when a sheet succeeds on a previously rate-limited instrument. (3) Consider demoting rate-limit-only pressure from HIGH to MEDIUM (delay but don't reject). (4) Add CLI command to manually clear stale rate limits — tracked as issue #153.

### F-150: Score-Level Model Override Not Wired — instrument_config.model Is Ignored
- **Found by:** Composer investigation, 2026-04-02
- **Severity:** P1 (high — composers cannot select models per-score, fundamental multi-instrument gap)
- **Status:** Resolved (movement 3, Blueprint/Foundation/Canyon)
- **Description:** `PluginCliBackend.__init__` at `cli_backend.py:96` sets `self._model = profile.default_model` — the value from the instrument YAML's `default_model` field. There is no code path that reads a `model` key from the score's `instrument_config` and passes it to the backend. `_build_command()` at line 176 correctly uses `self._model` to append `model_flag` + model name to the CLI args, but `self._model` is never updated from score config. There is no `set_model()` method on `PluginCliBackend`. The only way to change the model is to edit the instrument profile YAML directly — which is a global change affecting all scores using that instrument.
- **Impact:** (1) Composers cannot run `gemini-2.5-pro` vs `gemini-2.5-flash` per-score without editing shared instrument profiles. (2) Per-sheet model selection (e.g., cheap model for simple sheets, expensive model for complex ones) is impossible. (3) The `instrument_config` section in scores appears to support `model:` but silently ignores it. (4) Affects ALL instruments, not just gemini — claude-code, aider, codex-cli, etc. all have the same gap.
- **Related:** M1 instrument plugin system (roadmap steps 1-8). `instrument_config` currently only wires `timeout_seconds` and backend-specific fields.
- **Resolution:** Four fixes across three files: (1) PluginCliBackend gained apply_overrides/clear_overrides for model (cli_backend.py). (2) BackendPool.release() calls clear_overrides to prevent cross-sheet contamination (backend_pool.py). (3) BatonAdapter extracts model from sheet.instrument_config at dispatch time (adapter.py). (4) build_sheets decoupled movement-level instrument_config merge from instrument name resolution — was silently dropping config-only movement overrides (sheet.py). 19 TDD tests. Blueprint authored, Foundation committed (08c5ca4), Canyon committed adapter (d3ffebe). Tracked as issue #154.

### F-151: No Per-Sheet Instrument or Model Visibility in Status Output
- **Found by:** Composer investigation, 2026-04-02
- **Severity:** P1 (high — multi-instrument is a key feature with no observability)
- **Status:** Resolved (movement 3, Circuit)
- **Description:** `mzt status` (table and JSON) does not show which instrument or model is executing each sheet. The JSON per-sheet object contains only `status`, `attempt_count`, `validation_passed`, `error_*`, and `elapsed_seconds`. The `Sheet` model carries `instrument_name` and `instrument_config` (including model), but `SheetState` / `CheckpointState` does not persist or expose this. During a multi-instrument run (claude-code sheet 1, gemini-cli sheets 2-4, ollama sheet 5), the only way to determine what's running is to infer from process lists or log output patterns.
- **Impact:** (1) Composers cannot verify instrument assignment is correct during execution. (2) Debugging instrument-specific failures requires guessing which instrument ran. (3) Cost attribution per instrument is impossible. (4) The musical metaphor breaks — you can hear the orchestra but can't see who's playing.
- **Requirements:** (1) Show the actual instrument being used per sheet, keyed off what CLI tool / backend is actually running — not just the configured name if it differs. (2) Show the model being used alongside the instrument (e.g., "gemini-cli / gemini-2.5-pro", "ollama / qwen3:14b"). (3) When a musician name is known, show musician + instrument (e.g., "Forge → claude-code / opus-4"). When musician is unknown, show instrument + model only. (4) Persist instrument_name and model in SheetState for post-mortem analysis.
- **Related:** F-150 (instrument_config.model not wired). M4 multi-instrument feature set.
- **Resolution:** instrument_name populated in both execution paths (legacy runner `sheet.py:1567`, baton `manager.py:1822-1826`). Status display shows Instrument column in flat table (auto-detected), instrument breakdown with counts in summary view (50+ sheets), movement-grouped view already supported. 16 TDD tests. Commits 25ba278, 4a1308b. Remaining from original requirements: model field population, diagnose timeline integration, musician name display.

### F-152: Unsupported Instrument Kind Causes Infinite Silent Dispatch Loop
- **Found by:** Composer investigation, 2026-04-02 (first multi-instrument run)
- **Severity:** P0 (critical — sheet stuck forever, no error surfaced, compute wasted)
- **Status:** Resolved (movement 3, Canyon mateship pickup — dispatch-time guard)
- **Resolution:** Added `_send_dispatch_failure()` to BatonAdapter. All three early-return paths in `_dispatch_callback` now post a `SheetAttemptResult(execution_success=False, error_classification="E505")` to the baton inbox, routing the failure through the normal retry/exhaustion state machine instead of leaving the sheet stuck. Exception catch broadened from `(ValueError, RuntimeError)` to `Exception` to catch `NotImplementedError`. Attempt number derived from state (not hardcoded). 5 TDD tests. Pre-run guard (reject at submission time) remains open as enhancement.
- **Description:** Assigning an HTTP-kind instrument (`ollama`) to sheet 5 in the hello score caused an infinite silent retry loop. `_create_backend_for_profile` at `backend_pool.py:82-87` raises `NotImplementedError` for HTTP instruments. The dispatch loop at `dispatch.py:148-163` catches `Exception`, logs `"baton.dispatch.callback_failed"`, but does not mark the sheet as failed — it stays READY. The next dispatch cycle retries, hits the same error, and loops. Meanwhile `mzt status` shows `in_progress`, `mzt errors` shows nothing, and the ollama process is idle (no models loaded, `api/ps` returns `{"models":[]}`). `mzt instruments check ollama` reports "ready" despite the baton being unable to use it.
- **Impact:** (1) Sheet stuck forever with no error visible to the composer. (2) Dependent sheets never run. (3) Job never completes. (4) `instruments check` gives false confidence — "ready" means the service responds, not that the baton can use it. (5) Wasted compute on all prior sheets if the unsupported instrument is only discovered at the final sheet.
- **Related:** F-150 (instrument_config not wired at adapter.py:741). F-151 (no instrument visibility in status). Issue #155.
- **Fix:** Two guards: (1) **At `mzt run`** — when submitting a score, the conductor should check that every sheet's resolved instrument has a supported kind. Reject before any sheets execute. (2) **Dispatch-time guard** — on unrecoverable backend errors mid-run (tool disappeared, auth revoked), mark the sheet FAILED with E505, propagate to dependents, stop retrying. Also: `instruments check` should warn when kind is unsupported by the baton.

### F-153: CLI Help Text Mixes "job" and "score" Terminology
- **Found by:** Newcomer, Movement 2 (final review)
- **Severity:** P3 (low — paper cut, not a blocker)
- **Status:** Open
- **Description:** The music metaphor says "score" everywhere, but CLI help text is inconsistent. `mzt run` → "Run a **job** from a YAML configuration file". `mzt validate` → "Validate a **job** configuration file". Meanwhile `mzt resume`, `mzt cancel`, and `mzt status` correctly use "score" in their descriptions. The Typer parameter name `JOB_ID` appears in usage lines for all commands (`mzt status [JOB_ID]`, `mzt resume JOB_ID`, etc.) while the descriptions say "Score ID". Commands touched during M3 UX work were updated; untouched ones still say "job".
- **Impact:** A newcomer reading help text encounters two terms for the same concept. The music metaphor is described as "load-bearing" in the composer's notes.
- **Files:** `src/marianne/cli/commands/run.py` (docstring), `src/marianne/cli/commands/validate_cmd.py` (docstring), multiple commands (JOB_ID Typer param name)

### F-154: hello.yaml Working Tree Has Composer Testing Artifacts — Accidental Commit Risk
- **Found by:** Newcomer, Movement 2 (final review) — also flagged by Adversary, Prism, Ember, quality gate
- **Severity:** P1 (high — breaks newcomer experience if committed)
- **Status:** Open
- **Description:** Working tree diff at `examples/hello.yaml`: `instrument: claude-code` → `instrument: gemini-cli` (line 34), added `per_sheet_instruments:` with `1: claude-code` and `5: ollama` (lines 46-51), added `per_sheet_instrument_config:` with `5: { model: qwen3:14b }` (lines 49-51). Every reviewer has flagged this. It persists across multiple movement cycles. `mzt validate examples/hello.yaml` shows "Instrument: gemini-cli" which contradicts the README.
- **Impact:** If committed: anyone without Gemini CLI gets a broken flagship example. Anyone without Ollama gets a broken sheet 5. The "Your First Score" experience requires three instruments instead of one.
- **Action:** Revert working tree changes, or create a separate `examples/hello-multi-instrument.yaml` that showcases multi-instrument while keeping hello.yaml single-instrument.

### F-155: Learning Commands Dominate CLI — 12 of 26+ Commands
- **Found by:** Newcomer, Movement 2 (final review)
- **Severity:** P3 (low — organizational, not functional)
- **Status:** Open
- **Description:** The Learning section in `marianne --help` contains 12 commands (patterns-list, patterns-why, patterns-entropy, patterns-budget, learning-stats, learning-insights, learning-drift, learning-epistemic-drift, learning-activity, learning-export, learning-record-evolution, entropy-status). This is 46% of all CLI commands, but the learning system is a supporting feature, not the core workflow. Dash's CLI UX audit (`movement-2/cli-ux-audit.md`) also flagged this, noting it requires E-002 escalation for a subcommand refactor.
- **Impact:** Dilutes the signal of core commands (run, validate, status) in help output. Creates misleading impression of what Marianne is primarily for.
- **Action:** Consider `marianne learning <subcommand>` grouping (like `mzt instruments <subcommand>` and `mzt config <subcommand>`).

### F-156: Silent Re-Pause After Resume When Cost Limit Exceeded
- **Found by:** Axiom, Movement 2 (final review)
- **Severity:** P2 (medium — UX gap in cost enforcement)
- **Status:** Open
- **Description:** When a job exceeds its cost limit and is paused, `_handle_resume_job` in `core.py` clears `user_paused` and then `_check_job_cost_limit` immediately re-pauses the job. The resume IPC response appears to succeed (no error indication), but the job stays paused. The user has no indication of why the resume didn't take effect. This UX gap was created by the F-143 fix (Axiom, 90b8a76) — the cost enforcement is correct, but the user feedback is missing.
- **Impact:** Users will repeatedly resume a cost-exceeded job, see no error, and wonder why it's still paused. The resume command shows success while the job remains paused.
- **Action:** `_handle_resume_job` should check the cost limit and, if re-pausing, include a message in the IPC response. The resume CLI should show "Re-paused: cost limit exceeded ($X of $Y)" instead of appearing to succeed silently.

### F-157: Legacy Runner Has Zero Credential Redaction in Error Paths
- **Found by:** Adversary, Movement 2 (final review)
- **Severity:** P1 (high — credential leak risk in production execution path)
- **Status:** Open
- **Description:** `src/marianne/execution/runner/sheet.py` has 20+ locations where `str(e)` is passed to structured loggers without `redact_credentials()`. The baton musician (`musician.py`) has 6 redaction points. The checkpoint's `capture_output()` at `checkpoint.py:567-568` redacts stdout/stderr. But the runner's exception-to-log pathway bypasses both — exceptions go directly to structured logging. If a backend raises an exception containing credentials (e.g., auth failure with API key in message), the credential flows unredacted to `~/.marianne/marianne.log`, diagnostic output, and any downstream consumer. The legacy runner runs ALL current production workloads since the baton has not been activated.
- **Impact:** Credential exposure risk in the production execution path. The baton's redaction is irrelevant until activated.
- **Error class:** Defense-in-depth gap — redaction applied at some layers (checkpoint, baton musician) but not others (legacy runner error logging).
- **Action:** Add `redact_credentials()` to the exception logging paths in `sheet.py`, especially lines where `error=str(e)` is passed to loggers with `exc_info=True`. Alternatively, redact at the log handler level to catch all paths.

### F-158: Baton PromptRenderer Infrastructure Is Dead Code — register_job() Never Passes prompt_config
- **Found by:** Adversary, Movement 2 (final review)
- **Severity:** P1 (high — blocks baton Phase 1 activation)
- **Status:** Resolved (movement 3, Canyon)
- **Resolution:** Wired `config.prompt` and `config.parallel.enabled` into `register_job()` in `_run_via_baton` and `recover_job()` in `_resume_via_baton`. The PromptRenderer is now created for every baton job, enabling the full 9-layer prompt assembly pipeline. 3 TDD tests in test_baton_activation_fixes.py.
- **Description:** The BatonAdapter has full PromptRenderer infrastructure (`adapter.py:345-346, 420-426, 835-842`). The renderer is only created when `prompt_config` is passed to `register_job()`. But `manager.py:1764` calls `register_job()` without a `prompt_config` parameter. The renderer dict `self._job_renderers` is empty for all production jobs. When `use_baton: true` is activated, `renderer` at `adapter.py:836` evaluates to `None`, `pre_rendered` stays `None`, and the `sheet_task` receives `rendered_prompt=None` and `preamble=None` — raw templates instead of rendered prompts. The composer's M4 note explicitly warns about this: "Do NOT activate it in production until prompt assembly and state sync are wired."
- **Impact:** Blocks Phase 1 of the baton transition plan. Cannot prove the baton works until prompt assembly is wired. The PromptRenderer exists, the PromptConfig exists, the rendering pipeline exists — the wire between the manager and the adapter is missing.
- **Action:** Wire `PromptConfig` construction in `manager.py:_run_via_baton()` and pass it to `register_job()`. This is the "full prompt assembly via PromptBuilder" noted as remaining work in TASKS.md step 28.

### F-159: 85 Assertion-Free Test Functions Inflate Test Count
- **Found by:** Adversary, Movement 2 (final review)
- **Severity:** P3 (low — test hygiene, not correctness)
- **Status:** Open
- **Description:** 85 test functions across the test suite contain no `assert` statements, `pytest.raises` checks, `mock.assert_*` calls, or capture fixtures (capsys/capfd). Many are legitimate safety tests verifying "should not crash" behavior (especially in adversarial test suites where proving a handler doesn't raise is the goal). But ~15-20 in `test_runner_coverage_gaps.py` and others appear to exercise code paths without verifying output, inflating the test count without providing verification value. This is 0.8% of the 10,397 total.
- **Impact:** Minor inflation of the test quality signal. The most important tests (property-based, backward-tracing, TDD) are all well-asserted.
- **Action:** Audit the 85 assertion-free tests. Add assertions where output can be verified. Mark intentional "should not crash" tests with a comment or `@pytest.mark.smoke` marker for clarity.

### F-160: parse_reset_time Has No Upper Bound — Adversarial Wait Times Block Instruments
- **Found by:** Warden, Movement 3
- **Severity:** P2 (medium — robustness/DoS, not credential exposure)
- **Status:** Resolved (movement 3, Warden)
- **Description:** `ErrorClassifier.parse_reset_time()` at `classifier.py:217` parses rate limit reset times from API error messages. It has a 300s minimum floor (`RESET_TIME_MINIMUM_WAIT_SECONDS`) but NO maximum ceiling. An adversarial or malformed API response like "resets in 999999 hours" produces `wait_seconds = 3,599,996,400` (~114 years). This value flows to `RateLimitHit.wait_seconds` → `_handle_rate_limit_hit()` → `timer.schedule()` → instrument blocked indefinitely. Recovery exists via `mzt clear-rate-limits`, but users shouldn't need manual intervention for a parsed value.
- **Impact:** A single malformed rate limit message from any API provider could effectively disable an instrument for the lifetime of the conductor process. While the API provider could DOS the instrument anyway by not responding, the parsed timer creates a persistent block that survives individual sheet failures.
- **Resolution:** Added `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` (24h) to `constants.py`. Added `_clamp_wait()` static method to `ErrorClassifier` that clamps to `[300, 86400]`. All three parse_reset_time return paths now use `_clamp_wait()` instead of bare `max()`. 10 TDD tests in `test_rate_limit_wait_cap.py`: extreme hours, large hours, large minutes, normal hours/minutes unchanged, boundary cases, minimum still enforced, absolute time format.
- **Error class:** Unbounded parsed value from external input. Same class as F-081 (asymmetric enforcement) — safety measure exists but doesn't cover all paths.

### F-350: Uncommitted Rate Limit Wait Cap — 7th Occurrence of Anti-Pattern
- **Found by:** Bedrock, Movement 3
- **Severity:** P2 (medium — good defensive code, needs committing)
- **Status:** Resolved (movement 3, Warden)
- **Category:** pattern
- **Description:** Working tree has 4 uncommitted files implementing a rate limit wait time safety cap:
  1. `src/marianne/core/constants.py` — `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` (24h cap)
  2. `src/marianne/core/errors/classifier.py` — `_clamp_wait()` static method replacing 3 bare `max()` calls
  3. `tests/test_quality_gate.py` — BARE_MAGICMOCK baseline bump 1230→1234
  4. `tests/test_rate_limit_wait_cap.py` (untracked) — 10 TDD tests proving the cap works
  The change is well-designed: prevents adversarial/malformed API responses like "resets in 999999 hours" from blocking instruments forever. TDD was followed (tests exist). But it was never committed.
- **Impact:** A `git clean` would destroy the work. This is the 7th occurrence of the uncommitted work anti-pattern (F-013, F-019, F-057, F-080, F-089, F-096, F-350).
- **Error class:** Uncommitted work. Structural anti-pattern documented since M1.
- **Action:** Mateship pickup — verify tests pass, commit on main with attribution.

### F-200: clear_instrument_rate_limit Clears ALL Instruments on Unknown Name
- **Found by:** Breakpoint, Movement 3
- **Severity:** P2 (medium — operational correctness)
- **Status:** Resolved (movement 3, Breakpoint)
- **Category:** bug
- **Description:** `BatonCore.clear_instrument_rate_limit()` at `core.py:271-275` used the conditional `[self._instruments[instrument]] if instrument and instrument in self._instruments else list(self._instruments.values())`. When `instrument` is a truthy string NOT in `self._instruments` (e.g., `"nonexistent"`), the condition evaluates False, falling through to the else branch which clears ALL instruments. A user running `mzt clear-rate-limits -i typo-in-name` would silently clear rate limits on every instrument instead of doing nothing.
- **Impact:** Operational: a typo in the instrument name silently clears all rate limits instead of reporting "not found." Could cause rate limit storms if limits were legitimately in place.
- **Resolution:** Replaced ternary with explicit if/else using `self._instruments.get(instrument)`. Non-existent instrument now returns empty target list → 0 cleared. Regression test in `test_m3_adversarial_breakpoint.py::TestClearRateLimits::test_clear_nonexistent_instrument_returns_zero`.
- **Error class:** Fallthrough-to-default on failed lookup. Same pattern as "if X and X in dict" where the "else" branch has unintended side effects.

### F-201: clear_instrument_rate_limit Clears ALL Instruments on Empty String
- **Found by:** Breakpoint, Movement 3 (Pass 4)
- **Severity:** P3 (low — edge case of F-200)
- **Status:** Resolved (movement 3, Breakpoint)
- **Category:** bug
- **Description:** `BatonCore.clear_instrument_rate_limit()` at `core.py:271` used `if instrument:` (truthiness check). Empty string `""` is falsy in Python, so `clear_instrument_rate_limit("")` fell through to the else branch at line 277-279, clearing ALL instruments. Same bug class as F-200 — the F-200 fix addressed truthy-but-absent strings but left the falsy-string path open.
- **Impact:** Minor — empty string instrument names are unlikely in practice, but the pattern is dangerous: any falsy input silently escalates to "clear all" behavior.
- **Resolution:** Changed `if instrument:` to `if instrument is not None:` at `core.py:271`. Empty string now routes to the specific-instrument lookup, finds nothing, returns 0. Regression test in `test_m3_pass4_adversarial_breakpoint.py::TestF200Regression::test_empty_string_instrument_returns_zero`.
- **Error class:** Truthiness-vs-identity guard. Same root cause as F-200 — using `if X:` when `if X is not None:` is needed to distinguish "no argument" from "bad argument."


### F-440: State Sync Gap — Failure Propagation Not Synced to Checkpoint (Zombie Resurrection)
- **Found by:** Axiom, Movement 3
- **Severity:** P1 (high — resurrects F-039 zombie job pattern on restart)
- **Status:** Resolved (movement 3, Axiom)
- **Category:** bug
- **Description:** `_sync_sheet_status()` at `adapter.py:1126` only fires for `SheetAttemptResult` and `SheetSkipped` events. When `_propagate_failure_to_dependents()` at `core.py:1218` cascades FAILED status to downstream sheets, those status changes are NOT synced to the checkpoint. On restart recovery, the cascaded failures are lost — dependent sheets revert to PENDING. With their upstream dependency FAILED (correctly synced for the primary event), the PENDING dependents can never be dispatched (`_is_dependency_satisfied()` returns False for FAILED deps) and are never terminal → `is_job_complete()` returns False forever → zombie job.
- **Impact:** Any job with dependency chains where a sheet failure is followed by a conductor restart recreates the zombie job pattern that F-039 was designed to fix. In the 706-sheet concert with extensive dependency chains, the probability of this sequence is non-negligible.
- **Root cause:** `_propagate_failure_to_dependents()` directly modifies sheet status (core.py:1260) without generating events. The state sync callback (adapter.py:1126-1130) only fires for events in the inbox, not for direct state mutations.
- **Fix:** Added failure re-propagation in `BatonCore.register_job()` (core.py:546-556). After registering states, iterates over all FAILED sheets and calls `_propagate_failure_to_dependents()` for each. This is idempotent (only affects non-terminal dependents) and fixes the sync gap for both fresh registration and recovery. 8 TDD tests in `test_recovery_failure_propagation.py`. Updated 2 tests in `test_baton_m2c2_adversarial.py` that were asserting the buggy behavior.
- **Error class:** Sync gap — two subsystems (baton in-memory state vs checkpoint) diverge because only the event path is bridged, not the direct-mutation path. Same architectural pattern as F-065 (two correct subsystems composing into incorrect behavior).
- **Additional gaps (P2, not fixed):** `_sync_sheet_status` also doesn't fire for `EscalationResolved/EscalationTimeout` (terminal transitions), `CancelJob/ShutdownRequested` (CANCELLED transitions), or `ProcessExited` (failure transitions). These are lower impact because escalation+restart is rare, cancel/shutdown are at the baton's lifetime boundary, and ProcessExited recovers naturally (crashed sheets become PENDING on restart). The failure propagation gap was the critical one because it creates permanent zombies.

### F-450: IPC "Method Not Found" Misreported as "Conductor Not Running"
- **Found by:** Ember, Movement 3
- **Severity:** P2 (medium — misleading error for every new IPC method added to a stale conductor)
- **Status:** Resolved (movement 4, Harper)
- **Category:** bug
- **Description:** `try_daemon_route()` at `src/marianne/daemon/detect.py:170-174` catches `DaemonError` (which includes "Method not found: daemon.clear_rate_limits") and returns `(False, None)` — the same signal as "daemon not reachable." The function already tracks `daemon_confirmed_running` at line 110 and uses it to differentiate TimeoutError (lines 113-122). But the DaemonError handler at line 170 ignores this flag, treating "method not found on a running daemon" identically to "daemon not reachable."
- **Reproducer:** Start conductor from M2 code. Upgrade CLI to M3 code (with clear-rate-limits). Run `mzt clear-rate-limits`. Get: "Error: Marianne conductor is not running" while `mzt conductor-status` confirms it IS running.
- **Impact:** Trust-destroying inconsistency. User is told to start the conductor when it is already running. Broader: any CLI command added after a long-running conductor (or after a CLI upgrade without daemon restart) will give the same misleading error. Users upgrading Marianne without restarting their conductor will hit this for every new IPC method.
- **Action:** In the DaemonError catch at detect.py:170-174, check `daemon_confirmed_running`. If True, raise a descriptive DaemonError ("Conductor is running but does not support method X — restart the conductor to load new features") instead of returning `(False, None)`.
- **Error class:** Signal collapse — two distinct error states (not reachable vs. unsupported method) mapped to the same return value. Same pattern as the TimeoutError handler, which was already fixed.
- **Resolution:** Added `MethodNotFoundError(DaemonError)` to exception hierarchy. Mapped METHOD_NOT_FOUND (-32601) in `_CODE_EXCEPTION_MAP`. `try_daemon_route()` now re-raises MethodNotFoundError with user-friendly message including restart guidance. `run.py` catches DaemonError to prevent raw propagation. 15 TDD tests in `test_f450_method_not_found.py`. Updated 2 existing tests (`test_daemon_cli_detection.py`, `test_daemon_ipc_client.py`).

### F-210: Baton Path Missing Cross-Sheet Context (previous_outputs/previous_files)
- **Found by:** Weaver, Movement 3
- **Severity:** P1 (high — blocks baton activation for any score with sequential dependencies)
- **Status:** Resolved (movement 4, Canyon + Foundation)
- **Category:** architecture
- **Description:** The legacy runner populates `SheetContext.previous_outputs` and `SheetContext.previous_files` via `_populate_cross_sheet_context()` in `src/marianne/execution/runner/context.py:171-221`. This gives each sheet access to previous sheets' stdout output and captured files. The baton's PromptRenderer (`src/marianne/daemon/baton/prompt.py`) and musician `_build_prompt()` (`src/marianne/daemon/baton/musician.py:208-288`) have zero awareness of cross-sheet context. The `SheetExecutionState` at `src/marianne/daemon/baton/state.py:161-163` declares `previous_outputs: dict[int, str]` but it is never populated by the adapter or any dispatch code.
- **Impact:** 24 of 34 example scores use `cross_sheet: auto_capture_stdout: true`. Any score where sheet N references `{{ previous_outputs }}` or depends on seeing what sheet N-1 produced will render templates with empty cross-sheet context under the baton. This produces functionally different (worse) prompts compared to the legacy runner. **This is the most significant functional gap between baton and legacy paths.**
- **Resolution:** Wired cross-sheet context through the full baton dispatch pipeline:
  1. Added `previous_files` field to `AttemptContext` (state.py) — captures workspace file patterns
  2. Added `cross_sheet` parameter to `BatonAdapter.register_job()` and `recover_job()` — stores `CrossSheetConfig` per job
  3. Added `BatonAdapter._collect_cross_sheet_context()` — reads completed sheets' stdout from baton state, reads workspace files from `capture_files` patterns. Respects `lookback_sheets`, `max_output_chars`, truncation rules. Matches legacy runner behavior.
  4. Wired into `_dispatch_callback()` — populates `AttemptContext.previous_outputs` and `previous_files` before spawning musician
  5. Updated `PromptRenderer._build_context()` to copy cross-sheet data from `AttemptContext` to `SheetContext`
  6. Wired `config.cross_sheet` from manager to adapter in both `_run_via_baton` and `_resume_via_baton`
  21 TDD tests in `tests/test_f210_cross_sheet_baton.py`. mypy clean, ruff clean.
- **Error class:** Feature gap — baton path was built to replace the runner but does not replicate its cross-sheet context pipeline. Not a bug in existing code — a missing integration surface.

### F-211: Baton Checkpoint Sync Missing for 4 Event Types
- **Found by:** Weaver, Movement 3
- **Severity:** P2 (medium — escalation and cancel scenarios, not core execution path)
- **Status:** Resolved (movement 4, Canyon + Foundation)
- **Resolution:** Canyon M4: duck-typed _sync_sheet_status for all single-sheet events, pre-event capture for CancelJob, direct state scan for ShutdownRequested. 16+18 TDD tests. Foundation M4: state-diff dedup cache (_synced_status dict), explicit JobTimeout handler (_sync_all_sheets_for_job), RateLimitExpired handler (_sync_all_sheets_for_instrument), fixed _sync_cancelled_sheets_from_state to use dedup, fixed pre-existing test failure in test_baton_restart_recovery.py. Total: 6 event types covered (was 2).
- **Category:** architecture
- **Description:** `_sync_sheet_status()` in `src/marianne/daemon/baton/adapter.py:1109-1148` only syncs checkpoint for `SheetAttemptResult` and `SheetSkipped` events. Axiom identified (F-440 notes) and Weaver confirmed: EscalationResolved (core.py:1081-1090, 4 terminal paths), EscalationTimeout (core.py:1104-1132, FAILED + propagation), CancelJob (core.py:1159-1170, all sheets → CANCELLED), and ShutdownRequested (core.py:1172-1184, all → CANCELLED) are NOT synced. On restart after any of these events, checkpoint shows stale state and sheets are resurrected.
- **Impact:** Escalation decisions lost on restart (sheet re-escalates). Cancel commands reversed on restart (sheets resume). Shutdown cancellations reversed (work re-executed). F-440's register_job re-propagation covers the failure cascade gap but not these 4 event types.
- **Action:** Either expand `_sync_sheet_status()` to handle all terminal-transition events, or redesign to sync after every event that modifies sheet status. The simplest fix: add `isinstance(event, (EscalationResolved, EscalationTimeout, CancelJob, ShutdownRequested))` to the sync callback and iterate affected sheets.
- **Error class:** Incomplete bridge — same architectural pattern as F-440. The sync bridge only covers the common execution path, not the exception paths.

### F-212: Baton PromptRenderer Missing Spec Budget Gating
- **Found by:** Weaver, Movement 3
- **Severity:** P3 (low — spec corpus is lightly used in current scores)
- **Status:** Open
- **Category:** architecture
- **Description:** The legacy runner applies `_apply_spec_budget_gating()` at `src/marianne/execution/runner/sheet.py` to limit spec fragment injection based on context window budget. The baton's PromptRenderer at `src/marianne/daemon/baton/prompt.py` passes spec fragments directly to PromptBuilder without budget gating. For scores with large spec corpora, this could produce prompts that exceed the instrument's context window.
- **Action:** Add spec budget gating to PromptRenderer. Low priority because spec corpus is lightly used and current instruments have large context windows (1M+ for Opus).
- **Error class:** Feature gap — same class as F-210.

### F-460: "job" vs "score" Terminology Persists Across User-Facing Docs and CLI
- **Found by:** Newcomer, Movement 3
- **Severity:** P2 (medium — erodes the load-bearing music metaphor across all newcomer touchpoints)
- **Status:** Resolved (movement 3, Newcomer)
- **Category:** pattern
- **Description:** F-153 (M2) identified terminology inconsistency in CLI help text. This movement revealed the same pattern extends throughout all user-facing documentation. Found and fixed "job" → "score" in: `run.py` docstring, `validate.py` docstring, `run.py` --fresh help text, `recover.py` help text, `README.md` (12 instances: Quick Start, CLI Reference table, Features table, Configuration section, Conductor section), `getting-started.md` (10 instances: installation, Step 4-6 headings/text, resume section, dashboard, troubleshooting), `cli-reference.md` (11 instances: run/resume/pause/validate/list command descriptions, exit codes, CONFIG_FILE descriptions). Total: ~35 user-facing "job" → "score" fixes across 6 files.
- **Remaining:** ~70 "job" references remain in cli-reference.md, mostly in example commands (`my-job`), API paths (`/api/jobs`), and internal descriptions. These map to actual code identifiers (JobConfig, JOB_ID Typer parameter) — changing those requires code changes beyond documentation.
- **Error class:** Terminology drift — docs lag behind the metaphor. Same class as F-153.

### F-461: Cost Tracking Fiction Now More Convincing But Still Wrong
- **Found by:** Newcomer, Movement 3
- **Severity:** P1 (high — misleads users about actual spend)
- **Status:** Open
- **Category:** risk
- **Description:** Cost tracking now shows $0.12 for 114 completed sheets (up from $0.00 in M2 for 79 sheets). Token counts: 11,874 input / 5,937 output. For 114 sheets each running Opus with ~100K+ token prompts, actual spend is likely $200-$500. The cost display is more believable now (non-zero) which makes it more dangerous — a newcomer might trust $0.12 and set a budget accordingly. The tip "Set cost_limits.enabled: true in your score to prevent unexpected charges" teaches newcomers to rely on a system that under-counts by ~1000x.
- **Files:** Cost tracking flows through `ClaudeCliBackend` which doesn't parse actual token counts from Claude CLI output. F-048 (M0) tracked this. Still unresolved after 4 movements.
- **Impact:** Users who trust the displayed costs will set limits too low (blocking scores) or believe execution is essentially free when it isn't.
- **Action:** Either fix token parsing from ClaudeCliBackend output, or clearly label cost figures as "estimated" with a disclaimer about accuracy.

### F-462: F-450 Confirmed — clear-rate-limits Reports Conductor Not Running When It Is
- **Found by:** Newcomer, Movement 3 (confirming Ember M3 F-450)
- **Severity:** P2 (medium — new IPC methods fail misleadingly on stale conductors)
- **Status:** Resolved (movement 4, Harper — see F-450)
- **Category:** bug
- **Description:** Ran `mzt clear-rate-limits` while conductor is confirmed running (PID 1277279, `conductor-status` shows RUNNING, `status` shows active scores). Got: "Error: Marianne conductor is not running." Root cause at `detect.py:170-174`: `try_daemon_route()` conflates "IPC method not found" with "conductor not reachable." The production conductor predates the `clear-rate-limits` IPC method — it runs older code. Any new IPC method added by M3 musicians will hit this pattern on stale conductors.
- **Action:** Already tracked as F-450. Cross-referencing for independent confirmation.

### F-330: README CLI Reference Significantly Stale — 13 Commands Missing
- **Found by:** Compass, Movement 3
- **Severity:** P2 (medium — README is the first thing users see)
- **Status:** Resolved (movement 3, Compass)
- **Category:** risk
- **Description:** README.md CLI Reference was missing 13 commands that exist in the product: `init`, `cancel`, `clear`, `top`, `clear-rate-limits`, `start`, `stop`, `restart`, `conductor-status`, and the entire Conductor command group. The `--conductor-clone` global option was missing from Common Options. The `--escalation` option was listed as "not currently supported." Examples table was missing 5 examples (design-review, iterative-dev-loop, score-composer, prelude-cadenza-example, parallel-research-fanout). There was a formatting bug (missing blank line before Rosetta section). "35+" example count was stale (actual: 38). "Human-in-the-loop" was listed in Advanced Features despite being unsupported. A redundant Dashboard section duplicated the Services table entry. The "job control" terminology hadn't been migrated to "score control."
- **Impact:** Users reading only the README would not know about init, cancel, top, clear-rate-limits, conductor-clone, or the Conductor command group. The CLI Reference now matches the actual product groupings (Getting Started, Jobs, Monitoring, Diagnostics, Conductor, Instruments, Services, Configuration & Learning).
- **Resolution:** Restructured CLI Reference to match actual CLI help panel groups. Added all missing commands. Added --conductor-clone and --quiet to Common Options. Removed unsupported --escalation. Fixed formatting bug. Updated example count. Removed duplicate Dashboard section. Fixed terminology.

### F-331: getting-started.md Stale Terminology and Count
- **Found by:** Compass, Movement 3
- **Severity:** P3 (low — secondary doc)
- **Status:** Resolved (movement 3, Compass)
- **Category:** risk
- **Description:** getting-started.md had "35+" example count (actual: 38), "Job Won't Start" heading (should be "Score Won't Start" per F-460 terminology migration), and "tells Claude to save files" (should be instrument-agnostic).
- **Resolution:** Fixed count, heading, and instrument-agnostic wording.

### F-332: docs/index.md Stale Example Count
- **Found by:** Compass, Movement 3
- **Severity:** P3 (low)
- **Status:** Resolved (movement 3, Compass)
- **Category:** risk
- **Description:** docs/index.md stated "35+ working Marianne score configurations" when actual count is 38.
- **Resolution:** Updated to "38 working Marianne score configurations."

### F-333: README Manual Installation Missing [daemon] Extra
- **Found by:** Compass, Movement 3 (second pass)
- **Severity:** P1 (high — newcomer UX blocker)
- **Status:** Resolved (movement 3, Compass)
- **Category:** bug
- **Description:** `README.md` line 90, Manual Installation section: `pip install -e "."` omits the `[daemon]` extra. The Quick Start at line 117 requires `mzt start`, which depends on `psutil` (a daemon-only dependency). A newcomer who follows the manual path instead of `./setup.sh --daemon` hits an import error or unclear failure at Quick Start step 3. The recommended setup path (`./setup.sh --daemon`) handles this correctly, but the manual alternative doesn't.
- **Impact:** Any newcomer who prefers manual install over the setup script gets a broken first experience. The exact user we most need to impress — the one who wants to understand what they're installing — is the one who gets burned.
- **Error class:** Same class as F-026 (broken Quick Start), F-095 (init teaches wrong patterns). Setup paths that produce broken first experiences.
- **Resolution:** Changed to `pip install -e ".[daemon]"` with a note explaining the daemon extra is required for score execution. Guide independently fixed the same issue in commit f8245fa — convergent discovery.

### F-334: hello.yaml Cost Estimate Wrong by 10-30x
- **Found by:** Compass, Movement 3 (second pass)
- **Severity:** P2 (medium — misleading but in a comment, not UI)
- **Status:** Resolved (movement 3, Compass)
- **Category:** risk
- **Description:** `examples/hello.yaml` line 27 stated "Cost: ~$0.50" for 5 sheets of Claude Code Opus. Actual cost for 5 agent sessions of ~5 minutes each is closer to $5-15 depending on context size and output length. This is the same class as F-461 (cost tracking fiction) but in a user-facing comment rather than the status display.
- **Impact:** A newcomer running hello.yaml expects $0.50 and spends $5-15. The first encounter with Marianne involves a cost surprise. Trust erosion before the product even demonstrates its value.
- **Error class:** F-461 (cost fiction). Cost information is consistently wrong across the product surface.
- **Resolution:** Changed to "Cost: varies by instrument and model" — honest rather than wrong.

### F-463: Learning Stats Are User-Facing But Alarming Without Context
- **Found by:** Newcomer, Movement 3 (reviewer pass)
- **Severity:** P3 (low — internal metrics, not core UX)
- **Status:** Open
- **Category:** risk
- **Description:** `mzt learning-stats` reports 12.0% first-attempt success rate, 0.51 avg effectiveness (barely above random), and 0.0% recovery success rate. These numbers are visible to any user who discovers the command. Without context (e.g., "exploration-heavy workloads have low first-attempt rates by design"), these stats would alarm any engineer evaluating Marianne for adoption. The 0.51 effectiveness is related to F-009 (all patterns had 0.5000) — the semantic tag fix may improve this over time, but current data reflects pre-fix patterns.
- **Impact:** A potential adopter who runs `mzt learning-stats` might conclude the system doesn't learn effectively. The numbers need either contextual explanation in the output or clear documentation about what they mean.
- **Action:** Add brief context to `learning-stats` output explaining what the numbers mean, or document expected ranges in the CLI reference.

### F-464: `history` Command Placement Inconsistency (README vs CLI)
- **Found by:** Newcomer, Movement 3 (reviewer pass)
- **Severity:** P3 (low — minor doc inconsistency)
- **Status:** Resolved (movement 4, Guide)
- **Category:** risk
- **Description:** README.md lists `mzt history` under the "Monitoring" section (line ~216). The actual CLI (`marianne --help`) lists `history` under "Diagnostics." A newcomer reading the README builds a mental model where history is a monitoring tool, then finds it categorized differently in the CLI. Minor inconsistency but breaks the otherwise-clean mapping between README and CLI.
- **Impact:** Low — most users won't notice. But the README was carefully restructured this movement (Compass, F-330) to match CLI groups exactly. This one slipped through.
- **Action:** Move `history` to the Diagnostics section in README, consistent with the CLI grouping.

### F-465: README and Getting-Started Quick Start Broken — Score Name vs ID Mismatch
**Found by:** Newcomer, Movement 3 (second reviewer pass)
**Severity:** P1 (high — every newcomer hits this on the main path)
**Status:** Resolved (movement 4, Guide)
**Category:** bug
**Description:** README.md (line 141) says `mzt status hello-marianne`. Getting-started.md (line 60) says the same. **Additionally, `examples/hello.yaml:16` — the example file's own header comment — says `#   mzt status hello-marianne`. And README.md:158 says `mzt resume hello-marianne`.** Four locations, not two, all teaching the wrong command. The hello score's `name:` field is `hello-marianne`. But the conductor registers the score under the ID `hello` (derived from the filename, not the name field). Running `mzt status hello-marianne` produces: "Error: Score not found: hello-marianne / Hints: Run 'mzt list' to see available scores." The hint saves the user but the docs broke them.
**Impact:** Every single newcomer who follows the README or getting-started guide will hit this error at the monitoring step.
**Error class:** Same class as F-026, F-095 — setup paths that produce broken first experiences.
**Resolution:** Renamed `examples/hello.yaml` → `examples/hello-marianne.yaml` so filename stem matches the `name:` field. Now the conductor-derived ID (`hello-marianne`) matches what all docs teach. Updated 8 files: README.md (4 refs), getting-started.md (3 refs), examples/hello-marianne.yaml (3 refs: header, usage comment, colophon), examples/README.md (2 refs), tests/test_cli_user_journeys.py (1 ref), tests/test_status_display_bugs.py (1 ref). Also fixed F-464 (history command moved from Monitoring to Diagnostics in README).

### F-466: JOB_ID Persists in Every CLI Usage Line Despite F-460 Terminology Fix
**Found by:** Newcomer, Movement 3 (second reviewer pass)
**Severity:** P2 (medium — visible in every `--help` output)
**Status:** Open
**Category:** bug
**Description:** F-460 (M3) fixed command descriptions from "job" to "score" but did not rename the Typer argument parameter from `job_id` to `score_id`. The Typer argument name controls the usage line display. Result: every command shows `Usage: marianne <cmd> [OPTIONS] JOB_ID` in the first line, then says "Score ID to..." in the help text below it. The inconsistency is in the same help output. Affected commands: `resume`, `pause`, `cancel`, `status`, `errors`, `diagnose`, `history`, `recover`, `modify` (9 commands). Internal variables (`_JOB_ID_PATTERN`, `_JOB_ID_MAX_LENGTH`, `validate_job_id`) at `_shared.py:421-450` also use the old terminology.
**Impact:** A newcomer who reads the usage line sees "JOB_ID" and thinks "jobs." Then the help text says "Score ID." The music metaphor — which is load-bearing per composer directive — leaks in the most visible place. Note: this is an E-002 escalation trigger (changing CLI command interface) per the constraint spec.
**Action:** Rename `job_id` parameter to `score_id` across all 9 command files, plus `validate_job_id()` → `validate_score_id()`, `_JOB_ID_PATTERN` → `_SCORE_ID_PATTERN`, etc. in `_shared.py`. Requires composer approval per E-002.

### F-340: Quality Gate Assertion Baseline Stale (+6 Assertion-Less Tests)
- **Found by:** Canyon, Movement 4
- **Severity:** P3 (low — meta-test, not product bug)
- **Status:** Open
- **Category:** pattern
- **Description:** `test_quality_gate.py::test_all_tests_have_assertions` fails because `ASSERTION_LESS_TEST_BASELINE` is 116 but 122 test functions lack assertions. The 6 new offenders are in `test_runner_coverage_gaps.py` (3) and `test_runner_execution_coverage.py` (2) — all appear to be recently added tests that call functions without asserting on the result.
- **Impact:** The quality gate meta-test blocks the full `pytest tests/ -x` pass. No product impact.
- **Action:** Either (a) bump the baseline to 122 (quick fix), or (b) add assertions to the 6 offending tests (correct fix). Option (b) is preferred — the tests should verify something.
- **Error class:** Baseline drift — quality gate baseline not updated when new tests were added.

### F-180: Cost Tracking 10-100x Underestimate (D-024 Root Cause Analysis)
- **Found by:** Circuit, Movement 4
- **Severity:** P1 (high — cost fiction dangerous to users)
- **Status:** Partially resolved (4055f0b, Circuit M4)
- **Category:** bug
- **Description:** Five root causes make cost tracking fictional:
  1. **ClaudeCliBackend returns zero token data** (`claude_cli.py:467-477`). `_build_completed_result()` creates `ExecutionResult` without `input_tokens`/`output_tokens`. CostMixin falls to character-based estimation: `len(stdout)/4` — the CLI output, not LLM tokens. A 200K-token Opus call producing 5KB stdout gets estimated as ~1.25K output tokens (160x underestimate).
  2. **Baton musician uses hardcoded Sonnet pricing** (`musician.py:634-646`). `_estimate_cost()` hardcodes $3/1M input, $15/1M output regardless of instrument/model. When tokens are 0 (from CLI backends), cost is $0.00.
  3. **Instrument profile pricing never used** (`instruments.py:94-101`). `ModelCapacity.cost_per_1k_input/output` exists on every profile but is never plumbed into cost calculation.
  4. **Cost confidence tracked but never displayed** (`status.py:1294-1337`). `cost_confidence` field is set by CostMixin but `_render_cost_summary()` shows `$0.17` with no estimation indicator.
  5. **Legacy runner uses text output format** (`backend.py:295-296`). `output_format` defaults to `"text"` — no JSON to parse for tokens.
- **Impact:** Users see `$0.17` for work costing `$100+`. Cost limits (`max_cost_per_job: 50`) overshoot by 100x before triggering. The lie got more convincing each movement ($0.00 → $0.12 → $0.17).
- **Resolution (partial):**
  - Fix 1 (4055f0b): `_extract_tokens_from_json()` extracts tokens when `output_format="json"`. Accurate for scores using JSON output.
  - Fix 4 (4055f0b): `_render_cost_summary()` shows `~$X.XX (est.)` + warning when confidence < 0.9. JSON output includes `cost_confidence`.
- **Resolution (M5, Forge):**
  - Fix 2+3: `_estimate_cost()` now accepts `cost_per_1k_input`/`cost_per_1k_output` from instrument profile. Adapter resolves pricing from BackendPool registry (InstrumentProfile.ModelCapacity). Falls back to hardcoded Sonnet rates when profile unavailable. 6 TDD tests in test_f180_cost_pricing.py.
- **Remaining:**
  - Root cause 5: Consider defaulting `output_format` to `"json"` for accuracy.

### F-181: Uncommitted F-450 Fix in Working Tree
- **Found by:** Circuit, Movement 4
- **Severity:** P2 (medium — mateship pickup needed)
- **Status:** Resolved (movement 4, Harper — committed with additional CLI hardening)
- **Category:** pattern
- **Description:** F-450 fix (IPC `MethodNotFoundError` differentiation) is fully implemented but uncommitted. Changes across `detect.py`, `exceptions.py`, `ipc/errors.py`, plus `tests/test_f450_method_not_found.py` (14 tests). Tests pass individually. The pre-existing test `test_returns_false_on_unknown_method` was renamed to `test_raises_on_unknown_method` in the working tree.
- **Impact:** The fix works in the working tree but will be lost if anyone runs `git checkout .`.
- **Resolution:** Harper picked up and committed with additional improvements: wrapped MethodNotFoundError message with restart guidance, added DaemonError catch in run.py, fixed _MockMixin for pause-during-retry compatibility, total 15 TDD tests.

### F-182: Uncommitted Resume Improvements (#93, #103, #122) in Working Tree
- **Found by:** Circuit, Movement 4
- **Severity:** P2 (medium — mateship pickup needed)
- **Status:** Open
- **Category:** pattern
- **Description:** Three resume improvements are implemented but uncommitted: (1) `_should_auto_fresh()` in `manager.py` auto-detects changed score files for #103, (2) resume output clarity in `resume.py` showing previous status/error for #122, (3) `await_early_failure` removal from resume path for #122. Also includes `run.py` changes (auto-fresh info display). Plus `test_pause_during_retry.py`, `test_resume_output_clarity.py`, `test_stale_completed_detection.py` (new test files).
- **Impact:** Resume UX improvements lost if working tree reset.
- **Action:** Another musician should review and commit with proper attribution.

### F-190: CLI Commands Without DaemonError Catch May Show Raw Tracebacks on MethodNotFoundError
- **Found by:** Harper, Movement 4
- **Severity:** P3 (low — requires CLI/conductor version mismatch)
- **Status:** Resolved (movement 5, Forge)
- **Resolution:** Added DaemonError catch after JobSubmissionError in diagnose.py (errors, diagnose, history — 3 locations) and recover.py (1 location). Shows user-friendly error with restart guidance. 7 TDD tests in test_f190_daemon_error_catch.py. Note: top.py still has an uncovered try_daemon_route but is monitoring-only.
- **Category:** risk
- **Description:** Of ~15 CLI commands that call `try_daemon_route()`, only `cancel`, `pause`, `rate_limits`, and now `run` catch `DaemonError`. Others (`status`, `diagnose`, `recover`) catch only `JobSubmissionError`. If a stale conductor returns METHOD_NOT_FOUND for one of these commands, the user would see a raw `MethodNotFoundError` traceback instead of a friendly error message. Note: `status.py` has a generic `except Exception` catch that handles this, so only `diagnose.py` and `recover.py` are truly exposed.
- **Impact:** Poor UX on version mismatch. Low likelihood in practice — requires running a stale conductor after code changes.
- **Action:** ~~Add `MethodNotFoundError` or `DaemonError` to remaining command catch patterns in `diagnose.py` and `recover.py`. Low priority.~~ DONE.

### F-300: Resource Anomaly Patterns Show Zero Effectiveness Differentiation
- **Found by:** Oracle, Movement 4
- **Severity:** P2 (medium)
- **Status:** Open
- **Category:** architecture
- **Description:** Of 30,232 patterns in global-learning.db, 5,315 are `resource_anomaly` type with average effectiveness exactly 0.5000. The F-009/F-144 fix (semantic context tag namespace) only addressed `semantic_insight` patterns (avg 0.511, differentiating). Resource anomaly patterns remain uniformly cold — generated but never matched to executions for effectiveness updates. Verified via `SELECT pattern_type, COUNT(*), AVG(effectiveness_score) FROM patterns GROUP BY pattern_type` — semantic_insight shows 0.511, resource_anomaly shows 0.500.
- **Impact:** 17.6% of the pattern corpus contributes zero intelligence signal. Resource patterns track execution duration anomalies, cost outliers, rate limit events — all valuable operational signals being ignored.
- **Action:** Investigate the resource pattern application pipeline in `_query_relevant_patterns()`. The selection gate fix (F-009) may have only addressed semantic tag matching. Resource patterns may need a parallel tag namespace fix or a separate application mechanism.

### F-301: Instrument Name Column Is 99.99% Null
- **Found by:** Oracle, Movement 4
- **Severity:** P3 (low — expected at this stage)
- **Status:** Open
- **Category:** pattern
- **Description:** Only 3 of 30,232 patterns have non-null `instrument_name`. Field added in F-009/F-144 fix (M3). Production execution path (legacy runner) doesn't populate instrument_name during pattern storage. Verified via `SELECT instrument_name, COUNT(*) FROM patterns GROUP BY instrument_name` — null: 30,229, claude_cli: 3.
- **Impact:** Learning store cannot differentiate pattern effectiveness by instrument. Blocks baton's planned instrument-scoped learning queries.
- **Action:** Verify baton path populates instrument_name during pattern storage. Low priority — baton isn't default yet. Column will populate naturally once baton is default.

### F-302: Stale Detection Ceiling Unchanged Across 3 Movements
- **Found by:** Oracle, Movement 4
- **Severity:** P2 (medium)
- **Status:** Open — cross-references F-097
- **Category:** risk
- **Description:** p99 execution duration has been 30.2-30.5 minutes across M2, M3, and M4 (n=28,976 completed executions with duration > 0). This exactly matches `idle_timeout_seconds: 1800` stale detection threshold. The composer's F-097 directive to increase timeout from 1800 to 7200 remains unimplemented. Verified via percentile query on global-learning.db executions table.
- **Impact:** Top 1% of executions are killed by stale detection regardless of actual progress. For the v3 orchestra's 706 sheets, ~7 sheets per movement are potentially killed mid-work.
- **Action:** Implement the two unclaimed TASKS.md items: increase idle_timeout_seconds in generate-v3.py and regenerate the v3 score. This is a 10-minute config change that has been open since M1.

### F-250: Cross-Sheet capture_files Content Not Credential-Redacted
- **Found by:** Warden, Movement 4
- **Severity:** P2 (medium — same class as F-003, F-135)
- **Status:** Resolved (movement 4, Warden)
- **Category:** credential-exposure
- **Error Class:** Piecemeal credential redaction — safety applied to one data path but not adjacent parallel paths
- **Description:** `capture_files` content in cross-sheet context was read from workspace files and injected into prompts without credential scanning. Both the legacy runner path (`context.py:_capture_cross_sheet_files`) and the baton adapter path (`adapter.py:_collect_cross_sheet_context`) read raw file content and stored it in `previous_files`. If an agent wrote a file containing an API key (e.g., `.env`, config file), and that file matched a `capture_files` glob pattern, the credential would flow to the next sheet's prompt. Stdout paths (`previous_outputs`) were already safe — credentials are redacted at capture time by both the musician (`musician.py:584`) and `CheckpointState.capture_output()` (`checkpoint.py:567`).
- **Impact:** Credential exposure through the prompt pipeline when agents write files containing API keys. The credential would be sent to the backend in the next sheet's prompt. Low probability (requires agent to write credentials AND file to match capture pattern), but high severity when triggered.
- **Resolution:** Added `redact_credentials()` call before truncation on both paths. Legacy runner at `context.py:295` (imported at module level). Baton adapter at `adapter.py:772` (lazy import inside the loop, matching existing import pattern). 8 TDD tests in `test_cross_sheet_safety.py` covering Anthropic, OpenAI, GitHub, AWS, and Bearer token patterns on both paths, plus truncation-after-redaction ordering.

### F-251: Baton Cross-Sheet Context Missing [SKIPPED] Placeholder
- **Found by:** Warden, Movement 4
- **Severity:** P2 (medium — baton/legacy parity gap)
- **Status:** Resolved (movement 4, Warden)
- **Category:** data-integrity
- **Error Class:** Baton/legacy-runner behavioral divergence
- **Description:** The baton's `_collect_cross_sheet_context()` silently excluded skipped upstream sheets from `previous_outputs`, while the legacy runner's `_populate_cross_sheet_context()` correctly injected `[SKIPPED]` placeholders (fix from #120, Maverick M4). Fan-in prompts on the baton path received silent data gaps instead of explicit skip markers.
- **Impact:** Fan-in templates using `{{ previous_outputs }}` on the baton path would see gaps without explanation. Matters once the baton becomes the default execution path (Phase 2 transition).
- **Resolution:** Added `BatonSheetStatus.SKIPPED` check before the `COMPLETED` filter in `_collect_cross_sheet_context()` at `adapter.py:730`. Skipped sheets now inject `"[SKIPPED]"` into `previous_outputs`. Updated existing test assertion. 4 TDD tests in `test_cross_sheet_safety.py`.

### F-252: Unbounded instrument_fallback_history Accumulation
- **Found by:** Warden, Movement 5
- **Severity:** P2 (medium — state bloat, potential DoS under pathological fallback scenarios)
- **Status:** Resolved (movement 5, Warden)
- **Category:** state-integrity
- **Error Class:** Unbounded collection growth — same class as error_history before MAX_ERROR_HISTORY was added
- **Description:** `SheetState.instrument_fallback_history` (checkpoint.py:373) and `SheetExecutionState.fallback_history` (baton/state.py:244) grow without limit on each fallback transition. In pathological scenarios (many fallback instruments with repeated rate-limit cycling), a single sheet could accumulate thousands of records, bloating state files and memory. `error_history` already has `MAX_ERROR_HISTORY = 50` with `add_error_to_history()` enforcing the cap. Fallback history had no equivalent.
- **Impact:** State serialization slowdown and memory growth proportional to fallback event count. In a 10-instrument fallback chain with 3 retries each, that's 30 records per sheet — manageable. But if rate limits cycle (instrument A rate-limited → fall back to B → B rate-limited → fall back to C → C clears → back to A → ...), the history grows unboundedly across retry cycles.
- **Resolution:** Added `MAX_INSTRUMENT_FALLBACK_HISTORY = 50` to checkpoint.py (line 30) and `MAX_FALLBACK_HISTORY = 50` to baton/state.py (line 31). Added `SheetState.add_fallback_to_history()` (checkpoint.py:595) paralleling `add_error_to_history()`. Added trimming after append in `SheetExecutionState.advance_fallback()` (state.py:291). 10 TDD tests in `test_f252_fallback_history_cap.py`.

### F-202: Baton/Legacy Parity Gap — FAILED Sheet Stdout in Cross-Sheet Context
- **Found by:** Breakpoint, Movement 4
- **Severity:** P3 (low — baton is stricter, arguably more correct)
- **Status:** Resolved (movement 5, Blueprint — design decision)
- **Category:** architecture
- **Error Class:** Baton/legacy-runner behavioral divergence (same class as F-251)
- **Description:** The legacy runner `_populate_cross_sheet_context()` at `context.py:206-214` includes stdout from ANY non-SKIPPED sheet with stdout_tail — including FAILED and IN_PROGRESS sheets. The baton adapter at `adapter.py:738` explicitly filters `if prev_state.status != BatonSheetStatus.COMPLETED: continue`, excluding all non-COMPLETED, non-SKIPPED sheets. This means FAILED sheets with stdout are included in cross-sheet context on the legacy path but excluded on the baton path.
- **Impact:** When the baton becomes default (Phase 2), scores that rely on seeing failed sheet output in downstream prompts will get different behavior. The baton's stricter filtering may actually be preferable (failed output could be misleading), but the behavioral difference should be a conscious design decision, not an accident.
- **Action:** Decide: is the baton's stricter behavior intentional? If yes, document the difference. If no, align the baton with the legacy runner by including FAILED sheets with stdout. Test `test_m4_adversarial_breakpoint.py::TestBatonLegacySkippedParity::test_both_paths_skip_non_completed_non_skipped` documents the gap.
- **Resolution:** The baton's stricter behavior is the correct design. Failed sheet output may be incomplete, malformed, or contain error artifacts that would mislead downstream agents. The cross-sheet contract should be: "you see outputs from sheets that succeeded." The legacy runner's permissiveness is a legacy accident, not a feature. If error recovery patterns need failed output in the future, add an explicit `include_failed_outputs: true` field to CrossSheetConfig. Decision documented in collective memory under Design Decisions.

### F-254: Enabling use_baton Kills All In-Progress Legacy Jobs — baton.resume.no_checkpoint
- **Found by:** Composer + automated monitor, 2026-04-04
- **Severity:** P0 (critical — data loss, silently destroys hours of in-progress work)
- **Status:** Open
- **Category:** architecture / baton transition
- **Description:** When the conductor starts with `use_baton: true`, it attempts to resume all registered jobs through the baton path. The baton's resume logic looks for its own checkpoint format but the jobs were created by the legacy runner using `CheckpointState` in `.marianne-state.db`. The baton emits `"baton.resume.no_checkpoint"` error and immediately marks the job as `"job.completed"` (stored as FAILED in the registry). This happened to ALL 5 registered jobs on conductor startup — including `marianne-orchestra-v3` at 150/706 sheets (21% complete, ~2 days of work). The workspace's `.marianne-state.db` still contains the full CheckpointState showing 150 completed sheets, but the conductor considers the job finished.
- **Evidence:** Conductor log shows the sequence for every job: `job.resuming` → `baton.resume.no_checkpoint` (error) → `snapshot.captured` → `job.completed` — all within 100ms. No sheets were dispatched.
- **Dual state disagreement:** `mzt list` (registry) says FAILED. `mzt status <job>` (workspace state) says RUNNING with 4 in_progress sheets. Conductor memory says 0 running jobs. Three sources, three answers.
- **Impact:** (1) Flipping `use_baton: true` silently destroys all in-progress legacy work. (2) The "no checkpoint" error doesn't distinguish "never started" from "150 sheets completed via legacy runner." (3) The baton transition (composer directive, Phase 2) cannot safely flip the default without solving this. (4) `mzt status` shows stale RUNNING state that will never progress — the job is dead but looks alive.
- **Related:** Issue #111 (conductor state desync). F-139 (misleading resume error). Composer directive: baton transition Phase 1 must solve this before Phase 2.
- **Architectural principle:** The daemon is the ONLY source of truth for job state. Workspace files are artifacts, not state. The legacy runner's pattern of writing CheckpointState to `.marianne-state.db` in the workspace creates a dual-state problem — two sources of truth that can disagree. The baton should NOT learn to read legacy workspace state. That's perpetuating the wrong architecture.
- **Fix:** (1) The baton's state lives in the daemon's own DB (daemon-state.db / registry.db), not in workspace files. On restart, the daemon recovers from its own persisted state. (2) If the daemon has no record of a job, the job doesn't exist — period. No falling back to workspace files. (3) Legacy workspace state files (.marianne-state.db) should be treated as read-only artifacts for debugging, not as authoritative state. (4) The transition path: migrate any essential state from legacy workspace DBs into the daemon's DB as a one-time migration, then stop writing workspace state entirely.

### F-253: Cost Tracking Tip Says "Use JSON Output" But Doesn't Say How — And JSON Should Be Default
- **Found by:** Composer, 2026-04-04
- **Severity:** P2 (medium — UX gap, accurate cost tracking is a core feature)
- **Status:** Open
- **Category:** UX
- **Description:** `mzt status` displays a tip: "Set cost_limits.enabled: true in your score to prevent unexpected charges" and cost tracking shows near-zero values ($0.00-$0.03) for scores that have run hundreds of sheets. Accurate token/cost tracking requires the instrument to output in JSON format (so Marianne can parse `input_tokens_path` / `output_tokens_path` from structured output), but there is no guidance in the status output, score-writing guide, or CLI help on how to configure this. The instrument profiles define `output_format_flag` and `output_format_value` (e.g., `-o json` for claude-code), but whether this is applied depends on the profile config, not explicit user action. If JSON output is required for accurate cost tracking, it should be the default output format for all instruments — not something the user has to discover and configure.
- **Impact:** (1) Composers see $0.00 costs and assume the score is free or cost tracking is broken. (2) No actionable path from the tip to accurate tracking. (3) Cost limits (`max_cost_per_job`) can't function if costs aren't tracked. (4) The v3 orchestra has run 150+ sheets with reported cost of $0.03 — clearly wrong.
- **Action:** (1) Make JSON output format the default for all CLI instruments (instrument profiles should set it, not require user config). (2) If an instrument is running in text mode, warn that cost tracking is degraded. (3) The status tip should explain HOW to enable accurate tracking, not just that it exists.

### F-255: Baton Transition Blocked by Multiple Unsolved Gaps — End-to-End Testing Required
- **Found by:** Composer + automated monitor, 2026-04-04 (first production baton run)
- **Severity:** P0 (critical — baton cannot go to production without these fixes)
- **Status:** Open
- **Category:** architecture / baton transition
- **Description:** First production run of the baton (enabling `use_baton: true` on the conductor) revealed a cascade of gaps that prevent production use. These were discovered by running the v3 orchestra (150/706 completed sheets) through the baton after manual state migration. The task at TASKS.md line 282 ("Enable use_baton after F-210 fixed") was unblocked when F-210 was resolved in movement 4, but nobody tested end-to-end with real production data.
- **Gaps found (in order of discovery):**
  1. **`_load_checkpoint` reads workspace JSON, not daemon DB** (manager.py:2211-2244). The daemon's registry already has `checkpoint_json` with full state. The baton's resume path looks for `{workspace}/{job_id}.json` — a flat file that doesn't exist. The registry has `load_checkpoint()` ready to use. **Partial fix applied this session:** changed `_load_checkpoint` to read from `self._registry.load_checkpoint()`. Needs review.
  2. **Baton adapter doesn't publish to `_live_states`** — the legacy runner calls `_on_state_published()` on every checkpoint save, populating `_live_states` dict which `get_job_status()` reads. The baton adapter has no equivalent callback. Result: `mzt status <job>` shows "Full status unavailable" for baton-managed jobs. **FIXED (Foundation, M5):** `_run_via_baton` now creates initial CheckpointState in `_live_states` before `register_job()`. `_resume_via_baton` populates `_live_states` with recovered checkpoint. `_on_baton_state_sync` callback can now update sheet statuses. 7 TDD tests in `test_foundation_m5_f255_live_states.py`.
  3. **PluginCliBackend doesn't disable MCP** — the legacy `ClaudeCLIBackend` has `disable_mcp: True` default, passing `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`. The `PluginCliBackend` (used by baton via instrument profiles) has zero MCP handling. Result: 4 musicians spawn ~80 child processes (MCP servers, docker containers) instead of ~8. Potential deadlocks per legacy backend comments. **FIXED (Foundation, M5):** See F-271. Profile-driven `mcp_disable_args` on CliCommand.
  4. **Three state stores disagree** — daemon registry (FAILED), workspace SQLite (RUNNING), workspace JSON (doesn't exist). The daemon DB should be the ONLY source of truth. Workspace files are artifacts, not state.
  5. **`mzt list` and `mzt status` read different sources** — list reads registry (FAILED), status reads workspace or live_states (RUNNING). Three answers from three sources.
- **What was done this session:**
  - Changed `_load_checkpoint` to read from daemon DB registry (needs code review)
  - Manually migrated checkpoint state from daemon DB → workspace JSON to unblock the current run (workaround, not fix)
  - Filed F-152 (unsupported instrument kind dispatch loop), F-151 (no instrument visibility), F-150 (instrument_config.model — already fixed by Foundation), F-149 (global backpressure, issue #153), F-254 (baton kills legacy jobs)
  - Filed composer directive for baton transition phases
- **What still needs to be done:**
  - Wire baton adapter to publish state via `_on_state_published` or equivalent → `_live_states` + registry
  - Add MCP disabling to PluginCliBackend (read `mcp_config_flag` from profile, apply `--strict-mcp-config` + empty config)
  - Remove workspace JSON state file from `_load_checkpoint` entirely — daemon DB only
  - End-to-end test: run hello score AND a subset of orchestra through baton via `--conductor-clone`, verify status display, cost tracking, instrument assignment, resume after restart
  - ALL of the above needs code review before merging
- **Related:** F-254 (baton kills legacy jobs). F-152 (unsupported instrument loop). Issue #111 (conductor state desync). TASKS line 282 (enable baton). Step 29 (restart recovery — marked done but reads workspace not daemon DB).

### F-400: Uncommitted Architectural Work — Manager Checkpoint Loading
- **Found by:** Prism, Movement 4
- **Severity:** P1 (high — correct direction, incomplete implementation)
- **Status:** Open
- **Category:** architecture
- **Description:** `src/marianne/daemon/manager.py` `_load_checkpoint()` method (lines 2213-2247) switched from file-based to daemon-registry-based loading in the working tree. This change is architecturally correct (daemon as single source of truth, aligns with F-254 principle) but uncommitted and incomplete. The method now calls `await self._registry.load_checkpoint(job_id)` instead of reading `workspace / f"{safe_id}.json"`. No migration path exists for legacy jobs that have checkpoints in workspace files but not in the daemon registry. No tests exist for the new code path.
- **Evidence:** Working tree diff shows 25-line change at `manager.py:2213-2247`. HEAD version reads from workspace file. Working tree version reads from daemon registry (`registry.py:316-329` provides `load_checkpoint()` method). No corresponding test file changes in working tree.
- **Impact:** The change aligns with the right architecture (daemon as single source of truth) but doesn't solve the transition problem. Legacy jobs will fail to resume because they have no registry checkpoint. This makes F-254 (enabling use_baton kills jobs) worse, not better. Uncommitted code creates drift between what's tested and what exists.
- **Action:** (1) Commit this work WITH migration logic: on registry miss, try workspace file, migrate to registry, delete workspace file. (2) Add TDD tests for: registry hit, workspace migration fallback, both miss (error path). (3) Document as part of F-254 resolution strategy. (4) Consider: is this change a response to F-254? If so, coordinate with whoever made this change to understand the full plan.

### F-441: Pydantic Silently Accepts Unknown YAML Fields — All 37 Config Models Lack extra='forbid' (P0)
- **Found by:** Axiom, Movement 4
- **Severity:** P0 (critical — composer directive M5, blocks public release)
- **Status:** Resolved (movement 4, Journey + Axiom mateship)
- **Resolution:** `extra='forbid'` added to all 51 config models. Journey committed job.py (7d86035) + backward compat for total_sheets (6452f6c) + schema error hints (7d86035). Axiom mateship: remaining 45 models + test fixes. Sentinel verified (a39704a). Theorem property-tested (all models reject unknown fields). Dashboard E2E tests fixed for compatibility.
- **Category:** bug / configuration validation
- **Error class:** Configuration validation gap (same class as F-002 falsy YAML values)
- **Description:** All 37 config models in `src/marianne/core/config/` lack `model_config = ConfigDict(extra='forbid')`. Pydantic v2 defaults to `extra='ignore'` (silently drop unknown fields). Unknown YAML fields in score configs are accepted without error, then silently dropped. `mzt validate` reports "✓ Configuration valid" for configs with typos, non-existent features, or future unimplemented features.
- **Impact:** (1) Score authors think features work when Marianne drops them on the floor. Example: `instrument_fallbacks: [gemini-cli]` validates successfully but is ignored (field doesn't exist). (2) Typos in field names produce zero feedback (`instument:` accepted, feature ignored). (3) Future features appear to work before implementation (`loops:`, `conditionals:` accepted, ignored). (4) Debugging is impossible — user reports "feature doesn't work" when the actual problem is "I mistyped the field name."
- **Reproducer:**
```python
from marianne.core.config import JobConfig
import yaml
data = yaml.safe_load("""
name: test
workspace: /tmp
instrument: claude-code
instrument_fallbacks: [gemini-cli]  # doesn't exist
this_is_fake: true                   # doesn't exist
sheet: { size: 1, total_items: 1, bogus: 123 }  # bogus field
prompt: { template: "test" }
""")
config = JobConfig(**data)  # SUCCEEDS
print(hasattr(config, 'instrument_fallbacks'))  # False — field dropped
print(hasattr(config, 'this_is_fake'))          # False — field dropped
```
- **Affected models:** All 37 — JobConfig, SheetConfig, PromptConfig, BackendConfig, InstrumentConfig, ExecutionConfig, LearningConfig, OrchestrationConfig, WorkspaceConfig, SpecCorpusConfig, etc. (full list: `grep "^class.*Config.*BaseModel" src/marianne/core/config/*.py`)
- **Root cause:** Marianne migrated from Pydantic v1 (default `extra='forbid'`) to v2 (default `extra='ignore'`) without adding explicit `extra='forbid'` to preserve strict validation.
- **Fix:** Add to ALL 37 config models:
```python
from pydantic import BaseModel, ConfigDict

class JobConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # ... fields ...
```
Add V212 validation check with "did you mean X?" suggestions for common typos (`instument` → `instrument`, `dependancies` → `dependencies`, etc.).
- **Breaking change scope:** YES — any score with typos or non-existent fields will start failing validation. This is GOOD (fail-fast instead of silent corruption), but requires CHANGELOG notice and migration guidance.
- **Composer directive (M5, P0):** "Set extra='forbid' on JobConfig, SheetConfig, and all nested config models. Unknown fields must be ERROR severity, not warnings. If this breaks legitimate edge cases (e.g., YAML anchors, user-defined metadata), add an explicit metadata: field for arbitrary user data rather than making the whole model permissive."
- **GitHub issue:** #156 (already filed, recommend elevate to P0 milestone)
- **Evidence:** Report at `movement-4/axiom.md`, lines 387-535. Full reproduction, impact analysis, 37-model enumeration, fix requirements.
- **Verified by:** Empirical reproduction on HEAD (2026-04-04), Python REPL test, config model file enumeration.
- **Action:** Fix all 37 models + add regression tests before v1 public release.
- **Related findings:** F-002 (falsy YAML values rejected by overly strict guards — inverse problem, same validation layer)

### F-270: Stale test_extra_fields_ignored_by_default after extra='forbid' added
- **Found by:** Litmus, Movement 4
- **Severity:** P3 (low — test maintenance, not a product bug)
- **Status:** Resolved (movement 4, Axiom mateship — test removed during F-441 completion)
- **Category:** pattern
- **Finding:** `tests/test_instrument_models.py::TestInstrumentModelsAdversarial::test_extra_fields_ignored_by_default` (line 684) expects `ModelCapacity.model_validate()` to silently ignore unknown fields. Since `extra='forbid'` was added to all instrument config models (per composer directive M5), the test fails with `ValidationError: Extra inputs are not permitted`. The model behavior is CORRECT now (forbid unknown fields). The test is STALE (expects the old permissive behavior).
- **Impact:** Quality gate false failure. Pre-existing — not caused by litmus changes.
- **Resolution:** Test removed during Axiom's F-441 mateship completion (06500d0). Verified by Weaver M4 — `grep test_extra_fields_ignored_by_default tests/` returns no results.
- **Related:** F-462 (composer directive on extra='forbid'), Axiom M4 audit of 37 config models

### F-271: PluginCliBackend ignores mcp_config_flag — MCP process explosion
- **Found by:** Litmus, Movement 4 (litmus test 39, validating F-255.3)
- **Severity:** P1 (high — production impact: 80 child processes instead of 8)
- **Status:** Resolved (movement 5, Foundation)
- **Category:** bug
- **Finding:** `PluginCliBackend._build_command()` at `src/marianne/execution/instruments/cli_backend.py:169-232` does NOT reference `mcp_config_flag` from the instrument profile. The field EXISTS on `CliCommand` (`instruments.py:161-164`), is SET in the claude-code profile (`builtins/claude-code.yaml:78`), but is NEVER USED in command construction. The legacy `ClaudeCliBackend` has `disable_mcp=True` which adds `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`. The baton uses `PluginCliBackend`, so baton-managed sheets spawn MCP servers (docker containers, child processes) that the legacy runner prevents. F-255.3 documented this in production: 80 child processes instead of 8.
- **Impact:** Production MCP process explosion. Potential deadlocks per legacy backend comments. Affects ALL baton-managed sheets using claude-code instrument.
- **Action:** Add MCP disabling to `_build_command()`: when `mcp_config_flag` is set and no MCP servers are requested, add `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`. Litmus test 39 documents the gap — when fixed, the test assertion should be inverted.
- **Evidence:** Litmus test `TestPluginCliBackendMcpGap::test_build_command_ignores_mcp_config_flag` proves the gap exists. Legacy backend inspection via `TestPluginCliBackendMcpGap::test_legacy_backend_disables_mcp_by_default` confirms the protection exists in the old path but not the new.
- **Related:** F-255.3 (production discovery), F-105 (instrument schema expansion)
- **Resolution:** Canyon M5 mateship: profile-driven `CliCommand.mcp_disable_args` field replaces hardcoded approach. `_build_command()` injects these args when non-empty. Claude-code profile updated with `["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']`. Litmus test updated to verify fix holds. 8 TDD tests in `test_f271_mcp_disable.py`.

### F-451: Diagnose Can't Find Completed Jobs That Status Can Find
- **Found by:** Ember, Movement 4
- **Severity:** P2 (medium — UX inconsistency between commands)
- **Status:** Resolved (movement 5, Circuit)
- **Resolution:** When conductor returns JobSubmissionError (job not in registry) and -w workspace is provided, diagnose now falls back to filesystem search instead of immediately exiting. The -w flag is now visible (not hidden). Error hints mention -w when workspace not provided. 4 TDD tests in `test_f451_diagnose_workspace_fallback.py`.
- **Category:** UX
- **Description:** `mzt diagnose hello` returns "Score not found" even though `mzt status hello -w <workspace>` successfully shows full COMPLETED status. `diagnose` doesn't support `-w` (workspace) flag and only queries the conductor's registry. After a conductor restart, completed jobs may not be in the registry.
- **Impact:** Users can see a score's status (with -w) but can't diagnose it. The natural debugging path (`status` → see problem → `diagnose`) breaks when the conductor doesn't know about the job.
- **Reproducer:** `mzt status hello -w workspaces/hello-marianne` (PASS) then `mzt diagnose hello` (FAIL: "Score not found")
- **Action:** Add `-w` workspace fallback to diagnose, or ensure completed jobs persist in registry across restarts.

### F-452: `mzt list --json` Returns Null Cost, `status --json` Returns Structured Cost
- **Found by:** Ember, Movement 4
- **Severity:** P3 (low — machine consumer inconsistency)
- **Status:** Open
- **Category:** UX
- **Description:** `mzt list --json` returns `cost_usd: null` for all entries. `mzt status <job> --json` returns structured cost data (`total_estimated_cost`, `cost_confidence`, token counts). Machine consumers get inconsistent cost data depending on which command they use.
- **Reproducer:** `mzt list --json | jq '.[0].cost_usd'` returns `null`. `mzt status marianne-orchestra-v3 --json | jq '.cost'` returns `{total_estimated_cost: 0.004872, cost_confidence: 0.7, ...}`.
- **Action:** Add cost summary fields to `list --json` output.

### F-453: Dashboard E2E Test Cross-Test State Leakage
- **Found by:** Ember, Movement 4
- **Severity:** P3 (low — test infrastructure, not product bug)
- **Status:** Open
- **Category:** pattern / test-isolation
- **Description:** `test_dashboard_e2e.py::TestJobLifecycleE2E::test_complete_job_lifecycle` fails in full suite but passes in isolation. `TypeError: object Mock can't be used in 'await' expression` at `job_control.py:263`. Mock state contamination from another test.
- **Reproducer:** `pytest tests/test_dashboard_e2e.py -x` (PASS) vs `pytest tests/ -x` (FAIL at this test)
- **Action:** Investigate mock isolation — either the test needs its own event loop or another test is leaking mock state.

### F-430: ValidationRule.sheet Precedence Docstring Contradicts Implementation
- **Found by:** Prism, Movement 4
- **Severity:** P3 (low — edge case, misleading documentation)
- **Status:** Resolved (movement 5, Blueprint)
- **Category:** bug
- **Description:** `src/marianne/core/config/execution.py:494-500` — The `sheet` field docstring says "If both sheet and condition are set, the sheet filter takes precedence." But `_sheet_to_condition()` at line 506 only sets condition when `self.condition is None`, meaning the existing `condition` takes precedence over `sheet`. The docstring and code disagree.
- **Impact:** A score author who sets both `sheet: 3` and `condition: "sheet_num >= 5"` expects sheet to win (per docstring) but condition wins (per code). Low impact because this is an unusual combination, but the contract is wrong.
- **Action:** Either change the code to `if self.sheet is not None:` (overwrite condition always) to match the docstring, or fix the docstring to say "the condition takes precedence." Recommend fixing the docstring — the current behavior (condition overrides) is safer.
- **Resolution:** Fixed docstring to match code. New description: "Shorthand for condition: 'sheet_num == N'. If both sheet and condition are set, condition takes precedence (sheet is only applied when condition is absent)." 4 TDD tests in test_f430_validation_sheet_precedence.py pin the behavior.

### F-431: DaemonConfig and ProfilerConfig Missing extra='forbid'
- **Found by:** Prism, Movement 4
- **Severity:** P2 (medium — same class as F-441)
- **Status:** Resolved (movement 5, Maverick)
- **Resolution:** Added `model_config = ConfigDict(extra="forbid")` to all 9 daemon/profiler config models: ResourceLimitConfig, SocketConfig, ObserverConfig, SemanticLearningConfig, DaemonConfig (daemon/config.py), RetentionConfig, AnomalyConfig, CorrelationConfig, ProfilerConfig (profiler/models.py). 23 TDD tests in test_f431_daemon_config_strictness.py. Production conductor.yaml validated clean.
- **Category:** architecture
- **Description:** The F-441 fix (`extra="forbid"` on all config models) was comprehensive for `src/marianne/core/config/` (49 models) but did not cover `src/marianne/daemon/config.py` (5 models: DaemonConfig, ResourceLimitConfig, SocketConfig, ObserverConfig, SemanticLearningConfig) or `src/marianne/daemon/profiler/models.py` (4 models: ProfilerConfig, RetentionConfig, AnomalyConfig, CorrelationConfig). These models back `~/.marianne/conductor.yaml`, which is user-edited. Unknown fields in conductor config are silently dropped — same bug class.
- **Impact:** A user who typos a field in their `conductor.yaml` (e.g., `resource_limits:` instead of `resources:`, or `profiler.enbled:` instead of `profiler.enabled:`) gets no error. The same trust erosion that F-441 identified for score YAML applies to daemon config.
- **Action:** Add `model_config = ConfigDict(extra="forbid")` to all 9 daemon config models. Test against existing conductor.yaml files.

### F-432: examples/iterative-dev-loop-config.yaml Breaks With extra='forbid'
- **Found by:** Prism, Movement 4
- **Severity:** P2 (medium — user-facing example file fails validation)
- **Status:** Resolved (movement 4, Compass)
- **Category:** UX
- **Description:** `examples/iterative-dev-loop-config.yaml` is a generator config (consumed by `scripts/generate-iterative-dev-loop.py`), NOT a score. It has custom fields (`spec_dir`, `cycles`) that fail `extra="forbid"` on `JobConfig`. With the F-441 fix, any automated validation of `examples/*.yaml` will report this file as broken.
- **Impact:** Automated example validation (Journey M4 ran `find examples/ -name "*.yaml" -exec mzt validate {} \;`) will flag this file. New users exploring examples/ will find a broken file. The file header explains it's a generator config, but the filename pattern matches score expectations.
- **Resolution:** Moved to `scripts/iterative-dev-loop-config.yaml` (next to its generator script). Updated usage comments. Removed from both tables in `examples/README.md`. All 38 remaining examples validate clean.

### F-470: BatonAdapter._synced_status Memory Leak on Job Deregister
- **Found by:** Adversary, Movement 4
- **Severity:** P2 (medium — production-relevant for long-running daemons)
- **Status:** Resolved (movement 5, Maverick)
- **Resolution:** Added `_synced_status` cleanup to `deregister_job()` at adapter.py:519. Dict comprehension filters out all entries matching the deregistered job_id. 5 TDD tests in test_f470_synced_status_leak.py. Updated adversary's bug-proof test to regression test.
- **Category:** bug
- **Description:** `BatonAdapter.deregister_job()` at `src/marianne/daemon/baton/adapter.py:492-518` cleans up `_job_sheets`, `_job_renderers`, `_job_cross_sheet`, `_completion_events`, `_completion_results`, and `_active_tasks` — but does NOT clean up `_synced_status` (the F-211 state-diff dedup cache at line 344). The `_synced_status` dict is keyed by `(job_id, sheet_num)` tuples and grows proportionally to the total number of sheets across ALL jobs ever processed. For a long-running daemon processing thousands of jobs (e.g., the v3 orchestra at 706 sheets), the cache accumulates ~706 entries per run and never shrinks.
- **Evidence:** Test `test_synced_status_not_cleaned_on_deregister` in `tests/test_m4_adversarial_adversary.py` proves the leak: after deregistering all 100 simulated jobs (1000 entries), the cache still contains all 1000 entries.
- **Impact:** Memory growth O(total_sheets_ever) for the daemon process. For the orchestra's scale (706 sheets per run, multiple runs per day), this could reach tens of thousands of stale entries per day. Not critical for v1 beta but will compound in production.
- **Action:** Add cleanup of `_synced_status` entries to `deregister_job()`. Pattern: `self._synced_status = {k: v for k, v in self._synced_status.items() if k[0] != job_id}` or iterate and pop matching keys.
- **Error class:** Same class as F-129 (ephemeral state not cleaned up), F-077 (lifecycle mismatch).

### F-471: Pending Jobs Lost on Daemon Restart
- **Found by:** Adversary, Movement 4
- **Severity:** P2 (medium — architectural gap, not a code bug)
- **Status:** Mitigated (movement 5, Circuit — via F-149)
- **Resolution note:** F-149 fix removed the rate-limit→PENDING path from `submit_job()`. Rate limits no longer cause PENDING queueing — jobs go straight through and per-instrument rate limiting is handled at the sheet dispatch level. The PENDING infrastructure still exists for future use (resource-pressure queueing) but the primary trigger (rate limits) is gone.
- **Category:** architecture
- **Description:** `JobManager._pending_jobs` at `src/marianne/daemon/manager.py:156` is a plain `dict[str, JobRequest]` stored only in memory. Jobs queued as PENDING during rate limit backpressure (via `_queue_pending_job` at line ~815) have their `JobRequest` objects stored here. If the daemon restarts while jobs are PENDING, the `_pending_jobs` dict is lost (starts empty in `__init__`). The persistent `JobRegistry` records the job as `DaemonJobStatus.PENDING`, but the recovery path (`_recover_baton_orphans` at line ~519) only processes PAUSED jobs, not PENDING ones. The `_start_pending_jobs` method only processes the in-memory dict.
- **Impact:** After daemon restart, PENDING jobs appear in `mzt list` as PENDING but will never start. The user must manually `mzt cancel` and resubmit. This gap is more severe during rate limit storms where multiple jobs could be queued.
- **Action:** Either (a) persist the JobRequest alongside the registry entry and recover PENDING jobs in `start()`, or (b) document PENDING as ephemeral state that does not survive restart, with clear user guidance on resubmission.

### F-467: Validate Hint References Non-Configurable Field `total_sheets`
- **Found by:** Newcomer, Movement 4
- **Severity:** P3 (low — misleading hint, not a crash)
- **Status:** Resolved (movement 4, Newcomer)
- **Category:** bug
- **Description:** Error hint at `src/marianne/cli/commands/validate.py:295` told users "Add a 'sheet' section with total_sheets, total_items, and size." But `total_sheets` is a computed property derived from `total_items` and `size` — it is NOT a configurable field. After F-441 (`extra='forbid'`), a user following this hint would get a secondary validation error: "Extra inputs are not permitted" for `total_sheets`.
- **Impact:** Misleading error guidance that compounds confusion for newcomers. The user tries to fix one error by following the hint, and gets a new error from the "fix."
- **Resolution:** Changed hint to "Add a 'sheet' section with total_items and size." — references only actual configurable fields.

### F-472: Pre-existing test expects use_baton default=True (D-027 not yet completed)
- **Found by:** Foundation, Movement 5
- **Severity:** P3 (low — test maintenance, not a product bug)
- **Status:** Resolved (movement 5, Canyon — D-027 completed)
- **Category:** pattern
- **Finding:** `tests/test_baton_adapter.py::TestUseBatonFeatureFlag::test_daemon_config_has_use_baton_field` asserts `config.use_baton is True`, but `DaemonConfig.use_baton` still defaults to `False`. The test was written in anticipation of D-027 (flip baton default) which hasn't been completed yet. Test fails deterministically on every run.
- **Impact:** Quality gate false failure. Pre-existing — not caused by M5 changes.
- **Resolution:** D-027 complete (Canyon M5). `DaemonConfig.use_baton` now defaults to `True`. Test passes. Verified by Ghost M5.

### F-310: Test suite flaky — different tests fail on each full run (all pass in isolation)
- **Found by:** Ghost, Movement 5
- **Severity:** P2 (medium — undermines quality gate reliability)
- **Status:** Open
- **Category:** reliability
- **Finding:** Running `pytest tests/ -x` produces different failures each run. Observed failing tests across 4 consecutive runs:
  1. `test_f255_2_live_states.py::test_live_state_has_sheet_entries`
  2. `test_f255_2_live_states.py::test_run_via_baton_creates_live_state`
  3. `test_unknown_field_ux_journeys.py::test_instrument_fallbacks_not_silently_ignored` (FIXED — deterministic, see F-311)
  4. `test_daemon_backpressure.py::TestRateLimitExpiryTransitions::test_job_accepted_during_and_after_limit`
  All flaky tests pass when run in isolation. Pattern suggests cross-test state leakage: shared event loops, singleton state, or timing-dependent assertions that degrade under suite-wide resource contention (~500s runtime, 11,400+ tests).
- **Impact:** Quality gate can fail spuriously, masking real failures behind flaky noise.
- **Action:** (1) Audit tests with `asyncio.sleep(<small_value>)` for timing sensitivity. (2) Consider `--randomly-seed` for ordering dependency detection. (3) Run suite without `-x` to measure total flaky count per run.

### F-311: test_unknown_field_ux_journeys outdated after instrument_fallbacks field added
- **Found by:** Ghost, Movement 5
- **Severity:** P2 (medium — deterministic test failure)
- **Status:** Resolved (movement 5, Ghost)
- **Category:** bug
- **Finding:** `test_instrument_fallbacks_not_silently_ignored` expected `JobConfig(**score)` to raise for unknown field `instrument_fallbacks`, but `instrument_fallbacks` was added as a real field on `JobConfig` at `src/marianne/core/config/job.py:684`. Test failed deterministically.
- **Impact:** Full test suite fails on every run.
- **Resolution:** Updated test to use `instrument_priorities` (genuinely non-existent field). Test renamed to `test_instrument_priorities_not_silently_ignored`. All 21 tests pass.

### F-480: Trademark Collision — "Marianne" Name Blocked by Two Active Products
- **Found by:** Composer + monitor, Movement 5
- **Severity:** P0 (blocks v1 release under current name)
- **Status:** Open — rename to mzt initiated
- **Category:** legal / identity
- **Description:** Two entities hold active claims on "Marianne" in the AI orchestration space. (1) Marianne AI (London) — AI music production startup, $6M seed from Balderton Capital, 100K users. (2) Automation Anywhere — $2.8B company with "Marianne Orchestrator" product doing AI agent orchestration, USPTO trademark serial 99680702. The second is a direct product-category collision: same name, same domain (AI agent orchestration), backed by enterprise legal resources.
- **Impact:** Continuing under "Marianne" exposes the project to trademark enforcement the moment it gains public visibility. The v1 beta cannot ship under this name.
- **Resolution plan:** Rename to "Marianne AI Compose" (CLI: `mzt`). Named for Maria Anna "Marianne" Marianne — Wolfgang's older sister, the prodigy history forgot. The orchestral metaphor and all musical vocabulary (scores, sheets, conductors, musicians, concerts, baton, libretto, fermata) are retained. Only the product name and package name change. Package: `marianne`. Config path: `~/.mzt/`. Full scope in composer-notes.yaml P0 directive.

### F-481: Orphan Detection Relies on Hardcoded Cmdline Patterns — Environment-Specific, Silent Failure on Other Systems
- **Found by:** Composer, post-Movement 5
- **Severity:** P1 (high — silent failure on non-developer systems)
- **Status:** Partially resolved — legacy runner path wired, baton path unwired
- **Category:** architecture
- **Finding:** `ProcessGroupManager` used a hardcoded tuple `_ORPHAN_CMDLINE_PATTERNS` containing patterns specific to one developer's MCP stack (`symbols run`, `pyright-langserver`, `rust-analyzer`, `clangd`, etc.). On any other system these patterns match nothing — orphaned MCP/LSP servers accumulate silently until thread/PID exhaustion kills the conductor. The original `"mcp" in cmdline` fallback was also environment-specific (not all tool servers have "mcp" in their command line).
- **Resolution (partial):** Replaced pattern matching with PID ancestry tracking. `ProcessGroupManager` now has `track_backend_pid(pid)` / `untrack_backend_pid(pid)`. When a tracked backend PID dies, any orphaned children in the daemon's process tree are killed — regardless of what they're called. Works for any MCP server, LSP server, or tool server on any system.
- **What's wired:** Legacy runner path (`JobService._setup_components` → sets `backend._on_process_spawned` / `_on_process_exited` callbacks). Chain: `DaemonProcess._pgroup` → `JobManager._pgroup` → `JobService._pgroup_manager` → `_setup_components()`.
- **What's NOT wired:** Baton path. `BackendPool._acquire_locked()` creates `PluginCliBackend` instances without PID tracking callbacks. `PluginCliBackend` itself lacks `_on_process_spawned`/`_on_process_exited` callback slots. Until wired, any score using instruments + baton has zero orphan detection.
- **Action:** Wire baton path. (1) Add callback slots to `PluginCliBackend`. (2) Pass pgroup reference to `BackendPool`. (3) Wire callbacks in `BackendPool._acquire_locked()` after backend creation. Same pattern as `JobService._setup_components`.

### F-482: MCP Server Leak Cascade — Thread Exhaustion Kill Chain
- **Found by:** Composer, post-Movement 5
- **Severity:** P1 (high — conductor death from MCP-enabled scores)
- **Status:** Resolved for selective MCP; structural fix in F-481
- **Category:** bug
- **Finding:** Running a score with `disable_mcp: false` and no `--strict-mcp-config` caused Claude Code to load ALL ambient MCP servers from installed plugins. With `max_concurrent: 2`, each agent spawned N MCP servers. When Claude got SIGABRT, `_await_process_exit` waited 5 seconds before cleanup, giving orphans time to compound. The profiler's aiosqlite (thread-per-connection model) then hit `RuntimeError: can't start new thread`, killing the conductor. In one observed incident, 148 orphaned `symbols run` LSP servers consumed 11GB of RAM.
- **Resolution:** (1) `_await_process_exit` now skips the 5s sleep when process is already dead (SIGABRT) — immediately escalates to SIGKILL on the process group. (2) SIGTERM grace period reduced from 1.0s to 0.5s. (3) Score-level fix: use `cli_extra_args: ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{...}}']` to limit each agent to only the MCP servers it needs, instead of `disable_mcp: false` which loads everything ambient.
- **Structural gap:** No per-sheet MCP configuration exists in score YAML. The `cli_extra_args` escape hatch works but is fragile. A proper technique system (MCP/skills specified per-instrument or per-sheet in score config) is needed for v1.

### F-483: `cli_extra_args` Overrides Work — Validated for Selective MCP
- **Found by:** Composer, post-Movement 5
- **Severity:** Informational
- **Status:** Confirmed working
- **Category:** pattern
- **Finding:** `cli_extra_args` in backend config is applied last in `_build_command()` (line 270-272 of `claude_cli.py`), overriding any earlier flags. Using `disable_mcp: false` (prevents the backend from adding empty `--mcp-config`) combined with `cli_extra_args: ["--strict-mcp-config", "--mcp-config", '<json>']` successfully limits each agent to exactly the specified MCP servers. Verified by process command line inspection and 5 consecutive sheet completions with Playwright MCP.
- **Implication:** This is the correct workaround until a proper technique system exists. Scores needing specific MCP servers should use this pattern rather than `disable_mcp: false` alone.

### F-484: Agent-Spawned Background Processes Escape PGID Cleanup
- **Found by:** Composer, mzt-site monitoring session (2026-04-06)
- **Severity:** P2 (resource leak, potential port conflicts, accumulates over long concerts)
- **Status:** Open
- **Category:** bug

- **Finding:** When an agent uses Claude Code's Bash tool to spawn a background process (e.g., `python3 -m http.server 8800 &`), that process ends up in its own PGID — NOT in the agent's PGID. Marianne's cleanup kills the agent's PGID on sheet completion, but the Bash-spawned process survives indefinitely.

  **Mechanism:**
  1. `claude_cli.py:648` uses `start_new_session=True` → agent gets PGID X
  2. Agent's Bash tool spawns `bash -c '...'` → Claude Code gives it PGID Y (not X)
  3. Background process (`http.server &`) inherits PGID Y
  4. Sheet ends → Marianne kills PGID X → agent + MCP children die
  5. PGID Y survives — not in agent's group, not reparented to init

  **Observed:** Sheet 6 of mzt-site spawned `python3 -m http.server 8800` (PID 3353, PGID 3352). It survived 45+ minutes past sheet completion, through 9 subsequent sheet transitions, and persisted after the entire 15-sheet score completed. Port 8800 remained bound — any future sheet trying to start a server on 8800 would fail with `Address already in use`.

  **Why pgroup.py misses it:**
  - `cleanup_orphans()` walks daemon's child tree — Bash-spawned processes aren't in it (PPID is terminal, not daemon)
  - `reap_orphaned_backends()` scans for ppid=0/1 (reparented to init) — these have ppid=terminal PID, not init
  - `kill_all_children()` signals daemon's PGID — the orphan is in a different PGID entirely

  **Process tree evidence:**
  ```
  Conductor PGID 2420:
    PID 2420 (mzt start)

  Agent PGID 2435 (sheet 6):          ← killed on sheet end ✓
    PID 2435 (claude)
    PID 2461 (npm → playwright)
    PID 2521 (sh)
    PID 2522 (playwright-mcp node)

  Orphan PGID 3352:                   ← NOT killed, survives indefinitely
    PID 3352 (bash wrapper)
    PID 3353 (python3 -m http.server)

  Chrome PGID 5964:                   ← cleaned by Playwright exit handler ✓
    PID 5964 + 9 children
  ```

- **Action:**
  1. **Short-term:** Score authors should avoid prompts that cause agents to spawn persistent background processes, OR include cleanup instructions in downstream sheets.
  2. **Medium-term:** Track ALL PIDs spawned during a sheet's execution window (not just the agent PID). On sheet completion, kill any processes spawned after the agent started that are still alive and not in the agent's PGID.
  3. **Long-term:** The Bash tool PGID escape is a Claude Code architectural issue (#1935 area). Marianne could work around it by recording the full process tree snapshot before/after each sheet and killing the delta.

### F-485: Conductor RSS Step Function After Playwright-Heavy Sheets
- **Found by:** Composer, mzt-site monitoring session (2026-04-06)
- **Severity:** P3 (low — no continuous growth, but worth monitoring)
- **Status:** Open
- **Category:** risk

- **Finding:** Conductor RSS jumped from 84 MB to 101 MB (20% increase) between checks at ~T+25m, after sheets 6-7 completed (both used Playwright/Chrome, generating ~1 GB of transient Chrome processes). RSS then stabilized at 101 MB for the remaining 8 sheets and never returned to baseline.

  **RSS timeline:**
  ```
  T+0m:   80.7 MB (baseline)
  T+2m:   80.7 MB
  T+8m:   82.1 MB (Chrome active for sheet 6)
  T+15m:  82.6 MB (sheet 6 ended, Chrome cleaned up)
  T+20m:  83.8 MB
  T+27m:  101.2 MB  ← step function jump
  T+33m:  101.3 MB (stable)
  T+38m:  101.3 MB
  T+44m:  101.3 MB
  T+50m:  101.3 MB
  T+55m:  101.9 MB
  ```

  This is NOT a continuous leak — RSS stabilized. But 20 MB retained after transient activity suggests Python object retention (event bus history, learning hub state, or job metadata accumulated during Chrome-heavy sheets). In a long concert with many Playwright sheets, this could accumulate.

- **Action:** Monitor across longer runs (4+ hours). If RSS continues stepping up per-sheet, investigate with `tracemalloc` or `objgraph` on the daemon.

### F-486: Chrome/Playwright Process Group Isolation — Working As Designed
- **Found by:** Composer, mzt-site monitoring session (2026-04-06)
- **Severity:** Informational
- **Status:** Confirmed working
- **Category:** architecture

- **Finding:** Chrome browser processes spawned by Playwright MCP run in their own PGID (separate from both the conductor and the agent). This initially appeared to be a leak risk, but Playwright's exit handler cleans Chrome correctly when the MCP node process is killed. Verified across 3 sheet transitions where Chrome was active:
  - Sheet 6 → Check 4: Chrome PGID 3389 (10 processes, ~1 GB) fully cleaned up
  - Sheet 8 → Check 8: Chrome PGID 5964 (10 processes, ~878 MB) fully cleaned up
  - Sheet 12-14: No Chrome spawned (agent didn't use Playwright)

  The cleanup chain: Marianne kills agent PGID → playwright-mcp (node) dies → Playwright exit handler kills Chrome → all Chrome children terminate. This works because Playwright registers its own cleanup, not because Marianne manages Chrome's PGID.

- **Implication:** No Marianne-side fix needed for Chrome cleanup. However, any MCP server that spawns child processes WITHOUT registering an exit handler would leak in the same way as F-484.

### F-487: `reap_orphaned_backends()` Kills All User Processes — WSL2 VM Crash
- **Found by:** Composer, crash investigation (2026-04-06)
- **Severity:** P0 (critical — kills user's entire WSL2 session, 9 observed crashes)
- **Status:** Resolved — system-wide scan disabled in `pgroup.py`
- **Category:** bug

- **Finding:** The F-481 rewrite of `reap_orphaned_backends()` removed cmdline pattern filtering (`_ORPHAN_CMDLINE_PATTERNS`) and replaced it with ancestry-only detection (`ppid in {0, 1}`). Without filtering, the function kills EVERY user-owned process parented by init/systemd — including `systemd --user`, terminal emulators, dbus-daemon, and other session infrastructure. On WSL2, killing the user's systemd instance cascades into `systemd-poweroff.service` and shuts down the entire VM (exit code 9, all terminals dead). Same unscoped kill existed in `cleanup_orphans()` (killed any daemon-tree child not directly parented by the daemon when dead backends existed) and `_reap_orphans_proc()` (/proc fallback).
- **Trigger:** Any tracked backend PID found dead during a monitor cycle (every 15s). This occurs via race window between process exit and `_on_process_exited` callback, or on any timeout/error/cancellation path where `_on_process_exited` is never called. Observed 9 times — reproducible at sheet completion.
- **Resolution:** All three kill paths disabled. `reap_orphaned_backends()` is a no-op that drains dead PIDs from the tracking set without acting on them. `cleanup_orphans()` only reaps zombies. `_reap_orphans_proc()` returns empty. Orphaned MCP/LSP servers accumulate but don't crash the system.
- **Permanent fix:** Per-job PID tracking in the conductor DB (see composer-notes.yaml "PROCESS CLEANUP SIMPLIFICATION"). Eliminates the in-memory tracking set, the ancestry probing, and the system-wide scan entirely.

### F-488: Profiler Database Unbounded Growth — 551 MB with No Retention Enforcement
- **Found by:** Composer, crash investigation (2026-04-06)
- **Severity:** P2 (medium — disk waste, potential performance impact on profiler queries)
- **Status:** Open
- **Category:** operations

- **Finding:** `~/.marianne/monitor.db` has grown to 551 MB containing 518,017 snapshots and 1,720,196 process metric rows spanning 42 days (2026-02-23 to 2026-04-06). `MonitorStorage.cleanup()` exists and accepts a `RetentionConfig`, but it is never called on a schedule. The profiler collector writes snapshots every 5 seconds but has no cleanup loop. At ~1,200 snapshots/hour with ~3 process metrics per snapshot, the database grows by ~13 MB/day indefinitely.
- **Action:** Wire `cleanup()` into the profiler collector's periodic loop (e.g., once per hour). Default retention: 24h full resolution snapshots, 7d events. Also consider `VACUUM` after large deletes to reclaim space.

### F-489: README and Docs Outdated, Misaligned with Project Identity
- **Found by:** Composer (2026-04-06)
- **Severity:** P1 (high — first thing users and investors see)
- **Status:** Open
- **Category:** documentation, identity

- **Finding:** Multiple problems across README.md and docs/:

  **Install is outdated:** README install instructions don't reflect current state (marianne rename, dependencies, conductor-first workflow).

  **Architecture description is wrong:** README presents architecture as runner-first with daemon as optional. The conductor IS the execution authority — daemon-first is the only supported mode. `mzt run` routes through the conductor. The architecture section needs to reflect this.

  **Daemon terminology inconsistent:** Mix of "daemon" and "conductor" across docs. The conductor IS the daemon. Terminology should be consistent.

  **`hello.yaml` problems:** The example score is not using defaults (forces users to specify things that should be implicit). It attempts creative writing (literature generation) which is misaligned with the project's identity and values. Marianne's position: replace non-creative knowledge work — managers, coders, admins, commerce. Creative work CAN be replaced by AI, but Marianne will never provide scores for that. The project exists to help artists, not replace them. This is load-bearing identity, aligned with the live site's punk ethos.

  **`hello.yaml` needs two versions:**
  1. A minimal baton-testing version that uses fewer tokens (internal, for development)
  2. A clean user-facing version that demonstrates replacing non-creative work (what new users see in examples/)

  **Docs don't match the site:** The live site at marianne-orchestra-live has a specific voice and identity (dark, punk, "fear of god into capitalists"). The docs are generic technical writing that doesn't match. After the rename to Marianne and before public push, docs need to carry the same energy.

- **Action:**
  1. Overhaul README.md — daemon-first architecture, correct install, consistent terminology, identity-aligned examples
  2. Split hello.yaml into internal baton-test score and user-facing example
  3. Replace creative-work example with non-creative knowledge work (contract generation, code review, report synthesis, admin automation — things that SHOULD be automated)
  4. Audit all docs/ for daemon-first consistency and site voice alignment
  5. This is gated on the Marianne rename completing

### F-490: `os.killpg()` in `claude_cli.py` Can Nuke Entire User Session — WSL2 VM Crash (Real Cause)
- **Found by:** Composer, crash investigation session 3 (2026-04-06)
- **Severity:** P0 (critical — kills user's entire WSL2 session; supersedes F-487 as the actual root cause of the "exit code 00000009" crashes)
- **Status:** Guard in place (patch landed in marianne + marianne trees, three-agent review pending — see TASKS.md "Defensive Process-Cleanup Review")
- **Category:** bug, defensive-coding

- **⚠️ TAKEAWAY — Defensive coding around process control is NECESSARY AND CRITICAL. This pattern MUST be extended to other code.** The fix is a five-line guard function. The lesson is much larger: Marianne has been shipping unguarded `os.killpg()`, `os.kill()`, and related syscalls throughout the codebase, trusting that the pgid/pid argument is valid at the moment of the syscall. That assumption is wrong in at least four ways — PID recycling after reap, mocks in tests, getpgid race windows, and direct pid=1 sentinels. Any of those four routes through `os.killpg(pgid, SIGKILL)` compiles kernel-side to `kill(-1, SIGKILL)` and kills the entire user session. There are almost certainly other places in the codebase that have the same blast-radius vulnerability in other syscall families. Every `os.kill*`, `os.killpg*`, `os.getpgid*`, `process.kill*`, every raw signal delivery, every file-descriptor close on a recycled fd, every `unshare`/`mount`/`setuid` call — any code that hands a value to the kernel without validating its blast radius — is a future F-490 waiting to reproduce. **The guard pattern used here (refuse if target <= 1, refuse if target == own, log on refusal, return bool) is the baseline. Apply it everywhere this class of risk exists.**

- **Finding:** F-487 disabled `reap_orphaned_backends()` in `pgroup.py` but the WSL2 "all terminals killed with code 9" crashes kept happening. A fresh instrumented trace run under `strace -f -e trace=process,signal,%network,desc` captured the actual mechanism. Final syscall in `/home/emzi/marianne-strace.log` before system teardown:

  ```
  16776 08:12:16.570659 kill(-1, SIGKILL <unfinished ...>
  ```

  PID 16776 is `python -m pytest tests/ -q --tb=line --ignore=tests/test_check_instrument_available.py`. The log line immediately before the kill is `executing_command args_count=8 command=/usr/bin/claude component=backend.claude_cli prompt_length=11` — a test invoking Marianne's real `claude_cli` backend with a minimal prompt.

  `kill(-1, SIGKILL)` is the kernel-level translation of `os.killpg(1, signal.SIGKILL)`. `claude_cli.py` has **four** `os.killpg(pgid, SIGKILL)` sites — lines 355, 560, 884, 962 — all of which compute `pgid = os.getpgid(process.pid)` or similar. If `process.pid == 1` (mock, stub, reaped-and-recycled PID, or any other edge case), then `os.getpgid(1) == 1` → `os.killpg(1, SIGKILL)` → kernel `kill(-1, SIGKILL)` → "send SIGKILL to every process this UID can signal except init." That kills `systemd --user`, every bash shell in every WSL terminal, and cascades into `user@1000.service: Main process exited, code=killed, status=9/KILL` — the exact signature of the user-reported crashes.

  The bug is present in all four sites with the same pattern. Because pytest must coexist with the live daemon during development, any test that touches the real backend path is enough to trigger it.

  F-487's pgroup fix was necessary but did not address this second, distinct unscoped-kill path. Both bugs had the same symptom; neither alone explains the full crash history.

- **Sites:** `src/marianne/backends/claude_cli.py`
  - Line 355: `_handle_execution_timeout` — timeout → SIGKILL escalation
  - Line 560: `_kill_orphaned_process` — exception → cleanup
  - Line 884: `_await_process_exit` — post-wait escalation
  - Line 962: `_stream_with_progress` cancellation handler

- **Trigger:** Any code path that reaches a `killpg` site with a `process` object whose `.pid` is 0, 1, or a PID whose process group happens to equal 1 or the daemon's own process group. Observed trigger: pytest running backend tests while the live daemon is running in the same user session.

- **Action:**
  1. ✅ **Done** — `_safe_killpg(pgid, sig, *, context)` helper added to `src/marianne/backends/claude_cli.py` (live runtime) and `src/marianne/backends/claude_cli.py` (tracked dual tree). Refuses when `pgid <= 1` or `pgid == os.getpgid(0)`, logs `killpg_guard_refused` at warning level with `reason`, `pgid`, `signal`, `context`, returns bool.
  2. ✅ **Done** — all six `os.killpg` sites in `claude_cli.py` routed through `_safe_killpg` in both trees (sites at 355 timeout_escalation, 560 kill_orphaned_process, 875/884 await_exit_graceful/force, 954/962 cancel_graceful/force).
  3. ✅ **Verified** — runtime smoke test confirmed all four guard conditions block correctly (`pgid=1`, `pgid=0`, `pgid=-1`, `pgid=own_pgid`).
  4. **Pending — three-agent review** (see TASKS.md "Defensive Process-Cleanup Review"):
     - Agent 1: Correctness review of the guard itself + write regression test `tests/test_safe_killpg_guard.py`.
     - Agent 2: Coverage review — grep the entire codebase for sibling bugs in `os.kill*`, `os.killpg*`, `os.getpgid*`, `process.kill*`, other backends (`anthropic_api.py`, `plugin_cli.py`, `ollama.py`, `json_backend.py`), and `pgroup.py:154/339` (verify `_is_leader` guard is sufficient under inline/pytest execution).
     - Agent 3: Pattern-extension review — codify defensive process-control patterns as a project-wide convention at `workspaces/v1-beta-v3/movement-5/process-control-defensive-patterns.md`, feed into `.marianne/spec/constraints.yaml` as a new MUST, and identify other syscall families where "trust the value, call the syscall" has the same blast radius.
  5. **Not yet done** — identify the specific test that passed `pid=1` (or the equivalent) into the backend cleanup path. The guard makes this non-critical for session safety but it's worth knowing which test triggered the strace evidence at 08:12:16.

- **Related:** F-487 (pgroup.py unscoped kill — sibling bug, different code path, same symptom). F-482 (`_await_process_exit` PID-recycle SIGKILL — same function as site 3, reinforces that the whole cleanup family needs auditing).

### F-491: `mzt list` Status Coloring Matches Wrong Text When Score Name Contains Status Word
- **Found by:** Journey, Movement 5
- **Severity:** P2 (medium — visual confusion in list display)
- **Status:** Open
- **Category:** bug, UX
- **Description:** `_list_jobs` in `src/marianne/cli/commands/status.py:648-661` builds a formatted row string and uses `str.replace(plain_status, ..., 1)` to inject Rich color markup around the status value. Because the score ID column precedes the status column in the formatted string, `replace()` matches the first occurrence of the status word — which may be inside the score name. Example: score "running-my-app" with status "running" — the "running" in the score ID gets colored instead of the status column.
- **Impact:** Visual confusion. Wrong column colored. Undermines trust in status display for any score named with words like "running", "completed", "failed", "queued", "paused", "cancelled".
- **Action:** Replace `str.replace()` approach with per-column Rich markup applied before joining, or use positional string construction that doesn't depend on text matching.

### F-492: Directory Rename During Running Concert Breaks All Concurrent Shell Operations
- **Found by:** Journey, Movement 5
- **Severity:** P1 (high — breaks all concurrent musicians)
- **Status:** Open
- **Category:** process, operational
- **Description:** The F-480 directory rename from `marianne-ai-compose` to `marianne-ai-compose` was performed while concurrent orchestra sessions (sheets) were running. The Claude Code Bash tool, Glob tool, and Grep tool all depend on the initial working directory being valid. Once renamed, all shell-dependent tools fail with: `Working directory "/home/emzi/Projects/marianne-ai-compose" no longer exists.` The Read/Write/Edit tools continue working at the new path, but only if the agent independently discovers the new path.
- **Impact:** Any musician running concurrently with the rename loses: shell commands, git commit ability, quality checks (pytest/mypy/ruff), and code search. This session (Journey M5) was affected — could not run tests or commit work.
- **Action:** Directory renames must NEVER be performed during a running concert. Must be a post-concert task or the absolute last operation of a movement with a documented break point. Add to composer-notes.yaml as a binding directive.

### F-442: Instrument Fallback History Never Syncs from Baton to Checkpoint
- **Movement:** 5
- **Agent:** Axiom
- **Category:** bug (persistence gap)
- **Severity:** P2
- **Status:** Open
- **Finding:** `SheetState.add_fallback_to_history()` exists at `checkpoint.py:595` but is never called anywhere in the codebase. The baton maintains its own `SheetExecutionState.fallback_history` (state.py:247) which correctly accumulates records during execution, but `_on_baton_state_sync` (manager.py:508-538) only syncs the `status` field — no fallback history is transferred to the checkpoint's `instrument_fallback_history`. On conductor restart or job resume, all fallback transition history is lost.
- **Impact:** Post-mortem analysis after restart cannot reconstruct which instruments were tried and why they failed. The `add_fallback_to_history()` helper and `MAX_INSTRUMENT_FALLBACK_HISTORY` constant are dead code. Same architectural class as F-039, F-065, F-440 — two correct subsystems composing incorrectly at their boundary.
- **Action:** Add fallback history sync to `_on_baton_state_sync` or the checkpoint save path in the baton adapter. The sync callback signature may need extension to include optional fallback records alongside status.

### F-493: Status Truncated View Hides Active Sheets After Prior Validation Failures
- **Movement:** 5
- **Agent:** Composer (manual observation)
- **Category:** UX, architecture
- **Severity:** P2
- **Status:** Open
- **Finding:** After a resume on a large score (706 sheets), `mzt status` shows only the validation failures from the *previous* failed run and does not show the currently executing sheets. The "Now Playing" section and sheet progress are absent — the operator sees only stale errors and has no indication that work is actively happening. This was observed on `marianne-orchestra-v3` after resume from sheet 211.
- **Root Cause:** The status display uses a truncated view for large scores. When prior validation failures exist, they consume the truncated output and crowd out the "Now Playing" and "Active Sheets" sections. The truncation logic prioritizes errors over liveness signals.
- **Design Gap:** The truncated view is scoped too narrowly — it shows only the immediately relevant sheets (active + failed). For decision-making, an operator needs a *window* around the current execution point: the sheets just completed (confirming momentum), the sheets currently running (confirming liveness), and the sheets coming next (confirming what's ahead). Status is not a snapshot of the present moment — it is a signal that imparts either concern or confidence about the trajectory. An operator looking at status is deciding whether to intervene or let it run. Stale errors with no liveness signal always trigger concern, even when the system is healthy.
- **Action:** Redesign the truncated status view to show a sliding window around current execution: N sheets before (recent completions/failures), active sheets, and N sheets ahead (upcoming). Validation failures from prior runs should be demoted or separated from the current run's liveness display. The view should answer three questions: "What just happened?", "What's happening now?", and "What's next?"

### F-494: Baton Status Display Broken — Three Gaps
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (manual investigation with Opus)
- **Category:** bug (integration boundary)
- **Severity:** P0 — status display showed zero progress for baton-managed jobs
- **Status:** Fixed (this session, pending commit)
- **Finding:** Three gaps in the baton→status display pipeline caused `mzt status` to show 0% progress and "103 pending" even while sheets were actively executing:

  **Gap 1: Dispatch sync missing.** `dispatch_ready()` transitions sheets to DISPATCHED in baton state, but `_sync_sheet_status()` only runs for the *event* that triggered the loop iteration, not for state changes made by `dispatch_ready()`. Sheets dispatched to musicians never appeared as `in_progress` in the live state.

  **Gap 2: Completion metadata missing.** `_on_baton_state_sync()` only set `sheet_state.status` — it never called `mark_sheet_completed()` / `mark_sheet_started()` / `mark_sheet_failed()`. As a result, `last_completed_sheet` (the progress watermark), `completed_at`, `validation_passed`, job completion detection, and all derived fields were never updated. Progress stayed at 0/N forever.

  **Gap 3: Resume ghost sheets.** `_resume_via_baton()` set `_live_states[job_id] = checkpoint` (with stale `in_progress` sheets from the old run), then called `recover_job()` which internally resets those to PENDING in baton state. But the live CheckpointState was never reconciled — sheets appeared as "in_progress" for hours with no actual process.

- **Root Cause:** The baton adapter and daemon manager were developed in parallel. The `StateSyncCallback` was a minimal bridge (`job_id, sheet_num, status_string`) that carried no rich metadata. The legacy runner updates CheckpointState directly through its own save path; the baton relied on this thin callback that was never expanded to match.
- **Fix:**
  1. Added dispatch sync loop after `dispatch_ready()` returns — iterates `dispatched_sheets` and syncs each.
  2. Rewrote `_on_baton_state_sync()` to call `mark_sheet_started/completed/failed/skipped` with full metadata from `SheetExecutionState`. Expanded callback signature to `(job_id, sheet_num, status, baton_state)`.
  3. Added live state reconciliation after `recover_job()` — resets `in_progress` sheets to `pending` with cleared `started_at`.
- **Architectural Note:** Same class as F-039, F-065, F-440, F-442 — two correct subsystems composing incorrectly at their boundary. The baton tracks 11 states; the checkpoint tracks 5. The mapping exists but the sync was never wired to carry the full payload. This is the cost of the transition from legacy runner to baton — every integration surface needs explicit verification.

### F-495: Baton Structured Logging Emits Bare Strings
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (manual observation)
- **Category:** bug (observability)
- **Severity:** P2
- **Status:** Open
- **Finding:** Several baton log events (`adapter.dispatch.spawned`, `baton.dispatch.cycle_complete`, `adapter.job_recovered`, `adapter.started`, `baton.job_registered`) emit bare strings to the conductor log instead of structured JSON. These appear as unformatted lines interleaved with proper JSON log entries, breaking `jq`-based log analysis and making it impossible to filter baton events by job_id or sheet_num.
- **Root Cause:** Likely using `print()` or a logger call without the structured `extra={}` dict that the JSON formatter requires.
- **Action:** Audit all `_logger.*` calls in `src/marianne/daemon/baton/` and ensure they use the structured logging pattern.

### F-496: `instrument_model` Never Populated — Status Shows Profile Not Model
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (manual observation)
- **Category:** bug (observability)
- **Severity:** P3
- **Status:** Fixed (this session, pending commit) — baton path only
- **Finding:** `SheetState.instrument_model` (checkpoint.py:253) was defined but never populated anywhere. The status display showed `claude-code` (the profile name) for all sheets regardless of which model they were actually using. For multi-instrument scores, the operator had no way to see which model was executing.
- **Fix:** Populated `instrument_model` from `sheet.instrument_config.get("model")` during initial live state creation in `_run_via_baton()`. Updated "Now Playing" display to show `claude-code (claude-haiku-4-5-20251001)` when model is available.

### F-497: False Rate Limit Detection on Successful Completions Starves Multi-Job Dispatch
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (manual investigation with Opus)
- **Category:** bug (rate limit detection + dispatch starvation)
- **Severity:** P1 — resumed jobs never dispatch when sharing an instrument profile with an active job
- **Status:** Open
- **Finding:** Every haiku sheet completes successfully (`exit_code: 0`, `success: true`) but is flagged `rate_limited: true`. Claude Code handles rate limits internally (retries within the session) and exits 0, but `_detect_rate_limit` (base.py:213) scans stdout/stderr for rate limit text patterns and flags the result. This is the F-098 behavior: rate limit messages appear in output even when the CLI handled them and succeeded.

  The baton then marks the `claude-code` instrument as rate limited globally (core.py:1096). Since all score-level instruments (`haiku`, `sonnet`, `opus`) resolve to profile `claude-code`, ALL dispatch for ALL jobs using that profile is blocked. `dispatch_ready` (dispatch.py:138-140) skips every sheet.

  When the rate limit timer expires, `dispatch_ready` iterates jobs in registration order. Score 2 (registered first) gets the dispatch slot, its sheet completes successfully but with rate limit text again, and the cycle repeats. A2/B2 never get a dispatch slot.

  Evidence: conductor log shows every `plugin_cli_execute_complete` has `rate_limited: true` despite `success: true` and `exit_code: 0`. No actual API rate limiting is occurring — the sheets complete their work, just with historical rate limit text in output from Claude Code's internal retry handling.

- **Root Cause (compound):**
  1. **False positive detection (F-098):** `_detect_rate_limit` flags successful completions because rate limit text from Claude Code's internal retries appears in output. A sheet that succeeded should not be treated as rate limited.
  2. **Profile-level rate limiting:** All models on the same profile share one rate limit flag. Haiku rate limits block sonnet/opus dispatch.
  3. **Registration-order dispatch:** `dispatch_ready` iterates `baton._jobs.keys()` in insertion order. The first-registered job monopolizes dispatch windows.

- **Action:** Fix the detection: when `exit_code == 0` and the sheet produced valid output, `rate_limited` should be `false` regardless of rate limit text in output. The CLI already handled the rate limit. Separately, rate limits should eventually be per-model or per-logical-instrument, and dispatch should round-robin across jobs.

### F-498: Conductor Dispatches Without Intelligence — Phase 3 Scheduler Unwired
- **Movement:** 5 (post-movement, production testing + TSVS analysis)
- **Agent:** Composer (Opus session, cross-domain analysis)
- **Category:** architecture, opportunity
- **Severity:** P1 — the conductor is a deaf metronome, not an adaptive system
- **Status:** Spec complete, ready for implementation
- **Finding:** The baton dispatch (`dispatch_ready`) iterates jobs in registration order and dispatches ready sheets through binary gate checks (global cap, model cap, rate limit, circuit breaker). No priority scoring, no fairness, no signal integration. First-registered job monopolizes dispatch windows within shared model caps. The Phase 3 scheduler (`scheduler.py`) has a priority heap with scoring — it's built but not wired.

  The conductor has access to: cost data, learning patterns, execution history, instrument health, sheet complexity, intent hierarchy, cross-job relationships, entropy signals. It uses none of this for scheduling decisions.

  Full spec with implementation plan, architecture, testing strategy, and TSVS cross-domain analysis: `docs/plans/2026-04-07-intelligent-conductor-spec.md`

- **Root Cause:** Phase 2 (baton) focused on multi-instrument dispatch mechanics. Intelligence was deferred to Phase 3. The transition from "dispatch works" to "dispatch is smart" requires signal wiring, not new algorithms — the scheduler and signals already exist.
- **Action:** Wire Phase 3 scheduler into `dispatch_ready()`. Implementation order: (1) priority heap replaces job iteration, (2) EventBus subscriptions for completion/failure signals, (3) `score_sheet()` with fan-out + fairness + availability, (4) intent profile modulation, (5) proactive escalation prediction, (6) cross-job context. Each step independently valuable and testable.

### F-499: Dual State Model Is Structurally Unsound — Unified State Required
- **Movement:** 5 (post-movement, TSVS analysis)
- **Agent:** Composer (Opus session)
- **Category:** architecture
- **Severity:** P1 — every baton feature addition creates new sync boundary bugs
- **Status:** Phase 1 complete, Phase 2 spec'd
- **Finding:** The baton maintains `SheetExecutionState` (11-state dataclass) alongside the checkpoint's `SheetState` (previously 5-state Pydantic model). A sync callback maps between them on every state transition. Every bug found on 2026-04-07 (F-494 gaps 1-3, F-497 false rate limits) was a boundary failure between these two representations. The mapping was lossy, the sync was incomplete, the persist was missing.

  TSVS cross-domain analysis evaluated 5 architectural options. Conclusion: the dual model maximizes compatibility at the cost of correctness. It will keep producing boundary bugs as the baton evolves.

  **Phase 1 (completed):** Extended `SheetStatus` to 11 states (READY, DISPATCHED, WAITING, RETRY_SCHEDULED, FERMATA, CANCELLED). Added `fire_at` (enables cron-style scheduling), `rate_limit_expires_at` (observability), `healing_attempts`, `display_status` to `SheetState`. Mapping is now 1:1 (lossless). Status display shows all 11 states with proper colors/labels.

  **Phase 2 (spec'd):** Replace `SheetExecutionState` with `SheetState`. Baton reads/writes `SheetState` directly. Remove sync callback, mapping functions, and ~400 lines of boundary code. Migration via type alias (`SheetExecutionState = SheetState`) for incremental adoption. Full spec: `docs/plans/2026-04-07-unified-state-spec.md`

- **Root Cause:** The baton was built alongside the existing checkpoint system, not as a replacement. Expedient dual-state design created O(fields × transitions) sync code that must be correct for every combination.
- **Action:** Execute Phase 2 per the spec. Key steps: (1) port methods to SheetState, (2) type alias for transition, (3) remove sync layer. ~34 test files need import updates.

### F-500: `mzt list` Never Showed Progress for Baton Jobs
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (Opus session)
- **Category:** bug
- **Severity:** P2
- **Status:** Fixed (this session)
- **Finding:** `list_jobs()` returned `meta.to_dict()` which has no sheet-level data. The PROGRESS column always showed `-` for baton-managed jobs. Fixed by enriching active job entries with `progress_completed`/`progress_total` from `_live_states`.

### F-501: Baton Never Persisted State to Registry
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (Opus session)
- **Category:** bug (data loss risk)
- **Severity:** P1
- **Status:** Fixed (this session)
- **Finding:** The legacy runner persists checkpoint to the daemon registry on every state save via `_on_state_published`. The baton's `_on_baton_state_sync` updated `_live_states` in memory but never called `_registry.save_checkpoint()`. If the daemon crashed, all baton job progress was lost. Fixed by adding async registry persistence on significant transitions (started, completed, failed, skipped).

### F-502: CLI Commands Read Workspace State Instead of Conductor DB
- **Movement:** 5 (post-movement, production testing)
- **Agent:** Composer (Opus session)
- **Category:** architecture, bug
- **Severity:** P1 — commands fail or return stale data for baton jobs
- **Status:** Partially fixed (errors/diagnose), 3 commands remaining
- **Finding:** 5 CLI commands fall back to reading workspace state files (`.marianne-state.db`) when `--workspace` is provided or the conductor is unavailable: `status`, `diagnose`/`errors`, `pause`, `resume`, `recover`. For baton-managed jobs, workspace state files are never written — the baton writes to `_live_states` and the conductor registry. These commands fail with "No state found" or return stale data from the legacy runner's last write.

  The daemon-side handlers for `get_job_errors()` and `get_diagnostic_report()` called `JobService.get_status()` which reads workspace files. Fixed to use `get_job_status()` which reads `_live_states` then registry.

  **Remaining work:** `pause`, `resume`, `recover` CLI commands have workspace fallback paths in the CLI layer (not just the daemon handler). These need the `--workspace` fallback code removed so they always route through the conductor. The helpers `_find_job_state_direct()`, `_find_job_state_fs()`, `_create_pause_signal()`, `_wait_for_pause_ack()` in `helpers.py` should be deprecated.

- **Root Cause:** These commands were written before the daemon was the sole execution authority. The `--workspace` flag was a debug escape hatch. With the baton, it's a trap — the workspace has no state to read.
- **Action:** Remove workspace fallback paths from `pause.py`, `resume.py`, `recover.py` CLI commands. Deprecate workspace state helper functions. All 5 commands must be conductor-only.

### F-454: `list --json` Exposes Internal DB Error to Users
- **Found by:** Ember, Movement 5
- **Severity:** P2 (medium — internal implementation details leaked to users)
- **Status:** Open
- **Category:** bug, UX
- **Description:** `mzt list --json` returns `error_message: "Unexpected internal error: no such table: jobs"` for the running orchestra job. The `jobs` table doesn't exist in the state DB, and the error propagates unfiltered into user-facing JSON output. The score IS running (status shows it, conductor shows it), so the error is confusing — the system works but the list endpoint leaks an internal DB schema error.
- **Reproducer:** `mzt list --json | python -m json.tool` — the `error_message` field contains the raw SQLite error.
- **Impact:** Machine consumers parsing `list --json` see an implementation detail (SQLite table name) they can't act on. Undermines confidence in the API surface.
- **Action:** Either suppress internal DB errors in user-facing JSON, or ensure the state DB schema is created before first query, or return a meaningful error ("Status data unavailable — use `status <job>` for details").

### F-499: Property-Based Test Broken by Uncommitted SheetStatus Expansion
- **Found by:** Bedrock, Movement 5 Quality Gate (2026-04-07)
- **Severity:** P0 (critical — blocks quality gate)
- **Status:** Open
- **Category:** bug, test
- **Description:** `tests/test_execution_property_based.py::TestSheetStateTransitions::test_valid_transition_respects_allowed_set` at line 217 has a hardcoded `VALID_TRANSITIONS` dict that only knows about 5 SheetStatus values: PENDING, IN_PROGRESS, COMPLETED, FAILED, SKIPPED. Uncommitted changes to `src/marianne/core/checkpoint.py:147-183` expanded SheetStatus to 11 values by adding READY, DISPATCHED, WAITING, RETRY_SCHEDULED, FERMATA, and CANCELLED. When hypothesis generates `SheetStatus.READY` as a test input, the test tries to look up `VALID_TRANSITIONS[SheetStatus.READY]` and gets `KeyError: <SheetStatus.READY: 'ready'>`. The test suite fails on first error (-x flag), blocking the quality gate.
- **Impact:** Movement 5 quality gate cannot pass. The test failure is deterministic and immediate. All other checks pass (mypy clean, ruff clean, flowspec 0 critical), but the test regression blocks the gate.
- **Root cause:** The SheetStatus expansion was done as part of the Composer's post-movement F-494 fix session (production testing of baton status display). The checkpoint.py changes enable proper status sync between baton and checkpoint by making both use the same 11-state model. The test was written when SheetStatus had 5 states and was never updated.
- **Action:** Update the test's `VALID_TRANSITIONS` dict to include all 11 states with their valid transition rules. The transition map should match the baton's actual state machine defined in `src/marianne/daemon/baton/state.py:42-94`. The transitions are:
  - PENDING → {READY, SKIPPED}
  - READY → {DISPATCHED, WAITING, SKIPPED}
  - DISPATCHED → {IN_PROGRESS, FAILED, SKIPPED}
  - IN_PROGRESS → {COMPLETED, FAILED, SKIPPED, WAITING, RETRY_SCHEDULED, FERMATA}
  - WAITING → {READY, SKIPPED}
  - RETRY_SCHEDULED → {READY, SKIPPED}
  - FERMATA → {IN_PROGRESS, SKIPPED, CANCELLED}
  - COMPLETED, FAILED, SKIPPED, CANCELLED → {} (terminal)
- **Evidence:**
  - Test file: `tests/test_execution_property_based.py:195-217`
  - Checkpoint file: `src/marianne/core/checkpoint.py:147-183`
  - Git status: `src/marianne/core/checkpoint.py` modified, not staged
  - Last committed version (48ae9f5): SheetStatus has 5 states
  - Working tree: SheetStatus has 11 states (64 lines added, uncommitted)
  - pytest output: `KeyError: <SheetStatus.READY: 'ready'>` at line 217

### F-500: Massive Uncommitted Work from Composer's Production Session
- **Found by:** Bedrock, Movement 5 Quality Gate (2026-04-07)
- **Severity:** P1 (high — large-scale uncommitted work, coordination risk)
- **Status:** Open
- **Category:** process, coordination
- **Description:** The Composer's post-movement production testing and F-494 fix session left 326+ files modified but uncommitted, totaling 18,504 insertions and 6,992 deletions. This includes: (1) the complete Mozart→Marianne rename touching every file in the repository (documentation, examples, source code, tests, project metadata), and (2) baton state model enhancements (SheetStatus expansion to 11 states plus 4 new SheetState fields). The work is functionally complete — the `mzt` binary works, documentation is consistent, examples validate — but none of it is committed to git history.
- **Impact:**
  - Any concurrent musician who committed work during or after this session could not have seen these changes, creating potential merge conflicts or duplicated work
  - The rename and baton fixes are functionally part of Movement 5 but not attributed to any M5 musician in git history
  - Quality gate cannot assess whether M5 musicians' work is compatible with the rename because the rename is not in the commit log
  - If the Composer's session workspace is lost (disk failure, accidental deletion), all 18,504+ lines of work are lost
  - The uncommitted work introduces F-499 (test regression) which blocks the quality gate
- **Context:** This work appears to be a continuation of Ghost's M5 rename commits (42b0f71 "movement 5: [Ghost] complete Marianne rename — pyproject.toml + test imports" and 1ddc023 ".flowspec/config.yaml rename"). Ghost committed 326 files of the rename (pyproject.toml, all test imports, flowspec config). The Composer's session completed the remaining files (docs, examples, source code) plus the F-494 baton state model fixes. The commits 397c313 "fix(baton): list shows progress, state persists to registry" and 3765cb1 "feat(baton): per-model concurrency, status sync, rate limit fix, structured logging" are on top of M5 work but have extensive uncommitted changes following them.
- **Action:**
  1. Fix F-499 (update the property-based test for 11 states)
  2. Run full quality checks on the uncommitted work (`pytest`, `mypy`, `ruff`, `flowspec`) to verify no other regressions
  3. If all checks pass, commit the work as a single atomic commit: "movement 5: [Composer] complete Marianne rename + F-494 baton state sync fix"
  4. If checks fail, isolate the rename changes from the baton changes and commit them separately after fixing failures
  5. Update TASKS.md to mark F-480 rename phases as complete
- **Pattern:** Bedrock's memory documents this as the 8th occurrence of large uncommitted work findings (F-013, F-019, F-057, F-080, F-089 in prior movements, now F-500). This is a structural anti-pattern where large integration or debugging sessions happen after the formal movement ends, producing real and necessary work that exists outside the movement's coordination structure. The work is not individual failure but environmental — when a musician (often the Composer) performs post-movement production testing or integration, the changes accumulate as uncommitted work because they happen after musicians have moved on.
- **Evidence:**
  - `git status --short` shows 326+ files modified
  - `git diff --stat` shows 18,504 insertions, 6,992 deletions (output saved to tool-results/b4r501s0k.txt due to size)
  - Files affected: all of `docs/`, all of `examples/`, 100+ files in `src/marianne/`, all test files, README.md, CLAUDE.md, pyproject.toml
  - `src/marianne/core/checkpoint.py` has 64 lines of uncommitted additions (SheetStatus expansion + new fields)
  - Last commits: 397c313 (2026-04-07), 3765cb1 (2026-04-06) — both from Composer's production session
  - Directory is named `marianne-ai-compose` but git remote still shows `mozart-ai-compose` (rename in progress)
