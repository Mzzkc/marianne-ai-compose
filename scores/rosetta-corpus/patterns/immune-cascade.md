## Immune Cascade

`Status: Working` · **Source:** Immunology (innate/adaptive response). Absorbs Kill Chain F2T2EA. **Forces:** Finite Resources + Exponential Defect Cost.

### Core Dynamic

Escalating tiers: fast/cheap/broad first for intelligence, slow/expensive/precise targeting what tier 1 found, then learning persistence. Three structural moves: graduated response, intelligence forwarding, learning persistence. **Strict Sequential Variant (from Kill Chain):** When the problem is pure narrowing, collapse to a linear pipeline where `command_succeeds verifying count decreased` validates each gate.

### When to Use / When NOT to Use

Use when the problem requires broad search before targeted work and cheap scanning methods exist. Not when the problem is narrow enough for direct attack.

### Marianne Score Structure

```yaml
sheets:
  - name: broad-sweep
    instances: 8
    instrument: haiku
    prompt: "Scan {{ partition }} for issues. Write raw findings."
    validations:
      - type: file_exists
        path: "{{ workspace }}/sweep-{{ instance_id }}.md"
  - name: triage-handoff
    prompt: "Read all sweep files. Deduplicate. Prioritize. Write targeting-brief.md."
    capture_files: ["sweep-*.md"]
  - name: deep-investigation
    instrument: opus
    prompt: "Deep-dive on prioritized targets. Write remediation."
    capture_files: ["targeting-brief.md"]
  - name: learning
    prompt: "Write doctrine.md: what scanning missed, what triage misjudged, rules for next run."
    validations:
      - type: content_regex
        pattern: "RULE:\\s+.+"
```

### Failure Mode

The learning stage is useless if it doesn't write persistent, structured output. Specify the artifact: `doctrine.md` with `RULE:` entries that the next iteration's broad sweep reads via prelude.

### Composes With

Triage Gate (handoff IS triage), After-Action Review (coda), Relay Zone (relay between tiers)
