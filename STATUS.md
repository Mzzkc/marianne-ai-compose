# Marianne AI Compose — Status

Do not trust this file blindly. Run `pytest tests/ -x`, `mypy src/`, and `ruff check src/` to verify current state. Check `git log --oneline -20` for recent work.

## Current Phase

v0.1.0-alpha — **P0 "Ship" complete** (2026-05-29): M1 front door, M2 CI/release/safety,
M3 backlog triage, M4 sandbox-free onboarding all shipped. CI enforces ruff + mypy --strict +
pytest (85% coverage) on every push; `v0.1.0-alpha` is the first tagged release. Next: **P1 Launch**
(M5 Baton Stabilization, M6 Onboarding, M7 Market Positioning). Baton is the sole execution model.

## Known Issues

Launch-blocking limitations are catalogued in `KNOWN-ISSUES.md`. For the live, prioritized backlog,
check `gh issue list --repo Mzzkc/marianne-ai-compose` (every open issue is tier-labeled P0–P3 and
milestone-assigned M5–M9 / Backlog).

## Architecture

See `.marianne/spec/` for the specification corpus. See the project instruction files (e.g., `GEMINI.md`, `CLAUDE.md`) for development guidance.
