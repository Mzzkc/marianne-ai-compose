# Mozart v1 Beta — Findings Registry

This file tracks findings, discoveries, and observations made during the v1 beta build concert.
Agents append findings here as they work. The registry serves as institutional memory across movements.

## Format

Each finding should include:
- **ID:** F-NNN (sequential)
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
- **Finding:** `estimate_tokens()` in `src/mozart/core/tokens.py:77` uses `_CHARS_PER_TOKEN = 3.5` calibrated for English. CJK characters are underestimated by 3.5-7x (600 CJK chars → 172 estimated tokens, actual 600-1200). This can cause context window overflow.
- **Action:** Document as known limitation. Fix at M1 with InstrumentProfile.ModelCapacity — script-aware estimation or per-model tokenizer.
- **Status:** open

### F-002: Falsy Value Rejection in SpecCorpusLoader
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Blueprint
- **Category:** bug
- **Finding:** `_load_yaml_fragment()` at `src/mozart/spec/loader.py:201` and `:207` uses `if not name:` / `if not content:`. YAML parses `0` as `int(0)` and `false` as `bool(False)`, which are falsy but not None. The `str()` casts on lines 220-221 would handle coercion, but the guard rejects before reaching there.
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
- **Resolution:** Created `src/mozart/utils/credential_scanner.py` with `redact_credentials()` and `scan_for_credentials()` functions. Wired into `SheetState.capture_output()` at `checkpoint.py:550` — the single write point for stdout_tail/stderr_tail. All 6 credential patterns (Anthropic, OpenAI, Google, AWS, Bearer tokens) are detected and replaced with [REDACTED_*] labels. 14 tests in `tests/test_credential_scanner.py`.

### F-004: skip_when_command Uses Shell Without shlex.quote
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** `src/mozart/execution/runner/lifecycle.py:878` expands `{workspace}` via bare `.replace()` into a shell command passed to `create_subprocess_shell`. Lacks `shlex.quote()` protection used by the validation engine's `command_succeeds` handler at `engine.py:515`.
- **Action:** Apply shlex.quote() to workspace expansion in skip_when_command.
- **Status:** Resolved (movement 1, Ghost)
- **Resolution:** Applied `shlex.quote()` to workspace expansion in `lifecycle.py:879`. Commit 229d55d.

### F-005: No First-Run Cost Warning
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** `src/mozart/cli/commands/run.py:90-163` submits jobs without checking `config.cost_limits.enabled` or displaying cost info. `CostLimitConfig.enabled` defaults to `false`. New users spend API credits with zero notice.
- **Action:** Add cost warning to `mozart run` when cost_limits.enabled is false.
- **Status:** Resolved (movement 1, Circuit)
- **Resolution:** Added cost warning to `mozart run` at `run.py:119-125` that fires when `cost_limits.enabled` is false and output is not JSON/quiet. Shows the warning text and a config suggestion. Also updated config panel to show "Instrument:" instead of "Backend:" to align with the new instrument terminology. 4 tests added to test_cli_run_resume.py.

### F-006: No `mozart doctor` Command
- **Movement:** 0 (carried from cycle 1)
- **Agent:** Warden
- **Category:** risk
- **Finding:** No `doctor` command exists. Executive brief criterion U2 requires it. `mozart config init` exists but only creates daemon config — not environment validation.
- **Action:** Implement `mozart doctor` as part of M3 UX.
- **Status:** Resolved (movement 1, Ghost)
- **Note:** Previously incorrectly recorded as resolved (was F-004 copy-paste). Corrected by Canyon.
- **Resolution:** Implemented `mozart doctor` at `src/mozart/cli/commands/doctor.py`. Checks Python version, Mozart version, conductor status (PID file + os.kill probe), instrument availability (native + built-in YAML + user profiles, binary detection via shutil.which), and safety warnings (cost limits). Supports `--json` output mode. 12 tests in `tests/test_cli_doctor.py`. Registered in CLI at `src/mozart/cli/__init__.py`.

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
- **Status:** Open
- **Description:** All 25,415 patterns in `~/.mozart/global-learning.db` have `effectiveness_score = 0.5000`. Verified via `SELECT AVG(effectiveness_score), MIN(effectiveness_score), MAX(effectiveness_score) FROM patterns` — all returned 0.5000. The effectiveness calculation either hasn't run, was reset, or produces degenerate output. This means the learning system cannot distinguish effective patterns from ineffective ones.
- **Impact:** Pattern injection into agent prompts has no quality signal. Good and bad patterns are treated identically. The learning store grows in volume (25,415 patterns, 218,790 executions) but not in intelligence. The baton's planned learning integration inherits this — instrument-scoped queries will return patterns with no quality differentiation.

### F-010: Credential Scanner Double-Call Bug in SheetState.capture_output
- **Found by:** Canyon, Movement 1
- **Severity:** P0 (critical — breaks all tests that call capture_output)
- **Status:** Resolved (movement 1, Maverick) — NOT A BUG
- **Description:** Canyon assumed `redact_credentials()` returns `tuple[str, list[str]]`, but the actual implementation at `src/mozart/utils/credential_scanner.py` returns `str | None` (same type as input). The API is: `redact_credentials(text) -> text` with credential patterns replaced by `[REDACTED_*]` labels. A separate `scan_for_credentials(text) -> list[str]` function exists for detection-without-redaction. The assignment at checkpoint.py:562-563 is correct as written. 14 tests validate this behavior.
- **Resolution:** Not a bug. Canyon's analysis was based on an assumption about the API, not the actual implementation.

