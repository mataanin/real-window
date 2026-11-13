"""Address parsing, normalization, fuzzy matching and (free) Census geocoding."""
from __future__ import annotations
import math
import re
from dataclasses import dataclass, field

import requests
from rapidfuzz import fuzz

SUFFIX = {
    "street": "st", "str": "st", "st": "st", "avenue": "ave", "av": "ave", "ave": "ave", "aven": "ave",
    "road": "rd", "rd": "rd", "drive": "dr", "dr": "dr", "lane": "ln", "ln": "ln", "court": "ct", "ct": "ct",
    "place": "pl", "pl": "pl", "boulevard": "blvd", "blvd": "blvd", "terrace": "ter", "ter": "ter",
    "circle": "cir", "cir": "cir", "highway": "hwy", "hwy": "hwy", "turnpike": "tpke", "tpke": "tpke", "tpk": "tpke",
    "parkway": "pkwy", "pkwy": "pkwy", "square": "sq", "sq": "sq", "way": "way", "trail": "trl", "trl": "trl",
    "extension": "ext", "ext": "ext", "hill": "hl", "heights": "hts", "point": "pt", "ridge": "rdg",
    "crossing": "xing", "path": "path", "row": "row", "walk": "walk", "alley": "aly", "green": "grn",
}
DIRS = {"north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw",
        "southeast": "se", "southwest": "sw", "n": "n", "s": "s", "e": "e", "w": "w"}
UNIT_RE = re.compile(r"\s*(?:#|\b(?:apt|apartment|unit|ste|suite|fl|floor|bldg|building|rm)\b\.?)\s*([\w-]+)\s*$", re.I)
STATES = {"CT", "NY", "MA", "RI", "NJ", "NH", "VT", "ME", "PA"}  # only used as hints


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.,]", " ", s.lower())).strip()


def norm_street(street: str) -> str:
    """'48 Amsterdam Avenue' -> 'amsterdam ave' (number stripped, suffix/direction normalized)."""
    toks = _clean(street).split()
    while toks and re.match(r"^\d+[a-z]?(-\d+[a-z]?)?$|^&$|^and$", toks[0]):
        toks = toks[1:]
    out = []
    for i, t in enumerate(toks):
        if i == len(toks) - 1 and t in SUFFIX:
            out.append(SUFFIX[t])
        elif t in DIRS and (i == 0 or i == len(toks) - 1):
            out.append(DIRS[t])
        else:
            out.append(SUFFIX.get(t, t) if i == len(toks) - 1 else t)
    return " ".join(out)


def street_numbers(street: str) -> set[int]:
    """Numbers a street string covers: '613-615 Main' -> {613..615}; '53 & 59 N Main' -> {53, 59}."""
    s = _clean(street)
    m = re.match(r"^((?:\d+[a-z]?\s*(?:-|&|and|,)\s*)*\d+[a-z]?)\b", s)
    if not m:
        return set()
    nums = [int(re.sub(r"\D", "", x)) for x in re.findall(r"\d+[a-z]?", m.group(1))]
    out = set(nums)
    rng = re.match(r"^(\d+)[a-z]?\s*-\s*(\d+)", s)
    if rng:
        a, b = int(rng.group(1)), int(rng.group(2))
        if b < a and len(str(b)) < len(str(a)):  # '613-15' -> 615
            b = int(str(a)[: len(str(a)) - len(str(b))] + str(b))
        if 0 <= b - a <= 60:
            out |= set(range(a, b + 1))
    return out


@dataclass
class Address:
    raw: str
    number: str = ""
    street: str = ""          # e.g. "Amsterdam Ave" as typed
    unit: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    town: str = ""            # CT municipality from Census county subdivision (may differ from postal city)
    lat: float | None = None
    lon: float | None = None
    geocoded: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def line1(self) -> str:
        s = f"{self.number} {self.street}".strip()
        return f"{s} #{self.unit}" if self.unit else s

    @property
    def one_line(self) -> str:
        parts = [self.line1, self.city, f"{self.state} {self.zip}".strip()]
        return ", ".join(p for p in parts if p)

    @property
    def street_norm(self) -> str:
        return norm_street(self.street)

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.one_line.lower()).strip("-")

    @property
    def search_city(self) -> str:
        return self.city or self.town


