from __future__ import annotations
from urllib.parse import quote

from ..address import Address, match_score, haversine_m
from ..browser import fetch_json, human_pause
from ..extract import money, intish, strip_html
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx, Blocked

DETAIL_MAP = {
    "Asking Price": "list_price", "Property Type": "property_type", "Sub Type": "property_type",
    "Square Footage": "building_sf", "Net Rentable (sq ft)": "building_sf", "Units": "units",
    "Year Built": "year_built", "Lot Size (acres)": "lot_size", "Cap Rate": "cap_rate", "NOI": "noi",
    "Price/Unit": "price_per_unit", "Price/Sq Ft": "price_per_sf", "Occupancy": "occupancy",
    "Zoning": "zoning", "APN": "apn", "Stories": "stories", "Buildings": "buildings",
    "Pro-Forma Cap Rate": "proforma_cap_rate", "Pro-Forma NOI": "proforma_noi", "Tenancy": "tenancy",
    "Investment Type": "investment_type", "Class": "building_class", "Parking": "parking",
}


class Crexi(SiteAdapter):
    name, label = "crexi", "Crexi"
    domains = ["crexi.com"]
    home_url = "https://www.crexi.com/"

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        await self.open(page, self.home_url, ctx)
        lat, lon = addr.lat, addr.lon
        # Crexi's own property-records search also gives coordinates (and the parcel/APN).
        st, recs = await fetch_json(page, "https://api.crexi.com/universal-search/v2/search?query="
                                    + quote(f"{addr.line1}, {addr.search_city}, {addr.state}") + "&searchTypes=Records")
        if st in (403, 429):
            raise Blocked(f"Crexi API HTTP {st}")
        for r in recs or []:
            a = r.get("address") or {}
            if match_score(addr, a.get("streetAddress", ""), a.get("city")) >= 85:
                res.set("apn", a.get("apn"))
                loc = a.get("location") or {}
                lat, lon = lat or loc.get("lat"), lon or loc.get("lon")
                res.notes.append(f"Crexi property record: {a.get('fullAddress')}")
                break
        if lat is None:
            res.status = NOT_FOUND
            res.notes.append("could not geocode address for Crexi map search")
            return
        d = 0.003
        body = {"excludeFilters": [], "excludeSort": [], "ids": [],
                "boundingBox": {"latitudeMax": lat + d, "latitudeMin": lat - d, "longitudeMax": lon + d, "longitudeMin": lon - d},
                "filters": {}, "from": 0, "searchTypes": ["Sales"], "size": 60,
                "sorting": {"listingAttributes.dateActivated": "Descending"}}
        st, data = await fetch_json(page, "https://api.crexi.com/universal-search/v2/search", method="POST", body=body)
        if data is None:
            raise Blocked(f"Crexi search HTTP {st}")
        best, best_s = None, 0
        for it in data.get("items", []):
            locs = it.get("address") or it.get("locations") or []
            if isinstance(locs, dict):
                locs = [locs]
            for a in locs:
                street = a.get("streetAddress") or a.get("address") or a.get("fullAddress") or ""
                s = match_score(addr, street, a.get("city"))
                if s < 85 and a.get("location") and addr.lat:
                    pass
                if s > best_s:
                    best, best_s = it, s
            if best is not it and it.get("propertyName"):
                s = match_score(addr, it.get("propertyName"))
                if s > best_s:
                    best, best_s = it, s
        if not best or best_s < 85:
            res.status = NOT_FOUND
            res.notes.append(f"no Crexi sale listing at this address ({len(data.get('items', []))} listings within ~300 m)")
            return
        asset_id = str(best.get("id", "")).split("-")[-1]
        await human_pause()
        st, asset = await fetch_json(page, f"https://api.crexi.com/assets/{asset_id}")
        if not asset:
            raise Blocked(f"Crexi asset API HTTP {st}")
        res.status = FOUND
        res.url = f"https://www.crexi.com/properties/{asset_id}/{asset.get('urlSlug', '')}".rstrip("/")
        loc = (asset.get("locations") or [{}])[0]
        res.matched_address = loc.get("fullAddress") or f"{loc.get('address')}, {loc.get('city')}"
        res.set("status", asset.get("status"))
        res.set("list_date", (asset.get("activatedOn") or "")[:10] or None)
        for k, v in (asset.get("details") or {}).items():
            key = DETAIL_MAP.get(k)
            if isinstance(v, str) and set(v.strip()) <= {"*"}:
                res.login_gated_fields.append(key or k)
                continue
            if not key:
                res.fields.setdefault("_crexi_details", {})[k] = v
                continue
            if isinstance(v, str) and set(v.strip()) <= {"*"}:
                res.login_gated_fields.append(key)
                continue
            if key == "lot_size":
                v = f"{v} acres"
            if key in ("occupancy", "proforma_cap_rate", "proforma_noi", "tenancy", "investment_type", "buildings", "building_class"):
                res.fields.setdefault("_crexi_details", {})[k] = v
                continue
            res.set(key, v)
        if asset.get("askingPrice"):
            res.set("list_price", money(asset["askingPrice"]))
        brokers = asset.get("brokers") or []
        if brokers:
            names = [" ".join(x for x in (b.get("firstName"), b.get("lastName")) if x) for b in brokers]
            org = (brokers[0].get("brokerage") or {}).get("name") if isinstance(brokers[0].get("brokerage"), dict) else None
            res.set("broker", ", ".join(n for n in names if n) + (f" ({org})" if org else ""))
        res.set("photo_count", asset.get("numberOfImages") or asset.get("numberOfGalleryItems"))
        if asset.get("auctionStartDate") or asset.get("auctionEndDate") or (asset.get("status") or "").lower() == "auction":
            res.set("auction", f"start {asset.get('auctionStartDate')}, end {asset.get('auctionEndDate')}".replace("None", "?"))
        if asset.get("offersDueOn"):
            res.notes.append(f"offers due {asset['offersDueOn'][:10]}")
        desc = strip_html(asset.get("marketingDescription"))
        hl = strip_html(asset.get("investmentHighlights"))
        res.description = "\n\n".join(x for x in (desc, ("Highlights:\n" + hl) if hl else None) if x) or None
        if asset.get("vaultAccessStatus") == "AuthRequired" or res.login_gated_fields:
            res.notes.append("some Crexi fields/OM are masked for guests (sign in / sync crexi.com cookies): "
                             + ", ".join(sorted(set(res.login_gated_fields))))
        await self.open(page, res.url, ctx)
