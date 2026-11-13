# Terraform Infrastructure for Property Lookup API

This directory contains Terraform configuration to deploy the Property Lookup API to AWS EC2.

## Architecture

- EC2 t3.medium instance running Ubuntu 24.04 LTS
- Docker + Docker Compose running the API and Chrome
- Optional Tailscale for secure remote access
- Optional Elastic IP for stable addressing
- Security group with SSH access (restricted) and optional API access
- EBS root volume (20GB gp3) with encryption
- IMDSv2-only metadata service

## Prerequisites

1. **AWS account with credentials configured**:
   ```bash
   aws configure
   # Or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY
   ```

2. **AWS Session Manager plugin** (for SSH/port forwarding):
   ```bash
   # macOS
   brew install --cask session-manager-plugin
   
   # Ubuntu/Debian
   curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
   sudo dpkg -i session-manager-plugin.deb
   
   # Verify
   session-manager-plugin --version
   ```

3. Terraform installed:
   ```bash
   # macOS
   brew install terraform
   
   # Ubuntu/Debian
   wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
   echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
   sudo apt update && sudo apt install terraform
   ```

4. (Optional) SSH key pair in AWS or a local public key (only needed for SSH-over-SSM)

## Quick Start

1. **Create terraform.tfvars**:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit terraform.tfvars**:
   - Set `api_token` to a secure random value
   - (Optional) Set `key_pair_name` for SSH-over-SSM
   - Optionally set `tailscale_auth_key` and `proxy`
   - For private repos, set `github_token_ssm_parameter` (recommended) or `github_token`

   ```hcl
   api_token = "generate-with: openssl rand -base64 32"
   
   # Optional for private repos:
   # github_token_ssm_parameter = "/property-lookup/github-token"
   ```

3. **Initialize Terraform**:
   ```bash
   terraform init
   ```

4. **Review the plan**:
   ```bash
   terraform plan
   ```

5. **Apply**:
   ```bash
   terraform apply
   ```

6. **Save outputs**:
   ```bash
   terraform output
   
   # Connect via SSM Session Manager:
   terraform output -raw ssm_session_command | bash
   
   # Or set up SSH over SSM:
   terraform output ssm_ssh_config
   # Add the output to ~/.ssh/config, then:
   # ssh i-xxxxx
   
   # Port forward API to localhost:
   terraform output -raw ssm_port_forward_command | bash
   # Then access: http://localhost:8000
   ```

## Configuration Options

### Access Method

The instance uses **AWS SSM Session Manager** for secure access without opening SSH ports. No inbound rules by default.

### Required Variables

None! All variables have secure defaults.

### Recommended Variables

- `api_token`: Bearer token for API authentication
- `tailscale_auth_key`: Tailscale auth key for secure access
- `allocate_elastic_ip`: `true` for stable IP (default: `false`)

### Optional Variables

- `region`: AWS region (default: `us-east-1`)
- `instance_type`: EC2 instance type (default: `t3.medium`)
- `root_volume_size`: Root volume size in GB (default: `20`)
- `key_pair_name` / `public_key_path`: SSH key (optional, for SSH-over-SSM)
- `ssh_allowed_cidrs`: Direct SSH CIDRs (default: `[]`, disabled)
- `api_port`: API port (default: `8000`)
- `api_allowed_cidrs`: Public API access CIDRs (default: `[]`, closed)
- `github_token_ssm_parameter`: SSM path for GitHub token (for private repos)
- `github_token`: GitHub PAT (prefer SSM parameter)
- `github_deploy_key`: SSH deploy key (for SSH repo URLs)
- `proxy`: Residential proxy URL (recommended for production)
- `cache_ttl_hours`: Cache TTL in hours (default: `24`)
- `vpc_id`, `subnet_id`: Use specific VPC/subnet (default: default VPC)

## After Deployment

### 1. Verify Service

```bash
# Connect via SSM Session Manager
terraform output -raw ssm_session_command | bash

# Or set up port forwarding
terraform output -raw ssm_port_forward_command | bash
# In another terminal:
curl http://localhost:8000/health

# Or if using direct SSH/Tailscale:
# Check service status
docker ps
docker compose logs -f
```

