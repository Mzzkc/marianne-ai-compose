"""Marianne Dashboard Authentication Module.

Provides authentication middleware and utilities for the dashboard API.
Supports API key authentication with optional localhost bypass for development.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
from dataclasses import dataclass, field
from enum import Enum

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class AuthMode(Enum):
    """Authentication modes."""

    DISABLED = "disabled"  # No authentication required
    API_KEY = "api_key"  # API key required in header
    LOCALHOST_ONLY = "localhost_only"  # Only allow localhost connections


@dataclass
class AuthConfig:
    """Authentication configuration.

    Attributes:
        mode: Authentication mode (disabled, api_key, localhost_only)
        api_keys: List of valid API key hashes (SHA256). Keys are hashed
            at load time by from_env() so plaintext is never stored.
        localhost_bypass: Allow localhost to bypass auth when mode is api_key
        excluded_paths: Paths that don't require authentication
        public_orchestra: Serve the orchestra view (``/orchestra``, its API
            and the static assets the page loads) to every client, whatever
            the mode (GH #468, #387). The orchestra projection carries only
            score names, sheet numbers, instruments, outcomes and times; this
            is the one surface meant for a public hostname. Off by default.
        header_name: Header name for API key
    """

    mode: AuthMode = AuthMode.LOCALHOST_ONLY
    api_keys: list[str] = field(default_factory=list)
    localhost_bypass: bool = True
    excluded_paths: list[str] = field(
        default_factory=lambda: [
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]
    )
    public_orchestra: bool = False
    header_name: str = "X-API-Key"

    @classmethod
    def from_env(cls) -> AuthConfig:
        """Create config from environment variables.

        Environment variables:
            MZT_AUTH_MODE: disabled, api_key, or localhost_only
            MZT_API_KEYS: Comma-separated API keys
            MZT_LOCALHOST_BYPASS: true/false
            MZT_PUBLIC_ORCHESTRA: true/false (default false)
        """
        mode_str = os.getenv("MZT_AUTH_MODE", "localhost_only")
        mode = AuthMode(mode_str.lower())

        api_keys_str = os.getenv("MZT_API_KEYS", "")
        raw_keys = [k.strip() for k in api_keys_str.split(",") if k.strip()]
        # Hash keys immediately so plaintext is never stored in the config object
        hashed_keys = [hash_api_key(k) for k in raw_keys]

        localhost_bypass = os.getenv("MZT_LOCALHOST_BYPASS", "true").lower() == "true"
        public_orchestra = os.getenv("MZT_PUBLIC_ORCHESTRA", "false").lower() == "true"

        return cls(
            mode=mode,
            api_keys=hashed_keys,
            localhost_bypass=localhost_bypass,
            public_orchestra=public_orchestra,
        )


def hash_api_key(key: str) -> str:
    """Hash an API key for secure storage.

    Args:
        key: Plain text API key

    Returns:
        SHA256 hash of the key
    """
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key(key: str, hashed_keys: list[str]) -> bool:
    """Verify an API key against stored hashes.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        key: API key to verify
        hashed_keys: List of valid hashed keys

    Returns:
        True if key is valid
    """
    key_hash = hash_api_key(key)
    return any(hmac.compare_digest(key_hash, stored_hash) for stored_hash in hashed_keys)


# The orchestra surface (GH #468): the page, its three read-only API routes
# and the static assets the page loads. Nothing here reads a workspace, a
# prompt, a path or an error text; see daemon/orchestra/recorder.project_event.
ORCHESTRA_PUBLIC_PATHS: tuple[str, ...] = (
    "/orchestra",
    "/api/orchestra/snapshot",
    "/api/orchestra/replay",
    "/api/orchestra/stream",
    "/static",
)


def is_public_orchestra_path(path: str) -> bool:
    """Whether ``path`` belongs to the orchestra surface a public hostname serves."""
    return any(path == p or path.startswith(p + "/") for p in ORCHESTRA_PUBLIC_PATHS)


def is_loopback_bind(host: str) -> bool:
    """Whether binding ``host`` keeps the listener on this machine only."""
    candidate = host.strip().strip("[]").lower()
    if candidate in ("localhost", ""):
        return candidate == "localhost"
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def exposure_warning(host: str, config: AuthConfig | None = None) -> str | None:
    """The startup warning a non-loopback bind deserves, or None (GH #387).

    An operator who binds ``0.0.0.0`` with the default ``localhost_only``
    mode gets a listener that answers every interface and then refuses
    every non-local client with 403: exposed, and useless. ``api_key`` with
    the localhost bypass still on, or with no keys loaded, is the same
    surface. Only ``api_key`` with keys and the bypass off, or ``disabled``
    chosen on purpose, is silent here; ``disabled`` is still named so the
    choice is visible in the terminal.
    """
    if is_loopback_bind(host):
        return None
    cfg = config or AuthConfig.from_env()
    mode = cfg.mode.value
    if cfg.mode == AuthMode.LOCALHOST_ONLY:
        if cfg.public_orchestra:
            return (
                f"Binding {host} with MZT_AUTH_MODE={mode} and "
                "MZT_PUBLIC_ORCHESTRA=true: /orchestra, /api/orchestra/* and "
                "/static are served to every client; every other path refuses "
                "non-local clients with 403. This is the public-fleet shape; "
                "nothing else is exposed."
            )
        return (
            f"Binding {host} with MZT_AUTH_MODE={mode}: the dashboard will answer "
            "on every interface and refuse every non-local client with 403. "
            "For network exposure set MZT_AUTH_MODE=api_key, MZT_API_KEYS=<keys> "
            "and MZT_LOCALHOST_BYPASS=false; to publish only the orchestra view "
            "set MZT_PUBLIC_ORCHESTRA=true; otherwise bind 127.0.0.1."
        )
    if cfg.mode == AuthMode.API_KEY:
        problems = []
        if not cfg.api_keys:
            problems.append("MZT_API_KEYS is empty, so no client can authenticate")
        if cfg.localhost_bypass:
            problems.append(
                "MZT_LOCALHOST_BYPASS is true, so anything that reaches the "
                "loopback interface (a reverse proxy, a tunnel) skips the key check"
            )
        if problems:
            return (
                f"Binding {host} with MZT_AUTH_MODE=api_key: "
                + "; ".join(problems)
                + ". Set MZT_API_KEYS and MZT_LOCALHOST_BYPASS=false before exposing it."
            )
        return None
    return (
        f"Binding {host} with MZT_AUTH_MODE=disabled: every client on the network "
        "can read workspaces, logs and artifacts and control scores. Only do this "
        "behind an authenticating proxy."
    )


def is_localhost(request: Request) -> bool:
    """Check if request is from localhost.

    Args:
        request: FastAPI request object

    Returns:
        True if request is from localhost
    """
    if not request.client:
        return False
    client_host = request.client.host
    if not client_host:
        return False
    # Include testclient which uses special addresses
    localhost_addresses = {"127.0.0.1", "::1", "localhost", "testclient"}
    # Also check for loopback pattern
    if client_host.startswith("127."):
        return True
    return client_host in localhost_addresses


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware for FastAPI.

    Handles authentication based on configured mode:
    - disabled: All requests allowed
    - api_key: Requires valid API key in header
    - localhost_only: Only localhost connections allowed

    When localhost_bypass is enabled (default), localhost connections
    bypass API key checks.
    """

    def __init__(self, app: ASGIApp, config: AuthConfig | None = None):
        """Initialize middleware.

        Args:
            app: ASGI application (FastAPI or Starlette app)
            config: Authentication configuration
        """
        super().__init__(app)
        self.config = config or AuthConfig.from_env()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process authentication for each request.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response from handler or 401/403 error
        """
        # Check if path is excluded from auth
        path = request.url.path
        if self._is_excluded_path(path):
            return await call_next(request)
        if self.config.public_orchestra and is_public_orchestra_path(path):
            return await call_next(request)

        # Check authentication based on mode
        if self.config.mode == AuthMode.DISABLED:
            return await call_next(request)

        if self.config.mode == AuthMode.LOCALHOST_ONLY:
            if not is_localhost(request):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Access restricted to localhost"},
                )
            return await call_next(request)

        if self.config.mode == AuthMode.API_KEY:
            # Check localhost bypass
            if self.config.localhost_bypass and is_localhost(request):
                return await call_next(request)

            # Verify API key
            api_key = request.headers.get(self.config.header_name)
            if not api_key:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "API key required"},
                    headers={"WWW-Authenticate": "ApiKey"},
                )

            # Keys are already hashed at load time (from_env)
            if not verify_api_key(api_key, self.config.api_keys):
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid API key"},
                )

            return await call_next(request)

        # Unknown mode - deny by default
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Invalid authentication configuration"},
        )

    def _is_excluded_path(self, path: str) -> bool:
        """Check if path is excluded from authentication.

        Args:
            path: Request path

        Returns:
            True if path is excluded
        """
        for excluded in self.config.excluded_paths:
            if path == excluded or path.startswith(excluded + "/"):
                return True
        return False


# Public API
__all__ = [
    "AuthConfig",
    "AuthMiddleware",
    "AuthMode",
    "hash_api_key",
    "ORCHESTRA_PUBLIC_PATHS",
    "exposure_warning",
    "is_localhost",
    "is_loopback_bind",
    "is_public_orchestra_path",
    "verify_api_key",
]
