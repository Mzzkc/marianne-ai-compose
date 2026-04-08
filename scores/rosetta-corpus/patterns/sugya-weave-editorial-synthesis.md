## Sugya Weave (Editorial Synthesis)

`Status: Working` · **Source:** Talmudic sugya structure. **Forces:** Information Asymmetry + Convergence Imperative.

### Core Dynamic

Not just synthesis — editorial synthesis. The weaver takes a POSITION on the inputs, arguing for one interpretation while acknowledging alternatives. Produces an opinionated conclusion, not a summary. Requires structured validation that the position is supported.

### When to Use / When NOT to Use

Use when diverse inputs need an authoritative position, not just aggregation. Not when neutrality is required.

### Marianne Score Structure

```yaml
sheets:
  - name: weave
    prompt: >
      Read all inputs. Take a position. Write editorial-synthesis.md with:
      POSITION, SUPPORTING EVIDENCE, COUNTERARGUMENTS, CONCLUSION.
    capture_files: ["input-*.md"]
    validations:
      - type: content_contains
        content: "POSITION:"
      - type: content_contains
        content: "COUNTERARGUMENTS:"
```

### Failure Mode

Position is unsupported assertion. Validate that supporting evidence references specific inputs.

### Composes With

Fan-out + Synthesis, Source Triangulation, Rashomon Gate
