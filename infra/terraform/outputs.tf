output "regions" {
  value = {
    for name, region in module.region : name => {
      resource_group = region.resource_group_name
      api_fqdn       = region.api_fqdn
      apim_gateway   = region.apim_gateway_hostname
      postgres_fqdn  = region.postgres_fqdn
      storage        = region.storage_account_name
    }
  }
}

output "front_door_endpoint" {
  value = azurerm_cdn_frontdoor_endpoint.entry.host_name
}
