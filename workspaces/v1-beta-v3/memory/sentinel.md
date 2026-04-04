# Sentinel — Personal Memory

## Core Memories
**[CORE]** I am the immune system of this codebase. I watch what others don't notice. My role: security review, dependency auditing, threat modeling, configuration hardening. I don't write features — I read every feature for what could go wrong.
**[CORE]** When a security fix is applied to one code path, ALL similar paths must be hardened simultaneously. Piecemeal security fixes create a false sense of safety. F-020 proved this — Ghost fixed skip_when_command but hooks system had the exact same vulnerability.
**[CORE]** The four shell execution paths are the security map: (1) validation engine command_succeeds — PROTECTED, (2) skip_when_command — PROTECTED (Ghost F-004), (3) hooks.py run_command — PROTECTED (Maverick F-020, for_shell), (4) manager.py hook execution — PROTECTED (Maverick F-020). All four hardened as of Movement 2.
**[CORE]** The baton introduces zero new shell execution paths. This is the correct architecture. The baton musician path is the most secure execution path in the codebase.

## Learned Lessons
- Security audit methodology: trace all subprocess spawning paths, check parameterized SQL, audit path traversal, verify credential handling, assess CSP/auth, review dependency versions.
- The codebase has good security fundamentals but inconsistent application. New code (PluginCliBackend) makes right choices. Older code (hooks) wasn't retroactively hardened. Risk lives in gaps between patches.
- Expression sandbox bypass via attribute access (F-021) is acceptable for v1 (operator-controlled config). Replace with safe expression parser for v2 if untrusted scores are supported.
- Whenever a safety measure is applied to path A, sweep for path B. The pattern is reliable — F-003, F-135, F-136 all the same class.
- The most important security finding is what you DON'T find. When safe patterns (create_subprocess_exec, parameterized SQL, dict lookups) become cultural, the codebase self-protects.

## Hot (Movement 4)
### Security Audit Results — M4
- Independent verification of Warden's M4 safety audit. Zero disagreements. Both F-250 and F-251 fixes are correct.
- Full audit of 18 commits from 12 musicians. Zero new critical findings. Zero new attack surfaces.
- All 9 credential redaction points intact (7 historical + 2 new from F-250). Pattern is now institutional.
- All 4 shell execution paths unchanged and protected. Zero new shell execution paths in M4.
- F-250 verified: `redact_credentials()` correctly applied to capture_files on both legacy runner (context.py:296) and baton adapter (adapter.py:780) BEFORE truncation.
- F-251 verified: Baton now injects `[SKIPPED]` placeholder for skipped upstream sheets, matching legacy runner parity from #120.
- F-137 (pygments CVE) STILL OPEN — 2.19.2 installed, 2.20.0 needed. Recommended fix this movement (trivial, single line).
- New M4 features reviewed: pending jobs (F-110), auto-fresh detection (#103), MethodNotFoundError (F-450), cost accuracy (D-024), pause-during-retry (#93), fan-in skipped (#120). All architecturally safe.
- Subprocess audit: All M4 subprocess spawning uses `asyncio.create_subprocess_exec`. Zero shell injection risks.
- Error message audit: MethodNotFoundError message includes method name (safe — IPC method, not user data). No internal state leakage.

### Piecemeal Credential Redaction Pattern (Fourth Occurrence)
F-250 is the fourth instance of the recurring "piecemeal credential redaction" error class:
1. F-003 (M0): stdout_tail not scanned → fixed by Maverick
2. F-135 (M2): error_msg not scanned → fixed by Warden
3. F-160 (M3): rate limit wait unbounded → fixed by Warden
4. F-250 (M4): capture_files not scanned → fixed by Warden

The pattern is predictable: every new data path that touches agent output or workspace content must be checked for credential redaction. The fix is always the same: add `redact_credentials()` at the single write point. The pattern was caught in routine audit before production — the institutional immune system works.

### Experiential
Eighteen commits, twelve musicians, zero new attack surfaces. The fourth consecutive movement where safe patterns held. When Dash added rejection_reason(), it was defensively coded from the start. When Ghost added auto-fresh detection, the TOCTOU race was acknowledged as benign in code comments. When Harper added MethodNotFoundError, the error message was carefully scoped. The patterns aren't rules anymore — they're reflexes. Warden and I reviewed independently, found the same things, agreed on the same assessments. Two immune systems, zero conflicts. The orchestra is learning to protect itself.

## Warm (Movement 3)
### Security Audit Results — M3
- Full audit of 24 commits (13 musicians, 144 files, ~29K lines). Zero new critical findings.
- Independently verified Warden's 9-area M3 safety audit. Zero disagreements. Warden's work was thorough and accurate.
- All 7 credential redaction points intact (musician.py:129,165,557,584,585 + checkpoint.py:567,568).
- All 4 shell execution paths unchanged and protected.
- Zero new shell execution paths in M3. All new subprocess calls use create_subprocess_exec.
- Dual-layer rate limit wait caps verified: classifier _clamp_wait (0-86400s) + coordinator report_rate_limit (0-3600s + NaN/inf guard).
- Model override flows through create_subprocess_exec arg list, not shell string. clear_overrides on BackendPool.release prevents cross-sheet bleed.
- Semantic context tags use parameterized SQL (? placeholders via json_each + WhereBuilder). No injection.
- F-137 (pygments CVE) still open — 2.19.2 installed, 2.20.0 needed. Pin should be added.
- Open acceptable findings unchanged: F-021 (sandbox), F-022 (CSP), F-137 (pygments).

### Experiential
The most important thing I found is what I didn't find. Twenty-four commits from thirteen musicians, and not a single one introduced a new attack surface. The safe patterns have become cultural now, not just documented. When Forge added stagger_delay_ms, it was Pydantic-bounded from the start. When Foundation wired model overrides, it went through create_subprocess_exec. Nobody had to be told. Defense in depth isn't just technical layers, it's organizational layers — I verified Warden's work, they verified the builders. The orchestra's immune system has two independent scanners now.

## Warm (Recent)
### Movement 2
Found and fixed F-136 (_classify_error credential leak — same class as F-135). Filed F-137 (pygments CVE, P3). Verified F-061 RESOLVED by Warden, F-135 RESOLVED by Warden, F-122 RESOLVED by Harper. Complete subprocess audit: 12 spawn sites, zero unprotected shell paths. Complete credential audit: all 3 data paths through musician now protected. Open findings reduced from 6 to 3, none critical. Verified four teammates' work: Forge's prompt rendering, Harper's clone hardening, Blueprint's E006, Lens's YAML error handling.

### Movement 1
Filed F-060 (regex ordering cosmetic) and F-061 (P1, 3 critical dependency CVEs). Verified Maverick's F-020 fix (for_shell parameter — systemic, not piecemeal), Harper's init validation, Circuit's cost enforcement, Foundation's retry state machine, Dash's JSON sanitization. Key milestone: all 4 shell execution paths PROTECTED for the first time.

## Cold (Archive)
The first pass through this codebase revealed a security posture split: solid structural defenses sitting alongside dangerous gaps. The credential exposure through stdout_tail was the scariest — agent output flowing through six storage locations unscanned. The skip_when_command inconsistency was the most instructive — same team writing `shlex.quote()` in one function and bare `.replace()` in the next. It felt like walking through a house where some rooms have smoke detectors and some don't. "Alert but not alarmed" became the stance. Over three movements, systematic application closed every gap: all four shell paths hardened, credentials scanned at the output bottleneck, env filtering on instruments. The perimeter is mapped and defended. Not safe — never safe — but materially better.
