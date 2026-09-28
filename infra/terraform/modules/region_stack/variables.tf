variable "name_prefix" {
  type = string
}

variable "region_name" {
  type = string
}

variable "location" {
  type = string
}

variable "cidr" {
  type = string
}

variable "apps_subnet" {
  type = string
}

variable "data_subnet" {
  type = string
}

variable "openai_location" {
  type = string
}

variable "db_admin_password" {
  type      = string
  sensitive = true
}

variable "jwt_secret" {
  type      = string
  sensitive = true
}

variable "api_image" {
  type = string
}

variable "worker_image" {
  type = string
}

variable "front_door_id" {
  type        = string
  description = "Front Door profile resource GUID. APIM requires this value in X-Azure-FDID."
}

variable "apim_user_rate_limit" {
  type        = number
  description = "Authenticated requests per user per window. The counter key is the JWT sub claim."
  default     = 120
}

variable "apim_anonymous_rate_limit" {
  type        = number
  description = "Unauthenticated requests per source IP per window."
  default     = 60
}

variable "apim_rate_window_seconds" {
  type    = number
  default = 60
}

variable "postgres_zone" {
  type        = string
  description = "Primary availability zone. The region must support this zone."
  default     = "1"
}

variable "postgres_standby_zone" {
  type        = string
  description = "Standby availability zone for zone-redundant high availability."
  default     = "2"
}
