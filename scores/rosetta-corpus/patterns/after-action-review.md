## After-Action Review

`Status: Working` · **Source:** US Army AAR protocol. **Forces:** Information Asymmetry + Partial Failure.

### Core Dynamic

Dedicated review stage after execution. Not quality checking (that's validation). AAR asks: what was supposed to happen, what actually happened, why the difference, what to change. The AAR output feeds the next iteration's prelude.

### When to Use / When NOT to Use

Use after any significant execution to capture learning. Not for trivial tasks.

### Marianne Score Structure

```yaml
sheets:
  - name: aar
    prompt: >
      Read all execution outputs. Write aar.md:
      INTENDED: what the score was supposed to produce.
      ACTUAL: what was actually produced.
      DELTA: why the difference.
      SUSTAIN: what worked.
      IMPROVE: what to change next time.
    capture_files: ["**"]
    validations:
      - type: content_contains
        content: "SUSTAIN:"
      - type: content_contains
        content: "IMPROVE:"
```

### Failure Mode

AAR is generic platitudes. Validate specific references to actual outputs and concrete improvement recommendations.

### Composes With

Immune Cascade, Cathedral Construction, Back-Slopping
