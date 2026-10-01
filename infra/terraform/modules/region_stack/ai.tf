# Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.
resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${var.name_prefix}-${var.region_name}"
  location              = var.openai_location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${var.name_prefix}-${var.region_name}"
}

# gpt-4o-transcribe is offered only as GlobalStandard, so audio may be processed in any
# Azure region. Microsoft lists version 2025-03-20 for retirement on 2026-10-15 with no
# replacement named yet. Check the retirement schedule before an apply.
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
  name                 = "gpt-5-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-5-mini"
    version = "2025-08-07"
  }

  # DataZoneStandard keeps processing inside the US or EU data zone of openai_location.
  sku {
    name     = "DataZoneStandard"
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
