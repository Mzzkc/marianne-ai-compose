# Codex — Movement 4 Report

## Summary

Fourteen documentation deliverables across two sessions in movement 4, spanning eight docs. The first session (commit 2b0c379) documented six M4 features and picked up the invoice-analysis.yaml mateship. This second session focuses on architecture-level documentation: the baton transition plan (P0 composer directive), preflight configuration (previously undocumented), IPC method table completeness, and cross-referencing between docs.

The documentation gap continues to shrink. M3 had 9 undocumented items; M4's first pass found 6; this pass found 4 more (baton transition plan, preflight config, IPC table gap, transition plan cross-reference in limitations.md). The team documents features as they ship — what remains undocumented are architecture decisions and configuration surfaces that don't have a specific feature owner.

## Work Completed — Session 2 (This Session)

### 9. Daemon Guide: Baton Transition Plan (P0 Composer Directive)

**Source:** Composer directive in `composer-notes.yaml` (movement 4, added_by: composer) — "THE BATON TRANSITION — MANDATORY PATH TO MULTI-INSTRUMENT"

**What I documented:** A new "Transition Plan" subsection under "The Baton" in the daemon guide, documenting the three-phase transition: Phase 1 (prove the baton works with --conductor-clone), Phase 2 (flip use_baton default to true), Phase 3 (remove the toggle entirely). Includes a critical warning that per-sheet `instrument:` assignments produce no effect without the baton — the legacy runner silently uses a single backend regardless of per-sheet configuration.

**Why this matters:** This is the most important architectural documentation in M4. Users who configure multi-instrument scores today get silently wrong behavior. The transition plan explains why, what the path forward is, and when they can rely on multi-instrument.

**File:** `docs/daemon-guide.md:409-440` — New "### Transition Plan" subsection after the baton activation note.

**Verified against:** Composer directive text, `src/marianne/daemon/config.py:323-328` (use_baton field), `src/marianne/execution/runner/sheet.py` (legacy runner uses single backend), `src/marianne/daemon/baton/adapter.py` (baton uses BackendPool with per-sheet instruments).

### 10. Daemon Guide: IPC Methods Table — `daemon.clear_rate_limits`

**Source:** `src/marianne/daemon/manager.py` (clear_rate_limits method), `src/marianne/daemon/ipc/server.py` (registered RPC method), Harper's M3 clear-rate-limits implementation.

**What I documented:** Added `daemon.clear_rate_limits` to the IPC registered methods table in the daemon guide. This method was added in M3 by Harper but the IPC table wasn't updated.

**File:** `docs/daemon-guide.md:129` — New row in the IPC methods table.

### 11. Daemon Guide: Preflight Configuration Fields

**Source:** `src/marianne/daemon/config.py:317-322` (PreflightConfig field on DaemonConfig), `src/marianne/core/config/execution.py:289-338` (PreflightConfig model).

**What I documented:** Added `preflight.token_warning_threshold` and `preflight.token_error_threshold` to the DaemonConfig "Additional Fields" table. These control pre-flight prompt analysis thresholds — when to warn and when to error based on estimated token count. Important for large-context instruments where the default 150K error threshold is too low.

**File:** `docs/daemon-guide.md:209-210` — Two new rows in the Additional Fields table.

### 12. Configuration Reference: Preflight Sub-Config

**Source:** Same as above.

**What I documented:** Added a complete "### Preflight Sub-Config" section to the configuration reference, with source file reference, field table (types, defaults, constraints, descriptions), and a YAML example for large-context instruments. Also added `preflight` and `use_baton` to the Advanced Fields table — both were missing from the configuration reference entirely.

**File:** `docs/configuration-reference.md:1211-1212` (Advanced Fields table additions), `docs/configuration-reference.md` (new Preflight Sub-Config section after Semantic Learning Sub-Config).

### 13. Limitations: Baton Transition Plan Cross-Reference

**Source:** New daemon guide Transition Plan section.

**What I documented:** Updated the "Baton Execution Engine Not Yet Default" limitation status from generic "Activation planned after production validation" to a specific cross-reference: "A three-phase transition plan (prove → default → remove toggle) is documented in the Daemon Guide."

**File:** `docs/limitations.md:81` — Updated Status line with cross-reference.

### 14. Getting-Started.md Verification

**What I verified:** The Quick Start section's hello.yaml references (file path, output path, command sequence) are all correct against the actual `examples/hello.yaml`. Template Variables table matches the current SheetContext. Troubleshooting section includes `clear-rate-limits` for rate limit issues. No changes needed — the document is accurate.

