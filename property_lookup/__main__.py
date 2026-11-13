"""CLI: python -m property_lookup "123 Main St, Bridgeport, CT 06604" [--sites zillow,redfin] [--headed]"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

from .address import parse_address, geocode
from .browser import BrowserSession, ROOT
from .merge import write_report
from .models import SiteResult, ERROR
from .sites import ADAPTERS
from .sites.base import RunCtx

DEFAULT_ORDER = ["nepcg", "ct_judicial", "crexi", "loopnet", "commercialcafe", "brevitas", "redfin", "zillow", "realtor", "homes"]


def build_parser():
    p = argparse.ArgumentParser(prog="property_lookup", description=__doc__)
    p.add_argument("address", nargs="?", help='e.g. "48 Amsterdam Ave, Bridgeport, CT 06606"')
    p.add_argument("--sites", help="comma list; default all: " + ",".join(DEFAULT_ORDER))
    p.add_argument("--headed", action="store_true", default=True,
                   help="(default) headed Chrome on a virtual display - far fewer bot blocks")
    p.add_argument("--headless", dest="headed", action="store_false", help="headless Chrome (more blocks)")
    p.add_argument("--launch", action="store_true",
                   help="fallback: launch a private Playwright Chrome instead of attaching to the shared CDP Chrome on :9223")
    p.add_argument("--cookie-files", action="store_true",
                   help="also inject cookie files from cookies/<domain>.json|.txt (see COOKIES.md)")
    p.add_argument("--cookie-dir", type=Path, default=ROOT / "cookies")
    p.add_argument("--timeout", type=int, default=150, help="per-site timeout seconds (default 150)")
    p.add_argument("--concurrency", type=int, default=1, help="sites run in parallel (default 1 = sequential)")
    p.add_argument("--interactive", type=int, default=0, metavar="SECONDS",
                   help="on a bot challenge, wait this long for a human to clear it in the visible browser")
    p.add_argument("--out", type=Path, default=ROOT / "output")
    p.add_argument("--check-sessions", action="store_true", help="report per-site login/block state and exit")
    p.add_argument("--debug", action="store_true")
    return p


async def run(args) -> int:
    names = [s.strip() for s in (args.sites or ",".join(DEFAULT_ORDER)).split(",") if s.strip()]
    bad = [n for n in names if n not in ADAPTERS]
    if bad:
        print(f"unknown site(s): {bad}; known: {list(ADAPTERS)}", file=sys.stderr)
        return 2
    async with BrowserSession(mode="launch" if args.launch else "cdp", headed=args.headed,
                              cookie_files=args.cookie_files, cookie_dir=args.cookie_dir) as sess:
        if args.check_sessions:
            from .sessions import check_sessions
            rows = await check_sessions(sess, names)
            w = max(len(r["site"]) for r in rows)
            for r in rows:
                line = f"{r['site']:<{w}}  {r['state']:<22} cookies={r['cookies']:<4} {r.get('detail') or ''}"
                if r["state"] != "logged in" and r.get("hint"):
                    line += f"  ({r['hint']})"
                print(line)
            return 0
        if not args.address:
            print("address required", file=sys.stderr)
            return 2
        addr = geocode(parse_address(args.address))
        print(f"Address: {addr.one_line}  town={addr.town or '?'}  latlon={addr.lat},{addr.lon}")
        outdir = args.out / addr.slug
        ctx = RunCtx(outdir=outdir, interactive=args.interactive, debug=args.debug)
        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def one(n):
            async with sem:
                a = ADAPTERS[n]
                print(f"  -> {a.label} ...", flush=True)
                try:
                    r = await asyncio.wait_for(a.run(sess, addr, ctx), timeout=args.timeout)
                except asyncio.TimeoutError:
                    r = SiteResult(site=n, status=ERROR, notes=[f"timed out after {args.timeout}s"])
                print(f"     {a.label}: {r.status} {('- ' + r.url) if r.url else ''} {('[' + '; '.join(r.notes)[:160] + ']') if r.notes else ''}", flush=True)
                return r

        results = await asyncio.gather(*(one(n) for n in names))
    labels = {n: ADAPTERS[n].label for n in ADAPTERS}
    md, js = write_report(addr, list(results), outdir, labels)
    print(f"\nReport: {md}\nJSON:   {js}")
    return 0


def main():
    args = build_parser().parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
