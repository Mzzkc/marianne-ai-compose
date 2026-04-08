## Soil Maturity Index

`Status: Working` · **Source:** Soil science maturity metrics. **Forces:** Convergence Imperative.

### Core Dynamic

Domain-specific termination condition for iterative processes. Instead of "nothing changed" (Fixed-Point) or "all sections pass" (Rehearsal Spotlight), the maturity index measures a qualitative shift — the output has changed CHARACTER, not just improved. A script-driven exit code determines termination.

### When to Use / When NOT to Use

Use when convergence is qualitative (the writing style matured, the architecture became cohesive). Not when convergence is structural.

### Marianne Score Structure

```yaml
sheets:
  - name: iterate
    prompt: "Read output. Improve based on maturity criteria."
    capture_files: ["output/**"]
  - name: maturity-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/maturity_assessor.py --output {{ workspace }}/output.md"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Maturity metric doesn't capture the intended qualitative shift. Iterate on the assessor, not just the output.

### Composes With

Fixed-Point Iteration, Back-Slopping, Delphi Convergence
