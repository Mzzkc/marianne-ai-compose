## CEGAR Loop (Progressive Refinement)

`Status: Working` · **Source:** Counterexample-Guided Abstraction Refinement (Clarke et al., 2000), Expedition 4. **Scale:** iteration. **Iteration:** 4. **Force:** Progressive Commitment.

### Core Dynamic

Iteratively refines ABSTRACTION LEVEL, not output. Start coarse. If a problem is found, check if it's REAL or SPURIOUS (artifact of over-abstraction). If spurious, refine only the specific part that caused the false alarm. You never refine more than necessary. The structural move is minimum-cost verification through progressive abstraction refinement.

The multi-instrument strategy is central: cheap instrument (Sonnet) for the broad coarse pass, expensive instrument (Opus) for the targeted triage. This matches the work's nature — coarse scanning is pattern-matching (cheap), distinguishing real from spurious requires deep reasoning (expensive).

**Termination:** The loop terminates when the CLI validation sheet finds `refinement-targets.yaml` is empty (all findings resolved as REAL or SPURIOUS with no new areas to refine). If `max_chain_depth` is reached before convergence, the loop produces its best current report rather than failing.

### When to Use / When NOT to Use

Use for code review at scale (module-level first, function-level only where coarseness misleads), security audits (dependency scan then exploitability analysis), any verification where thorough analysis is expensive and most of the system is fine. Not when the abstraction hierarchy is shallow, spurious counterexamples are rare, or checking spurious vs. real costs more than full fine-grained analysis.

### Marianne Score Structure

```yaml
sheets:
  - name: coarse-check
    instrument: sonnet
    prompt: "Analyze at module level. Write findings.yaml with [{module, finding, confidence}]."
    validations:
      - type: file_exists
        path: "{{ workspace }}/findings.yaml"
  - name: triage-findings
    instrument: opus
    prompt: >
      For each finding in findings.yaml, determine: REAL or SPURIOUS?
      Write triage-report.yaml: [{module, finding, verdict: REAL|SPURIOUS, evidence}].
    capture_files: ["findings.yaml"]
    validations:
      - type: file_exists
        path: "{{ workspace }}/triage-report.yaml"
  - name: refine-or-report
    prompt: >
      Read triage-report.yaml.
      Write refinement-targets.yaml listing modules with SPURIOUS findings needing finer analysis.
      Write current-report.md summarizing all REAL findings confirmed so far.
    capture_files: ["triage-report.yaml"]
  - name: check-termination
    instrument: cli
    validations:
      - type: command_succeeds
        command: "python3 -c \"import yaml; t=yaml.safe_load(open('{{ workspace }}/refinement-targets.yaml')); assert len(t)==0, f'{len(t)} targets remain'\""
on_success:
  action: self
  inherit_workspace: true
  max_chain_depth: 5
```

### Failure Mode

Triage consistently marks real findings as spurious — refinement chases ghosts while real issues pass through. Validate by checking that refined areas produce fewer findings (convergence signal). If the loop exhausts `max_chain_depth` without converging, the abstraction hierarchy may be too shallow for this problem — fall back to full fine-grained analysis. The check-termination assertion fails when targets remain, breaking the self-chain — this is intentional, forcing refinement to continue.

### Composes With

Memoization Cache (unchanged modules skip re-analysis), CDCL Search (real findings become constraints), Immune Cascade (CEGAR IS graduated response with abstraction control)
