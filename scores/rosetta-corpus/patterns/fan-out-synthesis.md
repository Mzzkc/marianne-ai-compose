## Fan-out + Synthesis

`Status: Working` · **Source:** Ubiquitous — confirmed across all expeditions. Prior art: MapReduce (Dean & Ghemawat, 2004).

### Core Dynamic

Split work into parallel independent streams, merge in a synthesis stage. N agents work simultaneously on different facets. A final agent reads all outputs and produces a unified result. Most score-level patterns in this corpus build on, modify, or explicitly reject this structure. It is the default move when information asymmetry meets finite resources.

### When to Use / When NOT to Use

Use when the problem decomposes into independent sub-problems with a meaningful merge. Not when sub-problems share mutable state, synthesis is trivial concatenation, or fan-out width of 1 suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: prepare
    prompt: "Define scope and shared context for the analysis."
    validations:
      - type: file_exists
        path: "{{ workspace }}/scope.md"
  - name: analyze
    instances: 6
    prompt: "Analyze module {{ instance_id }}. Write findings to analysis-{{ instance_id }}.md."
    capture_files: ["scope.md"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/analysis-{{ instance_id }}.md"
  - name: synthesize
    prompt: "Read all analysis files. Produce a unified review addressing cross-cutting concerns."
    capture_files: ["analysis-*.md"]
    validations:
      - type: command_succeeds
        command: "test $(ls {{ workspace }}/analysis-*.md | wc -l) -ge 4"
```

### Failure Mode

Synthesis produces concatenation rather than integration. Validate with `command_succeeds` checking the synthesis references cross-cutting themes, not just individual reports. If fan-out agents share state, outputs will converge — use Prefabrication with interface contracts instead.

### Composes With

Barn Raising (conventions govern fan-out), Shipyard Sequence (validate before fanning out), After-Action Review (coda on synthesis), Triage Gate (classify outputs before synthesis), Relay Zone (compress before synthesis)

---

# Within-Stage Patterns

*Prompt techniques — these structure a single sheet's prompt, not sheet arrangement.*
