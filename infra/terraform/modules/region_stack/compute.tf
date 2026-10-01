# Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.
locals {
  database_url = "postgresql+psycopg://voiceadmin:${var.db_admin_password}@${azurerm_postgresql_flexible_server.this.fqdn}:5432/voice?sslmode=require"
  redis_url    = "rediss://:${urlencode(azurerm_redis_cache.this.primary_access_key)}@${azurerm_redis_cache.this.hostname}:${azurerm_redis_cache.this.ssl_port}/0"
  apim_allow_cidrs = [
    for ip in azurerm_api_management.this.public_ip_addresses :
    strcontains(ip, "/") ? ip : "${ip}/32"
  ]
}

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${var.name_prefix}-${var.region_name}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  zone_redundancy_enabled    = true
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
  secret {
    name  = "redis-url"
    value = local.redis_url
  }
  secret {
    name  = "appinsights-connection"
    value = azurerm_application_insights.this.connection_string
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
      # Settings reads this. HS256 is also the default and the algorithm the APIM policy checks.
      env {
        name  = "JWT_ALGORITHM"
        value = "HS256"
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
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection"
      }
      env {
        name        = "REDIS_URL"
        secret_name = "redis-url"
      }
      env {
        name  = "RATE_LIMIT_ENABLED"
        value = "true"
      }
      env {
        name  = "RATE_LIMIT_BACKEND"
        value = "redis"
      }
      env {
        name  = "RATE_LIMIT_REQUESTS"
        value = tostring(var.apim_user_rate_limit)
      }
      env {
        name  = "RATE_LIMIT_WINDOW_SECONDS"
        value = tostring(var.apim_rate_window_seconds)
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

    dynamic "ip_security_restriction" {
      for_each = { for index, cidr in local.apim_allow_cidrs : index => cidr }
      content {
        name             = "apim-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
        description      = "API Management outbound"
      }
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
  secret {
    name  = "appinsights-connection"
    value = azurerm_application_insights.this.connection_string
  }
  # Used only by the scale rule. KEDA reads the queue length, which needs Manage.
  secret {
    name  = "servicebus-scaler-connection"
    value = azurerm_servicebus_namespace_authorization_rule.scaler.primary_connection_string
  }

  template {
    min_replicas = 1
    max_replicas = 20

    # One copy works one message at a time, so ask for one copy per waiting message.
    # KEDA counts waiting messages, not locked ones being worked on.
    custom_scale_rule {
      name             = "transcription-queue"
      custom_rule_type = "azure-servicebus"
      metadata = {
        queueName    = azurerm_servicebus_queue.transcription.name
        messageCount = "1"
      }
      authentication {
        secret_name       = "servicebus-scaler-connection"
        trigger_parameter = "connection"
      }
    }

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
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection"
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
  secret {
    name  = "openai-key"
    value = azurerm_cognitive_account.openai.primary_access_key
  }
  secret {
    name  = "appinsights-connection"
    value = azurerm_application_insights.this.connection_string
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
        name  = "AZURE_OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      env {
        name        = "AZURE_OPENAI_API_KEY"
        secret_name = "openai-key"
      }
      env {
        name  = "AZURE_OPENAI_CHAT_DEPLOYMENT"
        value = azurerm_cognitive_deployment.chat.name
      }
      env {
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection"
      }
    }
  }
}

# Basic supports rate-limit-by-key. Consumption does not.
# Premium would add zones and a virtual network. One Basic unit is rated at about
# 1,000 requests a second. The worst case in docs/scaling.md is about 1,020, so this
# needs a load test before it is trusted.
resource "azurerm_api_management" "this" {
  name                = "apim-${var.name_prefix}-${var.region_name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  publisher_name      = "Voice Analytics"
  publisher_email     = "platform@example.com"
  sku_name            = "Basic_1"
}

resource "azurerm_api_management_api" "voice" {
  name                  = "voice"
  resource_group_name   = azurerm_resource_group.this.name
  api_management_name   = azurerm_api_management.this.name
  revision              = "1"
  display_name          = "Voice Analytics"
  path                  = ""
  protocols             = ["https"]
  service_url           = "https://${azurerm_container_app.api.ingress[0].fqdn}"
  subscription_required = false
}

resource "azurerm_api_management_api_operation" "proxy" {
  for_each            = toset(["GET", "POST", "PUT", "DELETE", "PATCH"])
  operation_id        = "proxy-${lower(each.value)}"
  api_name            = azurerm_api_management_api.voice.name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = azurerm_resource_group.this.name
  display_name        = "Proxy ${each.value}"
  method              = each.value
  url_template        = "/{*path}"

  template_parameter {
    name     = "path"
    required = true
    type     = "string"
  }
}

resource "azurerm_api_management_api_operation" "root" {
  operation_id        = "root-get"
  api_name            = azurerm_api_management_api.voice.name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = azurerm_resource_group.this.name
  display_name        = "Root"
  method              = "GET"
  url_template        = "/"
}

resource "azurerm_api_management_named_value" "jwt_secret" {
  name                = "jwt-secret"
  resource_group_name = azurerm_resource_group.this.name
  api_management_name = azurerm_api_management.this.name
  display_name        = "jwt-secret"
  secret              = true
  value               = base64encode(var.jwt_secret)
}

resource "azurerm_api_management_named_value" "front_door_id" {
  name                = "front-door-id"
  resource_group_name = azurerm_resource_group.this.name
  api_management_name = azurerm_api_management.this.name
  display_name        = "front-door-id"
  secret              = true
  value               = var.front_door_id
}

resource "azurerm_api_management_api_policy" "voice" {
  api_name            = azurerm_api_management_api.voice.name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = azurerm_resource_group.this.name
  xml_content = templatefile("${path.module}/apim_policy.xml.tftpl", {
    user_calls  = var.apim_user_rate_limit
    user_period = var.apim_rate_window_seconds
    anon_calls  = var.apim_anonymous_rate_limit
    anon_period = var.apim_rate_window_seconds
  })

  depends_on = [
    azurerm_api_management_named_value.jwt_secret,
    azurerm_api_management_named_value.front_door_id,
  ]
}
