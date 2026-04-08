## Season Bible

`Status: Working` · **Source:** Television production (show bible). **Forces:** Producer-Consumer Mismatch.

### Core Dynamic

A mutable reference document that evolves as the campaign progresses. Different from Barn Raising conventions (which are static). The bible records decisions, character evolutions, and continuity constraints. Scores read it before starting and update it after completing.

### When to Use / When NOT to Use

Use for multi-score campaigns needing continuity. Not for single-score work.

### Marianne Score Structure

```yaml
sheets:
  - name: read-bible
    prompt: "Read season-bible.md. Note current state and constraints."
    capture_files: ["season-bible.md"]
  - name: work
    prompt: "Execute work respecting bible constraints."
  - name: update-bible
    prompt: "Update season-bible.md with new decisions and state changes."
    validations:
      - type: content_contains
        path: "{{ workspace }}/season-bible.md"
        content: "Updated:"
```

### Failure Mode

Bible grows stale — scores read it but don't update. Validate update stage actually modifies the bible.

### Composes With

Lines of Effort, Relay Zone, Cathedral Construction
