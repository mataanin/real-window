# Cookies for property_lookup

## Primary path: agentcookie live CDP injection (recommended)

The tool attaches to one long-lived Chrome on this box (`./start_browser.sh`, CDP on
`127.0.0.1:9223`, profile `~/.property-lookup/chrome-profile`, virtual display `:99`).
agentcookie's Linux sink pushes your Mac's Chrome cookies straight into that Chrome over CDP,
so every run of `python -m property_lookup ...` sees your logged-in sessions with no files at all.
Setup: see `SETUP_AGENTCOOKIE.md`. Check state any time with:

    python -m property_lookup --check-sessions

## Fallback path: cookie files (`--cookie-files`)

Only used when you pass `--cookie-files` (works with both the CDP Chrome and `--launch`).
Put one file per site in `cookies/`, named after the registrable domain:

    cookies/loopnet.com.json      cookies/crexi.com.json      cookies/zillow.com.txt
    cookies/showcase.com.json     cookies/commercialcafe.com.json
    cookies/homes.com.json        cookies/brevitas.com.json   cookies/realtor.com.json
    cookies/redfin.com.json       cookies/commercialedge.com.json

Accepted formats (auto-detected by the first character):

1. **JSON** (Chrome extensions such as "EditThisCookie" / "Cookie-Editor", or a CDP
   `Network.getAllCookies` dump): a list of objects, or `{"cookies": [...]}`.
   Used keys: `name`, `value`, `domain` (or `host`/`host_key`), `path`, `secure`, `httpOnly`,
   `expirationDate` (or `expires`/`expiry`, unix seconds; omitted/`session: true` = session cookie),
   `sameSite` (`no_restriction`/`lax`/`strict`/`unspecified` or `None`/`Lax`/`Strict`).

       [{"domain": ".crexi.com", "name": "session", "value": "...", "path": "/",
         "secure": true, "httpOnly": true, "expirationDate": 1830000000, "sameSite": "lax"}]

2. **Netscape cookies.txt** (curl / yt-dlp / "Get cookies.txt"): tab-separated
   `domain  include_subdomains  path  secure  expires  name  value`, `#HttpOnly_` prefix honoured.

Files whose name doesn't end in `.json` or `.txt`, or that start with `.`, are ignored. A bad file is
skipped with a message; it never stops a run. Cookie values are never printed.

## Which domains matter

| Domain | Why |
|---|---|
| loopnet.com, showcase.com, homes.com | CoStar sites behind Akamai; a trusted `_abck`/`bm_sz` session plus login lowers blocks; LoopNet login unlocks full listing details |
| crexi.com | Units, year built, cap rate, NOI, OM vault are masked (`****`) for guests |
| zillow.com | PerimeterX (`_px3`, `_pxvid`, `_pxhd`) clearance; property pages are otherwise often a press-and-hold captcha |
| brevitas.com | "Private Listings" visible only when signed in |
| commercialcafe.com / commercialedge.com | Guest works; login only for saved searches |
| realtor.com, redfin.com | Guest works; login adds nothing essential (Redfin shows some sold data only to signed-in users in some markets) |

CT Judicial and Northeast Private Client Group need no cookies.
