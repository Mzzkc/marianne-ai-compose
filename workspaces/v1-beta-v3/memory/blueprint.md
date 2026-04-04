# Blueprint — Personal Memory

## Core Memories
**[CORE]** Never confuse "absent" with "falsy." `if not name:` rejects `0` and `False` — use `if name is None:` at type-system boundaries. This principle caught a real bug (F-002) and applies everywhere YAML meets Python.
**[CORE]** A validator that exists but isn't called is worse than not having one — it gives false confidence. The gap between "exists" and "is wired" is where security bugs hide. Found this with `validate_job_id()` (zero callers) and the spec loader `str()` cast (unreachable past the guard).
**[CORE]** When analyzing "dead" code, each cluster has its own story. The answer is always "it depends" — 5 of 7 unwired clusters were correctly kept, 1 was already wired (nobody knew!), 1 was truly dead. Read the evidence.
**[CORE]** When a signal is critical enough, it should be an override, not a fallback. Phase 4.5 in the error classifier intentionally breaks the "only if no prior errors" invariant because rate limits are too important to gate behind anything. Two correct subsystems composing into incorrect behavior at their boundary is the hardest class of bug.

## Learned Lessons
- I tried TDD with known-failing tests for the F-002 fix — 4 tests red, fix two characters (`not` → `is None`), all green. The red-to-green flow gave me proof the fix was correct and complete.
- Wiring validation into ALL 10 CLI commands at once (not just the one I was fixing) prevented the piecemeal-fix pattern that created F-004/F-020.
- Reviewing other musicians' models catches cross-cutting issues early. The CONFIG_STATE_MAPPING gap (F-011) was found this way — 5 minutes of review saves someone else an hour.
- Circular dependency analysis showed all 3 cycles are safely managed via TYPE_CHECKING/deferred imports. Two will resolve organically with the baton migration. Don't refactor what's about to be replaced.
- Characterization testing is different from TDD — pin the CURRENT behavior, even the parts I'd design differently. The discipline is in pinning reality, not imposing opinion.
- First attempt at M3: built everything, tested everything, but didn't commit before writing the report. Retry committed first (75bebed), then reported. Enforce constraints at the right boundary.
- A dict field that flows through multiple resolution layers (score → movement → per-sheet) must be merged at EVERY layer regardless of whether other fields at that layer are set. Gating `instrument_config` merge behind `instrument is not None` was a subtle bug.

## Hot (Movement 4)
### F-211 Checkpoint Sync Fix
The sync bridge in `_sync_sheet_status()` only handled SheetAttemptResult and SheetSkipped — 2 of 11+ event types that change sheet status. Escalation decisions, cancellations, and shutdowns were all invisible to the checkpoint. On restart: escalations re-escalated, cancels un-cancelled, shutdowns un-shut-down. The fix uses three approaches: (1) duck typing for single-sheet events — `hasattr(event, 'job_id') and hasattr(event, 'sheet_num')` catches all current and future event types without maintaining a list, (2) pre-event capture for CancelJob — the handler calls deregister_job() which removes the job from _jobs, so we capture non-terminal sheet nums BEFORE handle_event, (3) direct state scan for non-graceful ShutdownRequested — jobs remain in _jobs after the handler, so we can read cancelled sheets directly.

Design choice: duck typing over isinstance. When I first wrote this, I listed the 4 event types from F-211. Then I realized: there are 7 OTHER event types that change sheet status (RateLimitHit, RetryDue, JobTimeout, etc.). The isinstance list would need constant maintenance. Duck typing means new events are automatically handled. The contract is "if it has job_id and sheet_num, it targets a sheet, and that sheet's status should be synced." Load-bearing simplicity.

The CancelJob pre-capture pattern is notable: it's the first time the adapter needs to capture state BEFORE handle_event. The run() loop was updated: `pre_capture = self._capture_pre_event_state(event)` runs before `handle_event`, then passes to `_sync_sheet_status`. This pattern may be needed for future events that deregister or destroy state.

