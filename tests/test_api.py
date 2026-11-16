"""Tests for the FastAPI application."""
import pytest
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from fastapi.testclient import TestClient
from property_lookup.api.app import create_app
from property_lookup.api.models import LookupStatus, SiteStatus
from property_lookup.models import SiteResult, FOUND, NOT_FOUND


@pytest.fixture
def client():
    """Create test client without authentication."""
    os.environ.pop("API_TOKEN", None)
    
    # Mock Database and JobQueue
    with patch("property_lookup.api.app.Database") as mock_db_class, \
         patch("property_lookup.api.app.JobQueue") as mock_queue_class:
        
        # Create mock instances
        mock_db = AsyncMock()
        mock_db.init = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db_class.return_value = mock_db
        
        mock_queue = AsyncMock()
        mock_queue.start = AsyncMock()
        mock_queue.stop = AsyncMock()
        mock_queue.queue_size = Mock(return_value=0)
        mock_queue.active_count = Mock(return_value=0)
        mock_queue.submit_job = AsyncMock(return_value="test-job-id")
        mock_queue.get_job_status = AsyncMock(return_value=None)
        mock_queue.check_sessions = AsyncMock(return_value=[])
        mock_queue_class.return_value = mock_queue
        
        app = create_app()
        app.state.mock_queue = mock_queue
        app.state.mock_db = mock_db
        
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client


@pytest.fixture
def auth_client():
    """Create test client with authentication."""
    os.environ["API_TOKEN"] = "test-token-12345"
    
    # Mock Database and JobQueue
    with patch("property_lookup.api.app.Database") as mock_db_class, \
         patch("property_lookup.api.app.JobQueue") as mock_queue_class:
        
        # Create mock instances
        mock_db = AsyncMock()
        mock_db.init = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db_class.return_value = mock_db
        
        mock_queue = AsyncMock()
        mock_queue.start = AsyncMock()
        mock_queue.stop = AsyncMock()
        mock_queue.queue_size = Mock(return_value=0)
        mock_queue.active_count = Mock(return_value=0)
        mock_queue.submit_job = AsyncMock(return_value="test-job-id")
        mock_queue.get_job_status = AsyncMock(return_value=None)
        mock_queue.check_sessions = AsyncMock(return_value=[])
        mock_queue_class.return_value = mock_queue
        
        app = create_app()
        app.state.mock_queue = mock_queue
        app.state.mock_db = mock_db
        
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    
    os.environ.pop("API_TOKEN", None)


class TestHealthEndpoint:
    """Test health check endpoint."""
    
    def test_health_check(self, client):
        """Test basic health check."""
        with patch("property_lookup.api.app.check_cdp_connection", return_value=True):
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
            assert "cdp_connection" in data
            assert "queue_size" in data


class TestSitesEndpoint:
    """Test sites listing endpoint."""
    
    def test_list_sites(self, client):
        """Test listing available sites."""
        response = client.get("/sites")
        assert response.status_code == 200
        sites = response.json()
        assert isinstance(sites, list)
        assert len(sites) > 0
        
        for site in sites:
            assert "name" in site
            assert "label" in site
            assert "description" in site


