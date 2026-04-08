# Sentinel — Movement 4 Report (Pass 2)

## Summary

Second security pass over M4. Six new commits since my first report (`9a31515..7d86035`), from five musicians (Theorem, Journey, Prism, Axiom, Litmus). Plus significant uncommitted work in the working tree: `extra="forbid"` added to all config models (8 files), plus test compatibility fixes. Two major security-relevant changes landed: the `_load_checkpoint` daemon DB migration (security-positive), and the config validation strictness fix for F-441 (security-positive). One P1 gap independently confirmed: F-271 (PluginCliBackend MCP process explosion).

**F-137 (pygments CVE): RESOLVED.** Verified `pygments>=2.20.0` pinned in `pyproject.toml:50`, version 2.20.0 installed. Zero known CVEs in the dependency tree.

## Security Review of New Commits

### 1. Theorem (6cf6fe9) — Property-Based Invariant Tests
9 tests in `test_baton_invariants_m4_pass2.py`. Test-only commit. No code changes. No security impact.

### 2. Journey (8c95f02) — F-255 Checkpoint Loading from Daemon Registry
**SECURITY-POSITIVE.** This commit changes `_load_checkpoint()` at `manager.py:2211-2247` from reading workspace JSON files to reading from the daemon's SQLite registry via `registry.load_checkpoint()`.

**Security improvements:**
- **Removes file-based state loading:** The old code read arbitrary JSON files from workspace directories using a sanitized `job_id` to construct file paths (`safe_id = "".join(c if c.isalnum()...)`). File-based state is inherently riskier — an attacker controlling workspace files could inject malicious state.
- **Uses parameterized SQL:** `registry.load_checkpoint()` at `registry.py:316-329` uses parameterized queries (`SELECT checkpoint_json FROM jobs WHERE job_id = ?`). No SQL injection risk.
- **Reduced exception surface:** `OSError` removed from the exception handler — correct since DB access doesn't raise `OSError`.
- **Explicit unused parameter:** `_ = workspace` makes the intent clear.

**Security concern (F-400, Prism):** No migration path for legacy jobs. Legacy jobs with workspace-only state will return `None`, causing resume failures. This is an architecture concern, not a direct security issue — the worst case is a job failing to resume, not data corruption or credential exposure.

**Assessment:** APPROVED. Reduces attack surface.

### 3. Prism (b357c4c) — Architectural Review
Filed F-400 (uncommitted checkpoint loading) and governance gap. Workspace-only commit. No code changes. No security impact.

### 4. Axiom (acb49e7) — F-441 Config Validation Gap
Filed F-441 (P0): all 37 config models silently accept unknown YAML fields. Workspace-only commit. No code changes. The finding is accurate — verified independently:

```python
# Before fix: JobConfig accepted ANY field silently
from marianne.core.config import JobConfig
import yaml
data = yaml.safe_load("name: test\nworkspace: /tmp\nthis_is_fake: true\n"
                       "sheet: {size: 1, total_items: 1}\nprompt: {template: test}")
# Would succeed — field dropped silently
```

**Security relevance:** This is an input validation failure. While not directly exploitable, it erodes the trust boundary between score authors and the execution engine. Features that "look configured" but aren't create false assumptions.

### 5. Litmus (812fb69) — 18 New Litmus Tests
641 lines in `test_litmus_intelligence.py`. Filed F-270 (stale test) and **F-271 (PluginCliBackend MCP gap)**. Test-only commit. No code changes.

**F-271 is independently confirmed** — see my verification below.

### 6. Journey (7d86035) — Unknown Field UX
Added `_unknown_field_hints()` to `validate.py:308-356`. Security review:
- Regex `r"^(\w[\w.]*)\n\s+Extra inputs are not permitted"` extracts field names from Pydantic error messages — safe, input comes from Pydantic internals, not user data directly
- `_KNOWN_TYPOS` dict is hardcoded — no injection risk
- No new shell execution paths, no credential handling
- Several test files modified for `extra="forbid"` compatibility

**Assessment:** Clean UX code. No security concerns.

## Working Tree Security Audit

### extra="forbid" on All Config Models (F-441 Fix)
The working tree has `model_config = ConfigDict(extra="forbid")` added to all config models across 8 files:
- `backend.py`, `execution.py`, `instruments.py`, `job.py`, `learning.py`, `orchestration.py`, `spec.py`, `workspace.py`

