variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "pl"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 20
}

variable "vpc_id" {
  description = "VPC ID (default VPC if not specified)"
  type        = string
  default     = null
}

variable "subnet_id" {
  description = "Subnet ID (first available subnet in VPC if not specified)"
  type        = string
  default     = null
}

variable "ssh_allowed_cidrs" {
  description = "CIDR blocks allowed to SSH (empty = disabled, use SSM Session Manager)"
  type        = list(string)
  default     = []
}

variable "api_port" {
  description = "API port"
  type        = number
  default     = 8000
}

variable "api_allowed_cidrs" {
  description = "CIDR blocks allowed to access API (empty = closed, access via Tailscale)"
  type        = list(string)
  default     = []
}

variable "key_pair_name" {
  description = "Existing EC2 key pair name (mutually exclusive with public_key_path)"
  type        = string
  default     = null
}

variable "public_key_path" {
  description = "Path to SSH public key file (mutually exclusive with key_pair_name)"
  type        = string
  default     = null
}

variable "allocate_elastic_ip" {
  description = "Allocate and associate an Elastic IP"
  type        = bool
  default     = false
}

variable "tailscale_auth_key" {
  description = "Tailscale auth key (optional, enables Tailscale SSH)"
  type        = string
  sensitive   = true
  default     = null
}

variable "repo_url" {
  description = "Git repository URL to clone"
  type        = string
  default     = "https://github.com/mataanin/real-window.git"
}

variable "repo_ref" {
  description = "Git ref (branch, tag, or commit) to checkout"
  type        = string
  default     = "main"
}

variable "github_token" {
  description = "GitHub personal access token for private repos (use github_token_ssm_parameter instead for better security)"
  type        = string
  sensitive   = true
  default     = null
}

variable "github_deploy_key" {
  description = "SSH deploy key for private repos (stored in cloud-init, prefer SSM parameter)"
  type        = string
  sensitive   = true
  default     = null
}

variable "github_token_ssm_parameter" {
  description = "SSM Parameter Store path containing GitHub token (e.g. /property-lookup/github-token)"
  type        = string
  default     = null
}

variable "api_token" {
  description = "API bearer token for authentication (optional but recommended)"
  type        = string
  sensitive   = true
  default     = null
}

variable "proxy" {
  description = "HTTP proxy URL for Chrome (optional residential proxy)"
  type        = string
  sensitive   = true
  default     = null
}

variable "cache_ttl_hours" {
  description = "Cache TTL in hours"
  type        = number
  default     = 24
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
