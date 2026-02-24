# Mozart AI Compose - Claude Instructions

Project-specific instructions for AI assistants working on this codebase.

---

## Session Protocol

**Session Start:**
1. Read `STATUS.md` (root) - Quick status overview
2. Read `memory-bank/activeContext.md` - Current state and focus
3. Read `memory-bank/projectbrief.md` if unfamiliar with project purpose
4. Check `memory-bank/progress.md` for what's been done

**Session End:**
1. Update `memory-bank/activeContext.md` with session results
2. Update `STATUS.md` if phase/component status changed
3. Add entry to `memory-bank/progress.md` if significant work
4. Create session summary in `memory-bank/sessions/` if >1hr complex work

---

## Code Patterns

**Async Throughout:** All I/O operations use async. CLI backend uses `asyncio.create_subprocess_exec`.

**Pydantic v2:** All config and state models use Pydantic BaseModel with validation.

**Protocol-Based:** Backends and state storage use Protocol classes for swappability.

**Type Hints:** All functions have type hints. Use `mypy` for checking.

---

## Bug Reporting

When you discover bugs, test failures, or regressions while working on Mozart, **file them as GitHub issues immediately** — don't just note them in passing or leave them as TODOs.

```bash
gh issue create --repo Mzzkc/mozart-ai-compose \
  --title "Short description of the bug" \
  --body "## Bug\n\nDescription, root cause, reproducer, and fix options." \
  --label "bug"
```

Include: root cause analysis, reproducer steps, and fix options. Don't file vague "this is broken" issues — explain *why* it's broken so it can be fixed without re-investigation.

---

## Key Files

| Purpose | File |
|---------|------|
| CLI entry | `src/mozart/cli/` (package: `__init__.py`, `helpers.py`, `output.py`, `commands/`) |
| Conductor commands | `src/mozart/cli/commands/conductor.py` (`start`, `stop`, `restart`, `conductor-status`) |
| Config models | `src/mozart/core/config/` (package: `backend.py`, `execution.py`, `job.py`, `learning.py`, `orchestration.py`, `workspace.py`) |
| State models | `src/mozart/core/checkpoint.py` |
| Error handling | `src/mozart/core/errors/` (package: `classifier.py`, `codes.py`, `models.py`, `parsers.py`, `signals.py`) |
| Execution runner | `src/mozart/execution/runner/` (package: `base.py`, `sheet.py`, `lifecycle.py`, `recovery.py`, `cost.py`, `isolation.py`, `patterns.py`, `models.py`) |
| Learning store | `src/mozart/learning/store/` (package: 14 modules including `base.py`, `drift.py`, `budget.py`, `patterns_*.py`) |
| Claude backend | `src/mozart/backends/claude_cli.py` |
| State storage | `src/mozart/state/json_backend.py` |
| Fan-out expansion | `src/mozart/core/fan_out.py` |
| Example config | `examples/sheet-review.yaml` |
| Fan-out example | `examples/parallel-research-fanout.yaml` |
| Prelude/Cadenza example | `examples/prelude-cadenza-example.yaml` |

---

## Running Mozart Jobs

**The conductor is required for `mozart run`.** Without a running conductor, only `--dry-run` and `mozart validate` work. Start the conductor with `mozart start`.

**CRITICAL: Use `setsid` for detached conductor startup.**

```bash
# Start conductor (required before any job execution)
mozart start

# For detached conductor startup (e.g., from scripts):
setsid mozart start &

# WRONG: External timeout corrupts state
timeout 600 mozart run my-job.yaml
```

Individual jobs submitted via `mozart run` are managed by the daemon and do not need `setsid`.

**NEVER stop the conductor while jobs are actively running.** `mozart stop` kills the daemon process, which orphans all in-flight Claude agent processes and corrupts job state (sheets stuck as `in_progress` forever, no validation, no cleanup). Use `mozart pause` on individual jobs first, wait for them to pause, then stop. If you must reload config on a running job, use `mozart modify -c new.yaml --resume --wait`.

**Resuming vs. Fresh starts:**

If a job was interrupted, cancelled, or failed mid-progress, use `mozart resume` to pick up from the last checkpoint:
```bash
mozart resume <job-id> --workspace <dir>
```

