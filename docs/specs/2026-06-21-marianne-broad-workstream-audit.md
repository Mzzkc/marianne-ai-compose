# Marianne Broad Workstream Audit - 2026-06-21

This audit/work map is disk-backed as of 2026-06-21. It records current dirty paths and
what they belong to; it is not a claim that every item is ready to merge.

Test evidence in this file is cumulative from the current handoff run. As of
2026-06-23 the root static gates, compiler gates, and full root pytest suite
have been re-run after the final cold-start fixes; re-run the named suites
before commit because the worktree is active.

| Path(s) | Feature Bucket | Intentional? | Tested? | Needs Follow-up? | Disposition |
|---|---|---:|---:|---:|---|
| `compiler/` submodule | Generic fleet compiler, techniques, hook validation, workspace seed | Yes | Yes: compiler suite passed 168/4 skipped; generated generic fleet validated live | Re-run compiler tests before final commit | Commit after root pointer and submodule commit are aligned |
| `compiler/src/marianne_compiler/assets/`, `presets.py`, `workspace_seed.py` | Built-in `generic-fleet` preset and shared cadenza seed | Yes | Yes: live compile produced 32 scores + `fleet.yaml`; recompile preserved shared active file | Keep reviewing against canonical specs | Commit |
| `compiler/src/marianne_compiler/{pipeline,sheets,techniques,identity,validations,naming}.py` | Technique injection, lifecycle hook generation, project-prefixed score naming, identity/memory seeding, validation | Yes | Yes: compiler and root targeted suites passed; BC9K prefixed fleet artifact audit passed | Verify generated external-output hook behavior stays documented | Commit |
| `plugins/` submodule | Marianne plugin skills/techniques/catalog | Yes | Yes: skill validators for embed/conducting; plugin contents read/audited | Re-run skill validator after edits | Commit after plugin submodule commit |
| `plugins/marianne/skills/marianne-embed/` | Generic Marianne wrapper/embed skill | Yes | Yes: frontmatter/quick validation and bridge JSON behavior verified | Keep synchronized with installed skill | Commit |
| `plugins/marianne/skills/conducting/` | Conducting/composing skill | Yes | Yes: quick validation; guidance sharpened from dashboard/MCP/hoard work | Apply to one more conductor cycle if edited again | Commit |
| `plugins/marianne/techniques/*-specialist.md` | Generic fleet specialist voices/skills | Yes | Yes: emitted/injected in generic fleet preview | Voice comparison remains a design review area, not a failing test | Commit |
| `plugins/marianne/techniques/coordination.md` | Cadenza/stigmergic coordination | Yes | Yes: compile preview includes coordination contract and shared active files | Reconcile final wording with canonical specs | Commit |
| `docs/guides/{a2a,code-mode,interface-reference,mcp-pool,technique-guide}.md` | Runtime technique/A2A/MCP/code-mode docs | Yes | Yes: docs aligned to tests; interface focused tests passed 5/5 | Re-read after final runtime edits | Commit |
| `docs/instrument-guide.md`, `docs/limitations.md` | Instrument profile/MCP config documentation | Yes | Yes: profile and MCP tests passed; live Claude/Antigravity/Gemini behavior audited | Gemini remains live-blocked by provider eligibility | Commit |
| `docs/specs/2026-04-09-generic-agent-score-design.md` | Canonical generic-agent design spec updates | Yes | Yes: generic fleet compile/validation proof | Confirm no checklist-only material drifted into canonical spec | Commit if reconciled |
| `docs/specs/2026-06-10-interactive-mode-design.md` | Interactive mode/Antigravity proof updates | Yes | Yes: interactive tests and live Antigravity smokes previously passed | Re-run focused interactive tests before final | Commit |
| `docs/specs/2026-06-21-generic-fleet-cadenza-coordination.md` | Cadenza checklist/addendum candidate | Yes, as non-canonical candidate | Partially: compile seed/injection verified | Must stay labeled non-canonical unless composer approves | Commit or move under addenda if desired |
| `docs/specs/validation-gaps-addendum.md` | Validation blind-spot registry | Yes | Yes: new dashboard/report gaps reflect discovered failures | Add entries for any further gaps found | Commit |
| `src/marianne/daemon/baton/{adapter,events,musician}.py` | A2A routing, phase scoping, inbox injection, MCP/code-mode dispatch, cold-start job-state guards | Yes | Yes: A2A/MCP focused suites and full root suite passed; `__new__` register/deregister adversarial cases pass | No live paid fleet A2A run launched | Commit |
| `src/marianne/daemon/{manager,registry,config,fleet,mcp_pool,mcp_socket_bridge,technique_router}.py` | Conductor-owned MCP pool, fleet dependency execution, status/hook results, technique routing | Yes | Yes: MCP manager/wiring/dispatch tests; fleet dependency regression; diagnose verified old canyon/forge hook failures surface | Verify no stale conductor before live runs | Commit |
| `src/marianne/execution/{code_mode,sandbox,interface_gen}.py` | Code mode, sandbox mounts, generated technique runtime | Yes | Yes: interface tests 5/5, code-mode/MCP focused suites, ruff | Full root should be re-run after any code edit | Commit |
| `src/marianne/execution/instruments/cli_backend.py` | CLI MCP config injection/cleanup | Yes | Yes: profile/MCP dispatch tests; live Claude/Antigravity smokes | Gemini provider block remains documented | Commit |
| `src/marianne/instruments/builtins/{claude-code,gemini-cli,goose,antigravity}.yaml` | Built-in instrument profiles | Yes | Yes: profile suite; Claude and Antigravity live shared-MCP proof; Gemini config shape verified but provider-blocked | Antigravity profile is untracked; ensure intended | Commit |
| `src/marianne/validation/checks/techniques.py` | Technique validation | Yes | Yes: root/compiler validation tests | Re-run focused validation tests before commit | Commit |
| `src/marianne/cli/commands/{compile,diagnose,run,status}.py` | Built-in compile command, fleet-aware run command, diagnose hook reporting, resilient status validation rendering | Yes | Yes: CLI/diagnose/status tests; generated fleet dry-run; `--pause-before-chain` and `--job-prefix` targeted tests; live diagnose on canyon/forge surfaces hook results | Old canyon/forge scores still failed historically; not a current regression | Commit |
| `src/marianne/dashboard/**` | Dashboard editor/logs/status/jobs/system responsiveness | Yes | Yes: dashboard/API tests 465 passed, Playwright 15 passed, live dashboard smoke | Re-run after any route/template edits | Commit |
| `tests/test_dashboard*`, `tests/test_scores_api.py`, `tests/test_daemon_state_adapter.py` | Dashboard regression coverage | Yes | Yes: 465 dashboard/API tests and 15 Playwright tests passed | Keep browser tests in final validation set | Commit |
| `tests/test_a2a_wiring.py` | Multi-agent A2A delegation coverage | Yes | Yes: focused and full suites passed | Persistence remains documented as future work | Commit |
| `tests/test_mcp_*`, `tests/test_builtin_instrument_profiles.py`, `tests/test_goose_profile.py`, `tests/test_instrument_user_journeys.py` | Shared MCP/profile/dispatch coverage | Yes | Yes: focused MCP/profile suite and full root passed | Live provider limits documented | Commit |
| `tests/test_fleet_config.py`, `tests/test_cli_run_resume.py` | Fleet run CLI and dependency execution coverage | Yes | Yes: focused tests passed; live two-score CLI fleet proved dependent score started after root marker | Keep live drill in final smoke set when fleet execution changes | Commit |
| `tests/test_interface_gen.py` | Generated technique runtime coverage | Yes | Yes: 5/5 after docs alignment | None current | Commit |
| `tests/test_generic_fleet_{preset_contract,research_score}.py` | Generic fleet and research score contract | Yes | Yes: targeted and full suites passed | Re-run after compiler asset edits | Commit |
| `scores/generic-fleet-technique-research.yaml` | Technique research score | Yes | Yes: contract tests validate score shape | Outputs already read; keep internet-derived material reviewed before transformation | Commit |
| `workspaces/generic-fleet-technique-research/research/**` | Research outputs referenced by compiler/fleet design | Existing workspace artifact | Read/audited | Do not execute internet-derived material | Usually not committed unless workspace artifacts are intentionally tracked |
| `scores/prep/thinking-lab.yaml`, `scores/prep/thinking-lab-input/**` | Website/thinking-lab prep spillover | Likely yes, separate from Marianne runtime | Not part of Marianne runtime suites | Confirm composer wants these in this branch | Commit only if this site-review work belongs here |
| `scores/instrument-catalog-build-fold.yaml`, examples GLM model bumps, `scores/legion-dream.yaml` | Instrument catalog/model-name drift cleanup | Likely yes | YAML parsed by full root where covered; no live model call | Verify GLM 5.2 availability remains intended | Commit |
| `.gitignore` | Local tool artifact ignore (`.aider*`) | Likely yes | Not test-bearing | Confirm this belongs globally | Commit if desired |
| `scores/rosetta-corpus` submodule pointer | Rosetta corpus pointer | Unclear from root status; submodule worktree is clean | Not tested in this run | Confirm pointer movement is intentional | Commit pointer only if composer/previous work intended it |

