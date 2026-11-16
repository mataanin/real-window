FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PL_HEADLESS=0 \
    CDP_PORT=9223 \
    STATE_DIR=/app/.property-lookup

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    xvfb \
    x11-utils \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome with proper keyring (no deprecated apt-key)
RUN wget -q -O /tmp/google-chrome-key.gpg https://dl-ssl.google.com/linux/linux_signing_key.pub \
    && gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg /tmp/google-chrome-key.gpg \
    && rm /tmp/google-chrome-key.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Set up app directory
WORKDIR /app

# Copy source code first
COPY . .

# Install Python dependencies (non-editable)
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel \
    && pip3 install --no-cache-dir .

# Note: Playwright is installed but we don't install its browsers
# We use the Chrome deb package installed above via start_browser.sh
# This avoids needing playwright install-deps and keeps the image smaller

# Create necessary directories
RUN mkdir -p /app/output /app/cookies /app/.property-lookup /app/logs

# Expose API port
EXPOSE 8000

# Start script
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
