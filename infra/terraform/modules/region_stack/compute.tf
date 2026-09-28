locals {
  database_url = "postgresql+psycopg://voiceadmin:${var.db_admin_password}@${azurerm_postgresql_flexible_server.this.fqdn}:5432/voice?sslmode=require"
}

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${var.name_prefix}-${var.region_name}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
}

resource "azurerm_container_app" "api" {
  name                         = "ca-api-${var.region_name}"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"

  identity {
    type = "SystemAssigned"
  }

  secret {
    name  = "jwt-secret"
    value = var.jwt_secret
  }
  secret {
    name  = "database-url"
    value = local.database_url
  }
  secret {
    name  = "storage-connection"
    value = azurerm_storage_account.this.primary_connection_string
  }
  secret {
    name  = "servicebus-connection"
    value = azurerm_servicebus_namespace_authorization_rule.worker.primary_connection_string
  }
  secret {
    name  = "openai-key"
    value = azurerm_cognitive_account.openai.primary_access_key
  }
  secret {
    name  = "safety-key"
    value = azurerm_cognitive_account.safety.primary_access_key
  }

  template {
    min_replicas = 2
    max_replicas = 8

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 1
      memory = "2Gi"

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "JWT_SECRET"
        secret_name = "jwt-secret"
      }
      env {
        name  = "LLM_PROVIDER"
        value = "azure"
      }
      env {
        name  = "SAFETY_PROVIDER"
        value = "azure"
      }
      env {
        name  = "BLOB_PROVIDER"
        value = "azure"
      }
      env {
        name  = "ANALYSIS_MODE"
        value = "celery"
      }
      env {
        name  = "BROKER"
        value = "servicebus"
      }
      env {
        name        = "AZURE_STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection"
      }
      env {
        name  = "AZURE_STORAGE_CONTAINER"
        value = azurerm_storage_container.voice.name
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      env {
        name        = "AZURE_OPENAI_API_KEY"
        secret_name = "openai-key"
      }
      env {
        name  = "AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT"
        value = azurerm_cognitive_deployment.transcribe.name
      }
      env {
        name  = "AZURE_OPENAI_CHAT_DEPLOYMENT"
        value = azurerm_cognitive_deployment.chat.name
      }
      env {
        name  = "AZURE_CONTENT_SAFETY_ENDPOINT"
        value = azurerm_cognitive_account.safety.endpoint
      }
      env {
        name        = "AZURE_CONTENT_SAFETY_KEY"
        secret_name = "safety-key"
      }
      env {
        name        = "SERVICE_BUS_CONNECTION_STRING"
        secret_name = "servicebus-connection"
      }
      env {
        name  = "HOME_REGION"
        value = var.region_name
      }
      env {
        name  = "OTEL_ENABLED"
        value = "true"
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = "voice-analytics-api-${var.region_name}"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

resource "azurerm_container_app" "worker" {
  name                         = "ca-worker-${var.region_name}"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"

  secret {
    name  = "database-url"
    value = local.database_url
  }
  secret {
    name  = "storage-connection"
    value = azurerm_storage_account.this.primary_connection_string
  }
  secret {
    name  = "servicebus-connection"
    value = azurerm_servicebus_namespace_authorization_rule.worker.primary_connection_string
  }
  secret {
    name  = "openai-key"
    value = azurerm_cognitive_account.openai.primary_access_key
  }
  secret {
    name  = "safety-key"
    value = azurerm_cognitive_account.safety.primary_access_key
  }

  template {
    min_replicas = 1
    max_replicas = 20

    container {
      name    = "worker"
      image   = var.worker_image
      cpu     = 1
      memory  = "2Gi"
      command = ["python", "-m", "app.jobs.service_bus_worker"]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name  = "LLM_PROVIDER"
        value = "azure"
      }
      env {
        name  = "SAFETY_PROVIDER"
        value = "azure"
      }
      env {
        name  = "BLOB_PROVIDER"
        value = "azure"
      }
      env {
        name  = "BROKER"
        value = "servicebus"
      }
      env {
        name        = "AZURE_STORAGE_CONNECTION_STRING"
        secret_name = "storage-connection"
      }
      env {
        name  = "AZURE_STORAGE_CONTAINER"
        value = azurerm_storage_container.voice.name
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      env {
        name        = "AZURE_OPENAI_API_KEY"
        secret_name = "openai-key"
      }
      env {
        name  = "AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT"
        value = azurerm_cognitive_deployment.transcribe.name
      }
      env {
        name  = "AZURE_OPENAI_CHAT_DEPLOYMENT"
        value = azurerm_cognitive_deployment.chat.name
      }
      env {
        name  = "AZURE_CONTENT_SAFETY_ENDPOINT"
        value = azurerm_cognitive_account.safety.endpoint
      }
      env {
        name        = "AZURE_CONTENT_SAFETY_KEY"
        secret_name = "safety-key"
      }
      env {
        name        = "SERVICE_BUS_CONNECTION_STRING"
        secret_name = "servicebus-connection"
      }
      env {
        name  = "HOME_REGION"
        value = var.region_name
      }
      env {
        name  = "OTEL_ENABLED"
        value = "true"
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = "voice-analytics-worker-${var.region_name}"
      }
    }
  }
}

resource "azurerm_container_app_job" "rollup" {
  name                         = "caj-rollup-${var.region_name}"
  location                     = azurerm_resource_group.this.location
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 1

  schedule_trigger_config {
    cron_expression          = "*/15 * * * *"
    parallelism              = 1
    replica_completion_count = 1
  }

  secret {
    name  = "database-url"
    value = local.database_url
  }

  template {
    container {
      name    = "rollup"
      image   = var.worker_image
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python", "-m", "app.jobs.run_scheduled_rollup"]

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name  = "LLM_PROVIDER"
        value = "azure"
      }
      env {
        name  = "HOME_REGION"
        value = var.region_name
      }
    }
  }
}

resource "azurerm_api_management" "this" {
  name                = "apim-${var.name_prefix}-${var.region_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  publisher_name      = "Voice Analytics"
  publisher_email     = "platform@example.com"
  sku_name            = "Consumption_0"
}

resource "azurerm_api_management_api" "voice" {
  name                  = "voice"
  resource_group_name   = azurerm_resource_group.this.name
  api_management_name   = azurerm_api_management.this.name
  revision              = "1"
  display_name          = "Voice Analytics"
  path                  = "voice"
  protocols             = ["https"]
  subscription_required = false
}
