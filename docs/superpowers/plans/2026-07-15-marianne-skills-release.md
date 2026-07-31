# Marianne Skills Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a canonical, capability-aware Marianne Expert and executable composition release gates.

**Architecture:** The plugin repository owns both skills. Small Python tools enforce mechanical requirements; concise SKILL files retain judgment and route to those tools. Installed copies are verified deployment outputs.

**Tech Stack:** Markdown skills, Python 3.11, `unittest`, PyYAML, Marianne `JobConfig`, git CLI.

## Global Constraints

- Preserve the dirty catalog in the user's original plugin checkout.
- Write tests before each script or behavior change and observe the expected failure.
- Do not infer mutation authorization from writable files.
- Do not describe composing policy as Marianne parser behavior.
- Do not deploy a skill whose candidate was not forward-tested.

---

### Task 1: Establish plugin tests and canonical expert package

**Files:**
- Create: `plugins/tests/test_marianne_expert_release.py`
- Create: `plugins/marianne/skills/marianne-expert/` from the installed verified kit

**Interfaces:**
- Consumes: installed kit at `~/.agents/skills/marianne-expert`
- Produces: canonical plugin skill with `SKILL.md`, `VERSION`, playbooks, contracts, evidence and tools

- [ ] Write tests asserting required release files, frontmatter fields, no historical workspace paths, and a compact router.
- [ ] Run `python -m unittest tests.test_marianne_expert_release -v` in the plugin and observe missing-package failures.
- [ ] Copy the existing kit into the canonical plugin location without publisher workspaces or cache files.
- [ ] Run the tests and record the passing result.

### Task 2: Add capability preflight

**Files:**
- Create: `plugins/marianne/skills/marianne-expert/scripts/preflight.py`
- Create: `plugins/tests/test_expert_preflight.py`
- Modify: `plugins/marianne/skills/marianne-expert/SKILL.md`
- Modify: `plugins/marianne/skills/marianne-expert/BOOTSTRAP.md`

**Interfaces:**
- Produces: `collect_capabilities(repo: Path | None, write_authorized: bool, probe_online: bool) -> dict[str, object]`
- CLI: `preflight.py [--repo PATH] [--source-write-authorized] [--probe-online] [--output PATH]`

- [ ] Write tests for no repository, clean repository, dirty-file fingerprint, and explicit authorization.
- [ ] Run the test module and observe import/missing-function failures.
- [ ] Implement deterministic local discovery and optional official-site reachability probing.
- [ ] Update the router to require preflight and scoped evidence precedence.
- [ ] Run tests, `quick_validate.py`, and `wc -w SKILL.md`.

### Task 3: Add exact release manifests

**Files:**
- Create: `plugins/marianne/skills/marianne-expert/scripts/release_manifest.py`
- Create: `plugins/tests/test_expert_manifest.py`
- Create: `plugins/marianne/skills/marianne-expert/agents/openai.yaml`

**Interfaces:**
- CLI: `release_manifest.py build|verify ROOT [--output MANIFEST.sha256]`
- Digest input excludes `.git`, `__pycache__`, `*.pyc`, and the generated manifest.

- [ ] Write tests for deterministic ordering, changed content, extra files and relocation.
- [ ] Observe the tests fail because the command does not exist.
- [ ] Implement build and verify modes using stdlib SHA-256.
- [ ] Generate `agents/openai.yaml` with only display name, short description and default prompt.
- [ ] Build and verify the canonical manifest in two different directories.

### Task 4: Add composition design gate

**Files:**
- Create: `plugins/marianne/skills/composing/scripts/check_design.py`
- Create: `plugins/tests/test_composition_design_gate.py`
- Modify: `plugins/marianne/skills/composing/SKILL.md`

**Interfaces:**
- CLI: `check_design.py DESIGN.yaml`
- Required keys: `goal`, `authority`, `forces`, `stages`, `context_flow`, `injections`, `proof_obligations`, `repair_loop`, `release`.

- [ ] Write fixtures for a valid design, missing authority, dangling dependency, and release-without-reevaluation.
- [ ] Observe the expected missing-command failure.
- [ ] Implement validation with clear path-qualified findings and nonzero exit on errors.
- [ ] Update composing so the approved design artifact and successful checker are required before YAML.
- [ ] Run the focused tests.

### Task 5: Add score release gate and lock

**Files:**
- Create: `plugins/marianne/skills/composing/scripts/check_score_release.py`
- Create: `plugins/tests/test_composition_score_gate.py`
- Modify: `plugins/marianne/skills/composing/SKILL.md`
- Modify: `plugins/marianne/skills/score-authoring/SKILL.md`

**Interfaces:**
- CLI: `check_score_release.py SCORE.yaml --project-root PATH [--lock PATH] [--write-lock]`
- Produces: canonical JSON containing score SHA, injection SHA map, and combined candidate SHA.

- [ ] Write tests for missing/empty injections, workspace overlap, deterministic CLI fallback, weak file-only validation, valid score, lock mismatch and relocation.
- [ ] Observe missing-command failures.
- [ ] Implement loading through `JobConfig.from_yaml`, `build_sheets`, runtime-equivalent injection resolution, policy checks and lock verification.
- [ ] Replace composing's universal fallback rule with deterministic-sheet and evaluator exceptions.
- [ ] Run focused and complete plugin test suites.

### Task 6: Deploy and forward-test the candidate

**Files:**
- Modify: `~/.codex/skills/marianne-expert/`
- Modify: `~/.agents/skills/marianne-expert/`

**Interfaces:**
- Consumes: verified canonical plugin directory
- Produces: byte-identical installed copies

- [ ] Run the read-only RED Marianne score with the old installed skill and archive outputs.
- [ ] Build the candidate manifest.
- [ ] Replace both installed copies from the verified candidate.
- [ ] Verify both manifests and `diff -qr` the installations.
- [ ] Run the same Marianne score and require current-source reconciliation plus capability evidence.
- [ ] If behavior still fails, refine the smallest relevant skill text and rerun from a fresh workspace.

