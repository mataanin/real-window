output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.property_lookup.id
}

output "public_ip" {
  description = "Public IP address"
  value       = var.allocate_elastic_ip ? aws_eip.this[0].public_ip : aws_instance.property_lookup.public_ip
}

output "public_dns" {
  description = "Public DNS name"
  value       = aws_instance.property_lookup.public_dns
}

output "ssm_session_command" {
  description = "AWS SSM Session Manager command"
  value       = "aws ssm start-session --target ${aws_instance.property_lookup.id}"
}

output "ssm_ssh_config" {
  description = "SSH config snippet for SSH over SSM"
  value       = <<-EOF
    # Add to ~/.ssh/config:
    Host ${aws_instance.property_lookup.id}
        User ubuntu
        ProxyCommand aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p
  EOF
}

output "ssm_port_forward_command" {
  description = "Port forward command for local API access"
  value       = "aws ssm start-session --target ${aws_instance.property_lookup.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=8000,localPortNumber=8000"
}

output "ssh_command" {
  description = "Direct SSH command (only if ssh_allowed_cidrs is set)"
  value = length(var.ssh_allowed_cidrs) > 0 ? format(
    "ssh ubuntu@%s",
    var.allocate_elastic_ip ? aws_eip.this[0].public_ip : aws_instance.property_lookup.public_ip
  ) : "Direct SSH disabled - use SSM Session Manager"
}

output "api_url" {
  description = "API URL (if publicly accessible)"
  value = length(var.api_allowed_cidrs) > 0 ? format(
    "http://%s:%d",
    var.allocate_elastic_ip ? aws_eip.this[0].public_ip : aws_instance.property_lookup.public_ip,
    var.api_port
  ) : "API not publicly accessible - use Tailscale"
}

output "tailscale_enabled" {
  description = "Whether Tailscale was configured"
  value       = var.tailscale_auth_key != null ? "yes" : "no"
  sensitive   = true
}