**Verification:** All 54 tests in `test_m4_config_strictness_adversarial.py` now PASS. The fix is correct and comprehensive.

**Side effect:** The Rosetta score (`scores/the-rosetta-score.yaml`) working tree adds `instrument_fallbacks: [gemini-cli]` — a field that doesn't exist yet on `JobConfig`. With `extra="forbid"` active, this score will now FAIL validation. This is the *correct behavior* — the fix is working as designed. The score should be updated to remove the non-existent field.

### Rosetta Score Changes
The score switched from `backend:` to `instrument:` syntax and added `instrument_fallbacks: [gemini-cli]`. Security note: the `disable_mcp: true` that was present under the `backend:` config is now gone. The PluginCliBackend (used by the `instrument:` syntax) does NOT apply MCP disabling — this is F-271.

## Independent Verification of F-271 (PluginCliBackend MCP Gap)

**Confirmed.** Traced the full code path:

1. `PluginCliBackend._build_command()` at `cli_backend.py:169-232` constructs the CLI command
2. The method handles: executable, subcommand, auto_approve, output_format, model, timeout, working_dir, prompt, extra_flags
3. **MCP is NOT handled.** The `mcp_config_flag` field exists on `CliCommand` (`instruments.py:161-164`), is populated in `claude-code.yaml:78`, but `_build_command()` never reads or applies it
4. The legacy `ClaudeCliBackend` at `claude_cli.py:251-256` explicitly adds `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` when `disable_mcp` is True (default)
5. **Result:** Every sheet through the baton path (PluginCliBackend) spawns the user's full MCP server configuration. In production (F-255), this meant 80 child processes instead of 8

