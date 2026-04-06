"""Tests for Mozart Dashboard security module."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from marianne.dashboard.auth.security import (
    SecurityConfig,
    SecurityHeadersMiddleware,
    configure_cors,
    sanitize_filename,
    validate_job_id,
    validate_path_component,
)


class TestSecurityConfig:
    """Test security configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SecurityConfig()

        assert "http://localhost:8080" in config.cors_origins
        assert config.cors_allow_credentials is True
        assert config.add_security_headers is True
        assert "nosniff" in config.x_content_type_options

    def test_from_env(self):
        """Test config from environment variables."""
        env = {
            "MOZART_CORS_ORIGINS": "https://app.example.com,https://api.example.com",
            "MOZART_CORS_CREDENTIALS": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            config = SecurityConfig.from_env()

            assert "https://app.example.com" in config.cors_origins
            assert "https://api.example.com" in config.cors_origins
            assert config.cors_allow_credentials is False

    def test_production_config(self):
        """Test production configuration is strict."""
        config = SecurityConfig.production()

        assert config.cors_origins == []  # No CORS
        assert config.cors_allow_credentials is False
        assert "DELETE" not in config.cors_allow_methods  # Restricted methods


class TestSecurityHeadersMiddleware:
    """Test security headers middleware."""

    @pytest.fixture
    def app_with_security(self):
        """Create app with security headers middleware."""
        from starlette.responses import HTMLResponse

        app = FastAPI()

        @app.get("/")
        async def root():
            return {"status": "ok"}

        @app.get("/api/data")
        async def api_data():
            return {"data": "value"}

        @app.get("/html")
        async def html_page():
            return HTMLResponse("<html><body>ok</body></html>")

        config = SecurityConfig()
        app.add_middleware(SecurityHeadersMiddleware, config=config)
        return app

    def test_adds_security_headers(self, app_with_security):
        """Test all security headers are added to HTML responses."""
        client = TestClient(app_with_security)
        response = client.get("/html")

        assert response.status_code == 200

        # CSP, HSTS, XSS-Protection only on HTML
        assert "Content-Security-Policy" in response.headers
        assert "Strict-Transport-Security" in response.headers
        assert "X-XSS-Protection" in response.headers
        # Common headers on all responses
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers
        assert "Referrer-Policy" in response.headers

    def test_json_gets_common_headers_only(self, app_with_security):
        """Test JSON responses get common headers but not CSP/HSTS."""
        client = TestClient(app_with_security)
        response = client.get("/")

        assert response.status_code == 200
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers
        assert "Referrer-Policy" in response.headers
        # CSP, HSTS, XSS-Protection not on JSON
        assert "Content-Security-Policy" not in response.headers
        assert "Strict-Transport-Security" not in response.headers
        assert "X-XSS-Protection" not in response.headers

    def test_csp_header_content(self, app_with_security):
        """Test CSP header has expected directives."""
        client = TestClient(app_with_security)
        response = client.get("/html")

        csp = response.headers["Content-Security-Policy"]

        assert "default-src" in csp
        assert "script-src" in csp
        assert "style-src" in csp

    def test_hsts_header(self, app_with_security):
        """Test HSTS header is properly configured."""
        client = TestClient(app_with_security)
        response = client.get("/html")

        hsts = response.headers["Strict-Transport-Security"]

        assert "max-age=" in hsts
        assert "includeSubDomains" in hsts

    def test_x_content_type_options(self, app_with_security):
        """Test X-Content-Type-Options is nosniff."""
        client = TestClient(app_with_security)
        response = client.get("/")

        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options(self, app_with_security):
        """Test X-Frame-Options is set."""
        client = TestClient(app_with_security)
        response = client.get("/")

        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"

    def test_headers_disabled(self):
        """Test headers can be disabled."""
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"ok": True}

        config = SecurityConfig(add_security_headers=False)
        app.add_middleware(SecurityHeadersMiddleware, config=config)

        client = TestClient(app)
        response = client.get("/")

        # Should not have security headers
        assert "Content-Security-Policy" not in response.headers
        assert "X-Frame-Options" not in response.headers


class TestConfigureCors:
    """Test CORS configuration."""

    def test_cors_configured(self):
        """Test CORS middleware is added."""
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"ok": True}

        config = SecurityConfig(
            cors_origins=["http://example.com"],
        )
        configure_cors(app, config)

        # Check middleware was added (by making OPTIONS request)
        client = TestClient(app)
        response = client.options(
            "/",
            headers={"Origin": "http://example.com"}
        )

        # CORS should allow the origin
        assert "access-control-allow-origin" in response.headers or response.status_code == 200


