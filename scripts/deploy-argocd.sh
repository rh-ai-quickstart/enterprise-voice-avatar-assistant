#!/usr/bin/env bash
# Deploys (or updates) the assistant through Argo CD, run on the bastion host after
# scripts/bootstrap-cluster.sh. Creates the secrets from one file, registers the Argo CD
# application with the cluster's apps domain, waits for the sync, the pods and the models,
# and prints the URLs. Safe to run again. Everything printed also goes to the log file.
#
# Usage: SECRETS_FILE=~/secrets.env scripts/deploy-argocd.sh
#   PROJECT=voice-avatar-assistant   project prepared by bootstrap-cluster.sh
#   SECRETS_FILE=<path>              KEY=value file with the API keys (see secrets.env.example)
#   VALUES_FILE=values-demo-cluster.yaml   values file in chart/
#   VALUES_OBJECT_FILE=<json>        extra values merged on top (the profile overlay written by setup.sh)
#   LLM_NAME=llama-32-3b-instruct    InferenceService already on the cluster, used as the language model
#   LLM_ENDPOINT / LLM_MODEL         override the discovered OpenAI-compatible base URL and model id
#   REPO_URL=<git url>               fork to deploy from (default: the upstream repository)
#   TARGET_REVISION=main             branch, tag or commit
#   DOMAIN=<apps domain>             default: read from the cluster
#   SLACK_ENABLED, GOOGLE_DOCS_ENABLED   true/false; default: on when the keys are in assistant-integrations
#   WAIT=1                           0 = register the application and return
#   RUN_TESTS=0                      1 = run the connectivity test pod at the end
#   LOG_FILE=~/assistant-deploy-<timestamp>.log
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${PROJECT:-voice-avatar-assistant}"
REPO_URL="${REPO_URL:-https://github.com/rh-ai-quickstart/enterprise-voice-avatar-assistant.git}"
TARGET_REVISION="${TARGET_REVISION:-main}"
VALUES_FILE="${VALUES_FILE:-values-demo-cluster.yaml}"
LLM_NAME="${LLM_NAME:-llama-32-3b-instruct}"
APP=voice-avatar-assistant
CLUSTER_ENV="$HOME/assistant-cluster.env"
LOG_FILE="${LOG_FILE:-$HOME/assistant-deploy-$(date +%Y%m%d-%H%M%S).log}"
exec > >(tee -a "$LOG_FILE") 2>&1

