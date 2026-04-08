## Clash Detection

`Status: Working` · **Source:** MEP coordination / BIM in construction, Expedition 1. **Scale:** score-level. **Iteration:** 4.

### Core Dynamic

After parallel tracks produce outputs but BEFORE integration, a dedicated stage compares all outputs for CONFLICTS — without trying to merge them. Cheaper than integration testing. Different from the contract (which prevents KNOWN conflict classes) and integration testing (which discovers conflicts empirically). Clash detection uses the OUTPUTS as inputs, overlays them, and searches for interference patterns. The scope is detection, not resolution — downstream stages handle fixes.

### When to Use / When NOT to Use

Use when parallel tracks produce artifacts that must coexist (code modules, config files, API schemas), when the contract can't anticipate all conflict modes, or when integration testing is expensive enough that catching conflicts earlier saves meaningful cost. Not when parallel tracks produce truly independent artifacts, when the contract is exhaustive, or when parallel work is done by the same agent.

### Marianne Score Structure

```yaml
sheets:
  - name: track-work
    instances: 4
    prompt: "Build component {{ instance_id }}."
  - name: clash-scan
    prompt: >
      Read ALL track outputs. Search for naming collisions, interface mismatches,
      resource conflicts. Write clash-report.yaml with {clashes: [{type, items, detail}], clash_count: N}.
    capture_files: ["track-*/**"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; r=yaml.safe_load(open('{{ workspace }}/clash-report.yaml')); c=r.get('clash_count',0) if r else 0; assert c==0, f'{c} clashes found'\""
  - name: integrate
    prompt: "Assemble all track outputs."
    capture_files: ["track-*/**"]
```

### Failure Mode

Clash detection finds conflicts — the assertion fails, blocking integration. This is the INTENDED behavior. The score author must add a resolution stage after clash-scan that fixes conflicts and re-runs the scan. If clash-report.yaml is malformed or missing the `clash_count` key, the validation fails with a clear assertion error rather than a cryptic KeyError.

### Composes With

Prefabrication (clash detection after prefab tracks), Andon Cord (clash triggers diagnostic), The Tool Chain (CLI clash detection for structural conflicts)
