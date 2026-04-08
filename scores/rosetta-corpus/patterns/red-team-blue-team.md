## Red Team / Blue Team

`Status: Working` · **Source:** Military adversarial exercises. **Forces:** Information Asymmetry + Partial Failure.

### Core Dynamic

Information asymmetry via redaction: Red writes *effects* but not *methods*. Blue sees effects, must defend blind. Purple debrief gets full access. **Enforcement:** Separate workspace subdirectories (`red-workspace/` vs `blue-briefing/`). A relay stage copies only effect descriptions. Blue's `capture_files` is restricted to `blue-briefing/` only.

### When to Use / When NOT to Use

Use when the artifact needs adversarial stress-testing and the defender should not know attack methods. Not when the team is collaborative or the artifact is too simple for adversarial testing.

### Marianne Score Structure

```yaml
sheets:
  - name: red-attack
    prompt: "Attack the artifact. Write effects to red-workspace/effects.md and methods to red-workspace/methods.md."
    validations:
      - type: file_exists
        path: "{{ workspace }}/red-workspace/effects.md"
  - name: relay
    instrument: cli
    validations:
      - type: command_succeeds
        command: "cp {{ workspace }}/red-workspace/effects.md {{ workspace }}/blue-briefing/effects.md"
  - name: blue-defend
    prompt: "Read blue-briefing/effects.md. Defend. Write blue-response.md."
    capture_files: ["blue-briefing/effects.md"]
  - name: purple-debrief
    prompt: "Read ALL files. Write debrief with attack-defense matrix."
    capture_files: ["red-workspace/**", "blue-briefing/**", "blue-response.md"]
```

### Failure Mode

Red produces weak attacks, Blue passes trivially. Validate Red output contains specific attack categories. If relay leaks methods, Blue's defense is tainted.

### Composes With

After-Action Review (purple debrief IS AAR), Immune Cascade