`--fresh` is appropriate when starting a genuinely new run — e.g., self-chaining jobs that completed an iteration, or when the user asks to start over. It is NOT appropriate for jobs that were interrupted mid-progress, because it deletes checkpoint state and archives workspace artifacts, wiping hours of work. When in doubt, try `mozart resume` first.

**Monitoring:**
```bash
mozart status my-job -w ./workspace --watch
tail -f workspace/mozart.log
```

**Pausing Jobs:**
```bash
# Pause a running job gracefully
mozart pause my-job --workspace ./workspace

# Modify config and resume (one command)
mozart modify my-job --config updated.yaml --workspace ./workspace --resume

# Or use the two-step workflow (config auto-reloads on resume)
mozart pause my-job -w ./workspace
mozart resume my-job -c updated.yaml -w ./workspace
```

For comprehensive Mozart usage guidance, see: `/home/emzi/.claude/skills/mozart-usage.md`

---

## Debugging Mozart Errors

**CRITICAL: Use Mozart's diagnostic tools FIRST, before manual investigation.**

### Debugging Protocol (follow this order)

```bash
# 1. Check current status
mozart status <job-id> --workspace <dir>

# 2. Get full diagnostic report
mozart diagnose <job-id> --workspace <dir>

# 3. View error history
mozart errors <job-id> --verbose

# 4. THEN investigate code if needed
```

### Understanding Validation Failures

If `mozart status` shows "Validation: ✗ Fail":
- The execution ran, but output didn't meet requirements
- Check WHICH validation failed, not just that it failed
- The work may be complete but missing a required marker

```bash
# Check specific validation failures
cat workspace/my-job.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
sheet = d['sheets']['6']  # Change sheet number
for v in sheet.get('validation_details', []):
    status = '✓' if v.get('passed') else '✗'
    print(f'{status} {v.get(\"description\", \"unknown\")}')"
```

### Common Pitfalls

1. **Assuming exit_code=1 means failure** - Validations may have passed
2. **Not checking validation details** - ONE failed validation = sheet fails
3. **Looking at old logs** - Check file modification times
4. **Manual debugging before using tools** - ALWAYS run `status`/`diagnose` first

### Full Error Details

The `mozart.log` only shows truncated `stdout_tail` (500 chars). The state file captures up to 10KB per sheet in `sheets[N].stdout_tail` and `stderr_tail`.

```bash
# Extract errors from state file
cat workspace/my-job.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for k, s in d.get('sheets', {}).items():
    stdout = s.get('stdout_tail', '')
    if stdout and ('Error' in stdout or 'error' in stdout):
        print(f'=== Sheet {k} ===')
        print(stdout)
"
```

### Recovery Options

```bash
# Resume (re-executes sheet)
mozart resume job-id --workspace ./dir

# Recover (validates without re-execution, hidden command)
mozart recover job-id --workspace ./dir --dry-run
```

### Reference Skill

See: `/home/emzi/.claude/skills/mozart-usage.md` (global skill)

---

## Self-Healing & Enhanced Validation

Mozart includes two complementary features for error recovery:

### Enhanced Validation (`mozart validate`)

The validate command performs comprehensive pre-execution checks beyond schema validation:

```bash
# Basic validation
mozart validate my-job.yaml

# JSON output for CI/CD
mozart validate my-job.yaml --json
```

**Exit Codes:**
- `0`: Valid (warnings/info OK)
- `1`: Invalid (one or more errors)
- `2`: Cannot validate (file not found, YAML unparseable)

**Validation Checks:**
| Check | Description | Severity |
|-------|-------------|----------|
| V001 | Jinja syntax errors | ERROR |
| V002 | Workspace parent missing | ERROR (auto-fixable) |
| V003 | Template file missing | ERROR |
| V007 | Invalid regex patterns | ERROR |
| V101 | Undefined template variables | WARNING |
| V103 | Very short timeout | WARNING |
| V108 | Missing prelude/cadenza files (static paths) | WARNING |

### Self-Healing (`--self-healing`)

When enabled, Mozart automatically diagnoses and fixes common issues when retries are exhausted:

