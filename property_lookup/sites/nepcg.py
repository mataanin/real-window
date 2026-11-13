from __future__ import annotations
import re

import requests

from ..address import Address, match_score
from ..browser import body_text
from ..extract import facts_from_text, money
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx

INVENTORY = "https://buildout.com/plugins/3e346fb977cf39092f077024fa39e0a896533112/inventory.json"


class NEPCG(SiteAdapter):
    """Northeast Private Client Group current listings (their site embeds a Buildout inventory)."""
    name, label = "nepcg", "Northeast Private Client Group"
    domains = ["northeastpcg.com", "buildout.com"]
    home_url = "https://northeastpcg.com/for-sale/"
    has_login = False

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        items, pg = [], 0
        while pg < 10:
            r = requests.get(INVENTORY, params={"page": pg}, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
            d = r.json()
            items += d.get("inventory", [])
            meta = d.get("meta", {})
            if (pg + 1) * meta.get("limit", 30) >= meta.get("total", 0):
                break
            pg += 1
        best, best_s = None, 0
        for it in items:
            s = match_score(addr, it.get("address", ""), it.get("city"))
            if s > best_s:
                best, best_s = it, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append(f"not among NEPCG's {len(items)} current listings")
            return
        res.status, res.url = FOUND, best.get("show_link")
        res.matched_address = best.get("address_one_line")
        res.set("status", "under contract" if best.get("under_contract") else ("closed" if best.get("closed") else ("for sale" if best.get("sale") else "for lease")))
        attrs = dict(best.get("index_attributes") or []) | dict(best.get("info_window_attributes") or [])
        res.set("property_type", attrs.get("Property Type") or best.get("property_sub_type_name"))
        res.set("list_price", money(attrs.get("Price")) if re.search(r"\d", attrs.get("Price", "")) else attrs.get("Price"))
        res.set("building_sf", attrs.get("Building Size"))
        bc = best.get("broker_contacts") or []
        res.set("broker", "; ".join(b.get("name", "") for b in bc) + " (Northeast Private Client Group)")
        res.set("broker_contact", "; ".join(f"{b.get('phone', '')} {b.get('email', '')}".strip() for b in bc))
        # Detail page (Buildout iframe) has units, description, highlights
        await self.open(page, res.url, ctx, settle=5)
        frame = next((f for f in page.frames if "buildout.com" in f.url and "inventory" in f.url), None)
        text = ""
        if frame:
            for _ in range(15):
                text = await frame.evaluate("() => document.body ? document.body.innerText : ''")
                if "Property Details" in text or "Property Description" in text:
                    break
                await page.wait_for_timeout(1000)
        if text:
            for k, v in facts_from_text(text).items():
                res.set(k, v)
            m = re.search(r"Property Description\n(.+?)\n(?:Highlights|Map|Location)", text, re.S)
            hl = re.search(r"Highlights\n(.+?)\n(?:Map|Location|Access Secure)", text, re.S)
            res.description = "\n\n".join(x for x in ((m.group(1).strip() if m else None),
                                                      ("Highlights:\n" + hl.group(1).strip()) if hl else None) if x) or None
            res.raw_text_snippet = text[:1500]
