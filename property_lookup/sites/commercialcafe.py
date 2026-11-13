from __future__ import annotations
import re

from ..address import Address, match_score, slugify
from ..browser import human_pause, body_text
from ..extract import jsonld, facts_from_text, money, strip_html
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx

CARD_JS = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/commercial-property/"]')) {
    let c = a;
    for (let i = 0; i < 6 && c.parentElement; i++) { c = c.parentElement; if (c.innerText.length > 120) break; }
    out.push([a.href, c.innerText.replace(/\\n/g, ' | ').slice(0, 400)]);
  }
  return out;
}"""


class CommercialCafe(SiteAdapter):
    name, label = "commercialcafe", "CommercialCafe / CommercialEdge"
    domains = ["commercialcafe.com", "commercialedge.com"]
    home_url = "https://www.commercialcafe.com/"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        st, cities = addr.state.lower(), []
        for c in (addr.city, addr.town):
            if c and slugify(c) not in cities:
                cities.append(slugify(c))
        cards: dict[str, str] = {}
        for city in cities:
            base = f"https://www.commercialcafe.com/commercial-real-estate/us/{st}/{city}/"
            for u in (base, base + "?page=2", base + "?page=3"):
                await self.open(page, u, ctx)
                if "404" in (await page.title()):
                    break
                before = len(cards)
                for href, txt in await page.evaluate(CARD_JS):
                    cards.setdefault(href, txt)
                if len(cards) == before:
                    break
                await human_pause(0.4, 1.0)
        best, best_s = None, 0
        for href, txt in cards.items():
            # card text: "Name | 48 Amsterdam Avenue, North End, Bridgeport, CT | ..."
            for part in txt.split(" | ")[:3]:
                s = match_score(addr, part)
                if s > best_s:
                    best, best_s = href, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append(f"no CommercialCafe listing at this address among {len(cards)} listings in {', '.join(cities)}")
            return
        await human_pause()
        await self.open(page, best, ctx, settle=2.5)
        res.status, res.url = FOUND, page.url
        res.notes.append("CommercialCafe listings are mirrored on CommercialSearch/PropertyShark (same Yardi feed)")
        text = await body_text(page)
        m = re.search(r"\n(\d[^\n]{5,80}, [A-Z]{2} \d{5})\n", text)
        res.matched_address = m.group(1) if m else cards.get(best, "")[:120]
        blocks = re.findall(r"Property Information\n(.*?)\n(?:Description|Highlights|Location)\n", text, re.S)
        block = next((b for b in blocks if re.search(r"^(For Sale|For Lease|Property Type)", b, re.M)), "")
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        KEYS = {"For Sale", "For Lease", "Property Type", "Property Size", "Units", "Lot Size", "Year Built",
                "Year Renovated", "Date Updated", "Price per Unit", "Cap Rate", "NOI", "Building Class", "Zoning",
                "Parking", "Occupancy", "Stories", "Additional Rent", "Price/SF", "Price per SF"}
        kv, i = {}, 0
        while i < len(lines):
            if lines[i] in KEYS and i + 1 < len(lines) and lines[i + 1] not in KEYS:
                kv[lines[i]] = lines[i + 1]
                i += 2
            else:
                mm = re.match(r"^(%s)\s+(.+)$" % "|".join(map(re.escape, KEYS)), lines[i])
                if mm:
                    kv[mm.group(1)] = mm.group(2)
                i += 1
        res.set("list_price", money(kv.get("For Sale")))
        res.set("status", "for sale" if "For Sale" in kv else ("for lease" if "For Lease" in kv else None))
        res.set("property_type", kv.get("Property Type"))
        res.set("building_sf", kv.get("Property Size"))
        res.set("units", kv.get("Units"))
        res.set("lot_size", kv.get("Lot Size"))
        yb = kv.get("Year Built")
        if yb and kv.get("Year Renovated"):
            yb = f"{yb} (renovated {kv['Year Renovated']})"
        res.set("year_built", yb)
        res.set("cap_rate", kv.get("Cap Rate"))
        res.set("noi", kv.get("NOI"))
        res.set("zoning", kv.get("Zoning"))
        res.set("parking", kv.get("Parking"))
        res.set("stories", kv.get("Stories"))
        res.set("price_per_unit", kv.get("Price per Unit"))
        res.set("price_per_sf", kv.get("Price/SF") or kv.get("Price per SF"))
        if kv.get("Date Updated"):
            res.notes.append(f"CommercialCafe listing updated {kv['Date Updated']}")
        extra = {k: v for k, v in kv.items() if k in ("Building Class", "Occupancy", "Additional Rent")}
        if extra:
            res.fields["_commercialcafe_details"] = extra
        d = re.search(r"\nDescription\n+(?!Contacts)(.{80,8000}?)(?:\nRead More|\nHighlights|\nLocation\n|\nContacts?\n|\nNearby|\nTransportation|$)", text, re.S)
        if d:
            res.description = d.group(1).strip()
        hl = re.search(r"\nHighlights\n(.{20,3000}?)\n(?:Location|Contacts?|Nearby|Transportation|Map)\n", text, re.S)
        if hl:
            res.description = (res.description or "") + "\n\nHighlights:\n" + hl.group(1).strip()
        bm = await page.evaluate("""() => [...document.querySelectorAll('[class*=contact] [class*=name], [class*=broker] [class*=name], [class*=agent] [class*=name]')].map(e => e.innerText.trim()).filter(Boolean).slice(0, 4)""")
        if bm:
            res.set("broker", "; ".join(dict.fromkeys(bm)))
        for dct in await jsonld(page):
            if isinstance(dct, dict) and dct.get("@type") in ("Product", "RealEstateListing", "Place"):
                if isinstance(dct.get("image"), list) and len(dct["image"]) > 1:
                    res.set("photo_count", len(dct["image"]))
                if not res.description and dct.get("description"):
                    res.description = strip_html(dct["description"])
        res.raw_text_snippet = text[:1500]
