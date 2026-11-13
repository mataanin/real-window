# Deployment Guide

This guide covers deploying the Property Lookup API to an Ubuntu EC2 instance with Docker, Tailscale integration, and agentcookie sink setup.

## Overview

The deployment stack consists of:
- Docker container running Chrome (with CDP) + FastAPI service
- Optional Tailscale for secure remote access
- Optional agentcookie sink for syncing cookies from your Mac
- Optional residential proxy for bypassing datacenter IP blocks

## Quick Start with Docker Compose

### 1. Prerequisites

Ubuntu 22.04 LTS (or similar) with:
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose git
sudo usermod -aG docker $USER
# Log out and back in for group membership to take effect
```

### 2. Clone and Configure

```bash
git clone <repository-url>
cd property-lookup

# Optional: Set API authentication token
echo "API_TOKEN=your-secret-token-here" > .env

# Optional: Configure residential proxy
echo "PROXY=http://user:pass@proxy-host:port" >> .env

# Optional: Adjust cache TTL (default 24 hours)
echo "CACHE_TTL_HOURS=48" >> .env
```

### 3. Start Services

```bash
docker-compose up -d
```

The API will be available at `http://localhost:8000`

### 4. Verify

```bash
# Check service health
curl http://localhost:8000/health

# Check logs
docker-compose logs -f
```

## Advanced Setup: EC2 + Tailscale + agentcookie

### Prerequisites

- Ubuntu 22.04 EC2 instance (t3.medium or larger recommended)
- Tailscale account
- Mac with Chrome (for cookie syncing)

### 1. EC2 Instance Setup

Launch an EC2 instance with:
- AMI: Ubuntu 22.04 LTS
- Instance type: t3.medium minimum (2 vCPU, 4GB RAM)
- Storage: 20GB+ EBS volume
- Security group: Allow inbound on port 8000 (or restrict to your IP/Tailscale)

SSH into the instance:
```bash
ssh ubuntu@<ec2-public-ip>
```

### 2. Install Docker

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose git
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ubuntu
# Log out and back in
```

### 3. Install Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Follow authentication prompts
# Note your Tailscale IP: tailscale ip -4
```

### 4. Clone Repository

```bash
cd /home/ubuntu
git clone <repository-url>
cd property-lookup
```

### 5. Configure Environment

```bash
# Create .env file
cat > .env << 'EOF'
API_TOKEN=your-secure-random-token
CACHE_TTL_HOURS=24
CDP_PORT=9223
EOF

# Optional: Add proxy configuration
# PROXY=http://user:pass@residential-proxy-host:port
```

### 6. Start with Docker Compose

```bash
docker-compose up -d
```

### 7. Systemd Service (Optional)

For automatic startup on boot:

```bash
sudo tee /etc/systemd/system/property-lookup.service << 'EOF'
[Unit]
Description=Property Lookup API
Requires=docker.service
After=docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/property-lookup
ExecStart=/usr/bin/docker-compose up
ExecStop=/usr/bin/docker-compose down
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable property-lookup
sudo systemctl start property-lookup
```

## Agentcookie Setup for Cookie Syncing

Agentcookie syncs cookies from your Mac's Chrome to the Docker container's Chrome via CDP.

### On the EC2 Instance

1. **Get the container's Chrome CDP port mapping**:
   The Docker container exposes Chrome CDP on `127.0.0.1:9223` inside the container. You need to map this for agentcookie.

2. **Install agentcookie binary**:
```bash
cd /tmp
curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/agentcookie_1.7.0_linux_amd64.tar.gz
curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/checksums.txt
sha256sum --ignore-missing -c checksums.txt
tar -xzf agentcookie_1.7.0_linux_amd64.tar.gz
sudo mv agentcookie /usr/local/bin/
```

3. **Configure agentcookie sink**:
```bash
mkdir -p ~/.config/agentcookie

# Copy allowlist policy
cp /home/ubuntu/property-lookup/agentcookie/blocklist.yaml ~/.config/agentcookie/

# Create sink config
cat > ~/.config/agentcookie/sink.yaml << EOF
listen:
  addr: $(tailscale ip -4):9999
peer:
  hostname: your-mac.tail-xxxxx.ts.net  # Replace with your Mac's Tailscale hostname
live_cdp:
  enabled: true
  endpoint: http://127.0.0.1:9223
skip_chrome_sqlite: true
EOF
```

4. **Expose Docker CDP to host** (if not already):
   
   Update `docker-compose.yml` to expose CDP:
```yaml
services:
  property-lookup:
    ports:
      - "8000:8000"
      - "127.0.0.1:9223:9223"  # Add this line
```

Restart:
```bash
docker-compose down && docker-compose up -d
```

### On Your Mac

