## Commander's Intent Envelope

`Status: Working` · **Source:** Military mission command doctrine, Expedition 5. **Scale:** within-stage. **Iteration:** 4. **Type:** Prompt technique.

### Core Dynamic

Structures a single sheet's prompt as PURPOSE (why this task matters in the larger score), END STATE (measurable success conditions), CONSTRAINTS (hard boundaries — MUST NOT violate), and FREEDOMS (decisions the agent may make autonomously). The structural distinction from ordinary prompting: this changes the coordination contract from instructions (do X then Y) to boundaries (achieve Z however you see fit, except A). The agent finds its own path within the envelope. Validates end-state achievement and autonomous decision-making, not method compliance.

### When to Use / When NOT to Use

Use when the task has more than one valid approach, when inputs are variable-format, or when different instruments would achieve the end state differently. Not for purely mechanical tasks (format conversion, command execution), security-critical operations where deviations create vulnerabilities, or when validation criteria can't capture the end state precisely.

### Marianne Score Structure

```yaml
sheets:
  - name: execute
    prompt: |
      ## Commander's Intent
      PURPOSE: Ensure the web application has no exploitable input validation vulnerabilities.
      END STATE: Report listing all confirmed vulnerabilities with severity, location, fix. Zero false positives.
      CONSTRAINTS: Do not modify source code. Do not run code. Do not access external services.
      FREEDOMS: Choose which files to review. Choose review order. Choose depth based on risk.

      ## Context
      Read {{ workspace }}/codebase/ for the application source.

      ## Resources
      Write findings to {{ workspace }}/security-report.md and decision-log.md.
    validations:
      - type: file_exists
        path: "{{ workspace }}/security-report.md"
      - type: file_exists
        path: "{{ workspace }}/decision-log.md"
      - type: content_contains
        path: "{{ workspace }}/decision-log.md"
        content: "DECISION:"
```

### Failure Mode

Intent briefs too vague produce incoherent decisions; too specific collapses the decision space back to instructions. The decision-log validation is critical: if the agent made no autonomous decisions, the envelope wasn't adding value over direct instructions. If the log shows decisions outside the CONSTRAINTS, the boundaries were unclear.

### Composes With

Mission Command (intent IS mission command at sheet scale), Fan-out + Synthesis (intent envelope shared across instances), After-Action Review (decision-log feeds doctrine)
