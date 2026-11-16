"""Tests for the Browserbase provider (no network: HTTP and pages are faked)."""
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from property_lookup import browserbase as bb
from property_lookup.browser import BrowserSession


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("BROWSERBASE_") or k == "BROWSER_PROVIDER":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(bb, "_last_release", 0.0)


def cfg(**kw):
    base = dict(api_key="k", project_id="proj")
    base.update(kw)
    return bb.BrowserbaseConfig(**base)


class TestProvider:
    def test_local_without_key(self):
        assert bb.provider() == "local"

    def test_key_selects_browserbase(self, monkeypatch):
        monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
        assert bb.provider() == "browserbase"

    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
        monkeypatch.setenv("BROWSER_PROVIDER", "local")
        assert bb.provider() == "local"


class TestConfigFromEnv:
    def test_requires_key(self):
        with pytest.raises(bb.BrowserbaseError):
            bb.BrowserbaseConfig.from_env()

    def test_defaults_enable_everything(self, monkeypatch):
        monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
        c = bb.BrowserbaseConfig.from_env()
        assert c.verified and c.solve_captchas and c.block_ads and c.proxies
        assert (c.proxy_country, c.proxy_state, c.region) == ("US", "CT", "us-east-1")

    def test_overrides(self, monkeypatch):
        monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
        monkeypatch.setenv("BROWSERBASE_VERIFIED", "0")
        monkeypatch.setenv("BROWSERBASE_SOLVE_CAPTCHAS", "false")
        monkeypatch.setenv("BROWSERBASE_PROXY_STATE", "")
        monkeypatch.setenv("BROWSERBASE_PROXY_CITY", "Hartford")
        monkeypatch.setenv("BROWSERBASE_VIEWPORT", "1280x800")
        c = bb.BrowserbaseConfig.from_env()
        assert not c.verified and not c.solve_captchas
        assert c.proxy_state is None and c.proxy_city == "Hartford"
        assert c.viewport == (1280, 800)

    def test_context_file_next_to_db(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
        monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "x.db"))
        assert bb.BrowserbaseConfig.from_env().context_file == tmp_path / "data" / "browserbase_context.json"


class TestSessionPayload:
    def test_verified_with_residential_ct_proxy(self):
        body = bb.session_payload(cfg(), "ctx-1")
        s = body["browserSettings"]
        assert s["verified"] is True and "viewport" not in s
        assert s["solveCaptchas"] is True and s["blockAds"] is True
        assert s["context"] == {"id": "ctx-1", "persist": True}
        assert body["proxies"] == [{"type": "browserbase", "geolocation": {"country": "US", "state": "CT"}}]
        assert body["projectId"] == "proj" and body["region"] == "us-east-1"

    def test_standard_browser_uses_viewport_and_no_os(self):
        s = bb.session_payload(cfg(verified=False, os_name="mac"), None)["browserSettings"]
        assert "verified" not in s and "os" not in s and "context" not in s
        assert s["viewport"] == {"width": 1440, "height": 900}

    def test_verified_os(self):
        assert bb.session_payload(cfg(os_name="mac"), None)["browserSettings"]["os"] == "mac"

    def test_external_proxy(self):
        body = bb.session_payload(cfg(external_proxy="http://p:8080", external_proxy_username="u",
                                      external_proxy_password="pw"), None)
        assert body["proxies"] == [{"type": "external", "server": "http://p:8080", "username": "u", "password": "pw"}]

    def test_proxies_off(self):
        assert "proxies" not in bb.session_payload(cfg(proxies=False), None)

    def test_state_only_for_us(self):
        body = bb.session_payload(cfg(proxy_country="GB"), None)
        assert body["proxies"][0]["geolocation"] == {"country": "GB"}

    def test_timeout_clamped(self):
        assert bb.session_payload(cfg(timeout_s=10), None)["timeout"] == 60
        assert bb.session_payload(cfg(), None, timeout_s=99999)["timeout"] == 21600


class TestClient_:
    def test_context_created_once_and_persisted(self, tmp_path):
        c = cfg(context_file=tmp_path / "ctx.json")
        client = bb.BrowserbaseClient(c)
        client._req = Mock(return_value={"id": "ctx-new"})
        assert client.ensure_context() == "ctx-new"
        assert json.loads((tmp_path / "ctx.json").read_text())["context_id"] == "ctx-new"

        c2 = cfg(context_file=tmp_path / "ctx.json")
        client2 = bb.BrowserbaseClient(c2)
        client2._req = Mock()
        assert client2.ensure_context() == "ctx-new"
        client2._req.assert_not_called()

    def test_verified_rejected_falls_back(self, tmp_path):
        c = cfg(context_id="ctx")
        client = bb.BrowserbaseClient(c)
        calls = []

        def fake_req(method, path, body=None):
            calls.append(body)
            if body["browserSettings"].get("verified"):
                raise bb.BrowserbaseError("plan", 402)
            return {"id": "s1", "connectUrl": "wss://x"}

        client._req = fake_req
        assert client.create_session()["id"] == "s1"
        assert len(calls) == 2 and "verified" not in calls[1]["browserSettings"]
        assert not c.verified and c.warnings

    def test_auth_error_not_masked(self):
        client = bb.BrowserbaseClient(cfg(context_id="ctx"))
        client._req = Mock(side_effect=bb.BrowserbaseError("bad key", 401))
        with pytest.raises(bb.BrowserbaseError):
            client.create_session()
        assert client._req.call_count == 1


