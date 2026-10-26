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
#     RUN_TESTS=1            run the connectivity test pod at the end (helm test's pod, via scripts/test-services.sh)
#     HELM_TIMEOUT=20m       how long helm waits for the n8n-setup hook Job
#     AVATAR_PROVIDER=<name> openspline (the self-hosted worker on a GPU), tavus, bey, simli, hedra or none;
#                            default: the values file, or none without TAVUS_API_KEY. Several,
#                            comma-separated (openspline,tavus), are all offered in the UI, the
#                            first preselected; one without its key is left out there
#     LOG_FILE=<path>        also write everything printed to this file
#     (livekit.tcpTurn in the values: the host name of its load balancer is read from the cluster
#     and set, in a second upgrade the first time, when the cloud has just created it)
#     AVOID_NODES="<node> ..." nodes to keep every pod off (global.avoidNodes); nodes found unable to
#                            start containers while waiting are added, and AVOID_NODES_FILE keeps the list
#   Extra arguments go to helm, for example: scripts/deploy.sh -f my-values.yaml
#
# Secrets for the optional integrations (Slack, Tavus, Google) are read by
# scripts/create-secrets.sh from the environment; see its header. Slack and Google Docs are
# turned on (integrations.*.enabled) when their keys are in the integrations secret; a --set in
# the helm arguments wins. Without TAVUS_API_KEY the avatar is off (voice is audio-only) unless
# AVATAR_PROVIDER names another provider; openspline needs no key.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE="${RELEASE:-assistant}"
WAIT="${WAIT:-1}"
say() { printf '\n==> %s\n' "$*"; }
[ -n "${LOG_FILE:-}" ] && exec > >(tee -a "$LOG_FILE") 2>&1

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

has_key() { [ -n "$(oc get secret assistant-integrations -n "$PROJECT" -o jsonpath="{.data.$1}" 2>/dev/null)" ]; }
SLACK_ON=false; has_key SLACK_BOT_TOKEN && SLACK_ON=true
DOCS_ON=false; has_key GOOGLE_SERVICE_ACCOUNT_JSON && has_key GOOGLE_DOCS_FOLDER_ID && DOCS_ON=true
AVATAR_ARGS=()
case "${AVATAR_PROVIDER:-}" in ""|tavus) has_key TAVUS_API_KEY || AVATAR_PROVIDER=none;; esac
if [[ "${AVATAR_PROVIDER:-}" == *,* ]]; then
  AVATAR_ARGS=(--set "voiceAgent.avatarProvider=${AVATAR_PROVIDER%%,*}" --set "voiceAgent.avatarProviders={$AVATAR_PROVIDER}")
elif [ -n "${AVATAR_PROVIDER:-}" ]; then
  AVATAR_ARGS=(--set "voiceAgent.avatarProvider=$AVATAR_PROVIDER")
fi
say "Integrations from the keys in assistant-integrations: Slack $SLACK_ON, Google Docs $DOCS_ON, avatar ${AVATAR_PROVIDER:-from the values}"

