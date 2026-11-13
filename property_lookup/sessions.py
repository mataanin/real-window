"""--check-sessions: open each site's home page in the shared Chrome and report login/blocked state."""
from __future__ import annotations

from .browser import BrowserSession, goto, detect_block
from .sites import ADAPTERS


async def check_sessions(sess: BrowserSession, names: list[str]) -> list[dict]:
    rows = []
    for n in names:
        a = ADAPTERS[n]
        if not a.home_url or not a.domains or not a.has_login:
            rows.append({"site": n, "state": "n/a (public, no login)", "cookies": 0})
            continue
        cookies = await sess.context.cookies([f"https://www.{d}/" if d.count(".") == 1 else f"https://{d}/" for d in a.domains])
        page = await sess.new_page()
        state, detail = "unknown", ""
        try:
            await goto(page, a.home_url, settle=3)
            blk = await detect_block(page)
            if blk:
                state, detail = "blocked", blk
            else:
                logged = await page.evaluate(f"() => {{ try {{ return !!({a.logged_in_js}); }} catch (e) {{ return false; }} }}")
                state = "logged in" if logged else "guest (not logged in)"
        except Exception as e:
            state, detail = "error", str(e)[:120]
        finally:
            await page.close()
        rows.append({"site": n, "state": state, "detail": detail, "cookies": len(cookies),
                     "hint": a.login_hint})
    return rows
