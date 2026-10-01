# Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.
# Standard is not zone redundant. Premium would be, and is not used here.
resource "azurerm_servicebus_namespace" "this" {
  name                = "sb-${var.name_prefix}-${var.region_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Standard"
}

resource "azurerm_servicebus_namespace_authorization_rule" "worker" {
  name         = "worker"
  namespace_id = azurerm_servicebus_namespace.this.id
  listen       = true
  send         = true
  manage       = false
}

# The worker scale rule reads queue lengths. That needs Manage, so it gets its own rule
# and the apps keep the listen and send rule above.
resource "azurerm_servicebus_namespace_authorization_rule" "scaler" {
  name         = "scaler"
  namespace_id = azurerm_servicebus_namespace.this.id
  listen       = true
  send         = true
  manage       = true
}

# Every queue uses PT5M, the longest lock Service Bus allows. The worker does not renew
# locks, so a job that runs longer than 5 minutes is delivered again.
resource "azurerm_servicebus_queue" "transcription" {
  name          = "transcription"
  namespace_id  = azurerm_servicebus_namespace.this.id
  lock_duration = "PT5M"
}

resource "azurerm_servicebus_queue" "llm_layer1" {
  name          = "llm-layer1"
  namespace_id  = azurerm_servicebus_namespace.this.id
  lock_duration = "PT5M"
}

resource "azurerm_servicebus_queue" "llm_layer2" {
  name          = "llm-layer2"
  namespace_id  = azurerm_servicebus_namespace.this.id
  lock_duration = "PT5M"
}

resource "azurerm_servicebus_queue" "rollup" {
  name          = "rollup"
  namespace_id  = azurerm_servicebus_namespace.this.id
  lock_duration = "PT5M"
}
