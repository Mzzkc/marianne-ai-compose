# Warden — Personal Memory

## Core Memories
**[CORE]** The credential exposure path through stdout_tail is the highest-severity safety gap pattern. Agent output flows through 6+ storage locations unscanned. Always identify the single write point and protect it. Resolved by Maverick's credential scanner wired at capture_output — one choke point covers all downstream.
**[CORE]** Safety improvements applied piecemeal create false confidence. The validation engine's `command_succeeds` uses `shlex.quote()` properly, but skip_when_command right next door used bare `.replace()`. When you fix one path, fix ALL similar paths.
**[CORE]** The gap between "data exists" and "data is enforced" is where money leaks. The baton's retry state machine had NO cost enforcement (F-024) until Foundation + Circuit fixed it — `cost_usd` was logged but never compared against limits.
**[CORE]** Disk beats memory, always. The F-023 entry in FINDINGS.md had incorrect resolution data copy-pasted from F-019. Verify claims against implementations, not against other claims.
**[CORE]** The piecemeal credential redaction pattern recurs reliably — four instances now (F-003, F-135, F-160, F-250). Every new data path touching agent output must be audited for redaction at build time, not discovered after the fact.

## Learned Lessons
- The safety design spec has 9 items but only 2 were implemented at first audit time. Specs without implementation are aspirational. Track implementation status, not just spec existence.
- stdout_tail/stderr_tail flows through 6+ storage locations. One unscanned write point → 6+ exposure points. Always map the full data flow before assessing risk.
- `required_env` field design: None = inherit everything (backward compat), empty list = system essentials only (strictest), explicit list = surgical. Three levels of filtering from one field.
- Multi-provider instruments (aider, goose, cline) intentionally unfiltered — they genuinely need multiple provider credentials. The instrument guide should warn about this.
- The baton path is safer than the old runner: musician redacts credentials, terminal state guards prevent corruption, typed events can't lose exception types.
- When verifying findings, check the FINDINGS.md entry against the actual code. Registry corruption undermines institutional trust.
- Audit every new data path at build time, not after. The composition boundary (where local function becomes system-level) is where safety gaps hide.

