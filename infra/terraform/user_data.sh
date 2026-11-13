#!/bin/bash
set -euo pipefail

# Log all output
exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "==> Starting user-data script at $(date)"

# Update system
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

# Verify SSM agent is running (Ubuntu 24.04 ships it via snap)
echo "==> Verifying AWS SSM Agent"
if ! snap list amazon-ssm-agent > /dev/null 2>&1; then
  echo "Installing SSM agent"
  snap install amazon-ssm-agent --classic
fi
systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service
systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service --no-pager

# Install Docker
echo "==> Installing Docker"
apt-get install -y -qq ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable Docker
systemctl enable docker
systemctl start docker

# Add ubuntu user to docker group
usermod -aG docker ubuntu

%{ if tailscale_auth_key != null && tailscale_auth_key != "" ~}
# Install Tailscale
echo "==> Installing Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh

# Start Tailscale with SSH enabled
echo "==> Configuring Tailscale"
tailscale up --authkey="${tailscale_auth_key}" --ssh --accept-routes
%{ endif ~}

# Set up git authentication for private repos
%{ if github_token_ssm_parameter != null && github_token_ssm_parameter != "" ~}
echo "==> Fetching GitHub token from SSM Parameter Store"
GITHUB_TOKEN=$(aws ssm get-parameter --region ${region} --name "${github_token_ssm_parameter}" --with-decryption --query Parameter.Value --output text)
export GITHUB_TOKEN
%{ endif ~}
%{ if github_token != null && github_token != "" ~}
export GITHUB_TOKEN="${github_token}"
%{ endif ~}
%{ if github_deploy_key != null && github_deploy_key != "" ~}
echo "==> Setting up SSH deploy key"
mkdir -p /home/ubuntu/.ssh
cat > /home/ubuntu/.ssh/deploy_key << 'KEYEOF'
${github_deploy_key}
KEYEOF
chmod 600 /home/ubuntu/.ssh/deploy_key
chown ubuntu:ubuntu /home/ubuntu/.ssh/deploy_key
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/deploy_key -o StrictHostKeyChecking=no"
%{ endif ~}

# Clone repository
echo "==> Cloning repository"
cd /home/ubuntu
if [[ -n "$${GITHUB_TOKEN:-}" ]]; then
  # Use token for HTTPS clone
  REPO_WITH_TOKEN=$(echo "${repo_url}" | sed "s|https://|https://$${GITHUB_TOKEN}@|")
  sudo -u ubuntu git clone "$REPO_WITH_TOKEN" property-lookup || true
else
  sudo -u ubuntu git clone "${repo_url}" property-lookup || true
fi
cd property-lookup
%{ if github_deploy_key != null && github_deploy_key != "" ~}
sudo -u ubuntu GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git fetch origin
%{ else ~}
sudo -u ubuntu git fetch origin
%{ endif ~}
sudo -u ubuntu git checkout "${repo_ref}"
%{ if github_deploy_key != null && github_deploy_key != "" ~}
sudo -u ubuntu GIT_SSH_COMMAND="$GIT_SSH_COMMAND" git pull origin "${repo_ref}" || true
%{ else ~}
sudo -u ubuntu git pull origin "${repo_ref}" || true
%{ endif ~}

# Create .env file
echo "==> Creating .env file"
cat > .env << 'ENVEOF'
%{ if api_token != null && api_token != "" ~}
API_TOKEN=${api_token}
%{ endif ~}
%{ if proxy != null && proxy != "" ~}
PROXY=${proxy}
%{ endif ~}
CACHE_TTL_HOURS=${cache_ttl_hours}
CDP_PORT=9223
ENVEOF

chown ubuntu:ubuntu .env
chmod 600 .env

# Create output directory
mkdir -p output
chown ubuntu:ubuntu output

# Start the application
echo "==> Starting application with Docker Compose"
sudo -u ubuntu docker compose up -d

# Wait for service to be ready
echo "==> Waiting for service to start"
for i in $(seq 1 60); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "==> Service is ready!"
    break
  fi
  sleep 2
done

echo "==> User-data script completed at $(date)"
echo "==> Check service status: docker compose logs -f"
