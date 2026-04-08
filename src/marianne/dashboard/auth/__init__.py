"""Marianne Dashboard Authentication Module.

Provides authentication middleware and utilities for the dashboard API.
Supports API key authentication with optional localhost bypass for development.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from enum import Enum

from fastapi import HTTPException, Request, status
from fastapi.security import APIKeyHeader
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
    header_name: str = "X-API-Key"

    @classmethod
    def from_env(cls) -> AuthConfig:
        """Create config from environment variables.

        Environment variables:
            MZT_AUTH_MODE: disabled, api_key, or localhost_only
            MZT_API_KEYS: Comma-separated API keys
            MZT_LOCALHOST_BYPASS: true/false
        """
        mode_str = os.getenv("MZT_AUTH_MODE", "localhost_only")
        mode = AuthMode(mode_str.lower())

        api_keys_str = os.getenv("MZT_API_KEYS", "")
        raw_keys = [k.strip() for k in api_keys_str.split(",") if k.strip()]
        # Hash keys immediately so plaintext is never stored in the config object
        hashed_keys = [hash_api_key(k) for k in raw_keys]

        localhost_bypass = os.getenv("MZT_LOCALHOST_BYPASS", "true").lower() == "true"

        return cls(
            mode=mode,
            api_keys=hashed_keys,
            localhost_bypass=localhost_bypass,
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


def generate_api_key() -> str:
    """Generate a secure random API key.

    Returns:
        32-character URL-safe token
    """
    return secrets.token_urlsafe(32)


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

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
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


# FastAPI dependency for API key auth (alternative to middleware)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request,
    api_key: str | None = None,
) -> str:
    """FastAPI dependency to require API key.

    Use this as a dependency on specific routes instead of global middleware.

    Args:
        request: FastAPI request
        api_key: API key from header (auto-populated)

    Returns:
        Validated API key

    Raises:
        HTTPException: If authentication fails
    """
    config = AuthConfig.from_env()

    # Check localhost bypass
    if config.localhost_bypass and is_localhost(request):
        return "localhost"

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Keys are already hashed at load time (from_env)
    if not verify_api_key(api_key, config.api_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return api_key


# Public API
__all__ = [
    "AuthConfig",
    "AuthMiddleware",
    "AuthMode",
    "api_key_header",
    "generate_api_key",
    "hash_api_key",
    "is_localhost",
    "require_api_key",
    "verify_api_key",
]
