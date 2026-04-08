## Dead Letter Quarantine

`Status: Working` · **Source:** RabbitMQ/Kafka dead letter queues, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

After N retries, STOP RETRYING AND QUARANTINE. Move failed items to a separate processing path with different handling: different instruments, different prompts, different strategy. The quarantine is an ARTIFACT that persists, accumulates, and can be ANALYZED. "Why did these 7 items fail?" often reveals a systematic issue that fixing once clears the entire quarantine.

### When to Use / When NOT to Use

Use for any batch processing where some items are expected to fail, self-chaining scores where iteration N should not re-attempt items from N-1, or concert-level routing of failures to a different score. Not when every item MUST succeed, failures are truly random, or the quarantine grows to dwarf successful items (the pipeline itself is broken).

### Marianne Score Structure

```yaml
sheets:
  - name: process
    instances: 10
    prompt: "Process item {{ instance_id }}. Write result-{{ instance_id }}.md on success."
  - name: collect
    prompt: >
      Identify failures (missing or empty result files). Write quarantine.yaml listing
      failed items with {item_id, error_symptom, attempted_strategy}.
    capture_files: ["result-*.md"]
  - name: analyze-quarantine
    prompt: >
      Read quarantine.yaml. Identify common failure patterns.
      Write quarantine-analysis.md with: {pattern, affected_items, suggested_strategy}.
    capture_files: ["quarantine.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/quarantine-analysis.md"
  - name: reprocess
    prompt: >
      Read quarantine-analysis.md. For each failure pattern, apply the suggested strategy.
      Write reprocess-results.yaml: [{item_id, outcome: success|permanent_quarantine, detail}].
    capture_files: ["quarantine.yaml", "quarantine-analysis.md"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; r=yaml.safe_load(open('{{ workspace }}/reprocess-results.yaml')); success=[e for e in r if e['outcome']=='success']; print(f'{len(success)}/{len(r)} reprocessed successfully')\""
```

### Failure Mode

Quarantine analysis finds no patterns — items failed for unrelated reasons. The reprocess stage still runs but the "adapted strategy" has nothing to adapt from. In this case, escalate to a more capable instrument (Opus) rather than repeating the same strategy. If the quarantine grows across self-chain iterations, the pipeline itself needs debugging, not the items.

### Composes With

Triage Gate (BLACK category feeds quarantine), Screening Cascade (rejected items go to quarantine for pattern analysis), Circuit Breaker (circuit-tripped failures enter quarantine)
