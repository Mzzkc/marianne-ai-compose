"""Security middleware and utilities for Marianne Dashboard.

Provides security headers, CORS configuration, and input validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp


@dataclass
class SecurityConfig:
    """Security configuration.

    Attributes:
        cors_origins: Allowed CORS origins
        cors_allow_credentials: Allow credentials in CORS
        cors_allow_methods: Allowed HTTP methods
        cors_allow_headers: Allowed headers
        add_security_headers: Add security headers to responses
        content_security_policy: CSP header value
        strict_transport_security: HSTS header value
        x_content_type_options: X-Content-Type-Options header
        x_frame_options: X-Frame-Options header
        x_xss_protection: X-XSS-Protection header
        referrer_policy: Referrer-Policy header
    """

    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:8080", "http://127.0.0.1:8080"]
    )
    cors_allow_credentials: bool = True
    cors_allow_methods: list[str] = field(
        default_factory=lambda: ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    )
    cors_allow_headers: list[str] = field(
        default_factory=lambda: [
            "Accept",
            "Authorization",
            "Content-Type",
            "X-Requested-With",
        ]
    )
    add_security_headers: bool = True

    # Security headers
    content_security_policy: str = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
        "https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
        "https://unpkg.com; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    strict_transport_security: str = "max-age=31536000; includeSubDomains"
    x_content_type_options: str = "nosniff"
    x_frame_options: str = "SAMEORIGIN"
    x_xss_protection: str = "1; mode=block"
    referrer_policy: str = "strict-origin-when-cross-origin"

    @classmethod
    def from_env(cls) -> SecurityConfig:
        """Create config from environment variables.

        Environment variables:
            MZT_CORS_ORIGINS: Comma-separated origins
            MZT_CORS_CREDENTIALS: true/false
        """
        origins_str = os.getenv(
            "MZT_CORS_ORIGINS",
            "http://localhost:8080,http://127.0.0.1:8080"
        )
        origins = [o.strip() for o in origins_str.split(",") if o.strip()]

        credentials = os.getenv("MZT_CORS_CREDENTIALS", "true").lower() == "true"

        return cls(
            cors_origins=origins,
            cors_allow_credentials=credentials,
        )

    @classmethod
    def production(cls) -> SecurityConfig:
        """Create strict production configuration."""
        return cls(
            cors_origins=[],  # No CORS in production (same-origin only)
            cors_allow_credentials=False,
            cors_allow_methods=["GET", "POST"],  # Restrict methods
            cors_allow_headers=["Content-Type", "X-API-Key"],  # Restrict headers
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    def __init__(self, app: ASGIApp, config: SecurityConfig | None = None):
        """Initialize middleware.

        Args:
            app: ASGI application (FastAPI or Starlette app)
            config: Security configuration
        """
        super().__init__(app)
        self.config = config or SecurityConfig()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Add security headers to response.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response with security headers
        """
        response = await call_next(request)

        if self.config.add_security_headers:
            content_type = response.headers.get("content-type", "")
            is_html = "text/html" in content_type

            # Always add non-CSP security headers
            response.headers["X-Content-Type-Options"] = (
                self.config.x_content_type_options
            )
            response.headers["X-Frame-Options"] = self.config.x_frame_options
            response.headers["Referrer-Policy"] = self.config.referrer_policy

            # Remove potentially dangerous headers
            if "Server" in response.headers:
                del response.headers["Server"]

            # CSP and HSTS only on HTML responses
            if is_html:
                response.headers["Content-Security-Policy"] = (
                    self.config.content_security_policy
                )
                response.headers["Strict-Transport-Security"] = (
                    self.config.strict_transport_security
                )
                response.headers["X-XSS-Protection"] = (
                    self.config.x_xss_protection
                )

        return response


def configure_cors(app: FastAPI, config: SecurityConfig | None = None) -> None:
    """Configure CORS middleware for application.

    Args:
        app: FastAPI or Starlette app with add_middleware method
        config: Security configuration
    """
    config = config or SecurityConfig()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=config.cors_allow_credentials,
        allow_methods=config.cors_allow_methods,
        allow_headers=config.cors_allow_headers,
    )


MAX_JOB_ID_LENGTH = 256
MAX_FILENAME_LENGTH = 255  # POSIX NAME_MAX


def validate_job_id(job_id: str) -> bool:
    """Validate job ID format to prevent injection.

    Args:
        job_id: Job identifier to validate

    Returns:
        True if valid format
    """
    if not job_id or len(job_id) > MAX_JOB_ID_LENGTH:
        return False

    # Allow alphanumeric, hyphen, underscore, period
    allowed_chars = set(
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789-_."
    )
    return all(c in allowed_chars for c in job_id)


def validate_path_component(component: str) -> bool:
    """Validate path component to prevent traversal attacks.

    Args:
        component: Path component to validate

    Returns:
        True if safe
    """
    if not component:
        return False

    # Block directory traversal
    if ".." in component or component.startswith("/"):
        return False

    # Block null bytes
    return "\x00" not in component


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe use.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Remove path components
    filename = filename.replace("/", "_").replace("\\", "_")

    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Limit length
    if len(filename) > MAX_FILENAME_LENGTH:
        filename = filename[:MAX_FILENAME_LENGTH]

    # Ensure not empty
    if not filename:
        filename = "unnamed"

    return filename


# Public API
__all__ = [
    "SecurityConfig",
    "SecurityHeadersMiddleware",
    "configure_cors",
    "sanitize_filename",
    "validate_job_id",
    "validate_path_component",
]
