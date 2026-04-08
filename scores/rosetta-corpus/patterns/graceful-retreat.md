## Graceful Retreat

`Status: Working` · **Source:** Military phased withdrawal, Netflix degradation, Expedition 5. **Scale:** score-level. **Iteration:** 4. **Force:** Graceful Failure.

### Core Dynamic

Defines TIERS OF COMPLETENESS upfront. Tier 1: full output, all sections. Tier 2: core sections only. Tier 3: summary-only with pointers to what couldn't be completed. Each tier has its own validation criteria. If Tier 1 fails, the agent falls back to Tier 2 rather than failing entirely. The retreat is PLANNED — tiers defined in the prompt, not discovered during failure.

**Enforcement note:** Tier achievement is self-reported by the agent. For structural enforcement, a downstream CLI validation sheet should independently verify which tier's criteria are met, rather than trusting the agent's `tier_achieved` claim.

### When to Use / When NOT to Use

Use for long-running sheets where partial output has value, hard deadlines where "something by Tuesday" beats "perfection by Thursday," or pipeline stages where downstream can operate on partial input. Not when partial output is dangerous (security audits, financial calculations) or downstream can't distinguish "complete but simple" from "incomplete due to retreat."

### Marianne Score Structure

```yaml
sheets:
  - name: execute
    prompt: |
      TIER 1 (attempt first): Full analysis with all 5 sections (overview, architecture, security, performance, recommendations).
      TIER 2 (if Tier 1 fails): 3 core sections (overview, architecture, recommendations).
      TIER 3 (if Tier 2 fails): Executive summary with top-3 issues only.

      Write completion-status.yaml: {tier_achieved: 1|2|3, sections_completed: [], sections_skipped: [], reason}.
      Write the analysis to analysis.md.
    validations:
      - type: file_exists
        path: "{{ workspace }}/completion-status.yaml"
      - type: file_exists
        path: "{{ workspace }}/analysis.md"
  - name: verify-tier
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; s=yaml.safe_load(open('{{ workspace }}/completion-status.yaml')); tier=s['tier_achieved']; content=open('{{ workspace }}/analysis.md').read(); checks={'overview' in content.lower(), 'architecture' in content.lower()}; assert all(checks), f'Tier {tier} claimed but missing core sections'\""
```

### Failure Mode

Agent always retreats to Tier 3 because it's easiest — the retreat becomes the default. Validate that Tier 1 was genuinely attempted (check for partial Tier 1 artifacts). If downstream stages can't adapt to different tiers, the retreat produces useless partial output — ensure downstream reads `completion-status.yaml` and adjusts expectations.

### Composes With

Andon Cord (retreat triggers diagnostic), Dead Letter Quarantine (Tier 3 outputs enter quarantine for enhanced reprocessing), Cathedral Construction (retreat within a single iteration, continue next)
