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
