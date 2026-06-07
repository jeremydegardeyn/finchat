#!/usr/bin/env bash
# Dispatch the build-deploy workflow for an environment and watch it to completion.
#
# Usage: ./scripts/deploy.sh <dev|test|prod> [git-ref]
#   ./scripts/deploy.sh test
#   ./scripts/deploy.sh prod main
#
# Requires: gh CLI authenticated (gh auth status). For test/prod with required
# reviewers, the run will pause as "waiting" until approved in the UI.
set -euo pipefail

REPO="jeremydegardeyn/finchat"
ENV="${1:?usage: deploy.sh <dev|test|prod> [git-ref]}"
REF="${2:-main}"
case "$ENV" in
  dev | test | prod) ;;
  *) echo "error: environment must be dev|test|prod" >&2; exit 1 ;;
esac

echo "→ Dispatching build-deploy.yml  (environment=$ENV, ref=$REF)"
gh workflow run build-deploy.yml --repo "$REPO" --ref "$REF" -f environment="$ENV"

# Give GitHub a moment to register the run, then find the newest one for this ref.
sleep 6
RID="$(gh run list --repo "$REPO" -w build-deploy.yml -b "$REF" -L 1 --json databaseId -q '.[0].databaseId')"
if [ -z "$RID" ]; then
  echo "could not find the dispatched run; check: gh run list -w build-deploy.yml" >&2
  exit 1
fi

echo "→ Watching run $RID"
echo "  https://github.com/$REPO/actions/runs/$RID"
gh run watch "$RID" --repo "$REPO" --exit-status