# A node that cannot start containers (the pod sandbox keeps failing, as when the node's container
# storage breaks) holds every pod scheduled on it, and the scheduler favours it because it looks
# empty. While waiting, such nodes are found and avoided: the Deployments and InferenceServices of
# the stuck pods get a node affinity without them, the stuck pods are deleted and start elsewhere,
# and AVOID_NODES_FILE keeps the list so the next install renders it (global.avoidNodes).
AVOID_NODES="${AVOID_NODES:-}"
[ -z "$AVOID_NODES" ] && [ -s "${AVOID_NODES_FILE:-/nonexistent}" ] && AVOID_NODES=$(tr '\n' ' ' < "$AVOID_NODES_FILE" | sed 's/ *$//')
avoid_bad_nodes() {
  local p st node aff changed=0
  for p in $(oc get events -n "$PROJECT" --field-selector reason=FailedCreatePodSandBox -o json 2>/dev/null \
             | jq -r '[.items[] | {p: .involvedObject.name, n: (.count // 1)}] | group_by(.p)[] | select((map(.n) | add) >= 4) | .[0].p' 2>/dev/null); do
    st=$(oc get pod "$p" -n "$PROJECT" -o jsonpath='{.status.phase} {.spec.nodeName}' 2>/dev/null) || continue
    case "$st" in Running*|Succeeded*) continue;; esac
    node=${st#* }; [ -n "$node" ] || continue
    case " $AVOID_NODES " in *" $node "*) continue;; esac
    AVOID_NODES="${AVOID_NODES:+$AVOID_NODES }$node"; changed=1
    echo "  node $node cannot start containers ($(oc get events -n "$PROJECT" --field-selector "involvedObject.name=$p,reason=FailedCreatePodSandBox" -o jsonpath='{.items[-1:].message}' 2>/dev/null | sed 's/.*: //' | cut -c1-80)); moving pods off it. Tell the cluster admins: that node needs attention"
  done
  [ -n "$AVOID_NODES" ] || return 0
  [ "$changed" = 1 ] && [ -n "${AVOID_NODES_FILE:-}" ] && printf '%s\n' $AVOID_NODES > "$AVOID_NODES_FILE"
  aff=$(jq -nc --arg l "$AVOID_NODES" '{nodeAffinity: {requiredDuringSchedulingIgnoredDuringExecution: {nodeSelectorTerms: [{matchFields: [{key: "metadata.name", operator: "NotIn", values: ($l | split(" "))}]}]}}}')
  oc get pods -n "$PROJECT" -o json 2>/dev/null | jq -r --arg l "$AVOID_NODES" '($l | split(" ")) as $bad | .items[]
      | select(.status.phase != "Running" and .status.phase != "Succeeded") | select(.spec.nodeName as $n | $bad | index($n))
      | "\(.metadata.name) \(.metadata.labels["serving.kserve.io/inferenceservice"] // "-") \(.metadata.ownerReferences[0].kind // "-") \(.metadata.ownerReferences[0].name // "-")"' \
  | while read -r p isvc kind owner; do
      if [ "$isvc" != - ]; then
        oc patch isvc "$isvc" -n "$PROJECT" --type merge -p "{\"spec\":{\"predictor\":{\"affinity\":$aff}}}" >/dev/null && echo "  InferenceService $isvc now avoids $AVOID_NODES"
      elif [ "$kind" = ReplicaSet ]; then
        dep=$(oc get rs "$owner" -n "$PROJECT" -o jsonpath='{.metadata.ownerReferences[0].name}' 2>/dev/null) || dep=""
        [ -n "$dep" ] && oc patch deployment "$dep" -n "$PROJECT" --type merge -p "{\"spec\":{\"template\":{\"spec\":{\"affinity\":$aff}}}}" >/dev/null && echo "  Deployment $dep now avoids $AVOID_NODES"
      fi
      oc delete pod "$p" -n "$PROJECT" --wait=false >/dev/null 2>&1 && echo "  pod $p deleted; it starts again on another node"
    done || true   # set -e: a failed lookup must not end the deployment
  return 0
}
AVOID_ARGS=(); [ -n "$AVOID_NODES" ] && AVOID_ARGS=(--set "global.avoidNodes={$(echo $AVOID_NODES | tr ' ' ',')}") && echo "  pods avoid the nodes $AVOID_NODES (global.avoidNodes)"

# The TURN server on a TCP load balancer (livekit.tcpTurn): LiveKit announces it by the balancer's
# host name, which exists only once the cloud has created it; after the first install it is known
turn_lb_host() { oc get svc livekit-tcp-turn -n "$PROJECT" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true; }
TURN_HOST=$(turn_lb_host)
TURN_ARGS=(); [ -n "$TURN_HOST" ] && TURN_ARGS=(--set "livekit.tcpTurn.publicHost=$TURN_HOST")

say "Installing the chart"
# The n8n-setup hook Job runs inside the install and needs n8n up first (volume, image pull),
# which can take longer than Helm's default five minutes. Helm runs in the background so stuck
# pods on a broken node are dealt with while it waits.
helm upgrade --install "$RELEASE" "$ROOT/chart" --namespace "$PROJECT" --timeout "${HELM_TIMEOUT:-20m}" --set "global.domain=$DOMAIN" \
  --set "integrations.slack.enabled=$SLACK_ON" --set "integrations.googleDocs.enabled=$DOCS_ON" ${AVATAR_ARGS[@]+"${AVATAR_ARGS[@]}"} ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} ${AVOID_ARGS[@]+"${AVOID_ARGS[@]}"} ${TURN_ARGS[@]+"${TURN_ARGS[@]}"} "$@" &
