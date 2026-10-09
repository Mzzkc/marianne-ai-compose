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
