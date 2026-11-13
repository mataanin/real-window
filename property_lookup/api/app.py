"""FastAPI application for property lookup service."""
from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Security, status, Query
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .models import (
    LookupRequest, LookupResponse, LookupStatus, SiteInfo, SessionsResponse
)
from .queue import JobQueue
from .database import Database
from ..browser import check_cdp_connection

API_TOKEN = os.getenv("API_TOKEN")
security = HTTPBearer(auto_error=False) if API_TOKEN else None


async def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)):
    """Verify bearer token if API_TOKEN is set."""
    if not API_TOKEN:
        return None
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required"
        )
    if credentials.credentials != API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )
    return credentials.credentials


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup and cleanup on shutdown."""
    await app.state.db.init()
    await app.state.queue.start()
    yield
    await app.state.queue.stop()
    await app.state.db.close()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="Property Lookup API",
        description="API for looking up US property information across multiple listing sites",
        version="1.0.0",
        lifespan=lifespan
    )
    
    app.state.db = Database()
    app.state.queue = JobQueue(app.state.db)
    
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        cdp_reachable = await check_cdp_connection()
        return {
            "status": "healthy",
            "cdp_connection": "reachable" if cdp_reachable else "unreachable",
            "queue_size": app.state.queue.queue_size(),
            "active_jobs": app.state.queue.active_count()
        }
    
    @app.get("/sites", response_model=list[SiteInfo])
    async def list_sites(_: None = Security(verify_token)):
        """List all supported lookup sites."""
        from ..sites import ADAPTERS
        return [
            SiteInfo(
                name=name,
                label=adapter.label,
                description=f"Lookup on {adapter.label}"
            )
            for name, adapter in ADAPTERS.items()
        ]
    
    @app.post("/lookups", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
    async def create_lookup(
        request: LookupRequest,
        _: None = Security(verify_token)
    ):
        """Submit a new property lookup job."""
        job_id = await app.state.queue.submit_job(
            address=request.address,
            sites=request.sites,
            force_refresh=request.force_refresh
        )
        return {"job_id": job_id, "status": "queued"}
    
    @app.get("/lookups/{job_id}", response_model=LookupResponse)
    async def get_lookup(
        job_id: str,
        _: None = Security(verify_token)
    ):
        """Get status and results of a lookup job."""
        result = await app.state.queue.get_job_status(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return result
    
    @app.get("/lookups/{job_id}/report.md", response_class=PlainTextResponse)
    async def get_report_markdown(
        job_id: str,
        _: None = Security(verify_token)
    ):
        """Get the markdown report for a completed lookup."""
        result = await app.state.queue.get_job_status(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        
        if result.status != LookupStatus.DONE:
            raise HTTPException(
                status_code=400,
                detail=f"Report not available, job status: {result.status}"
            )
        
        if not result.report_md:
            raise HTTPException(status_code=404, detail="Markdown report not found")
        
        return result.report_md
    
    @app.get("/lookup")
    async def sync_lookup(
        address: str = Query(..., description="Property address to lookup"),
        sites: Optional[str] = Query(None, description="Comma-separated list of sites"),
        wait: bool = Query(False, description="Wait for completion"),
        timeout: int = Query(300, description="Max wait time in seconds"),
        _: None = Security(verify_token)
    ):
        """Convenience endpoint for synchronous lookups."""
        site_list = [s.strip() for s in sites.split(",")] if sites else None
        
        job_id = await app.state.queue.submit_job(
            address=address,
            sites=site_list,
            force_refresh=False
        )
        
        if not wait:
            return {"job_id": job_id, "status": "queued"}
        
        start_time = asyncio.get_event_loop().time()
        while True:
            result = await app.state.queue.get_job_status(job_id)
            if result and result.status in (LookupStatus.DONE, LookupStatus.FAILED):
                return result.model_dump()
            
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                result = await app.state.queue.get_job_status(job_id)
                return result.model_dump() if result else {"job_id": job_id, "status": "timeout"}
            
            await asyncio.sleep(1)
    
    @app.get("/sessions", response_model=SessionsResponse)
    async def check_sessions(_: None = Security(verify_token)):
        """Check login/block status for all sites."""
        sessions = await app.state.queue.check_sessions()
        return SessionsResponse(sessions=sessions)
    
    return app


app = create_app()
