#!/usr/bin/env bash
# Runs the workflow scenarios with Slack off, then on (unreachable, so every Slack failure must be
# reported). Needs Docker with Compose. CI runs it; locally: n8n/tests/run.sh
set -uo pipefail
cd "$(dirname "$0")"
# The n8n the chart deploys
N8N_IMAGE=$(grep -m1 -o 'ghcr.io/n8n-io/n8n:[^ "]*' ../../chart/values.yaml)
export N8N_IMAGE
echo "n8n image: $N8N_IMAGE"
failed=0
for slack in false true; do
  echo "== Slack $([ "$slack" = true ] && echo on || echo off)"
  export SLACK_ENABLED=$slack
  docker compose down -v --remove-orphans >/dev/null 2>&1
  if ! docker compose up -d --wait n8n; then
    docker compose logs --no-color import n8n | tail -40
    failed=1
    continue
  fi
  # Webhooks register a moment after n8n reports healthy
  docker compose run --rm scenarios || { failed=1; docker compose logs --no-color n8n | tail -40; }
done
docker compose down -v --remove-orphans >/dev/null 2>&1
exit $failed