## Current Runtime Truth

- Conductor is running and no dashboard server is intentionally left running.
  On 2026-06-23 the dashboard/browser verification first exposed a live runtime
  isolation bug: `create_app(state_backend=...)` still auto-bound the real
  conductor, letting tests consume the app rate limit and leave daemon event-bus
  subscriptions behind. The dashboard factory now keeps explicit-backend apps
  isolated unless `connect_daemon=True`, clears event/system globals when no
  daemon client is wired, keeps analytics backend-derived, and lets tests pass a
  `rate_limit_config`. Playwright disables the limiter because dedicated
  rate-limit tests cover that behavior. After the fix, focused verification
  passed: Ruff on touched dashboard files, mypy on touched dashboard routes, 258
  dashboard/API tests, 207 additional dashboard tests, and 15 Playwright tests.
  The live conductor had already become IPC-unresponsive before the isolation
  fix; direct registry inspection showed zero active jobs, graceful SIGTERM
  logged clean shutdown, the stale PID was force-cleared, and `mzt start`
  restored conductor PID 151942. Final machine checks: `mzt status --json`
  returned `active_count: 0`; the embed bridge emitted valid JSON on stdout with
  debug logs on stderr; `proof12-canyon` remained `paused_at_chain`; no Marianne
  tmux server or hoard/post/verify processes were running.
