## Reconnaissance Pull

`Status: Working` · **Source:** Military reconnaissance doctrine. **Forces:** Information Asymmetry.

### Core Dynamic

A cheap, fast reconnaissance stage discovers the landscape before committing to a plan. The recon output is advisory — downstream stages read it and adapt. Different from Forward Observer (which compresses). Reconnaissance discovers.

### When to Use / When NOT to Use

Use when the approach isn't obvious and exploration is cheap. Not when the task is well-understood.

### Marianne Score Structure

```yaml
sheets:
  - name: recon
    instrument: sonnet
    prompt: "Survey the input. Write recon-report.md: structure, complexity, risks, recommended approach."
    validations:
      - type: file_exists
        path: "{{ workspace }}/recon-report.md"
  - name: plan
    prompt: "Read recon-report.md. Write execution plan."
    capture_files: ["recon-report.md"]
  - name: execute
    prompt: "Execute per plan."
    capture_files: ["execution-plan.md"]
```

### Failure Mode

Recon is too shallow to inform planning. Use a more capable instrument for recon if the domain is complex.

### Composes With

Mission Command, Canary Probe
