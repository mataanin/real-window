FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PL_HEADLESS=0 \
    CDP_PORT=9223 \
    STATE_DIR=/app/.property-lookup

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    wget \
    gnupg \
    xvfb \
    x11vnc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Set up app directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY pyproject.toml requirements.txt* ./
RUN pip3 install --no-cache-dir -e .

# Install Playwright and Chrome browser
RUN pip3 install playwright && playwright install chromium && playwright install-deps

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/output /app/cookies /app/.property-lookup /app/logs

# Expose API port
EXPOSE 8000

# Start script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