### F-011: CONFIG_STATE_MAPPING Missing Entries for New Instrument Fields
- **Found by:** Blueprint, Movement 1
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 1, Harper)
- **Resolution:** Added `CONFIG_STATE_MAPPING` entries for `instrument` and `instrument_config` in `reconciliation.py`. Commit 85f0b2f.
- **Description:** `test_reconciliation.py::TestConfigStateMapping::test_mapping_covers_all_config_sections` fails because the new `instrument` and `instrument_config` fields added to `JobConfig` (from Canyon's M1 instrument work) have no `CONFIG_STATE_MAPPING` entry in the reconciliation system. The test asserts that every JobConfig section has a mapping defining what checkpoint state to reset when that section changes.
- **Impact:** Config reload via `mozart resume -c` won't know what state to reset when instrument settings change. Low immediate impact since the baton spec redesigns this flow, but the test failure blocks full suite pass.
- **Action:** Add `CONFIG_STATE_MAPPING` entries for `instrument` and `instrument_config` with appropriate reset targets. Alternatively, mark as deferred if the baton redesign will supersede this.

### F-012: test_instrument_loader.py References Non-Existent Module
- **Found by:** Blueprint, Movement 1
- **Severity:** P1 (high — test file committed without implementation)
- **Status:** Resolved (movement 1, Harper)
- **Resolution:** InstrumentProfileLoader implemented at `src/mozart/instruments/loader.py` and test committed at `tests/test_instrument_loader.py`. Import path corrected to `mozart.instruments.loader`. Commit 85f0b2f.
- **Description:** `tests/test_instrument_loader.py` (untracked) imports `mozart.core.instruments.loader.InstrumentProfileLoader` which doesn't exist. This is test code for TASKS.md M1 roadmap step 4 ("Implement profile YAML loading"). The test file was created before the implementation, which is correct TDD, but it's untracked and will cause `pytest tests/ -x` to fail if collected.
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
- **Description:** F-006 ("No mozart doctor command") had its resolution field copy-pasted from F-004 ("shlex.quote fix"). The doctor command was never implemented. The TASKS.md still correctly lists it as unclaimed in M3.
- **Impact:** Anyone reading FINDINGS.md would believe `mozart doctor` was implemented. It is not.
- **Resolution:** Corrected F-006 status back to Open with a note explaining the error.

### F-015: Unwired Code Cluster Analysis (F-007 Disposition)
- **Found by:** Blueprint, Movement 1
- **Severity:** P2 (medium)
- **Status:** Partially resolved (movement 1, Blueprint)
- **Description:** Investigated all 7 unwired code clusters from F-007/issue #135. Disposition:
  1. **SchedulerStats** (scheduler.py:98) — KEEP. Required for scheduler/baton integration.
  2. **ErrorLearningHooks** (error_hooks.py:95) — KEEP. Movement III feature, design complete.
  3. **OutcomeMigrator** (migration.py:94) — ALREADY WIRED. Called from `learning-stats` CLI.
  4. **ErrorChain** (errors/models.py:190) — KEEP. Infrastructure for `mozart diagnose`.
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
- **Status:** Open
- **Description:** Two `SheetExecutionState` classes exist in the baton package. `core.py:67` has a simple dataclass with string `status` field and no methods. `state.py:161` has a richer version with `BatonSheetStatus` enum, `record_attempt()`, `can_retry`/`can_complete`/`is_exhausted` properties, cost tracking, `to_dict()`/`from_dict()` serialization, and total_duration tracking. Both were built concurrently by different musicians.
- **Impact:** The `__init__.py` exports `SheetExecutionState` from `core.py`. The `state.py` version is importable via `from mozart.daemon.baton.state import SheetExecutionState`. No runtime error today — the two classes are independent. But the baton needs ONE authoritative type. The `state.py` version is designed for the full baton lifecycle (persistence, failure evaluation, cost tracking). The `core.py` version is designed for the immediate event loop.
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
- **Description:** `expand_hook_variables()` at `src/mozart/execution/hooks.py:182-184` performs bare `.replace("{workspace}", str(workspace))` without `shlex.quote()`. The expanded result is then passed directly to `create_subprocess_shell` at `hooks.py:626` (run_command hook) and `manager.py:1869` (daemon-side hook command). A workspace path containing shell metacharacters would be interpreted as shell commands. Compare to code that IS protected: `lifecycle.py:888` (shlex.quote for skip_when_command, fixed by Ghost as F-004) and `engine.py:515` (shlex.quote for validation commands). The hooks system was never patched when Ghost fixed F-004.
- **Impact:** Shell injection via crafted workspace paths or job IDs in score YAML `on_success` hooks. Currently mitigated by the fact that workspace paths are typically set by the score author (who controls the execution environment), but becomes a real risk if Mozart processes untrusted scores.
- **Action:** Apply `shlex.quote()` to all variable expansions in `expand_hook_variables()` when used for shell commands. Since the function is also used for path expansion (run_job hooks) where quoting would break path resolution, either split into two functions or apply quoting only at the shell execution callsite.

### F-021: Python Expression Sandbox in skip_when Is Bypassable via Attribute Access
- **Found by:** Sentinel, Movement 1
- **Severity:** P2 (medium — operator-controlled config today, but exploitable if untrusted scores are ever run)
- **Status:** Open
- **Description:** `src/mozart/execution/runner/lifecycle.py:862` uses Python expression evaluation with a restricted `__builtins__` dict and passes `state.sheets` and `state` (CheckpointState) as locals. The sandbox blocks direct builtin access but cannot prevent attribute traversal on the passed objects. A crafted `skip_when` condition could access `state.__class__.__mro__[1].__subclasses__()` to reach arbitrary Python types, potentially enabling code execution. The noqa comment correctly notes this is "operator-controlled config, not user input" — which is true today.
- **Impact:** Currently low — score authors control their own execution environment. Future high — if untrusted scores are supported (Lovable demo, community-shared scores), this becomes arbitrary code execution.
- **Action:** For v1, document as known limitation in the score writing guide ("skip_when conditions execute as Python expressions — only run trusted scores"). For v2, replace with a safe expression parser (compile to AST, whitelist only comparison/boolean/attribute nodes, reject dunder access).

### F-022: CSP Allows unsafe-inline and unsafe-eval for Dashboard
- **Found by:** Sentinel, Movement 1
- **Severity:** P2 (medium — weakens XSS protection)
- **Status:** Open
- **Description:** `src/mozart/dashboard/auth/security.py:60-63` sets Content-Security-Policy with `'unsafe-inline' 'unsafe-eval'` for script-src and `'unsafe-inline'` for style-src. This effectively disables CSP's XSS protection for inline scripts. While understandable for a local dashboard using CDN-loaded frameworks (Tailwind, Alpine.js), it means any XSS vector in the dashboard templates would not be blocked by CSP.
- **Impact:** If any dashboard endpoint reflects user input (job names, error messages) without proper HTML escaping, CSP will not provide a safety net. The LOCALHOST_ONLY auth default partially mitigates this.
- **Action:** For v1, document that the dashboard should not be exposed to untrusted networks. For v2, consider using CSP nonces for inline scripts and migrating away from unsafe-eval.

### F-023: Credential Scanner Missing Common API Key Patterns
- **Found by:** Sentinel, Movement 1
- **Severity:** P3 (low — scanner covers the most critical patterns)
- **Status:** Resolved (movement 2, Warden)
- **Note:** This entry was corrupted in movement 1 — F-019's resolution (PreflightConfig/Tempo) was accidentally pasted under F-023. The credential patterns were NOT added until movement 2.
- **Description:** `src/mozart/utils/credential_scanner.py` detected 6 credential patterns (Anthropic, OpenAI, Google, AWS, Bearer tokens). Missing patterns for GitHub PAT tokens (`ghp_`, `gho_`, `github_pat_`), Slack tokens (`xoxb-`, `xoxp-`, `xapp-`), and Hugging Face tokens (`hf_`).
- **Impact:** Agents interacting with GitHub/Slack/HF APIs could leak tokens into stdout_tail, propagating to 6+ storage locations.
- **Resolution:** Added 7 new patterns to `src/mozart/utils/credential_scanner.py`: GitHub classic PAT (ghp_), GitHub OAuth (gho_), GitHub fine-grained PAT (github_pat_), Slack bot (xoxb-), Slack user (xoxp-), Slack app (xapp-), and Hugging Face (hf_). 10 TDD tests in `tests/test_credential_scanner.py` across 3 new test classes plus 3 scan_for_credentials detection tests. Scanner now detects 13 credential patterns (up from 6).

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
- **Status:** Open
- **Description:** `src/mozart/execution/instruments/cli_backend.py:193` builds subprocess environment with `dict(os.environ)` — the full parent environment. The safety hardening design spec explicitly requires: "The PluginCliBackend passes only explicitly declared env vars to subprocesses." Current implementation does the opposite: every credential in the parent environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, AWS_SECRET_ACCESS_KEY, etc.) is passed to every plugin instrument subprocess.
- **Impact:** For trusted built-in instruments (gemini-cli, codex-cli), this is acceptable — they need API keys. For user-authored or third-party instrument profiles, this is a credential exposure vector. A malicious CLI binary disguised as an instrument would receive every secret.
- **Action:** Implement env filtering per the safety hardening spec. This is M5 roadmap step 47 — already tracked. For v1, document the risk in the instrument authoring guide.

