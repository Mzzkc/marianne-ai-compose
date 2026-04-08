## Commissioning Cascade

`Status: Working` · **Source:** Marine vessel commissioning. **Forces:** Instrument-Task Fit + Exponential Defect Cost.

### Core Dynamic

Validate at multiple scopes using different tools at each level. Unit → integration → acceptance, each with scope-appropriate validation instruments. Split chained validations into separate checks so failures are diagnosable.

### When to Use / When NOT to Use

Use when different validation scopes require different tools. Not when a single validation pass suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: unit-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python -m pytest tests/unit/ -q"
  - name: integration-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python -m pytest tests/integration/ -q"
  - name: acceptance-review
    prompt: "Read test results. Write acceptance report against the original requirements."
    capture_files: ["test-results/**"]
```

### Failure Mode

Unit tests pass but integration fails — the cascade catches this. If all validation is at one level, cascading adds no value.

### Composes With

Echelon Repair, Shipyard Sequence, The Tool Chain