```bash
# Enable self-healing
mozart run my-job.yaml --self-healing

# Auto-confirm suggested fixes
mozart run my-job.yaml --self-healing --yes

# Works with resume too
mozart resume job-id --self-healing
```

**How It Works:**
1. All configured retries attempted first
2. On exhaustion, diagnostic context collected
3. Applicable remedies identified and ranked
4. Automatic remedies applied without prompting
5. Suggested remedies prompt unless `--yes`
6. Diagnostic guidance shown for manual-fix issues
7. If any remedy applied, retry counter reset

**Built-in Remedies:**

| Remedy | Category | Triggers On |
|--------|----------|-------------|
| Create missing workspace | Automatic | E601 - workspace doesn't exist |
| Create parent directories | Automatic | E601/E201 - path parent missing |
| Fix path separators | Automatic | Backslashes on Unix paths |
| Suggest Jinja fix | Suggested | Template syntax errors |
| Diagnose auth errors | Diagnostic | E101/E102/E401 |
| Diagnose missing CLI | Diagnostic | CLI not found |

**Key Files:**
| File | Purpose |
|------|---------|
| `src/mozart/validation/` | Enhanced validation module |
| `src/mozart/healing/` | Self-healing module |
| `tests/test_validation_checks.py` | Validation tests (29 tests) |
| `tests/test_healing.py` | Healing tests (32 tests) |

---

## Testing

```bash
cd ~/Projects/mozart-ai-compose
source .venv/bin/activate

# Validate config
mozart validate examples/sheet-review.yaml

# Dry run
mozart run examples/sheet-review.yaml --dry-run

# Type check
mypy src/

# Lint
ruff check src/
```

---

## Development

```bash
# Install editable with dev deps
pip install -e ".[dev]"

# Run tests
pytest

# Watch for changes (future)
pytest --watch
```

---

## Writing Tests — Flaky Test Prevention

Tests MUST be deterministic. Flaky tests erode trust and block automated pipelines (including the issue-solver). These patterns have caused real failures in this codebase:

### Never: Fixed sleeps for async coordination

```python
# WRONG — races under load, too slow when it works
await asyncio.sleep(2.0)
assert manager.status == "completed"

# RIGHT — poll with deadline
deadline = asyncio.get_event_loop().time() + 10.0
while asyncio.get_event_loop().time() < deadline:
    status = await get_status()
    if status in ("completed", "failed"):
        break
    await asyncio.sleep(0.2)
assert status == "completed"
```

Fixed sleeps are the #1 source of flaky tests. Daemon concurrency (`max_concurrent_jobs` semaphore), event propagation, and process cleanup all have variable latency. Always poll for the expected state with a generous deadline.

### Never: Tight timing assertions

```python
# WRONG — fails under WSL, CI, or debug logging load
assert elapsed < 5.0, f"took {elapsed}s"

# RIGHT — generous bound that validates correctness, not perf
assert elapsed < 30.0, f"took {elapsed}s"
```

If you need a performance benchmark, put it in a separate benchmark suite, not in CI tests. Timing assertions should only catch gross regressions (10x), not tight budgets.

### Never: Assume PID 1 behavior

```python
# WRONG — PermissionError on non-root/WSL, EPERM on containers
os.kill(1, 0)  # Check if PID 1 is alive

# RIGHT — mock os.kill when testing process-alive detection
with patch("os.kill", return_value=None):
    # Now the "process alive" check always succeeds
    ...
```

PID 1 (`init`/`systemd`) behavior varies by platform. Mock `os.kill` instead of relying on specific PIDs.

### Never: Cross-test state leakage

```python
# WRONG — module-level mutable state shared across tests
_global_cache = {}

class TestFoo:
    def test_a(self):
        _global_cache["key"] = "value"  # Leaks to test_b

# RIGHT — reset shared state in fixtures
@pytest.fixture(autouse=True)
def _clear_cache():
    _global_cache.clear()
    yield
    _global_cache.clear()
```

Tests that pass alone but fail in the full suite indicate state leakage. Common culprits: module-level caches, class variables, singleton instances, monkeypatched globals not restored.

### Never: Assume mypy/ruff output is stable across environments