class TestValidateJobId:
    """Test job ID validation."""

    def test_valid_job_ids(self):
        """Test valid job IDs pass validation."""
        valid_ids = [
            "my-job",
            "job123",
            "Job_Name",
            "job.v2",
            "a",
            "A-B-C_1.2.3",
        ]

        for job_id in valid_ids:
            assert validate_job_id(job_id) is True, f"Expected {job_id} to be valid"

    def test_invalid_job_ids(self):
        """Test invalid job IDs fail validation."""
        invalid_ids = [
            "",  # Empty
            " ",  # Space only
            "job id",  # Contains space
            "job/path",  # Contains slash
            "job\\path",  # Contains backslash
            "../etc/passwd",  # Path traversal
            "a" * 300,  # Too long
            "job<script>",  # HTML
            "job\x00null",  # Null byte
        ]

        for job_id in invalid_ids:
            assert validate_job_id(job_id) is False, f"Expected {job_id!r} to be invalid"

    def test_special_characters(self):
        """Test special characters are rejected."""
        special_chars = "!@#$%^&*()+=[]{}|;:'\",<>?/\\"

        for char in special_chars:
            job_id = f"job{char}name"
            assert validate_job_id(job_id) is False, f"Expected job ID with {char!r} to be invalid"


class TestValidatePathComponent:
    """Test path component validation."""

    def test_valid_components(self):
        """Test valid path components pass."""
        valid_components = [
            "file.txt",
            "my-file",
            "file_name",
            "file123",
            "a",
        ]

        for component in valid_components:
            assert validate_path_component(component) is True

    def test_directory_traversal_blocked(self):
        """Test directory traversal is blocked."""
        traversal_attempts = [
            "..",
            "../",
            "..\\",
            "foo/../bar",
            "foo/../../etc",
        ]

        for attempt in traversal_attempts:
            assert validate_path_component(attempt) is False

    def test_absolute_path_blocked(self):
        """Test absolute paths are blocked."""
        assert validate_path_component("/etc/passwd") is False
        assert validate_path_component("/home/user") is False

    def test_null_byte_blocked(self):
        """Test null bytes are blocked."""
        assert validate_path_component("file\x00.txt") is False
        assert validate_path_component("\x00") is False

    def test_empty_blocked(self):
        """Test empty string is blocked."""
        assert validate_path_component("") is False


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_removes_path_separators(self):
        """Test path separators are replaced."""
        assert sanitize_filename("foo/bar/baz.txt") == "foo_bar_baz.txt"
        assert sanitize_filename("foo\\bar\\baz.txt") == "foo_bar_baz.txt"

    def test_removes_null_bytes(self):
        """Test null bytes are removed."""
        assert sanitize_filename("file\x00.txt") == "file.txt"

    def test_truncates_long_names(self):
        """Test long filenames are truncated."""
        long_name = "a" * 300
        result = sanitize_filename(long_name)
        assert len(result) <= 255

    def test_handles_empty(self):
        """Test empty filename becomes 'unnamed'."""
        assert sanitize_filename("") == "unnamed"

    def test_preserves_valid_names(self):
        """Test valid filenames are preserved."""
        assert sanitize_filename("document.pdf") == "document.pdf"
        assert sanitize_filename("my-file_v2.tar.gz") == "my-file_v2.tar.gz"


class TestSecurityIntegration:
    """Integration tests for security features."""

    def test_combined_middleware(self):
        """Test security headers and auth work together."""
        from marianne.dashboard.auth import AuthConfig, AuthMiddleware, AuthMode

        app = FastAPI()

        @app.get("/")
        async def root():
            return {"ok": True}

        # Add both middlewares
        auth_config = AuthConfig(mode=AuthMode.LOCALHOST_ONLY)
        security_config = SecurityConfig()

        app.add_middleware(SecurityHeadersMiddleware, config=security_config)
        app.add_middleware(AuthMiddleware, config=auth_config)

        client = TestClient(app)
        response = client.get("/")

        # Should have both auth pass and security headers
        assert response.status_code == 200
        assert "X-Content-Type-Options" in response.headers

    def test_security_on_error_responses(self):
        """Test security headers on error responses."""
        from fastapi import HTTPException

        app = FastAPI()

        @app.get("/error")
        async def error():
            raise HTTPException(status_code=400, detail="Bad request")

        config = SecurityConfig()
        app.add_middleware(SecurityHeadersMiddleware, config=config)

        client = TestClient(app)
        response = client.get("/error")

        # Error responses should also have security headers
        assert response.status_code == 400
        assert "X-Content-Type-Options" in response.headers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
