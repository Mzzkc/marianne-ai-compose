# Marianne AI Compose — Status

Do not trust this file blindly. Run `pytest tests/ -x`, `mypy src/`, and `ruff check src/` to verify current state. Check `git log --oneline -20` for recent work.

## Current Phase

v0.1.0-alpha — **P0 "Ship" complete** (2026-05-29); **P1 Launch in progress**. CI enforces
ruff + mypy --strict + pytest (85% coverage) on every push. Baton is the sole execution model.

### M5 Baton Stabilization — autonomous work complete (2026-06-12)

**M4 Sandbox Resolution is now empty** (#160, #210 closed). Every M5 item resolvable without a
composer decision or a named escalation-gate crossing is shipped and CI-green. The remainder is a
precise, verified **composer-decision queue** — each item is gated by the spec's own escalation rules
(`.marianne/spec/constraints.yaml`), so completing them autonomously would violate the no-assumptions
constraint:

| Issue | Gate | Disposition |
|-------|------|-------------|
| #137 | Schema decision | Lab-ruled infeasible without a producer signal; prerequisite filed as **#384** (`produces:` primitive). Build #384 → #137 becomes a bounded WARNING-first check. |
| #197 | Architecture decision | Lab-ruled: per-sheet worktree isolation as filed silently breaks shipped F-210 (cross-sheet context). Needs per-job re-scope. |
| #209 | Security review | Stage 2a (regex classification) is live-capable but cosmetic without Stage 3; Stage 3 executes agent-generated shell/python with no sandbox on bwrap-less hosts — needs a security review before activation. |
| #344 | ~~gated~~ **fixed** | obs2 (goose idle SIGKILL) resolved by the liveness gate (`d7c2b04`); obs1 (opus exit-nonzero-after-commit) structural fix shipped (`18226a9`) — a plain non-zero exit whose declared validations all pass is rescued to success. Issue kept open per the reproduce-or-mark instruction, pending a live-repro confirmation of the sporadic trigger. |
| #171 / #332 | 0-drift sequencing | Profile hot-reload extends existing SIGHUP (no CLI gate), but the composer-directed single-instrument-system unification must land first to avoid drift. |
| #58, #384 | Product/schema | Workspace-lifecycle vision (#58); the `produces:` schema primitive (#384). |

**Composer-domain action items** (named gates): plugins submodule push (cross-repo); `mzt doctor --clean`
(deletes the measured ~1.2G dead nested tree + stale clone artifacts — confirmation-gated).

## Known Issues

Launch-blocking limitations are catalogued in `KNOWN-ISSUES.md`. For the live, prioritized backlog,
check `gh issue list --repo Mzzkc/marianne-ai-compose` (every open issue is tier-labeled P0–P3 and
milestone-assigned M5–M9 / Backlog).

## Architecture

See `.marianne/spec/` for the specification corpus. See the project instruction files (e.g., `GEMINI.md`, `CLAUDE.md`) for development guidance.
