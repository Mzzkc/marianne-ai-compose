# Opus Convergence Analysis

> **Internal Reference** — This is a meta-analysis document, not user documentation.

**Date:** 2026-01-16
**Analysis:** Patterns to converge between Marianne Opus (v20, 20 cycles) and RLF Opus (v15, 15 cycles)

---

## Executive Summary

Two independent self-improving opus scores have evolved in parallel:
- **Marianne Opus** (v20): Orchestration framework evolution, 9-movement structure
- **RLF Opus** (v15): HTTP API wiring evolution, 5-sheet structure

Both have independently discovered similar patterns, validating their utility. This document identifies patterns to converge, creating stronger scores on both sides.

---

## Patterns FROM RLF → Marianne

### 1. Proactive Discovery Mode ⭐ HIGH VALUE

**RLF Pattern:**
```yaml
proactive_mode:
  evaluation_cycle_threshold: 3  # After 3 consecutive eval cycles
  graduated_model:
    cycle_3: "LIGHT PROACTIVE"   # ONE small improvement
    cycle_4_plus: "FULL PROACTIVE"  # Multiple improvements

  light_proactive_options:
    A: "Error Message Review - audit 3-5 error paths"
    B: "Performance Spot Check - profile ONE endpoint"
    C: "Test Gap Assessment - identify ONE untested edge case"
    D: "Documentation Audit - review ONE public API doc"
```

**Why Marianne needs this:**
- Marianne has no mechanism to prevent idle cycles
- When no high-CV candidates emerge, Marianne should still improve
- Proactive discovery provides bounded, safe improvement work

**Integration approach:**
- Add to Sheet 1 (External Discovery) phase
- Check for consecutive evaluation cycles in header
- If threshold reached, mandate ONE proactive task from options

---

### 2. Variance Tier Model ⭐ HIGH VALUE

**RLF Pattern:**
```yaml
variance_tiers:
  TIGHT:
    expected_variance: "5-50%"
    task_types: ["pure delegation", "simple wiring"]

  TIGHT_MODERATE:
    expected_variance: "10-50%"
    task_types: ["light transformation", "DTO reuse"]

  MODERATE:
    expected_variance: "25-100%"
    task_types: ["manual parsing", "new DTOs"]

  LOOSE:
    expected_variance: "50-200%"
    task_types: ["embedded logic", "auth/branching"]
```

**Why Marianne needs this:**
- Marianne tracks LOC accuracy but doesn't set expectations by task type
- Creates unrealistic pressure for high accuracy on complex tasks
- Tier-based approach acknowledges irreducible variance

**Integration approach:**
- Add variance tier classification to Sheet 5 (Specification)
- Use tier to set pass/fail criteria in Sheet 7 (Validation)
- Track variance within tier as success metric (not raw accuracy)

---

### 3. Task Type CV Modifiers ⭐ MEDIUM VALUE

**RLF Pattern:**
```yaml
task_type_cv_modifiers:
  wiring: +0.05        # Infrastructure exists
  bug_fix: +0.05       # Regression prevention
  refactoring: +0.03   # No new features
  documentation: +0.10 # No code changes
  proactive_discovery: +0.03  # Polish work
  true_integration: -0.05     # Cascading changes
```

**Why Marianne needs this:**
- Marianne calculates CV without task type adjustment
- Documentation improvements have artificially low CV
- Maintenance work gets undervalued relative to features

**Integration approach:**
- Add to CV calculation in Sheet 3 (TDF Synthesis)
- Apply AFTER domain/boundary average calculation
- Document modifier applied in synthesis output

---

### 4. Task Type LOC Multipliers ⭐ MEDIUM VALUE

**RLF Pattern:**
```yaml
task_type_loc_multipliers:
  wiring:
    impl: "× 0.8"  # Infrastructure exists
    test: "× 1.0"

  proactive_discovery:
    impl: "× 0.8"  # Polish work is small
    test: "× 0.5"  # May not need extensive tests

  green_field:
    impl: "× 1.35"
    test: "× 1.5"

  true_integration:
    impl: "× 1.5"
    test: "× 2.0"
```

**Why Marianne needs this:**
- Marianne applies multipliers inconsistently
- Proactive/maintenance work is systematically overestimated
- Clear multipliers enable accurate planning

**Integration approach:**
- Add to Sheet 5 (Specification) LOC formulas
- Apply based on task classification from Sheet 3

---

### 5. Stable Deferral Threshold ⭐ MEDIUM VALUE

**RLF Pattern:**
```yaml
stable_deferral:
  threshold_cycles: 10  # After 10 cycles deferred
  behavior: "STOP RE-EVALUATING"
  rationale: |
    If something has been deferred 10+ cycles without user friction,
    it's not actually needed. Stop wasting discovery cycles on it.

  reactivation_triggers:
    - explicit_user_request
    - new_user_friction_evidence
    - product_decision_made
```

**Why Marianne needs this:**
- Marianne re-evaluates deferred candidates every cycle
- Wastes discovery effort on perpetually low-priority items
- Stable deferrals should be marked as CLOSED

**Integration approach:**
- Add deferral cycle counter to research candidates
- At 10+ cycles, mark as STABLE_DEFERRAL
- Only re-evaluate if explicit trigger

---

### 6. Test Coverage Multiplier for Enum Complexity ⭐ MEDIUM VALUE

