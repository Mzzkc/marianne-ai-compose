# QA Score Suite

Adversarial testing overhaul for Mozart, split into focused scores that can run independently after shared infrastructure is set up.

## Run Order

```
qa-foundation          # 1. MUST be first — installs deps, creates fixtures
    │
    ├── qa-core        # 2. Config, checkpoint, fan-out, errors, templating
    ├── qa-execution   #    Runner, lifecycle, recovery, cost, escalation
    ├── qa-daemon      #    Manager, IPC, scheduler, event bus, health, monitor
    ├── qa-cli         #    Commands, helpers, output rendering
    ├── qa-dashboard   #    Routes, services, auth, SSE, security
    └── qa-validation  #    Validation checks, healing, remedies
          │
    qa-integration     # 3. Cross-module e2e tests (requires all module scores done)
          │
    qa-overnight       # 4. Long-running smoke tests (~55 min), standalone/nightly
```

**Phase 1 — Foundation:** `qa-foundation` installs hypothesis, pytest-xdist, and pytest-randomly. Creates `conftest_adversarial.py` with shared fixtures and strict mock helpers. Sets up coverage baseline at 80%.

**Phase 2 — Module scores:** The six module scores are independent of each other and can run in any order (or in parallel across separate workspaces). Each follows the same structure: audit existing tests, parallel 3-way overhaul (fix flaky, property-based, adversarial), merge, coverage gate, commit.

**Phase 3 — Integration:** `qa-integration` writes cross-module end-to-end tests with minimal mocking. Depends on all module scores being complete so the test improvements are in place.

**Phase 4 — Overnight:** `qa-overnight` runs ~55 minutes of smoke tests against a real conductor (start, submit, pause/resume, concurrent jobs, crash recovery). Doesn't modify code — designed for nightly cron.
