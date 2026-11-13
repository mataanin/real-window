from __future__ import annotations
from urllib.parse import quote
import asyncio
import re

import requests


def _suggest(q: str):
    try:
        r = requests.get("https://parser-external.geo.moveaws.com/suggest",
                         params={"input": q, "client_id": "rdc-home", "limit": 10, "area_types": "address"}, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/156.0 Safari/537.36",
                                  "Origin": "https://www.realtor.com", "Referer": "https://www.realtor.com/"})
        return r.json() if r.ok else None
    except Exception:
        return None

from ..address import Address, match_score, has_unit
from ..browser import fetch_json, human_pause
from ..extract import money, intish, next_data
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx, Blocked


class Realtor(SiteAdapter):
    name, label = "realtor", "Realtor.com"
    domains = ["realtor.com", "moveaws.com"]
    home_url = "https://www.realtor.com/"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        cands = []
        for q in (addr.one_line, f"{addr.line1}, {addr.search_city}, {addr.state}"):
            data = await asyncio.to_thread(_suggest, q)
            if data is None:
                raise Blocked("Realtor.com suggest endpoint unavailable")
            cands += data.get("autocomplete", [])
            if cands:
                break
        best, best_s, unit_only = None, 0, []
        for c in cands:
            if c.get("area_type") != "address" or not c.get("mpr_id"):
                continue
            s = match_score(addr, c.get("line", ""), c.get("city"))
            if s >= 85 and has_unit(c.get("line", "")) and not addr.unit:
                unit_only.append(c.get("line"))
                s -= 20
            if s > best_s:
                best, best_s = c, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            msg = f"no building-level address match in Realtor.com suggest ({len(cands)} candidates)"
            if unit_only:
                msg += f"; only unit-level records: {', '.join(unit_only[:4])}"
            res.notes.append(msg)
            return
        res.matched_address = f"{best.get('line')}, {best.get('city')}, {best.get('state_code')} {best.get('postal_code')}"
        res.url = f"https://www.realtor.com/realestateandhomes-detail/M{best['mpr_id']}"
        await human_pause()
        await self.open(page, res.url, ctx, settle=3)
        res.url = page.url
        nd = await next_data(page)
        pd = ((nd or {}).get("props", {}).get("pageProps", {}).get("initialReduxState") or {}).get("propertyDetails")
        if not pd:
            raise Blocked("Realtor.com detail page had no property data (likely Kasada challenge)")
        res.status = FOUND
        status = pd.get("status")
        res.set("status", (status or "").replace("_", " "))
        if status in ("for_sale", "pending", "contingent", "coming_soon"):
            res.set("list_price", money(pd.get("list_price")))
            res.set("list_date", (pd.get("list_date") or "")[:10])
        else:
            if pd.get("list_price"):
                res.notes.append(f"last list price {money(pd.get('list_price'))} ({(pd.get('list_date') or '')[:10]}, status {status})")
        res.set("last_sold_price", money(pd.get("last_sold_price")))
        res.set("last_sold_date", (pd.get("last_sold_date") or "")[:10] or None)
        d = pd.get("description") or {}
        res.set("beds", intish(d.get("beds")))
        res.set("baths", d.get("baths_consolidated") or intish(d.get("baths")))
        res.set("building_sf", intish(d.get("sqft")))
        res.set("lot_size", f"{intish(d.get('lot_sqft'))} sf" if d.get("lot_sqft") else None)
        res.set("year_built", d.get("year_built"))
        res.set("property_type", (d.get("type") or "").replace("_", " ") or None)
        res.set("stories", d.get("stories"))
        res.set("units", d.get("units"))
        res.set("heating_utilities", ", ".join(x for x in (d.get("heating"), d.get("cooling")) if x))
        res.set("parking", d.get("garage") and f"{d['garage']} garage")
        res.set("construction", d.get("construction"))
        res.description = d.get("text")
        res.set("photo_count", pd.get("photo_count"))
        res.set("price_per_sf", money(pd.get("price_per_sqft")))
        hoa = pd.get("hoa") or {}
        res.set("hoa", money(hoa.get("fee")))
        ests = ((pd.get("estimates") or {}).get("current_values") or [])
        best_est = next((e for e in ests if e.get("isbest_homevalue")), ests[0] if ests else None)
        if best_est and best_est.get("estimate"):
            res.set("estimate", f"{money(best_est['estimate'])} (Realtor.com estimate)")
        ads = pd.get("advertisers") or []
        if ads:
            a = ads[0]
            office = (a.get("office") or {}).get("name")
            res.set("broker", " - ".join(x for x in (a.get("name"), office) if x))
            phones = [p.get("number") for p in (a.get("phones") or []) if p.get("number")]
            res.set("broker_contact", ", ".join([*phones, a.get("email") or ""]).strip(", "))
        src = pd.get("source") or {}
        if src.get("days_on_mls"):
            res.set("days_on_market", src["days_on_mls"])
        facts = {}
        for det in pd.get("details") or []:
            cat = det.get("category")
            txt = "; ".join(det.get("text") or [])
            if cat and txt:
                facts[cat] = txt
        for cat, txt in facts.items():
            low = txt.lower()
            if "unit" in cat.lower() or "number of units" in low:
                res.set("unit_mix", txt[:400])
            if "heat" in cat.lower() or "utilit" in cat.lower():
                res.set("heating_utilities", txt[:300])
            if "parking" in cat.lower() or "garage" in cat.lower():
                res.set("parking", txt[:300])
            tm = re.search(r"Annual Tax Amount:\s*([\d,.]+)", txt)
            if tm:
                ty = re.search(r"Tax Year:\s*(\d{4})", txt)
                res.set("taxes", f"${float(tm.group(1).replace(',', '')):,.0f}" + (f" ({ty.group(1)})" if ty else ""))
            am = re.search(r"Assessed Value:\s*([\d,.]+)", txt)
            if am:
                res.set("assessment", f"${float(am.group(1).replace(',', '')):,.0f}")
            zm = re.search(r"Zoning(?: Description)?:\s*([^;]{1,60})", txt)
            if zm:
                res.set("zoning", zm.group(1).strip())
            if "income" in cat.lower() or "income" in low:
                res.set("gross_income", txt[:300])
        if facts:
            res.fields.setdefault("_realtor_details", facts)
        for e in pd.get("property_history") or []:
            res.price_history.append({"date": e.get("date"), "event": e.get("event_name"), "price": money(e.get("price")),
                                      "source": e.get("source_name")})
        for t in pd.get("tax_history") or []:
            res.tax_history.append({"year": t.get("year"), "tax": t.get("tax"), "assessed": (t.get("assessment") or {}).get("total")})