**RLF Pattern:**
```yaml
test_coverage_multiplier:
  simple_types: 1.0
    # Single struct request/response

  single_enum: 1.5
    # One enum type in request/response

  multi_variant_enum: 2.5
    # Enum with 4+ variants - each needs test

  nested_enum: 3.0
    # Recursive/nested enum variants
```

**Why Marianne needs this:**
- Marianne's test LOC formulas miss enum complexity
- Multi-variant types need per-variant test coverage
- Systematic underestimation when enums involved

**Integration approach:**
- Add enum detection to Sheet 5 (Specification)
- Apply multiplier to test LOC calculation
- Document detected enum complexity

---

### 7. LOC Accuracy Trend Tracking ⭐ LOW VALUE (already partial)

**RLF Pattern:**
```yaml
# Tracked in opus header
LOC_ACCURACY_TREND:
  v4: "23% variance (baseline)"
  v5: "52% variance (handler type + patterns) <- BIG WIN"
  v6: "52% variance (plateau)"
  v7: "37.8% variance (REGRESSION - soft LOC noise)"
  v8: "6.6% variance (2-step model validated)"
  # ...
```

**Why Marianne might benefit:**
- Visualizes LOC model evolution over time
- Identifies regression points in estimation accuracy
- Marianne tracks accuracy but doesn't plot trend

**Integration approach:**
- Add trend section to opus header
- Update each cycle with variance achieved
- Flag regressions for formula adjustment

---

## Patterns FROM Marianne → RLF

### 1. Pattern Trust Scoring ⭐ HIGH VALUE

**Marianne Pattern:**
```yaml
pattern_trust:
  formula: "base + quarantine_penalty + validation_bonus + age_factor + effectiveness_modifier"
  range: [0, 1]

  relevance_adjustments:
    quarantined: "-0.3 score penalty"
    high_trust: "+0.1 to +0.2 bonus"
    validated: "+0.05 bonus"
```

**Why RLF could benefit:**
- RLF doesn't track pattern effectiveness over time
- Patterns discovered in early cycles may become stale
- Trust scoring enables selective pattern application

---

### 2. Synergy Pair Validation ⭐ HIGH VALUE

**Marianne Pattern:**
```yaml
synergy_pair:
  criteria: "Both candidates address same problem space"
  implementation_order: "Foundation first, dependent second"
  combined_cv: "Average of pair CVs"

  example:
    pair: ["Pattern Quarantine", "Pattern Trust"]
    shared_space: "Safe autonomous learning"
    order: "Quarantine first (provides status), Trust second (uses status)"
```

**Why RLF could benefit:**
- RLF implements single candidates per cycle
- Related features could be paired for coherent evolution
- Reduces context switching between cycles

---

### 3. TDF Quadruplet Analysis ⭐ MEDIUM VALUE

**Marianne Pattern:**
- Marianne uses full TDF (COMP, SCI, CULT, EXP, META)
- Boundary analysis between all domain pairs
- Recognition level assessment (P0-P5)

**Why RLF could benefit:**
- RLF uses simplified TDF without META domain
- No explicit recognition level tracking
- Full TDF could improve candidate selection

---

## Shared Patterns (Independently Discovered)

Both opus scores independently discovered these patterns, validating their importance:

1. **Coverage Gates** (Marianne: ≥80% new code, RLF: >70% coverage)
2. **Code Review During Implementation** (both track early catch ratio)
3. **Evaluation Cycles** (both allow cycles without implementation)
4. **Multi-Tier LOC Estimation** (both use multipliers for complexity)
5. **Fixture Catalog Tracking** (both maintain test fixture inventory)

---

## Integration Priority

| Pattern | From | To | Priority | Complexity | Value |
|---------|------|----|---------:|------------|-------|
| Proactive Discovery Mode | RLF | Marianne | 1 | Medium | High |
| Variance Tier Model | RLF | Marianne | 2 | Low | High |
| Task Type CV Modifiers | RLF | Marianne | 3 | Low | Medium |
| Task Type LOC Multipliers | RLF | Marianne | 4 | Low | Medium |
| Stable Deferral Threshold | RLF | Marianne | 5 | Low | Medium |
| Test Coverage (Enum) | RLF | Marianne | 6 | Medium | Medium |
| Pattern Trust Scoring | Marianne | RLF | 7 | High | High |
| Synergy Pair Validation | Marianne | RLF | 8 | Medium | High |
| TDF Quadruplet | Marianne | RLF | 9 | High | Medium |

---

## Recommended Convergence Strategy

### Phase 1: Quick Wins (v21)
- Add Task Type CV Modifiers to Marianne
- Add Variance Tier Model to Marianne
- Add Stable Deferral Threshold to Marianne

### Phase 2: Structural (v22)
- Add Proactive Discovery Mode to Marianne
- Add Task Type LOC Multipliers to Marianne
- Add Test Coverage (Enum) Multiplier to Marianne

### Phase 3: Cross-Pollination (v23+)
- Port Pattern Trust Scoring to RLF
- Port Synergy Pair Validation to RLF
- Consider unified opus template

---

## Next Steps

1. Create `marianne-opus-convergence.yaml` score to implement convergence
2. Create `score-creation-skill.md` documenting patterns from both
3. Update example scores with lessons learned
