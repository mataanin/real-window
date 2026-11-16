#!/bin/bash
# Deploy script for property-lookup application
# Can be run manually or via SSM from GitHub Actions
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ubuntu/property-lookup}"
BRANCH="${BRANCH:-main}"

echo "==> Starting deployment at $(date)"
echo "==> Repository: $REPO_DIR"
echo "==> Branch: $BRANCH"

# Navigate to repository
cd "$REPO_DIR"

# Fetch latest changes
echo "==> Fetching from origin..."
git fetch origin

# Show what will change
echo "==> Changes to be deployed:"
git log --oneline HEAD..origin/$BRANCH | head -10 || echo "  (no new commits)"

# Hard reset to origin branch (preserves .env and output/ which are gitignored)
echo "==> Resetting to origin/$BRANCH..."
git reset --hard origin/$BRANCH

# Verify important files are preserved
if [ ! -f .env ]; then
  echo "WARNING: .env file not found after reset"
fi

# Older compose files bind-mounted ./property_lookup.db, which Docker created
# as an empty directory when the file was missing.
if [ -d property_lookup.db ]; then
  rmdir property_lookup.db 2>/dev/null || echo "WARNING: ./property_lookup.db is a non-empty directory"
fi
mkdir -p data output

# Stop containers
echo "==> Stopping containers..."
docker compose down

# Build new image
echo "==> Building Docker image..."
docker compose build

# Start containers
echo "==> Starting containers..."
docker compose up -d

# Wait for health check
echo "==> Waiting for application to be healthy..."
MAX_WAIT=180
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  sleep 5
  ELAPSED=$((ELAPSED + 5))
  
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "==> ✓ Application is healthy!"
    curl -s http://localhost:8000/health | head -20
    echo ""
    echo "==> Deployment completed successfully at $(date)"
    exit 0
  fi
  
  echo "  Waiting... ($ELAPSED/${MAX_WAIT}s)"
done

# Health check failed
echo "==> ✗ Health check failed after ${MAX_WAIT}s"
echo "==> Container logs:"
docker compose logs --tail=100
exit 1
