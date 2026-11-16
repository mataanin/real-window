"""Browser layer.

Primary mode ("cdp"): attach with Playwright to the long-lived Chrome started by ../start_browser.sh on
127.0.0.1:9223. agentcookie's Linux sink injects the Mac's Chrome cookies into that same Chrome over CDP,
so the default context already carries the user's sessions.

Fallback mode ("launch"): launch our own Chrome (channel=chrome, headed under the current DISPLAY) with a
separate persistent profile and inject cookie files from cookies/ (see COOKIES.md).

Hosted mode ("browserbase"): create a Browserbase session (Verified browser, residential proxy, CAPTCHA
solving) on a persistent Context and attach over CDP; the session is released, and the Context saved,
on exit. See browserbase.py.
"""
from __future__ import annotations
import asyncio
import json
import os
import random
import subprocess
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

ROOT = Path(__file__).resolve().parent.parent
CDP_URL = os.environ.get("PL_CDP_URL", "http://127.0.0.1:9223")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/156.0.0.0 Safari/537.36")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
"""


def cdp_up(url: str = CDP_URL) -> bool:
    try:
        with urllib.request.urlopen(url + "/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


async def human_pause(lo: float = 0.6, hi: float = 1.8) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


SOLVER_EVENTS = {
    "browserbase-solving-started": "started",
    "browserbase-solving-finished": "finished",
    "browserbase-solving-errored": "errored",
}


class BrowserSession:
    def __init__(self, mode: str = "cdp", headed: bool = True, cookie_files: bool = False,
                 cookie_dir: Path | None = None, auto_start: bool = True,
                 bb_config=None, bb_purpose: str = "lookup", bb_timeout_s: int | None = None):
        self.mode, self.headed, self.cookie_files = mode, headed, cookie_files
        self.cookie_dir = cookie_dir or (ROOT / "cookies")
        self.auto_start = auto_start
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._own_pages: list[Page] = []
        self.bb_config = bb_config
        self.bb_purpose = bb_purpose
        self.bb_timeout_s = bb_timeout_s
        self.bb_client = None
        self.bb_session: dict | None = None
        self._solver: dict[int, str] = {}

    @property
    def solves_captchas(self) -> bool:
        return self.mode == "browserbase" and bool(self.bb_config and self.bb_config.solve_captchas)

    @property
    def session_url(self) -> str | None:
        if self.bb_session:
            from .browserbase import SESSION_DASHBOARD
            return SESSION_DASHBOARD.format(id=self.bb_session["id"])
        return None

    def _track_solver(self, page: Page) -> None:
        key = id(page)

        def on_console(msg):
            state = SOLVER_EVENTS.get(msg.text)
            if state:
                self._solver[key] = state
        page.on("console", on_console)

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        if self.mode == "browserbase":
            from .browserbase import BrowserbaseClient, BrowserbaseConfig, wait_for_context_settle
            self.bb_config = self.bb_config or BrowserbaseConfig.from_env()
            self.bb_client = BrowserbaseClient(self.bb_config)
            await wait_for_context_settle()
            self.bb_session = await asyncio.to_thread(self.bb_client.create_session, True, self.bb_purpose,
                                                    self.bb_timeout_s)
            try:
                self.browser = await self._pw.chromium.connect_over_cdp(self.bb_session["connectUrl"])
            except Exception:
                await asyncio.to_thread(self.bb_client.release, self.bb_session["id"])
                raise
            # The default context carries the Browserbase fingerprint, proxy and persisted Context.
            self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
            for p in self.context.pages:
                self._track_solver(p)
        elif self.mode == "cdp":
            if not cdp_up() and self.auto_start:
                env = dict(os.environ)
                if not self.headed:
                    env["PL_HEADLESS"] = "1"
                subprocess.run([str(ROOT / "start_browser.sh")], check=False, env=env)
            if not cdp_up():
                raise RuntimeError(f"No Chrome on {CDP_URL}; run ./start_browser.sh (or use --launch)")
            self.browser = await self._pw.chromium.connect_over_cdp(CDP_URL)
            # Use the existing default context: that's where agentcookie injects cookies.
            self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        else:
            profile = Path.home() / ".property-lookup" / "playwright-profile"
            profile.mkdir(parents=True, exist_ok=True)
            self.context = await self._pw.chromium.launch_persistent_context(
                str(profile), channel="chrome", headless=not self.headed,
                args=["--disable-blink-features=AutomationControlled", "--lang=en-US"],
                locale="en-US", timezone_id="America/New_York", viewport={"width": 1440, "height": 900},
                user_agent=UA)
        if self.mode == "launch":
            # Only for our own Playwright-launched Chrome. The CDP Chrome is started without automation
            # flags, so navigator.webdriver is already false and patching it would be a detectable tell.
            await self.context.add_init_script(STEALTH_JS)
        if self.cookie_files:
            from .cookies import load_cookie_dir
            cookies = load_cookie_dir(self.cookie_dir)
            if cookies:
                ok = 0
                for c in cookies:  # add one by one so one bad cookie doesn't sink the batch
                    try:
                        await self.context.add_cookies([c]); ok += 1
                    except Exception:
                        pass
                print(f"[browser] injected {ok}/{len(cookies)} cookies from {self.cookie_dir}")
        return self

    async def new_page(self) -> Page:
        page = await self.context.new_page()
        if self.mode == "browserbase":
            self._track_solver(page)
        elif self.mode == "cdp":
            # The shared Chrome keeps its own UA/locale; emulate NY timezone + en-US per page via CDP.
            try:
                s = await self.context.new_cdp_session(page)
                await s.send("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})
                await s.send("Emulation.setLocaleOverride", {"locale": "en-US"})
            except Exception:
                pass
        self._own_pages.append(page)
        return page

    async def reset_bot_cookies(self, domains: list[str]) -> int:
        """Drop Akamai bot-manager cookies (_abck, bm_*, ak_bmsc) for the given sites from the shared
        context, keeping login cookies. A flagged sensor cookie otherwise gets every request denied."""
        import re as _re
        pat = _re.compile(r"^(_abck|ak_bmsc|bm_[a-z]+)$")
        n = 0
        for c in await self.context.cookies([f"https://www.{d}/" for d in domains]):
            if pat.match(c["name"]):
                try:
                    await self.context.clear_cookies(name=c["name"], domain=c["domain"])
                    n += 1
                except Exception:
                    pass
        return n

    async def new_isolated_page(self) -> Page:
        """Page in a fresh cookie-less context (guest). Used to retry when the shared context's
        bot-manager cookies (e.g. Akamai _abck) have been flagged."""
        if self.mode == "browserbase":
            # Extra contexts in a Browserbase browser don't get the session's fingerprint/proxy setup
            # reliably, so retry in the main context with the bot-manager cookies already dropped.
            page = await self.new_page()
            return page
        if self.mode == "cdp":
            ctx = await self.browser.new_context(no_viewport=True)
        else:
            ctx = await self.context.browser.new_context() if self.context.browser else self.context
            await ctx.add_init_script(STEALTH_JS)
        page = await ctx.new_page()
        self._own_pages.append(page)
        self._iso_contexts = getattr(self, "_iso_contexts", []) + [ctx]
        return page

    async def __aexit__(self, *exc) -> None:
        for c in getattr(self, "_iso_contexts", []):
            try:
                await c.close()
            except Exception:
                pass
        for p in self._own_pages:
            try:
                await p.close()
            except Exception:
                pass
        try:
            if self.mode == "launch" and self.context:
                await self.context.close()
            elif self.mode == "browserbase" and self.browser:
                await self.browser.close()
            # In CDP mode never close the shared browser; just disconnect.
        except Exception:
            pass
        finally:
            if self.mode == "browserbase" and self.bb_session and self.bb_client:
                try:
                    await asyncio.to_thread(self.bb_client.release, self.bb_session["id"])
                except Exception as e:
                    print(f"[browserbase] release failed for {self.bb_session['id']}: {e}")
            if self._pw:
                await self._pw.stop()

    async def wait_for_captcha(self, page: Page, seconds: int | None = None) -> bool:
        """After a challenge is detected, give Browserbase's solver time to clear it.

        Returns True once the page no longer looks blocked. Gives up early if the solver never
        engages (unsupported challenge), or at the deadline."""
        limit = seconds if seconds is not None else (self.bb_config.captcha_wait_s if self.bb_config else 45)
        grace = min(10, limit)
        t0 = asyncio.get_event_loop().time()
        engaged = False
        while (elapsed := asyncio.get_event_loop().time() - t0) < limit:
            await asyncio.sleep(1)
            state = self._solver.get(id(page))
            if state == "started":
                engaged = True
                continue
            if not await detect_block(page):
                return True
            if not engaged and state is None and elapsed >= grace:
                return False
        return False

    async def add_cookies(self, cookies: list[dict]) -> int:
        ok = 0
        for c in cookies:  # one by one so one bad cookie doesn't sink the batch
            try:
                await self.context.add_cookies([c])
                ok += 1
            except Exception:
                pass
        return ok


# ---------- page helpers ----------
BLOCK_MARKERS = [
    ("Press & Hold", "PerimeterX press-and-hold captcha"),
    ("px-captcha", "PerimeterX captcha"),
    ("Access to this page has been denied", "PerimeterX block"),
    ("Access Denied", "Akamai 'Access Denied'"),
    ("You don't have permission to access", "Akamai 'Access Denied'"),
    ("Just a moment...", "Cloudflare challenge"),
    ("Verify you are human", "Cloudflare/Turnstile challenge"),
    ("Checking your browser", "bot challenge"),
    ("Pardon Our Interruption", "Imperva/Distil block"),
    ("Request unsuccessful. Incapsula", "Imperva block"),
    ("Your request could not be processed", "bot block"),
    ("are you a robot", "captcha"),
    ("unusual traffic", "bot block"),
]


async def detect_block(page: Page) -> str | None:
    try:
        title = await page.title()
        body = await page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 3000)")
    except Exception:
        return None
    hay = f"{title}\n{body}"
    for marker, reason in BLOCK_MARKERS:
        if marker.lower() in hay.lower():
            # avoid false positives on long, real pages that merely mention a phrase
            if len(body) > 2500 and marker not in ("px-captcha", "Press & Hold"):
                continue
            return reason
    return None


async def wait_for_human(page: Page, seconds: int) -> bool:
    """When run with --interactive, give a person watching the browser time to clear a challenge."""
    print(f"  [interactive] challenge on {page.url[:80]} - solve it in the browser window ({seconds}s)...")
    for _ in range(seconds):
        await asyncio.sleep(1)
        if not await detect_block(page):
            return True
    return False


async def goto(page: Page, url: str, wait: str = "domcontentloaded", timeout: int = 45000, settle: float = 2.5):
    resp = await page.goto(url, wait_until=wait, timeout=timeout)
    await asyncio.sleep(settle + random.uniform(0, 1.2))
    return resp


async def fetch_text(page: Page, url: str, method: str = "GET", body: dict | None = None,
                     headers: dict | None = None) -> tuple[int, str]:
    """fetch() from inside the page so the site's own cookies / bot tokens apply."""
    js = """async ([url, method, body, headers]) => {
        const opt = {method, headers: Object.assign({'accept': 'application/json, text/plain, */*'}, headers || {}), credentials: 'include'};
        if (body !== null) { opt.body = JSON.stringify(body); opt.headers['content-type'] = 'application/json'; }
        const r = await fetch(url, opt);
        return [r.status, await r.text()];
    }"""
    status, text = await page.evaluate(js, [url, method, body, headers])
    return status, text


async def fetch_json(page: Page, url: str, **kw):
    status, text = await fetch_text(page, url, **kw)
    t = text
    if t.startswith("{}&&"):
        t = t[4:]
    try:
        return status, json.loads(t)
    except Exception:
        return status, None


async def body_text(page: Page, limit: int = 60000) -> str:
    try:
        return await page.evaluate(f"() => (document.body ? document.body.innerText : '').slice(0, {limit})")
    except Exception:
        return ""


async def screenshot(page: Page, path: Path) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=False, timeout=15000)
        return str(path)
    except Exception:
        return None


async def check_cdp_connection(url: str = CDP_URL) -> bool:
    """Async version of cdp_up for use in the API."""
    return await asyncio.get_event_loop().run_in_executor(None, cdp_up, url)
