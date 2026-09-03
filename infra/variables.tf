variable "app_name" {
  description = "Name for the shared resources (resource group, identity, container app environment) and the ACR image repository. Container apps are named separately."
  type        = string
  default     = "destiny-inclusion-robot"
}

variable "environment" {
  description = "The environment this stack is being deployed to. Also passed to the app as ENV."
  type        = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Allowed values for environment are \"development\", \"staging\", or \"production\"."
  }
}

variable "region" {
  description = "The Azure region resources will be deployed into"
  type        = string
  default     = "swedencentral"
}

variable "budget_code" {
  description = "Budget code for tagging resource groups. Required tag for resource groups"
  type        = string
}

variable "created_by" {
  description = "Creator of this infrastructure"
  type        = string
}

variable "owner" {
  description = "Owner email for this infrastructure"
  type        = string
}

variable "project" {
  description = "Project name for tagging"
  type        = string
  default     = "DESTINY"
}

# Robots
variable "robots" {
  description = "One container app per entry. The key is the `robot` CLI subcommand the container runs."
  type = map(object({
    robot_id         = string
    memory           = optional(string, "1Gi")
    replicas         = optional(number, 1)
    interval_seconds = optional(number, 30)
    batch_size       = optional(number, 10)
    extra_env        = optional(map(string), {})
  }))
  validation {
    condition     = toset(keys(var.robots)) == toset(["query", "prefilter", "llm"])
    error_message = "var.robots must have exactly the keys \"query\", \"prefilter\" and \"llm\"."
  }
}

variable "robot_secrets" {
  description = "HMAC secret each robot uses with the DESTINY repository, keyed to match var.robots."
  type        = map(string)
  sensitive   = true
}

variable "extra_env" {
  description = "Environment variables applied to every robot."
  type        = map(string)
  default     = {}
}

# DESTINY repository
variable "destiny_repository_url" {
  description = "DESTINY repository API endpoint the robots poll"
  type        = string
  default     = "https://api.staging.evidence-repository.org"
}

# LLM
variable "llm_azure_api_base" {
  description = "Base URL for Azure OpenAI"
  type        = string
}

variable "llm_azure_api_key" {
  description = "API key for Azure OpenAI"
  type        = string
  sensitive   = true
}

# Container Registry (shared)
variable "shared_container_registry_name" {
  description = "The name of the shared container registry"
  type        = string
}

variable "shared_resource_group_name" {
  description = "The resource group containing the shared container registry"
  type        = string
}

# GitHub Actions
variable "github_repo" {
  description = "GitHub repository for Actions OIDC"
  type        = string
  default     = "destiny-evidence/impact-case-1-robot-inclusion"
}

variable "github_owner_id" {
  description = "Immutable numeric ID of the GitHub organisation."
  type        = string
}

variable "github_app_id" {
  description = "GitHub App ID for configuring repository environments"
  type        = string
}

variable "github_app_installation_id" {
  description = "GitHub App installation ID"
  type        = string
}

variable "github_app_pem" {
  description = "GitHub App private key PEM file contents"
  type        = string
  sensitive   = true
}