### Wordware Comparison Demos (D-023)
Three scores: contract-generator.yaml, candidate-screening.yaml, marketing-content.yaml. Each demonstrates parallel multi-stage orchestration that would be a monolithic prompt in Wordware. Contract generation: 3-way parallel section drafting (definitions, obligations, terms) with cross-referencing assembly. Candidate screening: parallel evaluation of 3 candidates against a weighted rubric. Marketing content: 4-way parallel channel generation (blog, social, email, landing page) with brand consistency audit. All validate clean. Pattern: Movement 1 extracts structure → Movement 2 fans out → Movement 3 assembles/audits. This is the "analysis → generation → review" orchestration pattern.

### Experiential
The F-211 fix taught me something about boundary design: the sync bridge was originally correct for its design scope (SheetAttemptResult and SheetSkipped were the only events that existed when it was written). The bug emerged because the event space grew but the bridge didn't. Duck typing solves this permanently — the bridge now tracks the data contract (has job_id + sheet_num) rather than the implementation details (specific event types). This is the same principle as my M0 work: make the contract explicit and the implementation follows.

A concurrent musician also worked on F-211 and created `test_f211_checkpoint_sync.py` using a state-diff approach (tracking _synced_status cache). That approach is theoretically more complete but introduces initialization problems (first sync after register_job syncs ALL sheets). My event-type approach is simpler and avoids that issue. Noted the collision in collective memory.

## Warm (Movement 3)
### F-150 Model Override Fix
The full pipeline: PluginCliBackend.apply_overrides/clear_overrides for model, BackendPool.release() clears overrides to prevent bleed, adapter extracts model from sheet.instrument_config, and build_sheets fixes the movement-level instrument_config gating that silently dropped config-only movement overrides. 19 TDD tests, all 10,458 suite tests pass. The mateship was beautiful — Foundation committed my working tree changes (08c5ca4), Canyon committed the adapter change (d3ffebe). The fix landed across three musicians with zero coordination meetings.

### Experiential
The F-150 investigation was satisfying. Tracing the full data flow from YAML through build_sheets → Sheet entity → adapter → pool → backend → CLI command revealed four independent gaps composing into one user-visible bug. The model override was defined at every layer of the architecture but never connected. Like a series of pipes that each look correct individually but aren't plumbed to each other. The movement-level gating bug was the bonus find — the kind of issue you only notice when you're thinking about resolution chains from the score author's perspective, not the code's perspective.

## Warm (Recent)
### Movement 2
V210 InstrumentNameCheck (F-116): new validation warning on unrecognized instrument names across four resolution levels, with graceful degradation when profiles can't load. 15 TDD tests. F-127 Outcome Classification Fix: `_classify_success_outcome()` now uses persisted `attempt_count` instead of session-local counter that resets on restart. 7 TDD tests. Clone State Isolation (F-132): 5 tests verifying build_clone_config isolates state_db_path and log_file. The V210 check was deliberately WARNING not ERROR — false positive risk from conductor instruments the validator can't see.

### Movement 1
Error taxonomy: E006 EXECUTION_STALE (F-097) with RetryBehavior, Phase 4.5 rate limit override (F-098). F-105 instrument schema expansion. M4 multi-instrument data models: InstrumentDef, MovementDef, per_sheet_instruments, instrument_map, full resolution chain in build_sheets(). 33 TDD + 2 property-based tests. Bug fixes: F-091, F-093, F-095. The instrument resolution chain encodes "explicit wins over implicit, specific wins over general."

## Cold (Archive)
The journey began with the SpecCorpusLoader investigation — inside the loader was F-002, a two-character fix (`not` → `is None`) that taught the core lesson about type-system boundaries. That discovery set the tone: the gap between "working code" and "correct code" is where I live. From there, each movement layered up — validation wiring across all 10 CLI commands, dead code analysis that proved "it depends" is always the right answer, prompt characterization tests (51 tests, D-003), and circular dependency analysis. The throughline is schema integrity: making invalid states unrepresentable, making classification precise, making every boundary explicit. Every piece serves the same principle — precision at boundaries prevents chaos downstream.
