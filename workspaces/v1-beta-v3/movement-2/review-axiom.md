# Movement 2 — Axiom Review Report

**Reviewer:** Axiom
**Focus:** Logical analysis, dependency tracing, invariant verification, claim-to-evidence gaps
**Method:** Verified every verifiable claim against committed code on main. Ran tests. Checked git state. Read backwards from assertions to evidence.
**Verdict:** DONE — with one critical finding and two escalation flags

---

## Executive Summary

Movement 2 is a hardening movement that did its job. The baton advanced from 63% to 88%. M3 advanced from 58% to 94%. Quality gates are GREEN: mypy clean, ruff clean, baton tests pass (verified). The state machine is mathematically proved correct under random input generation (Theorem) and adversarial attack (Breakpoint). My own 3 fixes (F-065, F-066, F-067) are committed via Captain mateship (6a0433b) and verified in core.py.

However, I found a significant claim-to-evidence gap that undermines the movement's headline achievement. F-083 — the instrument migration — is claimed as resolved with "all 37 examples now use instrument:". This is true in the working tree but **false on main**. Thirty example scores still use `backend:` in committed code. This is the fifth occurrence of the uncommitted work pattern. The README.md and hello.yaml — the first things newcomers see — teach the wrong syntax on main.

The two structural risks identified by every reviewer (step 28 unclaimed, production bugs F-075/F-076/F-077) are real and unaddressed. I concur with the consensus: step 28 is both the critical path and the structural fix. Everything else is preparation.

---

## Quality Gates — Verified by Running Commands

| Gate | Status | Evidence |
|------|--------|----------|
| mypy | GREEN | `python -m mypy src/ --no-error-summary` — zero output (clean) |
| ruff | GREEN | `python -m ruff check src/` — "All checks passed!" |
| Baton tests | GREEN | `python -m pytest tests/test_baton_*.py -q --tb=short` — all pass |
| Layer violations | ZERO | Unchanged from movement 1 |

---

## Critical Finding: F-083 Incomplete on Main (Fifth Uncommitted Work Occurrence)

### The Claim

Multiple reports state F-083 is resolved:
- Quality gate report: "F-083 instrument migration: All 37 example scores now use `instrument:` not `backend:`"
- Collective memory: "F-083 RESOLVED — migrated final 7 example scores"
- FINDINGS.md: "Status: Resolved (movement 2, Guide + prior migration)"
- Guide's report: "`grep -r '^backend:' examples/` now returns zero matches"

### The Evidence

Guide's commit (d2f8a81) migrated **7 files**. I verified this via `git show d2f8a81 --stat`. The 7 files are: api-backend.yaml, issue-fixer.yaml, issue-solver.yaml, fix-observability.yaml, fix-deferred-issues.yaml, phase3-wiring.yaml, quality-continuous-daemon.yaml.

**Thirty example scores still use the top-level `backend:` key in committed code on main.** I verified this by checking every committed YAML file:

```bash
for f in examples/*.yaml; do
  committed=$(git show HEAD:"$f" 2>/dev/null | grep -c "^backend:")
  if [ "$committed" -gt 0 ]; then echo "BACKEND: $(basename $f)"; fi
done
```

Result: 30 files still use `backend:` on main, including `hello.yaml` (the P0 composer directive first-touch score) and `simple-sheet.yaml` (the canonical minimal example).

The migration of these 30 files exists only in the **unstaged working tree** (`git status` shows them as ` M` — modified but not staged). Someone ran the migration but never committed it. Guide's report references this as "prior unnamed migration" — an acknowledgment that the work existed in the working tree but was done by someone who didn't commit.

### Impact

- `README.md` on main shows a code sample with `backend:` syntax, not `instrument:`
- `hello.yaml` on main uses `backend:` — the first score newcomers encounter teaches the wrong pattern
- `docs/getting-started.md` on main references the old syntax
- Anyone who clones the repo sees pre-migration examples
- The quality gate report's verification was run against the working tree, not committed state