class TestAuthentication:
    """Test API authentication."""
    
    def test_no_auth_required_when_no_token(self, client):
        """Test endpoints work without auth when API_TOKEN not set."""
        response = client.get("/sites")
        assert response.status_code == 200
    
    def test_auth_required_when_token_set(self, auth_client):
        """Requests without a bearer token are rejected when API_TOKEN is set."""
        assert auth_client.get("/sites").status_code == 401

    def test_valid_token_accepted(self, auth_client):
        """Test valid bearer token is accepted."""
        r = auth_client.get("/sites", headers={"Authorization": "Bearer test-token-12345"})
        assert r.status_code == 200

    def test_invalid_token_rejected(self, auth_client):
        """Test invalid bearer token is rejected."""
        r = auth_client.get("/sites", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_health_needs_no_token(self, auth_client):
        with patch("property_lookup.api.app.check_cdp_connection", return_value=True):
            assert auth_client.get("/health").status_code == 200

    def test_body_endpoint_with_token(self, auth_client):
        r = auth_client.post("/lookups", json={"address": "48 Amsterdam Ave, Bridgeport, CT 06606"},
                             headers={"Authorization": "Bearer test-token-12345"})
        assert r.status_code == 202


def _job(status, report_md=None):
    from property_lookup.api.models import LookupResponse
    return LookupResponse(job_id="test-job-id", status=status, address="123 Main St", report_md=report_md)


class TestLookupEndpoints:
    """Test lookup creation and retrieval (mocked)."""

    def test_create_lookup(self, client):
        """Test creating a lookup job."""
        response = client.post("/lookups", json={"address": "48 Amsterdam Ave, Bridgeport, CT 06606"})
        assert response.status_code == 202, f"Failed: {response.text}"
        assert response.json() == {"job_id": "test-job-id", "status": "queued"}
        client.app.state.mock_queue.submit_job.assert_awaited_once_with(
            address="48 Amsterdam Ave, Bridgeport, CT 06606", sites=None, force_refresh=False)

    def test_create_lookup_with_sites(self, client):
        """Test creating a lookup with specific sites."""
        response = client.post("/lookups", json={
            "address": "48 Amsterdam Ave, Bridgeport, CT 06606",
            "sites": ["zillow", "redfin"],
            "force_refresh": True,
        })
        assert response.status_code == 202
        client.app.state.mock_queue.submit_job.assert_awaited_once_with(
            address="48 Amsterdam Ave, Bridgeport, CT 06606", sites=["zillow", "redfin"], force_refresh=True)

    def test_create_lookup_requires_address(self, client):
        assert client.post("/lookups", json={}).status_code == 422

    def test_get_lookup_status(self, client):
        """Test retrieving lookup status."""
        client.app.state.mock_queue.get_job_status = AsyncMock(return_value=_job(LookupStatus.RUNNING))
        response = client.get("/lookups/test-job-id")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-job-id"
        assert data["status"] == "running"

    def test_get_nonexistent_lookup(self, client):
        """Test retrieving non-existent lookup."""
        response = client.get("/lookups/nonexistent-id")
        assert response.status_code == 404


class TestSyncLookupEndpoint:
    """Test synchronous lookup endpoint."""

    def test_sync_lookup_no_wait(self, client):
        """Test sync lookup without waiting."""
        response = client.get("/lookup", params={"address": "123 Main St, Bridgeport, CT", "wait": "false"})
        assert response.status_code == 200
        assert response.json() == {"job_id": "test-job-id", "status": "queued"}

    def test_sync_lookup_with_sites(self, client):
        """Test sync lookup with specific sites."""
        response = client.get("/lookup", params={
            "address": "123 Main St, Bridgeport, CT", "sites": "zillow, redfin", "wait": "false"})
        assert response.status_code == 200
        kwargs = client.app.state.mock_queue.submit_job.call_args.kwargs
        assert kwargs["sites"] == ["zillow", "redfin"]

    def test_sync_lookup_wait_returns_result(self, client):
        client.app.state.mock_queue.get_job_status = AsyncMock(return_value=_job(LookupStatus.DONE, "# R"))
        response = client.get("/lookup", params={"address": "123 Main St, Bridgeport, CT", "wait": "true"})
        assert response.status_code == 200
        assert response.json()["status"] == "done"


class TestReportEndpoint:
    """Test markdown report endpoint."""

    def test_get_report_not_done(self, client):
        """Test getting report for incomplete job."""
        client.app.state.mock_queue.get_job_status = AsyncMock(return_value=_job(LookupStatus.QUEUED))
        response = client.get("/lookups/test-job-id/report.md")
        assert response.status_code == 400

    def test_get_report_done(self, client):
        client.app.state.mock_queue.get_job_status = AsyncMock(return_value=_job(LookupStatus.DONE, "# Report"))
        response = client.get("/lookups/test-job-id/report.md")
        assert response.status_code == 200
        assert response.text == "# Report"


class TestSessionsEndpoint:
    """Test sessions check endpoint."""
    
    def test_check_sessions(self, client):
        """Test checking site sessions."""
        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)


@pytest.mark.asyncio
async def test_database_operations():
    """Test database create, update, and retrieve operations."""
    from property_lookup.api.database import Database
    
    db = Database(":memory:")
    await db.init()
    
    await db.create_job(
        job_id="test-123",
        address="123 Main St",
        sites=["zillow", "redfin"],
        status=LookupStatus.QUEUED
    )
    
    job = await db.get_job("test-123")
    assert job is not None
    assert job["address"] == "123 Main St"
    assert job["status"] == LookupStatus.QUEUED
    
    await db.update_job("test-123", status=LookupStatus.RUNNING)
    job = await db.get_job("test-123")
    assert job["status"] == LookupStatus.RUNNING
    
    await db.close()


@pytest.mark.asyncio
async def test_cache_operations():
    """Test result caching operations."""
    from property_lookup.api.database import Database
    
    db = Database(":memory:")
    await db.init()
    
    await db.cache_result(
        address_slug="123-main-st-bridgeport-ct",
        result='{"test": "data"}',
        report_md="# Test Report"
    )
    
    cached = await db.get_cached_result("123-main-st-bridgeport-ct")
    assert cached is not None
    assert cached["result"] == '{"test": "data"}'
    assert cached["report_md"] == "# Test Report"
    
    await db.cleanup_old_cache(ttl_hours=0)
    cached = await db.get_cached_result("123-main-st-bridgeport-ct")
    assert cached is None
    
    await db.close()
