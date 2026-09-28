data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "this" {
  name     = "rg-${var.name_prefix}-${var.region_name}"
  location = var.location
}

locals {
  storage_name = substr(replace("${var.name_prefix}${var.region_name}st", "-", ""), 0, 24)
  key_vault    = substr(replace("kv${var.name_prefix}${var.region_name}", "-", ""), 0, 24)
}
