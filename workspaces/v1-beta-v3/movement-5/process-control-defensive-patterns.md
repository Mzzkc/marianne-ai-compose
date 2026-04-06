# Process Control Defensive Patterns

**Author:** Harper, Movement 5
**Source:** F-490 coverage review (Agent 2 + Agent 3 combined)
**Status:** Complete

## Background

F-490 was a blast-radius bug: `os.killpg(1, SIGTERM)` compiled to `kill(-1, SIGTERM)` in the kernel, killing the entire WSL session. The fix (`_safe_killpg`) is narrow but correct. This document codifies the defensive pattern project-wide and audits siblings.

## The Pattern

Every syscall that hands a PID, PGID, signal, or file descriptor to the kernel is a **blast-radius decision**. The guard pattern is:

1. **Validate the target** before the syscall (pgid > 1, pid > 0, not own pgroup)
2. **Wrap in try/except** for ProcessLookupError, PermissionError, OSError
3. **Log at WARNING** when the guard refuses (visible in conductor.log)
4. **Tests must mock the syscall** AND validate the guard branches

## Syscall Audit Results (2026-04-06)

### os.killpg — ALL GUARDED

| File | Line | Guard |
|------|------|-------|
| `backends/claude_cli.py` | 606, 926, 1003 | All route through `_safe_killpg()` (4-layer: pgid<=1, own pgroup, try/except, logging) |
| `daemon/pgroup.py` | 154 | `is_leader` check + member count + SIG_IGN dance + ProcessLookupError/PermissionError |
| `daemon/pgroup.py` | 339 | atexit handler — same guards as line 154 |

### os.kill (destructive, signal != 0) — ALL GUARDED

| File | Line | Guard |
|------|------|-------|
| `dashboard/services/job_control.py` | 602 | SIGTERM → 2s grace → signal 0 check → SIGKILL. Full try/except. |
| `dashboard/services/job_control.py` | 611 | SIGKILL after grace period. Guarded by existence check at 609. |
| `daemon/process.py` | 200 | Internal daemon signal dispatch. PID from validated conductor state. |

### os.kill (signal 0 only) — SAFE BY NATURE

Signal 0 is a non-destructive existence check. Found in: `cli/helpers.py:466`, `cli/commands/doctor.py:97`, `core/checkpoint.py:1100`, `dashboard/services/job_control.py:609,685,735,774`, `daemon/process.py:964`. All wrapped in try/except.

### os.waitpid — ALL GUARDED

| File | Line | Guard |
|------|------|-------|
| `daemon/pgroup.py` | 205, 305 | WNOHANG, ChildProcessError catch, zombie-only reaping |
| `daemon/system_probe.py` | 107, 239 | WNOHANG, ChildProcessError catch |

### os.setpgrp — GUARDED

`daemon/pgroup.py:98` — idempotency check, OSError handling, fallback to "already leader" detection.

### process.kill() / process.terminate() — ALL GUARDED

All asyncio subprocess kill/terminate calls are wrapped in try/except ProcessLookupError. Escalation pattern (SIGTERM → wait → SIGKILL) used consistently in: `backends/claude_cli.py`, `execution/runner/lifecycle.py`, `execution/validation/engine.py`, `execution/hooks.py`, `bridge/mcp_proxy.py`, `daemon/manager.py`.

### subprocess.Popen with preexec_fn — NOT FOUND

All subprocess creation uses `start_new_session=True` instead of `preexec_fn=os.setsid`. Safer — avoids GIL issues.

## Sibling Bug Classes

No new sibling bugs found. All process-control syscalls follow the defensive pattern:

1. **Validate before calling** (pgid/pid range checks, signal 0 probes)
2. **Catch expected errors** (ProcessLookupError, PermissionError, OSError, ChildProcessError)
3. **Escalation pattern** (SIGTERM → grace period → SIGKILL)
4. **SIG_IGN dance** for killpg (prevent self-kill when signaling own process group)

## Recommendations for Constraints Spec

Add to `.mozart/spec/constraints.yaml`:

```yaml
M-011:
  rule: "Every os.kill/os.killpg call MUST go through a guarded helper"
  rationale: "F-490 — unguarded killpg(1, sig) killed the WSL session 9 times"
  enforcement: "grep + code review"

M-012:
  rule: "Guard MUST validate pgid/pid > 1 and != own process group"
  rationale: "pgid ≤ 1 maps to kernel-level kill(-1, sig) or kill(0, sig)"
  enforcement: "unit test coverage of guard branches"

M-013:
  rule: "Tests MUST NOT patch os.killpg without also verifying guard behavior"
  rationale: "Mocked kill bypasses the real guard, hiding regressions"
  enforcement: "quality gate check"
```

## Beyond Process Control

Other syscall families with similar blast radius potential:

| Family | Risk | Current Status |
|--------|------|----------------|
| File descriptors (`os.close`, `os.dup2`) | Close wrong fd → crash unrelated code | Not used directly (asyncio handles) |
| Socket paths (`os.unlink`) | Delete wrong socket → break IPC | Clone path sanitization handles this |
| Signal handlers (`signal.signal`) | Wrong handler → mask shutdown | SIG_IGN dance is correct pattern |
| Mount/namespace (`os.unshare`) | Not used | N/A |

The process control family is the only one with active blast-radius risk in this codebase. The others are mitigated by asyncio abstractions.
