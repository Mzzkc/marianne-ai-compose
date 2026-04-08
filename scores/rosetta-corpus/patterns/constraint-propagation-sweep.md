## Constraint Propagation Sweep

`Status: Working` · **Source:** Constraint satisfaction, structured reasoning. **Scale:** within-stage. **Iteration:** 4. **Force:** Domain Reduction. **Type:** Prompt technique.

### Core Dynamic

Before generating ANY output, the prompt instructs the agent to separate three kinds of reasoning into mandatory phases: (1) ENUMERATE all constraints from the specification and workspace artifacts, (2) RESOLVE them pairwise to identify contradictions and prune impossible options, (3) GENERATE from the reduced solution space. The phases MUST be separate — generating during resolution skips contradictions; resolving during generation loses information. This is domain reduction before search: pruning is cheap, search through contradictory requirements is expensive.

This is a prompt structuring technique, not a multi-sheet orchestration pattern. The phases are instructions within one prompt, not separate sheets. This means no intermediate validation between phases — the agent can skip resolution and you'd only detect it from the output quality, not structurally. For structural enforcement, use three separate sheets with validation between them.

### When to Use / When NOT to Use

Use when specifications contain implicit contradictions from different stakeholders, when generating from conflicting requirements costs more than constraint analysis, or when reconciling heterogeneous inputs (multiple analyst reports, multi-team requirements). Not when constraints are few and independent, the specification is already consistent, or the task is creative rather than constrained.

### Marianne Score Structure

```yaml
sheets:
  - name: synthesize
    prompt: |
      ENUMERATE: List every constraint from the input documents.
      RESOLVE: Check each pair for conflicts. Mark the weaker constraint as pruned.
      Write constraint-audit.yaml: {id, constraint, status: active|pruned, reason}.
      GENERATE: Produce the architecture using only active constraints.
      Write the synthesis to synthesis.md.
    capture_files: ["requirements/*.md"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/constraint-audit.yaml"
      - type: command_succeeds
        command: "python3 -c \"import yaml; a=yaml.safe_load(open('{{ workspace }}/constraint-audit.yaml')); pruned=[e for e in a if e.get('status')=='pruned']; print(f'{len(pruned)} constraints pruned of {len(a)} total')\""
```

### Failure Mode

Agent performs all three phases but doesn't actually prune — the audit shows zero pruned constraints despite contradictory inputs. The `command_succeeds` validation catches this by printing stats, but can't enforce quality. For high-stakes synthesis, follow with a dedicated clash detection sheet comparing the synthesis against all input constraints.

### Composes With

Decision Propagation (propagation feeds constraint briefs), CDCL Search (failures become new constraints), Rashomon Gate (multiple frames on the same constraint set)

---

# Score-Level Patterns
