## Systemic Acquired Resistance

`Status: Working` · **Source:** Plant immune priming (SAR/ISR), Expedition 2. **Scale:** concert-level. **Iteration:** 4. **Force:** Accumulated Signal.

### Core Dynamic

When a score recovers from a failure, it broadcasts failure-derived defenses to all subsequent scores via structured `priming/` directory. Primed scores CHANGE BEHAVIOR — adjusting prompts, validation thresholds, or monitoring. The priming is specific: a rate-limit encounter primes for rate-limit handling, not general defensiveness.

**Primer schema:** Each primer file in `priming/` follows: `{threat_type: string, trigger_signature: string, countermeasure: string, confidence: float, timestamp: string}`. Downstream scores read primers matching their threat surface and incorporate countermeasures into their prompts.

### When to Use / When NOT to Use

Use for concert campaigns where scores face related threat landscapes, when failure in one score should make the entire campaign more resilient, or when first-encounter failure cost is high. Not when scores face unrelated threats, the priming signal is too vague, or defense overhead degrades unaffected scores (autoimmune response — primers that are too broad cause unnecessary caution).

### Marianne Score Structure

```yaml
sheets:
  - name: work
    prompt: |
      Before starting, read priming/ for defense primers matching your work type.
      For each relevant primer, incorporate the countermeasure into your approach.

      Execute the primary task. Write output to output.md.

      If you encounter and recover from a failure, write a primer to priming/:
      File: priming/{threat_type}.yaml
      Schema: {threat_type, trigger_signature, countermeasure, confidence, timestamp}.
    capture_files: ["priming/*.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/output.md"
```

### Failure Mode

Primers too broad cause autoimmune response — every score wastes tokens on irrelevant defenses. Primers too narrow never match. The `trigger_signature` field is the key: specific enough to match real threats, broad enough to generalize. If primers accumulate without pruning, the priming directory becomes noise. Include a `confidence` field and prune low-confidence primers after N uses without trigger.

### Composes With

After-Action Review (primers are structured AAR output), Back-Slopping (priming IS culture inheritance across scores), Circuit Breaker (primer from circuit-tripped instrument)

---

# Communication Patterns
