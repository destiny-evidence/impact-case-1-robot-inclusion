terraform {
  required_version = ">= 1.9"

  cloud {
    organization = "destiny-evidence"

    workspaces {
      project = "DESTINY"
      tags    = ["impact-case-1-robot-inclusion"]
    }
  }

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.1"
    }

    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.8"
    }

    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }
}

provider "azurerm" {
  features {}
}

provider "azuread" {
}

provider "github" {
  owner = split("/", var.github_repo)[0]
  app_auth {
    id              = var.github_app_id
    installation_id = var.github_app_installation_id
    pem_file        = var.github_app_pem
  }
}
