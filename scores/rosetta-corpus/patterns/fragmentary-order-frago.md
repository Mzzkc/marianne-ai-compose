## Fragmentary Order (FRAGO)

`Status: Working` · **Source:** Military fragmentary orders. **Forces:** Partial Failure.

### Core Dynamic

Mid-execution course correction via cadenza injection. When earlier stages produce unexpected results, a FRAGO sheet writes a correction document that downstream stages read. Not replanning — targeted adjustments to the existing plan.

### When to Use / When NOT to Use

Use when plans need mid-execution adjustment based on discovered conditions. Not when the plan is too broken for incremental fixes.

### Marianne Score Structure

```yaml
sheets:
  - name: assess
    prompt: "Read outputs so far. Identify deviations from plan. Write frago.md if corrections needed."
    capture_files: ["execution-plan.md", "progress/**"]
  - name: continue
    prompt: "Read frago.md if it exists. Adjust approach per corrections."
    capture_files: ["frago.md", "execution-plan.md"]
```

### Failure Mode

FRAGO contradicts the original plan too severely. Downstream agents can't reconcile. Keep corrections incremental.

### Composes With

Read-and-React, Lines of Effort, Mission Command
