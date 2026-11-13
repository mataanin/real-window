from __future__ import annotations
import re

from ..address import Address, match_score, slugify
from ..browser import human_pause, body_text
from ..extract import jsonld, facts_from_text, money, strip_html
from ..models import SiteResult, FOUND, NOT_FOUND, BLOCKED
from .base import SiteAdapter, RunCtx, Blocked


def _addr_from_listing_url(u: str) -> str:
    # https://www.loopnet.com/Listing/90-Grove-St-Ridgefield-CT/40936663/
    m = re.search(r"/Listing/([^/]+)/\d+", u)
    return m.group(1).replace("-", " ") if m else ""


async def _extract_costar_detail(page, res: SiteResult):
    """LoopNet and Showcase share CoStar markup: JSON-LD + 'Property Facts' label/value table."""
    for d in await jsonld(page):
        if not isinstance(d, dict):
            continue
        t = d.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in ("RealEstateListing", "Product", "Offer", "Place", "Apartment", "ApartmentComplex", "LandmarksOrHistoricalBuildings") for x in types):
            off = d.get("offers")
            off = off[0] if isinstance(off, list) and off else off
            if isinstance(off, dict):
                res.set("list_price", money(off.get("price")))
                ob = off.get("offeredBy") or {}
                if isinstance(ob, dict):
                    org = (ob.get("worksFor") or {}).get("name") if isinstance(ob.get("worksFor"), dict) else None
                    res.set("broker", " - ".join(x for x in (ob.get("name"), org) if x))
            if d.get("description") and not res.description:
                res.description = strip_html(d["description"])
            img = d.get("image")
            if isinstance(img, list):
                res.set("photo_count", len(img))
    text = await body_text(page)
    # Targeted "Label: Value" pairs from the Property Facts block (generic facts mis-read amenity lists).
    COSTAR = {
        "price": "list_price", "price per unit": "price_per_unit", "price per sf": "price_per_sf",
        "sale type": "sale_type", "property type": "property_type", "property subtype": "property_type",
        "apartment style": "construction", "building class": "building_class", "no. units": "units",
        "lot size": "lot_size", "building size": "building_sf", "rentable building area": "building_sf",
        "no. stories": "stories", "year built/renovated": "year_built", "year built": "year_built",
        "parking ratio": "parking", "parking": "parking", "zoning": "zoning", "apn / parcel id": "apn",
        "cap rate": "cap_rate", "noi": "noi", "gross rent multiplier": "grm", "average occupancy": "occupancy",
        "opportunity zone": "opportunity_zone", "auction date": "auction",
    }
    pf = re.search(r"Property Facts\n(.*?)\n(?:Amenities|Unit Mix|Secure Information|Map\n|Sale Advisor|Listing ID)", text, re.S)
    for line in (pf.group(1).splitlines() if pf else []):
        if ":" in line:
            k, v = line.split(":", 1)
            key = COSTAR.get(k.strip().lower())
            if key and v.strip():
                res.set(key, v.strip())
    head = text[:1500]
    m = re.search(r"\n(Multifamily|Retail|Office|Industrial|Mixed Use|Land|Hospitality|Specialty|Health Care)[^\n]* for Sale\n", head)
    if m:
        res.set("property_type", m.group(1))
        res.set("status", "For Sale")
    for pat, key in ((r"\n(\d+) Units\n", "units"), (r"([\d.]+%) Cap Rate", "cap_rate"), (r"([\d.]+%) (?:Average )?Occupancy", "occupancy")):
        m = re.search(pat, head)
        if m:
            res.set(key, m.group(1))
    m = re.search(r"\n(\d+) Photos\n", head)
    if m:
        res.set("photo_count", m.group(1))
    m = re.search(r"Date on Market: ([\d/]+)", text)
    if m:
        res.set("list_date", m.group(1))
    m = re.search(r"Last Updated: ([\d/]+)", text)
    if m:
        res.set("last_updated", m.group(1))
    m = re.search(r"Sale Advisors?\n(.*?)\n(?:Contact\n|Listing ID)", text, re.S)
    if m:
        lines = [l.strip() for l in m.group(1).splitlines() if l.strip() and not l.startswith(("Call ", "Contact "))]
        phones = [l for l in lines if re.fullmatch(r"\(?\d{3}\)?[ .-]?\d{3}-\d{4}", l)]
        people = [l for l in lines if l not in phones and len(l) < 80]
        if people:
            res.set("broker", " | ".join(people))
        if phones:
            res.set("broker_contact", ", ".join(dict.fromkeys(phones)))
    um = re.search(r"Unit Mix\n(Description\tNo\. Units.*?)\n(?:Secure Information|Financial Summary|Map\n|Attachments)", text, re.S)
    if um:
        rows = [r for r in um.group(1).splitlines()[1:] if "\t" in r]
        if rows:
            res.set("unit_mix", " | ".join(re.sub(r"\t", " / ", r) for r in rows) + "  (type / count / avg rent / SF)")
    if re.search(r"Financial Summary\nAccess detailed property financial data", text):
        res.login_gated_fields.append("financial summary (NOI, income, expenses)")
    att = re.search(r"Attachments\n(.*?)\n(?:Map|Sale Advisor)", text, re.S)
    if att:
        res.set("documents", " | ".join(l for l in att.group(1).splitlines() if l.strip())[:400])
    if re.search(r"Secure Information\n\nThese documents are confidential", text):
        res.login_gated_fields.append("confidential documents (OM / rent roll)")
    m = re.search(r"(?:Executive Summary|Property Description|Description)\n+(.{80,4000}?)\n(?:Property Facts|Investment Highlights|Amenities|Unit Mix|Location|Nearby)", text, re.S)
    if m and not res.description:
        res.description = m.group(1).strip()
    um = None and re.search(r"Unit Mix Information\n(.{20,800}?)\n(?:\n|Amenities|Property Taxes|Location)", text, re.S)
    if um:  # legacy layout
        res.set("unit_mix", re.sub(r"\s*\n\s*", " | ", um.group(1).strip())[:600])
    tx = re.search(r"Property Taxes\n(.{20,400}?)\n\n", text, re.S)
    if tx:
        res.set("taxes", re.sub(r"\s*\n\s*", " | ", tx.group(1).strip())[:300])
    if re.search(r"Auction|Bidding (?:Opens|Starts)|Ten-X", text):
        am = re.search(r"((?:Auction|Bidding)[^\n]{0,40}\n?[^\n]{0,80}(?:\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]{2} \d{1,2}, \d{4})[^\n]{0,60})", text)
        if am:
            res.set("auction", am.group(1).replace("\n", " "))
    res.raw_text_snippet = text[:1500]


