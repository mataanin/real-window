from __future__ import annotations
import datetime as dt
import json
from urllib.parse import quote
import asyncio

import requests

from ..address import Address, match_score, has_unit
from ..browser import fetch_json, human_pause
from ..extract import money, intish, strip_html, next_data
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx, Blocked


def _suggest(q: str):
    """zillowstatic.com autocomplete is a public CDN endpoint outside PerimeterX."""
    try:
        r = requests.get("https://www.zillowstatic.com/autocomplete/v3/suggestions", params={"q": q}, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/156.0 Safari/537.36",
                                  "Referer": "https://www.zillow.com/", "Origin": "https://www.zillow.com"})
        return r.json() if r.ok else None
    except Exception:
        return None


class Zillow(SiteAdapter):
    name, label = "zillow", "Zillow"
    domains = ["zillow.com", "zillowstatic.com"]
    home_url = "https://www.zillow.com/"
    login_hint = "PerimeterX: sync zillow.com cookies (_px3/_pxvid) from a browser that has passed the check, or run with --interactive and solve once in the shared Chrome"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        # The autocomplete lives on zillowstatic.com (not behind PerimeterX) - load a light page first.
        data = await asyncio.to_thread(_suggest, addr.one_line)
        if data is None:
            raise Blocked("Zillow autocomplete unavailable")
        best, best_s = None, 0
        for r in data.get("results", []):
            md = r.get("metaData") or {}
            if not md.get("zpid"):
                continue
            line = f"{md.get('streetNumber', '')} {md.get('streetName', '')}"
            s = match_score(addr, line, md.get("city"))
            unit = md.get("unitNumber")
            if bool(unit) != bool(addr.unit):
                s -= 20   # unit-level record when the building was asked for (or vice versa)
            elif unit and addr.unit and unit.lower() != addr.unit.lower():
                s -= 20
            if s > best_s:
                best, best_s = r, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append(f"no address match in Zillow autocomplete ({len(data.get('results', []))} results)")
            return
        md = best["metaData"]
        zpid = md["zpid"]
        res.matched_address = best.get("display")
        res.url = f"https://www.zillow.com/homedetails/{zpid}_zpid/"
        await human_pause()
        await self.open(page, res.url, ctx, settle=3)
        res.url = page.url
        nd = await next_data(page)
        prop = None
        if nd:
            cp = (nd.get("props", {}).get("pageProps", {}) or {}).get("componentProps", {}) or {}
            cache = cp.get("gdpClientCache")
            if isinstance(cache, str):
                cache = json.loads(cache)
            for v in (cache or {}).values():
                if isinstance(v, dict) and v.get("property"):
                    prop = v["property"]
                    break
        if not prop:
            # apartment/building pages (b/...) use a different payload; fall back to text
            res.status = FOUND
            res.notes.append("Zillow page had no gdpClientCache property (building page?) - text only")
            return
        res.status = FOUND
        self._extract(prop, res)

    def _extract(self, p: dict, res: SiteResult):
        res.set("status", (p.get("homeStatus") or "").replace("_", " ").lower() or None)
        if p.get("homeStatus") in ("FOR_SALE", "PENDING", "FOR_RENT", "COMING_SOON") and p.get("price"):
            res.set("list_price", money(p["price"]))
        res.set("days_on_market", p.get("daysOnZillow") if p.get("homeStatus") in ("FOR_SALE", "PENDING") else None)
        res.set("last_sold_price", money(p.get("lastSoldPrice")))
        res.set("property_type", (p.get("homeType") or "").replace("_", " ").title() or None)
        res.set("beds", intish(p.get("bedrooms")))
        res.set("baths", intish(p.get("bathrooms")))
        res.set("building_sf", intish(p.get("livingAreaValue") or p.get("livingArea")))
        if p.get("lotAreaValue"):
            res.set("lot_size", f"{p['lotAreaValue']} {p.get('lotAreaUnits') or ''}".strip())
        res.set("year_built", p.get("yearBuilt"))
        res.set("photo_count", p.get("photoCount"))
        res.set("estimate", f"{money(p.get('zestimate'))} (Zestimate)" if p.get("zestimate") else None)
        res.set("rent_estimate", f"{money(p.get('rentZestimate'))}/mo (Rent Zestimate)" if p.get("rentZestimate") else None)
        res.set("hoa", money(p.get("monthlyHoaFee")))
        if p.get("taxAssessedValue"):
            res.set("assessment", money(p["taxAssessedValue"]))
        ai = p.get("attributionInfo") or {}
        broker = " - ".join(x for x in (ai.get("agentName"), ai.get("brokerName")) if x)
        res.set("broker", broker or p.get("brokerageName"))
        contact = ", ".join(x for x in (ai.get("agentPhoneNumber"), ai.get("brokerPhoneNumber"), ai.get("agentEmail")) if x)
        res.set("broker_contact", contact)
        res.description = strip_html(p.get("description"))
        rf = p.get("resoFacts") or {}
        res.set("units", rf.get("numberOfUnitsTotal"))
        res.set("zoning", rf.get("zoning") or rf.get("zoningDescription"))
        res.set("stories", rf.get("stories") or rf.get("storiesTotal"))
        heat = []
        for k in ("heating", "cooling", "utilities", "waterSource", "sewer", "electric"):
            v = rf.get(k)
            if v:
                heat.append(f"{k}: {', '.join(v) if isinstance(v, list) else v}")
        res.set("heating_utilities", "; ".join(heat))
        pk = rf.get("parkingFeatures") or rf.get("parkingCapacity")
        if pk:
            res.set("parking", ", ".join(pk) if isinstance(pk, list) else str(pk))
        if rf.get("taxAnnualAmount"):
            res.set("taxes", money(rf["taxAnnualAmount"]))
        res.set("gross_income", money(rf.get("grossIncome")))
        res.set("expenses", money(rf.get("operatingExpense") or rf.get("totalExpenses")))
        res.set("noi", money(rf.get("netOperatingIncome")))
        res.set("rents", money(rf.get("totalActualRent")))
        res.set("list_date", rf.get("onMarketDate") and dt.datetime.utcfromtimestamp(rf["onMarketDate"] / 1000).strftime("%Y-%m-%d"))
        res.set("construction", ", ".join(rf.get("constructionMaterials") or []) or None)
        if p.get("foreclosureTypes"):
            ft = {k: v for k, v in p["foreclosureTypes"].items() if v}
            if ft:
                res.set("auction", "Zillow foreclosure flags: " + ", ".join(ft))
        for e in p.get("priceHistory") or []:
            res.price_history.append({"date": e.get("date"), "event": e.get("event"), "price": money(e.get("price")),
                                      "source": e.get("source")})
            if (e.get("event") or "").lower() == "sold" and "last_sold_date" not in res.fields:
                res.set("last_sold_date", e.get("date"))
        for t in p.get("taxHistory") or []:
            yr = dt.datetime.utcfromtimestamp(t["time"] / 1000).year if t.get("time") else None
            res.tax_history.append({"year": yr, "tax": t.get("taxPaid"), "assessed": t.get("value")})
