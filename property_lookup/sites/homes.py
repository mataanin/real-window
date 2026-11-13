from __future__ import annotations
import re

from ..address import Address, match_score, has_unit
from ..browser import human_pause, body_text
from ..extract import jsonld, facts_from_text, money, intish, strip_html
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx, Blocked

SUGG_SEL = "#autocomplete-container button.suggestion-btn, [role=option], .suggestion-item"
SUGG_JS = """() => [...document.querySelectorAll('#autocomplete-container button.suggestion-btn, [role=option], .suggestion-item')]
  .filter(e => e.offsetParent !== null).slice(0, 12).map((e, i) => [i, e.innerText.replace(/\\n/g, ' ').trim()])"""


class HomesCom(SiteAdapter):
    name, label = "homes", "Homes.com"
    domains = ["homes.com"]
    home_url = "https://www.homes.com/"
    retry_isolated = True
    login_hint = "CoStar Akamai bot manager; blocks are per IP/session and intermittent"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        n = await self.sess.reset_bot_cookies(["homes.com"])
        if n:
            res.notes.append(f"reset {n} Akamai bot cookies (login cookies kept)")
        await self.open(page, self.home_url, ctx)
        inp = page.locator("input.multiselect-search, input[placeholder*='Address']").first
        await inp.click(timeout=10000)
        await inp.type(f"{addr.line1}, {addr.search_city}, {addr.state}", delay=65)
        await page.wait_for_timeout(3500)
        sugg = await page.evaluate(SUGG_JS)
        best, best_s = None, 0
        for i, txt in sugg:
            if "address" not in txt.lower() and not re.match(r"^\d", txt):
                continue
            s = match_score(addr, txt.replace(" Address", ""))
            if has_unit(txt) and not addr.unit:
                s -= 15
            if s > best_s:
                best, best_s = (i, txt), s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append("no matching address in Homes.com autocomplete" + (f" (suggestions: {'; '.join(t for _, t in sugg[:4])})" if sugg else ""))
            return
        opts = page.locator(SUGG_SEL)
        visible = [o for o in await opts.all() if await o.is_visible()]
        await visible[best[0]].click()
        try:
            await page.wait_for_url(re.compile(r"/property/"), timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        if "/property/" not in page.url:
            # some addresses open a search page; take the first placard whose address matches
            links = await page.evaluate("() => [...new Set([...document.querySelectorAll('a[href*=\"/property/\"]')].map(a => [a.href, a.innerText.replace(/\\n/g,' ')]))]")
            hit = next((h for h, t in links if match_score(addr, t) >= 85 or match_score(addr, h.split('/property/')[1].replace('-', ' ')) >= 85), None)
            if not hit:
                res.status = NOT_FOUND
                res.notes.append(f"Homes.com autocomplete led to {page.url[:100]} without a property page")
                return
            await self.open(page, hit, ctx)
        from ..browser import detect_block
        if await detect_block(page):
            raise Blocked("Homes.com challenge on property page")
        res.status, res.url = FOUND, page.url
        res.matched_address = best[1].replace(" Address", "")
        for d in await jsonld(page):
            if not isinstance(d, dict):
                continue
            if d.get("url") and d["url"].split("?")[0].rstrip("/") != page.url.split("?")[0].rstrip("/"):
                continue   # similar/nearby homes carousel
            types = d.get("@type") if isinstance(d.get("@type"), list) else [d.get("@type")]
            if any(t in ("RealEstateListing", "Product", "SingleFamilyResidence", "Residence", "House", "Apartment") for t in types):
                off = d.get("offers") if isinstance(d.get("offers"), dict) else {}
                res.set("list_price", money(off.get("price")))
                ob = off.get("offeredBy") if isinstance(off.get("offeredBy"), dict) else {}
                org = (ob.get("memberOf") or {}).get("name") if isinstance(ob.get("memberOf"), dict) else None
                res.set("broker", " - ".join(x for x in (ob.get("name"), org) if x))
                res.set("broker_contact", ob.get("telephone"))
                me = d.get("mainEntity") if isinstance(d.get("mainEntity"), dict) else d
                res.set("beds", intish(me.get("numberOfBedrooms") or me.get("numberOfRooms")))
                res.set("baths", intish(me.get("numberOfBathroomsTotal")))
                fs = me.get("floorSize") or {}
                res.set("building_sf", intish(fs.get("value")) if isinstance(fs, dict) else None)
                res.set("year_built", me.get("yearBuilt"))
                if d.get("description") and not res.description:
                    res.description = strip_html(d["description"])
                if isinstance(d.get("image"), list):
                    res.set("photo_count", len(d["image"]))
                if off.get("availability"):
                    res.set("status", "active" if "InStock" in off["availability"] else off["availability"].split("/")[-1])
        text = await body_text(page)
        self._parse_text(text, res)
        res.raw_text_snippet = text[:1500]

    def _parse_text(self, text: str, res: SiteResult):
        head = text[:1500]
        sm = re.search(r"\n(NOT LISTED FOR SALE|FOR SALE|PENDING|UNDER CONTRACT|CONTINGENT|SOLD[^\n]*|OFF MARKET|FOR RENT|COMING SOON|AUCTION)\n", head)
        if sm:
            res.set("status", sm.group(1).lower())
        if sm and "not listed" not in sm.group(1).lower() and "sold" not in sm.group(1).lower():
            pm = re.search(r"\n\$([\d,]{5,})\s*\n", head)
            if pm:
                res.set("list_price", "$" + pm.group(1))
        for pat, key in ((r"\n([\d.]+)\nBeds?\n", "beds"), (r"\n([\d.]+)\nBaths?\n", "baths"), (r"\n([\d,]+)\nSq Ft\n", "building_sf")):
            m = re.search(pat, head)
            if m:
                res.set(key, m.group(1))
        em = re.search(r"Estimated Value:\s*(\$[\d,]+)", text)
        if em:
            rng = re.search(r"Range of Values:\s*(\$[\d,]+\s*-\s*\$[\d,]+)", text)
            res.set("estimate", f"{em.group(1)} (Homes.com avg of AVMs{', range ' + rng.group(1) if rng else ''})")
        m = re.search(r"\n(?:About This Home|Description)\n+(.{80,5000}?)\n(?:Listing Agent|Show More|Home Details|Property Details|Facts|Estimated Property Value)", text, re.S)
        if m and not res.description:
            res.description = m.group(1).strip()
        dm = re.search(r"(\d+)\s+Days? on (?:Homes\.com|Market)", text, re.I)
        if dm:
            res.set("days_on_market", dm.group(1))
        # Purchase history (public records): "Apr '06\n\tBuyer\t$840,000\t--"
        ph = re.search(r"Purchase History\n(.*?)Source: Public Records", text, re.S)
        if ph:
            for mo, yr, rest in re.findall(r"([A-Z][a-z]{2}) '(\d{2})\s*\n(.*?)(?=\n[A-Z][a-z]{2} '\d{2}|\Z)", ph.group(1), re.S):
                price = re.search(r"\$[\d,]+", rest)
                res.price_history.append({"date": f"{mo} 20{yr}" if int(yr) < 50 else f"{mo} 19{yr}", "event": "Sold (public record)",
                                          "price": price.group(0) if price else None, "source": "Public Records"})
            if "Sign in to view" in ph.group(1):
                res.login_gated_fields.append("buyer/owner names")
        lh = re.search(r"Price History\n(.{20,2500}?)\n(?:Tax History|Public Records|Schools|Neighborhood|Purchase History)", text, re.S)
        if lh:
            for row in re.findall(r"(\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]{2} \d{1,2}, \d{4})\s*\n?\s*([A-Za-z][A-Za-z ]{2,30})\s*\n?\s*(\$[\d,]+|--)", lh.group(1)):
                res.price_history.append({"date": row[0], "event": row[1].strip(), "price": row[2]})
        th = re.search(r"Tax History\n(.*?)Source: Public Records", text, re.S)
        if th:
            rows = re.findall(r"\n(\d{4})\t(\$[\d,]+)\t(\$[\d,]+)\t(\$[\d,]+)\t(\$[\d,]+)", th.group(1))
            for yr, tax, tot, land, impr in rows:
                res.tax_history.append({"year": int(yr), "tax": tax, "assessed": f"{tot} (land {land}, impr {impr})"})
            if rows:
                yr, tax, tot, land, impr = rows[0]
                res.set("taxes", f"{tax} ({yr})")
                res.set("assessment", f"{tot} (land {land}, impr {impr}, {yr})")
        own = re.search(r"Ownership History\n.*?\n(\w{3} \d{4} - Present)\n([^\n]+)", text, re.S)
        if own and "Sign in" in own.group(2):
            res.login_gated_fields.append("current owner")
