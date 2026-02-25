"""Tests for Mozart Dashboard authentication module."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozart.dashboard.auth import (
    AuthConfig,
    AuthMiddleware,
    AuthMode,
    generate_api_key,
    hash_api_key,
    is_localhost,
    require_api_key,
    verify_api_key,
)


class TestAuthConfig:
    """Test AuthConfig configuration class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AuthConfig()

        assert config.mode == AuthMode.LOCALHOST_ONLY
        assert config.api_keys == []
        assert config.localhost_bypass is True
        assert "/health" in config.excluded_paths
        assert "/docs" in config.excluded_paths
        assert config.header_name == "X-API-Key"

    def test_from_env_defaults(self):
        """Test config from environment with defaults."""
        with patch.dict("os.environ", {}, clear=True):
            config = AuthConfig.from_env()

            assert config.mode == AuthMode.LOCALHOST_ONLY
            assert config.api_keys == []
            assert config.localhost_bypass is True

    def test_from_env_api_key_mode(self):
        """Test config from environment with API key mode."""
        env = {
            "MOZART_AUTH_MODE": "api_key",
            "MOZART_API_KEYS": "key1,key2,key3",
            "MOZART_LOCALHOST_BYPASS": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            config = AuthConfig.from_env()

            assert config.mode == AuthMode.API_KEY
            assert config.api_keys == [
                hash_api_key("key1"),
                hash_api_key("key2"),
                hash_api_key("key3"),
            ]
            assert config.localhost_bypass is False

    def test_from_env_disabled_mode(self):
        """Test config with disabled authentication."""
        env = {"MOZART_AUTH_MODE": "disabled"}
        with patch.dict("os.environ", env, clear=True):
            config = AuthConfig.from_env()

            assert config.mode == AuthMode.DISABLED

    def test_from_env_empty_api_keys(self):
        """Test config with empty API keys string."""
        env = {"MOZART_API_KEYS": ""}
        with patch.dict("os.environ", env, clear=True):
            config = AuthConfig.from_env()

            assert config.api_keys == []


class TestApiKeyFunctions:
    """Test API key utility functions."""

    def test_hash_api_key(self):
        """Test API key hashing produces consistent results."""
        key = "test-api-key-123"
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex length
        assert hash1 != key  # Should not equal original

    def test_hash_api_key_different_inputs(self):
        """Test different keys produce different hashes."""
        hash1 = hash_api_key("key1")
        hash2 = hash_api_key("key2")

        assert hash1 != hash2

    def test_verify_api_key_valid(self):
        """Test verifying a valid API key."""
        key = "valid-test-key"
        stored_hash = hash_api_key(key)

        assert verify_api_key(key, [stored_hash]) is True

    def test_verify_api_key_invalid(self):
        """Test verifying an invalid API key."""
        valid_key = "valid-test-key"
        stored_hash = hash_api_key(valid_key)

        assert verify_api_key("wrong-key", [stored_hash]) is False

    def test_verify_api_key_multiple_valid(self):
        """Test verifying against multiple valid keys."""
        keys = ["key1", "key2", "key3"]
        hashes = [hash_api_key(k) for k in keys]

        assert verify_api_key("key2", hashes) is True
        assert verify_api_key("invalid", hashes) is False

    def test_verify_api_key_empty_list(self):
        """Test verifying against empty key list."""
        assert verify_api_key("any-key", []) is False

    def test_generate_api_key_unique(self):
        """Test generated API keys are unique."""
        keys = [generate_api_key() for _ in range(100)]

        assert len(set(keys)) == 100  # All unique

    def test_generate_api_key_length(self):
        """Test generated API keys have appropriate length."""
        key = generate_api_key()

        # URL-safe base64 encoding of 32 bytes
        assert len(key) >= 40


class TestIsLocalhost:
    """Test localhost detection."""

    def test_is_localhost_ipv4(self):
        """Test localhost detection for IPv4."""
        request = MagicMock()
        request.client.host = "127.0.0.1"

        assert is_localhost(request) is True

    def test_is_localhost_ipv6(self):
        """Test localhost detection for IPv6."""
        request = MagicMock()
        request.client.host = "::1"

        assert is_localhost(request) is True

    def test_is_localhost_hostname(self):
        """Test localhost detection for hostname."""
        request = MagicMock()
        request.client.host = "localhost"

        assert is_localhost(request) is True

    def test_is_not_localhost(self):
        """Test non-localhost detection."""
        request = MagicMock()
        request.client.host = "192.168.1.100"

        assert is_localhost(request) is False

    def test_is_localhost_no_client(self):
        """Test handling when client is None."""
        request = MagicMock()
        request.client = None

        assert is_localhost(request) is False


class TestAuthMiddleware:
    """Test authentication middleware."""

    @pytest.fixture
    def create_app(self):
        """Factory fixture to create test apps with different configs."""

        def _create_app(config: AuthConfig | None = None) -> FastAPI:
            app = FastAPI()

            @app.get("/")
            async def root():
                return {"status": "ok"}

            @app.get("/health")
            async def health():
                return {"healthy": True}

            @app.get("/api/data")
            async def api_data():
                return {"data": "sensitive"}

            app.add_middleware(AuthMiddleware, config=config)
            return app

        return _create_app

    def test_disabled_mode_allows_all(self, create_app):
        """Test disabled auth mode allows all requests."""
        config = AuthConfig(mode=AuthMode.DISABLED)
        app = create_app(config)
        client = TestClient(app)

        response = client.get("/api/data")
        assert response.status_code == 200

    def test_localhost_only_allows_localhost(self, create_app):
        """Test localhost_only mode allows localhost."""
        config = AuthConfig(mode=AuthMode.LOCALHOST_ONLY)
        app = create_app(config)
        client = TestClient(app)

        # TestClient uses localhost by default
        response = client.get("/api/data")
        assert response.status_code == 200

    def test_excluded_paths_bypass_auth(self, create_app):
        """Test excluded paths bypass authentication."""
        config = AuthConfig(
            mode=AuthMode.API_KEY,
            api_keys=[hash_api_key("secret")],
            localhost_bypass=False,
        )
        app = create_app(config)
        client = TestClient(app)

        # /health is excluded by default
        response = client.get("/health")
        assert response.status_code == 200

    def test_api_key_required_without_bypass(self, create_app):
        """Test API key required when localhost bypass disabled."""
        config = AuthConfig(
            mode=AuthMode.API_KEY,
            api_keys=[hash_api_key("valid-key")],
            localhost_bypass=False,
        )
        app = create_app(config)
        client = TestClient(app)

        # Request without API key
        response = client.get("/api/data")
        assert response.status_code == 401
        assert "API key required" in response.json()["detail"]

    def test_api_key_valid(self, create_app):
        """Test valid API key allows access."""
        config = AuthConfig(
            mode=AuthMode.API_KEY,
            api_keys=[hash_api_key("valid-key")],
            localhost_bypass=False,
        )
        app = create_app(config)
        client = TestClient(app)

        response = client.get("/api/data", headers={"X-API-Key": "valid-key"})
        assert response.status_code == 200

    def test_api_key_invalid(self, create_app):
        """Test invalid API key is rejected."""
        config = AuthConfig(
            mode=AuthMode.API_KEY,
            api_keys=[hash_api_key("valid-key")],
            localhost_bypass=False,
        )
        app = create_app(config)
        client = TestClient(app)

        response = client.get("/api/data", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401
        assert "Invalid API key" in response.json()["detail"]

    def test_api_key_with_localhost_bypass(self, create_app):
        """Test API key mode with localhost bypass enabled."""
        config = AuthConfig(
            mode=AuthMode.API_KEY,
            api_keys=[hash_api_key("secret")],
            localhost_bypass=True,  # Default
        )
        app = create_app(config)
        client = TestClient(app)

        # Should work without API key from localhost
        response = client.get("/api/data")
        assert response.status_code == 200


class TestRequireApiKeyDependency:
    """Test the require_api_key FastAPI dependency."""

    @pytest.fixture
    def app_with_dependency(self):
        """Create app using dependency instead of middleware."""
        from fastapi import Depends

        app = FastAPI()

        @app.get("/public")
        async def public():
            return {"public": True}

        @app.get("/protected")
        async def protected(key: str = Depends(require_api_key)):
            return {"key": key}

        return app

    def test_dependency_allows_localhost(self, app_with_dependency):
        """Test dependency allows localhost without key."""
        with patch.dict("os.environ", {"MOZART_LOCALHOST_BYPASS": "true"}):
            client = TestClient(app_with_dependency)
            response = client.get("/protected")

            # Localhost bypass returns "localhost" as key
            assert response.status_code == 200
            assert response.json()["key"] == "localhost"

    def test_dependency_requires_key_no_bypass(self, app_with_dependency):
        """Test dependency requires key when bypass disabled."""
        env = {
            "MOZART_LOCALHOST_BYPASS": "false",
            "MOZART_API_KEYS": "valid-key",
        }
        with patch.dict("os.environ", env, clear=True):
            client = TestClient(app_with_dependency)

            # Without key
            response = client.get("/protected")
            assert response.status_code == 401


class TestAuthModeEnum:
    """Test AuthMode enumeration."""

    def test_mode_values(self):
        """Test all auth mode values exist."""
        assert AuthMode.DISABLED.value == "disabled"
        assert AuthMode.API_KEY.value == "api_key"
        assert AuthMode.LOCALHOST_ONLY.value == "localhost_only"

    def test_mode_from_string(self):
        """Test creating mode from string value."""
        assert AuthMode("disabled") == AuthMode.DISABLED
        assert AuthMode("api_key") == AuthMode.API_KEY
        assert AuthMode("localhost_only") == AuthMode.LOCALHOST_ONLY


class TestSecurityBestPractices:
    """Test security-related functionality."""

    def test_constant_time_comparison(self):
        """Test that key verification uses constant-time comparison."""
        # This is a basic test - true timing attack prevention requires
        # statistical analysis, but we verify hmac.compare_digest is used
        import inspect

        source = inspect.getsource(verify_api_key)
        assert "hmac.compare_digest" in source

    def test_api_key_not_logged(self):
        """Test that API keys are not exposed in errors."""
        config = AuthConfig(
            mode=AuthMode.API_KEY,
            api_keys=["super-secret-key"],
            localhost_bypass=False,
        )

        app = FastAPI()

        @app.get("/")
        async def root():
            return {"ok": True}

        app.add_middleware(AuthMiddleware, config=config)
        client = TestClient(app)

        response = client.get("/", headers={"X-API-Key": "wrong-key"})

        # Error message should not contain the actual keys
        assert "super-secret-key" not in response.text
        assert "wrong-key" not in response.text

    def test_hash_irreversibility(self):
        """Test that hashed keys cannot easily reveal original."""
        key = "my-secret-api-key"
        hashed = hash_api_key(key)

        # Hash should not contain key substring
        assert key not in hashed
        # Should be hexadecimal
        assert all(c in "0123456789abcdef" for c in hashed)


# =============================================================================
# Rate Limiting Tests (rate_limit.py)
# =============================================================================


class TestSlidingWindowCounter:
    """Tests for the sliding window rate limiter implementation."""

    def test_allows_requests_under_limit(self) -> None:
        """Requests under the max are allowed."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=5)
        for _ in range(5):
            allowed, _, _ = counter.is_allowed("client-a")
            assert allowed

    def test_blocks_requests_over_limit(self) -> None:
        """Requests exceeding the max are blocked."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=3)
        for _ in range(3):
            counter.is_allowed("client-a")

        allowed, remaining, reset = counter.is_allowed("client-a")
        assert not allowed
        assert remaining == 0
        assert reset > 0

    def test_different_keys_independent(self) -> None:
        """Different client keys have independent counters."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=2)
        counter.is_allowed("client-a")
        counter.is_allowed("client-a")

        allowed, _, _ = counter.is_allowed("client-b")
        assert allowed

    def test_remaining_decreases(self) -> None:
        """Remaining count decreases with each request."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=3)
        _, remaining1, _ = counter.is_allowed("c")
        _, remaining2, _ = counter.is_allowed("c")
        assert remaining1 == 2
        assert remaining2 == 1

    def test_get_count(self) -> None:
        """get_count returns current request count in window."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)
        assert counter.get_count("c") == 0
        counter.is_allowed("c")
        counter.is_allowed("c")
        assert counter.get_count("c") == 2

    def test_reset_specific_key(self) -> None:
        """reset(key) clears only that key's counter."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)
        counter.is_allowed("a")
        counter.is_allowed("b")
        counter.reset("a")
        assert counter.get_count("a") == 0
        assert counter.get_count("b") == 1

    def test_reset_all(self) -> None:
        """reset(None) clears all counters."""
        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)
        counter.is_allowed("a")
        counter.is_allowed("b")
        counter.reset(None)
        assert counter.get_count("a") == 0
        assert counter.get_count("b") == 0

    def test_expired_entries_cleaned(self) -> None:
        """Entries older than the window are cleaned up."""
        import time

        from mozart.dashboard.auth.rate_limit import SlidingWindowCounter

        counter = SlidingWindowCounter(window_seconds=1, max_requests=2)
        counter.is_allowed("c")
        counter.is_allowed("c")

        allowed, _, _ = counter.is_allowed("c")
        assert not allowed

        time.sleep(1.5)
        allowed, _, _ = counter.is_allowed("c")
        assert allowed


class TestRateLimiter:
    """Tests for the combined multi-window rate limiter."""

    def test_disabled_allows_all(self) -> None:
        """When disabled, all requests are allowed."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig, RateLimiter

        config = RateLimitConfig(enabled=False)
        limiter = RateLimiter(config)
        allowed, info = limiter.check("client")
        assert allowed
        assert info["enabled"] is False

    def test_burst_limit_enforced(self) -> None:
        """Burst limit (1-second window) is enforced."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig, RateLimiter

        config = RateLimitConfig(
            burst_limit=2, requests_per_minute=100, requests_per_hour=1000,
        )
        limiter = RateLimiter(config)
        limiter.check("c")
        limiter.check("c")
        allowed, info = limiter.check("c")
        assert not allowed
        assert info["limit"] == "burst"

    def test_minute_limit_enforced(self) -> None:
        """Per-minute limit is enforced after burst passes."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig, RateLimiter

        config = RateLimitConfig(
            burst_limit=100, requests_per_minute=3, requests_per_hour=1000,
        )
        limiter = RateLimiter(config)
        for _ in range(3):
            limiter.check("c")
        allowed, info = limiter.check("c")
        assert not allowed
        assert info["limit"] == "minute"

    def test_hour_limit_enforced(self) -> None:
        """Per-hour limit is enforced after burst and minute pass."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig, RateLimiter

        config = RateLimitConfig(
            burst_limit=100, requests_per_minute=100, requests_per_hour=3,
        )
        limiter = RateLimiter(config)
        for _ in range(3):
            limiter.check("c")
        allowed, info = limiter.check("c")
        assert not allowed
        assert info["limit"] == "hour"

    def test_allowed_returns_remaining_info(self) -> None:
        """Allowed requests return remaining counts for all windows."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig, RateLimiter

        config = RateLimitConfig(
            burst_limit=10, requests_per_minute=50, requests_per_hour=500,
        )
        limiter = RateLimiter(config)
        allowed, info = limiter.check("c")
        assert allowed
        assert "burst_remaining" in info
        assert "minute_remaining" in info
        assert "hour_remaining" in info

    def test_reset_clears_all_windows(self) -> None:
        """Reset clears counters for all time windows."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig, RateLimiter

        config = RateLimitConfig(burst_limit=2)
        limiter = RateLimiter(config)
        limiter.check("c")
        limiter.check("c")
        limiter.reset("c")
        allowed, _ = limiter.check("c")
        assert allowed


class TestGetClientIdentifier:
    """Tests for the client identifier extraction."""

    def test_defaults_to_ip(self) -> None:
        """Without by_api_key, returns IP-based identifier."""
        from mozart.dashboard.auth.rate_limit import get_client_identifier

        request = MagicMock()
        request.client.host = "192.168.1.1"
        request.headers = {}
        result = get_client_identifier(request, by_api_key=False)
        assert result == "ip:192.168.1.1"

    def test_uses_api_key_when_enabled(self) -> None:
        """With by_api_key=True and key present, returns key-based identifier."""
        from mozart.dashboard.auth.rate_limit import get_client_identifier

        request = MagicMock()
        request.headers = {"X-API-Key": "sk-abc123def456"}
        result = get_client_identifier(request, by_api_key=True)
        assert result.startswith("key:")

    def test_falls_back_to_ip_when_no_key(self) -> None:
        """With by_api_key=True but no key header, falls back to IP."""
        from mozart.dashboard.auth.rate_limit import get_client_identifier

        request = MagicMock()
        request.headers = {}
        request.client.host = "10.0.0.1"
        result = get_client_identifier(request, by_api_key=True)
        assert result == "ip:10.0.0.1"

    def test_unknown_when_no_client(self) -> None:
        """Returns 'unknown' when request has no client info."""
        from mozart.dashboard.auth.rate_limit import get_client_identifier

        request = MagicMock()
        request.client = None
        request.headers = {}
        result = get_client_identifier(request, by_api_key=False)
        assert result == "ip:unknown"


class TestRateLimitMiddleware:
    """Tests for the ASGI rate limit middleware."""

    @staticmethod
    def _create_app(config=None):
        from mozart.dashboard.auth.rate_limit import RateLimitMiddleware

        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, config=config)

        @app.get("/api/test")
        def mock_endpoint():
            return {"ok": True}

        @app.get("/health")
        def health():
            return {"status": "healthy"}

        return app

    def test_adds_rate_limit_headers(self) -> None:
        """Allowed requests include rate limit headers."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig

        config = RateLimitConfig(requests_per_minute=100)
        app = self._create_app(config)
        client = TestClient(app)
        resp = client.get("/api/test")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers

    def test_returns_429_when_limited(self) -> None:
        """Returns 429 with Retry-After when rate limit exceeded."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig

        config = RateLimitConfig(
            burst_limit=1, requests_per_minute=100, requests_per_hour=1000,
        )
        app = self._create_app(config)
        client = TestClient(app)
        client.get("/api/test")
        resp = client.get("/api/test")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        body = resp.json()
        assert "rate limit" in body["detail"].lower()

    def test_excluded_paths_bypass_limits(self) -> None:
        """Excluded paths (like /health) bypass rate limiting."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig

        config = RateLimitConfig(burst_limit=1)
        app = self._create_app(config)
        client = TestClient(app)
        for _ in range(10):
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_disabled_passes_through(self) -> None:
        """When disabled, all requests pass through without headers."""
        from mozart.dashboard.auth.rate_limit import RateLimitConfig

        config = RateLimitConfig(enabled=False)
        app = self._create_app(config)
        client = TestClient(app)
        resp = client.get("/api/test")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers


# =============================================================================
# Security Module Tests (security.py)
# =============================================================================

import os

from mozart.dashboard.auth.security import (
    SecurityConfig,
    SecurityHeadersMiddleware,
    configure_cors,
    sanitize_filename,
    validate_job_id,
    validate_path_component,
)


class TestSecurityConfig:
    """Tests for the security configuration."""

    def test_default_config(self) -> None:
        """Default config has reasonable security settings."""
        config = SecurityConfig()
        assert config.add_security_headers is True
        assert "nosniff" in config.x_content_type_options
        assert "SAMEORIGIN" in config.x_frame_options
        assert len(config.cors_origins) > 0

    def test_from_env(self) -> None:
        """from_env reads MOZART_CORS_ORIGINS."""
        with patch.dict(
            os.environ,
            {"MOZART_CORS_ORIGINS": "https://app.example.com,https://api.example.com"},
        ):
            config = SecurityConfig.from_env()
            assert "https://app.example.com" in config.cors_origins
            assert "https://api.example.com" in config.cors_origins

    def test_from_env_defaults(self) -> None:
        """from_env uses defaults when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = SecurityConfig.from_env()
            assert len(config.cors_origins) > 0

    def test_production_config(self) -> None:
        """Production config is more restrictive."""
        config = SecurityConfig.production()
        assert config.cors_origins == []
        assert config.cors_allow_credentials is False
        assert "DELETE" not in config.cors_allow_methods


