"""Tests for the FastAPI application."""
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient
from property_lookup.api.app import create_app
from property_lookup.api.models import LookupStatus, SiteStatus
from property_lookup.models import SiteResult, FOUND, NOT_FOUND


@pytest.fixture
async def client():
    """Create test client."""
    os.environ.pop("API_TOKEN", None)
    app = create_app()
    
    await app.state.db.init()
    await app.state.queue.start()
    
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    
    await app.state.queue.stop()
    await app.state.db.close()


@pytest.fixture
async def auth_client():
    """Create test client with authentication."""
    os.environ["API_TOKEN"] = "test-token-12345"
    app = create_app()
    
    await app.state.db.init()
    await app.state.queue.start()
    
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    
    await app.state.queue.stop()
    await app.state.db.close()
    os.environ.pop("API_TOKEN", None)


class TestHealthEndpoint:
    """Test health check endpoint."""
    
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """Test basic health check."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "cdp_connection" in data
        assert "queue_size" in data


class TestSitesEndpoint:
    """Test sites listing endpoint."""
    
    @pytest.mark.asyncio
    async def test_list_sites(self, client):
        """Test listing available sites."""
        response = await client.get("/sites")
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
    
    @pytest.mark.asyncio
    async def test_no_auth_required_when_no_token(self, client):
        """Test endpoints work without auth when API_TOKEN not set."""
        response = await client.get("/sites")
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_auth_required_when_token_set(self, auth_client):
        """Test authentication required when API_TOKEN is set."""
        response = await auth_client.get("/sites")
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_valid_token_accepted(self, auth_client):
        """Test valid bearer token is accepted."""
        response = await auth_client.get(
            "/sites",
            headers={"Authorization": "Bearer test-token-12345"}
        )
        assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, auth_client):
        """Test invalid bearer token is rejected."""
        response = await auth_client.get(
            "/sites",
            headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401


class TestLookupEndpoints:
    """Test lookup creation and retrieval."""
    
    @pytest.mark.asyncio
    async def test_create_lookup(self, client):
        """Test creating a lookup job."""
        response = await client.post(
            "/lookups",
            json={"address": "48 Amsterdam Ave, Bridgeport, CT 06606"}
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"
    
    @pytest.mark.asyncio
    async def test_create_lookup_with_sites(self, client):
        """Test creating a lookup with specific sites."""
        response = await client.post(
            "/lookups",
            json={
                "address": "48 Amsterdam Ave, Bridgeport, CT 06606",
                "sites": ["zillow", "redfin"]
            }
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
    
    @pytest.mark.asyncio
    async def test_get_lookup_status(self, client):
        """Test retrieving lookup status."""
        create_response = await client.post(
            "/lookups",
            json={"address": "123 Main St, Bridgeport, CT"}
        )
        job_id = create_response.json()["job_id"]
        
        response = await client.get(f"/lookups/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert "status" in data
        assert data["status"] in ["queued", "running", "done", "failed"]
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_lookup(self, client):
        """Test retrieving non-existent lookup."""
        response = await client.get("/lookups/nonexistent-id")
        assert response.status_code == 404


class TestSyncLookupEndpoint:
    """Test synchronous lookup endpoint."""
    
    @pytest.mark.asyncio
    async def test_sync_lookup_no_wait(self, client):
        """Test sync lookup without waiting."""
        response = await client.get(
            "/lookup",
            params={"address": "123 Main St, Bridgeport, CT", "wait": "false"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"
    
    @pytest.mark.asyncio
    async def test_sync_lookup_with_sites(self, client):
        """Test sync lookup with specific sites."""
        response = await client.get(
            "/lookup",
            params={
                "address": "123 Main St, Bridgeport, CT",
                "sites": "zillow,redfin",
                "wait": "false"
            }
        )
        assert response.status_code == 200


class TestReportEndpoint:
    """Test markdown report endpoint."""
    
    @pytest.mark.asyncio
    @patch("property_lookup.api.queue.JobQueue._process_job")
    async def test_get_report_not_done(self, mock_process, client):
        """Test getting report for incomplete job."""
        response = await client.post(
            "/lookups",
            json={"address": "123 Main St, Bridgeport, CT"}
        )
        job_id = response.json()["job_id"]
        
        response = await client.get(f"/lookups/{job_id}/report.md")
        assert response.status_code == 400


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
