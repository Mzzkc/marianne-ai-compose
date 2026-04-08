## Closed-Loop Call

`Status: Working` · **Source:** Aviation CRM callout-response protocol. **Forces:** Producer-Consumer Mismatch + Partial Failure.

### Core Dynamic

Explicit handoff verification between stages. Stage A produces output. Stage B reads it and writes back a confirmation of what it understood. A CLI validation compares the two. Prevents semantic drift across pipeline stages.

### When to Use / When NOT to Use

Use when handoff fidelity is critical and semantic drift is a real risk. Not when stages are trivially compatible.

### Marianne Score Structure

```yaml
sheets:
  - name: produce
    prompt: "Write output with manifest.yaml listing key decisions."
  - name: consume
    prompt: "Read output. Write readback.yaml confirming your understanding of each decision."
    capture_files: ["manifest.yaml", "output/**"]
  - name: verify
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; m=yaml.safe_load(open('{{ workspace }}/manifest.yaml')); r=yaml.safe_load(open('{{ workspace }}/readback.yaml')); assert set(m.keys())==set(r.keys()), f'Key mismatch: {set(m.keys())-set(r.keys())}'\""
```

### Failure Mode

Readback is verbatim copy, not comprehension check. The validation should check structural understanding, not string matching.

### Composes With

Relay Zone, Prefabrication, Succession Pipeline
