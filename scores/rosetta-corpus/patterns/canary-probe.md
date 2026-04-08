## Canary Probe

`Status: Working` · **Source:** DevOps canary deployment, military recon-in-force, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Run a miniature version of the full pipeline on a tiny subset of real data before committing to full scale. The canary uses the EXACT SAME pipeline — identical instruments, validations, prompts — just on fewer items. If the canary dies, you've lost almost nothing. If it lives, you have evidence (not hope) that full-scale execution works.

**Representativeness caveat:** Canary testing's fundamental limitation is that the subset must be representative. If it isn't, you learn nothing. The selection stage should use structural diversity criteria (different file sizes, different formats, edge cases), not random sampling.

### When to Use / When NOT to Use

Use for any score operating on a list of items, migration scores, batch processing, or concert coordination where Score B depends on Score A's output format. Not when the canary subset can't be representative (tail-risk failures) or setup cost makes a probe nearly as expensive as the full run.

### Marianne Score Structure

```yaml
sheets:
  - name: select-canary
    prompt: >
      Select 3 representative items from the full set, choosing for structural diversity
      (different sizes, formats, edge cases). Write canary-manifest.yaml listing selected items.
    validations:
      - type: file_exists
        path: "{{ workspace }}/canary-manifest.yaml"
  - name: canary-run
    instances: 3
    prompt: >
      Read canary-manifest.yaml. Process item at index {{ instance_id }}.
      Write result to canary-result-{{ instance_id }}.md.
    capture_files: ["canary-manifest.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/canary-result-{{ instance_id }}.md"
  - name: canary-evaluate
    prompt: >
      Read all canary results. Evaluate: did each produce valid output?
      Write canary-verdict.yaml: {go: true/false, results: [{item, pass, reason}]}.
    capture_files: ["canary-result-*.md", "canary-manifest.yaml"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; v=yaml.safe_load(open('{{ workspace }}/canary-verdict.yaml')); assert 'go' in v\""
  - name: canary-gate
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; v=yaml.safe_load(open('{{ workspace }}/canary-verdict.yaml')); assert v['go'], 'Canary failed'\""
  - name: full-run
    instances: 20
    prompt: "Process remaining items from the full set."
    capture_files: ["canary-manifest.yaml"]
```

### Failure Mode

Canary passes but full run fails — the canary subset was unrepresentative. Mitigate by selecting for structural diversity, not convenience. If the canary itself is expensive (complex setup), the pattern provides no cost advantage — use a simpler validation gate instead.

### Composes With

Progressive Rollout (canary IS phase 1), Dead Letter Quarantine (canary failures reveal quarantine candidates), Speculative Hedge (canary each hedge path before committing)
