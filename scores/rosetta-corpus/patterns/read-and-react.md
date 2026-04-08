## Read-and-React

`Status: Working` · **Source:** Basketball read-and-react offense. **Forces:** Partial Failure + Information Asymmetry.

### Core Dynamic

Downstream stages read workspace state and adapt their behavior. Not conditional branching (which requires conductor support) but workspace-driven behavioral adaptation within a sheet's prompt.

### When to Use / When NOT to Use

Use when downstream behavior should adapt to upstream results. Not when the adaptation path is known upfront.

### Marianne Score Structure

```yaml
sheets:
  - name: work
    prompt: >
      Read previous outputs. Based on what you find:
      - If analysis-complete.yaml exists: proceed to synthesis.
      - If analysis-complete.yaml is missing: extend analysis first.
    capture_files: ["analysis-*.md", "analysis-complete.yaml"]
```

### Failure Mode

Agent ignores the workspace state and proceeds with default behavior. Validate that the expected adaptation actually occurred.

### Composes With

Triage Gate, FRAGO, Dormancy Gate
