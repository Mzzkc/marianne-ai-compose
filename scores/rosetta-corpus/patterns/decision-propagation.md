## Decision Propagation

`Status: Working` · **Source:** Constraint satisfaction (renamed from Arc Consistency Propagation). **Forces:** Information Asymmetry.

### Core Dynamic

When a sheet makes a decision that constrains downstream sheets, it writes a structured constraint brief rather than embedding the decision in prose. The brief has: decision, rationale, implications, and constraints-for-downstream. Each downstream sheet reads the brief and acknowledges which constraints it incorporated. Writing the brief requires judgment — the agent must identify which decisions are load-bearing.

### When to Use / When NOT to Use

Use when decisions in early stages have compounding effects. Not when stages are independent or constraints are simple enough for the prompt alone.

### Marianne Score Structure

```yaml
sheets:
  - name: decide
    prompt: >
      Make the architecture decision. Write constraint-brief.yaml:
      {decision, rationale, implications: [], constraints_for_downstream: []}.
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; b=yaml.safe_load(open('{{ workspace }}/constraint-brief.yaml')); assert 'constraints_for_downstream' in b\""
  - name: implement
    instances: 4
    prompt: "Read constraint-brief.yaml. Build component {{ instance_id }}. Acknowledge constraints."
    capture_files: ["constraint-brief.yaml"]
```

### Failure Mode

Constraint briefs too abstract to constrain. The brief should name specific artifacts and interfaces, not just abstract goals. Validate with `command_succeeds` checking brief has concrete entries.

### Composes With

CDCL Search, CEGAR Loop, Commander's Intent Envelope