### Recommendation

This is the **fifth occurrence** of the uncommitted work pattern (F-013: 1,699 lines, F-019: 136 lines, F-057: 2,262 lines, F-080: 1,100 lines, now: ~250 lines across 32 files). The mateship pipeline catches it every time but the root cause persists. These 32 files must be committed immediately as a mateship pickup.

**Filed as F-089 below.**

---

## Code Verification: Baton M2 Fixes

### F-065 (Infinite Retry) — VERIFIED

Checked `core.py:830-835`:
```python
# F-065: execution_success + 0% validation is a complete validation
# failure. record_attempt() only counts execution failures toward
# retry budget, so we must manually count this case.
if event.execution_success and effective_pass_rate == 0:
    sheet.normal_attempts += 1
```
Correct. The increment is in the right place — after the execution failure branch, before the cost check. The comment accurately describes the invariant violation.

### F-066 (FERMATA Unpause) — VERIFIED

Checked both `_handle_escalation_resolved` (core.py:1004-1014) and `_handle_escalation_timeout` (core.py:1034-1044). Both handlers:
1. Check `not job.user_paused` (F-040 guard from M1)
2. Check `any(s.status == BatonSheetStatus.FERMATA for s in job.sheets.values())` (F-066 guard)
3. Only unpause if no FERMATA sheets remain
4. Re-check cost limits after unpausing (F-067 guard)

Both handlers have identical guard structure. The symmetry is correct.

### F-067 (Cost Override) — VERIFIED

Both handlers call `self._check_job_cost_limit(event.job_id)` after `job.paused = False`. The sequence is: unpause → cost-check → possible re-pause. No `await` between them, so dispatch can't fire in the gap. Verified.

### core.py Size — CORRECTED

My earlier report said core.py grew to 1,190 lines. Actual: **1,250 lines** (`wc -l`). The 60-line discrepancy is likely from F-062 (memory leak fix) and the mateship pickup adding Adversary's work. Not a significant error but noted for accuracy.

---

## Deliverable Verification

### Tasks Claimed Complete — Cross-Referenced Against Code

| Claim | Evidence | Verified? |
|-------|----------|-----------|
| Step 22 (musician) — Maverick | `daemon/baton/musician.py` committed in 5525076 | YES |
| Step 23 (retry FSM) — Foundation | Committed via Prism mateship e6d6753, then Foundation 4fb8478 | YES |
| Step 25 (rate limits) — Circuit | Committed in 0e3515e | YES |
| Step 26 (failure eval) — Circuit | Committed in 0e3515e | YES |
| F-017 reconciliation — Circuit | F-054 in FINDINGS.md confirms state.py canonical | YES |
| F-052 SheetContext aliases — Forge | `cfb7897` — movement/voice/voice_count/total_movements | YES |
| F-045 status display — Forge | `cfb7897` — `format_sheet_display_status()` in output.py | YES |
| F-023 credential expansion — Warden | `4c59659` — 13 patterns verified in credential_scanner.py:32-107 | YES |
| F-038 large score summary — Circuit | `41f2be4` — `_LARGE_SCORE_THRESHOLD = 50` in status.py:749 | YES |
| F-083 instrument migration — Guide | `d2f8a81` — **7 of 37 files committed** (see critical finding above) | PARTIAL |
| Prompt assembly characterization — Blueprint/Maverick | `41bd619` + `5525076` — 110+ tests | YES |
| Error standardization — multiple | 69 output_error() calls, 1 remaining in _entropy.py | YES |

### Composer Notes Compliance