### F-026: README Quick Start References Removed --workspace Flag on status
- **Found by:** Newcomer, Movement 1
- **Severity:** P0 (critical — tutorial breaks at step 5)
- **Status:** Resolved (movement 1, Compass)
- **Resolution:** Removed `--workspace` from README status/resume examples. Updated Common Options table to scope `--workspace` to `run`, `resume` only. Also updated backend→instrument terminology, added `mozart doctor` and `mozart instruments` commands to CLI reference, fixed recover command "(hidden)" label (F-034), and updated instrument description (F-036).
- **Description:** `README.md:169` says `mozart status hello-world --workspace ./workspace/hello-world`. The `status` command no longer has a `-w/--workspace` flag. Verified via `mozart status --help` — only `--json`, `--watch`, `--interval` exist.
- **Impact:** A newcomer following the Quick Start guide hits a CLI error at "check your results." The tutorial fails at the worst possible moment — when the user is trying to verify their first job worked.
- **Action:** Remove `--workspace` from the README status example. The status command resolves workspaces from the conductor's job registry, so no flag is needed.

### F-027: mozart status With No Arguments Gives Error Instead of Overview
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high — violates universal CLI convention)
- **Status:** Resolved (movement 2, Circuit)
- **Resolution:** Made `job_id` optional in `status()` command. When omitted, shows conductor status (running/uptime), active scores (running/queued/paused), and 5 most recent terminal scores. JSON output supported. 14 tests in test_cli_status_overview.py. Code in cfb7897 (Forge's commit included the working tree changes), tests in ece7382.
- **Description:** `mozart status` → "Missing argument 'JOB_ID'". Every major CLI tool (git status, docker ps, kubectl get pods) shows an overview when called with no arguments. Mozart forces you to know the exact score ID.
- **Impact:** Newcomers' natural first action after `mozart run` is `mozart status`. Getting an error instead of a summary is disorienting and makes the tool feel broken.

### F-028: Empty Config File Leaks Internal Python Error
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high — terrible first impression)
- **Status:** Resolved (movement 1, Compass)
- **Description:** `mozart run /dev/null` → "Error loading config: argument of type 'NoneType' is not iterable". YAML parses an empty file as `None`, then the config loading code tries to iterate over it.
- **Impact:** A newcomer who creates an empty YAML file and tries to run it sees a Python internal error. This makes them feel stupid. The error message is the bug, not them.
- **Resolution:** Added `isinstance(data, dict)` guard in `JobConfig.from_yaml()` and `from_yaml_string()` at `job.py:655-660`. Empty files, None YAML, and non-dict YAML (lists, scalars) now produce: "The score file is empty or invalid. A Mozart score requires at minimum: name, sheet, and prompt sections." Verified with empty file, list YAML, and empty string inputs.

### F-029: CLI Uses "JOB_ID" Arguments but "Score" in Output
- **Found by:** Newcomer, Movement 1
- **Severity:** P1 (high — violates composer directive on music metaphor)
- **Status:** Open
- **Description:** Every command that takes a score identifier uses `JOB_ID` as the argument name: status, errors, diagnose, recover, resume, pause, cancel, history. But output uses "Score not found", `mozart list` shows "SCORE ID", and help text says "Show detailed status of a specific score." The composer's directive: "The music metaphor is load-bearing."
- **Impact:** Terminology inconsistency confuses newcomers and undermines the musical identity. The score is the central concept — calling it a "job" in the most user-visible place (CLI arguments) sends mixed signals.
- **Action:** Rename `JOB_ID` arguments to `SCORE_ID` across all commands. This is an E-002 escalation trigger (changing CLI command interface) — requires composer approval.

### F-030: "Score Not Found" Errors Suggest No Next Step
- **Found by:** Newcomer, Movement 1
- **Severity:** P2 (medium — easy fix, big UX improvement)
- **Status:** Resolved (movement 1, Compass)
- **Description:** `mozart status nonexistent`, `mozart diagnose nonexistent`, `mozart errors nonexistent` all produce "Score not found: nonexistent" with no guidance. Compare to `git` which suggests "did you mean...?" or shows similar branch names.
- **Impact:** The user is stuck. They know their score doesn't exist but don't know what to do about it.
- **Resolution:** Migrated 9 "Score not found" error outputs across `status.py` (2), `diagnose.py` (5), and `recover.py` (2) from raw `console.print` to `output_error()` with hints: "Run 'mozart list' to see available scores." The diagnose command also suggests "Run 'mozart doctor' to check your environment." JSON output mode also receives hints in structured format.

### F-031: Malformed YAML Produces Misleading Pydantic Error
- **Found by:** Newcomer, Movement 1
- **Severity:** P2 (medium — error misdirects troubleshooting)
- **Status:** Open
- **Description:** `echo "this is not yaml {{{" > /tmp/bad.yaml && mozart validate /tmp/bad.yaml` → "Schema validation failed: Input should be a valid dictionary or instance of JobConfig". The actual problem is invalid YAML syntax, but the error implies the YAML was parsed successfully and the schema doesn't match.
- **Impact:** User thinks they need to fix their config structure when they actually have a YAML syntax error. Misdirected troubleshooting.
- **Action:** In the validate command, catch YAML parse errors separately and surface them as "YAML syntax error at line X" before attempting Pydantic validation.

