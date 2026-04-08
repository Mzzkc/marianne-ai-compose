## Quorum Trigger

`Status: Working` · **Source:** Bacterial quorum sensing (threshold-triggered behavioral switch), Expedition 2. **Scale:** within-stage. **Iteration:** 4. **Force:** Accumulated Signal. **Type:** Prompt technique.

### Core Dynamic

Within-stage behavioral switch triggered by accumulated signal density. The agent works on its primary task while maintaining an explicit signal register (a YAML file tracking findings with severity levels). When accumulated signals cross a predefined threshold (e.g., "3+ CRITICAL findings"), the agent stops its current plan and switches to an alternate behavior (remediation, diagnosis, escalation). The switch is binary — a phase transition, not a gradual adjustment.

**Enforcement note:** The signal register is agent-maintained and therefore untrustworthy in isolation. A downstream CLI validation sheet should verify the register's threshold state independently. Do not rely solely on the agent self-reporting whether the trigger fired.

### When to Use / When NOT to Use

Use when conditions discovered mid-task make continuing the original plan wasteful or dangerous: code review finding critical security flaws, research discovering the premise is wrong, data processing hitting malformed records. Not when the threshold is ambiguous, the task is too short for the switch to fire, or the behavioral switch loses valuable pre-switch context.

### Marianne Score Structure

```yaml
sheets:
  - name: audit
    prompt: |
      Audit each module for vulnerabilities. Maintain a signal register in signal-register.yaml:
      each entry has {module, severity: LOW|MEDIUM|HIGH|CRITICAL, finding}.

      THRESHOLD: If you accumulate 3+ CRITICAL findings before completing the full audit,
      STOP scanning and switch to writing a remediation plan for findings so far.
      Write quorum-trigger-report.md if threshold fires.
    validations:
      - type: file_exists
        path: "{{ workspace }}/signal-register.yaml"
      - type: content_regex
        pattern: "severity:\\s+(LOW|MEDIUM|HIGH|CRITICAL)"
  - name: verify-threshold
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; r=yaml.safe_load(open('{{ workspace }}/signal-register.yaml')); crits=[e for e in r if e.get('severity')=='CRITICAL']; import os; triggered=os.path.exists('{{ workspace }}/quorum-trigger-report.md'); assert (len(crits)>=3)==triggered, f'Threshold mismatch: {len(crits)} crits, triggered={triggered}'\""
```

### Failure Mode

Agent miscounts findings or ignores the threshold entirely. The CLI verification sheet catches this: if 3+ CRITICALs exist but no trigger report (or vice versa), the validation fails. The deeper failure: the agent classifies everything as MEDIUM to avoid triggering. Only domain-specific validation of severity assignments catches this.

### Composes With

Andon Cord (quorum trigger within a stage, andon cord between stages), Circuit Breaker (quorum for quality, circuit breaker for infrastructure), Immune Cascade (quorum-triggered triage)
