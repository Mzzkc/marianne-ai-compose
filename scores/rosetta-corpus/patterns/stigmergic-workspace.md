## Stigmergic Workspace

`Status: Working` · **Source:** Ant colony optimization. **Forces:** Information Asymmetry + Finite Resources.

### Core Dynamic

Agents coordinate through workspace artifacts, not direct communication. Agent A writes a file; Agent B reads it. No messages, no coordination protocol — the workspace IS the communication channel.

### When to Use / When NOT to Use

Use when agents need loose coordination and the workspace captures state. Not when real-time coordination is needed.

### Marianne Score Structure

```yaml
sheets:
  - name: work
    instances: 8
    prompt: >
      Read workspace for current state. Do your work. Write results.
      If you find something relevant to other workers, write it to shared/signals/.
    capture_files: ["shared/signals/**"]
```

### Failure Mode

Conflicting writes to the same file. Use namespaced output directories per instance.

### Composes With

Barn Raising, Lines of Effort

---

# Adaptation Patterns