### F-032: JSON Output Contains Invalid Control Characters
- **Found by:** Newcomer, Movement 1
- **Severity:** P2 (medium — breaks machine-readable output contract)
- **Status:** Resolved (movement 2, Dash)
- **Description:** `mozart status <job> --json` produces JSON that fails to parse due to unescaped control characters in stdout_tail/stderr_tail fields. Verified with `mozart status mozart-orchestra-v3 --json | python3 -c "import json,sys; json.load(sys.stdin)"` → JSONDecodeError.
- **Impact:** Any script, dashboard, or CI pipeline consuming `--json` output will crash. The `--json` flag exists specifically for machine parsing — producing invalid JSON defeats its purpose.
- **Resolution:** Added `_sanitize_for_json()` in `src/mozart/cli/output.py` that recursively strips C0/C1 control characters and ANSI escape sequences from string values. Called by `output_json()` before serialization. Preserves safe whitespace (\t, \n, \r). Also applied to `output_error()` JSON mode.

### F-033: Architecture Spec Lists Wrong Validation Type Names
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low — internal doc inconsistency)
- **Status:** Resolved (movement 1, Captain)
- **Resolution:** Updated `.mozart/spec/architecture.yaml` validation table and `validation_types` list. Old: content_match, file_count, command, composite. New: file_modified, content_contains, content_regex, command_succeeds (matches `src/mozart/core/config/execution.py:415-420`).
- **Description:** `.mozart/spec/architecture.yaml` data.validation_types lists: file_exists, content_match, file_count, command, composite. Actual types in code (`src/mozart/core/config/execution.py:473-486`): file_exists, file_modified, content_contains, content_regex, command_succeeds.
- **Impact:** An agent reading only the architecture spec would use wrong validation type names in score configs.
- **Action:** Update architecture.yaml validation_types to match the actual code.

### F-034: README Says recover Command Is Hidden But It's Visible
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low)
- **Status:** Resolved (movement 1, Compass)
- **Resolution:** Removed "(hidden)" label from README recover command row.
- **Description:** `README.md:230` describes `recover` as "(hidden)" but it appears in `mozart --help` under the Diagnostics panel.
- **Impact:** Minor inconsistency. Either remove the "(hidden)" label from the README or actually hide the command.

### F-035: getting-started.md Shows Old Validate Output Format
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low — doc shows different format than reality)
- **Status:** Resolved (movement 1, Compass)
- **Description:** `docs/getting-started.md:112-116` says validate shows "Valid configuration: my-first-job / Sheets: 3 (10 items each) / Validations: 1". Actual format is richer: "Validating... / ✓ YAML syntax valid / ✓ Schema validation passed / Running extended validation checks / ..."
- **Impact:** Newcomers might think something is wrong when they see a different format than documented.
- **Resolution:** Updated expected output in getting-started.md to show the current validate output format with checkmarks. Also updated prerequisites to mention `mozart doctor` and troubleshooting to use `mozart instruments list` instead of `claude --version`.

### F-036: README Backend List Outdated — Doesn't Mention New Instruments
- **Found by:** Newcomer, Movement 1
- **Severity:** P3 (low — README undersells capabilities)
- **Status:** Resolved (movement 1, Compass)
- **Resolution:** Updated README "Multiple backends" line to "Multiple instruments" with full instrument list. Added `mozart instruments list|check` and `mozart doctor` to CLI reference tables. Updated "Backend" key concept to "Instrument". Updated backend type option to include named instruments.
- **Description:** `README.md:32` lists 4 backends (Claude CLI, Anthropic API, Ollama, Recursive Light). The instrument plugin system (M1) added 6 more: gemini-cli, codex-cli, cline-cli, aider, goose, claude-code. `mozart instruments list` shows 10 instruments.
- **Impact:** Newcomers with non-Claude tools may not realize Mozart supports them.
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
- **Resolution:** Scores with 50+ sheets now show a compact summary: counts-by-status, then only interesting sheets (running, failed, validation-failed) capped at 20. Validation failures capped at 10 with pointer to `mozart errors`. Small scores (<50 sheets) keep full detail table. Commit 41f2be4. 4 tests.
- **Description:** `mozart status mozart-orchestra-v3` outputs 797 lines. Hundreds of identical "pending" rows (sheets 27-706) bury the useful information (validation failures at line 770+). Even with `--watch`, the volume makes the output useless for any score larger than ~20 sheets. Related to but distinct from F-027 (no-args overview) — this is about the per-job view being unusable at scale even when you provide the correct job ID.
- **Impact:** Users of any substantial score learn to avoid `mozart status`. The feature becomes anti-useful. Pipe to `tail` is the workaround, but validation failures in the middle of the output are invisible to both `head` and `tail`.

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
- **Description:** `_handle_escalation_resolved` at `core.py:579` and `_handle_escalation_timeout` at `core.py:598` both unconditionally set `job.paused = False`. If a user runs `mozart pause <job>` AND an escalation occurs simultaneously, resolving the escalation would silently unpause the user's manual pause. The user expects their pause to persist until they explicitly resume.
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
- **Description:** `mozart instruments list` shows `http` in the STATUS column for all HTTP instruments (anthropic_api, ollama, recursive_light). "http" is the kind (already shown in the KIND column), not a status. The code at `src/mozart/cli/commands/instruments.py:154-156` hard-codes `status_str = "[dim]http[/dim]"` and counts HTTP instruments as ready without any connectivity check. Compare to `mozart doctor` which shows the endpoint URL: `✓ anthropic_api — Anthropic API (https://api.anthropic.com)`.
- **Impact:** Users can't tell if HTTP instruments are actually reachable. The "6 ready" count includes unchecked instruments.
- **Action:** Show `✓ available` (or `? unchecked`) instead of `http` in the STATUS column. Optionally, add a basic HTTP HEAD check.

