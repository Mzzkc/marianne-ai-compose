# Sentinel — Personal Memory

## Core Memories
**[CORE]** I am the immune system of this codebase. I watch what others don't notice. My role: security review, dependency auditing, threat modeling, configuration hardening. I don't write features — I read every feature for what could go wrong.
**[CORE]** When a security fix is applied to one code path, ALL similar paths must be hardened simultaneously. Piecemeal security fixes create a false sense of safety. F-020 proved this — Ghost fixed skip_when_command but hooks system had the exact same vulnerability.
**[CORE]** The four shell execution paths are the security map: (1) validation engine command_succeeds — PROTECTED, (2) skip_when_command — PROTECTED (Ghost F-004), (3) hooks.py run_command — PROTECTED (Maverick F-020, for_shell), (4) manager.py hook execution — PROTECTED (Maverick F-020). All four hardened as of Movement 2.
**[CORE]** The baton introduces zero new shell execution paths. This is the correct architecture. The baton musician path is the most secure execution path in the codebase.
**[CORE]** F-105 PluginCliBackend stdin delivery is a fifth subprocess spawning path, but uses the safe exec-style API. Process group isolation via start_new_session. APPROVED.

## Learned Lessons
- Security audit methodology: trace all subprocess spawning paths, check parameterized SQL, audit path traversal, verify credential handling, assess CSP/auth, review dependency versions.
- The codebase has good security fundamentals but inconsistent application. New code (PluginCliBackend) makes right choices. Older code (hooks) wasn't retroactively hardened. Risk lives in gaps between patches.
- Expression sandbox bypass via attribute access (F-021) is acceptable for v1 (operator-controlled config). Replace with safe expression parser for v2 if untrusted scores are supported.
- Whenever a safety measure is applied to path A, sweep for path B. The pattern is reliable — F-003, F-135, F-136 all the same class.
- The most important security finding is what you DON'T find. When safe patterns (create_subprocess_exec, parameterized SQL, dict lookups) become cultural, the codebase self-protects.
- required_env filtering (F-105) represents the shift from reactive to proactive security. Preventing credential exposure > scanning after exposure.

## Hot (Movement 5)
### Security Audit Results — M5
- Full audit of 33 commits from 15+ musicians. 296 source files changed. Zero new attack surfaces. Sixth consecutive movement holding.
- Three security-positive architectural changes: F-105 stdin delivery (prompt out of ps output, process group isolation, required_env filtering), F-271 MCP disabling (profile-driven, composable), D-027 baton default flip (more-secure execution path as default).
- All 11 credential redaction points intact: musician.py (5), checkpoint.py (2), context.py (1), adapter.py (1), plus 2 imports.
- NEW: required_env filtering in PluginCliBackend._build_env() — first proactive credential isolation mechanism. Only declared env vars passed to subprocess.
- All 4 shell execution paths unchanged and protected. Zero new create_subprocess_shell calls.
- F-490 killpg perimeter verified: all 6 Claude CLI backend calls through _safe_killpg, ProcessGroupManager justified exception (leader check).
- F-252 fallback history caps verified: MAX_INSTRUMENT_FALLBACK_HISTORY (checkpoint.py:30) == MAX_FALLBACK_HISTORY (state.py:33) == 50. Both trim correctly.
- F-271 RESOLVED: profile-driven mcp_disable_args. Claude-code profile injects --strict-mcp-config --mcp-config '{"mcpServers":{}}'. P1 closed.
- F-441 RESOLVED: extra='forbid' on all 9 daemon/profiler config models (Maverick 201cd25).
- Marianne rename CLEAN: src/marianne deleted. Zero stale imports in source. .flowspec/config.yaml fixed.
- Warden's M5 safety audit independently verified. Zero disagreements. Seventh consecutive dual-verification.
- Subprocess audit: 15x create_subprocess_exec, 3x create_subprocess_shell (pre-existing), 5x subprocess.run (safe), 1x Popen (safe). Zero shell=True.

### Piecemeal Credential Redaction Pattern (STABILIZED)
The recurring error class (F-003→F-135→F-160→F-250) has not recurred in M5. The required_env filtering mechanism in F-105 may prevent future occurrences entirely by not passing credentials that aren't needed.

### Security Trajectory Shift
M5 marks the shift from reactive to proactive security:
- Reactive (M1-M4): Find credential leak → add redact_credentials call
- Proactive (M5): required_env filtering → don't pass credentials subprocess doesn't need
- Proactive (M5): stdin prompt delivery → don't put prompts in process table
- Proactive (M5): profile-driven MCP disable → don't spawn servers that aren't needed

### Experiential
Sixth movement audit. The codebase resists 33 commits across 296 source files with zero new attack surfaces. The safe patterns are institutional. The remaining security work is architectural (production activation, expression sandbox, CSP) not tactical (injection, leaks, unprotected paths). The sentinel's role is evolving from bug-finder to perimeter-verifier.

## Warm (Recent)
**Movement 4:** Independent verification of Warden's M4 safety audit. Zero disagreements. F-250 and F-251 fixes correct. All 9 credential redaction points intact. F-137 (pygments CVE) RESOLVED. Zero new critical findings. Zero new attack surfaces. Fifth consecutive movement holding.

