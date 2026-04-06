"""Tests for Mozart Dashboard API."""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marianne.core.checkpoint import CheckpointState, JobStatus, SheetState, SheetStatus
from marianne.dashboard import create_app
from marianne.state.json_backend import JsonStateBackend

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_state_dir() -> Path:
    """Create a temporary directory for state files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def state_backend(temp_state_dir: Path) -> JsonStateBackend:
    """Create a test state backend."""
    return JsonStateBackend(temp_state_dir)


@pytest.fixture
def app(state_backend: JsonStateBackend) -> TestClient:
    """Create test client with configured app."""
    test_app = create_app(state_backend=state_backend)
    return TestClient(test_app)


@pytest.fixture
def sample_job() -> CheckpointState:
    """Create a sample job for testing."""
    return CheckpointState(
        job_id="test-job-123",
        job_name="Test Job",
        total_sheets=5,
        status=JobStatus.RUNNING,
        last_completed_sheet=2,
        current_sheet=3,
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.COMPLETED,
                attempt_count=2,
                validation_passed=True,
            ),
            3: SheetState(
                sheet_num=3,
                status=SheetStatus.IN_PROGRESS,
                attempt_count=1,
            ),
        },
    )


@pytest.fixture
def completed_job() -> CheckpointState:
    """Create a completed job for testing."""
    return CheckpointState(
        job_id="completed-job-456",
        job_name="Completed Job",
        total_sheets=3,
        status=JobStatus.COMPLETED,
        last_completed_sheet=3,
        completed_at=datetime.now(UTC),
        sheets={
            i: SheetState(
                sheet_num=i,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            )
            for i in range(1, 4)
        },
    )


@pytest.fixture
def failed_job() -> CheckpointState:
    """Create a failed job for testing."""
    return CheckpointState(
        job_id="failed-job-789",
        job_name="Failed Job",
        total_sheets=4,
        status=JobStatus.FAILED,
        last_completed_sheet=1,
        error_message="Sheet 2 validation failed",
        sheets={
            1: SheetState(
                sheet_num=1,
                status=SheetStatus.COMPLETED,
                attempt_count=1,
                validation_passed=True,
            ),
            2: SheetState(
                sheet_num=2,
                status=SheetStatus.FAILED,
                attempt_count=3,
                error_message="Validation failed",
            ),
        },
    )


# ============================================================================
# Health Check Tests
# ============================================================================


class TestHealthCheck:
    """Tests for health check endpoint."""

    def test_health_check_returns_healthy(self, app: TestClient) -> None:
        """Health endpoint returns healthy status."""
        response = app.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "mozart-dashboard"
        assert "version" in data

    def test_health_check_includes_version(self, app: TestClient) -> None:
        """Health endpoint includes version information."""
        response = app.get("/health")
        data = response.json()
        assert data["version"] == "0.1.0"


# ============================================================================
# Job List Tests
# ============================================================================


class TestListJobs:
    """Tests for job listing endpoint."""

    def test_list_jobs_empty(self, app: TestClient) -> None:
        """List returns empty when no jobs exist."""
        response = app.get("/api/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    async def test_list_jobs_with_jobs(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
        completed_job: CheckpointState,
    ) -> None:
        """List returns all jobs."""
        # Save jobs to backend
        await state_backend.save(sample_job)
        await state_backend.save(completed_job)

        response = app.get("/api/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["jobs"]) == 2

        # Check job summaries have expected fields
        job_ids = {j["job_id"] for j in data["jobs"]}
        assert "test-job-123" in job_ids
        assert "completed-job-456" in job_ids

    async def test_list_jobs_with_status_filter(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
        completed_job: CheckpointState,
        failed_job: CheckpointState,
    ) -> None:
        """List filters by status correctly."""
        await state_backend.save(sample_job)
        await state_backend.save(completed_job)
        await state_backend.save(failed_job)

        # Filter for completed only
        response = app.get("/api/jobs?status=completed")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["job_id"] == "completed-job-456"

        # Filter for running only
        response = app.get("/api/jobs?status=running")
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["job_id"] == "test-job-123"

        # Filter for failed only
        response = app.get("/api/jobs?status=failed")
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["job_id"] == "failed-job-789"

    async def test_list_jobs_with_limit(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
        completed_job: CheckpointState,
        failed_job: CheckpointState,
    ) -> None:
        """List respects limit parameter."""
        await state_backend.save(sample_job)
        await state_backend.save(completed_job)
        await state_backend.save(failed_job)

        response = app.get("/api/jobs?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 2
        assert data["total"] == 3  # Total still shows all matching

    async def test_list_jobs_returns_progress(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
    ) -> None:
        """Job summaries include progress information."""
        await state_backend.save(sample_job)

        response = app.get("/api/jobs")
        data = response.json()
        job = data["jobs"][0]

        assert job["total_sheets"] == 5
        assert job["completed_sheets"] == 2
        assert job["progress_percent"] == 40.0


# ============================================================================
# Job Detail Tests
# ============================================================================


class TestGetJob:
    """Tests for job detail endpoint."""

    async def test_get_job_success(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
    ) -> None:
        """Get job returns full details."""
        await state_backend.save(sample_job)

        response = app.get("/api/jobs/test-job-123")
        assert response.status_code == 200
        data = response.json()

        assert data["job_id"] == "test-job-123"
        assert data["job_name"] == "Test Job"
        assert data["status"] == "running"
        assert data["total_sheets"] == 5
        assert data["last_completed_sheet"] == 2
        assert data["current_sheet"] == 3
        assert len(data["sheets"]) == 3

    def test_get_job_not_found(self, app: TestClient) -> None:
        """Get job returns 404 for missing job."""
        response = app.get("/api/jobs/nonexistent-job")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_get_job_includes_sheets(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
    ) -> None:
        """Get job includes sheet details."""
        await state_backend.save(sample_job)

        response = app.get("/api/jobs/test-job-123")
        data = response.json()
        sheets = data["sheets"]

        # Check sheet data
        assert len(sheets) == 3
        sheet_by_num = {b["sheet_num"]: b for b in sheets}

        assert sheet_by_num[1]["status"] == "completed"
        assert sheet_by_num[1]["validation_passed"] is True
        assert sheet_by_num[2]["attempt_count"] == 2
        assert sheet_by_num[3]["status"] == "in_progress"

    async def test_get_job_includes_error_info(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        failed_job: CheckpointState,
    ) -> None:
        """Get job includes error information for failed jobs."""
        await state_backend.save(failed_job)

        response = app.get("/api/jobs/failed-job-789")
        data = response.json()

        assert data["status"] == "failed"
        assert data["error_message"] == "Sheet 2 validation failed"


# ============================================================================
# Job Status Tests
# ============================================================================


class TestGetJobStatus:
    """Tests for job status endpoint."""

    async def test_get_status_success(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
    ) -> None:
        """Status endpoint returns focused status info."""
        await state_backend.save(sample_job)

        response = app.get("/api/jobs/test-job-123/status")
        assert response.status_code == 200
        data = response.json()

        assert data["job_id"] == "test-job-123"
        assert data["status"] == "running"
        assert data["progress_percent"] == 40.0
        assert data["completed_sheets"] == 2
        assert data["total_sheets"] == 5
        assert data["current_sheet"] == 3

    def test_get_status_not_found(self, app: TestClient) -> None:
        """Status endpoint returns 404 for missing job."""
        response = app.get("/api/jobs/nonexistent/status")
        assert response.status_code == 404

    async def test_get_status_lightweight(
        self,
        app: TestClient,
        state_backend: JsonStateBackend,
        sample_job: CheckpointState,
    ) -> None:
        """Status endpoint doesn't include heavy fields like sheets."""
        await state_backend.save(sample_job)

        response = app.get("/api/jobs/test-job-123/status")
        data = response.json()

        # Should NOT have these heavy fields
        assert "sheets" not in data
        assert "config_snapshot" not in data

        # Should have these lightweight fields
        assert "job_id" in data
        assert "status" in data
        assert "progress_percent" in data


