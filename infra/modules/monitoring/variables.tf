variable "project_id" { type = string }
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "notification_email" {
  type        = string
  description = "Email for alert + budget notifications. Empty disables channel creation."
  default     = ""
}

variable "dlq_subscription_id" {
  type        = string
  description = "DLQ subscription short id to alarm on (undelivered messages)."
  default     = ""
}
