variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "openapi_spec" {
  type        = string
  description = "Rendered OpenAPI 2 (Swagger) document with x-google-backend addresses (base64-encoded)."
}

variable "gateway_service_account" {
  type        = string
  description = "SA the gateway uses to call backend Cloud Run services (run.invoker)."
}

variable "labels" {
  type    = map(string)
  default = {}
}
