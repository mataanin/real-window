from __future__ import annotations
import datetime as dt
from urllib.parse import quote

from ..address import Address, match_score, has_unit
import re

from ..browser import fetch_json, human_pause, body_text
from ..extract import money, intish, strip_html
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx, Blocked

SEARCH_STATUS = {1: "active", 2: "pending", 3: "pending", 4: "sold", 32: "off-market", 128: "contingent", 130: "active"}


def _ms_date(ms):
    try:
        return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return None


class Redfin(SiteAdapter):
    name, label = "redfin", "Redfin"
    domains = ["redfin.com"]
    home_url = "https://www.redfin.com/"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        await self.open(page, self.home_url, ctx)
        st, data = await fetch_json(page, "/stingray/do/location-autocomplete?v=2&al=1&location=" + quote(addr.one_line))
        if st in (403, 429) or data is None:
            raise Blocked(f"autocomplete HTTP {st}")
        rows = []
        for sec in (data.get("payload") or {}).get("sections", []) + [(data.get("payload") or {}).get("exactMatch") or {}]:
            rows += sec.get("rows", []) if isinstance(sec, dict) and "rows" in sec else ([sec] if sec.get("url") else [])
        best, best_s = None, 0
        for r in rows:
            if str(r.get("type")) != "1":  # 1 = address
                continue
            s = match_score(addr, r.get("name", ""), (r.get("subName") or "").split(",")[0])
            if has_unit(r.get("name", "")) != bool(addr.unit):
                s -= 20
            if s > best_s:
                best, best_s = r, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append(f"no address match in autocomplete ({len(rows)} rows)")
            return
        res.url = "https://www.redfin.com" + best["url"]
        res.matched_address = f"{best['name']}, {best.get('subName', '')}"
        pid = best["id"].split("_")[-1]
        await human_pause()
        await self.open(page, res.url, ctx)
        # Without listingId the APIs return public-record data only; the page HTML carries the MLS listingId.
        # Pick the listingId that sits next to this propertyId; the page also embeds other homes' cards
        # ("New listings for sale"), whose listingIds must not be used. Off-market pages get none.
        lid = await page.evaluate(r"""(pid) => {
            const h = document.documentElement.innerHTML.replace(/\\"/g, '"');
            const re = new RegExp('"propertyId":' + pid + '\\b[^{}]{0,400}?"listingId":(\\d+)|"listingId":(\\d+)[^{}]{0,400}?"propertyId":' + pid + '\\b');
            const m = h.match(re);
            if (m) return m[1] || m[2];
            const head = document.body.innerText.slice(0, 1500);
            if (/OFF MARKET|\bSOLD\b/.test(head)) return null;
            const f = h.match(/"listingId":(\d+)/);
            return f ? f[1] : null }""", str(pid))
        q = f"propertyId={pid}" + (f"&listingId={lid}" if lid else "") + "&accessLevel=1"
        _, atf = await fetch_json(page, f"/stingray/api/home/details/aboveTheFold?{q}")
        _, btf = await fetch_json(page, f"/stingray/api/home/details/belowTheFold?{q}")
        _, avm = await fetch_json(page, f"/stingray/api/home/details/avm?{q}")
        atf = (atf or {}).get("payload") or {}
        btf = (btf or {}).get("payload") or {}
        avm = (avm or {}).get("payload") or {}
        if not atf and not btf:
            raise Blocked("Redfin detail APIs returned nothing")
        res.status = FOUND
        a = atf.get("addressSectionInfo") or {}
        status = a.get("homeStatusLabel") or (a.get("status") or {}).get("displayValue")
        res.set("status", status)
        pi = a.get("priceInfo") or {}
        if pi.get("amount"):
            label = (pi.get("label") or "").lower()
            if "sold" in label:
                res.set("last_sold_price", money(pi["amount"]))
            else:
                res.set("list_price", money(pi["amount"]))
        if a.get("soldDate"):
            res.set("last_sold_date", _ms_date(a["soldDate"]))
        res.set("beds", intish(a.get("beds")))
        res.set("baths", intish(a.get("baths")))
        res.set("building_sf", intish((a.get("sqFt") or {}).get("value")))
        res.set("lot_size", f"{intish(a.get('lotSize'))} sf" if a.get("lotSize") else None)
        res.set("year_built", a.get("yearBuilt"))
        res.set("apn", a.get("apn"))
        res.set("days_on_market", a.get("cumulativeDaysOnMarket") or (round(a["timeOnRedfin"] / 86400000) if a.get("timeOnRedfin") else None))
        if a.get("pricePerSqFt") and "sold" not in (pi.get("label") or "").lower():
            res.set("price_per_sf", money(a["pricePerSqFt"]))
        mh = atf.get("mainHouseInfo") or {}
        agents = mh.get("listingAgents") or []
        if agents:
            names = []
            for ag in agents:
                n = (ag.get("agentInfo") or {}).get("agentName")
                b = ag.get("brokerName")
                names.append(" - ".join(x for x in (n, b) if x))
            res.set("broker", "; ".join(x for x in names if x))
        res.description = strip_html(mh.get("marketingRemarks", [{}])[0].get("marketingRemark")) if mh.get("marketingRemarks") else None
        text = await body_text(page)
        if not res.description:
            m = re.search(r"\nAbout this home\n+(.{40,6000}?)\n(?:Show more|Show less|Listed by|Redfin last checked|Home facts|Open houses)", text, re.S)
            if m:
                res.description = m.group(1).strip()
        lm = re.search(r"Listed by ([^\n]{3,120})", text)
        if lm and "broker" not in res.fields:
            res.set("broker", lm.group(1).replace(" • ", " - ").strip())
        dm = re.search(r"\n([\d,]+) days? on Redfin", text)
        if dm:
            res.set("days_on_market", dm.group(1))
        mb = atf.get("mediaBrowserInfo") or {}
        if mb.get("photos"):
            res.set("photo_count", len(mb["photos"]))
        # public records + amenities
        pr = btf.get("publicRecordsInfo") or {}
        bi = pr.get("basicInfo") or {}
        res.set("property_type", bi.get("propertyTypeName"))
        res.set("stories", intish(bi.get("numStories")))
        res.set("year_built", bi.get("yearBuilt"))
        res.set("building_sf", intish(bi.get("sqFtFinished") or bi.get("totalSqFt")))
        res.set("lot_size", f"{intish(bi.get('lotSqFt'))} sf" if bi.get("lotSqFt") else None)
        ti = pr.get("taxInfo") or {}
        if ti.get("taxesDue"):
            res.set("taxes", f"{money(ti['taxesDue'])} ({ti.get('rollYear')})")
        if ti.get("taxableLandValue") or ti.get("taxableImprovementValue"):
            tot = (ti.get("taxableLandValue") or 0) + (ti.get("taxableImprovementValue") or 0)
            res.set("assessment", f"{money(tot)} (land {money(ti.get('taxableLandValue'))}, impr {money(ti.get('taxableImprovementValue'))}, {ti.get('rollYear')})")
        for t in pr.get("allTaxInfo") or []:
            res.tax_history.append({"year": t.get("rollYear"), "tax": money(t.get("taxesDue")),
                                    "land": money(t.get("taxableLandValue")), "improvements": money(t.get("taxableImprovementValue"))})
        amen = {}
        for sg in (btf.get("amenitiesInfo") or {}).get("superGroups", []):
            for g in sg.get("amenityGroups", []):
                for e in g.get("amenityEntries", []):
                    if e.get("amenityName") and e.get("amenityValues") and e["amenityValues"] != ["—"]:
                        amen[e["amenityName"]] = ", ".join(map(str, e["amenityValues"]))
        res.set("units", amen.get("# of Units") or amen.get("Number of Units") or amen.get("Total Units"))
        res.set("zoning", amen.get("Zoning Code") or amen.get("Zoning"))
        heat = [f"{k}: {v}" for k, v in amen.items() if any(w in k.lower() for w in ("heat", "cool", "utilit", "meter", "fuel", "water", "sewer", "electric"))]
        res.set("heating_utilities", "; ".join(heat))
        park = [f"{k}: {v}" for k, v in amen.items() if "park" in k.lower() or "garage" in k.lower()]
        res.set("parking", "; ".join(park))
        cons = [amen.get(k) for k in ("Construction Type", "Exterior Wall", "Roof Shape Type") if amen.get(k)]
        res.set("construction", ", ".join(cons))
        res.set("property_type", amen.get("State Use Description"))
        if amen:
            res.fields.setdefault("_redfin_facts", amen)
        seen = set()
        for e in (btf.get("propertyHistoryInfo") or {}).get("events", []):
            row = {"date": _ms_date(e.get("eventDate")) if e.get("eventDate") else None,
                   "event": e.get("eventDescription"), "price": money(e.get("price")), "source": e.get("source")}
            if "rent" in str(e.get("source") or "").lower() or e.get("isRental"):
                row["event"] = f"{row['event']} (rental listing)"
            k = (row["date"], row["event"], row["price"])
            if k not in seen:
                seen.add(k)
                res.price_history.append(row)
        pv = (avm.get("predictedValue") or avm.get("priceInfo", {}).get("amount")) if avm.get("predictedValue") else None
        if pv:
            res.set("estimate", f"{money(pv)} (Redfin Estimate)")
        elif avm.get("sectionPreviewText"):
            res.notes.append(f"Redfin estimate: {avm['sectionPreviewText']}")
