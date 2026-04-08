## Back-Slopping (Learning Inheritance)

`Status: Working` · **Source:** Sourdough bread making. **Forces:** Convergence Imperative.

### Core Dynamic

Each iteration inherits a "culture" artifact from the previous iteration containing accumulated learning. The culture grows and refines over iterations, carrying forward what worked and what to avoid.

### When to Use / When NOT to Use

Use when later iterations should benefit from earlier learning. Not when each iteration is independent.

### Marianne Score Structure

```yaml
sheets:
  - name: work
    prompt: "Read culture.yaml for accumulated learning. Do the work. Update culture.yaml with new insights."
    capture_files: ["culture.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/culture.yaml"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Culture grows without pruning. Old lessons that no longer apply accumulate. Include a pruning step that removes stale entries.

### Composes With

Cathedral Construction, CDCL Search, Systemic Acquired Resistance
