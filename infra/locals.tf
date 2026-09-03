locals {
  # Container apps are capped at 32 characters; the managed environment allows 60.
  shortname = substr(var.environment, 0, 4)

  name            = "${var.app_name}-${var.environment}"
  robot_app_names = { for k in keys(var.robots) : k => "destiny-${k}-robot-${local.shortname}-app" }
  minimum_resource_tags = {
    "Created by"  = var.created_by
    "Environment" = var.environment
    "Owner"       = var.owner
    "Project"     = var.project
    "Region"      = var.region
  }
}