class FakePage:
    pass


class TestCaptchaWait:
    @pytest.fixture(autouse=True)
    def fast_sleep(self, monkeypatch):
        import asyncio
        real_sleep = asyncio.sleep
        clock = {"t": 0.0}

        async def fake_sleep(s):
            clock["t"] += s
            await real_sleep(0)

        class Loop:
            def time(self):
                return clock["t"]

        monkeypatch.setattr("property_lookup.browser.asyncio.sleep", fake_sleep)
        monkeypatch.setattr("property_lookup.browser.asyncio.get_event_loop", lambda: Loop())

    def session(self):
        s = BrowserSession(mode="browserbase", bb_config=cfg(captcha_wait_s=30))
        return s

    async def test_returns_true_when_solver_clears_block(self):
        s, page = self.session(), FakePage()
        s._solver[id(page)] = "finished"
        detect = AsyncMock(side_effect=["captcha", None])
        with patch("property_lookup.browser.detect_block", detect):
            assert await s.wait_for_captcha(page) is True
        assert detect.call_count == 2

    async def test_gives_up_when_solver_never_engages(self):
        s, page = self.session(), FakePage()
        with patch("property_lookup.browser.detect_block", AsyncMock(return_value="captcha")):
            assert await s.wait_for_captcha(page) is False

    async def test_waits_while_solving_then_deadline(self):
        s, page = self.session(), FakePage()
        s._solver[id(page)] = "started"
        detect = AsyncMock(return_value="captcha")
        with patch("property_lookup.browser.detect_block", detect):
            assert await s.wait_for_captcha(page) is False
        detect.assert_not_called()

    def test_local_modes_do_not_solve(self):
        assert BrowserSession(mode="cdp").solves_captchas is False
        assert BrowserSession(mode="browserbase", bb_config=cfg(solve_captchas=False)).solves_captchas is False


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    from property_lookup.api.app import create_app
    with patch("property_lookup.api.app.Database") as dbc, patch("property_lookup.api.app.JobQueue") as qc:
        db = AsyncMock()
        dbc.return_value = db
        q = AsyncMock()
        q.queue_size = Mock(return_value=0)
        q.active_count = Mock(return_value=0)
        q.browser_info = Mock(return_value={"provider": "browserbase"})
        q.import_cookies = AsyncMock(side_effect=lambda cookies: len(cookies))
        q.finish_login = AsyncMock(return_value=None)
        qc.return_value = q
        app = create_app()
        with TestClient(app) as c:
            c.queue = q
            yield c


class TestBrowserEndpoints:
    def test_health_reports_browserbase(self, api, monkeypatch):
        monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
        data = api.get("/health").json()
        assert data["browser_provider"] == "browserbase" and data["cdp_connection"] == "browserbase"

    def test_browser_info(self, api):
        assert api.get("/browser").json() == {"provider": "browserbase"}

    def test_cookie_import_filters_to_supported_sites(self, api):
        cookies = [
            {"domain": ".crexi.com", "name": "s", "value": "1"},
            {"domain": "www.loopnet.com", "name": "a", "value": "2"},
            {"domain": ".mybank.com", "name": "x", "value": "3"},
        ]
        r = api.post("/browser/cookies", json=cookies)
        assert r.status_code == 200 and r.json() == {"received": 2, "imported": 2}
        sent = api.queue.import_cookies.call_args.args[0]
        assert {c["domain"] for c in sent} == {".crexi.com", "www.loopnet.com"}

    def test_cookie_import_all_domains(self, api):
        r = api.post("/browser/cookies?all_domains=true",
                     json={"cookies": [{"domain": ".mybank.com", "name": "x", "value": "3"}]})
        assert r.json()["imported"] == 1

    def test_cookie_import_rejects_nothing_usable(self, api):
        r = api.post("/browser/cookies", json=[{"domain": ".mybank.com", "name": "x", "value": "3"}])
        assert r.status_code == 400

    def test_finish_login_without_session(self, api):
        assert api.post("/browser/login/finish").status_code == 404

    def test_login_opens_site_home_pages(self, api):
        api.queue.start_login = AsyncMock(return_value={
            "session_id": "s1", "live_view_url": "https://live", "_sess": object()})
        r = api.post("/browser/login", json={"sites": ["crexi", "nope"]})
        assert r.status_code == 200 and r.json() == {"session_id": "s1", "live_view_url": "https://live"}
        urls = api.queue.start_login.call_args.args[0]
        assert len(urls) == 1 and "crexi.com" in urls[0]

    def test_login_rejected_in_local_mode(self, api):
        api.queue.start_login = AsyncMock(side_effect=RuntimeError("login sessions need BROWSER_PROVIDER=browserbase"))
        assert api.post("/browser/login", json={}).status_code == 400
