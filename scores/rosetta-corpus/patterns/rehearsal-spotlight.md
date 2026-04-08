## Rehearsal Spotlight

`Status: Working` · **Source:** Theater rehearsal. **Forces:** Convergence Imperative + Finite Resources.

### Core Dynamic

After each iteration, identify the weakest sections and re-run ONLY those. Focuses expensive iteration on the parts that need it most.

### When to Use / When NOT to Use

Use when iteration is expensive and only parts of the output need rework. Not when the whole output needs rework each time.

### Marianne Score Structure

```yaml
sheets:
  - name: evaluate
    prompt: "Read output. Score each section. Write spotlight-targets.yaml: sections needing rework."
    capture_files: ["output/**"]
  - name: rehearse
    instances: 3
    prompt: "Rework the targeted section. Write improved version."
    capture_files: ["spotlight-targets.yaml", "output/**"]
  - name: check-done
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/check_quality.py --min-score 8"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 5
```

### Failure Mode

Spotlight always targets the same sections. Track which sections have been rehearsed and escalate persistent weaknesses.

### Composes With

Echelon Repair, Soil Maturity Index, CEGAR Loop
