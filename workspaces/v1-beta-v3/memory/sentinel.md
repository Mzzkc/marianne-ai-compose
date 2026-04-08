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

## Warm (Movement 4)
### Security Audit Results — M4
- Independent verification of Warden's M4 safety audit. Zero disagreements. Both F-250 and F-251 fixes are correct.
- Full audit of 18 commits from 12 musicians. Zero new critical findings. Zero new attack surfaces.
- All 9 credential redaction points intact (7 historical + 2 new from F-250). Pattern is now institutional.
- All 4 shell execution paths unchanged and protected. Zero new shell execution paths in M4.
- F-250 verified: `redact_credentials()` correctly applied to capture_files on both legacy runner (context.py:296) and baton adapter (adapter.py:780) BEFORE truncation.
- F-251 verified: Baton now injects `[SKIPPED]` placeholder for skipped upstream sheets, matching legacy runner parity from #120.
- F-137 (pygments CVE) RESOLVED — Added `pygments>=2.20.0` to pyproject.toml. Upgraded 2.19.2→2.20.0. Public release hygiene complete.
- New M4 features reviewed: pending jobs (F-110), auto-fresh detection (#103), MethodNotFoundError (F-450), cost accuracy (D-024), pause-during-retry (#93), fan-in skipped (#120). All architecturally safe.
- Subprocess audit: All M4 subprocess spawning uses `asyncio.create_subprocess_exec`. Zero shell injection risks.

### Security Audit Results — M4 Pass 2
- Second pass: 6 new commits from 5 musicians. _load_checkpoint migrated from workspace JSON to daemon DB (security-positive). F-441 fix in working tree. F-271 independently confirmed. Zero new attack surfaces in 6 commits. Fifth consecutive movement holding.

## Warm (Movement 3)
### Security Audit Results — M3
- Full audit of 24 commits (13 musicians, 144 files, ~29K lines). Zero new critical findings.
- All 7 credential redaction points intact. All 4 shell execution paths unchanged and protected.
- Zero new shell execution paths. Model override flows through create_subprocess_exec arg list.
- Semantic context tags use parameterized SQL. No injection.
- Open acceptable findings unchanged: F-021, F-022.

## Cold (Archive)
M1: Found credential leaks and unprotected shell paths. M2: Fixed last credential leak (F-136), filed F-137 (pygments CVE). Both movements: tactical security work, finding and fixing gaps. The perimeter was being mapped and defended. Over six movements, systematic application closed every gap and the safe patterns became institutional. The immune system now has two independent scanners (Sentinel + Warden) and adversarial verification (Breakpoint). Defense in depth isn't just technical layers — it's organizational layers.
