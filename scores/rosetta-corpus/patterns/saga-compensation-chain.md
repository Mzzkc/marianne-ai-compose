## Saga Compensation Chain

`Status: Aspirational [on_failure compensation actions]` · **Source:** Garcia-Molina & Salem (1987), distributed transactions, Expedition 4. **Scale:** concert-level. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

Every forward score in a concert is paired with a compensating score. If score Tk fails, compensations run Ck-1, Ck-2, ..., C1 in reverse order — not rollback (commits already happened) but forward-acting undo. The compensation isn't "delete what you made" — it's a score that produces artifacts neutralizing the forward score's effects.

**Implementation status:** Marianne does not yet have `on_failure` actions. The pattern can be approximated today with: (1) each forward score writes to `saga-log.yaml` documenting its side effects and compensation path, (2) on manual detection of failure, the user runs a separate compensation score that reads the saga log and undoes in reverse order.

### When to Use / When NOT to Use

Use for multi-score concerts where each score produces side effects on shared state, when partial completion is worse than full rollback, or when manual cleanup cost exceeds compensation engineering cost. Not when scores are idempotent, when scores don't produce side effects beyond workspace files, or when the concert is short enough for manual recovery.

### Marianne Score Structure

```yaml
# Forward score — writes to saga log for compensation context
sheets:
  - name: forward-step
    prompt: >
      Execute the migration step. Append to saga-log.yaml:
      {step: "schema-migration", artifacts: [...], side_effects: [...], compensation: "revert-schema.yaml"}.
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; log=yaml.safe_load(open('{{ workspace }}/saga-log.yaml')); assert len(log) > 0\""

# Compensation score (run manually or via future on_failure)
# sheets:
#   - name: compensate
#     prompt: "Read saga-log.yaml. For each entry in REVERSE order, execute the compensation."
#     capture_files: ["saga-log.yaml"]
```

### Failure Mode

Compensation scores can also fail — producing "compensation failure" on top of the original failure. Keep compensations simple and idempotent. The saga log must be written BEFORE side effects, not after — otherwise a crash between effect and log entry leaves uncompensatable state.

### Composes With

After-Action Review (compensation log feeds AAR), Look-Ahead Window (pre-check compensation score availability)
