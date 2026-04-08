## Vickrey Auction

`Status: Working (two-run approximation)` · **Source:** Vickrey auction theory. **Forces:** Instrument-Task Fit.

### Core Dynamic

Competitive probing: run the same task on multiple instruments, evaluate which performed best, use that instrument for the full run. The probing informs the NEXT run, not this one — dynamic instrument selection requires either a two-score concert or human-in-the-loop step.

### When to Use / When NOT to Use

Use when multiple instruments are available and it's unclear which performs best. Not when one instrument is clearly superior.

### Marianne Score Structure

```yaml
sheets:
  - name: probe-haiku
    instrument: haiku
    prompt: "Process the sample item. Write probe-haiku.md."
  - name: probe-sonnet
    instrument: sonnet
    prompt: "Process the same sample item. Write probe-sonnet.md."
  - name: evaluate
    prompt: "Compare probe outputs. Write instrument-recommendation.yaml: {winner, rationale}."
    capture_files: ["probe-haiku.md", "probe-sonnet.md"]
```

### Failure Mode

Probe item isn't representative of the full workload. Use multiple probe items.

### Composes With

Echelon Repair, Canary Probe