class LoopNet(SiteAdapter):
    name, label = "loopnet", "LoopNet (fallback Showcase)"
    domains = ["loopnet.com", "showcase.com"]
    home_url = "https://www.loopnet.com/"
    login_hint = "Akamai bot manager; blocks are intermittent per IP/session. Synced loopnet.com cookies (incl. _abck/bm_sz) help."

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        n = await self.sess.reset_bot_cookies(["loopnet.com", "showcase.com"])
        if n:
            res.notes.append(f"reset {n} Akamai bot cookies (login cookies kept)")
        city = slugify(addr.search_city)
        st = addr.state.lower()
        try:
            try:
                found = await self._loopnet(page, addr, res, ctx, city, st)
            except Blocked as e:
                # The shared profile's Akamai cookies may be flagged; a fresh guest context often passes.
                res.notes.append(f"LoopNet blocked in shared session ({e}); retried in a fresh guest context")
                page = await self.sess.new_isolated_page()
                found = await self._loopnet(page, addr, res, ctx, city, st)
            if found:
                return
            res.status = NOT_FOUND
        except Blocked as e:
            res.notes.append(f"LoopNet blocked: {e}; trying Showcase.com")
            res.status = BLOCKED
        # Fallback / second look on Showcase (same CoStar inventory, no login wall)
        try:
            ok = await self._showcase(page, addr, res, ctx, city, st)
            if not ok and res.status == NOT_FOUND:
                res.notes.append("not on Showcase either")
        except Blocked as e:
            res.notes.append(f"Showcase blocked too: {e}")
            if res.status != BLOCKED:
                res.notes.append("(LoopNet itself was reachable)")
            else:
                raise Blocked("LoopNet and Showcase both blocked by Akamai")

    async def _scan(self, page, ctx, urls, addr, link_sel: str, addr_from):
        seen = {}
        for u in urls:
            await self.open(page, u, ctx)
            if not page.url.rstrip("/").endswith(u.rstrip("/").split("/")[-1]) and u.rstrip("/")[-1].isdigit():
                break   # page N redirected back to page 1 = no more pages
            links = await page.evaluate(f"() => [...new Set([...document.querySelectorAll('{link_sel}')].map(a => a.href))]")
            for l in links:
                seen.setdefault(l, addr_from(l))
            await human_pause(0.5, 1.2)
        best, best_s = None, 0
        for link, text in seen.items():
            s = match_score(addr, text)
            if s > best_s:
                best, best_s = link, s
        return (best if best_s >= 85 else None), len(seen)

    async def _loopnet(self, page, addr, res, ctx, city, st) -> bool:
        # Warm up on the home page first (Akamai scores direct deep links from a cold session harshly)
        await self.open(page, "https://www.loopnet.com/", ctx, settle=3)
        await page.mouse.move(400, 300); await page.mouse.wheel(0, 600); await human_pause(1.0, 2.0)
        base = f"https://www.loopnet.com/search/commercial-real-estate/{city}-{st}"
        apt = f"https://www.loopnet.com/search/apartment-buildings/{city}-{st}"
        urls = [f"{apt}/for-sale/", f"{base}/for-sale/", f"{base}/for-sale/2/", f"{base}/for-sale/3/", f"{base}/auctions/"]
        best, n = await self._scan(page, ctx, urls, addr, 'a[href*="/Listing/"]', _addr_from_listing_url)
        if not best:
            res.notes.append(f"LoopNet: no listing at this address among {n} {addr.search_city} for-sale/auction listings")
            return False
        await self.open(page, best, ctx, settle=3)
        res.status, res.url = FOUND, page.url
        res.matched_address = _addr_from_listing_url(best)
        await _extract_costar_detail(page, res)
        if "auction" in best.lower() or res.fields.get("auction"):
            res.notes.append("Ten-X auction listing surfaced via LoopNet")
        return True

    async def _showcase(self, page, addr, res, ctx, city, st) -> bool:
        await self.open(page, "https://www.showcase.com/", ctx, settle=3)
        await page.mouse.wheel(0, 500); await human_pause(1.0, 2.0)
        base = f"https://www.showcase.com/{st}/{city}"
        urls = [f"{base}/apartment-buildings/for-sale/", f"{base}/commercial-real-estate/for-sale/"]
        def from_url(u):
            m = re.search(r"showcase\.com/([^/]+)/[^/]+/[^/]+/\d+", u)
            return m.group(1).replace("-", " ") if m else ""
        best, n = await self._scan(page, ctx, urls, addr, 'a[href*="showcase.com/"]', from_url)
        if not best:
            res.notes.append(f"Showcase: no listing at this address among {n} links")
            return False
        await self.open(page, best, ctx, settle=3)
        res.status = FOUND
        res.url = res.url or page.url
        res.notes.append(f"Showcase listing: {page.url}")
        await _extract_costar_detail(page, res)
        return True
