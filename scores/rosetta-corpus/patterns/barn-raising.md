## Barn Raising

`Status: Working` · **Source:** Community barn raising (Amish). **Forces:** Producer-Consumer Mismatch + Finite Resources.

### Core Dynamic

Shared conventions established before parallel work. A conventions document defines naming, structure, interfaces. All parallel tracks read it. Different from Prefabrication (which defines interfaces). Barn Raising defines conventions — broader scope, softer constraints.

### When to Use / When NOT to Use

Use when parallel agents need consistency beyond interface contracts. Not when a single agent does all work.

### Marianne Score Structure

```yaml
sheets:
  - name: conventions
    prompt: "Write conventions.md: naming rules, file structure, code style."
    validations:
      - type: file_exists
        path: "{{ workspace }}/conventions.md"
  - name: build
    instances: 6
    prompt: "Read conventions.md. Build component {{ instance_id }}."
    capture_files: ["conventions.md"]
```

### Failure Mode

Conventions too vague to enforce consistency. Too rigid to allow agent judgment. Strike the balance based on integration requirements.

### Composes With

Prefabrication, Mission Command, Lines of Effort
