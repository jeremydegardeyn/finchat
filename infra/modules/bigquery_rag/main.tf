###############################################################################
# BigQuery RAG module — vector knowledge base for the conversational agent.
# - BigQuery connection (CLOUD_RESOURCE) backing a remote embedding model
# - KB dataset (chunks + embeddings created by kb/setup_rag.sql)
# - least-privilege: connection SA -> aiplatform.user; readers -> dataViewer +
#   connectionUser (needed to run ML.GENERATE_EMBEDDING via the remote model);
#   writers (the CI deployer) -> dataEditor + connectionUser to build the corpus
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

# The corpus is a build artifact of the repo, so whoever deploys has to be able to WRITE
# it. Without this the refresh step fails with "Permission bigquery.tables.create denied
# on dataset finchat_kb_<env>" — and because that step is `continue-on-error` (a deploy
# should not fail over a search corpus), the run stays green and the only trace is an
# annotation. The corpus silently never refreshed. Dataset-scoped rather than a project
# role: the CI account already holds bigquery.jobUser project-wide, and the gap was write
# access to exactly this dataset.
resource "google_bigquery_dataset_iam_member" "kb_writers" {
  for_each   = toset(var.writer_members)
  project    = var.project_id
  dataset_id = google_bigquery_dataset.kb.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value
}

# Readers must be able to USE the connection to invoke the remote embedding model.
resource "google_bigquery_connection_iam_member" "kb_conn_users" {
  # Writers as well as readers: setup_platform_rag.sql embeds through the remote model,
  # so building the corpus needs connections.use exactly as querying it does.
  for_each      = toset(concat(var.reader_members, var.writer_members))
  project       = var.project_id
  location      = var.region
  connection_id = google_bigquery_connection.kb.connection_id
  role          = "roles/bigquery.connectionUser"
  member        = each.value
}