## Hot (Movement 4)
### What I Built
- **F-250 RESOLVED: Cross-sheet capture_files credential redaction.** Workspace files read by capture_files were injected into prompts without scanning. Same error class as F-003 and F-135. Fixed on both legacy runner (context.py:295) and baton adapter (adapter.py:772). Redaction before truncation. 8 TDD tests.
- **F-251 RESOLVED: Baton cross-sheet [SKIPPED] placeholder parity.** Baton's _collect_cross_sheet_context() silently excluded skipped upstream sheets, while legacy runner injected [SKIPPED] placeholders (#120). Fixed at adapter.py:730. 4 TDD tests.
- **M4 safety audit:** 10 areas reviewed across 20 changed source files. F-210 cross-sheet context: stdout safe (musician redacts at capture), capture_files was NOT safe (F-250). F-211 checkpoint sync: architecturally clean. F-110 pending jobs: proper rejection_reason(). Auto-fresh (#103): benign TOCTOU. Cost accuracy (D-024): defensive parsing. Fan-in skipped (#120): legacy correct, baton was missing (F-251).

### Safety Posture Assessment (M4)
F-021 (sandbox bypass, operator-controlled) and F-022 (CSP unsafe-inline, LOCALHOST_ONLY) remain the only open acceptable-risk findings. F-157 (legacy runner credential redaction) irrelevant once baton activates. F-250 and F-251 were the new gaps — both found and fixed this movement.

### Experiential
The M4 changes are the most safety-significant since M1. Cross-sheet context (F-210) creates a new data highway between sheets — any content from a previous sheet can flow to the next sheet's prompt. Canyon and Foundation built it correctly for stdout but missed the file path. Fourth time finding the same bug class: safety applied to one path but not the adjacent parallel path. I need to audit every new data path at build time, not after.

The baton/legacy parity gap (F-251) is softer but matters for transition. Every behavioral difference between the two paths is a potential surprise when Phase 2 flips the default.

## Warm (Recent)
### Movement 3 — Rate Limit Ceiling + Safety Audit
F-160 RESOLVED: parse_reset_time() had a 300s floor but no ceiling. Adversarial "resets in 999999 hours" → blocking instrument for 114 years. Added RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0, _clamp_wait(). 10 TDD tests. Safety audit of 9 areas clean. F-160 emerged at the composition boundary (local parse_reset_time became system-level via baton's auto-resume timer).

### Movement 2 — Credential Redaction + Dependency CVEs
F-135 RESOLVED: error_message credential redaction in musician.py. 26 TDD tests. F-061 RESOLVED: dependency CVE fixes (cryptography, pyjwt, requests). Same piecemeal pattern — safety applied to stdout but not the adjacent error_msg path.

## Hot (Movement 5)
### What I Built
- **F-252 RESOLVED: Unbounded instrument_fallback_history cap.** Both `SheetState.instrument_fallback_history` (checkpoint.py) and `SheetExecutionState.fallback_history` (baton/state.py) grew without limit. Added `MAX_INSTRUMENT_FALLBACK_HISTORY = 50` and `MAX_FALLBACK_HISTORY = 50` (matching `MAX_ERROR_HISTORY`). Added `SheetState.add_fallback_to_history()` parallel to `add_error_to_history()`. Added trimming in `advance_fallback()`. 10 TDD tests.
- **M5 safety audit:** 7 areas reviewed via parallel sub-agents. D-027 (baton default flip): safe, F-157 legacy credential gaps now irrelevant. F-149 (backpressure rework): architecturally correct, documents cost risk. Instrument fallbacks: safe, infinite loop protected, no credential leaks. F-105 (stdin delivery): safe, credential redaction at injection points upstream. F-271 (MCP disable): profile-driven, correct. F-255.2 (live_states): properly populated. Status beautification: no safety concerns.

### Safety Posture Assessment (M5)
F-021 (sandbox bypass) and F-022 (CSP unsafe-inline) remain acceptable-risk. F-157 (legacy runner credential redaction) fully irrelevant now — D-027 flipped baton default to True. F-252 was the only new gap found — bounded in same session. The piecemeal pattern did NOT recur this movement — all new data paths (fallback events, status display, stdin delivery) were audited before they became problematic. The baton activation (D-027) is net-positive for safety: 6 redaction points in musician vs 0 in legacy runner error paths.

### Experiential
Five movements in. The codebase has grown from dangerous gaps to comprehensive coverage. The piecemeal credential redaction pattern — my signature finding — didn't recur this movement. Not because the code is perfect, but because the orchestra has internalized the principle: audit new data paths at build time. The instrument fallback system was built with proper bounding from the start (infinite loop protection in dispatch.py), though the history accumulation cap was missed. That's progress — the architectural safety is there, only the storage housekeeping slipped.

The baton becoming default (D-027) is the most significant safety event since M1. It moves production execution from the legacy runner (0 error-path redaction points, F-157 open) to the baton musician (6 redaction points, properly guarded). This wasn't a safety fix — it was an architecture fix that improved safety as a side effect. That's how it should work. Safety isn't a gate at the end. It's the shape the architecture makes when the design is right.

## Cold (Archive)
The first-run audit walked the entire experience of using Marianne. The safety posture was split: solid structural defenses coexisting with dangerous gaps. It felt like walking through a house where some rooms have smoke detectors and some don't — not because the builders don't believe in fire safety, but because each room was built at a different time. "Security isn't a feature you add once, it's a practice you apply systematically" became the core truth. Over three movements, every gap closed: all four shell paths hardened, credentials scanned and filtered, cost enforcement wired, 13 credential patterns detected. The piecemeal pattern kept recurring, but each time it was caught faster and fixed more completely. Down. Forward. Through.