# ============================================================================
# OpenAPI / Swagger Tests
# ============================================================================


class TestOpenAPI:
    """Tests for OpenAPI documentation."""

    def test_openapi_schema_accessible(self, app: TestClient) -> None:
        """OpenAPI schema is accessible."""
        response = app.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Mozart Dashboard"
        assert "paths" in schema

    def test_docs_accessible(self, app: TestClient) -> None:
        """Swagger UI docs are accessible."""
        response = app.get("/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower() or "openapi" in response.text.lower()


# ============================================================================
# App Factory Tests
# ============================================================================


class TestAppFactory:
    """Tests for create_app factory function."""

    def test_create_app_with_backend(self, state_backend: JsonStateBackend) -> None:
        """App can be created with explicit backend."""
        app = create_app(state_backend=state_backend)
        assert app is not None
        assert app.title == "Mozart Dashboard"

    def test_create_app_with_state_dir(self, temp_state_dir: Path) -> None:
        """App can be created with state directory."""
        app = create_app(state_dir=temp_state_dir)
        assert app is not None

    def test_create_app_custom_title(self, state_backend: JsonStateBackend) -> None:
        """App title can be customized."""
        app = create_app(state_backend=state_backend, title="Custom Dashboard")
        assert app.title == "Custom Dashboard"

    def test_create_app_cors_enabled(self, state_backend: JsonStateBackend) -> None:
        """CORS middleware is configured."""
        app = create_app(state_backend=state_backend)
        # Check middleware is present
        middleware_names = [m.cls.__name__ for m in app.user_middleware]
        assert "CORSMiddleware" in middleware_names
