## Shipyard Sequence

`Status: Working` · **Source:** Shipbuilding hull block method. **Forces:** Exponential Defect Cost + Finite Resources.

### Core Dynamic

Validate foundational work under realistic conditions before investing in expensive fan-out. The launch gate uses `command_succeeds` exclusively — real execution, not LLM judgment. Construction has 1-3 stages; outfitting fans out only after launch passes.

### When to Use / When NOT to Use

Use when downstream fan-out is expensive, foundation must be solid, and real validation tools exist. Not when work is naturally parallel from the start.

### Marianne Score Structure

```yaml
sheets:
  - name: construct-schema
    prompt: "Generate the database schema and migration files."
  - name: launch-gate
    validations:
      - type: command_succeeds
        command: "cd {{ workspace }} && python manage.py migrate --check"
      - type: command_succeeds
        command: "cd {{ workspace }} && python manage.py test db_schema --verbosity=0"
  - name: outfitting
    instances: 4
    prompt: "Build {{ service_name }} against the validated schema."
    capture_files: ["schema.sql"]
```

### Failure Mode

If launch validation is too lenient, expensive fan-out proceeds on a broken foundation. The gate must use `command_succeeds`, never `content_contains`.

### Composes With

Succession Pipeline, Dormancy Gate, Triage Gate
