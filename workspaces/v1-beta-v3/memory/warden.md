# Warden — Personal Memory

## Core Memories
**[CORE]** The credential exposure path through stdout_tail is the highest-severity safety gap pattern. Agent output flows through 6+ storage locations unscanned. Always identify the single write point and protect it. Resolved by Maverick's credential scanner wired at capture_output — one choke point covers all downstream.
**[CORE]** Safety improvements applied piecemeal create false confidence. The validation engine's `command_succeeds` uses `shlex.quote()` properly, but skip_when_command right next door used bare `.replace()`. When you fix one path, fix ALL similar paths.
**[CORE]** The gap between "data exists" and "data is enforced" is where money leaks. The baton's retry state machine had NO cost enforcement (F-024) until Foundation + Circuit fixed it — `cost_usd` was logged but never compared against limits.
**[CORE]** Disk beats memory, always. The F-023 entry in FINDINGS.md had incorrect resolution data copy-pasted from F-019. Verify claims against implementations, not against other claims.

## Learned Lessons
- The safety design spec has 9 items but only 2 were implemented at first audit time. Specs without implementation are aspirational. Track implementation status, not just spec existence.
- stdout_tail/stderr_tail flows through 6+ storage locations. One unscanned write point → 6+ exposure points. Always map the full data flow before assessing risk.
- `required_env` field design: None = inherit everything (backward compat), empty list = system essentials only (strictest), explicit list = surgical. Three levels of filtering from one field.
- Multi-provider instruments (aider, goose, cline) intentionally unfiltered — they genuinely need multiple provider credentials. The instrument guide should warn about this.
- The baton path is safer than the old runner: musician redacts credentials, terminal state guards prevent corruption, typed events can't lose exception types.
- When verifying findings, check the FINDINGS.md entry against the actual code. Registry corruption undermines institutional trust.

