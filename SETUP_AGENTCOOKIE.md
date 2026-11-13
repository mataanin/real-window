# Syncing your Mac's Chrome cookies to this box with agentcookie

Source: https://github.com/mvanhorn/agentcookie (README "Linux sink setup", docs/teammates.md,
`examples/sink.yaml`, `examples/blocklist.yaml`, policy parser `internal/config/allowlist.go`).
Latest release when this was written: **v1.7.0** (assets `agentcookie_1.7.0_linux_amd64.tar.gz`,
`agentcookie_1.7.0_darwin_universal.tar.gz`, `checksums.txt`).

Nothing below has been installed or paired yet: it needs your Mac and Tailscale on both machines.
**This box does not have Tailscale installed today** (`which tailscale` is empty), so step 0 is required.

## How it fits together

    Mac Chrome --(agentcookie source --watch)--> Tailscale (AES-GCM) --> agentcookie sink (this box)
                                                                          |  live CDP Storage.setCookies
                                                                          v
                                       Chrome on 127.0.0.1:9223  <--  python -m property_lookup (connect_over_cdp)

The sink only accepts the domains in `agentcookie/blocklist.yaml` (an **allowlist** policy despite the
file name). On Linux a missing policy file means "accept nothing", so this file is required.

## 0. Tailscale on this box (once)

    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up            # sign in with the same tailnet as the Mac
    tailscale ip -4              # note the 100.x address

## 1. Start the shared Chrome (each boot; the tool also auto-starts it)

    cd /workspace/property-lookup && ./start_browser.sh
    # -> Chrome with --remote-debugging-port=9223 --remote-debugging-address=127.0.0.1
    #    --user-data-dir=~/.property-lookup/chrome-profile on Xvfb :99

## 2. Install the Linux binary (no wizard on Linux)

    cd /tmp
    curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/agentcookie_1.7.0_linux_amd64.tar.gz
    curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/checksums.txt
    sha256sum --ignore-missing -c checksums.txt
    tar -xzf agentcookie_1.7.0_linux_amd64.tar.gz
    mkdir -p ~/.local/bin && mv agentcookie ~/.local/bin/

## 3. Write the config (before pairing, so nothing lands under a sync-all policy)

    mkdir -p ~/.config/agentcookie
    cp /workspace/property-lookup/agentcookie/blocklist.yaml ~/.config/agentcookie/blocklist.yaml
    cp /workspace/property-lookup/agentcookie/sink.yaml      ~/.config/agentcookie/sink.yaml
    # edit sink.yaml: listen.addr = <tailscale ip -4>:9999, peer.hostname = <Mac MagicDNS name>

`sink.yaml` (push mode, per README):

    listen:
      addr: 100.x.y.z:9999
    peer:
      hostname: your-mac.tailnet
    live_cdp:
      enabled: true
      endpoint: http://127.0.0.1:9223
    skip_chrome_sqlite: true

`blocklist.yaml` format (from `internal/config/allowlist.go`): `version: 1` (required, only 1 is
accepted), `policy: allowlist` (or `blocklist`), `domains:` list of `{pattern, description}`; every
pattern must be non-empty. Patterns are SQLite `LIKE` against Chrome's `host_key` (`%` = wildcard,
case-insensitive, domain cookies have a leading dot), hence both `crexi.com` and `%.crexi.com`.
The shipped file allows: loopnet.com, showcase.com, homes.com, ten-x.com, crexi.com,
commercialcafe.com, commercialedge.com, brevitas.com, zillow.com, realtor.com, redfin.com
(each bare + `%.`). Nothing else (Google, mail, banks...) will be accepted by this sink.

Recommended: put the same allowlist on the Mac too (`~/.config/agentcookie/blocklist.yaml` there).
Both sides read the file independently; the Mac's copy controls what ever leaves the laptop, the
box's copy controls what lands here.

## 4. Pair

On the Mac:

    agentcookie wizard install --as source --peer <this-box-tailscale-hostname>
    # prints:  Pairing code: ABCD-EFGH-IJKL   Pair URL: http://your-mac.tailnet:9998/pair

On this box, with those values:

    ~/.local/bin/agentcookie pair --as sink --peer your-mac.tailnet \
        --code ABCD-EFGH-IJKL --pair-url http://your-mac.tailnet:9998/pair

## 5. Run the sink and verify

    ~/.local/bin/agentcookie sink &          # or a systemd --user unit you review yourself
    ~/.local/bin/agentcookie doctor          # want: "live_cdp: endpoint reachable" OK, "tailnet: bind address" OK
    ~/.local/bin/agentcookie status --json   # LastWriteMode contains "livecdp"
    # sink log should show: live_cdp: injected N cookies into M context(s)
    cd /workspace/property-lookup && .venv/bin/python -m property_lookup --check-sessions

Linux `doctor` reports expected FAILs for macOS-only checks (codesign, Chrome.app, launchctl).
"wrote 0 cookies" for Chrome SQLite is expected; success is the live CDP inject line.

## Alternative: pull mode ("teammate", no inbound listener)

If you'd rather the box pull from the Mac (agentcookie `teammates`), list this agent in the Mac's
`~/.config/agentcookie/teammates.yaml`, approve it, then on the box run the `join` line the Mac sends:

    ~/.local/bin/agentcookie join --from your-mac.tail-xxxx.ts.net --code ABCD-EFGH-IJKL --as grokbot --cadence turn+timer
    ~/.local/bin/agentcookie sink --pull-once      # at the start of each session

`join` writes a sync-all `blocklist.yaml` **only if none exists**, so copy our allowlist (step 3)
first. In pull mode the Mac's `blocklist.yaml` is what limits the sites (docs/teammates.md:
"there are no per-teammate blocklists"), so set the allowlist on the Mac as well.

## Caveats

- Cookie-authenticated sites (all the listing sites here) show logged-in UI after injection; Google/
  DBSC sessions do not transfer (not needed here).
- Bot-manager cookies (Zillow PerimeterX `_px*`, CoStar Akamai `_abck`) are minted for the Mac's
  browser fingerprint/IP. This box's Chrome reports a Linux UA and a different IP, so they may help,
  or may be rejected; login cookies (Crexi, LoopNet, Brevitas) are what reliably unlock data.
- CDP port is loopback-only; any process on this box running as the same user can read injected
  cookies through it.
