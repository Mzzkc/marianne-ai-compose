## Lines of Effort

`Status: Working (single-score approximation)` · **Source:** Military operational design (JP 5-0). **Forces:** Information Asymmetry + Finite Resources.

### Core Dynamic

Sustained parallel campaigns with different objectives converging toward a unified end state. Each line has its own scores, instruments, and success criteria. Coordination through shared workspace state, not message passing. Requires concert-level orchestration with multiple scores.

### When to Use / When NOT to Use

Use for large campaigns with distinct workstreams that must converge. Not when workstreams are independent or campaign is short.

### Marianne Score Structure

```yaml
# Single-score approximation — true Lines of Effort requires a concert
sheets:
  - name: define-lines
    prompt: "Define 3 lines of effort with objectives and convergence criteria."
  - name: line-work
    instances: 3
    prompt: "Execute line {{ instance_id }} per the defined objectives."
    capture_files: ["lines-definition.md"]
  - name: convergence-check
    prompt: "Read all line outputs. Assess convergence toward unified end state."
    capture_files: ["line-*/**"]
```

### Failure Mode

Lines diverge without convergence checks. Regular synchronization points are essential.

### Composes With

Season Bible, After-Action Review, Barn Raising
