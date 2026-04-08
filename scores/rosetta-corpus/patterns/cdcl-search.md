## CDCL Search

`Status: Working` · **Source:** Conflict-driven clause learning (SAT solving). **Forces:** Partial Failure + Information Asymmetry.

### Core Dynamic

When a branch fails, extract WHY it failed and add the failure reason as a new constraint. The constraint prevents the same failure pattern in subsequent iterations. Learning from failure, not just retrying.

### When to Use / When NOT to Use

Use when failures are informative and recurring patterns are likely. Not when failures are random.

### Marianne Score Structure

```yaml
sheets:
  - name: attempt
    prompt: "Read learned-clauses.yaml. Attempt the task avoiding known failure patterns."
    capture_files: ["learned-clauses.yaml"]
  - name: analyze-failure
    prompt: "If attempt failed, extract failure reason. Append to learned-clauses.yaml."
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; c=yaml.safe_load(open('{{ workspace }}/learned-clauses.yaml')); print(f'{len(c)} clauses learned')\""
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 10
```

### Failure Mode

Learned clauses are too specific (don't generalize) or too broad (over-constrain). Validate clause quality.

### Composes With

Back-Slopping, After-Action Review, CEGAR Loop
