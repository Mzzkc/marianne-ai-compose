## Quorum Consensus

`Status: Working` · **Source:** Distributed systems quorum. **Forces:** Partial Failure + Finite Resources.

### Core Dynamic

Accept results when a quorum (majority) of fan-out agents agree, even if some fail. N agents run; the synthesis stage proceeds when M of N produce valid output. The remaining agents' failures are logged but don't block the pipeline.

### When to Use / When NOT to Use

Use when fan-out may have partial failure and majority agreement is sufficient. Not when every agent's output is critical.

### Marianne Score Structure

```yaml
sheets:
  - name: analyze
    instances: 5
    prompt: "Analyze the artifact. Write analysis-{{ instance_id }}.md."
  - name: quorum-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "test $(ls {{ workspace }}/analysis-*.md 2>/dev/null | wc -l) -ge 3"
  - name: synthesize
    prompt: "Read available analyses. Note which are missing. Synthesize from quorum."
    capture_files: ["analysis-*.md"]
```

### Failure Mode

Quorum reached but the surviving agents all made the same error. Use Source Triangulation to ensure diversity.

### Composes With

Triage Gate, Source Triangulation, Fan-out + Synthesis
