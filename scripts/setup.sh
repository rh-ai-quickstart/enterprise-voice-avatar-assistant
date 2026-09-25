#!/usr/bin/env bash
# Interactive, resumable setup of the demo on a fresh cluster. Runs on the bastion host,
# logged in to the cluster as an administrator, from a clone of this repository.
#
#   scripts/setup.sh            discover the cluster, show progress, run every remaining step in order;
#                               it only stops for values it cannot know (keys, browser work) or on failure
#   scripts/setup.sh --status   discovery and progress only, changes nothing
#   scripts/setup.sh --step N   run step N (again), then stop
#   scripts/setup.sh --yes      never prompt: keys come from ~/secrets.env, missing ones are skipped (no Let's Encrypt e-mail)
#   scripts/setup.sh --reset    forget the saved progress (the cluster is not touched)
#
# Without a GPU the script stops and says what the demo needs. To use remote model endpoints
# instead, run it with PROFILE=remote and REMOTE_LLM_ENDPOINT, REMOTE_LLM_MODEL,
# REMOTE_STT_ENDPOINT, REMOTE_EMB_ENDPOINT (and the keys in ~/secrets.env).
#
# Progress and discovered facts are kept in ~/.assistant-setup/state.env. Every run is logged
# to ~/.assistant-setup/logs/setup-<timestamp>.log (the bootstrap and deploy steps add their
# own logs next to it); DEBUG=1 also writes a full command trace to <log>.trace. Every step
# checks the cluster before doing anything, so work done by hand or by an earlier run is
# recognised and not repeated.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"   # tools this script installs for the user go to ~/bin
STATE_DIR="${STATE_DIR:-$HOME/.assistant-setup}"
STATE="$STATE_DIR/state.env"
SECRETS_FILE="${SECRETS_FILE:-$HOME/secrets.env}"
PROJECT="${PROJECT:-voice-avatar-assistant}"
LLM_NAME="${LLM_NAME:-llama-32-3b-instruct}"
YES=0; MODE=next; ONLY=""
for a in "$@"; do case "$a" in --status) MODE=status;; --yes|-y) YES=1;; --reset) MODE=reset;; --step) MODE=step;; [0-9]*) ONLY="$a";; -h|--help) sed -n 2,14p "$0"; exit 0;; esac; done
mkdir -p "$STATE_DIR/logs"; touch "$STATE"; chmod 700 "$STATE_DIR"
[ "$MODE" = reset ] && { rm -f "$STATE"; touch "$STATE"; echo "progress forgotten ($STATE)"; exit 0; }
RUN_LOG="$STATE_DIR/logs/setup-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$RUN_LOG") 2>&1
if [ "${DEBUG:-0}" = 1 ]; then exec {BASH_XTRACEFD}>>"$RUN_LOG.trace"; export PS4='+ $(date +%H:%M:%S) ${BASH_SOURCE##*/}:${LINENO}: '; set -x; fi
printf '%s setup started by %s on %s; log %s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$(id -un)" "$(hostname)" "$RUN_LOG"

# ---------------------------------------------------------------- helpers -------------------
B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; D=$'\033[2m'; N=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ts()   { date +%H:%M:%S; }
run_step() {  # run_step N: runs stepN with a timestamped header and footer, records the duration
  local n=$1 start rc; start=$(date +%s)
  say ""; say "$(ts) ${D}---- step $n: ${STEPS[$((n-1))]} ----${N}"
  "step$n"; rc=$?
  save "STEP_${n}_LAST_RUN" "$(date +%Y-%m-%dT%H:%M) rc=$rc $(( $(date +%s) - start ))s"
  say "$(ts) ${D}---- step $n finished in $(( $(date +%s) - start ))s, exit $rc ----${N}"
  return $rc
}
ok()   { printf '  %sOK%s   %s\n' "$G" "$N" "$1"; }
warn() { printf '  %sWARN%s %s\n' "$Y" "$N" "$1"; }
bad()  { printf '  %sFAIL%s %s\n' "$R" "$N" "$1"; }
note() { printf '  %s..%s   %s\n' "$D" "$N" "$1"; }
save() { local k=$1 v=$2; { grep -v "^$k=" "$STATE" 2>/dev/null || true; } > "$STATE.tmp"; printf '%s=%q\n' "$k" "$v" >> "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; export "$k=$v"; }
unsave() { { grep -v "^$1=" "$STATE" 2>/dev/null || true; } > "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; unset "$1"; }
mark() { save "STEP_$1_DONE" "$(date +%Y-%m-%dT%H:%M)"; }
# ask <var> <prompt> <default>: interactive unless --yes; empty answer keeps the default
ask() { local var=$1 prompt=$2 def=${3:-}; local ans; if [ "$YES" = 1 ] && [ -n "$def" ]; then ans=$def; else read -r -p "  $prompt${def:+ [$def]}: " ans; ans=${ans:-$def}; fi; printf -v "$var" '%s' "$ans"; }
ask_secret() { local var=$1 prompt=$2; local ans=""; if [ "$YES" != 1 ]; then read -rs -p "  $prompt (typed text stays hidden): " ans; echo; fi; printf -v "$var" '%s' "$ans"; }
# need_key <KEY> <prompt> [regex] [hint]: a value for the secrets file, asked with hidden input
# until one is given (and matches the pattern). Enter alone asks again; typing Skip leaves the
# feature off. Values already in the file are not asked again. With --yes nothing can be
# asked: a missing key is reported and left off.
is_skip() { [[ "$1" =~ ^[Ss][Kk][Ii][Pp]$ ]]; }
skipped() { warn "$1 skipped: $2 stays off. To add it later: put it in $SECRETS_FILE and run scripts/setup.sh --step 5 then --step 6"; }
need_key() {
  local key=$1 prompt=$2 re=${3:-.} hint=${4:-} feature=${5:-this integration}; local v
  if [ -n "${!key:-}" ]; then ok "$key already in the file"; return 0; fi
  [ "$YES" != 1 ] || { skipped "$key" "$feature"; return 0; }
  while :; do
    read -rs -p "  $prompt (typed text stays hidden; type Skip to leave it out): " v; echo
    is_skip "$v" && { skipped "$key" "$feature"; return 0; }
    [ -n "$v" ] || { say "     ${Y}a value is needed${N}${hint:+: $hint} (or type Skip)"; continue; }
    [[ "$v" =~ $re ]] || { say "     ${Y}that does not look like it${N}${hint:+ ($hint)}; paste it again, or type Skip"; continue; }
    put "$key" "$v"; printf -v "$key" '%s' "$v"; export "$key"; ok "$key saved to $SECRETS_FILE"; return 0
  done
}
confirm() { local ans; [ "$YES" = 1 ] && return 0; read -r -p "  $1 [Y/n]: " ans; [ -z "$ans" ] || [[ "$ans" =~ ^[Yy] ]]; }
logfile() { echo "$STATE_DIR/logs/$1-$(date +%Y%m%d-%H%M%S).log"; }
# shellcheck disable=SC1090
. "$STATE" 2>/dev/null || true

# ---------------------------------------------------------------- login ---------------------
# Logs in when the bastion is not (or its token expired): proposes the API URL it can find,
# asks for the user (kubeadmin) and the password from the provisioning e-mail, hidden.
# helm renders the connectivity test pod (scripts/test-services.sh). The bastion usually has
# none: install it into ~/bin from the cluster's own download site, else from get.helm.sh.
ensure_helm() {
  command -v helm >/dev/null && { ok "helm $(helm version --short 2>/dev/null | cut -d+ -f1)"; return 0; }
  local arch; arch=$(uname -m); case "$arch" in x86_64) arch=amd64;; aarch64) arch=arm64;; esac
  mkdir -p "$HOME/bin"
  note "helm not found; installing it into $HOME/bin"
  if [ -n "${DOMAIN:-}" ] && curl -fsSL --max-time 120 "https://downloads-openshift-console.$DOMAIN/$arch/linux/helm" -o "$HOME/bin/helm.tmp" 2>/dev/null \
     && chmod +x "$HOME/bin/helm.tmp" && "$HOME/bin/helm.tmp" version --short >/dev/null 2>&1; then
    mv "$HOME/bin/helm.tmp" "$HOME/bin/helm"; ok "helm $(helm version --short | cut -d+ -f1) (from the cluster's download site)"; return 0
  fi
  rm -f "$HOME/bin/helm.tmp"
  local ver; ver=$(curl -fsSL --max-time 30 https://get.helm.sh/helm-latest-version 2>/dev/null | tr -d '[:space:]')
  if [ -n "$ver" ] && curl -fsSL --max-time 300 "https://get.helm.sh/helm-$ver-linux-$arch.tar.gz" | tar -xzO "linux-$arch/helm" > "$HOME/bin/helm.tmp" 2>/dev/null \
     && chmod +x "$HOME/bin/helm.tmp" && "$HOME/bin/helm.tmp" version --short >/dev/null 2>&1; then
    mv "$HOME/bin/helm.tmp" "$HOME/bin/helm"; ok "helm $ver (from get.helm.sh)"; return 0
  fi
  rm -f "$HOME/bin/helm.tmp"
  bad "could not install helm; install it by hand (https://helm.sh/docs/intro/install/) into \$PATH and run scripts/setup.sh again"
  return 1
}
ensure_login() {
  command -v oc >/dev/null || { bad "oc is not installed on this host (https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/)"; return 1; }
  if oc whoami >/dev/null 2>&1; then return 0; fi
  say "${B}Cluster login${N}"
  local reason; reason=$(oc whoami 2>&1 | head -1); note "oc whoami: ${reason:-no session}"
  local -a cands=(); local u host
  [ -n "${API_URL:-}" ] && cands+=("$API_URL")
  while read -r u; do [ -n "$u" ] && cands+=("$u"); done < <(oc config view -o jsonpath='{range .clusters[*]}{.cluster.server}{"\n"}{end}' 2>/dev/null)
  host=$(hostname -f 2>/dev/null || hostname); case "$host" in bastion.*) cands+=("https://api.${host#bastion.}:6443");; esac
  local -a uniq=(); for u in "${cands[@]}"; do case " ${uniq[*]:-} " in *" $u "*) ;; *) uniq+=("$u");; esac; done
  if [ "${#uniq[@]}" -gt 1 ]; then say "  API URLs found on this host:"; printf '     %s\n' "${uniq[@]}"; fi
  local api user pass
  ask api "API URL (from the provisioning e-mail, https://api.<guid>.<base domain>:6443)" "${uniq[0]:-}"
  [ -n "$api" ] || { bad "an API URL is needed"; return 1; }
  ask user "User" "${LOGIN_USER:-kubeadmin}"
  ask_secret pass "Password for $user"
  [ -n "$pass" ] || { bad "a password is needed"; return 1; }
  if oc login "$api" -u "$user" -p "$pass" >/dev/null 2>&1 || oc login "$api" -u "$user" -p "$pass" --insecure-skip-tls-verify=true >/dev/null 2>&1; then
    ok "logged in to $api as $(oc whoami)"; save API_URL "$api"; save LOGIN_USER "$user"
    oc auth can-i create namespaces >/dev/null 2>&1 && ok "cluster-admin permissions" || warn "this user cannot create cluster resources; the bootstrap needs kubeadmin or a cluster-admin"
    return 0
  fi
  bad "login failed: $(oc login "$api" -u "$user" -p "$pass" --insecure-skip-tls-verify=true 2>&1 | tail -1)"
  note "check the API URL and password in the provisioning e-mail; from the laptop the same values work with oc as well"
  return 1
}