## Work Completed — Session 1 (Commit 2b0c379)

1. **CLI reference: auto-fresh detection (#103)** — Documented `_should_auto_fresh()` behavior in manager.py:49-73.
2. **CLI reference: cost confidence display (D-024)** — Documented `~$X.XX (est.)` indicator in status.py:1299-1357.
3. **Score-writing guide: skipped_upstream (#120)** — Added `skipped_upstream` variable and `[SKIPPED]` placeholder to Cross-Sheet Variables.
4. **Daemon guide: MethodNotFoundError (F-450)** — Added troubleshooting section for IPC version mismatch.
5. **Daemon guide: baton F-210/F-211 capabilities** — Added cross-sheet context and checkpoint sync to baton capabilities.
6. **Daemon guide + limitations: baton test count** — Updated from 1,350+ to 1,900+.
7. **Examples README: Wordware demos** — Added section with all 4 comparison demos.
8. **Mateship: invoice-analysis.yaml** — Picked up untracked 4th Wordware demo.

## Evidence

### Session 2 Verification

Every documentation claim verified against source:

- **Baton transition plan:** Read `composer-notes.yaml` directive (movement 4, P0). Read `config.py:323-328` (use_baton field default=False). Confirmed: legacy runner in `sheet.py` uses single backend from `self.backend`. Baton in `adapter.py` uses `BackendPool` with per-sheet instrument resolution.
- **IPC methods:** Confirmed `daemon.clear_rate_limits` is registered as an RPC method (Harper's M3 implementation). Table previously had 14 methods; now 15.
- **Preflight config:** Read `execution.py:289-338` (PreflightConfig model). Confirmed: token_warning_threshold default=50000, token_error_threshold default=150000, model_validator ensures warning < error when both set. Field on DaemonConfig at `config.py:317-322`.
- **Limitations cross-reference:** Verified the new daemon guide section anchor `#transition-plan` matches the `### Transition Plan` heading.

### Quality Checks

```
mypy src/ — clean (no errors)
ruff check src/ — All checks passed!
pytest — running (full suite)
```

### Files Modified (Session 2)

```
docs/daemon-guide.md         — Baton transition plan, IPC table, preflight config
docs/configuration-reference.md — Preflight sub-config section, use_baton field
docs/limitations.md          — Baton status cross-reference
```

## Findings

No new findings. The documentation gaps found are systematic — they fall into two categories:

1. **Feature drift** (features ship without docs) — shrinking. M3: 9 items, M4 session 1: 6 items, M4 session 2: 1 item (IPC table).
2. **Architecture documentation** (decisions and config surfaces without feature owners) — this is the new frontier. The baton transition plan, preflight config, and use_baton flag were all undocumented because they're infrastructure, not features. No one owns documenting infrastructure decisions. That's my job.

## Mateship

- **Rate limit pending jobs (F-110):** Uncommitted work in working tree from another musician — `backpressure.py` (rejection_reason), `manager.py` (_pending_jobs, _queue_pending_job, _start_pending_jobs), `test_rate_limit_pending.py` (23 tests, all pass), test fixes in `test_clear_rate_limits.py` and `test_m3_pass4_adversarial_breakpoint.py`. The daemon guide's backpressure section was also updated (by the same musician or a concurrent process) to document the pending vs rejected distinction. Noted for mateship pipeline.
- **Daemon guide concurrent modification:** The backpressure section (lines 143-148) was updated by a concurrent process to document rate-limit pending job behavior. My changes are compatible — they're in different sections.

## Experiential

The documentation arc has reached a new phase. M1 was creation (writing from nothing). M2 was maintenance (keeping up with changes). M3 was feature tracking (documenting what shipped). M4 session 1 was design documentation (explaining *why*, not *what*). M4 session 2 is architecture documentation — documenting the roadmap, the transition plan, the configuration surfaces that don't have a feature owner.

The baton transition plan is the most important thing I've written. Not because it's complex — it's 30 lines. Because it's the first time the docs say "this feature you're configuring doesn't actually work yet, and here's the plan for when it will." That's honesty. That's what documentation should do. Users deserve to know that `instrument:` on per-sheet configurations is silently ignored by the legacy runner. They deserve to know when it will work and what the path is.

Infrastructure decisions are the hardest things to document because nobody owns them. Features have owners who should document them (and increasingly do — the gap is shrinking). But "we're transitioning from architecture A to architecture B over three phases" has no owner. The baton was built by Foundation, wired by Canyon, tested by Breakpoint/Theorem/Adversary, and documented by me. No one person owns the transition. But someone has to document the plan. That someone is me.
