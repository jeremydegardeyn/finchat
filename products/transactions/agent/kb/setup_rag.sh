#!/usr/bin/env bash
# Load the KB corpus and build the BigQuery vector store (run once per env, after
# `terraform apply` has created the KB dataset + connection).
#
# Usage (from repo root): ./products/transactions/agent/kb/setup_rag.sh [dev|test|prod]
set -euo pipefail

PROJECT="strongsville-city-schools"
REGION="us-central1"
ENV="${1:-dev}"
KB="finchat_kb_${ENV}"
HERE="$(cd "$(dirname "$0")" && pwd)"

CONN="$(terraform -chdir="infra/envs/${ENV}" output -raw kb_connection_id)" # project.region.id
CONN_SHORT="${CONN##*.}"                                                     # just the connection id

echo "→ Loading corpus into ${PROJECT}:${KB}.kb_raw"
bq load --project_id="$PROJECT" --replace --source_format=NEWLINE_DELIMITED_JSON \
  "${KB}.kb_raw" "${HERE}/corpus.jsonl" \
  doc_id:STRING,title:STRING,category:STRING,content:STRING

echo "→ Creating embedding model + kb_chunks (embeddings)"
sed -e "s/\${PROJECT}/${PROJECT}/g" -e "s/\${ENV}/${ENV}/g" \
    -e "s/\${REGION}/${REGION}/g" -e "s/\${CONN}/${CONN_SHORT}/g" \
    "${HERE}/setup_rag.sql" | bq query --project_id="$PROJECT" --use_legacy_sql=false

echo "✓ KB ready. Set KB_DATASET=${KB} on the agent (CI does this)."
echo "  Test: bq query --use_legacy_sql=false \"SELECT base.title FROM VECTOR_SEARCH(TABLE \\\`${PROJECT}.${KB}.kb_chunks\\\`,'embedding',(SELECT ml_generate_embedding_result AS embedding FROM ML.GENERATE_EMBEDDING(MODEL \\\`${PROJECT}.${KB}.embedding_model\\\`,(SELECT 'overdraft fees' AS content),STRUCT(TRUE AS flatten_json_output))),top_k=>3,distance_type=>'COSINE')\""
