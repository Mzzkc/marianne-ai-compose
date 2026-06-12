# Marianne AI Compose — Status

Do not trust this file blindly. Run `pytest tests/ -x`, `mypy src/`, and `ruff check src/` to verify current state. Check `git log --oneline -20` for recent work.

## Current Phase

v0.1.0-alpha — **P0 "Ship" complete** (2026-05-29); **P1 Launch in progress**. CI enforces
ruff + mypy --strict + pytest (85% coverage) on every push. Baton is the sole execution model.

### M5 Baton Stabilization — autonomous work complete (2026-06-12)

**M4 Sandbox Resolution is empty** (#160, #210 closed). After a composer interview unblocked the
gated items, the M5 baton-stabilization surface is essentially clear. Shipped CI-green this arc:

| Issue | Result |
|-------|--------|
| #344 | **fixed** — obs2 (goose idle SIGKILL) by the liveness gate (`d7c2b04`); obs1 (opus exit-nonzero-after-commit) structural fix (`18226a9`): a plain non-zero exit whose declared validations all pass is rescued to success. Kept open for a live-repro confirmation per instruction. |
| #197 | **shipped** (`f538850`) — per-JOB git worktree isolation (composer chose per-job; preserves F-210). |
| #209 | **shipped** (`116c6cf`) — opt-in code execution (Stages 2a+3); default off, no bwrap requirement, loud V304 validation warning when unsandboxed. |
| #171 / #332 | **shipped** (`155ae5f`) — SIGHUP instrument-profile hot-reload (composer corrected: the instrument system is plugin-only, not mid-unification). Issue **closed** 2026-06-12. |
| #359 | **shipped** (`771feb6`) — `mzt run --var k=v` runtime variables. |
| #137 | **closed** (`c62ac01` + `57b2232`) — cadenza-ordering validation via the composer-directed signal-proxy approach (no `produces:` field). V109 warns when a cadenza reads a file whose declared producer (a sheet-gated `file_exists`) is not a DAG-ancestor; V108 suppresses the false "not found" for runtime-produced files. |
| #58 | **core shipped** (`0030c43`) — conductor auto-derives `~/workspaces/<score-name>` when `workspace:` is omitted; `--workspace` is now a hidden expert override; managed-root ensured for preflight. Issue kept **open** for the archive/fresh-state verification residual. |

**Remaining (composer's call):**
- **#344** — kept open for a live-repro confirmation (obs1 + obs2 both structurally fixed in code).
- **#58** — archive/fresh-state verification residual (the auto-derivation core is done; the reuse/`--fresh` sub-items are not re-verified in code).
- **#384** — `produces:` schema primitive: now an *optional strengthening* of #137's proxy approach, no longer a blocker (the composer's design to specify).
- **Action items** (named gates): plugins submodule push (cross-repo); `mzt doctor --clean` (deletes the measured ~1.2G dead nested tree + stale clone artifacts — confirmation-gated).

## Known Issues

Launch-blocking limitations are catalogued in `KNOWN-ISSUES.md`. For the live, prioritized backlog,
check `gh issue list --repo Mzzkc/marianne-ai-compose` (every open issue is tier-labeled P0–P3 and
milestone-assigned M5–M9 / Backlog).

## Architecture

See `.marianne/spec/` for the specification corpus. See the project instruction files (e.g., `GEMINI.md`, `CLAUDE.md`) for development guidance.
