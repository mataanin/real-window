# property-lookup

Looks up one US property address (built for Connecticut 5-15 unit multifamily, works for 1-4 unit homes)
on public listing sites and merges everything into `output/<slug>/report.md` + `report.json`.

## Quick start

```bash
cd /workspace/property-lookup
. .venv/bin/activate
./start_browser.sh            # real Chrome, headed on Xvfb :99, CDP on 127.0.0.1:9223 (auto-started if needed)
python -m property_lookup "48 Amsterdam Ave, Bridgeport, CT 06606"
python -m property_lookup "18 Maple St, Trumbull, CT 06611" --sites redfin,zillow,realtor,homes
python -m property_lookup --check-sessions      # per site: reachable / blocked, logged in or guest
```

### Flags
| flag | meaning |
|---|---|
| `--sites a,b,...` | subset: `nepcg, ct_judicial, crexi, loopnet, commercialcafe, brevitas, redfin, zillow, realtor, homes` |
| `--headed` (default) / `--headless` | headless only matters with `--launch` |
| `--launch` | don't attach to the CDP Chrome; launch a private Playwright Chrome instead |
| `--cookie-files a.json,b.txt` / `--cookie-dir DIR` | optional fallback: load exported cookies (JSON or Netscape) into the context, see COOKIES.md |
| `--timeout S` | per-site timeout (default 150 s); a site that times out is reported `error`, the run continues |
| `--concurrency N` | sites run in parallel (default 1 = sequential, gentlest on bot protection) |
| `--interactive S` | when a captcha/challenge appears, wait up to S seconds for a human to solve it on the box desktop |
| `--out DIR` | output root (default `output/`) |
| `--check-sessions` | open each site and report cookie count, blocked state, logged-in vs guest |
| `--debug` | verbose logging |

## Cookies / sessions
Primary path: the persistent Chrome profile `~/.property-lookup/chrome-profile` behind CDP port 9223.
The tool attaches with `connect_over_cdp` and uses its default context, so any cookies there
(e.g. synced from the user's Mac by agentcookie in sink mode, see `SETUP_AGENTCOOKIE.md`) apply.
Allowlist policy for agentcookie: `agentcookie/blocklist.yaml`; sink config: `agentcookie/sink.yaml`.

Before LoopNet/Showcase/Homes.com visits the tool deletes only the Akamai bot-manager cookies
(`_abck`, `ak_bmsc`, `bm_*`) for those domains; a flagged sensor cookie otherwise gets every request
"Access Denied". Login cookies are kept.

## Output
Each adapter returns `{site, url, status: found|not_found|blocked|login_required|error, fields,
raw_text_snippet, screenshot_path}`. `report.md` has: sources + status, consolidated fact table
(value -> site(s)), conflicts, fields hidden behind login, price/tax history, other per-site facts,
descriptions, links, screenshots. Values are never invented; missing = blank.

## Sites (how each is read)
| site | method |
|---|---|
| NEPCG (Northeast Private Client Group) | Buildout inventory JSON + Buildout iframe detail page |
| CT Judicial foreclosures | town list -> pending sales by town -> notice page (plain HTTP) |
| Crexi | records search + universal-search API (bounding box) + asset API; guests see masked units/NOI/cap |
| LoopNet (fallback Showcase) | city for-sale / apartment-buildings / auctions search pages -> listing page text (Ten-X auctions appear here) |
| CommercialCafe / CommercialEdge | city listing pages -> detail page text |
| Brevitas | `/api/search` pins -> `/p/<id>` detail |
| Redfin | stingray autocomplete -> page -> aboveTheFold/belowTheFold/avm APIs with listingId |
| Zillow | zillowstatic autocomplete -> homedetails `__NEXT_DATA__` (PerimeterX often blocks) |
| Realtor.com | moveaws suggest API -> detail `__NEXT_DATA__` |
| Homes.com | site search box -> property page JSON-LD + text |

Skipped on purpose: CityFeet, Auction.com.

## What the tool does NOT do
It does not solve captchas, press-and-hold or other bot challenges. A challenged site is reported
`blocked` with a hint; rerun with `--interactive 120` and solve it on the box desktop, or rely on
synced cookies. Discovery uses city-level search pages, so a listing that the site files under a
portfolio name with no street number (e.g. "Noble Ave Portfolio") can be missed.
