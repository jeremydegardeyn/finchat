# Vertex AI Vector Search (Matching Engine) — managed ANN for the RAG corpus when
# it outgrows BigQuery VECTOR_SEARCH (sub-50 ms at very high QPS). Index + endpoint
# + deployed index. Reference overlay — not applied. Embeddings staged to GCS and
# stream-updated; the search contract (top-k by cosine/dot) is unchanged.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "env" { type = string }
variable "embeddings_gcs_uri" {
  type    = string
  default = "gs://finchat-enterprise-kb/embeddings"
}

resource "google_vertex_ai_index" "kb" {
  project      = var.project_id
  region       = var.region
  display_name = "${var.name_prefix}-${var.env}-kb"
  description  = "FinChat knowledge-base ANN index"

  metadata {
    contents_delta_uri = var.embeddings_gcs_uri
    config {
      dimensions                  = 768 # text-embedding-005
      approximate_neighbors_count = 150
      distance_measure_type       = "DOT_PRODUCT_DISTANCE"
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count    = 500
          leaf_nodes_to_search_percent = 7
        }
      }
    }
  }
  index_update_method = "STREAM_UPDATE"
}

resource "google_vertex_ai_index_endpoint" "ep" {
  project                 = var.project_id
  region                  = var.region
  display_name            = "${var.name_prefix}-${var.env}-kb-endpoint"
  public_endpoint_enabled = true
}

resource "google_vertex_ai_index_endpoint_deployed_index" "deployed" {
  index_endpoint    = google_vertex_ai_index_endpoint.ep.id
  index             = google_vertex_ai_index.kb.id
  deployed_index_id = "${var.name_prefix}_${var.env}_kb"
  display_name      = "${var.name_prefix}-${var.env}-kb"
  automatic_resources {
    min_replica_count = 1
    max_replica_count = 3
  }
}

output "index_endpoint_id" { value = google_vertex_ai_index_endpoint.ep.id }
