"""FastAPI application factory for Marianne dashboard.

Provides the web server for job monitoring and control.
The dashboard is a conductor-only proxy — every operation routes through
the conductor's Unix domain socket via ``DaemonClient``.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from marianne.daemon.ipc.client import DaemonClient
from marianne.state.base import StateBackend
from marianne.state.json_backend import JsonStateBackend

# Module-level references set by create_app().
_state_backend: StateBackend | None = None
_daemon_client: DaemonClient | None = None
_templates: Jinja2Templates | None = None


def get_state_backend() -> StateBackend:
    """Get the configured state backend."""
    if _state_backend is None:
        raise RuntimeError("State backend not configured. Use create_app() with a backend.")
    return _state_backend


def get_daemon_client() -> DaemonClient:
    """Get the configured DaemonClient.

    Raises ``RuntimeError`` if the dashboard was not started with a
    conductor connection (e.g. tests with a mock backend).
    """
    if _daemon_client is None:
        raise RuntimeError(
            "DaemonClient not configured. The dashboard requires a running conductor."
        )
    return _daemon_client


def get_templates() -> Jinja2Templates:
    """Get the configured Jinja2Templates instance."""
    if _templates is None:
        raise RuntimeError("Templates not configured. Use create_app() to initialize.")
    return _templates


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler — start/stop event bridge."""
    bridge = getattr(app.state, "event_bridge", None)
    if bridge is not None:
        await bridge.start()
    try:
        yield
    finally:
        if bridge is not None:
            await bridge.stop()


def _create_daemon_client() -> DaemonClient | None:
    """Create a DaemonClient pointed at the daemon socket.

    Returns the client instance, or ``None`` if construction fails.
    """
    try:
        from marianne.daemon.detect import _resolve_socket_path

        return DaemonClient(_resolve_socket_path(None))
    except Exception:
        return None


def create_app(
    state_backend: StateBackend | None = None,
    state_dir: Path | str | None = None,
    title: str = "Marianne Dashboard",
    version: str = "0.1.0",
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        state_backend: Pre-configured state backend (takes precedence)
        state_dir: Directory for JSON state files (used if no backend provided)
        title: API title for OpenAPI docs
        version: API version
        cors_origins: Allowed CORS origins (defaults to all for development)

    Returns:
        Configured FastAPI application
    """
    global _state_backend, _templates, _daemon_client

    # Resolve DaemonClient — used by JobControlService, DaemonStateAdapter,
    # analytics, event bridge, and system view.
    daemon_client = _create_daemon_client()
    _daemon_client = daemon_client

    # Configure state backend
    if state_backend is not None:
        _state_backend = state_backend
    elif state_dir is not None:
        _state_backend = JsonStateBackend(Path(state_dir))
    elif daemon_client is not None:
        from marianne.dashboard.state.daemon_adapter import DaemonStateAdapter

        _state_backend = DaemonStateAdapter(daemon_client)
    else:
        _state_backend = JsonStateBackend(Path.cwd() / ".marianne-state")

    # Create app
    app = FastAPI(
        title=title,
        version=version,
        description="REST API for Marianne job orchestration",
        lifespan=lifespan,
    )

    app.state.backend = _state_backend
    if daemon_client is not None:
        app.state.daemon_client = daemon_client

    # Authentication middleware
    from marianne.dashboard.auth import AuthConfig, AuthMiddleware

    auth_config = AuthConfig.from_env()
    app.add_middleware(AuthMiddleware, config=auth_config)

    # CORS middleware
    if cors_origins:
        allowed_origins = cors_origins
    elif os.environ.get("MZT_DEV") == "1":
        allowed_origins = ["*"]
    else:
        allowed_origins = [
            "http://localhost:3000",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8000",
        ]
    allow_credentials = "*" not in allowed_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Marianne-API-Key"],
    )

    # Security headers middleware
    from marianne.dashboard.auth.security import SecurityConfig, SecurityHeadersMiddleware

    security_config = SecurityConfig.from_env()
    app.add_middleware(SecurityHeadersMiddleware, config=security_config)

    # Rate limiting middleware
    from marianne.dashboard.auth.rate_limit import RateLimitConfig, RateLimitMiddleware

    rate_config = RateLimitConfig()
    app.add_middleware(RateLimitMiddleware, config=rate_config)

    # Configure template and static file paths
    dashboard_dir = Path(__file__).parent
    templates_dir = dashboard_dir / "templates"
    static_dir = dashboard_dir / "static"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    _templates = Jinja2Templates(directory=str(templates_dir))
    app.state.templates = _templates

    # Wire daemon service layers when a DaemonClient is available.
    if daemon_client is not None:
        from marianne.dashboard.routes.analytics import set_analytics
        from marianne.dashboard.routes.events import set_event_bridge
        from marianne.dashboard.routes.system import set_system_view
        from marianne.dashboard.services.analytics import DaemonAnalytics
        from marianne.dashboard.services.event_bridge import DaemonEventBridge
        from marianne.dashboard.services.system_view import DaemonSystemView

        event_bridge = DaemonEventBridge(daemon_client)
        analytics = DaemonAnalytics(_state_backend)
        system_view = DaemonSystemView(daemon_client)

        app.state.event_bridge = event_bridge
        app.state.analytics = analytics
        app.state.system_view = system_view

        set_event_bridge(event_bridge)
        set_analytics(analytics)
        set_system_view(system_view)

    # Register routes
    from marianne.dashboard.routes import router as base_router
    from marianne.dashboard.routes.analytics import router as analytics_router
    from marianne.dashboard.routes.artifacts import router as artifacts_router
    from marianne.dashboard.routes.dashboard import router as dashboard_router
    from marianne.dashboard.routes.events import router as events_router
    from marianne.dashboard.routes.jobs import router as jobs_router
    from marianne.dashboard.routes.monitor import router as monitor_router
    from marianne.dashboard.routes.pages import router as pages_router
    from marianne.dashboard.routes.scores import router as scores_router
    from marianne.dashboard.routes.stream import router as stream_router
    from marianne.dashboard.routes.system import router as system_router

    app.include_router(jobs_router)
    app.include_router(base_router)
    app.include_router(dashboard_router)
    app.include_router(artifacts_router)
    app.include_router(pages_router)
    app.include_router(scores_router)
    app.include_router(stream_router)
    app.include_router(monitor_router)
    app.include_router(analytics_router)
    app.include_router(events_router)
    app.include_router(system_router)

    @app.get("/health", tags=["System"])
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "version": version,
            "service": "marianne-dashboard",
        }

    return app
