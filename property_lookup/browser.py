"""Browser layer.

Primary mode ("cdp"): attach with Playwright to the long-lived Chrome started by ../start_browser.sh on
127.0.0.1:9223. agentcookie's Linux sink injects the Mac's Chrome cookies into that same Chrome over CDP,
so the default context already carries the user's sessions.

Fallback mode ("launch"): launch our own Chrome (channel=chrome, headed under the current DISPLAY) with a
separate persistent profile and inject cookie files from cookies/ (see COOKIES.md).
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


class BrowserSession:
    def __init__(self, mode: str = "cdp", headed: bool = True, cookie_files: bool = False,
                 cookie_dir: Path | None = None, auto_start: bool = True):
        self.mode, self.headed, self.cookie_files = mode, headed, cookie_files
        self.cookie_dir = cookie_dir or (ROOT / "cookies")
        self.auto_start = auto_start
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._own_pages: list[Page] = []

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        if self.mode == "cdp":
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
        if self.mode != "cdp":
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
        if self.mode == "cdp":
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
            if self.mode != "cdp" and self.context:
                await self.context.close()
            # In CDP mode never close the shared browser; just disconnect.
        finally:
            if self._pw:
                await self._pw.stop()


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
