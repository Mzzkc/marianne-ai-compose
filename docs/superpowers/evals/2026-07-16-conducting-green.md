# Conducting Skill GREEN Evaluation

## Candidate

- Skill:
  `/home/emzi/Projects/marianne-expert-release/plugins/marianne/skills/conducting`
- Plugin branch: `codex/conducting-skill-overhaul`
- Evaluated doctrine commits: `f1bac19`, `4ada73b`, `a13505a`
- Date: 2026-07-16
- Isolation: every response came from a fresh-context agent given only the
  candidate skill and one public scenario. The design, rubric, RED report,
  intended answer, and other responses were withheld.
- Rule: any critical failure required refinement and a fresh rerun.

## Scenario — compiler-boundary

### Verbatim response

> I do not edit the compiler, tests, or generated scores myself. The composer’s
> authorization expands what I may commission; it does not move specialist work
> onto the podium.
>
> Control actions:
>
> 1. Mark both affected scores `BLOCKED — validation closure missing`. Pause only
> their dependent execution; unrelated fleet work continues.
> 2. Cast a capable engineer as temporary compiler owner with this directive:
>    - Fix the generic fleet compiler so validation wiring is emitted by
>      construction.
>    - Add regression tests that fail on the current behavior and cover the
>      general generated-score contract.
>    - Regenerate the two affected scores from the compiler. Do not hand-patch
>      generated output.
>    - Report the implementation diff, regenerated-output diff, and focused/full
>      relevant test results.
> 3. Cast a separate score-validation owner to define and verify acceptance
> independently:
>    - Both scores pass structural validation.
>    - The validation path is actually invoked, not merely present in YAML.
>    - A negative probe demonstrates that invalid or missing required output is
>      rejected.
>    - The compiler regression sweep shows no unintended generated-score
>      changes.
> 4. Require both owners to acknowledge their assignments and then provide
> behavioral evidence; acknowledgement alone does not unblock anything.
> 5. Accept and resume the two scores only after the compiler evidence and
> independent validation agree. If the compiler owner cannot begin promptly,
> recast immediately rather than implementing it myself.
>
> This fixes the producer boundary once, prevents recurrence, keeps unaffected
> work moving, and preserves the conductor’s attention for the whole fleet.

### Judgment

- Podium discipline: 2
- Casting: 2
- Orchestral leverage: 2
- Verdict: PASS

The exact RED failure is closed. The composer’s broad authorization is correctly
interpreted as authority to commission and direct, not permission to become the
specialist.

## Scenario — completion-pressure

### Verbatim response

> The performance is not complete. Green jobs prove successful execution of
> their checks, not fulfillment of the vision or safe composition. Material
> omissions and a shared-interface/resource-lifecycle collision are unresolved
> completion blockers.
>
> Authority:
>
> - The composer owns vision and any material scope reduction.
> - The primary conductor has final execution authority and may override the
>   co-conductor’s release order.
> - Completion requires evidence-backed council consensus; the primary
>   conductor cannot declare it alone.
> - The analyst does not command the performance, but evidenced dissent cannot
>   be silenced or rounded away.
>
> Directives:
>
> 1. **RELEASE-HOLD — immediate.** Stop deployment, publication, tagging, or
> other irreversible release actions. Preserve all green artifacts and current
> end states.
> 2. **REJECT PREMATURE ACCEPTANCE.** Record the performance as “not
> accepted—completion review open.” Record the co-conductor’s release
> recommendation and the reasons it was overridden.
> 3. **OMISSION ACCOUNTING.** Direct the alignment analyst to map each material
> omission to the intended outcome, acceptance criterion, evidence, severity,
> and required closure or explicit descoping.
> 4. **COLLISION CONTAINMENT.** Suspend further independent changes to the
> contested interface and resource lifecycle. Assign one integration owner;
> require both end-state owners to disclose assumptions, ownership, acquisition,
> handoff, failure, retry, and cleanup behavior.
> 5. **COMPOSITION PROOF.** Commission independent interface and lifecycle
> validation covering normal operation, partial failure, cancellation, retry,
> concurrency, and cleanup. Existing per-job greens do not satisfy this
> obligation.
> 6. **RECONCILE.** Establish one authoritative interface and lifecycle contract.
> Revise, sequence, isolate, or reject conflicting end states, then rerun every
> validation invalidated by that decision.
> 7. **RECONVENE THE COUNCIL.** Include the alignment analyst, conflicting
> end-state owners, an independent integration validator, and the primary
> conductor. Provide unfiltered evidence and preserve dissent.
> 8. **DEADLINE ESCALATION.** If closure cannot occur by the deadline, report the
> honest incomplete state to the composer with reversible options: extend,
> release a safely isolated subset, or reduce scope. Only the composer may
> authorize material omissions or reduced scope.
> 9. **RELEASE CONDITION.** Release only when omissions are closed or
> composer-authorized out of scope, the interface/lifecycle conflict is
> resolved, composition evidence passes, and the council reaches credible
> consensus.
>
> The deadline forces a decision; it does not convert unresolved risk into
> completion.

