# Generic Fleet Cadenza Coordination Spec

Date: 2026-06-21

## Purpose

The shipped generic fleet must coordinate through durable workspace artifacts.
A2A is optional live support; it is not the primary or authoritative
coordination path.

## Contract

1. Compiling the built-in `generic-fleet` preset seeds a shared workspace when
   `defaults.shared_workspace.enabled: true`.
2. Seeding creates the workspace directories used by the coordination
   technique: `shared/active`, `shared/plans`, `shared/findings`,
   `shared/decisions`, `shared/directives`, `shared/specs`, `shared/archive`,
   `agents`, `collective`, `playspace`, and `cycle-state`.
3. Seeding writes concrete starter files into `shared/active/`, including a
   task board, status board, findings file, decision log, directives file, and
   handoff index.
4. Seeding never overwrites existing workspace coordination files.
5. Generated scores inject `{{workspace}}/shared/active` into the phases that
   coordinate material work: recon, plan, work, integration, and inspect.
6. Since directory cadenzas are non-recursive, all current shared context must
   be a direct child of `shared/active/`.
7. Every generic fleet agent has at least one agent-specific skill technique in
   addition to shared techniques.
8. Every generic fleet agent has seeded identity and memory files:
   `identity.md`, `profile.yaml`, `recent.md`, `growth.md`, and `archive/`.
9. CLI lifecycle sheets in the generated preset must render executable shell,
   not prose. The temperature check writes either
   `cycle-state/temperature-{agent}-play` or
   `cycle-state/temperature-{agent}-work` and exits successfully; the play
   sheet's `skip_when` consumes the agent-specific work marker. The maturity
   check writes `cycle-state/{agent}-maturity-report.yaml` and exits
   successfully.
10. When a generated sheet uses `shared/active`, completion requires cadenza
    evidence as well as a cycle-state artifact: `01-task-board.md` must have
    an owner-scoped row marked `done` with evidence pointing at the required
    artifact, and `02-agent-status.md` must have the agent/phase row marked
    `complete`. If a second shared-file write conflict prevents either update,
    the required artifact must include `COORDINATION UPDATE BLOCKED:` naming
    the blocked file and reason.
11. `02-agent-status.md` is a rolling current-state table, not a phase history
    ledger. Older phase validation is meaningful at sheet completion time; the
    durable historical pointers are the task-board row and the cycle-state
    artifact or blocked-marker note.
12. Status-board `updated` values are UTC minute timestamps generated with
    `date -u +%Y-%m-%dT%H:%MZ`. Appending `Z` to local time is invalid.
13. Active-file format examples must use non-reserving placeholders such as
    `{agent}-T-001` or `{source}-DIR-001`, not concrete plausible live IDs.
    A format row must never collide with an actual agent's first task,
    finding, decision, directive, or handoff row.

## Required Active Files

The seed set is intentionally small and generic:

- `00-cadenza-coordination.md`
- `01-task-board.md`
- `02-agent-status.md`
- `03-findings.md`
- `04-decision-log.md`
- `05-directives.md`
- `06-handoff-index.md`

## Validation Requirements

Compiler tests must assert:

- The seed files exist after compiling the preset.
- Existing active files are preserved across repeated compile runs.
- Generated scores contain `shared/active` directory cadenzas for the expected
  phases.
- Rendering preview has no missing-directory warnings for `shared/active`.
- Each agent has a unique agent-specific technique document wired as a skill.
- Packaged preset compilation works outside the repository tree.
- Generated CLI phase prompts execute under Bash and write the expected marker
  or report files.
- Generated shared-active phases include cadenza completion validations for
  recon, plan, work, integration, and inspect. Tests must prove stale `claimed`
  rows fail and terminal rows pass. The cadenza evidence path must be derived
  from the phase's actual required artifact path; for example, inspect checks
  must require `cycle-state/{agent}-inspection.md`, not
  `cycle-state/{agent}-inspect.md`.
- Generated cadenza completion validations reject future `updated` timestamps,
  catching local-time values mislabeled with a `Z` suffix.
- Seeded active-file format examples use placeholder IDs and do not include
  concrete examples such as `canyon-T-001`, `sentinel-F-001`, `north-D-001`,
  `composer-DIR-001`, or `canyon-H-001`.

Runtime tests should continue to cover MCP and A2A separately. Passing A2A
tests is not sufficient proof that the fleet can coordinate; cadenza injection
and seed artifact tests are mandatory.
