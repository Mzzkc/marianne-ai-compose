## Triage Gate

`Status: Working` · **Source:** Emergency medicine START protocol, military command. **Forces:** Finite Resources + Partial Failure.

### Core Dynamic

Coarse classification before expensive processing. A fast classifier reads fan-out outputs and routes: **RED** (forward to synthesis), **YELLOW** (rework with targeted prompt), **GREEN** (supplementary), **BLACK** (discard with logged reason). Structural checks first (schema compliance, required sections, word count), then semantic if needed. This is the convergence ranked #1 across all domains.

### When to Use / When NOT to Use

Use when fan-out produces mixed quality, downstream processing is expensive, and structural quality checks are definable. Not when all outputs must be incorporated or fan-out is narrow (2-3 agents).

### Marianne Score Structure

```yaml
sheets:
  - name: triage
    prompt: >
      Read each output in fan-out-results/. For each, write a line in triage-manifest.yaml:
      {id, category: RED|YELLOW|GREEN|BLACK, reason, rework_prompt}.
      Use structural checks first: required sections present, word count > 200.
    capture_files: ["fan-out-results/*.md"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; m=yaml.safe_load(open('{{ workspace }}/triage-manifest.yaml')); assert all(e['category'] in ['RED','YELLOW','GREEN','BLACK'] for e in m)\""
```

### Failure Mode

If YELLOW count is 0, the rework stage still executes but produces nothing — guard with a Read-and-React conditional. If everything is BLACK, synthesis gets no inputs; the score should fail explicitly.

### Composes With

Immune Cascade, Fan-out + Synthesis, Relay Zone