- `verify-mill` recurrence was disabled earlier; process checks should remain
  part of final validation (`mzt status`, no `sleep 300` verify process).
- A full paid/generated 32-agent generic-fleet execution has not been launched
  during the current proof. Compile, validation, seed preservation, preview
  render, generated fleet dry-run, a separate two-score live conductor fleet
  dependency drill, and the bounded single-agent proof12 live cycle were tested
  instead.
- Backyard Capitalism 9000 now has an ignored prefixed fleet workspace at
  `~/Projects/backyard-capitalism-9000/workspaces/marianne-bc9k-fleet`.
  It was generated with `mzt compile --preset generic-fleet --pause-before-chain
  --job-prefix bc9k-`, seeded with BC9K target directives in `shared/active/`,
  audited for no Kimi/Llama, portable bounded hooks, unprefixed agent identity,
  and validated 33/33 YAML files. A full paid 32-agent live launch has not yet
  been run.
- As of 2026-06-23, BC9K10 provided a live generated-fleet runtime proof but
  also exposed a cadenza truth gap: completed plan sheets could leave
  `01-task-board.md` / `02-agent-status.md` rows in `claimed` state. The
  compiler now emits opt-in cadenza completion validations for the generic
  fleet's shared-active phases. Proof11 generated 32 prefixed scores plus
  `fleet.yaml`, validated 33/33 YAMLs, confirmed zero absolute fleet member
  paths, passed `mzt run .../fleet.yaml --dry-run --json`, and launched live
  with doctor-ready Claude/Codex instruments. The live run proved recon/plan
  cadenza validation for all six initial agents, current rolling-phase cadenza
  validation through integration for Captain/Atlas/Tempo/North/Bedrock, Bedrock
  work validation, and Ghost plan validation; North's recon and Tempo inspect
  demonstrated the documented `COORDINATION UPDATE BLOCKED:` fallback under
  shared-file write contention. It also surfaced three remaining generator/seed
  defects: status rows could still use local CEST times with a `Z` suffix,
  seeded format examples reserved real owner-scoped IDs such as `north-D-001`,
  and inspect cadenza validation looked for `cycle-state/{agent}-inspect.md`
  instead of the required `cycle-state/{agent}-inspection.md`. Proof11 was
  cancelled at 2026-06-23T20:47Z to stop stale generated-score retry loops after
  the inspect-path bug was root-caused; no proof11 tmux sessions remain.
  (The tmux server process can still display the first proof11 `new-session`
  command as its argv while hosting an unrelated active session.)