1. **Install agentcookie**:
```bash
cd /tmp
curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/agentcookie_1.7.0_darwin_universal.tar.gz
tar -xzf agentcookie_1.7.0_darwin_universal.tar.gz
sudo mv agentcookie /usr/local/bin/
```

2. **Copy allowlist policy**:
```bash
mkdir -p ~/.config/agentcookie
# Copy blocklist.yaml from the repository to ~/.config/agentcookie/
```

3. **Run pairing wizard**:
```bash
agentcookie wizard install --as source --peer ec2-tailscale-hostname
# Note the pairing code and URL
```

### Complete Pairing

On the EC2 instance:
```bash
agentcookie pair --as sink --peer your-mac.tail-xxxxx.ts.net \
    --code ABCD-EFGH-IJKL \
    --pair-url http://your-mac.tail-xxxxx.ts.net:9998/pair
```

### Run the Sink

```bash
# Test first
agentcookie sink

# Or run as a service
sudo tee /etc/systemd/system/agentcookie-sink.service << 'EOF'
[Unit]
Description=Agentcookie Sink
After=network.target

[Service]
Type=simple
User=ubuntu
ExecStart=/usr/local/bin/agentcookie sink
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable agentcookie-sink
sudo systemctl start agentcookie-sink
```

### Verify Cookie Sync

```bash
# Check sink status
agentcookie status --json

# Check sessions via API
curl http://localhost:8000/sessions -H "Authorization: Bearer your-token"
```

## Residential Proxy Configuration

Datacenter IPs (AWS, GCP, Azure) are often blocked by Akamai/PerimeterX on sites like:
- LoopNet
- Showcase  
- Homes.com
- Zillow

To use a residential proxy:

1. **Configure proxy in .env**:
```bash
echo "PROXY=http://username:password@proxy-host:port" >> .env
```

2. **Update start_browser.sh** (automatic in docker-entrypoint.sh):

The proxy is passed to Chrome via `--proxy-server` flag when `PROXY` env var is set.

Update `start_browser.sh` to add:
```bash
PROXY_ARGS=()
if [[ -n "${PROXY:-}" ]]; then
  PROXY_ARGS=(--proxy-server="${PROXY}")
fi

"$CHROME_BIN" \
  --remote-debugging-port="${CDP_PORT}" \
  "${PROXY_ARGS[@]}" \
  ...
```

3. **Restart services**:
```bash
docker-compose down && docker-compose up -d
```

## Monitoring and Maintenance

### View Logs

```bash
# Docker Compose logs
docker-compose logs -f

# Specific service
docker-compose logs -f property-lookup

# Agentcookie logs (if using systemd)
sudo journalctl -u agentcookie-sink -f
```

### Health Check

```bash
curl http://localhost:8000/health
```

Should return:
```json
{
  "status": "healthy",
  "cdp_connection": "reachable",
  "queue_size": 0,
  "active_jobs": 0
}
```

### Database Maintenance

The SQLite database is persisted in `property_lookup.db`. To back up:

```bash
docker-compose exec property-lookup sqlite3 /app/property_lookup.db ".backup /app/backup.db"
docker cp $(docker-compose ps -q property-lookup):/app/backup.db ./backup.db
```

### Clear Cache

```bash
docker-compose exec property-lookup python3 -c "
import asyncio
from property_lookup.api.database import Database
async def clear():
    db = Database()
    await db.init()
    await db.cleanup_old_cache(ttl_hours=0)
    await db.close()
asyncio.run(clear())
"
```

## Troubleshooting

### Chrome CDP Not Reachable

```bash
# Check if Chrome is running in container
docker-compose exec property-lookup ps aux | grep chrome

# Check CDP port
docker-compose exec property-lookup curl -s http://127.0.0.1:9223/json/version

# Restart container
docker-compose restart
```

### Sites Getting Blocked

1. Check if cookies are syncing (if using agentcookie)
2. Consider adding residential proxy
3. Check sessions: `curl http://localhost:8000/sessions`
4. Reduce concurrency (lookups are sequential by default)

### High Memory Usage

Chrome can use significant memory. Increase Docker memory limits:

```yaml
services:
  property-lookup:
    deploy:
      resources:
        limits:
          memory: 4G
```

## Security Considerations

1. **API Token**: Always set `API_TOKEN` in production
2. **Firewall**: Restrict port 8000 to trusted IPs or use Tailscale
3. **HTTPS**: Put behind nginx with SSL certificate for production
4. **Secrets**: Never commit `.env` file or cookies to git
5. **Updates**: Regularly update base image and dependencies

## Production Recommendations

1. Use a reverse proxy (nginx/Caddy) with SSL
2. Set up log rotation for Docker logs
3. Monitor disk usage (screenshots and reports accumulate)
4. Set up automated backups of the SQLite database
5. Use a residential proxy for reliability
6. Consider running on a dedicated host with static IP allowlisted by your residential proxy provider
