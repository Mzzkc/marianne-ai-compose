## Echelon Repair

`Status: Working` · **Source:** Military echelon maintenance. **Forces:** Instrument-Task Fit + Finite Resources.

### Core Dynamic

Graduated instrument assignment. Easy work goes to cheap/fast instruments. Hard work escalates to expensive/capable instruments. The classification stage determines difficulty BEFORE assignment.

### When to Use / When NOT to Use

Use when work items vary in difficulty and instruments vary in cost/capability. Not when all work is equally complex.

### Marianne Score Structure

```yaml
sheets:
  - name: classify
    instrument: haiku
    prompt: "Read each item. Classify difficulty: E1 (simple), E2 (moderate), E3 (complex). Write echelon-manifest.yaml."
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; m=yaml.safe_load(open('{{ workspace }}/echelon-manifest.yaml')); assert all(e['echelon'] in ['E1','E2','E3'] for e in m)\""
  - name: e1-repair
    instrument: haiku
    prompt: "Process E1 items from echelon-manifest.yaml."
    capture_files: ["echelon-manifest.yaml"]
  - name: e2-repair
    instrument: sonnet
    prompt: "Process E2 items."
    capture_files: ["echelon-manifest.yaml"]
  - name: e3-repair
    instrument: opus
    prompt: "Process E3 items."
    capture_files: ["echelon-manifest.yaml"]
```

### Failure Mode

Misclassification: E3 items assigned to E1. Validate E1 output quality; escalate failures to E2.

### Composes With

Commissioning Cascade, Fermentation Relay, Screening Cascade, Circuit Breaker
