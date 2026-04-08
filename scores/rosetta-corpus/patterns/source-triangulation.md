## Source Triangulation

`Status: Working` · **Source:** Journalism, intelligence analysis. **Forces:** Information Asymmetry.

### Core Dynamic

Multiple agents analyze the SAME problem from DIFFERENT sources. The synthesis identifies: corroborated (multiple sources agree), uncorroborated (single source), and contradicted (sources disagree). Different from Rashomon Gate (which uses different frames on same evidence). Source Triangulation divides the evidence itself.

### When to Use / When NOT to Use

Use when claims need independent verification and multiple source types exist. Not when a single authoritative source suffices.

### Marianne Score Structure

```yaml
sheets:
  - name: investigate
    instances: 3
    cadenza:
      - "source-code.md"
      - "source-docs.md"
      - "source-tests.md"
    prompt: "Analyze from your assigned source. Write findings-{{ instance_id }}.md."
    validations:
      - type: file_exists
        path: "{{ workspace }}/findings-{{ instance_id }}.md"
  - name: triangulate
    prompt: >
      Read all findings. Categorize each claim: CORROBORATED (2+ sources),
      UNCORROBORATED (1 source), CONTRADICTED (sources disagree).
    capture_files: ["findings-*.md"]
```

### Failure Mode

Sources too similar produce trivially corroborated results. Ensure sources are structurally independent.

### Composes With

Rashomon Gate, Triage Gate, Sugya Weave