- A fresh patched proof12 compile at `/tmp/mzt-generic-proof12.y5cmDj`
  generated 32 prefixed scores plus `fleet.yaml`, validated 33/33 YAML files,
  dry-ran the fleet as 32 scores, confirmed all fleet member paths are relative,
  confirmed active seed examples use non-reserving placeholders instead of
  concrete IDs, confirmed inspect cadenza evidence points at
  `cycle-state/canyon-inspection.md`, and confirmed the generated prompt and
  cadenza validator contain the UTC timestamp requirement/future-skew check.
  `proof12-canyon` then ran live from the freshly patched score and reached
  `PAUSED_AT_CHAIN` after all 12 sheets completed or skipped as intended
  (sheet 6 skipped by the temperature gate), with 11/12 completed, every run
  validation passing, and the self-chain hook passing with `pause_before_chain`.
  The live proof specifically exercised the patched inspect cadenza path:
  sheet 7 passed with evidence `cycle-state/canyon-inspection.md`, no
  `cycle-state/canyon-inspect.md` artifact existed, the active task row
  `canyon-T-005` was `done`, and status timestamps were UTC-minute values such
  as `2026-06-23T21:03Z` and final `2026-06-23T21:10Z`.
- Stale old `bc9k10-*` generated-fleet proof jobs were cancelled after verifying
  they were pre-fix proof work waiting on rate/capacity, not current production
  work. Current runtime check after proof12 shows conductor running with
  `active_count: 0`, no Marianne tmux server, and no proof10/proof11/proof12
  processes.
- Gemini CLI shared-MCP live dispatch remains blocked by the provider's current
  `UNSUPPORTED_CLIENT` / tier eligibility response on this machine; config-file
  shape and cleanup are tested.
- Hoard-mill was audited side-effect-free from `~/Projects/hoard-mill`
  on 2026-06-23. `mzt validate` passed for `scores/post-mill.yaml` (9 sheets,
  22 validations), `scores/verify-mill.yaml` (4 sheets, 9 validations), and
  `scores/clip-mill.yaml` (56 sheets, 14 validations). The `/mnt/e` directory
  exists but is not mounted in WSL, Windows-side `cmd.exe` checks found no
  `E:\clip-hoard`, and searches under `/mnt/c`, `/mnt/d`, and the home directory
  found no real `clip-hoard` project root. Live staging/archive directory counts
  and cadence files therefore could not be re-read. Local authoritative
  artifacts show the June 22/23 post-mill run planned 20 pairs across Instagram
  and TikTok, posted/scheduled 17 batch entries plus the Instagram canary,
  recorded one definitive `forget-past-future` Instagram Drive lookup failure,
  promoted zero permanent rejections, and moved zero folders in the archive
  stage while skipping 155 not-yet-accounted staging entries. `state.json`
  currently has 104 scheduled entries, 39 canceled, 18 failed, and 0 permanent
  rejections; `REJECT_ATTENTION.flag` still exists from 2026-05-12 despite the
  empty permanent-rejection set and should be treated as stale until the mounted
  project root can be inspected. `verify-mill` remains disabled pending scheduler
  redesign; its last local cycle report is 2026-06-20 and clean (24 checked,
  24 verified, 0 failed). Process checks after score validation found no
  post/verify/clip/buffer/hoard workers and no `sleep 300` verify recurrence.

## Current Broad Validation - 2026-06-23

- `ruff check src tests` passed.
- `mypy src/` passed (`275 source files`).
- `cd compiler && ruff check src tests && mypy src/ && pytest -q` passed
  (`168 passed, 4 skipped`).
- First `pytest -q tests/` exposed seven cold-start/status regressions:
  `_job_learning_configs` was missing on `BatonAdapter.__new__` register and
  deregister paths, and status validation rendering assumed mock sheet objects
  exposed `failed_validations`/`validation_details`.
- After adding the adapter per-job map guard and status optional-field fallback,
  the seven exact failures passed, touched-file Ruff/mypy passed, root Ruff/mypy
  passed again, and `pytest -q tests/` passed with `10797 passed, 58 skipped,
  22 xfailed, 3 xpassed, 1 warning`.
- `git diff --check` passed after the edits, and `mzt status --json` still
  reported the conductor running with `active_count: 0`.

## Final Validation Set Before Commit

- `ruff check src tests`
- `mypy src/`
- `pytest -q tests/test_interface_gen.py`
- A2A/MCP/profile focused tests
- Dashboard focused + Playwright tests
- `cd compiler && pytest -q`
- `pytest -q tests/`
- Hoard-mill score validations from `~/Projects/hoard-mill`
- Clip-stage-reviewer browser/keyboard/filesystem smoke if that project is in scope for the commit