STEP=0; FAILED=0
step() { STEP=$((STEP + 1)); printf '\n\033[1m== Step %s: %s\033[0m  (%s)\n' "$STEP" "$1" "$(date +%H:%M:%S)"; }
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
info() { printf '  ..   %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=$((FAILED + 1)); }
debug(){ printf '  debug: %s\n' "$1"; }
run()  { printf '  $ %s\n' "$*"; "$@"; }
wait_for() {
  local timeout=$1 what=$2; shift 2
  local waited=0
  while ! "$@" >/dev/null 2>&1; do
    if [ "$waited" -ge "$timeout" ]; then return 1; fi
    sleep 15; waited=$((waited + 15))
    [ $((waited % 30)) -eq 0 ] && info "waiting for $what (${waited}s)$( [ "$what" = "all pods" ] && printf ': %s' "$(pods_report | cut -c1-200)")"
  done
  return 0
}
echo "Deploy log: $LOG_FILE"

step "Checks"
command -v oc >/dev/null || { fail "oc is not installed"; exit 1; }
command -v jq >/dev/null || { fail "jq is not installed (sudo dnf install -y jq)"; exit 1; }
if user=$(oc whoami 2>/dev/null); then ok "logged in as $user"; else fail "not logged in"; exit 1; fi
oc get namespace "$PROJECT" >/dev/null 2>&1 && ok "project $PROJECT exists" || { fail "project $PROJECT missing: run scripts/bootstrap-cluster.sh first"; exit 1; }
oc get deployment openshift-gitops-server -n openshift-gitops >/dev/null 2>&1 && ok "Argo CD present" || { fail "Argo CD not found in openshift-gitops: run scripts/bootstrap-cluster.sh"; exit 1; }
[ -f "$ROOT/chart/$VALUES_FILE" ] && ok "values file chart/$VALUES_FILE" || { fail "chart/$VALUES_FILE does not exist in this clone (VALUES_FILE)"; exit 1; }
VALUES_OBJECT='{}'
if [ -n "${VALUES_OBJECT_FILE:-}" ]; then VALUES_OBJECT=$(jq -c . "$VALUES_OBJECT_FILE") && ok "values overlay $VALUES_OBJECT_FILE" || { fail "$VALUES_OBJECT_FILE is not valid JSON"; exit 1; }; fi
DOMAIN="${DOMAIN:-$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')}"
[ -n "$DOMAIN" ] && ok "apps domain $DOMAIN" || { fail "could not read the apps domain; set DOMAIN="; exit 1; }
if [ -n "${SECRETS_FILE:-}" ]; then [ -r "$SECRETS_FILE" ] && ok "secrets file $SECRETS_FILE" || { fail "SECRETS_FILE $SECRETS_FILE is not readable"; exit 1; }; else warn "no SECRETS_FILE: integrations stay off unless the secrets already exist"; fi
if oc get secret livekit-turn-tls -n "$PROJECT" >/dev/null 2>&1; then ok "TURN certificate secret livekit-turn-tls present"; else warn "livekit-turn-tls missing: voice through corporate networks needs it (scripts/setup.sh step 4); LiveKit waits for it"; fi

step "Language model ($LLM_NAME)"
if [ -z "${LLM_ENDPOINT:-}" ]; then
  llm_ns=$(oc get isvc -A -o json 2>/dev/null | jq -r --arg n "$LLM_NAME" '.items[] | select(.metadata.name==$n) | .metadata.namespace' | head -1)
  [ -n "$llm_ns" ] || { fail "no InferenceService named $LLM_NAME (README, Setup); or set LLM_ENDPOINT and LLM_MODEL"; exit 1; }
  addr=$(oc get isvc "$LLM_NAME" -n "$llm_ns" -o jsonpath='{.status.address.url}')
  [ -n "$addr" ] || addr="http://${LLM_NAME}-predictor.${llm_ns}.svc.cluster.local:8080"
  LLM_ENDPOINT="${addr%/}/v1"
  ok "found $llm_ns/$LLM_NAME at $LLM_ENDPOINT"
fi
info "probing $LLM_ENDPOINT/models from inside the cluster"
models_json=$(oc run llm-probe-$$ -n "$PROJECT" --rm -i --restart=Never --quiet --image=registry.access.redhat.com/ubi9/ubi-minimal:latest -- curl -sk --max-time 20 -H "Authorization: Bearer ${LLM_API_KEY:-none}" "$LLM_ENDPOINT/models" 2>/dev/null || true)
served=$(printf '%s' "$models_json" | jq -r '.data[0].id // empty' 2>/dev/null)
if [ -n "$served" ]; then ok "endpoint answers; served model id: $served"; LLM_MODEL="${LLM_MODEL:-$served}"; else
  warn "no answer from $LLM_ENDPOINT/models yet (not deployed yet, auth, or TLS?); using model id ${LLM_MODEL:-$LLM_NAME}"; LLM_MODEL="${LLM_MODEL:-$LLM_NAME}"
  debug "oc get isvc $LLM_NAME -A -o yaml | grep -A3 -i 'auth\|url'; the chart's test pod re-checks later"; fi
printf 'DOMAIN=%s\nPROJECT=%s\nVALUES_FILE=%s\nLLM_ENDPOINT=%s\nLLM_MODEL=%s\n' "$DOMAIN" "$PROJECT" "$VALUES_FILE" "$LLM_ENDPOINT" "$LLM_MODEL" > "$CLUSTER_ENV" && ok "cluster facts written to $CLUSTER_ENV (source it for scripts/demo-preflight.sh)"

step "Secrets"
NAMESPACE="$PROJECT" SECRETS_FILE="${SECRETS_FILE:-}" "$ROOT/scripts/create-secrets.sh" | sed 's/^/  /'
has_key() { [ -n "$(oc get secret assistant-integrations -n "$PROJECT" -o jsonpath="{.data.$1}" 2>/dev/null)" ]; }
for key in SLACK_BOT_TOKEN TAVUS_API_KEY GOOGLE_DOCS_FOLDER_ID; do
  if has_key "$key"; then ok "$key set"; else warn "$key empty (feature off)"; fi
done
# integrations.*.enabled follow the keys unless set explicitly
if [ -z "${SLACK_ENABLED:-}" ]; then SLACK_ENABLED=false; has_key SLACK_BOT_TOKEN && SLACK_ENABLED=true; fi
if [ -z "${GOOGLE_DOCS_ENABLED:-}" ]; then GOOGLE_DOCS_ENABLED=false; has_key GOOGLE_SERVICE_ACCOUNT_JSON && has_key GOOGLE_DOCS_FOLDER_ID && GOOGLE_DOCS_ENABLED=true; fi
ok "integrations: Slack $SLACK_ENABLED, Google Docs $GOOGLE_DOCS_ENABLED; approvals in the admin portal$([ "$SLACK_ENABLED" = true ] && echo " and in Slack")"

step "Argo CD application"
run oc apply -f "$ROOT/deploy/argocd/appproject.yaml" >/dev/null
oc patch appprojects.argoproj.io "$APP" -n openshift-gitops --type merge -p "{\"spec\":{\"sourceRepos\":[\"$REPO_URL\"],\"destinations\":[{\"server\":\"https://kubernetes.default.svc\",\"namespace\":\"$PROJECT\"}]}}" >/dev/null && ok "AppProject allows $REPO_URL -> $PROJECT"
run oc apply -f "$ROOT/deploy/argocd/application.yaml" >/dev/null
oc patch applications.argoproj.io "$APP" -n openshift-gitops --type merge -p "{\"spec\":{\"source\":{\"repoURL\":\"$REPO_URL\",\"targetRevision\":\"$TARGET_REVISION\",\"helm\":{\"valueFiles\":[\"values.yaml\",\"$VALUES_FILE\"],\"valuesObject\":$VALUES_OBJECT,\"parameters\":[{\"name\":\"global.domain\",\"value\":\"$DOMAIN\"},{\"name\":\"models.llm.endpoint\",\"value\":\"$LLM_ENDPOINT\"},{\"name\":\"models.llm.servedModelName\",\"value\":\"$LLM_MODEL\"},{\"name\":\"integrations.slack.enabled\",\"value\":\"$SLACK_ENABLED\"},{\"name\":\"integrations.googleDocs.enabled\",\"value\":\"$GOOGLE_DOCS_ENABLED\"}]}},\"destination\":{\"namespace\":\"$PROJECT\"}}}" >/dev/null \
  && ok "Application $APP: $REPO_URL@$TARGET_REVISION, values $VALUES_FILE, global.domain=$DOMAIN, llm $LLM_MODEL at $LLM_ENDPOINT, Slack $SLACK_ENABLED, Google Docs $GOOGLE_DOCS_ENABLED"
oc annotate applications.argoproj.io "$APP" -n openshift-gitops argocd.argoproj.io/refresh=normal --overwrite >/dev/null
# Routes are immutable in their host: any route created earlier with a different host (for
# example by a sync that ran before the domain was set) is removed so Argo CD recreates it
for r in $(oc get routes -n "$PROJECT" -o json 2>/dev/null | jq -r --arg ns "$PROJECT" --arg d "$DOMAIN" '.items[] | select(.spec.host != "\(.metadata.name)-\($ns).\($d)") | .metadata.name'); do
  warn "route $r has host $(oc get route "$r" -n "$PROJECT" -o jsonpath='{.spec.host}'), not the one for this domain; deleting it so Argo CD recreates it"
  oc delete route "$r" -n "$PROJECT" --wait=false >/dev/null
done
[ "${WAIT:-1}" = "1" ] || { echo "Application registered (WAIT=0)."; exit 0; }

step "Sync"
app_status() { oc get applications.argoproj.io "$APP" -n openshift-gitops -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null; }
op_phase() { oc get applications.argoproj.io "$APP" -n openshift-gitops -o jsonpath='{.status.operationState.phase}' 2>/dev/null; }
op_message() { oc get applications.argoproj.io "$APP" -n openshift-gitops -o jsonpath='{.status.operationState.message}' 2>/dev/null | cut -c1-300; }
# Synced/Healthy is not enough: the n8n-setup hook Job runs inside the sync operation, so the
# operation itself has to have succeeded before the owner account and API key exist.
synced() { [ "$(app_status)" = "Synced/Healthy" ] && case "$(op_phase)" in ""|Succeeded) true;; *) false;; esac; }
start_sync() { oc patch applications.argoproj.io "$APP" -n openshift-gitops --type merge -p "{\"operation\":{\"initiatedBy\":{\"username\":\"deploy-argocd.sh\"},\"sync\":{\"revision\":\"$TARGET_REVISION\",\"prune\":true}}}" >/dev/null 2>&1; }
failing_hook_pod() { oc get pods -n "$PROJECT" -l job-name=n8n-setup --no-headers 2>/dev/null | grep -E 'CrashLoopBackOff|Error' | awk '{print $1}' | head -1; }
case "$(op_phase)" in
  Running)
    hook_pod=$(failing_hook_pod)
    if [ -n "$hook_pod" ]; then
      warn "the running sync waits for hook Job n8n-setup, whose pod $hook_pod keeps failing; ending that sync so the latest revision is synced (which recreates the Job)"
      oc patch applications.argoproj.io "$APP" -n openshift-gitops --type merge -p '{"status":{"operationState":{"phase":"Terminating"}}}' >/dev/null
      oc delete job n8n-setup -n "$PROJECT" --wait=false >/dev/null 2>&1 || true
      sleep 20; start_sync
    fi ;;
  Failed|Error)
    warn "the last sync ended with $(op_phase): $(op_message)"
    info "starting a new sync"
    oc delete job n8n-setup -n "$PROJECT" --wait=false >/dev/null 2>&1 || true
    start_sync ;;