class TestSecurityHeadersMiddleware:
    """Tests for the security headers middleware."""

    @staticmethod
    def _create_app(config=None):
        from starlette.responses import HTMLResponse

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, config=config)

        @app.get("/test")
        def mock_endpoint():
            return {"ok": True}

        @app.get("/test-html")
        def mock_html_endpoint():
            return HTMLResponse("<html><body>ok</body></html>")

        return app

    def test_adds_security_headers_on_html(self) -> None:
        """Default config adds all security headers on HTML responses."""
        app = self._create_app()
        client = TestClient(app)
        resp = client.get("/test-html")
        assert resp.status_code == 200
        assert "Content-Security-Policy" in resp.headers
        assert "X-Content-Type-Options" in resp.headers
        assert "X-Frame-Options" in resp.headers
        assert "X-XSS-Protection" in resp.headers
        assert "Referrer-Policy" in resp.headers
        assert "Strict-Transport-Security" in resp.headers

    def test_adds_common_headers_on_json(self) -> None:
        """Non-CSP security headers are added on JSON responses."""
        app = self._create_app()
        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert "X-Content-Type-Options" in resp.headers
        assert "X-Frame-Options" in resp.headers
        assert "Referrer-Policy" in resp.headers
        # CSP, HSTS, XSS-Protection only on HTML
        assert "Content-Security-Policy" not in resp.headers
        assert "Strict-Transport-Security" not in resp.headers
        assert "X-XSS-Protection" not in resp.headers

    def test_disabled_headers(self) -> None:
        """When add_security_headers=False, no security headers are added."""
        config = SecurityConfig(add_security_headers=False)
        app = self._create_app(config)
        client = TestClient(app)
        resp = client.get("/test-html")
        assert resp.status_code == 200
        assert "Content-Security-Policy" not in resp.headers

    def test_csp_header_value(self) -> None:
        """CSP header contains expected directives."""
        app = self._create_app()
        client = TestClient(app)
        resp = client.get("/test-html")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp
        assert "'self'" in csp


