## Dormancy Gate

`Status: Working` · **Source:** Seed dormancy in botany. **Forces:** Finite Resources.

### Core Dynamic

A gate that waits for external conditions before proceeding. The gate checks workspace state — if conditions aren't met, the score self-chains and checks again. Unlike a validation (which fails the score), dormancy gates pause and retry.

### When to Use / When NOT to Use

Use when work depends on external conditions that will eventually be met. Not when conditions are already known.

### Marianne Score Structure

```yaml
sheets:
  - name: check-conditions
    instrument: cli
    validations:
      - type: command_succeeds
        command: "test -f {{ workspace }}/external-data-ready.flag"
  - name: proceed
    prompt: "Conditions met. Begin processing."
    capture_files: ["external-data/**"]
```

### Failure Mode

External condition never materializes. `max_chain_depth` provides a safety bound.

### Composes With

Read-and-React, Shipyard Sequence
