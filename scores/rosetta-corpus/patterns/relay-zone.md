## Relay Zone

`Status: Working` · **Source:** Track relay (athletics). **Forces:** Producer-Consumer Mismatch.

### Core Dynamic

Context compression between pipeline stages. A dedicated relay sheet reads the full output of the previous stage and produces a compressed summary for the next stage. Prevents context window bloat across long pipelines.

### When to Use / When NOT to Use

Use when cumulative outputs exceed context limits. Not when all information must survive compression.

### Marianne Score Structure

```yaml
sheets:
  - name: relay
    prompt: >
      Read all prior outputs. Compress to relay-brief.md:
      key findings, open questions, critical data only. Target 20% of original size.
    capture_files: ["full-output/**"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/relay-brief.md"
      - type: command_succeeds
        command: "test $(wc -w < '{{ workspace }}/relay-brief.md') -lt 2000"
```

### Failure Mode

Relay loses critical information. Downstream stages produce incorrect results because the relay omitted a key finding. Validate relay completeness by checking key terms survive compression.

### Composes With

Fan-out + Synthesis, Forward Observer, Screening Cascade