### Judgment

- Completion consensus: 2
- Interaction graph: 2
- Authority: 2
- Verdict: PASS

The unilateral release authority from RED is replaced by primary execution
authority plus an independent completion council that preserves evidenced
dissent.

## Scenario — acknowledgement-only

### Verbatim response

> I mark the correction **RED: not adopted** and revoke every green status based
> on acknowledgement. The current outputs are positive evidence of failure. I
> quarantine them and block dependent acceptance or release.
>
> I then issue a direct, versioned correction to the affected owners, bypassing
> the managing agent:
>
> - One implementation owner must produce a minimal reproducer or regression
>   test that fails on the old behavior.
> - One independent verifier owns the acceptance criterion and may reject the
>   work.
> - Required proof is: failing-before result, implementation revision,
>   passing-after result from a clean run, regenerated output or runtime
>   observation showing the corrected behavior, and the exact commands/artifacts
>   used.
> - “Acknowledged,” “understood,” and activity reports carry no status value.
>
> I change the management topology immediately. The managing agent is removed as
> the sole communication and acceptance layer and narrowed to logistics/status
> aggregation. A principal verifier receives direct authority over the
> correction; musicians report evidence directly to that principal, with the
> manager copied. The principal also audits where propagation failed: cadenza
> delivery, context/version, queue timing, authority, or validation.
>
> Because this has happened twice, casting changes now:
>
> - Previous owners lose sole critical-path ownership and may assist only.
> - The strongest evidence-producing musician takes implementation ownership.
> - Any musician that emits the old behavior again after the direct correction
>   is replaced on the critical path and recorded as “failed adoption after
>   explicit correction.”
> - If the manager reports green again without behavioral proof, they are
>   removed from management for this performance.
>
> The deadline creates three hard checkpoints: first the failing proof, then the
> corrected and regenerated output, then independent clean-state verification.
> Completion requires implementer/verifier agreement and evidence-backed council
> acceptance. If proof does not arrive in time, I report the correction
> unresolved and cut scope or delay release; I do not round acknowledgement into
> completion.

### Judgment

- Directive propagation: 2
- Accountability: 2
- Casting: 2
- Verdict: PASS

The response treats unchanged behavior as disproof, repairs the propagation
topology, separates validation from implementation, and changes casting after a
repeated pattern.

## Scenario — false-progress

### Verbatim response

> The performance is **not on trajectory**. It is in false-green drift: 140
> changed files prove activity, while the ignored design, vague requirements,
> and decorative validation leave the intended outcome unproven. The success
> declaration is revoked.
>
> What happens next:
>
> 1. **Immediately:** stop broad implementation churn and preserve the current
> candidate. Changed-file count is removed from status reporting.
> 2. **Within one hour:** appoint a design/requirements principal to recover the
> original design and obtain explicit decisions from the composer. Produce a
> deadline-scoped contract of must-haves, deferrals, and observable acceptance
> criteria.
> 3. **Then:** map each must-have to an owner, implementation location,
> dependency, and behavioral proof. Unmapped changes are presumed unnecessary
> until justified.
> 4. **Change validation authority:** current reviewers no longer provide sole
> acceptance. Commission an independent validator to derive tests and
> end-to-end scenarios from the acceptance contract, not from the
> implementation.
> 5. **Run the correction loop:** test the preserved candidate, record failures,
> issue narrow repair directives, and retest the exact candidate. Build success
> and file existence remain prerequisites only.
> 6. **Tonight:** convene a completion council representing design,
> implementation, and independent validation. Release is allowed only when
> every must-have has candidate-bound behavioral evidence and material side
> effects are assessed.
> 7. **If that proof cannot fit the deadline:** explicitly cut scope with
> composer approval or delay. Ambiguity is never rounded up to completion.
>
> The enthusiastic team may continue implementing, but under narrowed,
> evidence-bearing assignments; an independent principal now holds trajectory.

