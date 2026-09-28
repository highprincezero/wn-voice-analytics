output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "api_fqdn" {
  value = azurerm_container_app.api.ingress[0].fqdn
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.this.fqdn
}

output "storage_account_name" {
  value = azurerm_storage_account.this.name
}