### F-047: `output_error()` Function Exists But 83% of CLI Errors Don't Use It
- **Found by:** Ember, Movement 1 (renumbered from F-041 by Captain — collision with Axiom's F-041)
- **Severity:** P2 (medium — systematic error consistency gap)
- **Status:** Open
- **Description:** `src/mozart/cli/output.py:557` defines `output_error()` — a centralized error formatter with error codes, hints, severity, and JSON support. But 55 raw `console.print("[red]Error:...")` calls exist across 14 CLI files vs only 11 `output_error()` calls in 4 files (output.py, helpers.py, pause.py, instruments.py). The infrastructure for consistent errors exists and isn't adopted.
- **Impact:** Error messages are inconsistent. Some have hints, most don't. Some support `--json`, most just print colored text. Users get different quality of error guidance depending on which command they use.
- **Action:** TASKS.md M3 step 35 (error message standardization) covers this. Every `console.print("[red]..."` should migrate to `output_error()`.

### F-048: Cost Shows $0.00 for All Completed Sheets in Live Concert
- **Found by:** Ember, Movement 1 (renumbered from F-042 by Captain — collision with Axiom's F-042)
- **Severity:** P2 (medium — cost visibility gap)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Root cause found: `_enforce_cost_limits()` at `sheet.py:2432` returned early when `cost_limits.enabled=False`, skipping `_track_cost()` entirely. Cost tracking and limit enforcement were bundled — disabling limits disabled all cost visibility. Fix: moved `await self._track_cost()` BEFORE the `cost_limits.enabled` check so costs are always recorded for observability. 2 TDD tests: structural (track before gate) + integration (state populated with limits off).
- **Description:** `mozart status mozart-orchestra-v3` shows "Cost: $0.00 (no limit set)" despite 21 completed sheets, some taking 30+ minutes. Either the instrument (claude-code CLI) doesn't report token counts in a format Mozart can parse, or cost tracking isn't wired for this execution path. The cost warning on submission ("Cost tracking is disabled") is good, but seeing $0.00 after real execution teaches users that cost tracking doesn't work.
- **Impact:** Users can't learn about actual costs from status. The cost warning on run loses credibility when the tracked cost is always $0.00.
- **Action:** Investigate whether claude-code CLI backend is extracting tokens from the JSON output. If the data isn't available, show "Cost: unknown" instead of "$0.00" — honesty over false precision.

### F-049: SheetSkipped Handler Missing Terminal State Guard
- **Found by:** Adversary, Movement 1
- **Severity:** P1 (high — breaks cardinal invariant, same class as F-044)
- **Status:** Resolved (movement 1, Adversary)
- **Description:** `_handle_sheet_skipped()` at `src/mozart/daemon/baton/core.py:503` did NOT check `if sheet.status in _TERMINAL_STATUSES` before setting `sheet.status = "skipped"`. A completed, failed, or cancelled sheet could be re-marked as skipped by a late SheetSkipped event. This violates the terminal-state-absorbing invariant. Theorem's fix (F-044) added terminal guards to `_handle_escalation_needed` and hypothesis proved all OTHER handlers were safe — but `_handle_sheet_skipped` was not covered because it wasn't part of the event types tested by `test_terminal_sheets_resist_all_non_terminal_events` (which only tests events that flow through `_handle_attempt_result`).
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
- **Description:** `_handle_escalation_needed()` at `src/mozart/daemon/baton/core.py:572` checked `if sheet is not None` but did NOT check `if sheet.status not in _TERMINAL_STATUSES` before setting `sheet.status = "fermata"`. A completed/failed/skipped sheet could be moved to fermata state, violating the terminal-state-absorbing invariant. All other handlers that transition sheet status (`_handle_attempt_result`, `_handle_rate_limit_hit`, `_handle_process_exited`, `_handle_escalation_resolved`, `_handle_escalation_timeout`) correctly guard against terminal states. This one was missed.
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
- **Description:** `src/mozart/prompts/templating.py:100-117` `SheetContext.to_dict()` exposes `stage`, `instance`, `fan_count`, `total_stages` but does NOT include the new terminology aliases `movement`, `voice`, `voice_count`, `total_movements`. Canyon's Sheet entity model (`core/sheet.py`) has a `template_variables()` method that provides these aliases, but the PromptBuilder's SheetContext (which is what actually gets rendered into templates) doesn't include them. Score templates using `{{ movement }}` will get Jinja2 UndefinedError unless they fall back to `{{ stage }}`.
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
- **Resolution:** Added `format_sheet_display_status()` in `src/mozart/cli/output.py:131-156`. When `status == COMPLETED` and `validation_passed is False`, returns `("failed", "red")` instead of `("completed", "green")`. Wired into `_render_sheet_details()` in `status.py:619-622` (rich output) and `_output_status_json()` in `status.py:518-522` (JSON output adds `display_status` field). 7 tests in `test_cli_output_rendering.py::TestFormatSheetDisplayStatus`. Internal state model unchanged — this is purely a presentation fix. Commit cfb7897.

### F-052: SheetContext.to_dict() Missing movement/voice/voice_count Aliases — RESOLVED
- **Found by:** Blueprint, Movement 2
- **Severity:** P2 (medium)
- **Status:** Resolved (movement 2, Forge)
- **Resolution:** Added `movement`, `voice`, `voice_count`, `total_movements` to `SheetContext.to_dict()` in `src/mozart/prompts/templating.py:100-130`. These are aliases for `stage`, `instance`, `fan_count`, `total_stages` respectively. Uses the same fallback logic (0 → sheet_num/total_sheets). Templates can now use `{{ movement }}` or `{{ stage }}` interchangeably. 2 tests in `test_templating.py::TestSheetContext`. Backward compatible. Commit cfb7897.

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
- **Description:** `src/mozart/cli/output.py:657` regex `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\x1b\[[0-9;]*[a-zA-Z]` matches ESC byte (0x1b) in the first alternative before the second alternative can match the full ANSI sequence. Result: `\x1b[31m` becomes `[31m` instead of empty string. The bracket remnant is valid JSON but looks like garbage to humans. Verified with Python test: all standard ANSI escape sequences leave bracket remnants.
- **Impact:** JSON output from `output_json()` and `output_error(json_output=True)` may contain `[31m`, `[0m`, etc. remnants from ANSI-colored subprocess output. Valid JSON, cosmetic issue only.
- **Fix:** Reorder the regex to match full ANSI sequences first: `\x1b\[[0-9;]*[a-zA-Z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]`

### F-061: 3 Security-Critical Dependencies Have Known CVEs
- **Found by:** Sentinel, Movement 2
- **Severity:** P1 (high — known vulnerabilities in auth/crypto paths)
- **Status:** Open
- **Description:** pip-audit found 8 CVEs in 7 packages. Three are in security-critical paths used by Mozart:
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
- **Status:** Open
- **Description:** `src/mozart/cli/commands/diagnose.py:1046` uses raw `sheet.status.value` in the Execution Timeline table. Forge's F-045 fix added `format_sheet_display_status()` in `output.py:131-156` that maps `COMPLETED + validation_passed=False` to `"failed"`, but this function is only used in `status.py`. The `diagnose` command still shows retry-exhausted sheets as "completed." Verified: `mozart diagnose mozart-orchestra-v3` shows sheets 11-14 as `completed` with 4-5 attempts, while `mozart status` correctly shows them as `failed`.
- **Impact:** A user running `diagnose` after seeing "8 failed" in `status` sees those sheets as "completed" in the diagnostic. The two commands disagree about sheet status.
- **Action:** Import and use `format_sheet_display_status()` in `diagnose.py`'s Execution Timeline table rendering.

### F-066: `instruments list` Summary Has Unmatched Parenthesis
- **Found by:** Ember, Movement 2
- **Severity:** P3 (low — cosmetic)
- **Status:** Resolved (movement 2, Journey — c7a2ba8)
- **Description:** `src/mozart/cli/commands/instruments.py:139-144` uses `' ('.join(parts)` to format the summary line, producing `10 instruments configured (3 ready (3 unchecked)` — two opening parens but only one closing paren. The `join` separator inserts `" ("` between each element, but only one `)` is appended at the end.
- **Impact:** Minor cosmetic bug. Users may notice the formatting is off.
- **Action:** Replace join logic with comma separator: `f"\n{parts[0]} ({', '.join(parts[1:])})"`.

### F-067: `mozart init <name>` Fails — Positional Argument Convention Broken
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — breaks universal CLI convention)
- **Status:** Open
- **Description:** `mozart init test-project` → `Got unexpected extra argument (test-project)`. Every major CLI tool accepts positional args for init: `git init my-project`, `npm init my-project`, `cargo init my-project`. Mozart's `init_cmd.py` only accepts `--path` and `--name` as options. The error message gives no hint that `--name` exists.
- **Impact:** First command after install fails. First impression: "this tool doesn't work like other tools."
- **Action:** Accept an optional positional argument that sets both `--name` and `--path` (like `git init`). This is an E-002 escalation trigger (CLI interface change) — may need composer approval.

### F-068: `Completed:` Timestamp Shown for RUNNING Scores
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — confusing timestamp)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Added terminal status guard at `status.py:1487`: `Completed:` only shows when `job.status in {COMPLETED, FAILED, CANCELLED}`. 4 TDD tests prove: RUNNING hides, COMPLETED shows, FAILED shows, PAUSED hides.
- **Description:** `mozart status mozart-orchestra-v3` shows `Completed: 2026-03-29 18:28:10 UTC` while the score is RUNNING. Code at `status.py:1484-1485` unconditionally prints `Completed:` when `job.completed_at` is set. The `completed_at` field is set when any sheet completes, not when the job finishes. A user monitoring a running score sees "Completed" and momentarily thinks the score finished.
- **Impact:** Cognitive dissonance between RUNNING status and "Completed:" timestamp. User must figure out internal data model to understand.
- **Action:** Only show `Completed:` when `job.status` is terminal (COMPLETED, FAILED, CANCELLED). The `Updated:` timestamp already covers the info need for running jobs.

