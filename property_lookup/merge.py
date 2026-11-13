"""Merge per-site results into one fact table + conflicts, and write report.md / report.json."""
from __future__ import annotations
import datetime as dt
import json
import re
from pathlib import Path

from .address import Address
from .models import FIELDS, SiteResult, FOUND

NUMERIC = {"list_price", "last_sold_price", "price_per_unit", "price_per_sf", "units", "beds", "baths", "building_sf",
           "year_built", "stories", "noi", "gross_income", "photo_count", "days_on_market", "taxes", "assessment", "hoa"}
NO_CONFLICT = {"broker", "broker_contact", "description", "photo_count", "heating_utilities", "parking", "construction",
               "estimate", "rent_estimate", "days_on_market", "property_type", "auction", "unit_mix", "rents", "apn"}
SITE_LABEL = {}


def _first_num(v) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _lot_sf(v) -> float | None:
    s = str(v).lower()
    n = _first_num(s)
    if n is None:
        return None
    if "acre" in s or re.search(r"\bac\b", s):
        return n * 43560
    return n


def _norm(key: str, v):
    if key in ("list_date", "last_sold_date", "last_updated"):
        m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", str(v))
        if m:
            return ("txt", f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    if key == "lot_size":
        n = _lot_sf(v)
        return ("num", n) if n is not None else ("txt", str(v).lower())
    if key in NUMERIC:
        n = _first_num(v)
        if n is not None:
            return ("num", n)
    if key == "status":
        s = str(v).lower()
        for canon, words in (("active", ("for sale", "active", "on-market", "on market", "for_sale", "call for offers", "highest & best")),
                             ("pending", ("pending", "under contract", "contingent")),
                             ("sold", ("sold",)), ("off-market", ("off market", "off-market", "not for sale", "off_market")),
                             ("auction", ("auction",))):
            if any(w in s for w in words):
                return ("txt", canon)
        return ("txt", s)
    if key == "cap_rate":
        n = _first_num(v)
        return ("num", n) if n is not None else ("txt", str(v))
    return ("txt", re.sub(r"\s+", " ", str(v)).strip().lower())


def _same(key, a, b) -> bool:
    if a[0] == "num" and b[0] == "num":
        x, y = a[1], b[1]
        if key == "lot_size":
            return abs(x - y) <= max(0.06 * max(x, y), 300)   # 0.29 ac vs 12,632 sf
        if key in ("building_sf",):
            return abs(x - y) <= 0.02 * max(x, y)
        if key in ("list_price", "last_sold_price", "taxes", "assessment", "noi", "gross_income", "price_per_unit", "price_per_sf"):
            return abs(x - y) <= 0.01 * max(abs(x), abs(y), 1)
        return x == y
    return a == b


def merge(results: list[SiteResult]) -> tuple[dict, list[dict]]:
    merged: dict[str, list[dict]] = {}
    conflicts = []
    for key, _label in FIELDS:
        groups: list[dict] = []
        for r in results:
            if r.status != FOUND or key not in r.fields:
                continue
            v = r.fields[key]
            nv = _norm(key, v)
            for g in groups:
                if _same(key, g["_n"], nv):
                    g["sources"].append(r.site)
                    break
            else:
                groups.append({"value": v, "sources": [r.site], "_n": nv})
        if groups:
            groups.sort(key=lambda g: -len(g["sources"]))
            for g in groups:
                g.pop("_n")
            merged[key] = groups
            if len(groups) > 1 and key not in NO_CONFLICT:
                conflicts.append({"field": key, "values": groups})
    return merged, conflicts


def _md_escape(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_report(addr: Address, results: list[SiteResult], outdir: Path, labels: dict[str, str]) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    merged, conflicts = merge(results)
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    L = []
    L.append(f"# Property lookup: {addr.one_line}\n")
    L.append(f"_Generated {now}. Input: `{addr.raw}`._  ")
    geo = []
    if addr.town:
        geo.append(f"town (Census county subdivision): **{addr.town}**")
    if addr.extra.get("county"):
        geo.append(f"county/planning region: {addr.extra['county']}")
    if addr.lat:
        geo.append(f"lat/lon {addr.lat:.6f}, {addr.lon:.6f}")
    if geo:
        L.append("Geocoded: " + "; ".join(geo) + "\n")
    L.append("Values are copied from the sites as shown; blank means no site showed it. Nothing is estimated by this tool.\n")
    L.append("## Sources checked\n")
    L.append("| Site | Status | Matched address | Link | Notes |")
    L.append("|---|---|---|---|---|")
    for r in results:
        link = f"[open]({r.url})" if r.url else ""
        notes = "; ".join(r.notes)[:400]
        L.append(f"| {labels.get(r.site, r.site)} | **{r.status}** | {_md_escape(r.matched_address or '')} | {link} | {_md_escape(notes)} |")
    L.append("")
    L.append("## Consolidated facts\n")
    L.append("| Field | Value | Source(s) |")
    L.append("|---|---|---|")
    for key, label in FIELDS:
        groups = merged.get(key)
        if not groups:
            L.append(f"| {label} |  |  |")
            continue
        first = True
        for g in groups:
            L.append(f"| {label if first else ''} | {_md_escape(g['value'])} | {', '.join(labels.get(s, s) for s in g['sources'])} |")
            first = False
    L.append("")
    L.append("## Conflicts\n")
    if not conflicts:
        L.append("No disagreements between sites on the compared fields.\n")
    else:
        for c in conflicts:
            label = dict(FIELDS)[c["field"]]
            vals = "; ".join(f"**{_md_escape(g['value'])}** ({', '.join(labels.get(s, s) for s in g['sources'])})" for g in c["values"])
            L.append(f"- **{label}**: {vals}")
        L.append("")
    gated = [(r.site, sorted(set(r.login_gated_fields))) for r in results if r.login_gated_fields]
    if gated:
        L.append("## Hidden behind login\n")
        for s, f in gated:
            L.append(f"- {labels.get(s, s)}: {', '.join(f)}")
        L.append("")
    hist = [(r, e) for r in results if r.status == FOUND for e in r.price_history]
    if hist:
        L.append("## Price / sale history\n")
        L.append("| Date | Event | Price | Site (source) |")
        L.append("|---|---|---|---|")
        for r, e in sorted(hist, key=lambda x: str(x[1].get("date") or ""), reverse=True):
            src = labels.get(r.site, r.site) + (f" ({e['source']})" if e.get("source") else "")
            L.append(f"| {e.get('date') or ''} | {_md_escape(e.get('event') or '')} | {e.get('price') or ''} | {_md_escape(src)} |")
        L.append("")
    taxes = [(r, t) for r in results if r.status == FOUND for t in r.tax_history]
    if taxes:
        L.append("## Tax / assessment history\n")
        L.append("| Year | Tax | Assessed / land + impr | Site |")
        L.append("|---|---|---|---|")
        for r, t in sorted(taxes, key=lambda x: str(x[1].get("year") or ""), reverse=True)[:30]:
            ass = t.get("assessed") or (f"{t.get('land')} + {t.get('improvements')}" if t.get("land") else "")
            L.append(f"| {t.get('year') or ''} | {t.get('tax') or ''} | {ass} | {labels.get(r.site, r.site)} |")
        L.append("")
    extra = [(r, k, v) for r in results if r.status == FOUND for k, v in r.fields.items() if k.startswith("_")]
    if extra:
        L.append("## Other site-specific facts\n")
        for r, k, v in extra:
            L.append(f"**{labels.get(r.site, r.site)}**: " + "; ".join(f"{a}: {b}" for a, b in v.items()) if isinstance(v, dict) else str(v))
            L.append("")
    descs = [r for r in results if r.status == FOUND and r.description]
    if descs:
        L.append("## Listing descriptions\n")
        seen = set()
        for r in descs:
            key = r.description[:200]
            if key in seen:
                L.append(f"### {labels.get(r.site, r.site)}\n\n_(same text as above)_\n")
                continue
            seen.add(key)
            L.append(f"### {labels.get(r.site, r.site)}\n\n{r.description.strip()}\n")
    L.append("## Links\n")
    for r in results:
        if r.url:
            L.append(f"- {labels.get(r.site, r.site)} ({r.status}): {r.url}")
    shots = [r for r in results if r.screenshot_path]
    if shots:
        L.append("\n## Screenshots\n")
        for r in shots:
            L.append(f"- {labels.get(r.site, r.site)}: `{r.screenshot_path}`")
    md = outdir / "report.md"
    md.write_text("\n".join(L) + "\n", encoding="utf-8")
    js = outdir / "report.json"
    js.write_text(json.dumps({
        "generated": now, "input": addr.raw, "address": addr.one_line, "town": addr.town,
        "lat": addr.lat, "lon": addr.lon, "geocode": addr.extra,
        "sources": [r.to_dict() for r in results], "merged": merged, "conflicts": conflicts,
    }, indent=2, default=str), encoding="utf-8")
    return md, js
