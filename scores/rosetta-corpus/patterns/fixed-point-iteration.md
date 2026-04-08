## Fixed-Point Iteration

`Status: Working` · **Source:** Numerical analysis, compiler dataflow. **Forces:** Convergence Imperative.

### Core Dynamic

Repeat the same operation until the output stops changing. Convergence is structural: diff the output of iteration N against iteration N-1. When the diff is empty (or below threshold), stop.

### When to Use / When NOT to Use

Use when the task naturally converges (each pass finds fewer issues). Not when convergence isn't guaranteed.

### Marianne Score Structure

```yaml
sheets:
  - name: iterate
    prompt: "Read previous output. Improve. Write output."
    capture_files: ["output.md"]
  - name: convergence-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "diff {{ workspace }}/output-prev.md {{ workspace }}/output.md | wc -l | xargs test 5 -gt"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Never converges. `max_chain_depth` provides the safety bound.

### Composes With

CDCL Search, Cathedral Construction, Memoization Cache
