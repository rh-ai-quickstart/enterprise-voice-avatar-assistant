#!/usr/bin/env bash
# One-command deployment for a regular OpenShift user: detects the project and the
# cluster apps domain, creates the secrets, installs or upgrades the chart, waits for
# the workloads and models, and prints the URLs. The manual steps in the README do the
# same thing one by one.
#
# Usage:
#   scripts/deploy.sh [helm args...]
#     PROJECT=<namespace>    target project (default: the current one; created if missing)
#     DOMAIN=<apps domain>   cluster apps domain (default: read from the cluster ingress config)
#     RELEASE=<name>         Helm release name (default: assistant)
#     MODELS=maas            prompt for remote LLM, Whisper and embeddings endpoints instead
#                            of deploying the models with the chart (or preset LLM_ENDPOINT,
#                            LLM_MODEL, LLM_API_KEY, STT_*, EMBEDDINGS_* in the environment)
#     WAIT=0                 do not wait for pods and models
#     RUN_TESTS=1            run `helm test` at the end
#   Extra arguments go to helm, for example: scripts/deploy.sh -f my-values.yaml
#
# Secrets for the optional integrations (Slack, Tavus, Google) are read by
# scripts/create-secrets.sh from the environment; see its header.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE="${RELEASE:-assistant}"
WAIT="${WAIT:-1}"
say() { printf '\n==> %s\n' "$*"; }

command -v oc >/dev/null || { echo "oc is required"; exit 1; }
command -v helm >/dev/null || { echo "helm is required"; exit 1; }
oc whoami >/dev/null 2>&1 || { echo "not logged in: run oc login first"; exit 1; }

PROJECT="${PROJECT:-$(oc project -q 2>/dev/null || true)}"
[ -n "$PROJECT" ] || { echo "no current project: set PROJECT=<name>"; exit 1; }
if ! oc get project "$PROJECT" >/dev/null 2>&1; then
  say "Creating project $PROJECT"
  oc new-project "$PROJECT" >/dev/null
fi
DOMAIN="${DOMAIN:-$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}' 2>/dev/null || true)}"
[ -n "$DOMAIN" ] || { echo "could not read the cluster apps domain: set DOMAIN=apps.example.com"; exit 1; }
say "Project $PROJECT, apps domain $DOMAIN, release $RELEASE, user $(oc whoami)"

MODEL_ARGS=()
if [ "${MODELS:-}" = "maas" ]; then
  say "Remote model endpoints (OpenAI-compatible base URLs ending in /v1)"
  ask() { local var="$1" prompt="$2" secret="${3:-}"; if [ -z "${!var:-}" ]; then if [ -n "$secret" ]; then read -rs -p "$prompt: " "$var"; echo; else read -r -p "$prompt: " "$var"; fi; fi; export "$var"; }
  ask LLM_ENDPOINT "LLM endpoint"; ask LLM_MODEL "LLM model name"; ask LLM_API_KEY "LLM API key (empty if none)" secret
  ask STT_ENDPOINT "Whisper (STT) endpoint"; ask STT_MODEL "STT model name"; ask STT_API_KEY "STT API key (empty if none)" secret
  ask EMBEDDINGS_ENDPOINT "Embeddings endpoint"; ask EMBEDDINGS_MODEL "Embeddings model name"; ask EMBEDDINGS_API_KEY "Embeddings API key (empty if none)" secret
  MODEL_ARGS=(--set models.llm.deploy=false --set "models.llm.endpoint=$LLM_ENDPOINT" --set "models.llm.servedModelName=$LLM_MODEL"
              --set models.stt.deploy=false --set "models.stt.endpoint=$STT_ENDPOINT" --set "models.stt.servedModelName=$STT_MODEL"
              --set models.embeddings.deploy=false --set "models.embeddings.endpoint=$EMBEDDINGS_ENDPOINT" --set "models.embeddings.servedModelName=$EMBEDDINGS_MODEL")
  export LLM_API_KEY="${LLM_API_KEY:-none}" STT_API_KEY="${STT_API_KEY:-none}" EMBEDDINGS_API_KEY="${EMBEDDINGS_API_KEY:-none}"
fi

say "Secrets (existing ones are kept)"
NAMESPACE="$PROJECT" "$ROOT/scripts/create-secrets.sh"

say "Installing the chart"
helm upgrade --install "$RELEASE" "$ROOT/chart" --namespace "$PROJECT" --set "global.domain=$DOMAIN" "${MODEL_ARGS[@]}" "$@"

if [ "$WAIT" = "1" ]; then
  say "Waiting for workloads"
  for d in $(oc get deploy -n "$PROJECT" -l app.kubernetes.io/instance="$RELEASE" -o name 2>/dev/null); do
    oc rollout status "$d" -n "$PROJECT" --timeout=600s || echo "  $d is not ready yet; continuing"
  done
  if oc get isvc -n "$PROJECT" >/dev/null 2>&1 && [ -n "$(oc get isvc -n "$PROJECT" -o name)" ]; then
    say "Waiting for models (first start downloads weights; up to 30 minutes)"
    for i in $(seq 1 180); do
      total=$(oc get isvc -n "$PROJECT" -o name | wc -l | tr -d ' ')
      ready=$(oc get isvc -n "$PROJECT" -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' | grep -c True || true)
      [ "$ready" = "$total" ] && break
      [ $((i % 6)) -eq 0 ] && echo "  $ready/$total models ready"
      sleep 10
    done
    oc get isvc -n "$PROJECT"
  fi
fi

say "URLs"
for r in frontend n8n qdrant; do
  h=$(oc get route "$r" -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null || true)
  [ -n "$h" ] && printf '  %-14s https://%s\n' "$r" "$h"
done
h=$(oc get route object-store -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null || true)
[ -n "$h" ] && printf '  %-14s https://%s/ui/\n' "object store" "$h"
echo "  Credentials: oc extract secret/assistant-object-store -n $PROJECT --to=-   (object store web UI); n8n: oc extract secret/assistant-n8n -n $PROJECT --keys=N8N_OWNER_PASSWORD --to=-"

if [ "${RUN_TESTS:-0}" = "1" ]; then
  say "Running helm test"
  helm test "$RELEASE" -n "$PROJECT" --logs
fi
