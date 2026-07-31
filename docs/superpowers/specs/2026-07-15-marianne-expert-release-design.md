# Marianne Expert Release and Acting Acceptance Design

## Purpose

Release `marianne-expert` as a reproducible skill, harden `composing` so its
quality claims are enforced, and prove both through a Marianne-run acting test
that completes the native-backend unification work against current source.

The supplied C1 handoff is evidence, not an execution script. At baseline HEAD
`2de88f9`, it already undercounts surviving `marianne.backends.base` consumers.
The expert must rediscover the live surface before planning or editing.

## Boundaries

This work has three independently testable products:

1. A canonical `marianne-expert` skill in the Marianne plugin repository.
2. Deterministic design and score release gates shipped with `composing`.
3. A Marianne acceptance score that uses the candidate skill to reconcile and
   complete C1 in an isolated source worktree.

Installed copies under `~/.codex/skills` and `~/.agents/skills` are deployment
outputs. The moved expertise workspaces are historical evidence only.

## Marianne Expert Architecture

`SKILL.md` remains a compact router. It sends the agent to a preflight script,
then to one task playbook and the minimum evidence needed. Preflight reports
independent capabilities rather than one exclusive mode:

- pinned kit availability;
- current source read access;
- current source write authorization;
- Marianne CLI availability;
- conductor IPC availability;
- online primary-source availability.

Authorization is explicit input; it is never inferred from filesystem mode.
Preflight fingerprints repository HEAD, branch, dirty paths, and dirty-file
content. Runtime feature status remains separate from session access.

Evidence precedence is scoped by claim type. Current implementation questions
use current source and tests first. Historical questions use pinned source and
git history. External product facts use official current sources. Conflicts are
reported rather than averaged.

The release includes a deterministic manifest. Verification fails when a file
differs, an absolute historical expertise-workspace path leaks into runtime
instructions, required files are absent, or the two installed copies differ.

## Composing Gate Architecture

Composition produces two artifacts before runnable YAML:

1. `composition-design.yaml`, a reviewable contract containing goal, authority,
   forces, stage DAG, context ledger, injection manifest, proof obligations,
   repair loop, and release policy.
2. `composition-lock.json`, generated from the approved score and every
   load-bearing injected file.

`check_design.py` rejects missing design fields and invalid stage dependencies.
`check_score_release.py` loads the score through Marianne's real `JobConfig`,
builds its sheets, resolves injection paths using runtime workspace semantics,
and rejects missing or empty load-bearing injections. It also enforces these
composition policies:

- workspace and project root are distinct;
- deterministic `cli` sheets have an explicit empty fallback chain;
- every produced artifact has more than a file-existence validation;
- repair cannot flow directly to release without reevaluation;
- the released score and injected inputs match the lock digest.

Policy findings are labelled as composing policy, not falsely described as
runtime parser behavior.

## Acceptance Data Flow

The acceptance score uses a separate artifact workspace and the isolated source
worktree as `project_root`:

```text
raw handoff + live git state
        -> freeze manifest
        -> expert reconciliation
        -> approved implementation plan
        -> source changes
        -> deterministic verification
        -> independent inspection
        -> repair -> verification loop
        -> exact-hash release evidence
```

The candidate `marianne-expert/SKILL.md` is attached as a Marianne skill
technique. A unique release sentinel in the skill and the rendered prompt proves
that the candidate, rather than an ambient skill, was injected.

## Safety

- Never modify the user's dirty main checkout.
- Never stop or restart a conductor with active jobs.
- Never use `--fresh` to recover an interrupted acceptance run.
- Freeze source state before mutation and again before release.
- Do not delete old backend tests merely because the handoff calls them dead;
  first classify which behavior the profile-driven replacement must retain.
- Any source change after verification invalidates the candidate digest and
  returns the run to verification.

## Error Handling

Missing injection, unresolved authority, dirty overlap, unavailable instrument,
baseline test failure, and digest mismatch are held states, not warnings. The
score writes a structured blocker artifact and stops before the unsafe edge.

Online verification is conditional. If unavailable, the expert records the
capability gap and does not claim current upstream verification. Live API smoke
is conditional on credentials; local translator contract tests remain required.

## Test Strategy

### Skill RED/GREEN

- RED: run a read-only Marianne sheet using the current installed expert against
  the raw C1 handoff; record whether it fingerprints live source and corrects the
  stale coupling count.
- GREEN: repeat with the candidate skill and require the capability manifest,
  contradiction report, current-source citations, and no mutation.

### Deterministic gates

- Unit tests cover clean/dirty repositories, explicit write authorization,
  missing injections, empty directories, unsafe workspace overlap, deterministic
  CLI fallback leakage, weak validations, lock mismatch, and relocation.
- Every test is written and observed failing before implementation.

### Acting acceptance

- Run the C1 score through the existing conductor.
- Run phase-local tests and the full suite.
- Inspect the exact final diff and candidate hash.
- Rerun verification after every repair.
- The released digest must equal the evaluated digest.

## Release Criteria

Release requires both plugin skill tests and the parent Marianne full suite to
pass, a successful relocation check, matching Codex/Agents installations, an
actual Marianne job record proving candidate injection, and a completed C1 end
state or an evidence-backed blocker outside the authorized scope.

