output "ecr_url" {
  description = "URL do repositório ECR da imagem"
  value       = aws_ecr_repository.api.repository_url
}

output "banco_endpoint" {
  description = "Endpoint (host:porta) do PostgreSQL"
  value       = aws_db_instance.banco.endpoint
}
