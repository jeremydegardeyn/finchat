variable "project_id" { type = string }
variable "env" { type = string }
variable "name_prefix" {
  type    = string
  default = "finchat"
}

variable "info_types" {
  type        = list(string)
  description = "DLP infoTypes to inspect/de-identify."
  default     = ["EMAIL_ADDRESS", "PERSON_NAME", "PHONE_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "CREDIT_CARD_NUMBER", "IBAN_CODE"]
}
