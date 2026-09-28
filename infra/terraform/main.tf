resource "azurerm_resource_group" "global" {
  name     = "rg-${var.name_prefix}-global"
  location = var.global_location
}

module "region" {
  source   = "./modules/region_stack"
  for_each = { for region in var.regions : region.name => region }

  name_prefix               = var.name_prefix
  region_name               = each.value.name
  location                  = each.value.location
  cidr                      = each.value.cidr
  apps_subnet               = each.value.apps_subnet
  data_subnet               = each.value.data_subnet
  openai_location           = each.value.openai_location
  db_admin_password         = var.db_admin_password
  jwt_secret                = var.jwt_secret
  api_image                 = var.api_image
  worker_image              = var.worker_image
  front_door_id             = azurerm_cdn_frontdoor_profile.entry.resource_guid
  apim_user_rate_limit      = var.apim_user_rate_limit
  apim_anonymous_rate_limit = var.apim_anonymous_rate_limit
  apim_rate_window_seconds  = var.apim_rate_window_seconds
}

resource "azurerm_cdn_frontdoor_profile" "entry" {
  name                = "afd-${var.name_prefix}"
  resource_group_name = azurerm_resource_group.global.name
  sku_name            = "Standard_AzureFrontDoor"
}

resource "azurerm_cdn_frontdoor_endpoint" "entry" {
  name                     = "voice-${var.name_prefix}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.entry.id
}

resource "azurerm_cdn_frontdoor_origin_group" "api" {
  name                     = "regional-api"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.entry.id

  load_balancing {
    sample_size                 = 4
    successful_samples_required = 3
  }

  health_probe {
    interval_in_seconds = 120
    path                = "/api/v1/health"
    protocol            = "Https"
    request_type        = "GET"
  }
}

resource "azurerm_cdn_frontdoor_origin" "api" {
  for_each = module.region

  name                           = each.key
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.api.id
  host_name                      = each.value.apim_gateway_hostname
  origin_host_header             = each.value.apim_gateway_hostname
  certificate_name_check_enabled = true
  http_port                      = 80
  https_port                     = 443
  priority                       = 1
  weight                         = 1000
}

resource "azurerm_cdn_frontdoor_route" "api" {
  name                          = "api"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.entry.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.api.id
  cdn_frontdoor_origin_ids      = [for origin in azurerm_cdn_frontdoor_origin.api : origin.id]
  supported_protocols           = ["Http", "Https"]
  patterns_to_match             = ["/*"]
  forwarding_protocol           = "HttpsOnly"
  https_redirect_enabled        = true
  link_to_default_domain        = true
}