esac
waited=0; retried=0
until synced; do
  if [ "$waited" -ge 1800 ]; then break; fi
  sleep 20; waited=$((waited + 20))
  case "$(op_phase)" in
    Failed|Error)
      if [ "$retried" = 0 ]; then
        warn "sync ended with $(op_phase): $(op_message)"; info "retrying the sync once"
        oc delete job n8n-setup -n "$PROJECT" --wait=false >/dev/null 2>&1 || true
        start_sync; retried=1
      elif [ "$(app_status)" = "Synced/Healthy" ]; then
        break
      fi ;;
  esac
  if [ $((waited % 40)) -eq 0 ]; then
    info "application $(app_status) after ${waited}s; sync operation: $(oc get applications.argoproj.io "$APP" -n openshift-gitops -o jsonpath='{.status.operationState.phase}: {.status.operationState.message}' 2>/dev/null | cut -c1-160)"
    for p in $(oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' | awk '{print $1":"$3}'); do
      name=${p%%:*}; state=${p#*:}
      line=""; case "$state" in *CrashLoop*|*Error*) line=$(oc logs "$name" -n "$PROJECT" --previous --tail=300 2>/dev/null | grep -m1 -E 'Error|error|memory|OOM|Exception' | cut -c1-200);; esac
      info "pod $name $state${line:+; last error: $line}"
    done
    oc get applications.argoproj.io "$APP" -n openshift-gitops -o json | jq -r '.status.resources[]? | select(.health.status != null and .health.status != "Healthy") | "     \(.kind)/\(.name): \(.health.status) \(.health.message // "")"' | head -8
    [ "$(oc get applications.argoproj.io "$APP" -n openshift-gitops -o jsonpath='{.status.operationState.phase}')" = "Error" ] && oc get applications.argoproj.io "$APP" -n openshift-gitops -o jsonpath='{.status.operationState.message}{"\n"}' | cut -c1-300 | sed 's/^/     /'
  fi
done
if synced; then ok "application Synced/Healthy, sync operation $(op_phase)"; else
  fail "application is $(app_status), sync operation $(op_phase): $(op_message)"
  [ -z "$(failing_hook_pod)" ] || { info "hook Job n8n-setup log:"; oc logs -n "$PROJECT" -l job-name=n8n-setup --tail=5 2>/dev/null | sed 's/^/     /'; }
  debug "oc describe applications.argoproj.io $APP -n openshift-gitops | tail -40; Argo CD UI: https://$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}')"
fi

step "Models"
has_isvc() { local i; for i in 1 2 3 4; do oc get isvc -n "$PROJECT" -o name 2>/dev/null | grep -q . && return 0; sleep 5; done; return 1; }
if has_isvc; then
  models_ready() { [ "$(oc get isvc -n "$PROJECT" -o json | jq -r '[.items[] | (.status.conditions[]? | select(.type=="Ready") | .status)] | all(.=="True") and (length>0)')" = "true" ]; }
  waited=0
  until models_ready; do
    if [ "$waited" -ge 2400 ]; then break; fi
    sleep 30; waited=$((waited + 30))
    if [ $((waited % 60)) -eq 0 ]; then
      info "models after ${waited}s (weights download on first start takes 5 to 15 min):"
      oc get isvc -n "$PROJECT" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,REASON:.status.conditions[?(@.type=="Ready")].reason' | sed 's/^/     /'
      for isvc in $(oc get isvc -n "$PROJECT" -o jsonpath='{.items[*].metadata.name}'); do
        pod=$(oc get pods -n "$PROJECT" -l "serving.kserve.io/inferenceservice=$isvc" --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null)
        [ -n "$pod" ] || { printf '     %s: no pod yet\n' "$isvc"; continue; }
        printf '     %s: %s; log: %s\n' "$isvc" "$(oc get pod "$pod" -n "$PROJECT" -o jsonpath='{.status.phase} ready={.status.containerStatuses[*].ready}')" "$(oc logs "$pod" -n "$PROJECT" -c kserve-container --tail=1 2>/dev/null | tr -d '\r' | cut -c1-160)"
      done
    fi
  done
  if models_ready; then ok "all InferenceServices Ready"; oc get isvc -n "$PROJECT" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status' | sed 's/^/     /'; else
    fail "models not Ready after 40 min"; debug "oc get pods -n $PROJECT -l component=predictor; oc logs -n $PROJECT -l serving.kserve.io/inferenceservice=whisper-large-v3-turbo --tail=50; GPU memory: oc exec -n nvidia-gpu-operator ds/nvidia-driver-daemonset -- nvidia-smi"; fi
else
  warn "no InferenceService in $PROJECT (remote model endpoints?)"
fi

step "Pods"
pods_ready() { ! oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' | grep -q .; }
pods_report() { oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' | awk '{print $1":"$3}' | tr '\n' ' '; }
if wait_for 900 "all pods" pods_ready; then ok "all pods Running or Completed"; else
  fail "some pods are not ready:"; oc get pods -n "$PROJECT" --no-headers | grep -v -E 'Running|Completed' | sed 's/^/     /'; debug "oc describe pod <name> -n $PROJECT; oc logs <name> -n $PROJECT --previous"; fi

step "URLs"
for r in frontend n8n livekit qdrant; do
  host=$(oc get route "$r" -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null) && [ -n "$host" ] && printf '  %-9s https://%s\n' "$r" "$host"
done
host=$(oc get route frontend -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null) && [ -n "$host" ] && printf '  %-9s https://%s/admin  (password: oc extract secret/assistant-admin -n %s --keys=ADMIN_PASSWORD --to=-)\n' "admin" "$host" "$PROJECT"
printf '  %-9s https://%s\n' "argocd" "$(oc get route openshift-gitops-server -n openshift-gitops -o jsonpath='{.spec.host}')"
if [ "${RUN_TESTS:-0}" = "1" ]; then
  step "Connectivity test pod"
  overlay=(); [ -n "${VALUES_OBJECT_FILE:-}" ] && overlay=(-f "$VALUES_OBJECT_FILE")
  NS="$PROJECT" "$ROOT/scripts/test-services.sh" -f "$ROOT/chart/$VALUES_FILE" "${overlay[@]}" --set global.domain="$DOMAIN" --set models.llm.endpoint="$LLM_ENDPOINT" --set models.llm.servedModelName="$LLM_MODEL" || FAILED=$((FAILED + 1))
fi
echo
if [ "$FAILED" -gt 0 ]; then echo "Deployment finished with $FAILED problem(s); see the FAIL lines above and the log $LOG_FILE"; exit 1; fi
echo "Deployment complete. Next: n8n first run and the sample documents (scripts/setup.sh)."