If a validation script runs `mypy` or `ruff`, changes from OTHER concurrent work (concerts, issue-solver) can introduce new errors that fail your validation. Type annotations must be correct — don't return `Any` from typed functions, don't leave `# type: ignore` on lines that no longer need it.

### Checklist for new tests

1. Does it use `asyncio.sleep()` for coordination? → Replace with polling loop
2. Does it assert timing < N seconds? → Use generous bounds (10x expected)
3. Does it reference specific PIDs or process state? → Mock `os.kill`/`os.getpid`
4. Does it pass alone but fail in `pytest tests/`? → Find and isolate shared state
5. Does it depend on filesystem timing (mtime)? → Use `retry_count`/`retry_delay_ms` in validations, or mock
6. Run the full suite (`pytest tests/ -x`) before declaring tests pass

---

## Worktree Isolation (Parallel-Safe Jobs)

Mozart supports git worktree isolation for safe parallel job execution. When enabled, each job runs in an isolated git worktree, allowing multiple jobs to modify code simultaneously without interference.

### When to Use Worktree Isolation

- Running multiple code-modifying jobs in parallel
- Needing clean commit validation without other jobs' changes
- CI/CD environments where multiple agents work on the same repo

### Configuration

```yaml
# In your job config
isolation:
  enabled: true           # Opt-in to worktree isolation
  mode: worktree          # Only mode currently supported
  cleanup_on_success: true    # Remove worktree after success
  cleanup_on_failure: false   # Keep for debugging on failure
  lock_during_execution: true # Prevent accidental removal
  fallback_on_error: true     # Continue without isolation if creation fails
```

### How It Works

1. At job start, Mozart creates a git worktree in detached HEAD mode
2. The backend's working directory is set to the worktree path
3. All sheet executions happen within the isolated worktree
4. On completion, worktree is cleaned up based on config

### Key Files

| File | Purpose |
|------|---------|
| `src/mozart/isolation/worktree.py` | GitWorktreeManager implementation |
| `src/mozart/core/config/` | IsolationConfig schema (in config package) |
| `examples/worktree-isolation.yaml` | Example configuration |
| `tests/test_worktree.py` | Comprehensive worktree tests |

### Benefits

- **Fast isolation:** ~24ms per worktree (negligible vs API latency)
- **Storage efficient:** Git objects shared between worktrees
- **Automatic cleanup:** Even if Mozart crashes, `git worktree prune` cleans up

---

## Conductor Mode

Mozart uses a long-running conductor process that manages multiple concurrent jobs, coordinates rate limits across jobs, and provides a Unix socket IPC interface.

### Quick Start

```bash
# Start the conductor (foreground, for development)
mozart start --foreground

# Start the conductor (background, production)
mozart start

# Check conductor status
mozart conductor-status

# Stop the conductor
mozart stop

# Submit a job via CLI (auto-detects running conductor)
mozart run my-job.yaml  # Routes through conductor if running
```

### How CLI Routes Through Conductor

When you run `mozart run`, the CLI checks for a running conductor via the Unix socket at the default IPC path. If a conductor is found, the job is submitted for execution. If no conductor is running, `mozart run` exits with an error. The conductor is required for job execution — only `--dry-run` and `mozart validate` work without it.

### Key Daemon Files

| Purpose | File |
|---------|------|
| Daemon config | `src/mozart/daemon/config.py` |
| Type definitions | `src/mozart/daemon/types.py` |
| Output protocol | `src/mozart/daemon/output.py` |
| Job execution service | `src/mozart/daemon/job_service.py` |
| Daemon process entry | `src/mozart/daemon/process.py` |
| Job manager | `src/mozart/daemon/manager.py` |
| Resource monitor | `src/mozart/daemon/monitor.py` |
| System probe | `src/mozart/daemon/system_probe.py` |
| Process groups | `src/mozart/daemon/pgroup.py` |
| Cross-job scheduler | `src/mozart/daemon/scheduler.py` |
| Rate limit coordination | `src/mozart/daemon/rate_coordinator.py` |
| Backpressure/load mgmt | `src/mozart/daemon/backpressure.py` |
| Centralized learning | `src/mozart/daemon/learning_hub.py` |
| Semantic analyzer | `src/mozart/daemon/semantic_analyzer.py` |
| Daemon detection | `src/mozart/daemon/detect.py` |
| Health checks | `src/mozart/daemon/health.py` |
| Job registry | `src/mozart/daemon/registry.py` |
| Task utilities | `src/mozart/daemon/task_utils.py` |
| Event bus (pub/sub) | `src/mozart/daemon/event_bus.py` |
| Daemon exceptions | `src/mozart/daemon/exceptions.py` |
| IPC server | `src/mozart/daemon/ipc/server.py` |
| IPC client | `src/mozart/daemon/ipc/client.py` |
| JSON-RPC protocol | `src/mozart/daemon/ipc/protocol.py` |
| IPC handler | `src/mozart/daemon/ipc/handler.py` |
| IPC errors | `src/mozart/daemon/ipc/errors.py` |

