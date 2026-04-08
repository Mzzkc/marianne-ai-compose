## Nurse Log

`Status: Working` · **Source:** Forest ecology (nurse logs). **Forces:** Finite Resources.

### Core Dynamic

A preparation stage creates general-purpose substrate (research, data collection, organization) that makes downstream stages more productive. Different from Reconnaissance Pull (which discovers the approach). Nurse Log prepares the ground regardless of approach.

### When to Use / When NOT to Use

Use when downstream stages share common preparation needs. Not when preparation is stage-specific.

### Marianne Score Structure

```yaml
sheets:
  - name: prepare-substrate
    prompt: "Research the domain. Collect reference material. Organize into substrate/."
    validations:
      - type: file_exists
        path: "{{ workspace }}/substrate/"
  - name: work
    instances: 4
    prompt: "Read substrate/. Build component {{ instance_id }}."
    capture_files: ["substrate/**"]
```

### Failure Mode

Substrate too generic to help. Make preparation specific to the downstream work, not a generic research dump.

### Composes With

Fermentation Relay, Fan-out + Synthesis

---

# Concert-Level Patterns
