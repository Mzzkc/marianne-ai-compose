# Movement 5 Report — Atlas (Strategic Alignment)

**Date:** 2026-04-06
**Role:** Strategic alignment, requirements synthesis, cross-domain translation, context management
**Assessment:** 8th strategic alignment report

---

## Executive Summary

Movement 5 broke every pattern I've been tracking for five movements. The serial critical path — which advanced exactly one step per movement for M2 through M4 — completed three steps in one movement (F-271, F-255.2, D-027). The baton is now the default execution model in code. The Marianne rename landed. Instrument fallbacks shipped as a complete feature. The participation model shifted from broad (32 musicians, 100% in M4) to deep (8-12 musicians, 25-37%, doing concentrated serial work).

This is the inflection point I predicted would arrive but couldn't force: the moment when the orchestra's geometry stopped optimizing for breadth and started executing depth. The question is whether this holds into M6 or reverts.

---

## Strategic State (Multi-Domain)

### What Changed Structurally (Computational)

**1. Baton Phase 2 COMPLETE — The Critical Path Advanced**

| Step | Movement | Who | Evidence |
|------|----------|-----|----------|
| F-271 MCP explosion | M5 | Foundation + Canyon | `src/marianne/core/config/instruments.py:172-177` (mcp_disable_args) |
| F-255.2 live_states | M5 | Foundation + Canyon | `src/marianne/daemon/manager.py:2017-2038` |
| D-027 flip default | M5 | Canyon | `src/marianne/daemon/config.py:333-338` (use_baton=True) |

Three serial steps in one movement. The previous rate was one step per movement for four consecutive movements. Canyon's focused session proves the serial path CAN accelerate when a musician dedicates fully to it.

**2. Marianne Rename Phase 1 COMPLETE**

Ghost completed the most mechanically risky operation of the entire project: renaming the Python package from `marianne` to `marianne` across 326 files, updating pyproject.toml, and fixing all test imports. Zero source-level stale imports remain. Zero test failures from the rename. This is infrastructure execution at its best.

**Remaining rename work (verified):**
- CLAUDE.md: 14 `src/marianne/` references → fixed by Atlas this session
- STATUS.md: stale throughout → fixed by Atlas this session
- Config paths: `~/.marianne/` → `~/.mzt/` (not started)
- CLI command: `marianne` → `mzt` (not started)
- Docs: all references still say `marianne` (not started)
- Examples: score files reference `marianne` in comments/names (not started)
- Story: Marianne's story in README/docs (not started — Codex/Guide assigned)

**3. Instrument Fallbacks: Full Feature**

Harper delivered the complete instrument fallback pipeline: config models on JobConfig/MovementDef/SheetConfig, Sheet entity resolution (per_sheet > movement > score-level), baton dispatch logic (immediate for unavailable, after retry exhaustion for rate limits), V211 validation, status display with fallback history, observability event pipeline. Circuit added 15 adversarial tests + fallback event emission + status indicators. 50+ TDD tests total.

This is the first complete new feature to ship through the baton path. It proves the new execution model can receive features, not just run existing ones.

**4. Documentation Updated**

Codex delivered 12 documentation updates across 5 docs: D-027 baton default, F-149 backpressure rework, F-451 diagnose workspace fallback, instrument fallbacks in config reference + score-writing guide, V211 validation, disable_mcp hazard in limitations. Every M5 user-facing change has corresponding documentation.

### What the Numbers Say (Scientific)

| Metric | M4 End | M5 End | Delta |
|--------|--------|--------|-------|
| Source lines | 98,447 | 99,718 | +1,271 (+1.3%) |
| Test files | 333 | 363 | +30 |
| Tests passing | 11,397 | 11,638 | +241 |
| M5 commits | — | 17+ | — |
| Musicians contributing | 32 (100%) | 12 (37%) | -20 |
| Mateship rate | 39% | 33% | -6% |
| Quality gate | PASS | PASS (mypy clean, ruff clean) | — |

**Interpretation:** Source growth remains asymptotic (~1%/movement). Test growth is healthy (+30 files, +241 tests). Participation dropped sharply but this is NOT degradation — M5 work was concentrated serial tasks (rename, baton flip, fallbacks) that naturally engage fewer musicians deeply rather than many broadly. This is the geometry shift the critical path demanded.

### What Users Would See (Cultural)

A user encountering Marianne today would see:
1. **11,638 passing tests** across 363 files — extraordinary quality assurance
2. **Status beautification** (D-029) — musical header panels, "Now Playing" section, compact stats, relative times
3. **Instrument fallbacks** — scores can now specify fallback instruments when primary is unavailable
4. **Better diagnostics** — `mzt diagnose -w` works even when conductor can't find the job (F-451)
5. **The baton as default** — multi-instrument scores actually work now (the baton routes to the correct instrument per sheet)

But they would ALSO see:
1. **Stale `marianne` CLI** — the command hasn't been renamed to `mzt` yet
2. **Stale config paths** — `~/.marianne/` hasn't changed
3. **No demo** — still no Lovable demo or impressive hello experience
4. **Docs say `marianne` everywhere** — the rename is incomplete

### What I'm Not Seeing (Meta)

**The production conductor is lying.** Canyon's D-027 flipped the code default to `use_baton: true`, but `~/.marianne/conductor.yaml` still has `use_baton: false`. The production conductor — the one running this orchestra — is using legacy runner. Every status display, every "baton is default" claim, every "Phase 2 complete" celebration describes what the code says, not what the conductor does. The baton has never run a real production job through this orchestra.

This is exactly the integration cliff Prism has warned about for five movements. The baton has 1,500+ tests. It has never conducted a real performance.

---

## Directive Tracking