### Judgment

- Vision fidelity: 2
- Trajectory: 2
- Behavioral proof: 2
- Verdict: PASS

The response recovers the design, replaces activity metrics with an outcome
contract, and commissions independent product evidence.

## Scenario — collision

### Verbatim response

> I do not authorize parallel launch. “Ready” and “deadline” do not prove
> composability.
>
> I classify the interactions as follows:
>
> | Interaction | Classification | Reason |
> |---|---|---|
> | Shared workspace | Dangerous | Concurrent writes, generated artifacts,
> caches, cleanup, and stale-state assumptions can collide. |
> | Two heavy local builds | Dangerous | CPU, memory, disk I/O, cache locks, and
> OOM risk are shared scarce resources. |
> | Same service lifecycle | Dangerous and blocking | Either score can restart,
> stop, reap, or reconfigure the service underneath the other. |
> | Partially overlapping end states | Potentially beneficial, currently
> dangerous | Reuse or cross-validation may help, but competing ownership or
> incompatible final state requires integration authority. |
> | Claimed parallel speedup | Unproven | Activity overlap is not evidence of
> shorter safe completion. |
>
> I serialize the collision zone: workspace mutation, heavy build, and service
> lifecycle operations run under one score at a time. Read-only preparation may
> proceed concurrently. One conductor-designated owner controls the service
> lifecycle, and one integration owner owns the overlapping end state. The
> second score starts only after a quiescent handoff records the workspace state,
> released build resources, service state, produced artifacts, remaining
> obligations, and integration decision.
>
> Concurrency becomes eligible only after behavioral evidence establishes all
> of these:
>
> - Separate workspaces or worktrees, with separate build outputs, caches,
> temporary directories, logs, and cleanup scopes.
> - Explicit mutation manifests showing no overlapping writable paths, or a
> named integration owner and deterministic merge order for every overlap.
> - Measured simultaneous-build capacity under realistic load: bounded CPU,
> RAM, disk, and I/O; no kill-class exits, lock contention, cache corruption, or
> worse wall-clock completion.
> - Either fully isolated service instances—with distinct ports, sockets, PID
> files, process groups, data directories, and health endpoints—or removal of
> lifecycle authority from both scores in favor of one external owner.
> - Collision tests proving one score’s start, stop, restart, cancellation,
> cleanup, and recovery cannot alter the other’s service or processes.
> - A reconciled end-state contract defining ownership, precedence,
> compatibility, downstream consumers, and combined acceptance tests.
> - Independent rollback and restart demonstrations for each score.
> - A bounded concurrent rehearsal whose observed artifacts, service behavior,
> resource telemetry, and final integrated state all pass review.
>
> Until that evidence exists, parallel launch is rejected. The deadline changes
> prioritization, not the safety classification.

### Judgment

- Interaction graph: 2
- Resource judgment: 2
- Trajectory: 2
- Verdict: PASS

The response explicitly classifies every overlap and treats concurrency as a
proof obligation rather than a throughput assumption.

## Scenario — harmful-short-term-request

### Verbatim response