# ---------------------------------------------------------------- discovery -----------------
discover() {
  API=""; USER_NAME=""; OCP_VERSION=""; DOMAIN="${DOMAIN:-}"; NODE_COUNT=0; INSTANCE=""; GPUS="${GPUS:-0}"; GPU_PRODUCT=""; GPU_MEMORY=""; GPU_REPLICAS=""; GPU_ALLOC=0
  RHOAI_VERSION=""; DSC=""; KSERVE=""; OP_NFD=no; OP_GPU=no; OP_CM=no; OP_GITOPS=no; LLM_NS="${LLM_NS:-}"; LLM_READY=""; LLM_SHARE=""
  PROJECT_EXISTS=no; ARGO_READY=no; TURN_SECRET=no; SECRETS_IN_CLUSTER=no; APP_STATE=""; ISVC_TOTAL=0; ISVC_READY=0; PODS_NOT_READY=1; FRONTEND_URL=""; N8N_URL=""; DOCS_INDEXED=""
  LOGGED_IN=0; command -v oc >/dev/null && oc whoami >/dev/null 2>&1 && LOGGED_IN=1
  [ "$LOGGED_IN" = 1 ] || return
  progress() { [ "${QUIET_DISCOVERY:-0}" = 1 ] || printf '  %s..%s %s\n' "$D" "$N" "$1"; }
  progress "cluster and nodes"
  API=$(oc whoami --show-server); USER_NAME=$(oc whoami)
  OCP_VERSION=$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null)
  DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}' 2>/dev/null)
  NODE_COUNT=$(oc get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
  INSTANCE=$(oc get nodes -o jsonpath='{.items[0].metadata.labels.node\.kubernetes\.io/instance-type}' 2>/dev/null)
  GPUS=$(oc get nodes -o json 2>/dev/null | jq '[.items[].metadata.labels["nvidia.com/gpu.count"] // "0" | tonumber] | add')
  [ "${GPUS:-0}" -gt 0 ] || GPUS=$(oc get nodes -o json 2>/dev/null | jq '[.items[].status.capacity["nvidia.com/gpu"] // "0" | tonumber] | add')
  GPU_PRODUCT=$(oc get nodes -o jsonpath='{.items[0].metadata.labels.nvidia\.com/gpu\.product}' 2>/dev/null)
  GPU_MEMORY=$(oc get nodes -o jsonpath='{.items[0].metadata.labels.nvidia\.com/gpu\.memory}' 2>/dev/null)
  GPU_REPLICAS=$(oc get nodes -o jsonpath='{.items[0].metadata.labels.nvidia\.com/gpu\.replicas}' 2>/dev/null)
  GPU_ALLOC=$(oc get nodes -o json 2>/dev/null | jq '[.items[].status.allocatable["nvidia.com/gpu"] // "0" | tonumber] | add')
  progress "operators"
  # CSVs are looked up in the operator's own namespace (and openshift-operators): a cluster-wide
  # listing returns every copied CSV of every namespace and takes minutes on a busy cluster
  csv_version() {  # <prefix> <namespace...>: version of the first Succeeded CSV found
    local n=$1; shift; local ns
    for ns in "$@"; do oc get csv -n "$ns" -o json 2>/dev/null | jq -r --arg n "$n" '.items[] | select(.metadata.name | startswith($n)) | select(.status.phase=="Succeeded") | .spec.version' | head -1 | grep . && return 0; done
    return 1
  }
  RHOAI_VERSION=$(csv_version rhods-operator redhat-ods-operator openshift-operators || true)
  DSC=$(oc get datasciencecluster -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  KSERVE=$(oc get datasciencecluster "$DSC" -o jsonpath='{.spec.components.kserve.managementState}' 2>/dev/null)
  OP_NFD=$(csv_version nfd openshift-nfd openshift-operators >/dev/null && echo yes || echo no)
  OP_GPU=$(csv_version gpu-operator-certified nvidia-gpu-operator openshift-operators >/dev/null && echo yes || echo no)
  OP_CM=$(csv_version cert-manager-operator cert-manager-operator openshift-operators >/dev/null && echo yes || echo no)
  OP_GITOPS=$(csv_version openshift-gitops-operator openshift-gitops-operator openshift-operators >/dev/null && echo yes || echo no)
  progress "language model"
  LLM_NS=$(oc get isvc -A -o json 2>/dev/null | jq -r --arg n "$LLM_NAME" '.items[] | select(.metadata.name==$n) | .metadata.namespace' | head -1)
  LLM_READY=""; LLM_SHARE=""
  if [ -n "$LLM_NS" ]; then
    LLM_READY=$(oc get isvc "$LLM_NAME" -n "$LLM_NS" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    LLM_SHARE=$(oc get isvc "$LLM_NAME" -n "$LLM_NS" -o json 2>/dev/null | jq -r '.spec.predictor.model.args // [] | .[] | select(startswith("--gpu-memory-utilization")) | sub("^--gpu-memory-utilization=?";"")' | head -1)
  fi
  progress "project and application"
  PROJECT_EXISTS=$(oc get namespace "$PROJECT" >/dev/null 2>&1 && echo yes || echo no)
  ARGO_READY=$([ "$(oc get deployment openshift-gitops-server -n openshift-gitops -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "1" ] && echo yes || echo no)
  TURN_SECRET=$(oc get secret livekit-turn-tls -n "$PROJECT" >/dev/null 2>&1 && echo yes || echo no)
  SECRETS_IN_CLUSTER=$(oc get secret assistant-integrations -n "$PROJECT" >/dev/null 2>&1 && echo yes || echo no)
  APP_STATE=$(oc get applications.argoproj.io voice-avatar-assistant -n openshift-gitops -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null)
  ISVC_TOTAL=$(oc get isvc -n "$PROJECT" --no-headers 2>/dev/null | wc -l | tr -d ' ')
  ISVC_READY=$(oc get isvc -n "$PROJECT" -o json 2>/dev/null | jq '[.items[] | select(.status.conditions[]? | select(.type=="Ready" and .status=="True"))] | length')
  PODS_NOT_READY=$(oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' | wc -l | tr -d ' ')
  FRONTEND_URL=$(oc get route frontend -n "$PROJECT" -o jsonpath='https://{.spec.host}' 2>/dev/null)
  N8N_URL=$(oc get route n8n -n "$PROJECT" -o jsonpath='https://{.spec.host}' 2>/dev/null)
  DOCS_INDEXED=""
  if [ "$PODS_NOT_READY" = 0 ] && [ -n "$FRONTEND_URL" ]; then
    DOCS_INDEXED=$(oc exec deploy/rag-api -n "$PROJECT" -- .venv/bin/python -c 'import urllib.request,json; print(len(json.load(urllib.request.urlopen("http://ingestion:8080/v1/documents", timeout=10))))' 2>/dev/null || echo "")
  fi
}

# ---------------------------------------------------------------- profile -------------------
# gpu:    every GPU on the cluster is used, each advertised 4 times through time-slicing so the
#         language model, Whisper and BGE-M3 can share a card; fixed memory shares; no guardrails
# remote: no GPU, every model is a remote OpenAI-compatible endpoint (PROFILE=remote)
suggest_profile() { if [ "${GPUS:-0}" -eq 0 ]; then echo remote; else echo gpu; fi; }
profile_slices() { case "$1" in gpu) echo 4;; *) echo 1;; esac; }
write_values_object() {  # -> $STATE_DIR/values-object.json, deep-merged over the values file by Argo CD
  local llm_deploy=true; [ -n "${LLM_NS:-}" ] && llm_deploy=false
  case "$PROFILE" in
  remote)
    jq -n --arg le "$REMOTE_LLM_ENDPOINT" --arg lm "$REMOTE_LLM_MODEL" --arg se "$REMOTE_STT_ENDPOINT" --arg sm "$REMOTE_STT_MODEL" --arg ee "$REMOTE_EMB_ENDPOINT" --arg em "$REMOTE_EMB_MODEL" \
      '{models:{llm:{deploy:false,endpoint:$le,servedModelName:$lm},stt:{deploy:false,endpoint:$se,servedModelName:$sm},embeddings:{deploy:false,endpoint:$ee,servedModelName:$em},guardrails:{provider:"none",deploy:false}}}' ;;
  *)
    jq -n --argjson d "$llm_deploy" '{models:{llm:{deploy:$d,args:["--max-model-len=8192","--gpu-memory-utilization=0.45","--max-num-seqs=8","--enforce-eager","--enable-auto-tool-choice","--tool-call-parser=llama3_json"]},stt:{deploy:true,args:["--gpu-memory-utilization=0.15","--max-num-seqs=8","--enforce-eager"]},embeddings:{args:["--runner=pooling","--max-model-len=8192","--gpu-memory-utilization=0.12","--max-num-seqs=8","--enforce-eager"]},guardrails:{provider:"none",deploy:false}}}' ;;
  esac > "$STATE_DIR/values-object.json"
}

# ---------------------------------------------------------------- status --------------------
STEPS=("Cluster and prerequisites" "Deployment profile" "Cluster bootstrap" "TURN certificate" "Keys and integrations" "Deploy with Argo CD" "n8n workflows" "Sample documents" "Verification")
step_state() {  # prints done|todo|attention and a detail
  case "$1" in
  1) if [ "$LOGGED_IN" != 1 ]; then echo "attention|not logged in to a cluster"; elif [ -z "$RHOAI_VERSION" ]; then echo "attention|OpenShift AI not found";
     elif [[ "$RHOAI_VERSION" == 2.* ]]; then echo "attention|OpenShift AI $RHOAI_VERSION; 3.x is required"; elif [ "$KSERVE" != Managed ]; then echo "todo|KServe is ${KSERVE:-unset} (bootstrap fixes it)";
     else echo "done|OpenShift $OCP_VERSION, OpenShift AI $RHOAI_VERSION, $NODE_COUNT node(s) $INSTANCE, ${GPUS:-0} GPU(s) ${GPU_PRODUCT:+($GPU_PRODUCT)}, KServe Managed"; fi ;;
  2) if [ -n "${PROFILE:-}" ]; then echo "done|$PROFILE${LLM_NS:+; language model $LLM_NS/$LLM_NAME}"; else echo "todo|suggested: $(suggest_profile)"; fi ;;
  3) if [ "$PROJECT_EXISTS" = yes ] && [ "$ARGO_READY" = yes ] && [ -n "${STEP_3_DONE:-}" ]; then echo "done|project, Argo CD, GPU sharing (${GPU_ALLOC:-?} schedulable GPUs)";
     elif [ "$PROJECT_EXISTS" = yes ] && [ "$ARGO_READY" = yes ]; then echo "todo|project and Argo CD exist; run to verify GPU sharing and the model share"; else echo "todo|project ${PROJECT_EXISTS}, Argo CD ${ARGO_READY}"; fi ;;
  4) if [ "$TURN_SECRET" = yes ]; then echo "done|secret livekit-turn-tls present"; elif [ "$PROJECT_EXISTS" != yes ]; then echo "todo|after the bootstrap"; else echo "todo|secret missing"; fi ;;
  5) if [ "$SECRETS_IN_CLUSTER" = yes ] && [ -n "${STEP_5_DONE:-}" ]; then echo "done|$SECRETS_FILE and the cluster secrets"; elif [ "$SECRETS_IN_CLUSTER" = yes ]; then echo "todo|cluster secrets exist; run to review the keys"; elif [ -f "$SECRETS_FILE" ]; then echo "todo|$SECRETS_FILE exists, not yet applied"; else echo "todo|no $SECRETS_FILE yet"; fi ;;
  6) if [ "$APP_STATE" = "Synced/Healthy" ] && [ "${PODS_NOT_READY:-1}" = 0 ]; then echo "done|application Synced/Healthy, ${ISVC_READY:-0}/${ISVC_TOTAL:-0} models Ready"; elif [ -n "$APP_STATE" ]; then echo "attention|application $APP_STATE, ${PODS_NOT_READY} pod(s) not ready"; else echo "todo|not deployed"; fi ;;
  7) if [ -n "${STEP_7_DONE:-}" ]; then echo "done|owner account, API key, workflows active"; elif [ -n "$N8N_URL" ]; then echo "todo|$N8N_URL"; else echo "todo|after the deployment"; fi ;;
  8) if [ -n "$DOCS_INDEXED" ] && [ "$DOCS_INDEXED" -ge 10 ]; then echo "done|$DOCS_INDEXED documents indexed"; elif [ -n "$DOCS_INDEXED" ]; then echo "todo|$DOCS_INDEXED documents indexed"; else echo "todo|after the deployment"; fi ;;
  9) if [ -n "${STEP_9_DONE:-}" ]; then echo "done|preflight passed $STEP_9_DONE"; else echo "todo|preflight not run"; fi ;;
  esac
}
show_status() {
  say ""; say "${B}Enterprise voice avatar assistant: setup${N}"
  if [ "$LOGGED_IN" = 1 ]; then say "  cluster $API as $USER_NAME, apps domain $DOMAIN"; else say "  ${R}not logged in${N}: the script asks for the API URL, user and password when run"; fi
  say "  progress file $STATE; this run's log $RUN_LOG"; say ""
  NEXT=""
  for i in 1 2 3 4 5 6 7 8 9; do
    local st; st=$(step_state $i); local kind=${st%%|*} detail=${st#*|} icon="○"
    case "$kind" in done) icon="${G}✔${N}";; attention) icon="${R}!${N}";; esac
    [ -z "$NEXT" ] && [ "$kind" != done ] && NEXT=$i
    printf '  %s %s %-26s %s%s%s\n' "$icon" "$i" "${STEPS[$((i-1))]}" "$D" "$detail" "$N"
  done
  say ""
}