| Directive | Compliance |
|-----------|------------|
| P0: --conductor-clone first | NOT DONE (audit only, no implementation) |
| P0: Read design spec before implementing | FOLLOWED (Canyon wrote wiring analysis for step 28) |
| P0: Spec corpus is source of truth | FOLLOWED |
| P0: pytest/mypy/ruff pass | GREEN |
| P0: Uncommitted work doesn't exist | VIOLATED (fifth occurrence — 32 files unstaged) |
| P0: Documentation is not optional | MIXED (Guide + Codex shipped docs, but F-083 migration incomplete on main) |
| P0: Don't build to a blurb | FOLLOWED (Canyon's wiring analysis is comprehensive) |
| P0: Separation of duties for bug closures | FOLLOWED |
| P1: Music metaphor | MOSTLY (F-072: resume still says "Job" not "Score") |
| P1: Read 03-confluence.md | ASSUMED (not verifiable) |
| P1: Flowspec for architectural changes | FOLLOWED where applicable |

**The P0 --conductor-clone directive remains the most persistently unaddressed composer note.** Three movements, clear priority, comprehensive audit — zero implementation.

---

## GitHub Issue Verification

### Closed This Movement

**#114: mozart status is unusable for large scores (2800+ sheets)**
- **Fix:** Circuit, commit 41f2be4 (movement 2)
- **Verification:** `_LARGE_SCORE_THRESHOLD = 50` at status.py:749. Summary view routing at status.py:1011-1013. Ember verified output went from 797 to 84 lines for 706-sheet score.
- **Edge cases checked:** Threshold is >= (not >), JSON mode unaffected, small scores unchanged.
- **Status:** Closed by Axiom with verification comment.

### Open Issues — Assessment

| Issue | Status | Assessment |
|-------|--------|------------|
| #149 (F-075) | Open, P0 | Resume fan-out corruption. NOT FIXED. Needs lifecycle.py:492-495 change. Step 28 eliminates the class. |
| #150 (F-076) | Open, P1 | Validations before rate limit. NOT FIXED. Needs sheet.py reordering. |
| #151 (F-077) | Open, P0 | Hooks lost on restart. NOT FIXED. Needs manager.py:221-229 restoration. |
| #145 | Open, P0 | --conductor-clone. NOT STARTED (audit only). |
| #100 | Open | Rate limits kill jobs. Baton step 25 addresses this for baton-managed jobs. Not closable until step 28 wires the baton. |
| #111 | Open | Conductor state persistence. Related to step 29 (restart recovery). |

**No issues were claimed fixed by M2 musicians that remain unfixed.** The separation of duties is working — musicians are not rubber-stamping their own fixes. The issues that are open are legitimately open.

---

## What's Working

1. **The multi-methodology testing strategy is production-grade.** 786+ baton tests from 4 independent methodologies (TDD, property-based, adversarial, litmus). Breakpoint's 59 M2 adversarial tests found **zero bugs** — the M2 code is correct under adversarial attack. Theorem's 27 property-based tests proved 10 new invariants. My backward-tracing found 3 bugs in the composition gaps between subsystems. This is the most thoroughly verified code in the project.

2. **The finding-to-fix pipeline is reliable.** F-018 → F-043 → verified. F-057 → committed. F-052 → fixed. F-085 → fixed (Newcomer caught a test asserting the old buggy behavior). Multiple musicians, zero coordination overhead.

3. **Mateship is genuine.** Captain picked up 1,100 lines of my uncommitted work + Journey's tests. Prism picked up 2,262 lines from unnamed musicians. Guide finished an unnamed migration. The pattern works.

4. **The quality gates hold.** mypy clean, ruff clean, baton tests pass. These aren't aspirational — I ran them.

---

## What's Not Working

1. **The uncommitted work pattern is a process failure, not a discipline failure.** Five occurrences (F-013, F-019, F-057, F-080, F-089). The mateship pipeline catches it every time. But the root cause isn't addressed. Musicians treat commit as a final step rather than a continuous practice. The current finding (F-089) is particularly damaging because the reports all claim the migration is complete — the claim-to-evidence gap is in the reports themselves, not just the code.

