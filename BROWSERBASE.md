# Running lookups in Browserbase

Set `BROWSERBASE_API_KEY` and the app stops using the local Chrome/Xvfb and runs every lookup in a
[Browserbase](https://www.browserbase.com) cloud browser instead. Nothing else changes in the API.

## What gets turned on

| Feature | Default | Env var | Notes |
|---|---|---|---|
| Verified browser (real fingerprints recognised by bot-protection vendors) | on | `BROWSERBASE_VERIFIED` | Scale plan only. If the API rejects it, the app falls back to the standard stealth browser and reports the reason under `GET /browser` → `warnings`. Replaces the deprecated `advancedStealth`. |
| Verified OS profile | Browserbase default (`windows`) | `BROWSERBASE_OS` | `windows`, `mac`, `linux`, `mobile`, `tablet`. Only valid with Verified; changing it on an existing Context can lower success rates. |
| Residential proxy | on, US / CT | `BROWSERBASE_PROXIES`, `BROWSERBASE_PROXY_COUNTRY`, `BROWSERBASE_PROXY_STATE`, `BROWSERBASE_PROXY_CITY` | Set `BROWSERBASE_PROXY_STATE=` (empty) for any US state. Proxy bandwidth is billed per GB above the plan allowance. |
| Your own proxy instead | off | `BROWSERBASE_EXTERNAL_PROXY` (+ `_USERNAME`, `_PASSWORD`) | e.g. a residential provider you already pay for. Overrides the Browserbase proxy. |
| CAPTCHA solving | on | `BROWSERBASE_SOLVE_CAPTCHAS` | When a page looks blocked the adapter waits up to `BROWSERBASE_CAPTCHA_WAIT` seconds (default 45) for the solver, and gives up after ~10 s if the solver never starts (unsupported challenge). |
| Ad blocking | on | `BROWSERBASE_BLOCK_ADS` | Faster pages, fewer third-party scripts. |
| Persistent Context (cookies, localStorage) | on | `BROWSERBASE_CONTEXT_ID` | Created on first use and remembered in `data/browserbase_context.json`. Every session saves back to it when released. |
| Session recording / logs | on | `BROWSERBASE_RECORD_SESSION`, `BROWSERBASE_LOG_SESSION` | Blocked or failed sites get a `browserbase replay: https://www.browserbase.com/sessions/<id>` note so you can watch what happened. |
| Region | `us-east-1` | `BROWSERBASE_REGION` | Closest to the CT proxies and the EC2 instance. |
| Session timeout | 1800 s | `BROWSERBASE_SESSION_TIMEOUT` | Hard cap per lookup session. |
| Viewport | 1440x900 | `BROWSERBASE_VIEWPORT` | Ignored with Verified (its fingerprint fixes the viewport). |

`BROWSER_PROVIDER=local` forces the local Chrome even when a key is set; `BROWSER_PROVIDER=browserbase`
forces Browserbase. `BROWSERBASE_PROJECT_ID` is optional (inferred from the key).

One Browserbase session runs at a time: each lookup opens a session on the shared Context and releases it
when done, which is when Browserbase saves the cookies. Running sessions in parallel on one Context
would race to save cookies and can get logins revoked.

## Setup on the EC2 instance

```bash
sudo -iu ubuntu
cd ~/property-lookup
cat >> .env <<'EOF'
BROWSERBASE_API_KEY=bb_live_...
BROWSERBASE_PROJECT_ID=...
EOF
bash scripts/deploy.sh
curl -s localhost:8000/health        # "browser_provider":"browserbase"
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/browser
```

Put the key in `.env` on the running instance. Setting the `browserbase_api_key` Terraform variable
also works for new instances, but it changes user_data, which **replaces** the existing instance.

## Getting logged-in cookies into Browserbase

No Tailscale or agentcookie needed. Either:

**A. Log in through the live view (recommended).** The session runs in the same Browserbase browser
and proxy that lookups use, so sites see a consistent device.

```bash
curl -s -X POST localhost:8000/browser/login -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"sites": ["crexi", "loopnet", "brevitas"], "minutes": 20}'
# -> {"session_id": "...", "live_view_url": "https://...", "pages": [...], "expires_at": "..."}
```

Open `live_view_url` in your browser, sign in to each site, then save:

```bash
curl -s -X POST localhost:8000/browser/login/finish -H "Authorization: Bearer $TOKEN"
```

Lookups wait while the login session is open; it is released automatically after `minutes`.

**B. Import cookies exported from your desktop browser.** Export with the Cookie-Editor or
EditThisCookie extension (JSON) on each site, then:

```bash
curl -s -X POST localhost:8000/browser/cookies -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data @crexi-cookies.json
# -> {"received": 12, "imported": 12}
```

Only cookies for the supported sites' domains are kept (add `?all_domains=true` to keep everything).
Login cookies transfer well; anti-bot cookies (Akamai `_abck`, PerimeterX `_px*`) are tied to the
browser that minted them and usually don't help.

Check the result with `GET /sessions` (opens one Browserbase session and visits each site's home page).

## CLI

```bash
BROWSERBASE_API_KEY=... python -m property_lookup "48 Amsterdam Ave, Bridgeport, CT 06606" --browserbase
```

## Limits worth knowing

- Verified is a Scale-plan feature; on lower plans you get Browserbase's standard stealth browser plus
  proxies and CAPTCHA solving.
- The CAPTCHA solver covers common challenge types; PerimeterX press-and-hold and some Akamai blocks
  may still end as `blocked`. Watch the replay link to see what the site showed.
- Using these features doesn't change the sites' terms of service; several of these sites prohibit
  automated access.