# ---------------------------------------------------------------- steps ---------------------
step1() {
  say "${B}Step 1: cluster and prerequisites${N}"
  [ "$LOGGED_IN" = 1 ] || { ensure_login && discover || return 1; }
  for t in oc jq git openssl curl python3; do command -v $t >/dev/null && ok "$t" || { bad "$t missing (sudo dnf install -y $t)"; return 1; }; done
  ensure_helm || return 1
  oc auth can-i create namespaces >/dev/null 2>&1 && ok "cluster-admin permissions" || { bad "this user cannot create cluster resources; use kubeadmin"; return 1; }
  ok "OpenShift $OCP_VERSION on $NODE_COUNT node(s), $INSTANCE"
  case "$OCP_VERSION" in 4.1[0-8].*) warn "OpenShift AI 3.x needs OpenShift 4.19.9 or later";; esac
  [ -n "$RHOAI_VERSION" ] && ok "OpenShift AI $RHOAI_VERSION" || { bad "OpenShift AI is not installed; this guide assumes an environment with OpenShift AI 3 (see the README prerequisites)"; return 1; }
  [[ "$RHOAI_VERSION" == 3.* ]] || { bad "OpenShift AI $RHOAI_VERSION found; 3.x is required"; return 1; }
  [ "$KSERVE" = Managed ] && ok "KServe Managed in DataScienceCluster $DSC" || warn "KServe is ${KSERVE:-unset}; the bootstrap sets it to Managed"
  for o in "NFD:$OP_NFD" "GPU Operator:$OP_GPU" "cert-manager:$OP_CM" "GitOps:$OP_GITOPS"; do [ "${o#*:}" = yes ] && ok "${o%%:*} installed" || warn "${o%%:*} missing; the bootstrap installs it"; done
  if [ "${GPUS:-0}" -gt 0 ]; then ok "$GPUS GPU(s) ${GPU_PRODUCT:+$GPU_PRODUCT }(schedulable now: ${GPU_ALLOC:-?})"; else warn "no GPU on this cluster; models will have to be remote endpoints"; fi
  if [ -n "$LLM_NS" ]; then ok "language model $LLM_NS/$LLM_NAME (Ready=$LLM_READY, GPU share ${LLM_SHARE:-default 0.9})"; else note "no InferenceService $LLM_NAME on the cluster; the chart can deploy Llama 3.1 8B itself (needs a GPU)"; fi
  sc=$(oc get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{end}')
  [ -n "$sc" ] && ok "default StorageClass $sc" || { bad "no default StorageClass"; return 1; }
  save OCP_VERSION "$OCP_VERSION"; save RHOAI_VERSION "$RHOAI_VERSION"; save GPUS "${GPUS:-0}"; save DOMAIN "$DOMAIN"; save LLM_NS "${LLM_NS:-}"
  [ "$KSERVE" = Managed ]
}
step2() {
  say "${B}Step 2: deployment profile${N} (decided from the GPUs on the cluster)"
  local mem_gib=""; [ -n "$GPU_MEMORY" ] && mem_gib=$((GPU_MEMORY / 1024))
  say "  found: ${GPUS:-0} GPU(s)${GPU_PRODUCT:+ $GPU_PRODUCT}${mem_gib:+ with $mem_gib GiB each}${LLM_NS:+; language model $LLM_NS/$LLM_NAME already deployed}"
  say "  the demo needs on GPUs: the language model, Whisper large-v3-turbo and BGE-M3."
  if [ "${PROFILE:-}" = remote ]; then
    for v in REMOTE_LLM_ENDPOINT REMOTE_LLM_MODEL REMOTE_STT_ENDPOINT REMOTE_EMB_ENDPOINT; do [ -n "${!v:-}" ] || { bad "PROFILE=remote needs $v (OpenAI-compatible base URL including /v1, or the model id)"; return 1; }; save "$v" "${!v}"; done
    save REMOTE_STT_MODEL "${REMOTE_STT_MODEL:-whisper-large-v3-turbo}"; save REMOTE_EMB_MODEL "${REMOTE_EMB_MODEL:-bge-m3}"
    ok "remote profile: every model is a remote endpoint; keys LLM_API_KEY, STT_API_KEY, EMBEDDINGS_API_KEY go into $SECRETS_FILE"
  elif [ "${GPUS:-0}" -eq 0 ]; then
    bad "not enough GPUs: the demo needs at least 1 NVIDIA GPU with 24 GB (an L4), this cluster has none."
    say "     required: 1 GPU of 24 GB shared by the language model (55%), Whisper (15%) and BGE-M3 (12%)"
    say "     available: 0 GPUs (no node carries nvidia.com/gpu.count; check the instance type with: oc get nodes -L node.kubernetes.io/instance-type)"
    say "     options: add a GPU node (g6.8xlarge or larger), or run with remote model endpoints: PROFILE=remote REMOTE_LLM_ENDPOINT=... scripts/setup.sh"
    return 1
  elif [ -n "$GPU_MEMORY" ] && [ "$GPU_MEMORY" -lt 20000 ]; then
    bad "not enough GPU memory: the demo needs 22 GB or more on one GPU, this cluster's ${GPU_PRODUCT:-GPU} has $mem_gib GiB."
    say "     required: language model 55% (about 12.5 GB with the 3B model), Whisper 15% (3.4 GB), BGE-M3 12% (2.7 GB) on the same card"
    say "     available: $GPUS x ${GPU_PRODUCT:-GPU} with $mem_gib GiB"
    say "     options: a GPU with 24 GB (L4, A10G, or larger), or remote endpoints for the language model (PROFILE=remote)"
    return 1
  else
    PROFILE=gpu
    ok "gpu profile: all $GPUS GPU(s) used, each advertised 4 times through time-slicing"
    if [ -n "$LLM_NS" ]; then say "     language model: $LLM_NS/$LLM_NAME, its GPU memory share is lowered to 55% in step 3"; else say "     language model: Llama 3.1 8B (4-bit) deployed by the chart at 45%"; fi
    say "     Whisper 15%, BGE-M3 12%; no guardrail model in this demo"
  fi
  save PROFILE "$PROFILE"; write_values_object; ok "profile $PROFILE saved ($STATE_DIR/values-object.json)"; mark 2
}
step3() {
  say "${B}Step 3: cluster bootstrap${N} (operators, KServe, GPU sharing, model share, Argo CD, project)"
  [ -n "${PROFILE:-}" ] || { bad "choose the profile first (step 2)"; return 1; }
  local log; log=$(logfile bootstrap); local slices; slices=$(profile_slices "$PROFILE")
  local frac=0.55
  say "  running scripts/bootstrap-cluster.sh with GPU_SLICES=$slices LLM_GPU_FRACTION=$frac (log $log)"
  PROJECT="$PROJECT" GPU_SLICES="$slices" LLM_NAME="${LLM_NS:+$LLM_NAME}" LLM_GPU_FRACTION="$frac" LOG_FILE="$log" "$ROOT/scripts/bootstrap-cluster.sh" && { mark 3; return 0; }
  bad "bootstrap reported problems; see $log, fix, and run: scripts/setup.sh --step 3"; return 1
}
step4() {
  say "${B}Step 4: TURN certificate${N} (voice through corporate networks needs TURN over TLS with a trusted certificate)"
  [ "$PROJECT_EXISTS" = yes ] || { bad "project missing; run step 3 first"; return 1; }
  local host="livekit-turn-${PROJECT}.${DOMAIN}" secret=livekit-turn-tls
  local name; name=$(oc get ingresscontroller default -n openshift-ingress-operator -o jsonpath='{.spec.defaultCertificate.name}' 2>/dev/null); name="${name:-router-certs-default}"
  local tmp; tmp=$(mktemp -d)
  say "  \$ oc get secret $name -n openshift-ingress   (the cluster's wildcard certificate)"
  if oc get secret "$name" -n openshift-ingress -o jsonpath='{.data.tls\.crt}' 2>/dev/null | base64 -d > "$tmp/tls.crt" && [ -s "$tmp/tls.crt" ] \
     && oc get secret "$name" -n openshift-ingress -o jsonpath='{.data.tls\.key}' | base64 -d > "$tmp/tls.key" \
     && openssl x509 -in "$tmp/tls.crt" -noout -ext subjectAltName 2>/dev/null | grep -q "\*\.${DOMAIN}" \
     && openssl verify -untrusted "$tmp/tls.crt" "$tmp/tls.crt" >/dev/null 2>&1; then
    note "$(openssl x509 -in "$tmp/tls.crt" -noout -issuer -enddate | tr '\n' ' ')"
    oc create secret tls "$secret" -n "$PROJECT" --cert="$tmp/tls.crt" --key="$tmp/tls.key" --dry-run=client -o yaml | oc apply -f - >/dev/null \
      && ok "secret $PROJECT/$secret created from the trusted wildcard certificate for $host"
    rm -rf "$tmp"; mark 4; return 0
  fi
  rm -rf "$tmp"
  warn "the wildcard certificate is self-signed or does not cover *.$DOMAIN; cert-manager can request one from Let's Encrypt (the apps domain must be reachable from the internet)"
  [ "$YES" = 1 ] || ask ACME_EMAIL "E-mail for Let's Encrypt (empty to skip TURN for now)" "${ACME_EMAIL:-}"
  [ -n "${ACME_EMAIL:-}" ] || { warn "TURN skipped: voice works on open networks only. Rerun later: scripts/setup.sh --step 4"; mark 4; return 0; }
  save ACME_EMAIL "$ACME_EMAIL"
  oc get crd clusterissuers.cert-manager.io >/dev/null 2>&1 || { bad "cert-manager is not installed (step 3 installs it)"; return 1; }
  oc apply -f - <<YAML >/dev/null
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-http01
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ${ACME_EMAIL}
    privateKeySecretRef:
      name: letsencrypt-http01-account
    solvers:
      - http01:
          ingress:
            ingressClassName: openshift-default
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: ${secret}
  namespace: ${PROJECT}
spec:
  secretName: ${secret}
  dnsNames:
    - ${host}
  issuerRef:
    name: letsencrypt-http01
    kind: ClusterIssuer
YAML
  ok "ClusterIssuer letsencrypt-http01 and Certificate $PROJECT/$secret applied; waiting for the ACME challenge"
  cert_ready() { [ "$(oc get certificate "$secret" -n "$PROJECT" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = "True" ]; }
  local waited=0
  until cert_ready; do
    [ "$waited" -ge 600 ] && { bad "certificate not Ready after 10 min; oc describe certificate $secret -n $PROJECT; oc get order,challenge -n $PROJECT"; return 1; }
    sleep 15; waited=$((waited + 15)); [ $((waited % 60)) -eq 0 ] && note "waiting for the ACME challenge (${waited}s)"
  done
  ok "certificate Ready for $host (renews itself)"; mark 4
}
# pause <text>: shows a browser action and waits for Enter (Skip returns 1). With --yes it only prints.
pause() { say "$1"; [ "$YES" = 1 ] && return 0; local a; read -r -p "  Press Enter when done (or type Skip): " a; ! is_skip "$a"; }
slack_api() { curl -s --max-time 20 -H "Authorization: Bearer $SLACK_BOT_TOKEN" "https://slack.com/api/$1" "${@:2}"; }
# google_token <key file>: prints an access token obtained with the service account (JWT signed
# by openssl), or ERROR <reason>. Proves the key is valid and the Drive API accepts it.
google_token() {
  python3 - "$1" <<'PY'
import base64, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
d = json.load(open(sys.argv[1]))
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
now = int(time.time())
msg = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps({"iss": d["client_email"], "scope": "https://www.googleapis.com/auth/drive", "aud": d["token_uri"], "iat": now, "exp": now + 600}).encode())
kf = tempfile.NamedTemporaryFile("w", delete=False); kf.write(d["private_key"]); kf.close()
try:
    sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", kf.name], input=msg.encode(), capture_output=True, check=True).stdout
finally:
    os.unlink(kf.name)
req = urllib.request.Request(d["token_uri"], data=urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": msg + "." + b64(sig)}).encode())
try:
    print(json.load(urllib.request.urlopen(req, timeout=20))["access_token"])
except urllib.error.HTTPError as e:
    print("ERROR " + e.read().decode()[:200].replace("\n", " "))
except Exception as e:  # noqa: BLE001
    print("ERROR " + str(e)[:200])
PY
}
step5() {
  say "${B}Step 5: keys and integrations${N} (one file: $SECRETS_FILE; each browser action is shown one at a time and checked)"
  local n8n_host="n8n-$PROJECT.$DOMAIN"
  if [ ! -f "$SECRETS_FILE" ]; then cp "$ROOT/secrets.env.example" "$SECRETS_FILE" && chmod 600 "$SECRETS_FILE" && ok "created $SECRETS_FILE from secrets.env.example"; fi
  # shellcheck disable=SC1090
  set -a; . "$SECRETS_FILE"; set +a
  put() { local k=$1 v=$2; { grep -v "^$k=" "$SECRETS_FILE" || true; } > "$SECRETS_FILE.tmp"; printf '%s=%s\n' "$k" "$v" >> "$SECRETS_FILE.tmp"; mv "$SECRETS_FILE.tmp" "$SECRETS_FILE"; chmod 600 "$SECRETS_FILE"; }
  local v r

  # ---- n8n owner --------------------------------------------------------------------------
  say ""; say "  ${B}n8n owner account${N} (created by the chart's n8n-setup job; nothing to do in the n8n UI)"
  local cl_email cl_pass; cl_email=$(oc get secret assistant-n8n -n "$PROJECT" -o jsonpath='{.data.N8N_OWNER_EMAIL}' 2>/dev/null | base64 -d); cl_pass=$(oc get secret assistant-n8n -n "$PROJECT" -o jsonpath='{.data.N8N_OWNER_PASSWORD}' 2>/dev/null | base64 -d)
  if [ -n "$cl_pass" ]; then
    # the account exists (or will, with these values): the cluster's values win and go into the file
    { [ "${N8N_OWNER_EMAIL:-}" = "$cl_email" ] && [ "${N8N_OWNER_PASSWORD:-}" = "$cl_pass" ]; } || { put N8N_OWNER_EMAIL "$cl_email"; put N8N_OWNER_PASSWORD "$cl_pass"; N8N_OWNER_EMAIL=$cl_email; N8N_OWNER_PASSWORD=$cl_pass; }
    ok "n8n owner $cl_email; the password is in $SECRETS_FILE (N8N_OWNER_PASSWORD). To change it, use the n8n UI (Settings > Personal), then update the file"
  else
    if [ -n "${N8N_OWNER_EMAIL:-}" ] && [ -n "${N8N_OWNER_PASSWORD:-}" ]; then ok "n8n owner ${N8N_OWNER_EMAIL} from the file"
    elif [ "$YES" = 1 ]; then note "n8n owner admin@example.com with a generated password (both stored in secret assistant-n8n and written to $SECRETS_FILE by the next run)"
    else
      ask v "5o. E-mail for the n8n owner account (the login of the n8n UI)" "${N8N_OWNER_EMAIL:-admin@example.com}"
      while ! [[ "$v" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; do say "     ${Y}that is not an e-mail address${N}"; ask v "    E-mail for the n8n owner account" "admin@example.com"; done
      put N8N_OWNER_EMAIL "$v"; N8N_OWNER_EMAIL=$v
      while :; do
        read -rs -p "      Password for $v (at least 8 characters with a number and a capital letter; Enter generates one): " v2; echo
        if [ -z "$v2" ]; then v2="Aa1$(openssl rand -hex 5)"; ok "password generated"; break; fi
        [ "${#v2}" -ge 8 ] && [[ "$v2" =~ [0-9] ]] && [[ "$v2" =~ [A-Z] ]] && break
        say "     ${Y}n8n needs at least 8 characters, a number and a capital letter${N}"
      done
      put N8N_OWNER_PASSWORD "$v2"; N8N_OWNER_PASSWORD=$v2
      ok "n8n owner $N8N_OWNER_EMAIL; the password is in $SECRETS_FILE (N8N_OWNER_PASSWORD)"
    fi
  fi

  # ---- Slack ------------------------------------------------------------------------------
  say ""; say "  ${B}Slack${N} (approval cards and notifications; n8n's Slack credential is created from the token by the chart)"
  local manifest="$HOME/slack-app-manifest.json"
  sed "s/N8N_HOST/$n8n_host/" "$ROOT/n8n/slack-app-manifest.json" > "$manifest"
  if [ -n "${SLACK_BOT_TOKEN:-}" ]; then ok "SLACK_BOT_TOKEN already in the file"; else
    say "  5a. On your laptop open https://api.slack.com/apps > Create New App > From a manifest > choose the workspace,"
    say "      paste the manifest below (also saved as $manifest) and create the app:"
    sed 's/^/        /' "$manifest"
    pause "" || skipped SLACK_BOT_TOKEN "Slack"
  fi
  while :; do
    [ -n "${SLACK_BOT_TOKEN:-}" ] || { need_key SLACK_BOT_TOKEN "5b. Install App > Install to Workspace, then paste the Bot User OAuth Token" '^xoxb-' "it starts with xoxb-" "Slack (approval cards, notifications)"; }
    [ -n "${SLACK_BOT_TOKEN:-}" ] || break
    r=$(slack_api auth.test)
    if [ "$(printf '%s' "$r" | jq -r '.ok')" = "true" ]; then ok "Slack: app '$(printf '%s' "$r" | jq -r .user)' in workspace '$(printf '%s' "$r" | jq -r .team)'"; break; fi
    say "     ${Y}Slack rejects that token${N} ($(printf '%s' "$r" | jq -r '.error // "no answer"')); paste it again, or type Skip"
    put SLACK_BOT_TOKEN ""; unset SLACK_BOT_TOKEN
  done
  if [ -n "${SLACK_BOT_TOKEN:-}" ]; then
    # channels: create the missing ones and join them (scopes channels:manage, channels:join from the manifest);
    # an app installed with fewer scopes gets the instruction instead
    local chans; chans=$(slack_api "conversations.list?types=public_channel&exclude_archived=true&limit=999")
    local c missing=() notmember=()
    for c in assistant-ingestion assistant-documents assistant-approvals assistant-tickets assistant-knowledge-gaps; do
      case "$(printf '%s' "$chans" | jq -r --arg c "$c" '.channels[]? | select(.name==$c) | .is_member')" in
        true) ;; false) notmember+=("$c");; *) missing+=("$c");; esac
    done
    for c in "${missing[@]}"; do
      r=$(slack_api conversations.create -X POST -H 'Content-Type: application/json' -d "{\"name\":\"$c\"}")
      if [ "$(printf '%s' "$r" | jq -r .ok)" = "true" ]; then ok "channel #$c created (the app is a member)"; else warn "cannot create #$c ($(printf '%s' "$r" | jq -r .error)); create it in Slack and invite the app"; notmember+=("$c"); fi
    done
    for c in "${notmember[@]}"; do
      local id; id=$(slack_api "conversations.list?types=public_channel&exclude_archived=true&limit=999" | jq -r --arg c "$c" '.channels[]? | select(.name==$c) | .id')
      r=$([ -n "$id" ] && slack_api conversations.join -X POST -H 'Content-Type: application/json' -d "{\"channel\":\"$id\"}" || echo '{"ok":false,"error":"channel_not_found"}')
      if [ "$(printf '%s' "$r" | jq -r .ok)" = "true" ]; then ok "app joined #$c"; else
        pause "  5c. In Slack, create the channel #$c (if missing) and invite the app to it: /invite @Enterprise Assistant" || true
      fi
    done
    # clicks on the approval cards are refused unless Slack signed them with the app's signing secret
    need_key SLACK_SIGNING_SECRET "5d. In the app's settings, Basic Information > App Credentials, show and paste the Signing Secret" '^[0-9a-f]{32}$' "32 hexadecimal characters" "Slack buttons (approvals then happen in the admin portal only)"
    [ "${#missing[@]}" = 0 ] && [ "${#notmember[@]}" = 0 ] && ok "channels #assistant-ingestion #assistant-documents #assistant-approvals #assistant-tickets #assistant-knowledge-gaps exist and the app is in each"
    # the request URL contains this cluster's domain: set by the manifest for a new app, by hand for a reused one
    if [ "${SLACK_INTERACTIVITY_HOST:-}" = "$n8n_host" ]; then ok "Interactivity request URL confirmed for $n8n_host"
    elif [ "$YES" = 1 ]; then warn "check the app's Interactivity request URL: https://$n8n_host/webhook/slack-interactions"
    else
      if confirm "5e. Was the app created just now from the manifest above (its request URL then already points to this cluster)?"; then save SLACK_INTERACTIVITY_HOST "$n8n_host"; ok "Interactivity request URL set by the manifest"
      else
        pause "  5e. In the app's settings, Interactivity & Shortcuts, set the Request URL to https://$n8n_host/webhook/slack-interactions and Save Changes." && { save SLACK_INTERACTIVITY_HOST "$n8n_host"; ok "Interactivity request URL confirmed"; } || warn "request URL not confirmed: approval buttons in Slack will not reach n8n until it is set"
      fi
    fi
  fi

  # ---- Tavus ------------------------------------------------------------------------------
  say ""; say "  ${B}Tavus${N} (avatar video). Free plan: 25 conversational minutes a month, one stream."
  while :; do
    [ -n "${TAVUS_API_KEY:-}" ] && ok "TAVUS_API_KEY already in the file" || need_key TAVUS_API_KEY "5f. On your laptop open https://platform.tavus.io > API Keys > Create, then paste the key" '^[A-Za-z0-9_-]{16,}$' "the key from the Tavus API Keys page" "the avatar video"
    [ -n "${TAVUS_API_KEY:-}" ] || break
    local code; code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "x-api-key: $TAVUS_API_KEY" https://tavusapi.com/v2/replicas)
    case "$code" in 200) ok "Tavus accepts the key"; break;; 401|403) say "     ${Y}Tavus rejects that key${N} (HTTP $code); paste it again, or type Skip"; put TAVUS_API_KEY ""; unset TAVUS_API_KEY;; *) warn "Tavus not reachable from here (HTTP $code); keeping the key unverified"; break;; esac
  done

  # ---- Google ----------------------------------------------------------------------------
  say ""; say "  ${B}Google Docs${N} (transcript archival). A service account writes the documents: no OAuth client, no redirect URL, no sign-in."
  local sa_file="$STATE_DIR/google-sa.json"
  sa_email() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("client_email") and d.get("private_key"); print(d["client_email"])' "$1" 2>/dev/null; }
  if [ -n "${GOOGLE_SERVICE_ACCOUNT_FILE:-}" ] && [ -n "$(sa_email "${GOOGLE_SERVICE_ACCOUNT_FILE/#\~/$HOME}")" ]; then
    ok "service account key already in the file ($(sa_email "${GOOGLE_SERVICE_ACCOUNT_FILE/#\~/$HOME}"))"
  elif [ "$YES" = 1 ]; then
    skipped GOOGLE_SERVICE_ACCOUNT_FILE "transcript archival to Google Docs"
  else
    say "  5g. On your laptop open https://console.cloud.google.com : create or pick a project;"
    say "      APIs & Services > Library > enable the Google Drive API;"
    say "      IAM & Admin > Service Accounts > Create service account (any name, no roles) > Keys > Add key > Create new key > JSON."
    say "      Open the downloaded key file in a text editor."
    while :; do
      say "  5h. Paste the JSON key now and finish with a line containing only }  (or type a path to the file, or Skip):"
      local buf="" line first=1 path="" skip=0
      while IFS= read -rs line; do
        if [ "$first" = 1 ]; then
          first=0
          [ -n "$line" ] || break
          is_skip "$line" && { skip=1; break; }
          case "$line" in /*|~*) path="${line/#\~/$HOME}"; break;; esac
        fi
        buf+="$line"$'\n'
        [ "$line" = "}" ] && break
      done
      echo
      [ "$skip" = 0 ] || { skipped GOOGLE_SERVICE_ACCOUNT_FILE "transcript archival to Google Docs"; break; }
      if [ -n "$path" ]; then
        if [ -n "$(sa_email "$path")" ]; then cp "$path" "$sa_file" && chmod 600 "$sa_file"; else say "     ${Y}$path is not a readable service account key${N}; paste the key's content or another path, or type Skip"; continue; fi
      else
        [ -n "$buf" ] || { say "     ${Y}the key is needed${N}: paste everything from { to } (or type Skip)"; continue; }
        printf '%s' "$buf" > "$sa_file.tmp"
        if [ -n "$(sa_email "$sa_file.tmp")" ]; then mv "$sa_file.tmp" "$sa_file" && chmod 600 "$sa_file"; else rm -f "$sa_file.tmp"; say "     ${Y}that was not a service account key${N} (expected JSON with client_email and private_key); paste it again, or type Skip"; continue; fi
      fi
      local tok; tok=$(google_token "$sa_file")
      case "$tok" in ERROR*) say "     ${Y}Google rejects that key${N} (${tok#ERROR }); a deleted key or a disabled service account; paste another, or type Skip"; continue;; esac
      put GOOGLE_SERVICE_ACCOUNT_FILE "$sa_file"; GOOGLE_SERVICE_ACCOUNT_FILE="$sa_file"
      ok "Google accepts the key; service account $(sa_email "$sa_file")"; break
    done
  fi
  if [ -n "${GOOGLE_DOCS_FOLDER_ID:-}" ]; then ok "GOOGLE_DOCS_FOLDER_ID already in the file"
  elif [ "$YES" = 1 ] || [ -z "${GOOGLE_SERVICE_ACCOUNT_FILE:-}" ]; then skipped GOOGLE_DOCS_FOLDER_ID "transcript archival to Google Docs"
  else
    say "  5i. In Google Drive create a folder for the transcripts and share it with $(sa_email "${GOOGLE_SERVICE_ACCOUNT_FILE/#\~/$HOME}") as Editor."
    while :; do
      read -r -p "      Paste the folder's URL or id (Skip to leave it out): " v
      is_skip "$v" && { skipped GOOGLE_DOCS_FOLDER_ID "transcript archival to Google Docs"; break; }
      v="${v##*/folders/}"; v="${v%%[?#]*}"; v="${v// /}"
      [ -n "$v" ] || { say "     ${Y}the folder is needed${N}: open it in Google Drive and copy its URL (or type Skip)"; continue; }
      [[ "$v" =~ ^[A-Za-z0-9_-]{10,}$ ]] || { say "     ${Y}that does not look like a folder id${N} (letters, digits, - and _); try again, or type Skip"; continue; }
      local tok; tok=$(google_token "${GOOGLE_SERVICE_ACCOUNT_FILE/#\~/$HOME}")
      case "$tok" in ERROR*) warn "cannot verify the folder (${tok#ERROR }); keeping the id unverified"; put GOOGLE_DOCS_FOLDER_ID "$v"; GOOGLE_DOCS_FOLDER_ID="$v"; break;; esac
      r=$(curl -s --max-time 20 -H "Authorization: Bearer $tok" "https://www.googleapis.com/drive/v3/files/$v?supportsAllDrives=true&fields=id,name,mimeType,capabilities(canAddChildren)")
      if [ "$(printf '%s' "$r" | jq -r '.mimeType')" = "application/vnd.google-apps.folder" ] && [ "$(printf '%s' "$r" | jq -r '.capabilities.canAddChildren')" = "true" ]; then
        put GOOGLE_DOCS_FOLDER_ID "$v"; GOOGLE_DOCS_FOLDER_ID="$v"; ok "folder '$(printf '%s' "$r" | jq -r .name)' is shared with the service account as Editor"; break
      fi
      local reason; reason=$(printf '%s' "$r" | jq -r '.error.message // .error.status // "no folder with that id is visible to the service account"' | cut -c1-140)
      say "     ${Y}the service account cannot write to that folder${N}: $reason"
      say "     share the folder with $(sa_email "${GOOGLE_SERVICE_ACCOUNT_FILE/#\~/$HOME}") as Editor (and check the Drive API is enabled), then paste the URL again, or type Skip"
    done
  fi

  if [ "${PROFILE:-}" = remote ]; then
    say ""; say "  ${B}Remote model keys${N} for the endpoints of step 2."
    for k in LLM_API_KEY STT_API_KEY EMBEDDINGS_API_KEY; do need_key "$k" "$k" '.' "" "the remote model behind it" || return 1; done
  fi
  say ""; say "  keys present in $SECRETS_FILE:"; grep -v '^#' "$SECRETS_FILE" | grep -v '=$' | grep -v '^$' | sed 's/=.*/=<set>/' | sed 's/^/     /'
  note "edit the file at any time with: nano $SECRETS_FILE ; the deploy step (6) applies it. Changed a key later? scripts/setup.sh --step 5 then --step 6."
  [ "$PROJECT_EXISTS" = yes ] || { bad "project $PROJECT missing; run step 3 first"; return 1; }
  if [ "$SECRETS_IN_CLUSTER" = yes ]; then
    note "rewriting the integrations and model-key secrets in the cluster from the file (passwords are kept)"
    NAMESPACE="$PROJECT" SECRETS_FILE="$SECRETS_FILE" REFRESH=assistant-integrations,assistant-models,assistant-n8n "$ROOT/scripts/create-secrets.sh" | sed 's/^/  /' || return 1
  else
    note "creating the secrets in $PROJECT from the file (passwords are generated)"
    NAMESPACE="$PROJECT" SECRETS_FILE="$SECRETS_FILE" "$ROOT/scripts/create-secrets.sh" | sed 's/^/  /' || return 1
  fi
  local key slack=off docs=off
  in_cluster() { [ -n "$(oc get secret assistant-integrations -n "$PROJECT" -o jsonpath="{.data.$1}" 2>/dev/null)" ]; }
  for key in SLACK_BOT_TOKEN SLACK_SIGNING_SECRET TAVUS_API_KEY GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_DOCS_FOLDER_ID; do
    if in_cluster "$key"; then ok "$key in the cluster"; else warn "$key empty in the cluster (skipped; that feature stays off)"; fi
  done
  in_cluster SLACK_BOT_TOKEN && slack=on
  in_cluster GOOGLE_SERVICE_ACCOUNT_JSON && in_cluster GOOGLE_DOCS_FOLDER_ID && docs=on
  ok "step 6 deploys with Slack $slack and Google Docs $docs (integrations.*.enabled follow the keys); requests are approved in the admin portal$([ "$slack" = on ] && echo " and in Slack")"
  note "admin portal password (generated, kept on re-runs): oc extract secret/assistant-admin -n $PROJECT --keys=ADMIN_PASSWORD --to=-"
  mark 5
}
step6() {
  say "${B}Step 6: deploy with Argo CD${N}"
  [ -n "${PROFILE:-}" ] || { bad "choose the profile first (step 2)"; return 1; }
  [ -f "$STATE_DIR/values-object.json" ] || write_values_object
  local log; log=$(logfile deploy)
  say "  profile $PROFILE, values chart/values-demo-cluster.yaml plus $STATE_DIR/values-object.json, secrets from $SECRETS_FILE (log $log)"
  say "  this takes 10 to 20 minutes, mostly model downloads"
  local llm_env=()
  if [ "$PROFILE" = remote ]; then llm_env=(LLM_ENDPOINT="$REMOTE_LLM_ENDPOINT" LLM_MODEL="$REMOTE_LLM_MODEL"); elif [ -z "${LLM_NS:-}" ]; then llm_env=(LLM_ENDPOINT="http://llama-3-1-8b-instruct-predictor.$PROJECT.svc.cluster.local:8080/v1" LLM_MODEL="llama-3-1-8b-instruct"); fi
  # shellcheck disable=SC1090
  set -a; [ -f "$SECRETS_FILE" ] && . "$SECRETS_FILE"; set +a
  env PROJECT="$PROJECT" SECRETS_FILE="$SECRETS_FILE" VALUES_OBJECT_FILE="$STATE_DIR/values-object.json" LLM_NAME="$LLM_NAME" LOG_FILE="$log" RUN_TESTS=1 "${llm_env[@]}" "$ROOT/scripts/deploy-argocd.sh" && { mark 6; return 0; }
  bad "deployment reported problems; see $log, fix, and run: scripts/setup.sh --step 6"; return 1
}
step7() {
  say "${B}Step 7: n8n${N} (owner account and API key are created by the chart's n8n-setup job; this verifies the workflows)"
  [ -n "$N8N_URL" ] || { bad "n8n route not found; run step 6 first"; return 1; }
  local key waited=0; key=$(oc get secret assistant-n8n-api -n "$PROJECT" -o jsonpath='{.data.N8N_API_KEY}' 2>/dev/null | base64 -d 2>/dev/null)
  while [ -z "$key" ] && [ "$waited" -lt 600 ]; do   # the job runs during the Argo CD sync; give it 10 minutes
    local pod; pod=$(oc get pods -n "$PROJECT" -l job-name=n8n-setup --no-headers 2>/dev/null | tail -1)
    [ -n "$pod" ] || { bad "job n8n-setup has not run (no pod); oc get applications.argoproj.io voice-avatar-assistant -n openshift-gitops shows the sync state"; return 1; }
    case "$pod" in
      *CrashLoopBackOff*|*Error*)
        bad "job n8n-setup is failing: ${pod}"; oc logs -n "$PROJECT" -l job-name=n8n-setup --tail=3 2>/dev/null | sed 's/^/     /'
        note "oc logs -n $PROJECT -l job-name=n8n-setup shows the full log; after a fix, scripts/setup.sh --step 6 re-syncs and re-runs the job"; return 1 ;;
    esac
    [ $((waited % 60)) -eq 0 ] && note "waiting for job n8n-setup to create the owner account and API key (${waited}s): ${pod}"
    sleep 15; waited=$((waited + 15))
    key=$(oc get secret assistant-n8n-api -n "$PROJECT" -o jsonpath='{.data.N8N_API_KEY}' 2>/dev/null | base64 -d 2>/dev/null)
  done
  [ -n "$key" ] || { bad "job n8n-setup did not store an API key in 10 minutes; oc logs -n $PROJECT -l job-name=n8n-setup"; return 1; }
  ok "API key from secret assistant-n8n-api"
  local wf; wf=$(curl -s --max-time 20 -H "X-N8N-API-KEY: $key" "$N8N_URL/api/v1/workflows?limit=50" | jq -r '.data[]? | "\(.active) \(.name)"' 2>/dev/null)
  [ -n "$wf" ] || { bad "n8n did not answer with the key at $N8N_URL/api/v1/workflows"; return 1; }
  printf '%s\n' "$wf" | sed 's/^true /  active   /; s/^false /  INACTIVE /'
  if printf '%s\n' "$wf" | grep -q '^false'; then
    bad "inactive workflows above; oc logs deploy/n8n -n $PROJECT -c import-workflows shows why they were not published"; return 1
  fi
  ok "every workflow is active"
  note "n8n UI: $N8N_URL, login $(oc get secret assistant-n8n -n "$PROJECT" -o jsonpath='{.data.N8N_OWNER_EMAIL}' | base64 -d), password: N8N_OWNER_PASSWORD in $SECRETS_FILE (also: oc extract secret/assistant-n8n -n $PROJECT --keys=N8N_OWNER_PASSWORD --to=-)"
  mark 7
}
step8() {
  say "${B}Step 8: sample documents${N} (15 policies, procedures, an invoice and a contract)"
  [ "${PODS_NOT_READY:-1}" = 0 ] || { bad "pods not ready; run step 6 first"; return 1; }
  say "  \$ NS=$PROJECT scripts/load-sample-docs.sh"
  NS="$PROJECT" "$ROOT/scripts/load-sample-docs.sh" | sed 's/^/  /' || { bad "upload failed"; return 1; }
  say "  waiting for ingestion (Slack #assistant-ingestion reports each document)"
  local waited=0 n=0
  while [ "$waited" -lt 900 ]; do
    n=$(oc exec deploy/rag-api -n "$PROJECT" -- .venv/bin/python -c 'import urllib.request,json; print(len(json.load(urllib.request.urlopen("http://ingestion:8080/v1/documents", timeout=10))))' 2>/dev/null || echo 0)
    [ "$n" -ge 10 ] && break; sleep 20; waited=$((waited + 20)); [ $((waited % 60)) -eq 0 ] && note "$n documents indexed after ${waited}s"
  done
  [ "$n" -ge 10 ] || { bad "only $n documents indexed after 15 min; oc logs deploy/ingestion -n $PROJECT --tail=50; scripts/n8n-executions.sh"; return 1; }
  ok "$n documents indexed"; NS="$PROJECT" "$ROOT/scripts/check-index.sh" | sed 's/^/  /'; mark 8
}
step9() {
  say "${B}Step 9: verification${N}"
  local extra=(); [ -f "$STATE_DIR/values-object.json" ] && extra=(-f "$STATE_DIR/values-object.json")
  local llm_set=(); [ -f "$HOME/assistant-cluster.env" ] && { . "$HOME/assistant-cluster.env"; llm_set=(--set "models.llm.endpoint=$LLM_ENDPOINT" --set "models.llm.servedModelName=$LLM_MODEL"); }
  say "  \$ NS=$PROJECT scripts/demo-preflight.sh -f chart/values-demo-cluster.yaml ${extra[*]:-} --set global.domain=$DOMAIN ${llm_set[*]:-}"
  if NS="$PROJECT" "$ROOT/scripts/demo-preflight.sh" -f "$ROOT/chart/values-demo-cluster.yaml" "${extra[@]}" --set "global.domain=$DOMAIN" "${llm_set[@]}"; then
    mark 9; say ""; say "  ${G}Ready for the demo.${N}"; say "  frontend $FRONTEND_URL"; say "  n8n      $N8N_URL (login $(oc get secret assistant-n8n -n "$PROJECT" -o jsonpath='{.data.N8N_OWNER_EMAIL}' 2>/dev/null | base64 -d), password N8N_OWNER_PASSWORD in $SECRETS_FILE)"
    say "  admin    $FRONTEND_URL/admin (sign in with your name and the password from: oc extract secret/assistant-admin -n $PROJECT --keys=ADMIN_PASSWORD --to=-)"
    say "  Walk through docs/demo-script.md: a cited text answer, a voice session (the browser asks for the microphone), a request by voice with its Slack card, the archive button."
    [ "${SLACK_INTERACTIVITY_HOST:-}" = "n8n-$PROJECT.$DOMAIN" ] || say "  Check once: the Slack app's Interactivity request URL must be $N8N_URL/webhook/slack-interactions (step 5 asks about it)."
    say "  Next cluster: clone, scripts/setup.sh."
  else bad "preflight reported problems (docs/troubleshooting.md); fix and run: scripts/setup.sh --step 9"; return 1; fi
}

# ---------------------------------------------------------------- main ----------------------
command -v jq >/dev/null || { echo "jq is required: sudo dnf install -y jq"; exit 1; }
[ "$MODE" = status ] || ensure_login || { discover; show_status; exit 1; }
say "$(ts) discovering the cluster (a few seconds)"
discover; show_status
case "$MODE" in
status) exit 0 ;;
step) [ -n "$ONLY" ] || { echo "usage: scripts/setup.sh --step N"; exit 1; }; ensure_helm || exit 1; run_step "$ONLY"; rc=$?; discover; show_status; exit $rc ;;
esac
ensure_helm || exit 1   # the deployment test and the verification render the test pod with helm
while :; do
  [ -n "$NEXT" ] || { say "  ${G}Every step is done.${N} scripts/setup.sh --step N runs one again."; exit 0; }
  current=$NEXT
  run_step "$current"; rc=$?
  say "$(ts) refreshing the cluster state"
  QUIET_DISCOVERY=1 discover; show_status
  [ "$rc" = 0 ] || { say "  ${R}Stopped at step $current${N}: fix what is reported above, then run scripts/setup.sh again (it resumes there)."; exit 1; }
  [ "$NEXT" != "$current" ] || { say "  ${R}Step $current finished but the cluster does not show it as done${N} (see its line above); run scripts/setup.sh --step $current after fixing, or report this output."; exit 1; }
done
