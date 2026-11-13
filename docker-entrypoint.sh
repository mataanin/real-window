#!/bin/bash
set -e

# Start Chrome with CDP
echo "Starting Chrome with CDP on port ${CDP_PORT}..."
/app/start_browser.sh

# Wait for Chrome to be ready
for i in $(seq 1 30); do
    if curl -s -m 2 "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
        echo "Chrome CDP is ready"
        break
    fi
    sleep 1
done

# Start the FastAPI application
echo "Starting FastAPI application..."
exec python3 -m uvicorn property_lookup.api.app:app --host 0.0.0.0 --port 8000
