"""Tests for Marianne Dashboard rate limiting module."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marianne.dashboard.auth.rate_limit import (
    RateLimitConfig,
    RateLimiter,
    RateLimitMiddleware,
    SlidingWindowCounter,
    get_client_identifier,
)


class TestSlidingWindowCounter:
    """Test sliding window rate limit counter."""

    def test_allows_under_limit(self):
        """Test requests under limit are allowed."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)

        for i in range(10):
            allowed, remaining, _ = counter.is_allowed("test-key")
            assert allowed is True
            assert remaining == 10 - i - 1

    def test_blocks_over_limit(self):
        """Test requests over limit are blocked."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=3)

        # Use up the limit
        for _ in range(3):
            counter.is_allowed("test-key")

        # Should be blocked
        allowed, remaining, reset = counter.is_allowed("test-key")
        assert allowed is False
        assert remaining == 0
        assert reset > 0

    def test_different_keys_independent(self):
        """Test different keys have independent limits."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=2)

        # Use up limit for key1
        counter.is_allowed("key1")
        counter.is_allowed("key1")
        allowed_key1, _, _ = counter.is_allowed("key1")

        # key2 should still work
        allowed_key2, _, _ = counter.is_allowed("key2")

        assert allowed_key1 is False
        assert allowed_key2 is True

    def test_get_count(self):
        """Test getting current count."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)

        assert counter.get_count("test-key") == 0

        counter.is_allowed("test-key")
        counter.is_allowed("test-key")
        counter.is_allowed("test-key")

        assert counter.get_count("test-key") == 3

    def test_reset_key(self):
        """Test resetting a specific key."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)

        counter.is_allowed("key1")
        counter.is_allowed("key2")

        counter.reset("key1")

        assert counter.get_count("key1") == 0
        assert counter.get_count("key2") == 1

    def test_reset_all(self):
        """Test resetting all keys."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=10)

        counter.is_allowed("key1")
        counter.is_allowed("key2")

        counter.reset()

        assert counter.get_count("key1") == 0
        assert counter.get_count("key2") == 0

    def test_window_expiry(self):
        """Test that old requests expire from window."""
        counter = SlidingWindowCounter(window_seconds=60, max_requests=2)
        base_time = 1000000.0

        # Use up limit at base_time
        with patch("marianne.dashboard.auth.rate_limit.time.time", return_value=base_time):
            counter.is_allowed("test-key")
            counter.is_allowed("test-key")
            allowed1, _, _ = counter.is_allowed("test-key")
        assert allowed1 is False

        # Advance time beyond 60s window
        with patch("marianne.dashboard.auth.rate_limit.time.time", return_value=base_time + 61):
            allowed2, _, _ = counter.is_allowed("test-key")
        assert allowed2 is True


class TestRateLimiter:
    """Test combined rate limiter."""

    def test_disabled_allows_all(self):
        """Test disabled limiter allows all requests."""
        config = RateLimitConfig(enabled=False)
        limiter = RateLimiter(config)

        for _ in range(1000):
            allowed, info = limiter.check("test-key")
            assert allowed is True
            assert info["enabled"] is False

    def test_burst_limit(self):
        """Test burst limit is enforced."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=3,
            requests_per_minute=100,
            requests_per_hour=1000,
        )
        limiter = RateLimiter(config)

        # Should allow burst_limit requests
        for _ in range(3):
            allowed, _ = limiter.check("test-key")
            assert allowed is True

        # Should block next request
        allowed, info = limiter.check("test-key")
        assert allowed is False
        assert info["limit"] == "burst"

    def test_minute_limit(self):
        """Test minute limit is enforced."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=100,  # High burst to not trigger
            requests_per_minute=5,
            requests_per_hour=1000,
        )
        limiter = RateLimiter(config)

        # Should allow up to minute limit
        for _ in range(5):
            allowed, _ = limiter.check("test-key")
            assert allowed is True

        # Should block at minute limit
        allowed, info = limiter.check("test-key")
        assert allowed is False
        assert info["limit"] == "minute"

    def test_check_returns_remaining(self):
        """Test check returns remaining counts."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=10,
            requests_per_minute=60,
            requests_per_hour=1000,
        )
        limiter = RateLimiter(config)

        allowed, info = limiter.check("test-key")

        assert allowed is True
        assert "burst_remaining" in info
        assert "minute_remaining" in info
        assert "hour_remaining" in info

    def test_reset(self):
        """Test resetting rate limiter."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=2,
            requests_per_minute=100,
            requests_per_hour=1000,
        )
        limiter = RateLimiter(config)

        # Use up burst limit
        limiter.check("test-key")
        limiter.check("test-key")
        allowed1, _ = limiter.check("test-key")
        assert allowed1 is False

        # Reset
        limiter.reset("test-key")

        # Should be allowed again
        allowed2, _ = limiter.check("test-key")
        assert allowed2 is True


class TestGetClientIdentifier:
    """Test client identifier extraction."""

    def test_identifier_by_ip(self):
        """Test identifier uses IP by default."""
        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.headers.get.return_value = None

        identifier = get_client_identifier(request, by_api_key=False)

        assert identifier == "ip:192.168.1.100"

    def test_identifier_by_api_key(self):
        """Test identifier uses API key when configured."""
        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.headers.get.return_value = "test-api-key-12345"

        identifier = get_client_identifier(request, by_api_key=True)

        # Should truncate key
        assert identifier.startswith("key:")
        assert "12345" not in identifier  # Full key not exposed

    def test_identifier_fallback_to_ip(self):
        """Test falls back to IP when no API key."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers.get.return_value = None

        identifier = get_client_identifier(request, by_api_key=True)

        assert identifier == "ip:10.0.0.1"

    def test_identifier_no_client(self):
        """Test handling when client is None."""
        request = MagicMock()
        request.client = None
        request.headers.get.return_value = None

        identifier = get_client_identifier(request, by_api_key=False)

        assert identifier == "ip:unknown"


