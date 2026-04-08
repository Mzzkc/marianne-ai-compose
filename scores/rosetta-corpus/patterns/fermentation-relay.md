## Fermentation Relay

`Status: Working` · **Source:** Fermentation microbiology. **Forces:** Instrument-Task Fit.

### Core Dynamic

Cheap instruments do initial processing; expensive instruments refine. The pipeline is fixed in YAML. "Substrate-driven" refers to how you design the gate between stages, not runtime switching.

### When to Use / When NOT to Use

Use when early stages benefit from fast/cheap processing and later stages need precision. Not when all stages need the same capability.

### Marianne Score Structure

```yaml
sheets:
  - name: extract
    instrument: haiku
    prompt: "Extract raw information. Write extraction.md."
    validations:
      - type: file_exists
        path: "{{ workspace }}/extraction.md"
  - name: refine
    instrument: sonnet
    prompt: "Refine extraction. Resolve ambiguities."
    capture_files: ["extraction.md"]
  - name: polish
    instrument: opus
    prompt: "Final quality pass. Produce polished output."
    capture_files: ["refined.md"]
```

### Failure Mode

Early cheap stages produce such poor output that expensive stages spend all their budget fixing garbage. Validate intermediate quality.

### Composes With

Echelon Repair, Succession Pipeline, Screening Cascade
