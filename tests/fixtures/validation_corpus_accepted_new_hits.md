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