helm_pid=$!
i=0; while kill -0 "$helm_pid" 2>/dev/null; do sleep 5; i=$((i + 1)); [ $((i % 4)) -eq 0 ] && avoid_bad_nodes; done
if ! wait "$helm_pid"; then
  echo "  helm upgrade failed; pods not running:"; oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' | sed 's/^/     /' || true
  oc logs -n "$PROJECT" -l job-name=n8n-setup --tail=5 2>/dev/null | sed 's/^/     n8n-setup: /' || true
  exit 1
fi

FAILED=0
if [ -z "$TURN_HOST" ] && [ "$(helm get values "$RELEASE" -n "$PROJECT" -a -o json 2>/dev/null | jq -r '.livekit.tcpTurn.enabled // false')" = true ]; then
  say "TURN server on a TCP load balancer: waiting for the cloud to give it a host name"
  for i in $(seq 1 30); do TURN_HOST=$(turn_lb_host); [ -n "$TURN_HOST" ] && break; sleep 10; done
  if [ -n "$TURN_HOST" ]; then
    echo "  $TURN_HOST (its DNS name can take a few minutes to resolve everywhere)"
    if helm upgrade "$RELEASE" "$ROOT/chart" --namespace "$PROJECT" --reuse-values --set "livekit.tcpTurn.publicHost=$TURN_HOST" >/dev/null; then
      echo "  LiveKit announces it from now on (restarted with it)"
    else echo "  FAIL could not set livekit.tcpTurn.publicHost=$TURN_HOST"; FAILED=$((FAILED + 1)); fi
  else
    echo "  FAIL no host name after 5 minutes: oc describe svc livekit-tcp-turn -n $PROJECT (does the cloud refuse load balancers?)"; FAILED=$((FAILED + 1))
  fi
