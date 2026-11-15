provider "aws" {
  region = var.regiao

  default_tags {
    tags = {
      Project     = "tech-challenge-f4"
      Fase        = "04"
      Repositorio = "tech-challenge-f4-os-service"
      ManagedBy   = "terraform"
    }
  }
}
