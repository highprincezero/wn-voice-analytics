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

resource "azurerm_servicebus_queue" "transcription" {
  name         = "transcription"
  namespace_id = azurerm_servicebus_namespace.this.id
}

resource "azurerm_servicebus_queue" "llm_layer1" {
  name         = "llm-layer1"
  namespace_id = azurerm_servicebus_namespace.this.id
}

resource "azurerm_servicebus_queue" "llm_layer2" {
  name         = "llm-layer2"
  namespace_id = azurerm_servicebus_namespace.this.id
}

resource "azurerm_servicebus_queue" "rollup" {
  name         = "rollup"
  namespace_id = azurerm_servicebus_namespace.this.id
}
