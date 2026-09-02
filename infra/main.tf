# This deploys the toy robot as a container application into Azure. You may choose to do this differently.

# This is where the container app will pull images from
data "azurerm_container_registry" "destiny_shared_infra" {
  name                = var.container_registry_name
  resource_group_name = var.container_registry_resource_group_name
}

# This might exist for you if your robot has already been deployed.
# In this case, you can use a data resource instead https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/resource_group
resource "azurerm_resource_group" "robot_resource_group" {
  name     = "rg-${var.robot_name}-${var.environment}"
  location = "swedencentral"
}

# Create a user assigned identity for our robot. This is the identity used when authenticating.
resource "azurerm_user_assigned_identity" "toy_robot" {
  location            = azurerm_resource_group.robot_resource_group.location
  name                = var.robot_name
  resource_group_name = azurerm_resource_group.robot_resource_group.name
}

# This creates a container app to run the toy robot in
module "container_app_toy_robot" {
  source                          = "app.terraform.io/destiny-evidence/container-app/azure"
  version                         = "1.3.0"
  app_name                        = var.robot_name
  environment                     = var.environment # toy-robot is for an example enhancement flow only.
  container_registry_id           = data.azurerm_container_registry.destiny_shared_infra.id
  container_registry_login_server = data.azurerm_container_registry.destiny_shared_infra.login_server
  resource_group_name             = azurerm_resource_group.robot_resource_group.name
  region                          = azurerm_resource_group.robot_resource_group.location

  # Polling configuration
  env_vars = [
    {
      name  = "DESTINY_REPOSITORY_URL"
      value = var.destiny_repository_url
    },
    {
      name  = "ROBOT_ID"
      value = var.robot_id
    },
    {
      name        = "ROBOT_SECRET"
      secret_name = "robot-secret"
    },
    {
      name  = "POLL_INTERVAL_SECONDS",
      value = "30"
    },
    {
      name  = "BATCH_SIZE",
      value = "5"
    },
    {
      name  = "OTEL_SERVICE_NAME",
      value = "${var.robot_name}-${var.environment}"
    },
    {
      name  = "OTEL_TRACES_EXPORTER",
      value = "otlp"
    },
    {
      name  = "OTEL_LOGS_EXPORTER",
      value = "otlp"
    },
    {
      name  = "OTEL_EXPORTER_OTLP_PROTOCOL",
      value = "http/protobuf"
    },
    {
      name  = "OTEL_EXPORTER_OTLP_ENDPOINT",
      value = var.honeycomb_endpoint
    },
    {
      name        = "OTEL_EXPORTER_OTLP_HEADERS",
      secret_name = "honeycomb-api-header"
    },
    {
      name  = "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED",
      value = "true"
    }
  ]

  secrets = [
    {
      name  = "robot-secret",
      value = var.robot_secret
    },
    {
      name  = "honeycomb-api-header",
      value = "x-honeycomb-team=${var.honeycomb_api_key}"
    }
  ]

  # You can see here that we're passing the user assigned identity that we created above to the client application.
  # This identity has the robot role assignment and will allow the robot to authenticate with destiny repository.
  identity = {
    id           = azurerm_user_assigned_identity.toy_robot.id
    principal_id = azurerm_user_assigned_identity.toy_robot.principal_id
    client_id    = azurerm_user_assigned_identity.toy_robot.client_id
  }
}