class TestRateLimitMiddleware:
    """Test rate limit middleware."""

    @pytest.fixture
    def create_app(self):
        """Factory fixture to create test apps."""

        def _create_app(config: RateLimitConfig | None = None) -> FastAPI:
            app = FastAPI()

            @app.get("/")
            async def root():
                return {"status": "ok"}

            @app.get("/health")
            async def health():
                return {"healthy": True}

            @app.get("/api/data")
            async def api_data():
                return {"data": "value"}

            app.add_middleware(RateLimitMiddleware, config=config)
            return app

        return _create_app

    def test_disabled_allows_all(self, create_app):
        """Test disabled middleware allows all requests."""
        config = RateLimitConfig(enabled=False)
        app = create_app(config)
        client = TestClient(app)

        for _ in range(100):
            response = client.get("/api/data")
            assert response.status_code == 200

    def test_excluded_paths_bypass(self, create_app):
        """Test excluded paths bypass rate limiting."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=1,  # Very restrictive
        )
        app = create_app(config)
        client = TestClient(app)

        # /health is excluded by default
        for _ in range(10):
            response = client.get("/health")
            assert response.status_code == 200

    def test_rate_limit_enforced(self, create_app):
        """Test rate limit is enforced."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=2,
            requests_per_minute=100,
            requests_per_hour=1000,
        )
        app = create_app(config)
        client = TestClient(app)

        # Should allow burst_limit requests
        response1 = client.get("/api/data")
        response2 = client.get("/api/data")
        assert response1.status_code == 200
        assert response2.status_code == 200

        # Should return 429 on excess
        response3 = client.get("/api/data")
        assert response3.status_code == 429
        assert "Rate limit exceeded" in response3.json()["detail"]

    def test_rate_limit_headers(self, create_app):
        """Test rate limit headers are included."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=10,
            requests_per_minute=60,
        )
        app = create_app(config)
        client = TestClient(app)

        response = client.get("/api/data")

        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers

    def test_429_includes_retry_after(self, create_app):
        """Test 429 response includes Retry-After header."""
        config = RateLimitConfig(
            enabled=True,
            burst_limit=1,
        )
        app = create_app(config)
        client = TestClient(app)

        # Use up limit
        client.get("/api/data")

        # Should get 429 with Retry-After
        response = client.get("/api/data")

        assert response.status_code == 429
        assert "Retry-After" in response.headers
        assert int(response.headers["Retry-After"]) > 0


class TestRateLimitConfig:
    """Test rate limit configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RateLimitConfig()

        assert config.enabled is True
        assert config.requests_per_minute == 60
        assert config.requests_per_hour == 1000
        assert config.burst_limit == 10
        assert "/health" in config.excluded_paths

    def test_custom_config(self):
        """Test custom configuration."""
        config = RateLimitConfig(
            enabled=False,
            requests_per_minute=120,
            burst_limit=20,
        )

        assert config.enabled is False
        assert config.requests_per_minute == 120
        assert config.burst_limit == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