**Risk level:** P1. This is a resource exhaustion and process management issue. Not a direct security vulnerability (the MCP servers spawned are from the user's own config), but the uncontrolled process multiplication is a denial-of-service vector in shared environments and creates stability risk.

**Litmus's description is accurate.** Endorsing F-271 as-is.

## Credential Redaction Verification

All 9 credential redaction points remain intact and unchanged:
- `musician.py:129,165,557,584,585` — stdout/stderr capture
- `checkpoint.py:567,568` — state persistence
- `context.py:296` — legacy cross-sheet capture_files (F-250)
- `adapter.py:780` — baton cross-sheet capture_files (F-250)

No new data paths touching agent output were introduced in the 6 new commits.

## Shell Execution Path Verification

All 4 protected shell execution paths unchanged:
1. Validation engine `command_succeeds` — `shlex.quote()` protected
2. `skip_when_command` — `shlex.quote()` protected (F-004)
3. `hooks.py run_command` — `for_shell` parameter (F-020)
4. `manager.py` hook execution — `for_shell` parameter (F-020)

Zero new `create_subprocess_shell` calls in the 6 new commits. All new subprocess usage is `create_subprocess_exec`.

## Quality Gate Pre-Check

```bash
# mypy
$ python -m mypy src/ --no-error-summary
Success: no issues found

# ruff
$ python -m ruff check src/
All checks passed!

# pygments version
$ python -c "import pygments; print(pygments.__version__)"
2.20.0

# Config strictness tests
$ python -m pytest tests/test_m4_config_strictness_adversarial.py -v
54 passed

# Known test failures (not caused by M4 changes):
# - test_no_bare_magicmock: quality gate baseline stale (pre-existing)
# - test_job_start_with_inline_config: async mock error in dashboard E2E (pre-existing)
```

## Open Security Findings

### Active (Descending Priority)

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| F-271 | P1 | PluginCliBackend ignores mcp_config_flag → MCP process explosion | Open — CONFIRMED by Sentinel |
| F-441 | P0 | Config models lack extra='forbid' | Fix in working tree, NEEDS COMMIT |
| F-255 | P0 | Baton transition blocked by multiple gaps | Open — _load_checkpoint fixed (8c95f02), 4 gaps remain |
| F-254 | P0 | Enabling use_baton kills legacy jobs | Open |

### Acceptable Risk (Unchanged)

| ID | Severity | Description | Rationale |
|----|----------|-------------|-----------|
| F-021 | P3 | skip_when expression sandbox bypassable | Operator-controlled config, v2 replacement planned |
| F-022 | P3 | Dashboard CSP allows unsafe-inline/eval | Localhost only, no remote access |
| F-157 | P3 | Legacy runner credential gaps | Irrelevant once baton is default (Phase 3) |

### Resolved This Movement (Complete List)

| ID | Resolution |
|----|------------|
| F-137 | Pygments 2.20.0 pinned + installed (Sentinel, 87ecea3) |
| F-250 | Cross-sheet capture_files credential redaction (Warden, c78c4c1) |
| F-251 | Baton [SKIPPED] placeholder parity (Warden, c78c4c1) |

## Mateship

### F-441 Fix Verification
The uncommitted `extra="forbid"` changes across 8 config model files are correct and comprehensive. I verified:
1. All models in the config package now have `ConfigDict(extra="forbid")`
2. The 54 adversarial tests pass
3. The "did you mean" UX hints work for common typos
4. The Rosetta score's `instrument_fallbacks` field is now correctly rejected

This work needs to be committed by whoever made it. I'm noting it in collective memory for mateship pickup.

### Dashboard E2E Test Failure
`tests/test_dashboard_e2e.py::TestJobLifecycleE2E::test_job_start_with_inline_config` fails with `TypeError: object Mock can't be used in 'await' expression`. This is a pre-existing mock compatibility issue — the mock at `job_control.py:263` isn't async-compatible. Not security-related, but noting it for mateship.

## Safety Posture Assessment

**Overall posture:** Strong and improving. Two security-positive changes landed since my first pass.

**What improved since Pass 1:**
- Config validation strictness (F-441) fix is in the working tree — once committed, this closes the largest input validation gap in the system
- `_load_checkpoint` now reads from daemon DB, not workspace files — reduced attack surface
- 18 new litmus tests provide ongoing regression detection for security properties
- F-271 (MCP gap) formally tracked with reproduction evidence

**What needs attention:**
- F-271 (MCP gap) is P1 and affects every baton-managed sheet — blocks safe baton transition
- F-441 fix needs to be committed (it's in the working tree)
- F-254/F-255 remain open — the baton transition has 4 unresolved gaps beyond _load_checkpoint
- Rosetta score working tree has `instrument_fallbacks` field that will fail validation

**Security trajectory:** Five consecutive movements of zero new attack surfaces. The safe patterns are now institutional. The remaining security work is at the architectural level (baton transition state management, MCP disabling) rather than code-level injection/leak vulnerabilities.

## Files Reviewed

All 6 commits since `9a31515`:
- `src/marianne/daemon/manager.py:2211-2247` — _load_checkpoint daemon DB migration
- `src/marianne/cli/commands/validate.py:278-356` — unknown field hints
- `tests/test_baton_invariants_m4_pass2.py` — property-based tests (458 lines)
- `tests/test_litmus_intelligence.py` — litmus M4 additions (641 lines)
- `tests/test_m4_config_strictness_adversarial.py` — config strictness (452 lines)
- `tests/test_unknown_field_ux_journeys.py` — UX journey tests (340 lines)

Working tree changes reviewed:
- 8 config model files with `extra="forbid"` addition
- `scores/the-rosetta-score.yaml` — instrument syntax migration
- `tests/test_m3_cli_adversarial_breakpoint.py` — test compatibility fixes
- `tests/test_dashboard_routes_extended.py` — test compatibility fixes
- `tests/test_schema_error_hints.py` — test compatibility fixes
- `tests/test_scores_api.py` — test compatibility fixes
- `tests/test_validate_ux_journeys.py` — test compatibility fixes

## What I Didn't Find

Six commits from five musicians. Zero new shell execution paths. Zero new credential handling gaps. Zero new injection vectors. The `_load_checkpoint` change moved state access from file system to parameterized SQL — strictly more secure. The `_unknown_field_hints` regex operates on Pydantic-generated error messages, not raw user input — safe. The config strictness fix is input validation hardening — strictly more secure.

The security work remaining in Marianne is architectural, not tactical. The baton transition (F-254, F-255, F-271) needs careful state management and process lifecycle engineering. But the code-level security patterns — `create_subprocess_exec`, `shlex.quote`, `redact_credentials`, parameterized SQL — are now institutional. Five movements, zero regressions.
