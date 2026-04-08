## Talmudic Page

`Status: Working` · **Source:** Talmudic commentary layout (Mishnah + Gemara + commentaries). **Forces:** Information Asymmetry.

### Core Dynamic

A central text surrounded by commentary layers at different levels of abstraction. The central text anchors all commentary; each layer responds to the text AND to other layers. Produces interlinked multi-perspective analysis without losing the central thread.

### When to Use / When NOT to Use

Use when a primary artifact needs multi-layer annotation. Not when commentaries are independent (use plain Fan-out).

### Marianne Score Structure

```yaml
sheets:
  - name: central-text
    prompt: "Write the core analysis."
  - name: commentary
    instances: 3
    prompt: "Read the core analysis. Write commentary from your perspective."
    capture_files: ["core-analysis.md"]
  - name: interlink
    prompt: "Read core + all commentaries. Write cross-referenced synthesis."
    capture_files: ["core-analysis.md", "commentary-*.md"]
```

### Failure Mode

Commentaries ignore each other and respond only to the central text. The interlink stage must reference cross-commentary connections.

### Composes With

Sugya Weave, Fan-out + Synthesis
