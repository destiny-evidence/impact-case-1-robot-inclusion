locals {
  shortname = substr(var.environment, 0, 4)

  name            = "${var.app_name}-${local.shortname}"
  robot_app_names = { for k in keys(var.robots) : k => "destiny-${k}-robot-${local.shortname}-app" }
  minimum_resource_tags = {
    "Created by"  = var.created_by
    "Environment" = var.environment
    "Owner"       = var.owner
    "Project"     = var.project
    "Region"      = var.region
  }
}
