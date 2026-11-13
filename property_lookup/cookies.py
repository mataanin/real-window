"""Optional fallback: load per-domain cookie files (Chrome-extension JSON export or Netscape cookies.txt).

Primary path is agentcookie live CDP injection into the shared Chrome (see SETUP_AGENTCOOKIE.md);
this loader is only used with --cookie-files.
"""
from __future__ import annotations
import json
from pathlib import Path

SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict", "unspecified": None}


def _from_json(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("cookies", [])
    out = []
    for c in data:
        name, value = c.get("name"), c.get("value")
        domain = c.get("domain") or c.get("host") or c.get("host_key")
        if not name or domain is None:
            continue
        ck = {"name": name, "value": value or "", "domain": domain, "path": c.get("path") or "/",
              "secure": bool(c.get("secure", False)), "httpOnly": bool(c.get("httpOnly", c.get("httponly", False)))}
        exp = c.get("expirationDate", c.get("expires", c.get("expiry")))
        if exp not in (None, "", -1) and not c.get("session", False):
            try:
                ck["expires"] = float(exp)
            except (TypeError, ValueError):
                pass
        ss = c.get("sameSite")
        if isinstance(ss, str):
            ss = SAMESITE.get(ss.lower(), ss.capitalize())
            if ss in ("None", "Lax", "Strict"):
                ck["sameSite"] = ss
                if ss == "None":
                    ck["secure"] = True
        out.append(ck)
    return out


def _from_netscape(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only, line = True, line[len("#HttpOnly_"):]
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        ck = {"name": name, "value": value, "domain": domain, "path": path or "/",
              "secure": secure.upper() == "TRUE", "httpOnly": http_only}
        try:
            e = float(expires)
            if e > 0:
                ck["expires"] = e
        except ValueError:
            pass
        out.append(ck)
    return out


def load_cookie_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    if text[0] in "[{":
        return _from_json(json.loads(text))
    return _from_netscape(text)


def load_cookie_dir(cookie_dir: Path, domains: list[str] | None = None) -> list[dict]:
    cookies: list[dict] = []
    if not cookie_dir.is_dir():
        return cookies
    for p in sorted(cookie_dir.iterdir()):
        if p.suffix not in (".json", ".txt") or p.name.startswith("."):
            continue
        dom = p.stem
        if domains and not any(dom.endswith(d) or d.endswith(dom) for d in domains):
            continue
        try:
            cookies.extend(load_cookie_file(p))
        except Exception as e:  # keep going on a bad file
            print(f"[cookies] skip {p.name}: {e}")
    return cookies