### Conductor Debugging Protocol

```bash
# 1. Check if conductor is running
mozart conductor-status

# 2. List active jobs
mozart list

# 3. Check a specific job
mozart status <job-id>

# 4. View conductor logs
tail -f ~/.mozart/mozart.log
```

### Architecture

The daemon uses a layered architecture:

1. **IPC Layer** — Unix socket + JSON-RPC 2.0 for client-daemon communication
2. **Job Manager** — Tracks job lifecycle, handles submission/cancellation
3. **Event Bus** — Async pub/sub for runner callback events (`sheet.started`, `sheet.completed`, `sheet.failed`, `sheet.retrying`, `sheet.validation_passed/failed`, `job.cost_update`, `job.iteration`). Runners fire events via `_fire_event()`, the manager routes them to the event bus for downstream consumers (dashboard, learning, webhooks).
4. **Scheduler** *(Phase 3 — built & tested, not yet wired)* — Cross-job sheet scheduling with priority and fair-share. Infrastructure is ready; jobs currently run monolithically via JobService.
5. **Rate Coordinator** *(Phase 3 — partially wired)* — Shares rate limit state across concurrent jobs. Write path active (rate limit events flow from runners via `JobManager._on_rate_limit`). Read path not yet wired (scheduler doesn't consume the data).
6. **Backpressure** — Load management to prevent resource exhaustion (active: gates job submission and provides memory-based pressure levels)
7. **Resource Monitor** — Tracks CPU/memory/process usage
8. **Learning Hub** — Centralizes pattern learning across all daemon jobs
9. **Semantic Analyzer** — LLM-based analysis of sheet completions. Subscribes to EventBus `sheet.completed`/`sheet.failed` events, sends context to an LLM, and stores insights as `SEMANTIC_INSIGHT` patterns in the learning store. Always-on when conductor is running (configurable via `DaemonConfig.learning`).

---

## Patterns from Naurva Scripts

This project generalizes patterns from `/home/emzi/Projects/Naurva/run-sheet-review.sh`:

1. **State checkpoint** - JSON file with last_completed_sheet, status
2. **Fallback state inference** - Check artifact files if checkpoint missing
3. **Rate limit detection** - Pattern matching on output
4. **Separate retry vs wait counters** - Rate limits don't consume retries
5. **Validation before marking complete** - File existence + mtime checks
6. **Atomic state saves** - Write temp file, then rename

---

## Reference Docs

- `docs/daemon-guide.md` - Daemon setup, configuration, and troubleshooting
- `docs/score-writing-guide.md` - How to author Mozart scores
- `docs/configuration-reference.md` - Every config field documented
- `docs/limitations.md` - Known limitations and workarounds
- `docs/cli-reference.md` - Complete CLI command reference

## Reference Skills

When working on this project, relevant skills:
- `/home/emzi/.claude/skills/mozart-score-authoring.md` - **Mozart score/config writing guide (syntax, validations, prompts, features)**
- `/home/emzi/.claude/skills/mozart-usage.md` - **Mozart debugging and usage guide (global)**
- `/home/emzi/.claude/skills/session-startup-protocol.md`
- `/home/emzi/.claude/skills/session-shutdown-protocol.md`
- `/home/emzi/.claude/skills/wolf-prevention-patterns.md`

---

*Created: 2025-12-18*
