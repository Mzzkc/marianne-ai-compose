## Composting Cascade

`Status: Working` · **Source:** Four-phase composting microbiology, Expedition 2. **Scale:** score-level + instrument strategy. **Iteration:** 4. **Force:** Threshold Accumulation.

### Core Dynamic

The work's own output drives phase transitions. CLI instruments measure workspace state ("temperature") and threshold crossings trigger phase changes. The agents don't know they're transitioning — the thermometer knows. CLI instruments are in the control loop; AI instruments are the workers.

**"Temperature" defined:** Workspace metrics that indicate readiness for the next phase. Examples: type coverage percentage (for refactoring), test pass rate (for code generation), function count per file (for extraction work). The metric must be measurable by a CLI script and meaningfully indicate phase readiness.

**Script dependencies:** `temperature.py` and `exhaustion.py` are user-supplied. Interface contract: `temperature.py --threshold N` exits 0 if temperature meets threshold, exits 1 otherwise. `exhaustion.py --max-churn N` exits 0 if change rate is below threshold (work is cooling), exits 1 otherwise.

### When to Use / When NOT to Use

Use for multi-phase projects where work nature should change based on measurable workspace state, codebase refactoring where simple cleanup enables complex restructuring, or documentation campaigns where raw generation enables consolidation. Not when workspace metrics don't reflect work state, phase transitions need human judgment, or the work is single-phase.

### Marianne Score Structure

```yaml
sheets:
  - name: simple-work
    prompt: "Execute simple cleanup tasks. Rename variables, add type hints, extract functions."
  - name: temperature-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/temperature.py --threshold 60"
  - name: complex-work
    instrument: opus
    prompt: "Execute complex restructuring. Introduce abstractions, rewrite algorithms."
    capture_files: ["temperature-report.yaml"]
  - name: cooling-check
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 {{ workspace }}/exhaustion.py --max-churn 5"
  - name: maturation
    instrument: haiku
    prompt: "Write documentation, migration guide, changelog."
```

### Failure Mode

Temperature metric doesn't correlate with actual readiness — complex-work fires too early and fails because the codebase isn't ready. Calibrate thresholds empirically: run the pipeline once, observe when complex-work succeeds, set the threshold there. If temperature never rises (simple-work doesn't change the measured metric), the cascade stalls at the temperature check.

### Composes With

The Tool Chain (CLI instruments as thermometers), Succession Pipeline (composting IS succession with metric-driven gates), Echelon Repair (instrument escalation per phase)

---

# Iteration Patterns
