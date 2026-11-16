#!/bin/bash
set -euo pipefail

CDP_PORT="${CDP_PORT:-9223}"
STATE_DIR="${STATE_DIR:-/app/.property-lookup}"
mkdir -p "$STATE_DIR"

if [[ "${PL_HEADLESS:-0}" != "1" ]]; then
    PL_DISPLAY="${PL_DISPLAY:-:99}"
    DISPLAY_NUM="${PL_DISPLAY#:}"
    # A restarted container keeps /tmp, so a previous run's lock/socket would
    # make Xvfb refuse to start while Chrome still sees a "display".
    rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}"

    echo "Starting Xvfb on ${PL_DISPLAY}..."
    Xvfb "$PL_DISPLAY" -screen 0 1920x1200x24 -ac -nolisten tcp >"$STATE_DIR/xvfb.log" 2>&1 &
    echo $! > "$STATE_DIR/xvfb.pid"
    for i in $(seq 1 30); do
        if xdpyinfo -display "$PL_DISPLAY" >/dev/null 2>&1; then
            echo "Xvfb is ready"
            break
        fi
        if [ "$i" -eq 30 ]; then
            echo "ERROR: Xvfb did not become ready on ${PL_DISPLAY}" >&2
            cat "$STATE_DIR/xvfb.log" >&2 || true
            exit 1
        fi
        sleep 0.5
    done
    export PL_DISPLAY DISPLAY="$PL_DISPLAY"
fi

/app/start_browser.sh

echo "Waiting for Chrome CDP to be ready..."
for i in $(seq 1 30); do
    if curl -s -m 2 "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
        echo "Chrome CDP is ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: Chrome CDP failed to start after 30 seconds" >&2
        cat "$STATE_DIR/chrome.log" >&2 || true
        exit 1
    fi
    sleep 1
done

echo "Starting FastAPI application..."
exec python -m uvicorn property_lookup.api.app:app --host 0.0.0.0 --port 8000
