terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Bucket e região vêm de -backend-config, pois dependem da conta de quem executa.
  backend "s3" {
    key          = "os-service/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
