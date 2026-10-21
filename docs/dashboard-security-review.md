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

- `routes/artifacts.py` symlink **TOCTOU** — closed (GH #387). The read opens
  with `O_NOFOLLOW`, checks the descriptor is a regular file whose device and
  inode match a fresh `lstat` of the path, and serves the bytes from that
  descriptor; `FileResponse` (a second open by path, which follows symlinks) is
  no longer used on this route.
- If the dashboard is ever bound to `0.0.0.0`, operators MUST set
  `MZT_AUTH_MODE=api_key` with real keys and `MZT_LOCALHOST_BYPASS=false`.
  `mzt dashboard` now prints a red **Exposed bind** panel at startup when the
  host is non-loopback and auth is still `localhost_only`, or `api_key` with no
  keys or the bypass on (GH #387); `disabled` on a network bind is named too.
- **Public orchestra surface** (`MZT_PUBLIC_ORCHESTRA=true`, GH #468): the
  middleware serves `/orchestra`, `/api/orchestra/{snapshot,replay,stream}` and
  `/static` to every client regardless of mode. The surface is the conductor's
  whitelisted projection (names, sheet numbers, instruments, outcomes, times;
  `daemon/orchestra/recorder.project_event`), the API is read-only, `replay`
  validates `day` as `YYYY-MM-DD`, and rate limiting still applies. The prefix
  match is exact-segment (`/orchestral` is not public). Off by default.

## Conclusion

The dashboard is reasonably hardened for an alpha launch: localhost-only by
default at both the bind and auth layers, non-spoofable localhost detection,
hashed + constant-time key comparison, header-based (CSRF-resistant) auth, path
traversal blocked, parameterized SQL, rate limiting, and restricted CORS. No
critical findings; the two minor notes are tracked for the pre-network-exposure
hardening pass.