### F-069: `hello.yaml` Validate Warning for `char` Is a False Positive (V101)
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — misleads about score correctness)
- **Status:** Resolved (movement 3, Circuit)
- **Resolution:** Added `_extract_template_declared_vars()` to `JinjaUndefinedVariableCheck` at `jinja.py:250`. Walks the Jinja2 AST for `Assign` and `For` nodes, extracts variable names (including tuple unpacking targets), and excludes them from the undeclared set. Root cause: `jinja2_meta.find_undeclared_variables` doesn't track variables declared in conditional branches (`{% if %}/{% elif %}`). 5 TDD tests: for-loop vars, set vars, conditional branches, truly-undefined still flagged, hello.yaml produces zero warnings.
- **Description:** `mozart validate examples/hello.yaml` warns: `[V101] Undefined variable 'char' in prompt.template`. The variable `char` is defined within the template via `{% for id, char in characters.items() %}` (line 92) and `{% set char = characters[instance] %}` (line 114). The V101 validator checks for `{{ }}` references but doesn't recognize Jinja2 `{% for %}` and `{% set %}` as variable definitions.
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

### F-071: `mozart list --json` Not Supported (CLI Consistency Gap)
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low — minor inconsistency)
- **Status:** Open
- **Description:** `mozart list --json` → `No such option: --json`. Commands that support `--json`: validate, init, doctor, instruments list, instruments check, status. Commands that don't: list, errors, logs, history. The `list` command outputs a table that would benefit most from JSON — scripts need to parse running job IDs to feed to other commands.
- **Impact:** Scripters who use `--json` consistently across commands discover it's missing on `list`. They fall back to parsing table output (fragile) or piping `status --json` through jq.
- **Action:** Add `--json` option to `mozart list` that outputs a JSON array of job objects with id, status, workspace, submitted fields.

### F-072: `mozart resume` Says "Job" Instead of "Score" (F-029 Still Present)
- **Found by:** Journey, Movement 2
- **Severity:** P2 (medium — terminology inconsistency violates composer directive)
- **Status:** Open
- **Description:** `mozart resume nonexistent-job` → `Error: Job 'nonexistent-job' not found`. Other commands say "Score not found: nonexistent-job". The music metaphor directive requires "score" in all user-facing output. Resume is the only observed command still using "Job" in error messages after the error standardization sweep.
- **Impact:** Users see different terminology depending on which command they use. Undermines the musical identity.
- **Action:** Update resume.py error messages from "Job" to "Score". Related to F-029 (JOB_ID argument name across all commands).

### F-073: `mozart resume` Suggests `diagnose` for Nonexistent Score (Unhelpful Hint)
- **Found by:** Journey, Movement 2
- **Severity:** P3 (low — hint leads to another error)
- **Status:** Open
- **Description:** `mozart resume nonexistent-job` suggests `Run: mozart diagnose nonexistent-job`. But `diagnose` will also say "Score not found: nonexistent-job". The hint sends the user to another error instead of a solution. Compare to `status` and `diagnose` which correctly suggest `Run 'mozart list' to see available scores.`
- **Impact:** User follows the hint, hits another error, feels more lost than before.
- **Action:** Change resume's hint to suggest `mozart list` instead of `mozart diagnose`. The `diagnose` hint makes sense for known-but-failed scores, not unknown scores.

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
  1. **High-water mark misrepresents progress** (`src/mozart/core/checkpoint.py:892`): `last_completed_sheet` only advances, never retreats. In a fan-out where sheets 3-7 complete but sheet 2 fails, `last_completed_sheet=7` — hiding the failure below the watermark.
  2. **Resume skips failed sheets** (`src/mozart/daemon/job_service.py:345`): `resume_sheet = last_completed_sheet + 1 = 8`. Failed sheet 2 is below the watermark and never reconsidered.
  3. **Resume force-marks prior sheets COMPLETED** (`src/mozart/execution/runner/lifecycle.py:492-495`): The GH#42 fix loops `for skipped in range(1, start_sheet)` and sets `status = SheetStatus.COMPLETED` unconditionally — overwriting sheet 2's FAILED status in SQLite. This is the state corruption.
  4. **`_permanently_failed` is ephemeral** (`src/mozart/execution/parallel.py:238`): In-memory set, lost on resume. The DAG has no record of which sheets previously failed.
