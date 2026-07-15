# C1 Marianne Acting Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use Marianne and the candidate Marianne Expert to reconcile and complete native-backend unification against current source.

**Architecture:** A score freezes evidence, asks one accountable expert to reconcile and implement, then uses deterministic CLI sheets and an independent inspection sheet to gate an exact candidate digest. Source and artifact workspaces remain separate.

**Tech Stack:** Marianne score YAML, gpt-5.6/codex-cli, bash CLI sheets, pytest, git.

## Global Constraints

- Use `/home/emzi/Projects/marianne-expert-release` as project root and a separate score workspace.
- Never stop the active conductor and never use destructive git commands.
- Treat the supplied handoff as fallible evidence.
- Re-run the full suite after every repair.
- A changed source digest invalidates prior inspection.

---

### Task 1: Repair the inherited baseline oracle

**Files:**
- Modify: `tests/test_instrument_user_journeys.py`

- [ ] Use the two existing failing tests as RED and confirm they fail only because `gpt-5.6` is absent from expected names/counts.
- [ ] Add `gpt-5.6` to the expected set and derive the combined registry count from the expected set plus native profiles.
- [ ] Run both tests and then the full suite.

### Task 2: Compose and statically gate the acceptance score

**Files:**
- Create: `scores-internal/marianne-expert-c1/design.yaml`
- Create: `scores-internal/marianne-expert-c1/score.yaml`
- Create: `scores-internal/marianne-expert-c1/input/c1-handoff.md`

- [ ] Write the design contract with authority, stages, injection ledger, proof obligations and repair loop.
- [ ] Run `check_design.py` and observe a pass before writing score YAML.
- [ ] Compose freeze, reconcile, implement, verify, inspect and release movements.
- [ ] Attach the candidate expert as a skill technique and include a unique release sentinel.
- [ ] Give every deterministic CLI sheet an explicit empty fallback chain.
- [ ] Run `mzt validate`, `check_score_release.py --write-lock`, and lock verification.

### Task 3: Run the read-only expert reconciliation

**Files:**
- Runtime output: `workspaces/marianne-expert-c1/reconciliation.json`

- [ ] Submit only the freeze and reconciliation boundary without source-write authority.
- [ ] Record the returned runtime job ID.
- [ ] Inspect status, history and rendered evidence.
- [ ] Require discovery of all current base-contract consumers and an explicit correction of the stale six-file claim.

### Task 4: Execute approved C1 phases

**Files:**
- Modify: source, tests, docs and specs discovered by the reconciliation stage

- [ ] Grant source-write authority in the run manifest only after reconciliation passes.
- [ ] Relocate the backend contract with a compatibility phase and tests.
- [ ] Implement profile-driven Anthropic wire translation and preserve Ollama behavior with tests.
- [ ] Migrate surviving behavioral tests before deleting native implementation tests.
- [ ] Remove native registrations, modules, package and obsolete dependency only after consumers are rerouted.
- [ ] Align binding architecture canon, user docs and historical annotations.

### Task 5: Verify and inspect the exact candidate

**Files:**
- Runtime output: `verification.json`, `inspection.md`, `release.json`

- [ ] Run targeted translator/profile tests.
- [ ] Run source scans for native classes, old import paths, Doctrine exceptions and false module claims.
- [ ] Run the full pytest suite and documentation checks.
- [ ] Run `mzt instruments check anthropic_api` and `mzt instruments check ollama`.
- [ ] Hash the source candidate and dispatch independent inspection.
- [ ] If inspection changes source, invalidate the digest and repeat all verification.
- [ ] Write release evidence only when inspected and verified digests match.