def parse_address(raw: str) -> Address:
    a = Address(raw=raw.strip())
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return a
    # "800 Seaview Ave, Unit 770-5, Bridgeport, CT" -> fold the unit part into line 1
    if len(parts) > 1 and UNIT_RE.match(" " + parts[1]) and re.match(r"^\s*(#|apt|apartment|unit|ste|suite|fl|floor|bldg|building|rm)\b", parts[1], re.I):
        parts = [parts[0] + " " + parts[1]] + parts[2:]
    # No commas: split "18 Maple St Trumbull CT 06611" after the last street suffix
    if len(parts) == 1:
        m = re.match(r"^(.*?)\s+([A-Za-z]{2})\s*(\d{5})?(?:-\d{4})?$", parts[0])
        if m and m.group(2).upper() in STATES | {"CA", "TX", "FL", "IL", "GA", "AZ", "WA", "OR", "CO", "NC", "SC", "VA", "MD", "DE", "OH", "MI"}:
            body, st, z = m.group(1), m.group(2), m.group(3) or ""
            toks = body.split()
            idx = max((i for i, t in enumerate(toks) if t.lower().strip(".") in SUFFIX and i > 0), default=None)
            if idx is not None and idx < len(toks) - 1:
                parts = [" ".join(toks[: idx + 1]), " ".join(toks[idx + 1:]), f"{st} {z}".strip()]
    line1 = parts[0]
    um = UNIT_RE.search(line1)
    if um:
        a.unit = um.group(1)
        line1 = line1[: um.start()].strip()
    m = re.match(r"^(\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?)\s+(.*)$", line1)
    if m:
        a.number, a.street = m.group(1).replace(" ", ""), m.group(2)
    else:
        a.street = line1
    rest = parts[1:]
    if rest:
        last = rest[-1]
        sm = re.match(r"^([A-Za-z]{2})\s*(\d{5})?(?:-\d{4})?$", last)
        if sm:
            a.state = sm.group(1).upper()
            a.zip = sm.group(2) or ""
            rest = rest[:-1]
        else:
            sm2 = re.match(r"^(.*?)\s+([A-Za-z]{2})\s*(\d{5})?(?:-\d{4})?$", last)
            if sm2 and len(rest) == 1:
                a.city, a.state, a.zip = sm2.group(1), sm2.group(2).upper(), sm2.group(3) or ""
                rest = []
        if rest:
            a.city = rest[0]
    return a


def geocode(a: Address, timeout: float = 15) -> Address:
    """Fill lat/lon, zip, postal city and CT town via the free US Census geocoder. Best effort."""
    try:
        q = f"{a.number} {a.street}, {a.city}, {a.state} {a.zip}".strip()
        r = requests.get(
            "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress",
            params={"address": q, "benchmark": "Public_AR_Current", "vintage": "Current_Current",
                    "layers": "County Subdivisions,Counties", "format": "json"},
            timeout=timeout)
        matches = r.json()["result"]["addressMatches"]
        if not matches:
            return a
        m = matches[0]
        a.lon, a.lat = m["coordinates"]["x"], m["coordinates"]["y"]
        comp = m.get("addressComponents", {})
        a.zip = a.zip or comp.get("zip", "")
        a.state = a.state or comp.get("state", "")
        if not a.city and comp.get("city"):
            a.city = comp["city"].title()
        geos = m.get("geographies", {})
        cs = geos.get("County Subdivisions") or []
        if cs:
            a.town = re.sub(r"\s+(town|city|borough)$", "", cs[0].get("NAME", ""), flags=re.I)
        co = geos.get("Counties") or []
        if co:
            a.extra["county"] = co[0].get("NAME")
        a.extra["census_matched"] = m.get("matchedAddress")
        a.geocoded = True
    except Exception as e:
        a.extra["geocode_error"] = str(e)[:200]
    return a


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def match_score(target: Address, candidate_text: str, cand_city: str | None = None) -> float:
    """0..100 confidence that candidate_text (a street line or full address) is the target property.

    Requires the street number to match (ranges like 613-615 accepted) and the street name to be similar.
    """
    if not candidate_text:
        return 0.0
    cand = candidate_text.split(",")[0]
    cand = UNIT_RE.sub("", cand).strip()
    # handle "Main Street Apartments | 613 Main Street" style strings: find 'num street' inside
    m = re.search(r"(\d+[A-Za-z]?(?:\s*(?:-|&|and)\s*\d+[A-Za-z]?)*\s+[A-Za-z0-9 .'-]+)", cand)
    if m:
        cand = m.group(1)
    # drop anything after the street suffix: "86 Whittier Street Bridgeport Connecticut" -> "86 Whittier Street"
    toks = cand.split()
    for i, t in enumerate(toks[1:], start=1):
        if t.lower().strip(".") in SUFFIX and i >= 2:
            nxt = toks[i + 1].lower().strip(".") if i + 1 < len(toks) else ""
            cand = " ".join(toks[: i + 1] + ([toks[i + 1]] if nxt in DIRS else []))
            break
    tnum = int(re.sub(r"\D", "", target.number) or -1) if target.number else -1
    nums = street_numbers(cand)
    if tnum < 0 or tnum not in nums:
        # target itself may be a range ("613-615 Main St")
        tnums = street_numbers(target.number + " x") if target.number else set()
        if not (tnums & nums):
            return 0.0
    cs, ts = norm_street(cand), target.street_norm
    if not cs or not ts:
        return 0.0
    # compare without suffix too (some sites drop 'St')
    def core(s):
        t = s.split()
        return " ".join(t[:-1]) if len(t) > 1 and t[-1] in SUFFIX.values() else s
    score = max(fuzz.ratio(cs, ts), fuzz.ratio(core(cs), core(ts)) - 3)
    if cand_city and target.search_city:
        cc = _clean(cand_city)
        if cc and cc not in {_clean(target.city), _clean(target.town)} and fuzz.ratio(cc, _clean(target.search_city)) < 80:
            score -= 25
    return float(score)


def has_unit(text: str) -> bool:
    return bool(UNIT_RE.search(text.split(",")[0]))


def is_match(target: Address, candidate_text: str, cand_city: str | None = None, threshold: float = 85) -> bool:
    return match_score(target, candidate_text, cand_city) >= threshold


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
