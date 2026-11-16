locals {
  nome    = "tech-challenge-f4-os-service"
  prefixo = "/tech-challenge-f4"
}

# Rede e cluster pertencem à plataforma; aqui só se lê o que ela publicou.
data "aws_ssm_parameter" "vpc_id" {
  name = "${local.prefixo}/rede/vpc_id"
}

data "aws_ssm_parameter" "subnets_privadas" {
  name = "${local.prefixo}/rede/subnets_privadas"
}

data "aws_ssm_parameter" "cluster_sg_id" {
  name = "${local.prefixo}/cluster/sg_id"
}

locals {
  subnets_privadas = split(",", data.aws_ssm_parameter.subnets_privadas.value)
}

resource "aws_ecr_repository" "api" {
  name         = local.nome
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Mantém as 10 últimas imagens"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_db_subnet_group" "banco" {
  name       = "${local.nome}-db"
  subnet_ids = local.subnets_privadas
}

resource "aws_security_group" "banco" {
  name        = "${local.nome}-db"
  description = "PostgreSQL do os-service, aberto apenas para o cluster"
  vpc_id      = data.aws_ssm_parameter.vpc_id.value
}

resource "aws_vpc_security_group_ingress_rule" "banco_do_cluster" {
  security_group_id            = aws_security_group.banco.id
  referenced_security_group_id = data.aws_ssm_parameter.cluster_sg_id.value
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL a partir do cluster"
}

# Sem / @ " e espaço, que o RDS recusa ou que quebrariam a URL de conexão.
resource "random_password" "banco" {
  length           = 24
  special          = true
  override_special = "_-"
}

resource "aws_db_instance" "banco" {
  identifier     = "${local.nome}-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t3.micro"

  allocated_storage = 20
  storage_type      = "gp2"
  storage_encrypted = true

  db_name  = "os"
  username = "os"
  password = random_password.banco.result

  db_subnet_group_name   = aws_db_subnet_group.banco.name
  vpc_security_group_ids = [aws_security_group.banco.id]
  publicly_accessible    = false

  # Ambiente efêmero: sem snapshot final, para destruir sem sobras; backup automático de uma semana.
  skip_final_snapshot     = true
  deletion_protection     = false
  backup_retention_period = 7
  apply_immediately       = true

  monitoring_interval          = 0
  performance_insights_enabled = false
}

resource "aws_ssm_parameter" "database_url" {
  name  = "${local.prefixo}/os-service/database_url"
  type  = "SecureString"
  value = "postgresql://os:${random_password.banco.result}@${aws_db_instance.banco.endpoint}/os?sslmode=require"
}
