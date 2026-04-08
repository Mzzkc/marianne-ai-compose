## Delphi Convergence

`Status: Working` · **Source:** Delphi method (RAND Corporation). **Forces:** Convergence Imperative + Information Asymmetry.

### Core Dynamic

Multiple agents independently assess, then converge through structured rounds. Different from Fan-out + Synthesis (one round). Delphi iterates until convergence — each round shares anonymized prior assessments, allowing agents to update positions.

### When to Use / When NOT to Use

Use when independent expert judgment needs convergence. Not when a single assessment suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: assess
    instances: 3
    prompt: "Read prior round results if they exist. Write your independent assessment."
    capture_files: ["round-*/**"]
  - name: check-convergence
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/check_convergence.py --threshold 0.8"
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 5
```

### Failure Mode

Agents anchor on first-round assessments and never genuinely update. Validate that positions actually change between rounds.

### Composes With

Source Triangulation, Rashomon Gate
