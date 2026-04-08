## Prefabrication

`Status: Working` · **Source:** Construction industry (offsite fabrication). **Forces:** Producer-Consumer Mismatch + Finite Resources.

### Core Dynamic

Define interface contracts before parallel work begins. Each parallel track gets a shared interface definition and builds to it. Integration only assembles pre-validated pieces. Different from Fan-out + Synthesis: prefabrication has an explicit interface specification stage before fan-out.

### When to Use / When NOT to Use

Use when parallel tracks must produce compatible outputs. Not when outputs are independent (use plain Fan-out) or when the interface can't be defined upfront.

### Marianne Score Structure

```yaml
sheets:
  - name: interface-spec
    prompt: "Define the shared API contract. Write interface-spec.yaml."
    validations:
      - type: file_exists
        path: "{{ workspace }}/interface-spec.yaml"
  - name: build
    instances: 4
    prompt: "Build component {{ instance_id }} according to interface-spec.yaml."
    capture_files: ["interface-spec.yaml"]
  - name: integrate
    prompt: "Assemble all components. Verify all interfaces match."
    capture_files: ["component-*/**"]
```

### Failure Mode

Interface spec too loose allows incompatible implementations. Too tight eliminates the benefits of parallel work.

### Composes With

Barn Raising, Clash Detection, Mission Command
