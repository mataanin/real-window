"""Job queue for managing property lookups."""
from __future__ import annotations
import asyncio
import datetime as dt
import json
import uuid
from typing import Optional

from .models import LookupStatus, LookupResponse, SiteProgress, SiteStatus, SessionInfo
from .database import Database
from ..address import parse_address, geocode
from ..browser import BrowserSession
from ..merge import write_report
from ..sites import ADAPTERS


class JobQueue:
    """Queue for managing property lookup jobs with single-worker execution."""
    
    def __init__(self, db: Database, timeout: int = 150):
        self.db = db
        self.timeout = timeout
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_job: Optional[str] = None
        self.worker_task: Optional[asyncio.Task] = None
        self.running = False
        self._browser_session: Optional[BrowserSession] = None
    
    async def start(self):
        """Start the worker task."""
        if self.running:
            return
        self.running = True
        self.worker_task = asyncio.create_task(self._worker())
    
    async def stop(self):
        """Stop the worker task."""
        self.running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        if self._browser_session:
            await self._browser_session.__aexit__(None, None, None)
    
    def queue_size(self) -> int:
        """Return the number of queued jobs."""
        return self.queue.qsize()
    
    def active_count(self) -> int:
        """Return 1 if a job is active, 0 otherwise."""
        return 1 if self.active_job else 0
    
    async def submit_job(
        self,
        address: str,
        sites: Optional[list[str]] = None,
        force_refresh: bool = False
    ) -> str:
        """Submit a new lookup job and return its ID."""
        job_id = str(uuid.uuid4())
        
        await self.db.create_job(
            job_id=job_id,
            address=address,
            sites=sites,
            status=LookupStatus.QUEUED
        )
        
        await self.queue.put({
            "job_id": job_id,
            "address": address,
            "sites": sites,
            "force_refresh": force_refresh
        })
        
        return job_id
    
    async def get_job_status(self, job_id: str) -> Optional[LookupResponse]:
        """Get the current status and results of a job."""
        job = await self.db.get_job(job_id)
        if not job:
            return None
        
        return LookupResponse(
            job_id=job_id,
            status=LookupStatus(job["status"]),
            address=job.get("address"),
            progress=[
                SiteProgress(**p) for p in json.loads(job.get("progress") or "[]")
            ],
            result=json.loads(job["result"]) if job.get("result") else None,
            report_md=job.get("report_md"),
            error=job.get("error"),
            created_at=job.get("created_at"),
            completed_at=job.get("completed_at")
        )
    
    async def check_sessions(self) -> list[SessionInfo]:
        """Check login/block status for all sites."""
        from ..sessions import check_sessions as do_check_sessions
        
        browser = await self._get_browser()
        site_names = list(ADAPTERS.keys())
        rows = await do_check_sessions(browser, site_names)
        
        return [
            SessionInfo(
                site=r["site"],
                state=r["state"],
                cookies=r["cookies"],
                detail=r.get("detail"),
                hint=r.get("hint")
            )
            for r in rows
        ]
    
    async def _get_browser(self) -> BrowserSession:
        """Get or create the shared browser session."""
        if not self._browser_session:
            self._browser_session = BrowserSession(
                mode="cdp",
                headed=True,
                cookie_files=False
            )
            await self._browser_session.__aenter__()
        return self._browser_session
    
    async def _worker(self):
        """Worker that processes jobs one at a time."""
        while self.running:
            try:
                job = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            
            self.active_job = job["job_id"]
            try:
                await self._process_job(job)
            except Exception as e:
                await self.db.update_job(
                    job_id=job["job_id"],
                    status=LookupStatus.FAILED,
                    error=str(e)
                )
            finally:
                self.active_job = None
                self.queue.task_done()
    
    async def _process_job(self, job: dict):
        """Process a single lookup job."""
        job_id = job["job_id"]
        address_str = job["address"]
        requested_sites = job["sites"]
        force_refresh = job["force_refresh"]
        
        await self.db.update_job(job_id, status=LookupStatus.RUNNING)
        
        addr = geocode(parse_address(address_str))
        
        if not force_refresh:
            cached = await self.db.get_cached_result(addr.slug)
            if cached:
                await self.db.update_job(
                    job_id=job_id,
                    status=LookupStatus.DONE,
                    result=cached["result"],
                    report_md=cached.get("report_md"),
                    progress=cached.get("progress", "[]")
                )
                return
        
        site_names = requested_sites if requested_sites else list(ADAPTERS.keys())
        
        bad_sites = [s for s in site_names if s not in ADAPTERS]
        if bad_sites:
            await self.db.update_job(
                job_id=job_id,
                status=LookupStatus.FAILED,
                error=f"Unknown sites: {bad_sites}"
            )
            return
        
        progress = [
            SiteProgress(site=name, status=SiteStatus.PENDING)
            for name in site_names
        ]
        await self.db.update_job(
            job_id=job_id,
            progress=json.dumps([p.model_dump() for p in progress])
        )
        
        browser = await self._get_browser()
        
        from ..sites.base import RunCtx
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / addr.slug
            outdir.mkdir(parents=True, exist_ok=True)
            ctx = RunCtx(outdir=outdir, interactive=0, debug=False)
            
            results = []
            for idx, name in enumerate(site_names):
                adapter = ADAPTERS[name]
                progress[idx].status = SiteStatus.RUNNING
                await self.db.update_job(
                    job_id=job_id,
                    progress=json.dumps([p.model_dump() for p in progress])
                )
                
                try:
                    result = await asyncio.wait_for(
                        adapter.run(browser, addr, ctx),
                        timeout=self.timeout
                    )
                    results.append(result)
                    
                    progress[idx].status = SiteStatus(result.status)
                    progress[idx].url = result.url
                    progress[idx].notes = result.notes
                    
                except asyncio.TimeoutError:
                    from ..models import SiteResult, ERROR
                    result = SiteResult(
                        site=name,
                        status=ERROR,
                        notes=[f"timed out after {self.timeout}s"]
                    )
                    results.append(result)
                    progress[idx].status = SiteStatus.ERROR
                    progress[idx].notes = [f"timed out after {self.timeout}s"]
                
                await self.db.update_job(
                    job_id=job_id,
                    progress=json.dumps([p.model_dump() for p in progress])
                )
            
            labels = {n: ADAPTERS[n].label for n in ADAPTERS}
            md_path, json_path = write_report(addr, results, outdir, labels)
            
            with open(json_path, "r") as f:
                result_json = json.load(f)
            
            with open(md_path, "r") as f:
                report_md = f.read()
            
            await self.db.cache_result(
                address_slug=addr.slug,
                result=json.dumps(result_json),
                report_md=report_md,
                progress=json.dumps([p.model_dump() for p in progress])
            )
            
            await self.db.update_job(
                job_id=job_id,
                status=LookupStatus.DONE,
                result=json.dumps(result_json),
                report_md=report_md,
                progress=json.dumps([p.model_dump() for p in progress])
            )
