from __future__ import annotations
import re
from urllib.parse import urlencode

from ..address import Address, match_score, haversine_m
from ..browser import fetch_json, human_pause, body_text
from ..extract import jsonld, facts_from_text, money, strip_html
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx, Blocked

STATE_FULL = {"CT": "Connecticut", "NY": "New York", "MA": "Massachusetts", "RI": "Rhode Island", "NJ": "New Jersey",
              "NH": "New Hampshire", "VT": "Vermont", "ME": "Maine", "PA": "Pennsylvania"}


class Brevitas(SiteAdapter):
    name, label = "brevitas", "Brevitas"
    domains = ["brevitas.com"]
    home_url = "https://brevitas.com/listings"
    login_hint = "'Private Listings' are visible only to signed-in members"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        await self.open(page, self.home_url, ctx)
        pins = []
        for city in dict.fromkeys(x for x in (addr.city, addr.town) if x):
            q = {"location": f"{city}, {addr.state}", "city": city, "state": addr.state, "country": "United States",
                 "state_full": STATE_FULL.get(addr.state, addr.state)}
            st, data = await fetch_json(page, "/api/search?" + urlencode(q))
            if data is None:
                raise Blocked(f"Brevitas search HTTP {st}")
            pins += data.get("pins", [])
        best, best_s = None, 0
        for p in pins:
            s = match_score(addr, p.get("name", ""))
            if s < 85 and addr.lat and p.get("lat"):
                dist = haversine_m(addr.lat, addr.lon, p["lat"], p["lng"])
                if dist < 40:   # geocoded on the same parcel - name may be a marketing title
                    s = 86
            if s > best_s:
                best, best_s = p, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append(f"no Brevitas listing at this address among {len(pins)} pins in {addr.search_city}")
            return
        await human_pause()
        await self.open(page, f"https://brevitas.com/p/{best['uuid']}", ctx, settle=3)
        res.status, res.url = FOUND, page.url
        res.matched_address = best.get("name")
        res.set("list_price", money(best.get("price")))
        res.set("property_type", (best.get("type") or "").replace("_", " ") or None)
        text = await body_text(page)
        for d in await jsonld(page):
            if isinstance(d, dict):
                off = d.get("offers") if isinstance(d.get("offers"), dict) else None
                if off:
                    res.set("list_price", money(off.get("price")))
                if d.get("description") and not res.description:
                    res.description = strip_html(d["description"])
        text = text.replace("\u00a0", " ")
        MAP = {"lot area": "lot_size", "lot size": "lot_size", "units": "units", "building size": "building_sf",
               "building area": "building_sf", "rentable area": "building_sf", "square feet": "building_sf",
               "year built": "year_built", "cap rate": "cap_rate", "noi": "noi", "price/unit": "price_per_unit",
               "price per unit": "price_per_unit", "zoning": "zoning", "parking": "parking", "stories": "stories",
               "gross income": "gross_income", "expenses": "expenses", "taxes": "taxes", "price/sf": "price_per_sf",
               "apn": "apn", "parcel": "apn"}
        dm = re.search(r"\nDetails:\n(.*?)\nDescription:", text, re.S)
        extra = {}
        for line in (dm.group(1).splitlines() if dm else []):
            mm = re.match(r"^\s*([^:]{2,40}):\s+(.+?)\s*$", line)
            if mm:
                k, v = mm.group(1).strip(), mm.group(2).strip()
                key = MAP.get(k.lower())
                if key:
                    res.set(key, v)
                else:
                    extra[k] = v
        if extra:
            res.fields["_brevitas_details"] = extra
        sm = re.search(r"\n(Active|Pending|Under Contract|Sold|Off Market|Auction)\n", text[:3000])
        if sm:
            res.set("status", sm.group(1).lower())
        um = re.search(r"LAST UPDATED:\s*([\d/]+)", text)
        if um:
            res.notes.append(f"Brevitas listing last updated {um.group(1)}")
        pm = re.search(r"\n(\d+) Photos\n", text)
        if pm:
            res.set("photo_count", pm.group(1))
        m = re.search(r"\nDescription:\n(.{40,8000}?)\n\s*(?:Report Listing|Map:|Documents:)", text, re.S)
        if m and not res.description:
            res.description = re.sub(r"[ \t]{2,}", " ", m.group(1)).strip()
        bm = re.search(r"More Listings from ([^\n]{3,60})", text)
        if bm:
            res.set("broker", bm.group(1).strip())
        if re.search(r"\bauction\b", text, re.I):
            am = re.search(r"([^\n]*[Aa]uction[^\n]{0,120})", text)
            if am:
                res.set("auction", am.group(1)[:160])
        if "Private Listing" in text and "Log In" in text[:1500]:
            res.notes.append("Brevitas private-listing details require login")
        res.raw_text_snippet = text[:1500]
