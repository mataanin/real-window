"""FastAPI application for property lookup service."""
from __future__ import annotations
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Depends, status, Query
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .models import (
    LookupRequest, LookupResponse, LookupStatus, LoginRequest, SiteInfo, SessionsResponse
)
from .queue import JobQueue
from .database import Database
from ..browser import check_cdp_connection
from ..browserbase import provider as browser_provider
from ..sites import ADAPTERS

security = HTTPBearer(auto_error=False)


async def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Verify bearer token if API_TOKEN is set."""
    api_token = os.getenv("API_TOKEN")
    if not api_token:
        return None
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required"
        )
    if credentials.credentials != api_token:
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
    
    app.state.db = Database(os.getenv("DB_PATH", "property_lookup.db"))
    app.state.queue = JobQueue(app.state.db)
    
    @app.get("/health")
    async def health():
        """Health check endpoint."""
        if browser_provider() == "browserbase":
            cdp = "browserbase"
        else:
            cdp = "reachable" if await check_cdp_connection() else "unreachable"
        return {
            "status": "healthy",
            "browser_provider": browser_provider(),
            "cdp_connection": cdp,
            "queue_size": app.state.queue.queue_size(),
            "active_jobs": app.state.queue.active_count()
        }
    
    @app.get("/sites", response_model=list[SiteInfo])
    async def list_sites(_: None = Depends(verify_token)):
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
        lookup_request: LookupRequest,
        _: None = Depends(verify_token)
    ):
        """Submit a new property lookup job."""
        job_id = await app.state.queue.submit_job(
            address=lookup_request.address,
            sites=lookup_request.sites,
            force_refresh=lookup_request.force_refresh
        )
        return {"job_id": job_id, "status": "queued"}
    
    @app.get("/lookups/{job_id}", response_model=LookupResponse)
    async def get_lookup(
        job_id: str,
        _: None = Depends(verify_token)
    ):
        """Get status and results of a lookup job."""
        result = await app.state.queue.get_job_status(job_id)
        if not result:
            raise HTTPException(status_code=404, detail="Job not found")
        return result
    
    @app.get("/lookups/{job_id}/report.md", response_class=PlainTextResponse)
    async def get_report_markdown(
        job_id: str,
        _: None = Depends(verify_token)
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
        _: None = Depends(verify_token)
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
    async def check_sessions(_: None = Depends(verify_token)):
        """Check login/block status for all sites."""
        sessions = await app.state.queue.check_sessions()
        return SessionsResponse(sessions=sessions)

    @app.get("/browser")
    async def browser_info(_: None = Depends(verify_token)):
        """Browser provider and effective (non-secret) Browserbase settings."""
        return app.state.queue.browser_info()

    @app.post("/browser/login")
    async def start_login(req: LoginRequest = LoginRequest(), _: None = Depends(verify_token)):
        """Open a Browserbase session on the persistent Context and return its live-view URL.

        Sign in to the sites in that window, then call POST /browser/login/finish to save the cookies.
        Lookups wait while a login session is open."""
        from .queue import BrowserBusy
        urls = req.urls or [ADAPTERS[s].home_url for s in (req.sites or []) if s in ADAPTERS and ADAPTERS[s].home_url]
        try:
            login = await app.state.queue.start_login(urls, minutes=req.minutes)
        except BrowserBusy as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {k: v for k, v in login.items() if not k.startswith("_")}

    @app.post("/browser/login/finish")
    async def finish_login(_: None = Depends(verify_token)):
        """Release the login session; Browserbase saves its cookies to the Context."""
        session_id = await app.state.queue.finish_login()
        if not session_id:
            raise HTTPException(status_code=404, detail="No login session is open")
        return {"released": session_id}

    @app.post("/browser/cookies")
    async def import_cookies(payload: Any = Body(...), all_domains: bool = Query(False),
                             _: None = Depends(verify_token)):
        """Import cookies exported from a desktop browser (Cookie-Editor / EditThisCookie JSON, a
        CDP cookie dump, or {"cookies": [...]}). By default only cookies for supported sites are kept."""
        from ..cookies import _from_json
        try:
            cookies = _from_json(payload)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Unrecognised cookie JSON: {e}")
        if not all_domains:
            allowed = site_cookie_domains()
            cookies = [c for c in cookies if cookie_domain_allowed(c["domain"], allowed)]
        if not cookies:
            raise HTTPException(status_code=400, detail="No usable cookies for supported sites")
        imported = await app.state.queue.import_cookies(cookies)
        return {"received": len(cookies), "imported": imported}

    return app


def site_cookie_domains() -> set[str]:
    from ..sites import ADAPTERS
    return {d.lower() for a in ADAPTERS.values() for d in a.domains}


def cookie_domain_allowed(domain: str, allowed: set[str]) -> bool:
    d = domain.lower().lstrip(".")
    return any(d == a or d.endswith("." + a) for a in allowed)


app = create_app()
