# Applicable only for cloud provisioning. This file is used only when deployed to the cloud with a multi-region deployment.
variable "name_prefix" {
  description = "Short globally unique prefix. Change this before a real apply."
  type        = string
  default     = "vanpoc01"
}

variable "global_location" {
  description = "Region for the global resource group and Front Door profile."
  type        = string
  default     = "eastus"
}

variable "db_admin_password" {
  description = "PostgreSQL administrator password. Use a URL-safe value."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "HMAC secret for API access tokens."
  type        = string
  sensitive   = true
}

variable "api_image" {
  description = "Container image for the FastAPI service."
  type        = string
  default     = "ghcr.io/highprincezero/wn-voice-analytics-api:latest"
}

variable "apim_user_rate_limit" {
  description = "Authenticated API Management calls per user per window, keyed on the JWT sub claim."
  type        = number
  default     = 120
}

variable "apim_anonymous_rate_limit" {
  description = "Unauthenticated API Management calls per source IP per window."
  type        = number
  default     = 60
}

variable "apim_rate_window_seconds" {
  description = "Rate-limit window for API Management and the in-process Redis limiter."
  type        = number
  default     = 60
}

variable "worker_image" {
  description = "Container image for workers and the scheduled rollup job."
  type        = string
  default     = "ghcr.io/highprincezero/wn-voice-analytics-api:latest"
}

variable "regions" {
  description = "One entry per region. The length of this list is N."
  type = list(object({
    name            = string
    location        = string
    cidr            = string
    apps_subnet     = string
    data_subnet     = string
    openai_location = string
  }))
  default = [
    {
      name            = "eus"
      location        = "eastus"
      cidr            = "10.10.0.0/16"
      apps_subnet     = "10.10.0.0/23"
      data_subnet     = "10.10.2.0/24"
      openai_location = "eastus"
    },
    {
      name            = "weu"
      location        = "westeurope"
      cidr            = "10.20.0.0/16"
      apps_subnet     = "10.20.0.0/23"
      data_subnet     = "10.20.2.0/24"
      openai_location = "westeurope"
    },
  ]
}
