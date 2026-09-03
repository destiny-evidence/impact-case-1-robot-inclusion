# Lets the deploy workflow exchange a GitHub OIDC token for an Azure one, so
# there are no long-lived Azure credentials in GitHub.

data "github_repository" "this" {
  full_name = var.github_repo
}

locals {
  github_owner = split("/", var.github_repo)[0]
  github_name  = split("/", var.github_repo)[1]

  oidc_subject = join("", [
    "repo:${local.github_owner}@${var.github_owner_id}",
    "/${local.github_name}@${data.github_repository.this.repo_id}",
    ":environment:${var.environment}",
  ])
}

resource "azuread_application_registration" "github_actions" {
  display_name     = "github-actions-${local.name}"
  sign_in_audience = "AzureADMyOrg"
}

resource "azuread_service_principal" "github_actions" {
  client_id = azuread_application_registration.github_actions.client_id
  owners    = [data.azuread_client_config.current.object_id]
}

resource "azuread_application_flexible_federated_identity_credential" "github_actions" {
  application_id = azuread_application_registration.github_actions.id
  display_name   = "gha-${local.name}"
  audience       = "api://AzureADTokenExchange"
  issuer         = "https://token.actions.githubusercontent.com"

  claims_matching_expression = join(" and ", [
    "claims['sub'] matches '${local.oidc_subject}'",
    "claims['repository_id'] eq '${data.github_repository.this.repo_id}'",
    "claims['repository_owner_id'] eq '${var.github_owner_id}'",
  ])
}

resource "azurerm_role_assignment" "github_actions_acr_push" {
  principal_id         = azuread_service_principal.github_actions.object_id
  scope                = data.azurerm_container_registry.this.id
  role_definition_name = "AcrPush"
}

resource "azurerm_role_assignment" "github_actions_robot" {
  for_each = azurerm_container_app.robot

  principal_id         = azuread_service_principal.github_actions.object_id
  scope                = each.value.id
  role_definition_name = "Contributor"
}

resource "azurerm_role_assignment" "github_actions_container_app_environment" {
  principal_id         = azuread_service_principal.github_actions.object_id
  scope                = azurerm_container_app_environment.this.id
  role_definition_name = "Contributor"
}

resource "azurerm_role_assignment" "github_actions_resource_group_reader" {
  principal_id         = azuread_service_principal.github_actions.object_id
  scope                = azurerm_resource_group.this.id
  role_definition_name = "Reader"
}

resource "github_repository_environment" "this" {
  repository  = local.github_name
  environment = var.environment
}

locals {
  github_environment_variables = {
    AZURE_CLIENT_ID       = azuread_application_registration.github_actions.client_id
    AZURE_TENANT_ID       = data.azurerm_subscription.current.tenant_id
    AZURE_SUBSCRIPTION_ID = data.azurerm_subscription.current.subscription_id
    REGISTRY_NAME         = data.azurerm_container_registry.this.name
    REGISTRY_SERVER       = data.azurerm_container_registry.this.login_server
    APP_NAME              = var.app_name
    RESOURCE_GROUP        = azurerm_resource_group.this.name
    ENVIRONMENT_NAME      = var.environment
    CONTAINER_APP_ENV     = azurerm_container_app_environment.this.name
    CONTAINER_APP_NAMES   = join(" ", sort([for a in azurerm_container_app.robot : a.name]))
  }
}

resource "github_actions_environment_variable" "this" {
  for_each = local.github_environment_variables

  repository    = github_repository_environment.this.repository
  environment   = github_repository_environment.this.environment
  variable_name = each.key
  value         = each.value
}
