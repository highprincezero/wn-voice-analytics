output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "api_fqdn" {
  value = azurerm_container_app.api.ingress[0].fqdn
}

output "apim_gateway_hostname" {
  value = trimprefix(azurerm_api_management.this.gateway_url, "https://")
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.this.fqdn
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}
