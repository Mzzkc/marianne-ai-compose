## Mission Command

`Status: Working` · **Source:** Auftragstaktik (Prussian military doctrine). **Forces:** Information Asymmetry.

### Core Dynamic

Separate "what and why" (centralized) from "how" (decentralized). The intent envelope has three layers: **purpose** (why), **key tasks** (what), **end state** (what done looks like). Agents adapt freely within the decision space. Validate end-state achievement, never method compliance. The structural distinction: Mission Command scores have a *specific, named intent document* that replaces per-agent context acquisition, plus end-state-only validation.

### When to Use / When NOT to Use

Use when tasks require agent judgment and conditions may differ from expectations. Not for mechanical tasks or constraints so tight only one approach is valid.

### Marianne Score Structure

```yaml
sheets:
  - name: mission-brief
    prompt: >
      Write mission-brief.md with three sections:
      PURPOSE: why this refactoring matters.
      KEY TASKS: the 4 modules that must be decoupled.
      END STATE: all 340 tests pass, public API unchanged, coupling metric < 0.3.
    validations:
      - type: content_contains
        content: "PURPOSE:"
      - type: content_contains
        content: "END STATE:"
  - name: execute
    instances: 4
    prompt: "Read mission-brief.md. Decouple module {{ instance_id }}."
    capture_files: ["mission-brief.md"]
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python -m pytest --tb=no -q"
```

### Failure Mode

Intent briefs too vague produce incoherent decisions. Too specific collapses the decision space. The end state must be testable with `command_succeeds`.

### Composes With

After-Action Review, Barn Raising, Prefabrication