fi
not_ready() { oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' || true; }
# Kueue (where it manages the project): workloads not admitted yet, with the reason (no quota, booking ended, ...)
queued() { oc get workloads.kueue.x-k8s.io -n "$PROJECT" -o json 2>/dev/null | jq -r '.items[]? | select([.status.conditions[]? | select(.type=="Admitted" and .status=="True")] | length == 0) | "  queued by Kueue: \(.metadata.name): \([.status.conditions[]?.message] | last // "waiting for admission")"' 2>/dev/null || true; }
if [ "$WAIT" = "1" ]; then
  say "Waiting for workloads"
  pending=""
  for i in $(seq 1 60); do
    pending=$(oc get deploy -n "$PROJECT" -l app.kubernetes.io/instance="$RELEASE" -o json 2>/dev/null | jq -r '.items[] | select((.status.availableReplicas // 0) < (.spec.replicas // 1)) | .metadata.name' | tr '\n' ' ')
    [ -z "$pending" ] && break
    [ $((i % 2)) -eq 0 ] && avoid_bad_nodes
    [ $((i % 6)) -eq 0 ] && echo "  waiting for ${pending}after $((i * 10))s"
    sleep 10
  done
  [ -z "$pending" ] && echo "  every deployment available" || echo "  not available yet: ${pending}continuing"
  if oc get isvc -n "$PROJECT" >/dev/null 2>&1 && [ -n "$(oc get isvc -n "$PROJECT" -o name)" ]; then
    say "Waiting for models (first start downloads weights; up to 40 minutes)"
    for i in $(seq 1 240); do
      total=$(oc get isvc -n "$PROJECT" -o name | wc -l | tr -d ' ')
      ready=$(oc get isvc -n "$PROJECT" -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' | grep -c True || true)
      [ "$ready" = "$total" ] && break
      if [ $((i % 6)) -eq 0 ]; then
        echo "  $ready/$total models ready after $((i * 10))s"
        for p in $(oc get pods -n "$PROJECT" -l serving.kserve.io/inferenceservice -o name 2>/dev/null); do
          echo "  $p: $(oc get "$p" -n "$PROJECT" -o jsonpath='{.status.phase}' 2>/dev/null); log: $(oc logs "$p" -n "$PROJECT" -c kserve-container --tail=1 2>/dev/null | tr -d '\r' | cut -c1-150)"
        done
        queued; avoid_bad_nodes
      fi
      sleep 10
    done
    oc get isvc -n "$PROJECT"
    [ "$ready" = "$total" ] || { echo "  FAIL models not Ready after 40 minutes: oc get pods -n $PROJECT -l serving.kserve.io/inferenceservice; oc describe isvc -n $PROJECT"; queued; FAILED=$((FAILED + 1)); }
  fi
  if oc get deploy openspline -n "$PROJECT" >/dev/null 2>&1; then
    say "Waiting for the OpenSpline avatar worker (its first start installs OpenSpline and downloads its weights; up to 40 minutes)"
    os_ready=""
    for i in $(seq 1 240); do
      [ "$(oc get deploy openspline -n "$PROJECT" -o jsonpath='{.status.availableReplicas}' 2>/dev/null)" = 1 ] && { os_ready=1; break; }
      if [ $((i % 6)) -eq 0 ]; then
        # the container at work: install, models (init containers) or the server loading the model
        pod=$(oc get pods -n "$PROJECT" -l app.kubernetes.io/name=openspline -o json 2>/dev/null | jq -r '.items[0] // empty | "\(.metadata.name) \(([.status.initContainerStatuses[]? | select(.state.running) | .name] + [.status.containerStatuses[]? | select(.state.running) | .name])[0] // .status.phase)"')
        os_pod=""; os_ctr=""; read -r os_pod os_ctr <<< "$pod" || true
        echo "  after $((i * 10))s: ${os_pod:-no pod yet}${os_ctr:+ in $os_ctr}${os_ctr:+: $(oc logs "$os_pod" -n "$PROJECT" -c "$os_ctr" --tail=1 2>/dev/null | tr -d '\r' | cut -c1-150)}"
        queued; avoid_bad_nodes
      fi
      sleep 10
    done
    if [ -n "$os_ready" ]; then echo "  openspline ready: voice sessions get the self-hosted avatar"
    else echo "  FAIL openspline not ready after 40 minutes: oc logs deploy/openspline --all-containers -n $PROJECT"; queued; FAILED=$((FAILED + 1)); fi
  fi
  say "Waiting for every pod"
  for i in $(seq 1 60); do [ -z "$(not_ready)" ] && break; [ $((i % 2)) -eq 0 ] && avoid_bad_nodes; [ $((i % 6)) -eq 0 ] && { echo "  not ready after $((i * 10))s:"; not_ready | awk '{print "     "$1" "$3}'; queued; }; sleep 10; done
  if [ -n "$(not_ready)" ]; then echo "  FAIL pods not ready:"; not_ready | sed 's/^/     /'; queued; echo "  oc describe pod <name> -n $PROJECT; oc logs <name> -n $PROJECT --previous"; FAILED=$((FAILED + 1)); else echo "  every pod Running or Completed"; fi
fi

say "URLs"
for r in frontend n8n qdrant; do
  h=$(oc get route "$r" -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null || true)
  [ -n "$h" ] && printf '  %-14s https://%s\n' "$r" "$h"
done
h=$(oc get route frontend -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null || true)
[ -n "$h" ] && printf '  %-14s https://%s/admin  (password: oc extract secret/assistant-admin -n %s --keys=ADMIN_PASSWORD --to=-)\n' "admin portal" "$h" "$PROJECT"
h=$(oc get route object-store -n "$PROJECT" -o jsonpath='{.spec.host}' 2>/dev/null || true)
[ -n "$h" ] && printf '  %-14s https://%s/ui/\n' "object store" "$h"
echo "  Credentials: oc extract secret/assistant-object-store -n $PROJECT --to=-   (object store web UI); n8n: oc extract secret/assistant-n8n -n $PROJECT --keys=N8N_OWNER_PASSWORD --to=-"

if [ "${RUN_TESTS:-0}" = "1" ]; then
  # the chart's test pod, rendered with the same values (what helm test runs), through the script
  # that starts it again on another node when its node cannot start containers
  say "Running the connectivity test pod"
  NS="$PROJECT" RELEASE="$RELEASE" AVOID_NODES="$AVOID_NODES" AVOID_NODES_FILE="${AVOID_NODES_FILE:-}" "$ROOT/scripts/test-services.sh" \
    --set "global.domain=$DOMAIN" ${AVATAR_ARGS[@]+"${AVATAR_ARGS[@]}"} ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} "$@" || FAILED=$((FAILED + 1))
fi
[ "$FAILED" = 0 ] || { echo; echo "Deployment finished with $FAILED problem(s); see the FAIL lines above${LOG_FILE:+ and the log $LOG_FILE}"; exit 1; }
