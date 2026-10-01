# Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.
resource "azurerm_postgresql_flexible_server" "this" {
  name                          = "psql-${var.name_prefix}-${var.region_name}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  version                       = "16"
  delegated_subnet_id           = azurerm_subnet.data.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  administrator_login           = "voiceadmin"
  administrator_password        = var.db_admin_password
  storage_mb                    = 131072
  sku_name                      = "GP_Standard_D4ds_v5"
  public_network_access_enabled = false
  zone                          = var.postgres_zone
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = true

  high_availability {
    mode                      = "ZoneRedundant"
    standby_availability_zone = var.postgres_standby_zone
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]
}

resource "azurerm_postgresql_flexible_server_database" "voice" {
  name      = "voice"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_storage_account" "this" {
  name                            = local.storage_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "GZRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true
  }
}

resource "azurerm_storage_container" "voice" {
  name                  = "voice"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# Standard is not zone redundant. Premium would be, and is not used here.
resource "azurerm_redis_cache" "this" {
  name                 = "redis-${var.name_prefix}-${var.region_name}"
  location             = azurerm_resource_group.this.location
  resource_group_name  = azurerm_resource_group.this.name
  capacity             = 1
  family               = "C"
  sku_name             = "Standard"
  minimum_tls_version  = "1.2"
  non_ssl_port_enabled = false
}

resource "azurerm_key_vault" "this" {
  name                       = local.key_vault
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = true
  soft_delete_retention_days = 7
  rbac_authorization_enabled = true
}
