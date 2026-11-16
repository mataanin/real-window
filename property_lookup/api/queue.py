"""Job queue for managing property lookups."""
from __future__ import annotations
import asyncio
import datetime as dt
import json
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from .models import LookupStatus, LookupResponse, SiteProgress, SiteStatus, SessionInfo
from .database import Database
from ..address import parse_address, geocode
from ..browser import BrowserSession
from ..browserbase import provider as browser_provider
from ..merge import write_report
from ..sites import ADAPTERS


class BrowserBusy(RuntimeError):
    pass


class JobQueue:
    """Queue for managing property lookup jobs with single-worker execution."""
    
    def __init__(self, db: Database, timeout: int = 150):
        self.db = db
        self.timeout = timeout
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active_job: Optional[str] = None
        self.worker_task: Optional[asyncio.Task] = None
        self.running = False
        self.provider = browser_provider()
        self._browser_session: Optional[BrowserSession] = None
        self._bb_config = None
        # One Browserbase session at a time on the shared Context; parallel sessions would race
        # to persist cookies and can get logins revoked.
        self._browser_lock = asyncio.Lock()
        self._login: Optional[dict] = None

    @property
    def bb_config(self):
        if self._bb_config is None and self.provider == "browserbase":
            from ..browserbase import BrowserbaseConfig
            self._bb_config = BrowserbaseConfig.from_env()
        return self._bb_config

    def browser_info(self) -> dict:
        info: dict = {"provider": self.provider}
        if self.provider == "browserbase":
            try:
                info["browserbase"] = self.bb_config.summary()
            except Exception as e:
                info["error"] = str(e)
            if self._login:
                info["login_session"] = {k: self._login[k] for k in ("session_id", "live_view_url", "expires_at")}
        return info
    
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
        if self._login:
            await self.finish_login()
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
        
        site_names = list(ADAPTERS.keys())
        async with self._browser("sessions") as browser:
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

    @asynccontextmanager
    async def _browser(self, purpose: str = "lookup", wait: bool = True):
        """Shared local Chrome, or a fresh Browserbase session released (and its Context saved) on exit."""
        if self.provider != "browserbase":
            yield await self._get_browser()
            return
        if not wait and self._browser_lock.locked():
            raise BrowserBusy("browser is busy (a lookup or login session is running)")
        async with self._browser_lock:
            async with BrowserSession(mode="browserbase", bb_config=self.bb_config, bb_purpose=purpose) as s:
                yield s

    async def import_cookies(self, cookies: list[dict]) -> int:
        """Add cookies to the browser profile (persisted to the Browserbase Context on release)."""
        async with self._browser("cookie-import") as browser:
            return await browser.add_cookies(cookies)

    async def start_login(self, urls: list[str], minutes: int = 15) -> dict:
        """Open a Browserbase session on the shared Context and return its live-view URL so a person can
        sign in to sites. Lookups wait until finish_login() releases it (or it times out)."""
        if self.provider != "browserbase":
            raise RuntimeError("login sessions need BROWSER_PROVIDER=browserbase")
        if self._login:
            return self._login
        try:
            await asyncio.wait_for(self._browser_lock.acquire(), timeout=5)
        except asyncio.TimeoutError:
            raise BrowserBusy("a lookup is running; try again when the queue is idle")
        try:
            sess = BrowserSession(mode="browserbase", bb_config=self.bb_config, bb_purpose="login",
                                  bb_timeout_s=minutes * 60 + 120)
            await sess.__aenter__()
            for url in urls:
                page = await sess.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass
            live = await asyncio.to_thread(sess.bb_client.live_urls, sess.bb_session["id"])
        except Exception:
            self._browser_lock.release()
            raise
        expires = dt.datetime.utcnow() + dt.timedelta(minutes=minutes)
        self._login = {
            "session_id": sess.bb_session["id"],
            "live_view_url": live.get("debuggerFullscreenUrl"),
            "pages": [{"url": p.get("url"), "live_view_url": p.get("debuggerFullscreenUrl")}
                      for p in live.get("pages", [])],
            "replay_url": sess.session_url,
            "expires_at": expires.isoformat() + "Z",
            "_sess": sess,
            "_timer": asyncio.create_task(self._login_timeout(minutes * 60)),
        }
        return self._login

    async def _login_timeout(self, seconds: int):
        await asyncio.sleep(seconds)
        await self.finish_login(_from_timer=True)

    async def finish_login(self, _from_timer: bool = False) -> Optional[str]:
        """Release the login session so Browserbase saves its cookies to the Context."""
        login, self._login = self._login, None
        if not login:
            return None
        if not _from_timer:
            login["_timer"].cancel()
        try:
            await login["_sess"].__aexit__(None, None, None)
        finally:
            self._browser_lock.release()
        return login["session_id"]
    
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
        
        async with self._browser("lookup") as browser:
            await self._run_sites(job_id, addr, site_names, progress, browser)

    async def _run_sites(self, job_id, addr, site_names, progress, browser):
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
