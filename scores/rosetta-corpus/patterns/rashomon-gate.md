## Rashomon Gate

`Status: Working` · **Source:** Kurosawa's *Rashomon* (1950), epistemological frame analysis, Expedition 6. **Scale:** score-level. **Iteration:** 4. **Force:** Structured Disagreement.

### Core Dynamic

Every fan-out instance gets the SAME evidence but analyzes from a DIFFERENT analytical frame. Contradictions are not failures — they are data. The synthesis categorizes findings by agreement level: UNANIMOUS (high confidence), MAJORITY, SPLIT (genuine ambiguity), UNIQUE (deep insight or frame artifact). The PATTERN of agreement across frames reveals more than any single analysis.

Different from Source Triangulation (which divides sources) and plain fan-out (which divides work). The cadenza mechanism (see Glossary) maps 1:1 to instances — each instance receives a different frame file defining its analytical perspective.

### When to Use / When NOT to Use

Use for problems where the right analytical frame is unknown, security audits (attacker/defender/compliance), code review (correctness/maintainability/performance), any task where the risk is "right answer from the wrong frame." Not when frames are so similar they produce trivially similar outputs, evidence is unambiguous, or the synthesis agent can't distinguish genuine disagreement from different vocabulary.

### Marianne Score Structure

```yaml
sheets:
  - name: evidence
    prompt: "Assemble the artifact all analysts will examine."
  - name: analyze
    instances: 4
    cadenza:
      - "frame-security.md"
      - "frame-performance.md"
      - "frame-maintainability.md"
      - "frame-correctness.md"
    prompt: "Analyze the evidence through your assigned frame. Write analysis-{{ instance_id }}.md."
    capture_files: ["evidence/**"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/analysis-{{ instance_id }}.md"
  - name: triangulate
    prompt: >
      Read all analyses. For EACH finding across all frames, categorize:
      UNANIMOUS (all frames agree), MAJORITY (most agree), SPLIT (even division), UNIQUE (one frame only).
      Write triangulation.yaml: {findings: [{finding, category, frames_agreeing, detail}], summary_counts: {unanimous: N, majority: N, split: N, unique: N}}.
    capture_files: ["analysis-*.md"]
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; t=yaml.safe_load(open('{{ workspace }}/triangulation.yaml')); assert len(t.get('findings',[])) > 0, 'No findings categorized'\""
```

### Failure Mode

Frames too similar produce trivially UNANIMOUS results — the gate adds cost without insight. Frames too dissimilar produce all UNIQUE results — no agreement signal to act on. The optimal frame set produces a mix of categories. If the validation only checks for keyword presence (UNANIMOUS/SPLIT), an agent can write the keywords without doing the categorization. The `command_succeeds` validation checking finding count prevents this.

### Composes With

Source Triangulation (Rashomon for frames, triangulation for sources), Sugya Weave (weave the triangulated findings into a position), Commander's Intent Envelope (frame IS the intent for each instance)