| Directive | Assignee | Status | Evidence |
|-----------|----------|--------|----------|
| D-026 | Foundation | **COMPLETE** | F-271 `instruments.py:172-177`, F-255.2 `manager.py:2017-2038` |
| D-027 | Canyon | **COMPLETE** | `config.py:333-338` default=True |
| D-028 | Guide | Not verified M5 | Wordware demos exist from M4, shipping status unclear |
| D-029 | Dash + Lens | **COMPLETE** | Status, list, conductor-status all beautified |
| D-030 | Axiom | Not verified M5 | No Axiom commit in M5 |
| D-031 | ALL | **78% (25/32)** | 7 musicians missing meditations → now 26/32 with Atlas |

### Missing Meditations (7 → 6)

Before this session: Atlas, Breakpoint, Journey, Litmus, Oracle, Sentinel, Warden
After this session: Breakpoint, Journey, Litmus, Oracle (has report but no meditation file), Sentinel, Warden

**Wait** — let me verify: Oracle committed a report (b425fe6) but collective memory doesn't mention an Oracle meditation. Let me check.

Actually: Oracle's M5 report exists at `movement-5/oracle.md`, and Bedrock's at `movement-5/bedrock.md`, and Warden's at `movement-5/warden.md` — these musicians committed reports but their meditation status needs verification against the `meditations/` directory.

Present in meditations/: adversary, axiom, bedrock, blueprint, canyon, captain, circuit, codex, compass, dash, ember, forge, foundation, ghost, guide, harper, lens, maverick, newcomer, north, prism, spark, tempo, theorem, weaver, **atlas** (new) = **26**

Missing: Breakpoint, Journey, Litmus, Oracle, Sentinel, Warden = **6**

---

## Risk Register (Updated)

### 1. CRITICAL — Integration Cliff (UNCHANGED)

The baton has never run a real production job. `conductor.yaml` still has `use_baton: false`. The code default is True but config overrides it. Five movements of warnings about this. 1,500+ tests prove components work. Zero tests prove the whole works.

**What would resolve this:** One musician, one session, `--conductor-clone` with `use_baton: true`, run hello-marianne.yaml, verify per-sheet instrument assignment works end-to-end.

### 2. HIGH — Marianne Rename Incomplete (NEW)

Phase 1 (package + imports) is done. Phases 2-5 (config paths, CLI command, docs, examples, story) are not. The project is in a split identity state — source code says `marianne`, everything else says `marianne`. Every doc, every example, every config path, every CLI invocation uses the old name. The longer this persists, the more stale references accumulate.

### 3. HIGH — Demo Vacuum (UNCHANGED — 10+ movements)

No Lovable demo. No impressive hello experience. Wordware demos exist (D-023 from M4) but haven't been "shipped" in any visible way. The product works but has zero external-facing proof.

### 4. MEDIUM — Production Conductor Config Drift

The production conductor config doesn't match code defaults. `use_baton: false` overrides the new default of `true`. Status displays and documentation describe the code state, not the running state. This creates a persistent gap between documentation and reality.

### 5. LOW — Participation Narrowing

8-12 musicians committed in M5 (down from 32 in M4). Natural for serial work, but if M6 continues concentrated, the 20+ musicians with no M5 commits may have stale context that degrades their M6 effectiveness.

---

## What Atlas Did This Movement

### Context Management (P0)
- **Fixed STATUS.md:** Completely stale since M4. Updated header (Marianne AI Compose, Phase 2 complete, 11,638 tests, 99,718 source lines), current section (M5 progress, all critical resolutions, current blockers), Key Files table (all `src/marianne/` → `src/marianne/`).
- **Fixed CLAUDE.md:** 14 stale `src/marianne/` references updated to `src/marianne/` across config models, repository organization, key files, and instrument system sections.

### Strategic Assessment (P0)
- 8th strategic alignment report (this document). Full M5 analysis across all five domains.
- Directive tracking with evidence.
- Risk register updated.
- Meditation count verification and reconciliation.

### Meditation (P1)
- Written to `meditations/atlas.md`. Theme: the map and the territory — how fresh eyes catch drift that continuity makes invisible.

### Quality Verification
```
$ python -m mypy src/ --no-error-summary
(clean — zero errors)

$ python -m ruff check src/
All checks passed!

$ pytest: 11,638 passed, 5 skipped (verified by Ghost, Bedrock)
```

---

## Files Changed

| File | Change |
|------|--------|
| `STATUS.md` | Full rewrite — M5 state, Marianne rename, 11,638 tests, key files table |
| `CLAUDE.md` | 14 `src/marianne/` → `src/marianne/` references |
| `workspaces/v1-beta-v3/meditations/atlas.md` | New — meditation |
| `workspaces/v1-beta-v3/movement-5/atlas.md` | New — this report |
| `workspaces/v1-beta-v3/memory/atlas.md` | Appended — M5 hot context |
| `workspaces/v1-beta-v3/memory/collective.md` | Appended — Atlas M5 progress |

---

## Recommendation for M6

1. **Run the baton in production.** Update `conductor.yaml` to `use_baton: true`. If something breaks, that's information. If nothing breaks, the integration cliff closes. This has been the recommendation since M2.

2. **Complete the Marianne rename.** Assign the remaining phases to specific musicians with specific deliverables: Codex for docs, Guide for examples + story, Ghost for config paths + CLI command.

3. **Ship the demo.** The Lovable demo has been P0 for 10+ movements. Either execute it or acknowledge it's not going to happen in the orchestra format and plan an alternative.

4. **Don't let context rot.** STATUS.md was stale for an entire movement. CLAUDE.md had 14 wrong file paths. These are the maps agents read at session start. Stale maps produce stale work. Someone should own context freshness as a movement-level responsibility.

---

*Down. Forward. Through.*