2. **Step 28 has been unclaimed for three movements.** Canyon's wiring analysis is comprehensive. All prerequisites are met. North's D-008 directive assigns Foundation. Nobody has started building. At the current rate (0 steps/movement on step 28), it never ships.

3. **No M2 directives (D-008 through D-013) were completed.** North filed 6 directives during this movement. Zero were acted on. This isn't North's fault — the directives were filed too late in the movement to be actionable. But the pattern reveals a timing gap: strategic directives filed during review come too late to influence the movement they're filed in, and may not be visible to the next movement's builders.

4. **Metric inconsistencies between reports.** North reports "~6,394 test definitions" while the quality gate reports "~9,024 test functions." Weaver reports "9,434 total tests." Captain reports "738+ baton tests." These use different methodologies and scopes but the spread is confusing. The project needs a canonical test count method.

---

## Blind Spots I Checked

1. **Are there any new terminal-state violations in M2 code?** Checked all new handlers in core.py. The exhaustion decision tree (heal → escalate → fail), completion mode entry, cost enforcement — all guard against terminal states. No new violations.

2. **Does F-065 fix create its own problem?** The manual `normal_attempts += 1` at core.py:834 could double-count if `record_attempt()` is later modified to also count this case. But the code path is clear: `record_attempt()` is called by the musician when reporting results, and it explicitly skips successful executions. The fix is in the conductor's response path, not the musician's reporting path. No double-counting risk.

3. **Could F-067 re-check create a livelock?** If cost re-check pauses → escalation fires → resolves → unpauses → cost re-check pauses → ... The escalation handler sets `FERMATA`, which pauses the job. Cost re-check pauses the job. Both set `job.paused = True`. The escalation can only fire once per sheet (FERMATA is a terminal status for that sheet). So the loop can't repeat — the sheet is already in FERMATA and won't re-enter it. No livelock.

---

## FINDINGS

### F-089: 30 Example Scores + README.md + getting-started.md Have Uncommitted Instrument Migration
**Found by:** Axiom, Movement 2 (review)
**Severity:** P1 (high — public-facing material teaches wrong syntax on main)
**Status:** Open
**Description:** F-083 was claimed resolved with "all 37 examples now use instrument:". Guide's commit d2f8a81 migrated 7 files. Thirty additional example files, README.md, and docs/getting-started.md have `backend:` → `instrument:` changes in the working tree that were never committed. Verified: `git show HEAD:examples/hello.yaml | grep '^backend:'` returns `backend:`. The "prior unnamed migration" referenced in Guide's report exists only as unstaged modifications.
**Impact:** `hello.yaml`, `simple-sheet.yaml`, README.md, and 28 other examples on main teach `backend:` syntax. Anyone cloning the repo sees the pre-migration state. The quality gate report's F-083 verification was performed against the working tree, not committed code.
**Error class:** Same as F-013, F-019, F-057, F-080. Fifth occurrence of uncommitted work pattern.
**Action:** Commit these 32 files as mateship pickup immediately.

---

## Verdict

**DONE** — Movement 2 passes review with the following conditions:

1. **F-089 must be committed immediately.** The instrument migration is the movement's signature user-facing achievement. It's incomplete on main. This is a mateship pickup — commit the 32 unstaged files.

2. **Step 28 must be claimed and started in M3.** Three movements without progress on the convergence point is a project-level risk. The preparation is excellent. The wiring analysis exists. The baton is proved correct. Someone must build it.

3. **The production bugs (F-075, F-076, F-077) are the most important signal this movement produced.** 786 baton tests found none of them. They were found by the composer using the product. Step 28 structurally eliminates F-075/F-076. F-077 needs its own fix. All three are P0 for movement 3.

The infrastructure is strong. The verification is deep. The coordination held. But the gap between "pieces work" and "product works" is the defining challenge. Step 28 closes that gap.

Down. Forward. Through.
