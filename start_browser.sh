#!/usr/bin/env bash
# Start (or reuse) the long-lived Chrome that agentcookie's Linux sink injects
# cookies into and that property_lookup attaches to over CDP.
#
#   ./start_browser.sh            # start if not running, print status
#   ./start_browser.sh --stop     # stop Chrome (and our Xvfb if we started it)
#   ./start_browser.sh --status
#
# Env overrides:
#   CDP_PORT=9223  CHROME_PROFILE=~/.property-lookup/chrome-profile
#   PL_DISPLAY=:99 (dedicated virtual display so Chrome outlives agent desktops)
#   CHROME_BIN=google-chrome   PL_HEADLESS=1 (use --headless=new instead of Xvfb)
#   STATE_DIR=~/.property-lookup (for logs and PIDs)
set -euo pipefail
CDP_PORT="${CDP_PORT:-9223}"
STATE_DIR="${STATE_DIR:-$HOME/.property-lookup}"
CHROME_PROFILE="${CHROME_PROFILE:-$STATE_DIR/chrome-profile}"
PL_DISPLAY="${PL_DISPLAY:-:99}"
CHROME_BIN="${CHROME_BIN:-$(command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser || true)}"
LOG="$STATE_DIR/chrome.log"
mkdir -p "$STATE_DIR" "$CHROME_PROFILE"

up() { curl -s -m 2 "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; }

if [[ "${1:-}" == "--status" ]]; then
  if up; then echo "Chrome CDP up on 127.0.0.1:${CDP_PORT}"; curl -s "http://127.0.0.1:${CDP_PORT}/json/version"; exit 0
  else echo "Chrome CDP NOT running on ${CDP_PORT}"; exit 1; fi
fi

if [[ "${1:-}" == "--stop" ]]; then
  pkill -f -- "--remote-debugging-port=${CDP_PORT}" || true
  if [[ -f "$STATE_DIR/xvfb.pid" ]]; then kill "$(cat "$STATE_DIR/xvfb.pid")" 2>/dev/null || true; rm -f "$STATE_DIR/xvfb.pid"; fi
  echo "stopped"; exit 0
fi

if up; then
  echo "Reusing Chrome already listening on 127.0.0.1:${CDP_PORT}"; exit 0
fi
[[ -z "$CHROME_BIN" ]] && { echo "No Chrome/Chromium binary found" >&2; exit 2; }

HEADLESS_ARGS=()
if [[ "${PL_HEADLESS:-0}" == "1" ]]; then
  HEADLESS_ARGS=(--headless=new)
else
  # Dedicated Xvfb display (headed Chrome looks far more like a real browser
  # to bot protection than headless).
  if ! xdpyinfo -display "$PL_DISPLAY" >/dev/null 2>&1 && ! [[ -e "/tmp/.X11-unix/X${PL_DISPLAY#:}" ]]; then
    nohup Xvfb "$PL_DISPLAY" -screen 0 1920x1200x24 -ac -nolisten tcp >"$STATE_DIR/xvfb.log" 2>&1 &
    echo $! > "$STATE_DIR/xvfb.pid"
    sleep 1
  fi
  export DISPLAY="$PL_DISPLAY"
fi

# Stale profile lock from a crashed Chrome would block startup.
rm -f "$CHROME_PROFILE/SingletonLock" "$CHROME_PROFILE/SingletonSocket" "$CHROME_PROFILE/SingletonCookie"

PROXY_ARGS=()
if [[ -n "${PROXY:-}" ]]; then
  PROXY_ARGS=(--proxy-server="${PROXY}")
fi

nohup "$CHROME_BIN" \
  --remote-debugging-port="${CDP_PORT}" \
  --remote-debugging-address=127.0.0.1 \
  --user-data-dir="$CHROME_PROFILE" \
  --no-first-run --no-default-browser-check \
  --disable-blink-features=AutomationControlled \
  --password-store=basic \
  --lang=en-US \
  --window-size=1440,960 \
  "${PROXY_ARGS[@]}" \
  "${HEADLESS_ARGS[@]}" \
  about:blank >"$LOG" 2>&1 &
echo $! > "$STATE_DIR/chrome.pid"

for i in $(seq 1 30); do
  if up; then echo "Chrome started (pid $(cat "$STATE_DIR/chrome.pid")) on 127.0.0.1:${CDP_PORT}, profile $CHROME_PROFILE, display ${DISPLAY:-headless}"; exit 0; fi
  sleep 0.5
done
echo "Chrome failed to open CDP port ${CDP_PORT}; see $LOG" >&2
tail -20 "$LOG" >&2
exit 1
