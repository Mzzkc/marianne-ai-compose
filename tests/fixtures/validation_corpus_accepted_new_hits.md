# S3 corpus baseline refresh

The initial pre-overhaul baseline is commit `cca6a3da`. The following new
findings were read in score context before this refresh. They are accepted
true positives, not exceptions in the sweep test. Later growth remains a test
failure until separately reviewed.

| Score | Code and count | Context |
| --- | --- | --- |
| `examples/engineering/codebase-rewrite.yaml` | V217 ×1 | `on_success.job_path` is the literal `[CHANGE THIS: /absolute/path/to/codebase-rewrite.yaml]`; no such score exists. |
| `examples/engineering/quality-triage.yaml` | V217 ×1 | `on_success.job_path` is the literal `[CHANGE THIS: ABSOLUTE path to examples/engineering/issue-solver.yaml]`; no such score exists. |
| `examples/engineering/score-composer.yaml` | V110 ×1 | `prompt.variables.project_root` has no score reference; INFO only. |
| `examples/finance/24x7-trader/pre-market.yaml` | V110 ×1 | `prompt.variables.today_iso` has no score reference; INFO only. |
| `scores/instrument-catalog-build.yaml` | V110 ×1 | `prompt.variables.lab_workspace` has no score reference; INFO only. |
| `scores/instrument-catalog-refresh.yaml` | V110 ×2 | `prompt.variables.catalog_changelog_path` and `catalog_md_path` have no score references; INFO only. |

The first V105 sweep found shell `${name}` and embedded Python f-string local
variables, so that check was narrowed before this refresh. V218's first sweep
matched directory arguments to `find` and `gh`; it was narrowed to explicit
file-extension paths. Neither false-positive class was added to the baseline.

Forge's flow branch adds `examples/patterns/convergence-loop.yaml` after the
Sentinel capture. It was read and appended to the fixture inventory with its
single V205 INFO finding (a file_exists-only validation); the sweep inventory
now requires every current venue example and score YAML to have a fixture row.

The flow join also introduced `FLOW_RESERVED_NAMES`, the shared built-in-name
source required by Blueprint A7. Rebinding V208 to that source exposed exactly
**204 WARN findings in 102 persistent-agent stock scores**: every file declares
`stakes` and `thinking_method` under `prompt.variables`, while the prompt
renderer overwrites both from separate `prompt.stakes` and
`prompt.thinking_method` fields. This is a proved content-delivery defect,
filed as `shared/findings/P1-bedrock-stock-prompt-variables-shadow-stakes.md`
in the S3 Work workspace. The 102 rows each gain V208 ×2 in the baseline;
the warnings remain visible and are not suppressed. T6 score migration owns
their correction and a different agent must verify it.

# loud_silence stage refresh (score-language build, 2026-10-14)

V327 (reserved key set) landed with the D4 reserve sweep; every new hit was
read in score context before this refresh. True positives, not exceptions.

| Score | Code and count | Context |
| --- | --- | --- |
| `agents:adversary/scores/full-lifecycle.yaml` | V327 ×3 | Sets `rate_limit.wait_minutes`, `rate_limit.max_waits`, and `retry.completion_threshold_percent` — all accepted-but-unread today (the first two also equal their defaults). The warning is the designed behaviour; agent-owned score, left for its owner. |
| `agents:adversary/scores/lifecycle-integration.yaml` | V327 ×3 | Same shape as full-lifecycle: `retry.completion_threshold_percent`, `rate_limit.wait_minutes`, `rate_limit.max_waits` — accepted-but-unread; agent-owned, left for its owner. |
| 106 agent and venue scores (stock shapes; see `/tmp` sweep summary in the
stage report) | V327 ×1–3 each | Stock engagement shapes set `rate_limit.wait_minutes`, `rate_limit.max_waits`, and/or `retry.completion_threshold_percent` — accepted-but-unread today (mostly at their defaults). Agent-owned scores left for owners; rosetta mirrors (byte-identical to the submodule) and `negative-treatment-watch.yaml`'s `cost_limits.max_cost_per_sheet` (stage-7 wiring pending) absorbed here rather than edited. |

# MN-007 profile-loader repair (2026-10-21)

The former sweep fixture recursively called its patched loader. The repaired
fixture loads all 11 shipped profiles and asserts that the registry is nonempty.
Each row below was reviewed against the score's instrument declarations and
the emitted diagnostics. Only the specified growth is accepted; unrelated
expectations and the growth assertion remain intact.

V325 is an INFO summary for the explicitly selected `strong` capability class.
V210 reports host-specific names absent from the shipped registry. V215 for
`hello-local` follows from its sole unresolved `opencode-gemma` AI route.
These are true diagnostics under the hermetic shipped-profile test, not new
validator false positives. The host-dependent examples still require the
profiles described in their prerequisites; this baseline does not install or
certify them.

| Score | Accepted growth | Reviewed reason |
| --- | --- | --- |
| `examples/engineering/codebase-rewrite.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/engineering/issue-solver.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/engineering/project-roadmap.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/engineering/quality-continuous.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/engineering/quality-triage.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/engineering/saas-app-builder.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/getting-started/hello-local.yaml` | V210 ×1, V215 ×1 | Top-level `opencode-gemma` requires the user profile described at lines 44–48; no shipped profile resolves the chain. |
| `examples/patterns/dead-letter-quarantine.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/patterns/echelon-repair.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/patterns/immune-cascade.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/patterns/prefabrication.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/patterns/shipyard-sequence.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/patterns/source-triangulation.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/research/research-agent.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/cathedral-construction.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/condorcet-s-premise.yaml` | V210 ×25, V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. Host-coupled jury references six custom profiles on 30 sheets; five V210 hits were already recorded, so growth is 25. |
| `examples/rosetta/firing-the-pass.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/join-semilattice-merge.yaml` | V210 ×6, V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. `music-critic`, `gpt-5.6-repair`, and `opencode-glm-5.3-flash` each occur in a movement and a per-sheet override; none is shipped. |
| `examples/rosetta/live-relay.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/negative-treatment-watch.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/rashomon-gate.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/the-attested-merge-gate.yaml` | V210 ×1, V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. Movement 18 selects host profile `opencode-gemma`. |
| `examples/rosetta/the-economic-injury-line.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `examples/rosetta/the-precedent-bench.yaml` | V210 ×1, V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. Movement 18 selects host profile `opencode-gemma`. |
| `examples/rosetta/the-unprimed-falsifier.yaml` | V210 ×25, V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. The explicitly documented host `opencode-gemma` reader occupies 24 per-sheet overrides and movement 7. |
| `scores/instrument-catalog-build-fold.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `scores/instrument-catalog-build.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `scores/instrument-catalog-refresh-fold.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `scores/instrument-catalog-refresh.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
| `scores/thinking-lab.yaml` | V325 ×1 | Top-level `instrument: strong` resolves through the shipped class map. |
