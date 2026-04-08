## Speculative Hedge

`Status: Working` · **Source:** CPU branch prediction, military COA analysis, financial hedging, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Run DIFFERENT strategies on the SAME problem and commit to whichever succeeds. Not fan-out (same task, different data) — this runs different APPROACHES on the same data. The cost analysis: if retry-from-scratch costs more than running both, hedge.

**Execution note:** In current Marianne, approaches run sequentially (sheets execute in order). This means the delivery time is the SUM of both approaches, not the MAX. The value proposition is not time savings but elimination of the "wrong approach, start over" scenario — you always get at least one valid result. For true parallel hedging, use two separate scores in a concert.

### When to Use / When NOT to Use

Use for migration tasks with unknown edge cases, research with multiple search strategies, any task where "wrong approach, retry" costs more than "both approaches, discard one." Not when both approaches are equally expensive and success rate is high, when budget is hard-capped, or when approaches interfere.

### Marianne Score Structure

```yaml
sheets:
  - name: analyze
    prompt: "Analyze the problem. Define two approaches and evaluation criteria. Write hedge-plan.yaml."
  - name: approach-a
    prompt: "Execute approach A: mechanical transformation. Write all output to approach-a-result/."
  - name: approach-b
    prompt: "Execute approach B: clean-room rewrite guided by tests. Write all output to approach-b-result/."
    capture_files: ["hedge-plan.yaml"]
  - name: evaluate
    prompt: "Run tests against both. Write hedge-decision.yaml: {winner, rationale, test_results}."
    capture_files: ["approach-a-result/**", "approach-b-result/**"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; d=yaml.safe_load(open('{{ workspace }}/hedge-decision.yaml')); assert 'winner' in d\""
```

### Failure Mode

Both approaches fail — the hedge didn't reduce risk, it doubled cost. Mitigate with a Canary Probe on each approach before full execution. If approaches write to the same files (no subdirectory isolation), they clobber each other's output — always use separate output directories.

### Composes With

Wargame Table (wargame before hedging to reduce approaches), Canary Probe (canary each approach before full hedge)
