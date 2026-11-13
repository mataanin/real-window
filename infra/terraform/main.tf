terraform {
  required_version = ">= 1.0"
}

data "aws_vpc" "default" {
  default = var.vpc_id == null
  id      = var.vpc_id
}

data "aws_subnets" "selected" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_subnet" "selected" {
  id = var.subnet_id != null ? var.subnet_id : sort(data.aws_subnets.selected.ids)[0]
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_security_group" "property_lookup" {
  name        = "${var.name_prefix}-property-lookup"
  description = "Security group for property-lookup API"
  vpc_id      = data.aws_vpc.default.id

  tags = merge(
    var.tags,
    {
      Name = "${var.name_prefix}-property-lookup"
    }
  )
}

resource "aws_security_group_rule" "ssh" {
  count             = length(var.ssh_allowed_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = var.ssh_allowed_cidrs
  security_group_id = aws_security_group.property_lookup.id
  description       = "SSH access (prefer SSM Session Manager)"
}

resource "aws_security_group_rule" "api" {
  count             = length(var.api_allowed_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  from_port         = var.api_port
  to_port           = var.api_port
  protocol          = "tcp"
  cidr_blocks       = var.api_allowed_cidrs
  security_group_id = aws_security_group.property_lookup.id
  description       = "API access"
}

resource "aws_security_group_rule" "egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.property_lookup.id
  description       = "Allow all outbound"
}

resource "aws_iam_role" "this" {
  name = "${var.name_prefix}-property-lookup-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "parameter_store" {
  count = var.github_token_ssm_parameter != null ? 1 : 0
  name  = "parameter-store-read"
  role  = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "arn:aws:ssm:${var.region}:*:parameter${var.github_token_ssm_parameter}"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name_prefix}-property-lookup-instance"
  role = aws_iam_role.this.name

  tags = var.tags
}

resource "aws_key_pair" "this" {
  count      = var.key_pair_name == null && var.public_key_path != null ? 1 : 0
  key_name   = "${var.name_prefix}-property-lookup"
  public_key = file(var.public_key_path)

  tags = var.tags
}

locals {
  key_name = var.key_pair_name != null ? var.key_pair_name : (
    var.public_key_path != null ? aws_key_pair.this[0].key_name : null
  )

  user_data = templatefile("${path.module}/user_data.sh", {
    region                     = var.region
    tailscale_auth_key         = var.tailscale_auth_key
    repo_url                   = var.repo_url
    repo_ref                   = var.repo_ref
    github_token               = var.github_token
    github_deploy_key          = var.github_deploy_key
    github_token_ssm_parameter = var.github_token_ssm_parameter
    api_token                  = var.api_token
    proxy                      = var.proxy
    cache_ttl_hours            = var.cache_ttl_hours
  })
}

resource "aws_instance" "property_lookup" {
  ami                  = data.aws_ami.ubuntu.id
  instance_type        = var.instance_type
  key_name             = local.key_name
  subnet_id            = data.aws_subnet.selected.id
  iam_instance_profile = aws_iam_instance_profile.this.name

  vpc_security_group_ids = [aws_security_group.property_lookup.id]

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  user_data                   = local.user_data
  user_data_replace_on_change = true

  tags = merge(
    var.tags,
    {
      Name = "${var.name_prefix}-property-lookup"
    }
  )
}

resource "aws_eip" "this" {
  count    = var.allocate_elastic_ip ? 1 : 0
  domain   = "vpc"
  instance = aws_instance.property_lookup.id

  tags = merge(
    var.tags,
    {
      Name = "${var.name_prefix}-property-lookup"
    }
  )
}
