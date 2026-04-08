## Screening Cascade

`Status: Working` · **Source:** Medical screening. **Forces:** Instrument-Task Fit + Finite Resources.

### Core Dynamic

Batch processing with escalating instruments at each stage. Stage 1 screens with cheap instrument, passes ambiguous cases to Stage 2 with more capable instrument, and so on. Different from Echelon Repair (which classifies upfront): Screening Cascade discovers difficulty through progressive screening.

### When to Use / When NOT to Use

Use when difficulty isn't classifiable upfront but emerges during processing. Not when all items need the same treatment.

### Marianne Score Structure

```yaml
sheets:
  - name: screen-1
    instrument: haiku
    prompt: "Process all items. Mark items you're uncertain about as ESCALATE. Write screen-1-results.yaml."
    validations:
      - type: file_exists
        path: "{{ workspace }}/screen-1-results.yaml"
  - name: screen-2
    instrument: sonnet
    prompt: "Process ESCALATE items from screen-1. Mark remaining uncertain as ESCALATE-2."
    capture_files: ["screen-1-results.yaml"]
  - name: screen-3
    instrument: opus
    prompt: "Process ESCALATE-2 items."
    capture_files: ["screen-2-results.yaml"]
```

### Failure Mode

Stage 1 escalates everything (no screening value). Validate escalation rates: if >50% escalate, the screening threshold is too conservative.

### Composes With

Echelon Repair, Immune Cascade, Dead Letter Quarantine
