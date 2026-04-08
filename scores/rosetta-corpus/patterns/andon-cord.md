## Andon Cord

`Status: Working` · **Source:** Toyota Production System stop-the-line, Expedition 1. **Scale:** adaptation. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

Replaces blind retry with diagnostic intervention. On validation failure: detect → stop (don't retry blindly) → diagnose (dedicated diagnostic sheet reads failure output) → fix (inject diagnosis as cadenza) → resume (re-run with new context). Transforms failure response from stochastic retry to deterministic diagnosis.

**Relationship to self-healing:** Marianne's conductor-level self-healing feature implements a similar detect-diagnose-fix loop. Andon Cord is the score-level pattern — you compose it explicitly in your YAML. Self-healing is the conductor-level implementation that applies automatically. Both exist at different abstraction levels.

### When to Use / When NOT to Use

Use when failures are diagnostic (agent misunderstood the task, missed a constraint), when failure output contains enough information to diagnose root cause, or when retry cost justifies a diagnostic stage (~$1+ per attempt). Not when failures are stochastic (network timeouts — just retry), failure output is empty, or diagnosis cost exceeds a few blind retries.

### Marianne Score Structure

```yaml
sheets:
  - name: generate
    prompt: "Generate the REST API implementation."
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && pytest -x 2>&1 | tee {{ workspace }}/test-output.log; exit ${PIPESTATUS[0]}"
  - name: diagnose
    prompt: |
      The previous stage failed validation. Read the failed output and test results.
      Write andon-diagnosis.md with:
      ROOT CAUSE: (what specifically went wrong)
      FIX PLAN: (concrete steps to fix)
    capture_files: ["**/*.py", "test-output.log"]
    validations:
      - type: content_contains
        content: "ROOT CAUSE:"
      - type: content_contains
        content: "FIX PLAN:"
  - name: regenerate
    prompt: "Read andon-diagnosis.md. Fix the identified issue. Do not rewrite from scratch."
    capture_files: ["andon-diagnosis.md", "**/*.py"]
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && pytest -x"
```

### Failure Mode

Diagnosis is wrong — the root cause analysis misidentifies the problem, and the fix introduces new failures. Validate that the regenerated output passes the SAME validation that the original failed. If diagnosis consistently fails, fall back to a more capable instrument for the diagnostic sheet (Opus for triage, per CEGAR Loop strategy).

### Composes With

Circuit Breaker (andon for task failure, circuit breaker for instrument failure), Quorum Trigger (quorum triggers andon), Commissioning Cascade (andon at each commissioning tier)