### 2. Set Up Agentcookie (Cookie Syncing)

If using Tailscale, follow these steps to sync cookies from your Mac.

#### On the EC2 Instance

```bash
# Install agentcookie
cd /tmp
curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/agentcookie_1.7.0_linux_amd64.tar.gz
tar -xzf agentcookie_1.7.0_linux_amd64.tar.gz
sudo mv agentcookie /usr/local/bin/

# Create config directory
mkdir -p ~/.config/agentcookie

# Copy allowlist from repo
cp ~/property-lookup/agentcookie/blocklist.yaml ~/.config/agentcookie/

# Create sink config (replace with your Mac's Tailscale hostname)
cat > ~/.config/agentcookie/sink.yaml << 'EOF'
listen:
  addr: $(tailscale ip -4):9999
peer:
  hostname: your-mac.tail-xxxxx.ts.net
live_cdp:
  enabled: true
  endpoint: http://127.0.0.1:9223
skip_chrome_sqlite: true
EOF
```

#### On Your Mac

```bash
# Install agentcookie
cd /tmp
curl -LO https://github.com/mvanhorn/agentcookie/releases/download/v1.7.0/agentcookie_1.7.0_darwin_universal.tar.gz
tar -xzf agentcookie_1.7.0_darwin_universal.tar.gz
sudo mv agentcookie /usr/local/bin/

# Copy allowlist
mkdir -p ~/.config/agentcookie
# Copy blocklist.yaml from repo

# Run pairing wizard
agentcookie wizard install --as source --peer $(terraform output -raw public_ip | sed 's/\./-/g')
# Note the pairing code
```

#### Complete Pairing

Back on EC2:
```bash
# Use code from Mac wizard
agentcookie pair --as sink --peer your-mac.tail-xxxxx.ts.net \
  --code ABCD-EFGH-IJKL \
  --pair-url http://your-mac.tail-xxxxx.ts.net:9998/pair

# Run sink
agentcookie sink &

# Verify
agentcookie status --json
```

### 3. Test the API

```bash
# Get your API token from terraform.tfvars

# Via Tailscale
curl http://$(terraform output -raw public_ip):8000/health

# Test lookup (with auth if api_token is set)
curl -X POST http://$(terraform output -raw public_ip):8000/lookups \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"address": "48 Amsterdam Ave, Bridgeport, CT 06606"}'

# Get job status
curl http://$(terraform output -raw public_ip):8000/lookups/JOB_ID \
  -H "Authorization: Bearer YOUR_API_TOKEN"
```

## Updating

To update the application:

```bash
# SSH to instance
ssh ubuntu@$(terraform output -raw public_ip)

# Update code
cd property-lookup
git pull

# Restart services
docker compose down
docker compose up -d

# Watch logs
docker compose logs -f
```

Or use Terraform to trigger a new deployment:

```bash
# Change repo_ref in terraform.tfvars, then:
terraform apply
```

## Costs

Estimated monthly costs (us-east-1, as of 2024):
- EC2 t3.medium: ~$30/month
- EBS gp3 20GB: ~$2/month
- Elastic IP (if allocated): ~$3.60/month (when attached: $0)
- Data transfer: Variable

**Total: ~$32-36/month** (excluding data transfer)

## Cleanup

```bash
terraform destroy
```

## Security Notes

1. Never commit `terraform.tfvars` (contains secrets)
2. Use `api_token` in production
3. Restrict `ssh_allowed_cidrs` to your IP or Tailscale network
4. Keep `api_allowed_cidrs` empty and use Tailscale for API access
5. Regularly update the Ubuntu AMI and Docker images
6. Consider using AWS Secrets Manager for sensitive variables
7. Enable CloudWatch logs for monitoring

## Troubleshooting

### Service Not Starting

```bash
# Check user-data logs
tail -f /var/log/user-data.log

# Check Docker logs
docker compose logs -f
```

### Chrome Not Accessible

```bash
# Verify Chrome CDP port
curl http://localhost:9223/json/version

# Restart services
docker compose restart
```

### Tailscale Issues

```bash
# Check Tailscale status
sudo tailscale status

# Re-authenticate
sudo tailscale up --ssh
```
