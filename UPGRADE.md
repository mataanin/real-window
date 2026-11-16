# Upgrade Instructions for Running Instances

This guide explains how to upgrade a running EC2 instance with the fixed Docker configuration.

## What Changed

**Docker Fixes:**
- Base image: `python:3.12-slim-bookworm` (was `ubuntu:22.04` with Python 3.10)
- Chrome installation: Uses proper GPG keyring (no deprecated `apt-key`)
- Build process: Copies source before `pip install` (non-editable install)
- Error handling: Better logging in docker-entrypoint.sh

**Terraform user_data Fixes:**
- Docker GPG key handling (no deprecated `gpg --dearmor`)
- Explicit `docker compose build` before starting
- Extended health check timeout (120s for first-time Chrome setup)
- Better error reporting in logs

## Option 1: Update Running Instance (Quick)

If you have a running EC2 instance deployed via Terraform:

### Step 1: Connect via SSM

```bash
# From your local machine
terraform output -raw ssm_session_command | bash

# Or directly
aws ssm start-session --target <instance-id>
```

### Step 2: Pull Latest Code

```bash
cd /home/ubuntu/property-lookup
git pull origin main
```

### Step 3: Rebuild and Restart

```bash
# Stop current containers
docker compose down

# Rebuild with new Dockerfile
docker compose build --no-cache

# Start services
docker compose up -d

# Watch logs
docker compose logs -f
```

### Step 4: Verify

```bash
# Wait about 30 seconds for Chrome to start, then:
curl http://localhost:8000/health

# Should return JSON with "status": "healthy"
```

**Estimated downtime:** 2-3 minutes for rebuild + startup

## Option 2: Recreate Instance (Clean Slate)

If you prefer a fresh instance with the fixed user_data:

```bash
# From your local machine in infra/terraform/
terraform apply -replace=aws_instance.property_lookup

# This will:
# 1. Destroy the old instance
# 2. Create a new one with fixed cloud-init
# 3. Run the full deployment from scratch
```

**Estimated downtime:** 5-10 minutes for new instance + build

**Advantages:**
- Tests the full cloud-init process
- Verifies user_data fixes work
- Clean state

**Disadvantages:**
- Longer downtime
- Loses any data in SQLite database (cached lookups)
- New instance ID (update any monitoring/alerts)

## Option 3: Just Update Without Downtime (Risky)

If you want to pull updates but delay the rebuild:

```bash
cd /home/ubuntu/property-lookup
git pull origin main

# Containers keep running with old build
# Rebuild when convenient
```

## Troubleshooting

### Docker Build Fails

Check the error:
```bash
docker compose build 2>&1 | tee build.log
cat build.log
```

Common issues:
- Insufficient disk space: `df -h`
- Docker daemon not running: `sudo systemctl status docker`
- Network issues: Test with `curl https://pypi.org/simple/`

### Container Won't Start

Check logs:
```bash
docker compose logs

# Specific container
docker logs property-lookup-property-lookup-1

# Check Chrome logs
tail -50 ~/.property-lookup/chrome.log
```

### Health Check Fails

```bash
# Check if API is listening
curl -v http://localhost:8000/health

# Check if Chrome CDP is up
curl http://127.0.0.1:9223/json/version

# Check container status
docker compose ps
```

## Rollback

If the new version has issues:

```bash
cd /home/ubuntu/property-lookup

# Revert to previous commit
git log --oneline -5  # Find the old commit
git checkout <old-commit-sha>

# Rebuild and restart
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Recommendation

For production instances: **Use Option 1** (update running instance)
- Faster
- Less disruptive
- Tests the actual upgrade path

For testing/staging: **Use Option 2** (recreate)
- Validates cloud-init
- Clean state
- Tests full deployment

## Post-Upgrade Verification

After upgrading, test a lookup:

```bash
# Get your API token from .env
cat /home/ubuntu/property-lookup/.env | grep API_TOKEN

# Test a lookup (replace with your token)
curl -X POST http://localhost:8000/lookups \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"address": "48 Amsterdam Ave, Bridgeport, CT 06606", "sites": ["zillow"]}'

# Note the job_id, then check status
curl http://localhost:8000/lookups/JOB_ID \
  -H "Authorization: Bearer YOUR_TOKEN"
```
