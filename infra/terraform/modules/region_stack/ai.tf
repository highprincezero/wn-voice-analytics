resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${var.name_prefix}-${var.region_name}"
  location              = var.openai_location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${var.name_prefix}-${var.region_name}"
}

resource "azurerm_cognitive_deployment" "transcribe" {
  name                 = "gpt-4o-transcribe"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4o-transcribe"
    version = "2025-03-20"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 30
  }
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = "gpt-4.1-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 80
  }
}

resource "azurerm_cognitive_account" "safety" {
  name                = "cs-${var.name_prefix}-${var.region_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  kind                = "ContentSafety"
  sku_name            = "S0"
}
