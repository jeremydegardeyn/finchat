# Enterprise network: a custom VPC with a private subnet (Private Google Access),
# secondary ranges for GKE pods/services, Cloud NAT for egress, a PSC endpoint for
# Google APIs, and a default-deny ingress posture. Reference overlay — not applied.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "subnet_cidr" { type = string }
variable "pods_cidr" { type = string }
variable "services_cidr" { type = string }

resource "google_compute_network" "vpc" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-${var.env}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "primary" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-${var.env}-subnet"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "router" {
  project = var.project_id
  name    = "${var.name_prefix}-${var.env}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-${var.env}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# Private Service Connect endpoint so workloads reach Google APIs over private IP.
resource "google_compute_global_address" "psc_apis" {
  project      = var.project_id
  name         = "${var.name_prefix}-${var.env}-psc-googleapis"
  purpose      = "PRIVATE_SERVICE_CONNECT"
  address_type = "INTERNAL"
  address      = "10.0.0.100"
  network      = google_compute_network.vpc.id
}

resource "google_compute_global_forwarding_rule" "psc_apis" {
  project               = var.project_id
  name                  = "${var.name_prefix}-${var.env}-psc-googleapis"
  target                = "all-apis"
  network               = google_compute_network.vpc.id
  ip_address            = google_compute_global_address.psc_apis.id
  load_balancing_scheme = ""
}

resource "google_compute_firewall" "deny_ingress" {
  project       = var.project_id
  name          = "${var.name_prefix}-${var.env}-deny-ingress"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  priority      = 65534
  source_ranges = ["0.0.0.0/0"]
  deny { protocol = "all" }
}

resource "google_compute_firewall" "allow_internal" {
  project       = var.project_id
  name          = "${var.name_prefix}-${var.env}-allow-internal"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  source_ranges = [var.subnet_cidr, var.pods_cidr, var.services_cidr]
  allow { protocol = "tcp" }
  allow { protocol = "udp" }
  allow { protocol = "icmp" }
}

output "network_id" { value = google_compute_network.vpc.id }
output "network_name" { value = google_compute_network.vpc.name }
output "subnet_id" { value = google_compute_subnetwork.primary.id }
output "subnet_self_link" { value = google_compute_subnetwork.primary.self_link }
output "pods_range_name" { value = "pods" }
output "services_range_name" { value = "services" }