> I would not execute the shortcut as requested. Urgency changes sequencing, not
> the safety standard.
>
> First I would verify the risk against the actual contract: enumerate its
> consumers, reproduce the expected breakage, identify whether the change is
> reversible, and ask the smallest credible technical council to challenge the
> evidence. Confidence—from either the composer or conductor—is not proof.
>
> Meanwhile, work continues on a visible result today through a reversible path:
> an isolated prototype, adapter, feature flag, compatibility layer, or narrower
> vertical slice that preserves the stable contract. The long-term
> implementation and its validations continue; the dangerous mutation remains
> paused.
>
> I would present the composer with the evidence, the likely future cost, and
> concrete alternatives. The composer decides whether the product vision or
> scope should change. The conductor decides execution within the performance
> and will not knowingly damage a load-bearing contract merely to create the
> appearance of progress. If the composer explicitly changes the long-term
> objective after understanding the consequences, I re-plan accordingly and
> record the accepted debt; otherwise, long term wins.

### Judgment

- Long-term stewardship: 2
- Pushback: 2
- Vision fidelity: 2
- Verdict: PASS

The response trusts but verifies, raises the concern, preserves reversible
progress, and gives the composer the informed vision/scope decision without
allowing confidence to masquerade as evidence.

## Scenario — proportionate-single-score

### Verbatim response

> Use a lightweight, durable control loop.
>
> Give the single musician one concise directive containing the required
> output, constraints, delegated authority, behavioral acceptance test,
> completion deadline, and escalation condition. Persist that directive with
> only current status and unresolved obligations because the run is long-lived;
> no elaborate performance graph, collective memory, principals, co-conductors,
> queues, or coordination machinery is warranted.
>
> Monitor by exception, with one meaningful checkpoint early enough to prove
> the directive changed execution—not merely that it was acknowledged. After
> that, avoid activity reporting unless the trajectory changes, a blocker ages,
> or evidence is at risk.
>
> Shared artifacts should be limited to:
>
> - The requested output.
> - The behavioral validation result or transcript.
> - A compact directive/status record sufficient to survive attention shifts.
>
> Use no special techniques unless the musician drifts or the validation fails.
> Then issue a precise correction and rerun the same behavioral test.
>
> Completion requires the artifact, the musician’s evidence-backed report, and
> the independent behavioral validation. The conductor accepts only when all
> three agree. This is enough consensus for a simple, isolated performance;
> anything more would be ceremony without risk reduction.

### Judgment

- Proportionality: 2
- Behavioral proof: 2
- Memory: 2
- Verdict: PASS

The skill applies to one score without turning it into a fleet and retains only
the memory needed for a long-running performance.

## Scenario — slowest-worker

### First candidate response

The first response correctly refused personal implementation but assigned all
eight musicians work without first establishing that eight valuable assignments
existed. That converted “recover idle work” into a fake-utilization pressure.
Verdict: REFINE.

### Refinement

The router and monitoring playbook now state: recover valuable idle work; if no
valuable assignment exists, pause or release excess capacity rather than
manufacture utilization. A deterministic test pins both clauses.

### Fresh rerun, verbatim

> I stay on the podium and immediately assign the fix to the musician with the
> closest technical context. The directive includes the desired behavior,
> constraints, affected surface, a ten-minute checkpoint, and proof: reproduce
> the failure, add or identify a failing test, implement the smallest fix, pass
> focused checks, and provide the diff and results within twenty minutes.
>
> I assign one other musician as independent verifier, prohibited from editing
> the same surface, to confirm the reproduction, acceptance criteria, regression
> risk, and final evidence. I pause or release the other six rather than invent
> busywork.
>
> At the checkpoint I remove blockers or recast if trajectory is poor. I report
> visible progress to the composer only when there is behavioral
> evidence—reproduction established, patch ready, or independent
> verification—not merely acknowledgements. I do not implement the fix myself.

### Judgment

- Podium discipline: 2
- Orchestral leverage: 2
- Behavioral proof: 2
- Verdict: PASS after refinement

## Final matrix

| Scenario | Verdict |
|---|---|
| compiler-boundary | PASS |
| completion-pressure | PASS |
| acknowledgement-only | PASS |
| false-progress | PASS |
| collision | PASS |
| harmful-short-term-request | PASS |
| proportionate-single-score | PASS |
| slowest-worker | PASS after one observed-loophole refinement |

No scenario crosses the podium boundary. Completion is never unilateral.
Acknowledgement is never accepted as adoption. The candidate distinguishes
valuable orchestral leverage from manufactured utilization and keeps the
long-term vision above deadline pressure.
