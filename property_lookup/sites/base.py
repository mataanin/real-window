from __future__ import annotations
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page

from ..address import Address
from ..browser import BrowserSession, detect_block, goto, screenshot, wait_for_human, body_text
from ..models import SiteResult, FOUND, NOT_FOUND, BLOCKED, LOGIN_REQUIRED, ERROR


class Blocked(Exception):
    pass


class LoginRequired(Exception):
    pass


@dataclass
class RunCtx:
    outdir: Path
    interactive: int = 0          # seconds to wait for a human to clear a challenge (0 = don't wait)
    debug: bool = False


class SiteAdapter:
    name: str = "base"
    label: str = "Base"
    domains: list[str] = []          # cookie domains this adapter needs
    home_url: str = ""
    # For --check-sessions: JS returning true when logged in (evaluated on home_url)
    # Logged out = a visible "Sign in"/"Log in" link near the top of the page (generic, works for all sites here)
    logged_in_js: str = r"""!/(^|\n)\s*(Sign in|Sign In|Log in|Log In|Login|Join \/ Sign in|Sign in \/ Register)\s*(\n|$)/.test(document.body.innerText.slice(0, 5000))"""
    has_login: bool = True
    login_hint: str = ""
    retry_isolated: bool = False      # on an Akamai-style block, retry once in a fresh guest context

    async def lookup(self, page: Page, addr: Address, res: SiteResult, ctx: RunCtx) -> None:
        raise NotImplementedError

    async def open(self, page: Page, url: str, ctx: RunCtx, settle: float = 2.5, wait: str = "domcontentloaded"):
        resp = await goto(page, url, settle=settle, wait=wait)
        reason = await detect_block(page)
        if reason and ctx.interactive:
            if await wait_for_human(page, ctx.interactive):
                reason = None
        if reason:
            raise Blocked(f"{reason} at {page.url[:120]}")
        return resp

    async def run(self, sess: BrowserSession, addr: Address, ctx: RunCtx) -> SiteResult:
        res = SiteResult(site=self.name)
        t0 = time.time()
        self.sess = sess
        page = await sess.new_page()
        try:
            try:
                await self.lookup(page, addr, res, ctx)
            except Blocked as e:
                if not self.retry_isolated:
                    raise
                res.notes.append(f"blocked in shared session ({e}); retried in a fresh guest context")
                await page.close()
                page = await sess.new_isolated_page()
                await self.lookup(page, addr, res, ctx)
            if res.status == FOUND and not res.fields and not res.description:
                res.notes.append("matched a page but extracted no fields")
        except Blocked as e:
            res.status = BLOCKED
            res.notes.append(str(e))
        except LoginRequired as e:
            res.status = LOGIN_REQUIRED
            res.notes.append(str(e))
        except Exception as e:
            res.status = ERROR
            res.notes.append(f"{type(e).__name__}: {str(e)[:300]}")
            if ctx.debug:
                res.notes.append(traceback.format_exc()[-1500:])
        finally:
            if res.status in (FOUND, BLOCKED, LOGIN_REQUIRED, ERROR):
                res.screenshot_path = await screenshot(page, ctx.outdir / "screenshots" / f"{self.name}.png")
            if res.status == FOUND and not res.raw_text_snippet:
                txt = await body_text(page, 1500)
                res.raw_text_snippet = txt[:1500] or None
            if res.status in (BLOCKED, LOGIN_REQUIRED) and not res.raw_text_snippet:
                res.raw_text_snippet = (await body_text(page, 600)) or None
            res.elapsed_s = round(time.time() - t0, 1)
            try:
                await page.close()
            except Exception:
                pass
        return res
