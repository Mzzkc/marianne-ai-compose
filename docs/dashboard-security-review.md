# Dashboard Security Review (M6 / #292)

**Date:** 2026-06-12 · **Scope:** the FastAPI dashboard (`src/marianne/dashboard/`)
against OWASP Top-10 basics before any external exposure. **Verdict: no critical
findings.** The alpha posture is localhost-only by default, which satisfies the
issue's mitigation criterion.

## What was reviewed

Auth middleware and config (`dashboard/auth/`), the route surface
(`dashboard/routes/`), bind configuration (`cli/commands/dashboard.py`), CORS,
and the data layer.

## Findings against OWASP basics

| Area | Status | Evidence |
|------|--------|----------|
| **A01 Broken access control / auth bypass** | ✅ OK | Default auth mode is `LOCALHOST_ONLY` (`AuthConfig.from_env`, env `MZT_AUTH_MODE`). `is_localhost()` uses `request.client.host` (the real TCP peer) — it does **not** trust `X-Forwarded-For`, so the localhost gate is not header-spoofable (uvicorn doesn't enable proxy headers by default). |
| **A02 Cryptographic failures** | ✅ OK | API keys are SHA256-hashed at load (`from_env`) — plaintext is never stored — and compared with `hmac.compare_digest` (constant-time, no timing oracle). |
| **A03 Injection — path traversal** | ✅ OK | `routes/artifacts.py` resolves the requested path and enforces `full_path.is_relative_to(workspace_resolved)`; `routes/scores.py` confines `workspace_path` to CWD/HOME roots. |
| **A03 Injection — SQL** | ✅ OK | No string-formatted/f-string SQL in the registry/state layer (parameterized queries only). |
| **CSRF** | ✅ Low risk | Auth is a custom **header** (`X-API-Key`), not a cookie, so browsers don't auto-attach it cross-origin; state-changing routes (`POST/DELETE` job control) are therefore not CSRF-exploitable in the browser-credential sense. |
| **A05 Security misconfiguration** | ✅ OK | Bind defaults to `127.0.0.1` (`--host 0.0.0.0` is an explicit opt-in). CORS is restricted to `localhost`/`127.0.0.1` origins. Per-client rate limiting is in place (`auth/rate_limit.py`). |

## Excluded-from-auth paths

`/health`, `/docs`, `/openapi.json`, `/redoc` are auth-excluded — acceptable for
alpha (they expose only health and the API schema, and only on localhost by
default).

## Minor / non-blocking notes (tracked, not launch-blocking)

- `routes/artifacts.py` carries a documented symlink **TOCTOU** caveat (a path
  could be swapped between the `is_relative_to` check and the open). Severity is
  low under the localhost-only default; worth a hardening pass (open the file
  with `O_NOFOLLOW` / re-validate post-open) before any non-localhost exposure.
- If the dashboard is ever bound to `0.0.0.0`, operators MUST set
  `MZT_AUTH_MODE=api_key` with real keys — the localhost bypass should be off for
  network exposure. This is documented behavior; consider a loud startup warning
  when `--host` is non-loopback and auth is `localhost_only`.

## Conclusion

The dashboard is reasonably hardened for an alpha launch: localhost-only by
default at both the bind and auth layers, non-spoofable localhost detection,
hashed + constant-time key comparison, header-based (CSRF-resistant) auth, path
traversal blocked, parameterized SQL, rate limiting, and restricted CORS. No
critical findings; the two minor notes are tracked for the pre-network-exposure
hardening pass.
