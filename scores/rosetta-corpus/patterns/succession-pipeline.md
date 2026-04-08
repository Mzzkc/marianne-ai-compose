## Succession Pipeline

`Status: Working` · **Source:** Forest succession ecology. **Forces:** Exponential Defect Cost.

### Core Dynamic

Each stage transforms the workspace into a state where the next becomes possible. The substrate transformation test: does Stage N's output become Stage N+1's input *substrate* — a different *kind* of thing? Three mandatory phases using categorically different methods.

### When to Use / When NOT to Use

Use when stages require fundamentally different methods and each output is the next's prerequisite environment. Not when each stage uses the same approach (that's iteration).

### Marianne Score Structure

```yaml
sheets:
  - name: parse
    prompt: "Parse source files into abstract syntax trees. Write AST JSON."
    validations:
      - type: command_succeeds
        command: "python3 -c \"import json; json.load(open('{{ workspace }}/ast.json'))\""
  - name: transform
    prompt: "Transform AST into intermediate representation."
    capture_files: ["ast.json"]
  - name: generate
    prompt: "Generate target code from IR."
    capture_files: ["ir.dot"]
```

### Failure Mode

If your stages use the same method with growing detail, that's Fixed-Point Iteration, not Succession.

### Composes With

Shipyard Sequence, Barn Raising