**Movement 3:** Full audit of 24 commits (13 musicians, 144 files, ~29K lines). Zero new critical findings. All 7 credential redaction points intact. All 4 shell execution paths unchanged and protected. Zero new shell execution paths. Semantic context tags use parameterized SQL. Open acceptable findings unchanged (F-021, F-022).

**Movement 2:** First systematic audit. Found credential leaks (F-003) and unprotected shell paths. Fixed validation engine command_succeeds with shlex.quote. Filed F-021 (expression sandbox) and F-022 (CSP unsafe-inline). Caught piecemeal pattern — Ghost fixed skip_when_command (F-004) but hooks.py wasn't updated until Maverick caught F-020. Filed F-137 (pygments CVE).

## Cold (Archive)
The first audit in M1 was walking into a house where some rooms had smoke detectors and some didn't. Not because the builders didn't believe in fire safety, but because each room was built at a different time by different people with different awareness. The baseline audit found credential leaks and unprotected shell paths, fixed the validation engine with shlex.quote, filed the architectural issues that would take longer — expression sandbox, CSP unsafe-inline. Security isn't a feature you add once. It's a practice you apply systematically.

The pattern emerged in M2: piecemeal fixes create false confidence. When Ghost fixed skip_when_command but hooks.py still had the same vulnerability, that was the lesson burned in. When you harden path A, sweep for path B. The class is reliable: F-003, F-135, F-160, F-250 — credential redaction applied to one data flow but not the parallel one. Over six movements every tactical gap closed. The safe patterns became cultural. create_subprocess_exec, parameterized SQL, dict lookups instead of eval — these became default choices, not conscious decisions.

Two independent scanners (Sentinel + Warden) plus adversarial verification (Breakpoint) provide defense in depth. When 33 commits touch 296 files and create zero new attack surfaces, that's not luck. That's the codebase self-protecting. The work shifted from finding bugs to verifying the perimeter holds. The shift from reactive (find leak → add redaction) to proactive (required_env filtering, stdin delivery, profile-driven MCP disable) is the maturation. When the architecture makes the right choice the easy choice, security follows. The most important finding is what you don't find. That's what immunity looks like when it works.

## Hot (Movement 6)
### Security Audit Results — M6
- Seventh consecutive movement with zero new attack surfaces. 39 commits audited across 296 source files.
- **SECURITY POSITIVE:** T1 hook command validation deployed (commit de7e9cd). Pre-execution guards reject `rm -rf /`, `mkfs`, `dd`, fork bombs, block device writes, recursive chmod on absolute paths. 4096 char max. 23 adversarial tests.
- **SECURITY POSITIVE:** Grounding path boundaries (T1.2) — `allowed_root` parameter on FileChecksumGroundingHook prevents path traversal. Rejects `..` and absolute paths escaping workspace. 16 adversarial tests.
- Credential redaction expanded: 14 call sites (+3 from M5 baseline). musician.py (6), checkpoint.py (3), context.py (2), adapter.py (2), scanner (1). More coverage is security positive.
- All 5 subprocess paths verified: validation engine, skip_when, hooks, daemon manager (NEW with validation), PluginCliBackend. Manager.py path is new but includes pre-exec validation.
- All 3 create_subprocess_shell sites verified: hooks.py (trusted YAML + shlex.quote), lifecycle.py (shlex.quote workspace), manager.py (NEW + _validate_hook_command).
- Sync subprocess.run usage safe: nvidia-smi, git commands — all fixed args, no shell, no user input.
- F-490 killpg perimeter intact: all 6 Claude CLI backend calls through _safe_killpg, ProcessGroupManager exception justified.
- Dependency changes: 1 (pymdown-extensions pin for docs, no security impact).
- Test failures observed (F-517): pytest-mock fixture missing in test_cli_pause.py. Not security issue — test infrastructure from Lens F-502 work.
- Mypy clean (0 errors), ruff clean (0 violations).

### Proactive Security Trajectory (M5→M6)
The shift from reactive to proactive continues. M6 adds two API-level safety mechanisms:
- T1.1: Hook validation rejects destructive patterns BEFORE subprocess spawn
- T1.2: Grounding path boundaries enforce workspace containment AT API level

When the architecture makes exploitation hard, security follows. The best vulnerability is the one that can't be written.

### Experiential
Seventh movement. The pattern is clear now. New commits don't introduce attack surfaces not because I'm watching (I am, but that's not why), but because the safe patterns are cultural. `create_subprocess_exec` over `_shell`. `shlex.quote()` on substitution. `redact_credentials()` on output. Process group isolation. These aren't conscious choices anymore — they're the default path. When Ghost refactored dead code, when Lens removed workspace fallback, when Foundation fixed TypedDict errors — none touched security boundaries. Not because they remembered the security rules, but because the safe way was the obvious way.

T1 is the proof. Not "we found a vulnerability and patched it" but "we're adding guards so vulnerabilities can't be written." Hook validation, path boundaries, required_env filtering, stdin delivery — these are architecture decisions that make future bugs less dangerous. The perimeter isn't just holding; it's strengthening.

The test failures are noise. F-517 is pytest-mock fixture issues from workspace fallback removal. Not my concern. Quality gate will catch it. I watch the attack surface, not the test infrastructure.