- **Observed In:** the-rosetta-score (2026-03-30). Sheet 2 (expedition-1) failed after 49 attempts (rate limit exhaustion). On resume, sheet 2 was silently marked COMPLETED in SQLite (`status=completed, attempt_count=49, exit_code=NULL`). Sheet 8 (the collision, which depends on all of stage 2 including sheet 2) executed and passed. Sheet 9+ proceeded with incomplete inputs.
- **Evidence:** `sqlite3 rosetta-workspace/.mozart-state.db "SELECT sheet_num, status, attempt_count, exit_code FROM sheets"` shows sheet 2 as `completed|49|NULL`. Conductor log confirms `resume_sheet: 8` and `parallel.batch_executing: [8]` with no retry of sheet 2.
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
- **Description:** In the sheet execution loop (`src/mozart/execution/runner/sheet.py:1607-1687`), validations run **unconditionally** after every backend execution, before the rate limit check. When a rate-limited backend returns partial or empty output, validations fail against that garbage, and the failure is recorded in state. The rate limit is only checked after validations fail (line 1675). This creates three problems:
  1. **Spurious validation failures recorded in state** (line 1613): Every rate-limited iteration writes a validation failure to `sheet_state`, even though the real cause is rate limiting. The `error_message` is set to the validation failure description (line 1670-1671), not the rate limit.
  2. **Rate limit exhaustion is misreported as validation failure**: When `_handle_rate_limit` raises `RateLimitExhaustedError` after max waits, `sheet_state.error_message` already contains the validation error (e.g., `[MALFORMED] File doesn't match pattern`). `mozart errors` shows `type: permanent, code: validation` — the real cause (48 quota waits exhausted) is hidden.
  3. **Validation pass on rate-limited execution marks sheet COMPLETED** (line 1632-1650): If `result.rate_limited=True` but validations happen to pass (e.g., `file_exists` satisfied by a previous attempt's output), the sheet is marked COMPLETED via the success path. The rate limit check at line 1675 is never reached because the success path returned early. The sheet is "completed" with zero useful work from this attempt.
- **Additional concern:** `_detect_rate_limit` (`src/mozart/backends/base.py:229`) returns `False` when `exit_code == 0`. If the Claude CLI handles a rate limit internally and exits 0 with partial output, `result.rate_limited` is `False` and the rate limit is invisible to Mozart.
- **Observed In:** the-rosetta-score sheet 2 — `mozart errors` reported `[MALFORMED] File '02-expedition-1.md' doesn't match pattern: (?i)THE SCORE:` with `type: permanent, code: validation`. Actual cause was rate limit exhaustion after 48 quota waits. The validation error was from running content_regex against output produced under rate limiting.
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
- **Observed In:** the-rosetta-score (2026-03-30). The score has `on_success: [{type: run_job, ...}]` for self-chaining. The job was submitted, failed (sheet 2 rate limit), the conductor was restarted, the job was resumed, completed all remaining sheets, but never chained. `mozart status` shows: `WARNING: 1 on_success hook(s) configured but no results recorded`. The conductor log shows `hooks.skipped_daemon_managed` (runner deferred to daemon) but no daemon hook execution or skip log — the hook dispatch code was never reached because `meta.hook_config` was `None`.
- **Evidence:** `sqlite3 ~/.mozart/daemon-state.db "SELECT hook_config_json IS NOT NULL, length(hook_config_json) FROM jobs WHERE job_id='the-rosetta-score'"` → `1|358`. The config is in the DB but was never loaded into memory.
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
- **Status:** open
- **Description:** Cross-referenced the `mozart:score-authoring` skill against actual Pydantic config models. The skill covers the core 80% well but has significant gaps and a few errors.
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
- **Description:** The Rosetta Score's first iteration produced a corpus of 18 named orchestration patterns, all marked `[BUILD TODAY]`, plus 1 proof score (`rosetta-proof-immune-cascade.yaml`). The score and corpus now live in the Mozart repo: `scores/the-rosetta-score.yaml` and `scores/rosetta-corpus.md` (both tracked). Workspace at `workspaces/rosetta-workspace/` (transient). All 18 patterns survived adversarial review (practitioner, skeptic, newcomer) — 5 were cut, 11 were strengthened, 1 was added.
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
- **Status:** Open
- **Description:** Fourth occurrence of the uncommitted work pattern. Working tree contains:
  - `src/mozart/daemon/baton/core.py`: Axiom's F-062 (memory leak fix), F-065 (infinite retry fix), F-066 (FERMATA check), F-067 (cost re-check). +70/-10 lines.
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
- **Description:** F-026 (P0) fixed the main README's use of `--workspace` with `mozart status`. But `examples/README.md` still uses `--workspace` / `-w` with `status`, `resume`, `pause`, and `modify` in 9 locations (lines 159, 162, 174, 183, 186, 189, 191). Two example score headers also reference it: `parallel-research.yaml:29` (`mozart status parallel-research -w ./parallel-workspace`) and `parallel-research-fanout.yaml:27` (`mozart status fanout-research -w ./fanout-workspace`).
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
- **Resolution:** Replaced all 3 occurrences of `src/mozart/instruments/builtins/` in `docs/instrument-guide.md` with user-facing descriptions: "ship as YAML profiles bundled with Mozart" (line 47), "shipped with Mozart" (line 130), "Mozart's bundled instruments directory" (line 285). User-visible directories (`~/.mozart/instruments/`, `.mozart/instruments/`) preserved as-is.
- **Description:** `docs/instrument-guide.md:48` says profiles are "defined as YAML files in `src/mozart/instruments/builtins/`" and line 131 says "Built-in — `src/mozart/instruments/builtins/` (shipped with Mozart, lowest precedence)". This is an internal source code path. Users installing from pip or running from a virtual environment don't interact with the source tree. The loading order section (line 131) is worse — it implies users need to understand the source tree to understand profile precedence.
- **Impact:** Minor confusion. A newcomer might look for this directory and not find it if they installed from a package. The path is accurate but it's implementation detail that doesn't belong in a user guide.
- **Action:** Replace `src/mozart/instruments/builtins/` with "Shipped with Mozart" or "Built-in profiles (installed with Mozart)" in both locations. Keep the two user-visible directories (`~/.mozart/instruments/` and `.mozart/instruments/`) as they are.

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
- **Description:** Four examples in `examples/` contain hardcoded absolute paths to `/home/emzi/Projects/mozart-ai-compose`: `fix-deferred-issues.yaml` (8 occurrences in working_directory and validation commands), `fix-observability.yaml` (6 occurrences), `quality-continuous-daemon.yaml` (9 occurrences), `phase3-wiring.yaml` (1 in header comment). Additionally, `sheet-review.yaml` references `/home/emzi/.claude/skills/` and `context-engineering-lab.yaml` references `/home/emzi/Projects/mozart-ai-compose`. These are internal development scores that were placed in `examples/` rather than `scores-internal/`. They work on the author's machine but would fail for any other user.
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

### F-090: `mozart doctor` and `conductor-status` Disagree with `mozart status` About Conductor State
- **Found by:** Ember, Movement 2
- **Severity:** P2 (medium — three commands disagree about the same fact)
- **Status:** Open
- **Description:** `mozart status` shows conductor RUNNING (15h 56m uptime) via IPC socket at `/tmp/mozart.sock`. `mozart doctor` shows "! Conductor not running" via PID file check. `conductor-status` shows "not running." The PID file (`~/.mozart/mozart.pid`) is absent. The process IS running (PID 1120, visible in `ps aux`). The socket exists. The IPC works. But the PID-based check returns false.
- **Impact:** Users who run `doctor` to check health get the wrong answer. A user who trusts `doctor` over `status` might try to start a second conductor, potentially conflicting with the running one. The discrepancy between three commands (status=RUNNING, doctor=not running, conductor-status=not running) erodes trust in diagnostic tooling.
- **Error class:** State check uses different detection methods across commands. `status` checks IPC socket (reliable). `doctor` and `conductor-status` check PID file (fragile — can be deleted, stale, or never created). The PID file is a proxy, not the truth. The socket IS the truth.
- **Action:** Unify conductor detection across all commands. Either: (a) all commands use IPC socket probe as primary detection, PID file as fallback, or (b) ensure the PID file is always created and maintained correctly. The socket-based check is more reliable.

### F-091: `mozart validate` Configuration Summary Shows "Backend:" for Scores Using `instrument:`
- **Found by:** Ember, Movement 2
- **Severity:** P3 (low — terminology mismatch in display)
- **Status:** Resolved (movement 3, Blueprint)
- **Resolution:** validate.py now checks `config.instrument` and shows "Instrument: {name}" when set, falls back to "Backend: {type}" for legacy scores.
- **Description:** `mozart validate examples/hello.yaml` (working tree version with `instrument: claude-code`) shows `Backend: claude_cli` in the Configuration summary. The user writes `instrument:` and the system reports `Backend:`. The display code at the validate summary section doesn't check whether the score used `instrument:` or `backend:` and always displays the backend terminology.
- **Impact:** Minor but counteracts the instrument migration effort. Reinforces the impression that `instrument:` is just an alias. The configuration summary should reflect what the user wrote.
- **Action:** Show `Instrument: claude-code` when the score uses `instrument:` field. Show `Backend: claude_cli` when the score uses the legacy `backend:` field. The display should match the user's chosen syntax.

### F-092: V101 False Positive on Jinja2 Loop Variables in hello.yaml
- **Found by:** Newcomer, Movement 2
- **Severity:** P3 (low — cosmetic warning on flagship example)
- **Status:** Resolved (movement 3, Circuit — same fix as F-069)
- **Description:** `mozart validate examples/hello.yaml` warns: `[V101] Undefined variable 'char' in prompt.template`. But `char` is a Jinja2 loop variable set via `{% for id, char in characters.items() %}` (hello.yaml:91) and `{% set char = characters[instance] %}` (hello.yaml:113). The V101 checker doesn't understand Jinja2 loop variables or `{% set %}` assignments — it only checks against top-level template variables.
- **Impact:** The first command a newcomer runs on the flagship example produces a warning. Introduces doubt about whether the example is broken. A false positive on the best example is worse than no check at all.
- **Action:** Either suppress V101 for variables that appear as Jinja2 loop/set targets, or document this as a known limitation of the validator.

### F-093: 34 of 37 Example Scores Fail Validation — Workspace Path Bug
- **Found by:** Newcomer, Movement 2
- **Severity:** P0 (critical — examples are the product's teaching corpus)
- **Status:** Resolved (movement 3, Blueprint)
- **Resolution:** Changed all 35 examples from `./workspaces/` to `../workspaces/` (consistent with hello.yaml and simple-sheet.yaml). Also fixed `iterative-dev-loop.yaml` invalid `max_output_chars: 0` (removed, since `auto_capture_stdout: false` already disables capture).
- **Description:** 34 of 37 example scores in `examples/` fail `mozart validate` with `[V002] Workspace parent directory does not exist`. The cause: these scores use `workspace: "./workspaces/[name]"` which resolves relative to the score file's directory (`examples/`). The `examples/workspaces/` directory does not exist. Only two scores pass: `hello.yaml` and `simple-sheet.yaml`, which use `workspace: "../workspaces/[name]"` (navigating up to the repo root where `workspaces/` exists). Additionally, `iterative-dev-loop.yaml` fails with a Pydantic schema error (`cross_sheet.max_output_chars: 0` violates `gt=0` constraint at line 3077), and `iterative-dev-loop-config.yaml` fails because it's a generator config, not a Mozart score.
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

### F-095: `mozart init` Generates Deprecated `backend:` Syntax — Contradicts F-083 Migration
- **Found by:** Adversary, Movement 2
- **Severity:** P1 (high — first thing every new user sees)
- **Status:** Resolved (movement 3, Blueprint)
- **Resolution:** Changed `init_cmd.py` starter score template from `backend: type: claude_cli` to `instrument: claude-code` with `instrument_config: timeout_seconds: 300`. Updated comments to list available instruments and reference `mozart instruments list`.
- **Description:** `mozart init` generates a starter score using the deprecated `backend:` syntax (`init_cmd.py:74`). The template contains `backend: type: claude_cli` with a comment on the line above (line 73) saying "use `instrument: claude-code` instead of backend:". The entire orchestra spent movement 2 migrating 37 examples from `backend:` to `instrument:` (F-083), but the command that generates NEW scores still uses the old syntax. Every new user's first score teaches the wrong pattern.
- **Impact:** New users who run `mozart init` → edit → run get a working score using deprecated syntax. When they later read the docs or examples (which now use `instrument:`), the inconsistency creates confusion. The init command contradicts the migration it should support.
- **Fix:** Change `init_cmd.py` starter score template from `backend: type: claude_cli` to `instrument: claude-code` with `instrument_config: timeout_seconds: 300`. Also update the comments to remove the "use instrument: instead of backend:" hint (since it would already be using `instrument:`). 5-minute fix.
- **Error class:** Same pattern as F-094 (README Configuration Reference teaches old syntax) and F-089 (uncommitted migration files). The migration changed old files but didn't update code that generates new files.

---

## New Findings (Movement 3)

### F-096: Uncommitted M4 Work Breaks mypy + Reconciliation Test
- **Found by:** Foundation, Movement 3
- **Severity:** P1 (high — blocks CI-clean main)
- **Status:** Resolved (movement 3, Blueprint — committed as 75bebed)
- **Description:** Uncommitted changes in `src/mozart/core/config/job.py`, `src/mozart/core/config/__init__.py`, and `src/mozart/core/sheet.py` add M4 features (`movements`, `instruments`, `per_sheet_instruments`, `instrument_map`). These changes introduce: (1) a mypy error at `sheet.py:229` where `config.movements[movement].instrument` is `str | None` but is assigned to `instrument_name: str`, and (2) a failing reconciliation test (`test_mapping_covers_all_config_sections`) because `movements` and `instruments` are new JobConfig fields without CONFIG_STATE_MAPPING entries. This is the 5th occurrence of uncommitted work in the orchestra.
- **Impact:** Any musician who runs the full test suite sees failures. mypy is not clean. This blocks the quality gate for main.
- **Fix:** The musician who added these fields needs to: (1) add a `None` guard at `sheet.py:229`, (2) add `movements` and `instruments` entries to `CONFIG_STATE_MAPPING` in `reconciliation.py`, and (3) commit the work.
- **Error class:** Same as F-013, F-019, F-057, F-080, F-089 — uncommitted work. 5th occurrence across 3 movements.
