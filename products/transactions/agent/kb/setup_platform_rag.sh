#!/usr/bin/env bash
# Build the PLATFORM docs vector store from the repo's own documentation.
#
# Separate from setup_rag.sh on purpose: that corpus answers customers, this one answers
# analysts and admins asking how FinChat itself works. See setup_platform_rag.sql.
#
# Re-run after any documentation change — the corpus is a build artifact of the repo, so
# it goes stale the moment an ADR lands. It is cheap: embedding ~450 chunks is cents.
#
# Usage (from repo root): ./products/transactions/agent/kb/setup_platform_rag.sh [dev|test|prod]
set -euo pipefail

PROJECT="strongsville-city-schools"
ENV="${1:-dev}"
KB="finchat_kb_${ENV}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/../../../.." && pwd)"

echo "→ Regenerating the corpus from repo docs"
python "${ROOT}/scripts/build_repo_corpus.py"

echo "→ Loading into ${PROJECT}:${KB}.platform_raw"
bq load --project_id="$PROJECT" --replace --source_format=NEWLINE_DELIMITED_JSON \
  "${KB}.platform_raw" "${HERE}/repo_corpus.jsonl" \
  doc_id:STRING,title:STRING,category:STRING,source_path:STRING,content:STRING

echo "→ Embedding into ${KB}.platform_chunks"
sed -e "s/\${PROJECT}/${PROJECT}/g" -e "s/\${ENV}/${ENV}/g" \
    "${HERE}/setup_platform_rag.sql" | bq query --project_id="$PROJECT" --use_legacy_sql=false

echo "✓ Platform docs searchable. Ask via the Analyst view — the router sends"
echo "  'how does X work' / 'why did we choose Y' questions to PLATFORM."
bq query --project_id="$PROJECT" --use_legacy_sql=false --format=pretty \
  "SELECT category, COUNT(*) AS chunks FROM \`${PROJECT}.${KB}.platform_chunks\` GROUP BY 1 ORDER BY 2 DESC"
