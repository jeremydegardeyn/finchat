###############################################################################
# BigQuery RAG module — vector knowledge base for the conversational agent.
# - BigQuery connection (CLOUD_RESOURCE) backing a remote embedding model
# - KB dataset (chunks + embeddings created by kb/setup_rag.sql)
# - least-privilege: connection SA -> aiplatform.user; readers -> dataViewer +
#   connectionUser (needed to run ML.GENERATE_EMBEDDING via the remote model)
###############################################################################

resource "google_bigquery_connection" "kb" {
  project       = var.project_id
  location      = var.region
  connection_id = "${var.name_prefix}-${var.env}-kb"
  friendly_name = "FinChat KB embeddings (${var.env})"
  cloud_resource {}
}

# The connection's delegated SA calls Vertex for embeddings.
resource "google_project_iam_member" "kb_conn_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_bigquery_connection.kb.cloud_resource[0].service_account_id}"
}

resource "google_bigquery_dataset" "kb" {
  project     = var.project_id
  dataset_id  = "${var.name_prefix}_kb_${var.env}"
  location    = var.region
  description = "RAG knowledge base: document chunks + vector embeddings."
  labels      = merge(var.labels, { layer = "kb" })
}

resource "google_bigquery_dataset_iam_member" "kb_readers" {
  for_each   = toset(var.reader_members)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.kb.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = each.value
}

# Readers must be able to USE the connection to invoke the remote embedding model.
resource "google_bigquery_connection_iam_member" "kb_conn_users" {
  for_each      = toset(var.reader_members)
  project       = var.project_id
  location      = var.region
  connection_id = google_bigquery_connection.kb.connection_id
  role          = "roles/bigquery.connectionUser"
  member        = each.value
}