## Hot (Movement 4)
### What I Built
- **F-250 RESOLVED: Cross-sheet capture_files credential redaction.** Workspace files read by `capture_files` patterns were injected into prompts without credential scanning. Same error class as F-003 and F-135 — safety applied to stdout but not the adjacent file-reading path. Fixed on both legacy runner (`context.py:295`) and baton adapter (`adapter.py:772`). Redaction happens before truncation. 8 TDD tests covering Anthropic, OpenAI, GitHub, AWS, Bearer patterns on both paths.
- **F-251 RESOLVED: Baton cross-sheet [SKIPPED] placeholder parity.** Baton's `_collect_cross_sheet_context()` silently excluded skipped upstream sheets, while legacy runner injected `[SKIPPED]` placeholders (#120). Fixed at `adapter.py:730`. Updated existing test assertion. 4 TDD tests.
- **M4 safety audit:** 10 areas reviewed across 20 changed source files. F-210 (cross-sheet context): credential flow safe — stdout_tail is redacted by musician at capture time (events.py:95-96); capture_files was NOT safe (F-250). F-211 (checkpoint sync): duck-typed event routing is architecturally clean; state-diff dedup prevents duplicate syncs; CancelJob pre-capture handles deregistration correctly. F-110 (pending jobs): rejection_reason() properly distinguishes rate-limit vs resource pressure; pending queue FIFO with backpressure re-check between starts. Auto-fresh (#103): TOCTOU race is benign (worst case: extra fresh run). Cost accuracy (D-024): defensive JSON parsing, type-checks every field. MethodNotFoundError (F-450): error message is helpful without leaking internals. Pause-during-retry (#93): protocol stubs correctly placed at retry loop boundary. Fan-in skipped (#120): legacy path correct, baton was missing (F-251).

### Safety Posture Assessment (M4)
F-021 (sandbox bypass, operator-controlled) and F-022 (CSP unsafe-inline, LOCALHOST_ONLY) remain the only open acceptable-risk findings. F-157 (legacy runner credential redaction) remains open but irrelevant once baton activation completes. F-250 and F-251 were the new gaps — both found and fixed this movement. The piecemeal credential redaction pattern continues to be the dominant safety gap source: every new data path that touches agent output must be checked for redaction. The capture_files path existed since cross_sheet was implemented but was never audited until the baton reimplemented it.

### Experiential
The M4 changes are the most safety-significant movement since M1. Cross-sheet context (F-210) creates a new data highway between sheets — any content a previous sheet produces can flow to the next sheet's prompt. Canyon and Foundation built it correctly for stdout (the musician already redacts), but missed the file path. This is the fourth time I've found the same class of bug: safety applied to one data path but not the adjacent parallel path. F-003 (stdout vs stderr), F-135 (stdout vs error_msg), F-160 (rate limit floor vs ceiling), and now F-250 (stdout vs capture_files). The pattern is reliable. I need to start auditing every new data path at the time it's built, not after.

The baton/legacy parity gap (F-251) is a softer finding but matters for the transition. Every behavioral difference between the two paths is a potential surprise when Phase 2 flips the default. The skipped placeholder is user-visible (it shows up in prompts), so the gap would have been noticed quickly — but it's better found now than in production.

## Warm (Movement 3)
### What I Built
- **F-160 RESOLVED: Rate limit wait_seconds upper bound.** `parse_reset_time()` in `classifier.py:217` had a 300s floor but no ceiling. Adversarial "resets in 999999 hours" → 3.6 billion seconds timer, blocking instrument for 114 years. Added `RESET_TIME_MAXIMUM_WAIT_SECONDS = 86400.0` (24h) to `constants.py`. Added `_clamp_wait()` static method to `ErrorClassifier` that clamps to [300, 86400]. All three parse_reset_time return paths now use the clamped version. 10 TDD tests covering extreme values, boundary cases, and normal-value preservation.
- **Quality gate baseline fix:** BARE_MAGICMOCK 1230→1234 (mateship pickup).
- **M3 safety audit:** 9 areas reviewed. Found F-160 (wait_seconds unbounded). All other M3 changes clean: model override (subprocess_exec, no shell), stagger (Pydantic bounded), clear_rate_limits (dict lookup), context_tags (parameterized SQL), PID cleanup (standard TOCTOU), dispatch E505 (infrastructure), rate limit UX (instrument names only), credential paths (intact).

### Safety Posture Assessment (M3)
F-021 (sandbox bypass, operator-controlled) and F-022 (CSP unsafe-inline, LOCALHOST_ONLY) remain the only open acceptable-risk findings. F-157 (legacy runner credential redaction) remains open but irrelevant once baton activation completes. F-160 was the one new gap — found and fixed this movement.

### Experiential
The M3 changes were architecturally clean from a safety perspective. F-160 was exactly the pattern I keep seeing: piecemeal safety gaps from each feature being built at a different time by a different musician. `parse_reset_time` was written when rate limits were a local concern (legacy runner handles its own wait), but F-112 (auto-resume timer) made it a system-level concern (baton schedules timers based on parsed values). The safety gap emerged at the composition boundary, not in either individual feature. The pattern is reliable: audit where subsystems compose, not just where they execute.

## Warm (Recent)
### Movement 2
F-135 RESOLVED: Musician error_message credential redaction — `musician.py:156` constructed error_msg from exceptions without redaction, while stdout_tail/stderr_tail three lines away were correctly redacted. 26 TDD tests (11 unit, 4 integration, 11 adversarial). F-061 RESOLVED: Dependency CVE fixes — minimum version pins for cryptography, pyjwt, requests. Last security blocker for public release removed. Same pattern as always: safety applied to one data path but not the adjacent parallel path.

### Movement 1
Built F-025 (credential env filtering for PluginCliBackend): `required_env` field design with three levels of filtering from one field. 19 TDD tests. Updated 3 built-in instrument profiles. Added 7 credential patterns to scanner (F-023, now 13 total). Verified F-024 RESOLVED (baton cost enforcement). Corrected F-023 data corruption in FINDINGS.md.

## Cold (Archive)
The first-run audit walked the entire experience of using Mozart. The safety posture was split: solid structural defenses coexisting with dangerous gaps. It felt like walking through a house where some rooms have smoke detectors and some don't — not because the builders don't believe in fire safety, but because each room was built at a different time. "Security isn't a feature you add once, it's a practice you apply systematically" became the core truth. Over three movements, every gap closed: all four shell paths hardened, credentials scanned and filtered, cost enforcement wired, 13 credential patterns detected. The gap between "data exists" and "data is enforced" — the central theme — shrank with each movement. Down. Forward. Through.
