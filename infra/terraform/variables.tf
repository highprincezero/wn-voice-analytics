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
