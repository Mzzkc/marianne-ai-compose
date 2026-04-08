## Forward Observer

`Status: Working` · **Source:** Military forward observation. **Forces:** Finite Resources + Information Asymmetry.

### Core Dynamic

A cheap, fast observer (instrument: haiku or sonnet) reads large input and produces a compressed brief for the expensive operator (instrument: opus). Reduces context window pressure and cost. The observer cost must save more tokens downstream than it consumes.

### When to Use / When NOT to Use

Use when input is too large for the main instrument or when cheap summarization preserves actionable information. Not when all information is critical.

### Marianne Score Structure

```yaml
sheets:
  - name: observe
    instrument: haiku
    prompt: "Read the full input. Write observer-brief.md: key findings, actionable items only."
    validations:
      - type: file_exists
        path: "{{ workspace }}/observer-brief.md"
  - name: operate
    instrument: opus
    prompt: "Read observer-brief.md. Execute the detailed analysis."
    capture_files: ["observer-brief.md"]
```

### Failure Mode

Observer discards critical information. Validate by checking brief covers all major topics from the input.

### Composes With

Relay Zone, Screening Cascade, Immune Cascade
