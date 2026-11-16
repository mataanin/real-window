"""Browserbase (hosted Chrome) provider.

Each lookup runs in a fresh Browserbase session attached to one persistent Context, so cookies from
logins (live view or imported) survive across sessions. Sessions are created with every anti-bot
option the account allows: Verified browser (Scale plan), residential proxies pinned to a US
state, CAPTCHA solving, and ad blocking.

Configuration is environment-only; see `BrowserbaseConfig.from_env`.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

API_BASE = os.environ.get("BROWSERBASE_API_BASE", "https://api.browserbase.com/v1")
SESSION_DASHBOARD = "https://www.browserbase.com/sessions/{id}"

# Browserbase saves a persisted Context when the session closes; reusing it immediately can
# start from the previous state.
CONTEXT_SETTLE_S = 5.0
_last_release = 0.0


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def provider() -> str:
    """'browserbase' or 'local'. BROWSER_PROVIDER wins; otherwise an API key selects Browserbase."""
    p = (os.environ.get("BROWSER_PROVIDER") or "").strip().lower()
    if p in ("browserbase", "local"):
        return p
    return "browserbase" if os.environ.get("BROWSERBASE_API_KEY") else "local"


class BrowserbaseError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


@dataclass
class BrowserbaseConfig:
    api_key: str
    project_id: str | None = None
    context_id: str | None = None
    context_file: Path = Path("data/browserbase_context.json")
    verified: bool = True
    os_name: str | None = None
    solve_captchas: bool = True
    block_ads: bool = True
    proxies: bool = True
    proxy_country: str = "US"
    proxy_state: str | None = "CT"
    proxy_city: str | None = None
    external_proxy: str | None = None
    external_proxy_username: str | None = None
    external_proxy_password: str | None = None
    region: str = "us-east-1"
    record_session: bool = True
    log_session: bool = True
    timeout_s: int = 1800
    captcha_wait_s: int = 45
    viewport: tuple[int, int] | None = (1440, 900)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "BrowserbaseConfig":
        key = os.environ.get("BROWSERBASE_API_KEY", "")
        if not key:
            raise BrowserbaseError("BROWSERBASE_API_KEY is not set")
        default_ctx_file = Path(os.environ.get("DB_PATH", "data/property_lookup.db")).parent / "browserbase_context.json"
        vp = os.environ.get("BROWSERBASE_VIEWPORT", "1440x900").lower()
        viewport = None
        if "x" in vp:
            try:
                w, h = vp.split("x", 1)
                viewport = (int(w), int(h))
            except ValueError:
                viewport = None
        return cls(
            api_key=key,
            project_id=os.environ.get("BROWSERBASE_PROJECT_ID") or None,
            context_id=os.environ.get("BROWSERBASE_CONTEXT_ID") or None,
            context_file=Path(os.environ.get("BROWSERBASE_CONTEXT_FILE") or default_ctx_file),
            verified=_flag("BROWSERBASE_VERIFIED", _flag("BROWSERBASE_ADVANCED_STEALTH", True)),
            os_name=os.environ.get("BROWSERBASE_OS") or None,
            solve_captchas=_flag("BROWSERBASE_SOLVE_CAPTCHAS", True),
            block_ads=_flag("BROWSERBASE_BLOCK_ADS", True),
            proxies=_flag("BROWSERBASE_PROXIES", True),
            proxy_country=(os.environ.get("BROWSERBASE_PROXY_COUNTRY") or "US").upper(),
            proxy_state=(os.environ.get("BROWSERBASE_PROXY_STATE", "CT") or None),
            proxy_city=os.environ.get("BROWSERBASE_PROXY_CITY") or None,
            external_proxy=os.environ.get("BROWSERBASE_EXTERNAL_PROXY") or None,
            external_proxy_username=os.environ.get("BROWSERBASE_EXTERNAL_PROXY_USERNAME") or None,
            external_proxy_password=os.environ.get("BROWSERBASE_EXTERNAL_PROXY_PASSWORD") or None,
            region=os.environ.get("BROWSERBASE_REGION") or "us-east-1",
            record_session=_flag("BROWSERBASE_RECORD_SESSION", True),
            log_session=_flag("BROWSERBASE_LOG_SESSION", True),
            timeout_s=_int("BROWSERBASE_SESSION_TIMEOUT", 1800),
            captcha_wait_s=_int("BROWSERBASE_CAPTCHA_WAIT", 45),
            viewport=viewport,
        )

    def summary(self) -> dict:
        """Non-secret view of the effective configuration."""
        return {
            "project_id": self.project_id,
            "context_id": self.context_id or _read_context_file(self.context_file),
            "verified": self.verified,
            "os": self.os_name,
            "solve_captchas": self.solve_captchas,
            "block_ads": self.block_ads,
            "proxies": self._proxy_summary(),
            "region": self.region,
            "record_session": self.record_session,
            "session_timeout_s": self.timeout_s,
            "captcha_wait_s": self.captcha_wait_s,
            "warnings": list(self.warnings),
        }

    def _proxy_summary(self) -> str:
        if self.external_proxy:
            return "external"
        if not self.proxies:
            return "off"
        geo = "/".join(x for x in (self.proxy_country, self.proxy_state, self.proxy_city) if x)
        return f"browserbase residential ({geo})"


def proxies_payload(cfg: BrowserbaseConfig):
    if cfg.external_proxy:
        p = {"type": "external", "server": cfg.external_proxy}
        if cfg.external_proxy_username:
            p["username"] = cfg.external_proxy_username
        if cfg.external_proxy_password:
            p["password"] = cfg.external_proxy_password
        return [p]
    if not cfg.proxies:
        return None
    geo: dict = {"country": cfg.proxy_country}
    if cfg.proxy_state and cfg.proxy_country == "US":
        geo["state"] = cfg.proxy_state.upper()
    if cfg.proxy_city:
        geo["city"] = cfg.proxy_city
    return [{"type": "browserbase", "geolocation": geo}]


def session_payload(cfg: BrowserbaseConfig, context_id: str | None, persist: bool = True,
                    purpose: str = "lookup", timeout_s: int | None = None) -> dict:
    bs: dict = {
        "solveCaptchas": cfg.solve_captchas,
        "blockAds": cfg.block_ads,
        "recordSession": cfg.record_session,
        "logSession": cfg.log_session,
    }
    if context_id:
        bs["context"] = {"id": context_id, "persist": persist}
    if cfg.verified:
        # Verified ships a complete fingerprint; overriding the viewport would break it.
        bs["verified"] = True
        if cfg.os_name:
            bs["os"] = cfg.os_name
    elif cfg.viewport:
        bs["viewport"] = {"width": cfg.viewport[0], "height": cfg.viewport[1]}
    body: dict = {
        "browserSettings": bs,
        "region": cfg.region,
        "timeout": max(60, min(21600, timeout_s or cfg.timeout_s)),
        "userMetadata": {"app": "property-lookup", "purpose": purpose},
    }
    if cfg.project_id:
        body["projectId"] = cfg.project_id
    px = proxies_payload(cfg)
    if px:
        body["proxies"] = px
    return body


def _read_context_file(path: Path) -> str | None:
    try:
        return json.loads(path.read_text()).get("context_id") or None
    except Exception:
        return None


class BrowserbaseClient:
    def __init__(self, cfg: BrowserbaseConfig):
        self.cfg = cfg
        self.http = requests.Session()
        self.http.headers.update({"X-BB-API-Key": cfg.api_key, "Content-Type": "application/json"})

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        r = self.http.request(method, f"{API_BASE}{path}", json=body, timeout=60)
        if r.status_code >= 400:
            raise BrowserbaseError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:400]}", r.status_code)
        return r.json() if r.content else {}

    def ensure_context(self) -> str:
        if self.cfg.context_id:
            return self.cfg.context_id
        cid = _read_context_file(self.cfg.context_file)
        if not cid:
            body = {"projectId": self.cfg.project_id} if self.cfg.project_id else {}
            cid = self._req("POST", "/contexts", body)["id"]
            self.cfg.context_file.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.context_file.write_text(json.dumps({"context_id": cid, "created_at": time.time()}))
        self.cfg.context_id = cid
        return cid

    def create_session(self, persist: bool = True, purpose: str = "lookup", timeout_s: int | None = None) -> dict:
        ctx = self.ensure_context()
        body = session_payload(self.cfg, ctx, persist=persist, purpose=purpose, timeout_s=timeout_s)
        try:
            return self._req("POST", "/sessions", body)
        except BrowserbaseError as e:
            # Verified needs the Scale plan; fall back to the standard browser rather than fail.
            if not self.cfg.verified or e.status not in (400, 402, 403, 422):
                raise
            self.cfg.verified = False
            msg = f"Verified browser rejected, using standard stealth: {str(e)[:200]}"
            if msg not in self.cfg.warnings:
                self.cfg.warnings.append(msg)
            print(f"[browserbase] {msg}")
            return self._req("POST", "/sessions",
                             session_payload(self.cfg, ctx, persist=persist, purpose=purpose, timeout_s=timeout_s))

    def live_urls(self, session_id: str) -> dict:
        return self._req("GET", f"/sessions/{session_id}/debug")

    def release(self, session_id: str) -> None:
        global _last_release
        body = {"status": "REQUEST_RELEASE"}
        if self.cfg.project_id:
            body["projectId"] = self.cfg.project_id
        try:
            self._req("POST", f"/sessions/{session_id}", body)
        finally:
            _last_release = time.time()


async def wait_for_context_settle() -> None:
    gap = CONTEXT_SETTLE_S - (time.time() - _last_release)
    if gap > 0:
        await asyncio.sleep(gap)