class TestConfigureCorsHelper:
    """Tests for the CORS configuration helper."""

    def test_configure_cors_adds_middleware(self) -> None:
        """configure_cors calls app.add_middleware with CORS settings."""
        mock_app = MagicMock()
        config = SecurityConfig(cors_origins=["https://example.com"])
        configure_cors(mock_app, config)
        mock_app.add_middleware.assert_called_once()


class TestValidateJobId:
    """Tests for job ID validation."""

    @pytest.mark.parametrize(
        "job_id",
        ["my-job", "job_123", "Job.Name.v2", "a" * 256, "simple"],
    )
    def test_valid_job_ids(self, job_id: str) -> None:
        """Valid job IDs are accepted."""
        assert validate_job_id(job_id) is True

    @pytest.mark.parametrize(
        "job_id",
        [
            "",
            "a" * 257,
            "job/../../etc/passwd",
            "job<script>",
            "job; rm -rf /",
            "job\x00null",
        ],
    )
    def test_invalid_job_ids(self, job_id: str) -> None:
        """Invalid/malicious job IDs are rejected."""
        assert validate_job_id(job_id) is False


class TestValidatePathComponent:
    """Tests for path component validation."""

    @pytest.mark.parametrize(
        "component", ["output.txt", "sheet-1", "workspace_v2"],
    )
    def test_valid_paths(self, component: str) -> None:
        assert validate_path_component(component) is True

    @pytest.mark.parametrize(
        "component", ["", "..", "../etc/passwd", "/absolute/path", "file\x00.txt"],
    )
    def test_invalid_paths(self, component: str) -> None:
        assert validate_path_component(component) is False


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_normal_filename(self) -> None:
        assert sanitize_filename("report.txt") == "report.txt"

    def test_removes_path_separators(self) -> None:
        assert sanitize_filename("../../etc/passwd") == ".._.._etc_passwd"

    def test_removes_null_bytes(self) -> None:
        assert sanitize_filename("file\x00.txt") == "file.txt"

    def test_truncates_long_filenames(self) -> None:
        assert len(sanitize_filename("a" * 300)) == 255

    def test_empty_becomes_unnamed(self) -> None:
        assert sanitize_filename("") == "unnamed"

    def test_backslashes_replaced(self) -> None:
        assert sanitize_filename("path\\to\\file.txt") == "path_to_file.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
