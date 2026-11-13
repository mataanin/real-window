"""Generic extraction helpers: JSON-LD, __NEXT_DATA__, label/value scraping, number parsing."""
from __future__ import annotations
import html as htmlmod
import json
import re
from typing import Any

from playwright.async_api import Page


async def jsonld(page: Page) -> list[Any]:
    raw = await page.evaluate(
        "() => [...document.querySelectorAll('script[type=\"application/ld+json\"]')].map(s => s.textContent)")
    out = []
    for r in raw:
        try:
            d = json.loads(r)
        except Exception:
            continue
        if isinstance(d, dict) and "@graph" in d:
            out.extend(d["@graph"])
        elif isinstance(d, list):
            out.extend(d)
        else:
            out.append(d)
    return out


async def next_data(page: Page) -> dict | None:
    raw = await page.evaluate("() => { const n = document.querySelector('#__NEXT_DATA__'); return n ? n.textContent : null }")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def walk(obj, pred, depth=0, max_depth=12):
    """Yield every dict inside obj for which pred(dict) is True."""
    if depth > max_depth:
        return
    if isinstance(obj, dict):
        try:
            if pred(obj):
                yield obj
        except Exception:
            pass
        for v in obj.values():
            yield from walk(v, pred, depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, pred, depth + 1, max_depth)


def strip_html(s: str | None) -> str | None:
    if not s:
        return s
    s = re.sub(r"<(br|/p|/li|/div)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<li[^>]*>", "- ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmlmod.unescape(s).replace("\u200b", "")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", s)).strip()


def money(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return f"${v:,.0f}" if v else None
    s = str(v).strip()
    m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([MK])?", s)
    if not m:
        return s
    n = float(m.group(1).replace(",", ""))
    if m.group(2) == "M":
        n *= 1e6
    elif m.group(2) == "K":
        n *= 1e3
    return f"${n:,.0f}" if n else None


def num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?[\d,]*\.?\d+", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def intish(v) -> str | None:
    n = num(v)
    if n is None:
        return None
    return f"{int(n):,}" if n == int(n) else f"{n:,}"


# Label -> canonical field, used to scrape "Label  Value" facts tables from page text.
LABELS: list[tuple[str, str]] = [
    (r"(?:asking|sale|list(?:ing)?)\s*price|^price", "list_price"),
    (r"no\.? of units|number of units|# of units|^units|unit count|total units", "units"),
    (r"unit mix", "unit_mix"),
    (r"price per unit|price/unit", "price_per_unit"),
    (r"price per (?:sf|sq\.? ?ft)|price/sf", "price_per_sf"),
    (r"cap rate", "cap_rate"),
    (r"\bnoi\b|net operating income", "noi"),
    (r"gross (?:income|rent|scheduled income)|gross operating income|effective gross income", "gross_income"),
    (r"operating expenses|total expenses|expenses", "expenses"),
    (r"(?:building|rentable building area|gross building area|property size|building size|square footage|living sq\.? ?ft|living area|total sf)", "building_sf"),
    (r"lot size|land area|land acres|lot area|acreage", "lot_size"),
    (r"year built(?:/renovated)?|built in", "year_built"),
    (r"zoning(?: code)?", "zoning"),
    (r"parking", "parking"),
    (r"(?:annual )?(?:real estate )?taxes|tax amount|property tax", "taxes"),
    (r"assess(?:ed|ment)(?: value)?", "assessment"),
    (r"property type|property subtype|building class|sub type", "property_type"),
    (r"no\.? stories|stories|# of stories", "stories"),
    (r"apn|parcel(?: number| id)?", "apn"),
    (r"heating(?: type)?|utilities|metering", "heating_utilities"),
    (r"construction(?: type)?", "construction"),
    (r"days on market|time on market", "days_on_market"),
    (r"date listed|listed on|list date|on market date", "list_date"),
    (r"(?:sale|listing) (?:type|status)|^status", "status"),
    (r"auction (?:date|start|end)|bidding", "auction"),
]


def facts_from_text(text: str, labels=LABELS) -> dict[str, str]:
    """Scrape 'Label\\tValue' or 'Label\\nValue' pairs (typical facts tables) out of innerText."""
    out: dict[str, str] = {}
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        if not line or len(line) > 60:
            continue
        label, value = line, None
        if "\t" in line:
            label, value = [x.strip() for x in line.split("\t", 1)]
        elif ":" in line and len(line.split(":", 1)[1].strip()) > 0:
            label, value = [x.strip() for x in line.split(":", 1)]
        else:
            # value on next non-empty line
            for j in range(i + 1, min(i + 3, len(lines))):
                if lines[j]:
                    value = lines[j]
                    break
        if not value or len(value) > 120:
            continue
        lab = label.lower().strip(" :")
        for pat, key in labels:
            if re.fullmatch(f"(?:{pat})", lab, flags=re.I) or (len(lab) < 30 and re.match(f"^(?:{pat})$", lab, flags=re.I)):
                if key not in out:
                    out[key] = value
                break
    return out
