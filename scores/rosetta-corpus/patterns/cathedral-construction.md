## Cathedral Construction

`Status: Working` · **Source:** Medieval cathedral building. **Forces:** Convergence Imperative + Finite Resources.

### Core Dynamic

Long-running iterative refinement where each iteration adds structural elements. Different from Fixed-Point (which converges to stability). Cathedral Construction builds toward a known target through incremental addition.

### When to Use / When NOT to Use

Use for large artifacts that can't be produced in one pass. Not when the work is convergent (use Fixed-Point).

### Marianne Score Structure

```yaml
sheets:
  - name: plan-iteration
    prompt: "Read current state. Plan what to add this iteration."
    capture_files: ["cathedral/**"]
  - name: build
    prompt: "Execute the plan. Add to the cathedral."
  - name: inspect
    prompt: "Review what was built. Write inspection-report.md."
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 20
```

### Failure Mode

Each iteration adds but never integrates. Include integration checks in the inspection stage.

### Composes With

After-Action Review, Back-Slopping, Memoization Cache
