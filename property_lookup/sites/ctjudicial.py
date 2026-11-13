from __future__ import annotations
import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..address import Address, match_score
from ..models import SiteResult, FOUND, NOT_FOUND
from .base import SiteAdapter, RunCtx

BASE = "https://sso.eservices.jud.ct.gov/foreclosures/Public/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"}


class CTJudicial(SiteAdapter):
    """CT Judicial Branch pending foreclosure-by-sale (auction) notices. Public, no login, plain HTTP."""
    name, label = "ct_judicial", "CT Judicial foreclosure sales"
    domains = []
    home_url = BASE + "PendPostbyTownList.aspx"

    async def run(self, sess, addr, ctx):
        import time
        res = SiteResult(site=self.name)
        t0 = time.time()
        try:
            await self.lookup(None, addr, res, ctx)
        except Exception as e:
            res.status = "error"
            res.notes.append(f"{type(e).__name__}: {str(e)[:300]}")
        res.elapsed_s = round(time.time() - t0, 1)
        return res

    async def lookup(self, page, addr: Address, res: SiteResult, ctx: RunCtx):
        if addr.state and addr.state != "CT":
            res.status = NOT_FOUND
            res.notes.append("not a Connecticut address")
            return
        s = requests.Session()
        s.headers.update(UA)
        towns_html = s.get(self.home_url, timeout=30).text
        towns = {a.get_text(strip=True): a["href"] for a in BeautifulSoup(towns_html, "lxml").select("a[href*='PendPostbyTownDetails']")}
        cands = [t for t in (addr.town, addr.city) if t]
        town = next((t for t in towns if any(t.lower() == c.lower() for c in cands)), None)
        if not town:
            res.status = NOT_FOUND
            res.notes.append(f"no pending foreclosure sales listed for {' / '.join(cands)} ({len(towns)} towns have sales)")
            return
        url = BASE + towns[town].strip().replace(" ", "%20")
        soup = BeautifulSoup(s.get(url, timeout=30).text, "lxml")
        rows = soup.select("tr")
        hits = []
        for tr in rows:
            txt = tr.get_text(" ", strip=True)
            m = re.search(r"ADDRESS:\s*(.+?)(?:View Full Notice|$)", txt, re.I)
            if not m:
                continue
            a_txt = m.group(1)
            # "91 Success Ave. a/k/a 91 Court A, Building 20" -> try each alias
            score = max(match_score(addr, part) for part in re.split(r"\ba/k/a\b|\baka\b", a_txt, flags=re.I))
            if score >= 85:
                hits.append((tr, txt, a_txt))
        res.notes.append(f"checked {len(rows) - 1} pending sale notices in {town}")
        if not hits:
            res.status = NOT_FOUND
            res.url = url
            return
        tr, txt, a_txt = hits[0]
        res.status = FOUND
        res.matched_address = a_txt.strip()
        cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
        notice = next((a["href"] for a in tr.select("a[href*='PendPostDetailPublic']")), None)
        res.url = BASE + notice if notice else url
        sale_date = next((c for c in cells if re.search(r"\d{1,2}/\d{1,2}/\d{4}", c)), "")
        docket = next((c for c in cells if re.match(r"^[A-Z]{3}CV", c)), "")
        res.set("status", "pending foreclosure auction")
        auction = f"Foreclosure sale {sale_date}; docket {docket}"
        if notice:
            d = BeautifulSoup(s.get(BASE + notice, timeout=30).text, "lxml").get_text("\n", strip=True)
            res.description = d[:6000]
            def grab(label):
                m = re.search(label + r"\s*:?\s*\n([^\n]{1,200})", d, re.I)
                return m.group(1).strip() if m else None
            parts = {
                "sale date": grab(r"SALE DATE"), "time": grab(r"SALE TIME"), "inspection from": grab(r"INSPECTION COMMENCING AT"),
                "case": grab(r"Case Caption"), "docket": grab(r"Docket Number") or docket,
                "notice runs from": grab(r"NOTICE RUNS FROM"),
            }
            dep = re.search(r"(?:bank check|certified check)[^$]{0,120}(\$[\d,]+(?:\.\d\d)?)", d, re.I)
            if dep:
                parts["bidder deposit"] = dep.group(1)
            tm = re.search(r"FORECLOSURE SALE:\s*([^\n]{1,120})", d)
            if tm:
                kind = re.sub(r"\s*ADDRESS:.*", "", tm.group(1)).strip(" :")
                if kind:
                    parts["type"] = kind
                    res.set("property_type", kind)
            for lab in (r"appraised value", r"fair market value"):
                m = re.search(lab + r"[^$\n]{0,60}(\$[\d,]+)", d, re.I)
                if m:
                    parts[lab] = m.group(1)
            com = re.search(r"Committee:?\s*\n([^\n]{3,80})\n([^\n]{0,80}\d{3}[^\n]{0,40})?", d)
            if com:
                parts["committee"] = " ".join(x for x in com.groups() if x)
            auction = "Foreclosure auction " + "; ".join(f"{k}: {v}" for k, v in parts.items() if v)
        res.set("auction", auction[:900])
        res.raw_text_snippet = txt[:1500]
