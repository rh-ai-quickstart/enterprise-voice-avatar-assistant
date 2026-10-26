#!/usr/bin/env bash
# Interactive, resumable setup of the demo on a cluster, from a clone of this repository. Runs
# on the bastion host or on a Mac or Linux laptop, logged in to the cluster with oc.
#
#   scripts/setup.sh            discover the cluster, show progress, run every remaining step in order;
#                               it only stops for values it cannot know (keys, browser work) or on failure
#   scripts/setup.sh --status   discovery and progress only, changes nothing
#   scripts/setup.sh --step N   run step N (again), then stop
#   scripts/setup.sh --yes      never prompt: keys come from ~/secrets.env, missing ones are skipped
#   scripts/setup.sh --reset    forget the saved progress (the cluster is not touched)
#
# The access decides the path. A cluster-admin gets the full setup: operators, GPU sharing,
# Argo CD. A user who is only an admin of one project (a shared cluster) gets the namespace
# path: models from the cluster's Models-as-a-Service where it has them, the others served in
# the project on a GPU queue (Kueue), a TURN certificate from cert-manager, a Helm install.
# ACCESS=namespace forces that path; PROJECT picks the project (default: the current one).
#
# With cluster-admin and no GPU the script stops and says what the demo needs. To use remote
# model endpoints instead, run it with PROFILE=remote and REMOTE_LLM_ENDPOINT, REMOTE_LLM_MODEL,
# REMOTE_STT_ENDPOINT, REMOTE_EMB_ENDPOINT (and the keys in ~/secrets.env).
#
# The avatar video is chosen in step 2: tavus (Tavus cloud, its key in step 5), bey (Beyond
# Presence cloud, its key in step 5), openspline (self-hosted, OpenSpline on whole GPUs of the
# project), several (openspline,tavus,bey: the person picks one in the UI, the first preselected)
# or none (audio only); AVATAR_PROVIDER answers the question,
# and a later run can switch with --step 2 then --step 6. OpenSpline runs
# in high quality on a GPU pair when the booked quota has one free next to the models, else in
# low quality on one GPU; AVATAR_QUALITY=low or high decides instead.
#
# On another laptop nothing needs copying: after you confirm the target, setup rebuilds what only
# the first machine had from the cluster. It fills the keys missing from ~/secrets.env from the
# secrets created before, restores the avoided nodes from the release's node exclusions, and
# writes docs/handoff/private/environment.md (internal values, kept out of git).
#
# On macOS missing tools (a current bash, oc, helm, jq, ...) are installed with Homebrew,
# which the script offers to install first.
#
# Progress and discovered facts are kept in ~/.assistant-setup/state.env. Every run is logged
# to ~/.assistant-setup/logs/setup-<timestamp>.log (the bootstrap and deploy steps add their
# own logs next to it); DEBUG=1 also writes a full command trace to <log>.trace. Every step
# checks the cluster before doing anything, so work done by hand or by an earlier run is
# recognised and not repeated.

# ---------------------------------------------------------------- macOS ---------------------
# Written for bash 3.2 (what macOS ships) up to the exec: the rest of the script and the
# scripts it calls need bash 4 or later, so on a Mac they run under Homebrew's bash.
IS_MAC=0; [ "$(uname -s)" = Darwin ] && IS_MAC=1
YES=0; case " $* " in *" --yes "*|*" -y "*) YES=1;; esac
brew_env() { local b; for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do if [ -x "$b" ]; then eval "$("$b" shellenv)"; return 0; fi; done; return 1; }
ensure_brew() {  # Homebrew, installed with its official installer after asking (it asks for the Mac password once)
  if command -v brew >/dev/null 2>&1 || brew_env; then return 0; fi
  echo "  Homebrew (https://brew.sh) installs the tools this setup needs on macOS, and it is not installed."
  if [ "$YES" != 1 ]; then
    local a; printf '  Install Homebrew now with its official installer? [Y/n]: '; read -r a
    case "$a" in ""|[Yy]*) ;; *) echo "  Install Homebrew from https://brew.sh, then run scripts/setup.sh again."; return 1;; esac
  fi
  NONINTERACTIVE=$YES /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" && brew_env
}
[ "$IS_MAC" = 1 ] && brew_env
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
  [ "$IS_MAC" = 1 ] || { echo "bash 4 or later is required (this is bash $BASH_VERSION)"; exit 1; }
  if ! { command -v brew >/dev/null 2>&1 && [ -x "$(brew --prefix)/bin/bash" ]; }; then
    echo "macOS ships bash $BASH_VERSION; the setup needs bash 4 or later, installing it with Homebrew (brew install bash)"
    ensure_brew && brew install bash || { echo "could not install bash with Homebrew; run: brew install bash"; exit 1; }
  fi
  exec "$(brew --prefix)/bin/bash" "$0" "$@"
fi

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"   # tools this script installs for the user go to ~/bin
STATE_DIR="${STATE_DIR:-$HOME/.assistant-setup}"
STATE="$STATE_DIR/state.env"
SECRETS_FILE="${SECRETS_FILE:-$HOME/secrets.env}"
PROJECT_ENV="${PROJECT:-}"; ACCESS_ENV="${ACCESS:-}"; DOMAIN_ENV="${DOMAIN:-}"; AVATAR_ENV="${AVATAR_PROVIDER:-}"
PROJECT="${PROJECT:-voice-avatar-assistant}"
LLM_NAME="${LLM_NAME:-llama-32-3b-instruct}"
# namespace path: the Models-as-a-Service model names (patterns) used when the service has them
MAAS_LLM_MODEL="${MAAS_LLM_MODEL:-llama-32-3b}"; MAAS_STT_MODEL="${MAAS_STT_MODEL:-whisper.*}"; MAAS_EMB_MODEL="${MAAS_EMB_MODEL:-bge-m3}"
# namespace path: Kokoro moves to a GPU slice the queue has left over (TTS_GPU=false keeps it on CPU)
KOKORO_GPU_IMAGE="${KOKORO_GPU_IMAGE:-ghcr.io/remsky/kokoro-fastapi-gpu:v0.8.2}"
MODE=next; ONLY=""
for a in "$@"; do case "$a" in --status) MODE=status;; --yes|-y) YES=1;; --reset) MODE=reset;; --step) MODE=step;; [0-9]*) ONLY="$a";; -h|--help) sed -n 2,29p "$0"; exit 0;; esac; done
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
[ -n "$PROJECT_ENV" ] && PROJECT=$PROJECT_ENV   # the environment wins over the project saved by an earlier run
[ -n "$AVATAR_ENV" ] && AVATAR_CHOICE=$AVATAR_ENV   # likewise the avatar
# nodes found unable to start containers: deploy.sh and test-services.sh keep the full list in this
# file, which wins over the progress file; every install and test pod avoids them
[ -s "$STATE_DIR/avoid-nodes" ] && AVOID_NODES=$(tr '\n' ' ' < "$STATE_DIR/avoid-nodes" | sed 's/ *$//')

# ---------------------------------------------------------------- tools ---------------------
# have_tool <command>: usable, not just present (macOS has a git stub that opens an installer
# dialog, and LibreSSL as openssl)
have_tool() { case "$1" in git) git --version >/dev/null 2>&1;; openssl) openssl version 2>/dev/null | grep -q '^OpenSSL';; *) command -v "$1" >/dev/null 2>&1;; esac; }
# ensure_tool <command> [quiet]: present, or installed: with Homebrew on macOS (after Homebrew
# itself), with dnf (after asking) or the OpenShift mirror (oc) on Linux
ensure_tool() {
  local t=$1 quiet=${2:-} f arch
  if have_tool "$t"; then [ -n "$quiet" ] || ok "$t"; return 0; fi
  if [ "$IS_MAC" = 1 ]; then
    case "$t" in oc) f=openshift-cli;; python3) f=python;; openssl) f=openssl@3;; *) f=$t;; esac
    note "$t not found; installing it with Homebrew (brew install $f)"
    if ensure_brew && brew install "$f" >/dev/null; then
      [ "$t" = openssl ] && export PATH="$(brew --prefix openssl@3)/bin:$PATH"
      have_tool "$t" && { ok "$t (installed with Homebrew)"; return 0; }
    fi
    bad "could not install $t; run: brew install $f"; return 1
  fi
  if [ "$t" = oc ]; then
    arch=""; [ "$(uname -m)" = aarch64 ] && arch=-arm64
    note "oc not found; installing it into $HOME/bin from mirror.openshift.com"
    mkdir -p "$HOME/bin"
    if curl -fsSL --max-time 300 "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux$arch.tar.gz" | tar -xzf - -C "$HOME/bin" oc 2>/dev/null && have_tool oc; then ok "oc $(oc version --client -o json | jq -r .releaseClientVersion 2>/dev/null)"; return 0; fi
    bad "could not install oc; get it from https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/"; return 1
  fi
  if command -v dnf >/dev/null && confirm "$t is missing; install it with: sudo dnf install -y $t ?"; then
    sudo dnf install -y "$t" >/dev/null && have_tool "$t" && { ok "$t (installed with dnf)"; return 0; }
  fi
  bad "$t missing (sudo dnf install -y $t)"; return 1
}

# ---------------------------------------------------------------- login ---------------------
# Logs in when the bastion is not (or its token expired): proposes the API URL it can find,
# asks for the user (kubeadmin) and the password from the provisioning e-mail, hidden.
# helm renders the connectivity test pod (scripts/test-services.sh). The bastion usually has
# none: install it into ~/bin from the cluster's own download site, else from get.helm.sh.
ensure_helm() {
  command -v helm >/dev/null && { ok "helm $(helm version --short 2>/dev/null | cut -d+ -f1)"; return 0; }
  [ "$IS_MAC" = 1 ] && { ensure_tool helm; return; }
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
  ensure_tool oc quiet || return 1
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
  ask user "User (type token to log in with an API token instead)" "${LOGIN_USER:-kubeadmin}"
  if [ "$user" = token ]; then
    # clusters with single sign-on: the token comes from the console, <your name> > Copy login command
    ask_secret pass "API token (console: your name, top right > Copy login command > Display token)"
    [ -n "$pass" ] || { bad "a token is needed"; return 1; }
    if oc login "$api" --token="$pass" >/dev/null 2>&1 || oc login "$api" --token="$pass" --insecure-skip-tls-verify=true >/dev/null 2>&1; then
      ok "logged in to $api as $(oc whoami)"; save API_URL "$api"; save LOGIN_USER token; return 0
    fi
    bad "login failed: $(oc login "$api" --token="$pass" --insecure-skip-tls-verify=true 2>&1 | tail -1)"; return 1
  fi
  ask_secret pass "Password for $user"
  [ -n "$pass" ] || { bad "a password is needed"; return 1; }
  if oc login "$api" -u "$user" -p "$pass" >/dev/null 2>&1 || oc login "$api" -u "$user" -p "$pass" --insecure-skip-tls-verify=true >/dev/null 2>&1; then
    ok "logged in to $api as $(oc whoami)"; save API_URL "$api"; save LOGIN_USER "$user"
    oc auth can-i '*' '*' --all-namespaces >/dev/null 2>&1 && ok "cluster-admin permissions" || note "no cluster-admin: the setup stays inside one project (namespace path)"
    return 0
  fi
  bad "login failed: $(oc login "$api" -u "$user" -p "$pass" --insecure-skip-tls-verify=true 2>&1 | tail -1)"
  note "check the API URL and password in the provisioning e-mail; from the laptop the same values work with oc as well"
  return 1
}

# ---------------------------------------------------------------- discovery -----------------
# doc_counts: "<indexed> <classified>" from the ingestion service: documents with chunks, and inbox
# files that were only classified (they have none); nothing when the service cannot be asked
doc_counts() {
  oc exec deploy/rag-api -n "$PROJECT" -- .venv/bin/python -c 'import urllib.request,json; d=json.load(urllib.request.urlopen("http://ingestion:8080/v1/documents", timeout=10)); n=sum(1 for x in d if (x.get("chunks") or 0) > 0); print(n, len(d) - n)' 2>/dev/null
}
discover() {
  API=""; USER_NAME=""; OCP_VERSION=""; DOMAIN="${DOMAIN:-}"; NODE_COUNT=0; INSTANCE=""; GPUS="${GPUS:-0}"; GPU_PRODUCT=""; GPU_MEMORY=""; GPU_REPLICAS=""; GPU_ALLOC=0
  RHOAI_VERSION=""; DSC=""; KSERVE=""; OP_NFD=no; OP_GPU=no; OP_CM=no; OP_GITOPS=no; LLM_NS="${LLM_NS:-}"; LLM_READY=""; LLM_SHARE=""
  PROJECT_EXISTS=no; ARGO_READY=no; TURN_SECRET=no; SECRETS_IN_CLUSTER=no; APP_STATE=""; ISVC_TOTAL=0; ISVC_READY=0; PODS_NOT_READY=1; FRONTEND_URL=""; N8N_URL=""; DOCS_INDEXED=""; DOCS_CLASSIFIED=""
  ACCESS=""; KUBE_VERSION=""; PROJECT_ADMIN=no; KSERVE_API=no; KUEUE=no; LOCAL_QUEUES=""; GPU_OFFERS=""; MAAS_URL="${MAAS_URL:-}"; MAAS_IDS=""; HELM_STATE=""; CERT_MANAGER=no
  LOGGED_IN=0; command -v oc >/dev/null && oc whoami >/dev/null 2>&1 && LOGGED_IN=1
  [ "$LOGGED_IN" = 1 ] || return
  progress() { [ "${QUIET_DISCOVERY:-0}" = 1 ] || printf '  %s..%s %s\n' "$D" "$N" "$1"; }
  progress "cluster and access"
  API=$(oc whoami --show-server); USER_NAME=$(oc whoami)
  ACCESS=$ACCESS_ENV; [ -n "$ACCESS" ] || { oc auth can-i '*' '*' --all-namespaces >/dev/null 2>&1 && ACCESS=cluster || ACCESS=namespace; }
  if [ "$ACCESS" = namespace ]; then
    STEPS[0]="Access and prerequisites"; STEPS[2]="Project check"; STEPS[5]="Deploy with Helm"
    discover_namespace
  else
    discover_cluster
  fi
  progress "project and application"
  PROJECT_EXISTS=$(oc get namespace "$PROJECT" >/dev/null 2>&1 && echo yes || echo no)
  TURN_SECRET=$(oc get secret livekit-turn-tls -n "$PROJECT" >/dev/null 2>&1 && echo yes || echo no)
  SECRETS_IN_CLUSTER=$(oc get secret assistant-integrations -n "$PROJECT" >/dev/null 2>&1 && echo yes || echo no)
  if [ "$ACCESS" = cluster ]; then
    ARGO_READY=$([ "$(oc get deployment openshift-gitops-server -n openshift-gitops -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "1" ] && echo yes || echo no)
    APP_STATE=$(oc get applications.argoproj.io voice-avatar-assistant -n openshift-gitops -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null)
  elif command -v helm >/dev/null; then
    HELM_STATE=$(helm status assistant -n "$PROJECT" -o json 2>/dev/null | jq -r '.info.status // empty')
  fi
  ISVC_TOTAL=$(oc get isvc -n "$PROJECT" --no-headers 2>/dev/null | wc -l | tr -d ' ')
  ISVC_READY=$(oc get isvc -n "$PROJECT" -o json 2>/dev/null | jq '[.items[] | select(.status.conditions[]? | select(.type=="Ready" and .status=="True"))] | length')
  PODS_NOT_READY=$(oc get pods -n "$PROJECT" --no-headers 2>/dev/null | grep -v -E 'Running|Completed' | wc -l | tr -d ' ')
  FRONTEND_URL=$(oc get route frontend -n "$PROJECT" -o jsonpath='https://{.spec.host}' 2>/dev/null)
  N8N_URL=$(oc get route n8n -n "$PROJECT" -o jsonpath='https://{.spec.host}' 2>/dev/null)
  DOCS_INDEXED=""; DOCS_CLASSIFIED=""
  if [ "$PODS_NOT_READY" = 0 ] && [ -n "$FRONTEND_URL" ]; then
    read -r DOCS_INDEXED DOCS_CLASSIFIED < <(doc_counts)
  fi
}
discover_cluster() {
  progress "cluster and nodes"
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
}
# A project admin cannot read nodes, operators or the ingress configuration: the facts come
# from the project, the API groups, the console URL, Kueue and the Models-as-a-Service API
discover_namespace() {
  progress "project, GPU queues and Models-as-a-Service"
  KUBE_VERSION=$(oc version -o json 2>/dev/null | jq -r '.serverVersion.gitVersion // empty')
  OCP_VERSION=$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null)
  DOMAIN=$DOMAIN_ENV
  if [ -z "$DOMAIN" ]; then local c; c=$(oc whoami --show-console 2>/dev/null); c=${c#https://}; c=${c%%/*}; [ "${c#*.}" != "$c" ] && DOMAIN=${c#*.}; fi
  # the project: the one given or saved, else the current one, as long as this user administers it
  is_admin() { oc auth can-i create rolebindings -n "$1" >/dev/null 2>&1 && oc auth can-i create secrets -n "$1" >/dev/null 2>&1; }
  if [ -z "$PROJECT_ENV" ] && ! is_admin "$PROJECT"; then local cur; cur=$(oc project -q 2>/dev/null); [ -n "$cur" ] && is_admin "$cur" && PROJECT=$cur; fi
  is_admin "$PROJECT" && PROJECT_ADMIN=yes
  # captured before matching: grep -q in a pipe can end oc early, which pipefail counts as a failure
  local apis; apis=$'\n'$(oc api-resources --api-group=serving.kserve.io -o name 2>/dev/null; oc api-resources --api-group=cert-manager.io -o name 2>/dev/null)
  [[ "$apis" == *$'\n'inferenceservices.* ]] && KSERVE_API=yes
  [[ "$apis" == *$'\n'issuers.* ]] && CERT_MANAGER=yes
  # Kueue: a model pod needs a LocalQueue whose ClusterQueue has GPU quota (on a shared cluster,
  # a booking). GPU_OFFERS lines: <local queue> <resource> <units> <until, epoch or empty>
  local lq cq
  while read -r lq cq; do
    [ -n "$lq" ] || continue
    KUEUE=yes; LOCAL_QUEUES+="${LOCAL_QUEUES:+ }$lq"
    GPU_OFFERS+="$(oc get clusterqueues.kueue.x-k8s.io "$cq" -o json 2>/dev/null | jq -r --arg lq "$lq" '(.metadata.labels["rhai-tmm.dev/until"] // "") as $u | .spec.resourceGroups[].flavors[].resources[] | select(.name | test("^nvidia.com/(gpu|mig-)")) | select((.nominalQuota | tostring | tonumber? // 0) > 0) | "\($lq) \(.name) \(.nominalQuota) \($u)"')"$'\n'
  done < <(oc get localqueues.kueue.x-k8s.io -n "$PROJECT" -o json 2>/dev/null | jq -r '.items[]? | "\(.metadata.name) \(.spec.clusterQueue)"')
  [ -n "$(printf '%s' "$GPU_OFFERS" | tr -d '[:space:]')" ] || GPU_OFFERS=""
  # Models-as-a-Service (OpenShift AI): the model list answers to the user's own token
  [ -n "$MAAS_URL" ] || { [ -n "$DOMAIN" ] && MAAS_URL="https://maas.$DOMAIN"; }
  if [ -n "$MAAS_URL" ]; then
    MAAS_IDS=$(curl -s --max-time 15 -H "Authorization: Bearer $(oc whoami -t 2>/dev/null)" "$MAAS_URL/maas-api/v1/models" 2>/dev/null | jq -r '.data[]? | select(.ready != false) | .id' 2>/dev/null | tr '\n' ' ')
  fi
}
# maas_find <name pattern>: the id of the first MaaS model whose name matches; maas_endpoint <id>:
# its OpenAI-compatible base URL (ids look like publishers/<namespace>/models/<name>)
val() { printf '%s' "${!1:-}"; }   # the value of the variable named $1
maas_find() { local id; for id in $MAAS_IDS; do case "$id" in */models/*) [[ "${id##*/}" =~ ^($1)$ ]] && { echo "$id"; return 0; };; esac; done; return 1; }
maas_endpoint() { local p=${1%/models/*}; echo "$MAAS_URL/${p##*/}/${1##*/}/v1"; }
gpu_offers() { printf '%s' "$GPU_OFFERS" | grep -v '^[[:space:]]*$'; }
until_text() { [ -n "$1" ] || return 0; if [ "$IS_MAC" = 1 ]; then date -r "$1" '+%a %d %b %H:%M'; else date -d "@$1" '+%a %d %b %H:%M'; fi; }

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
  namespace)
    # per model: a remote endpoint (Models-as-a-Service or REMOTE_*), or the chart on a GPU of the
    # queue, which gets the whole card or MIG slice (hence the higher memory shares)
    jq -n --arg q "${GPU_QUEUE:-}" \
      --arg ls "$MODEL_LLM_SRC" --arg lu "${MODEL_LLM_URL:-}" --arg ln "${MODEL_LLM_NAME:-}" --arg lg "${MODEL_LLM_GPU:-}" \
      --arg ss "$MODEL_STT_SRC" --arg su "${MODEL_STT_URL:-}" --arg sn "${MODEL_STT_NAME:-}" --arg sg "${MODEL_STT_GPU:-}" \
      --arg es "$MODEL_EMB_SRC" --arg eu "${MODEL_EMB_URL:-}" --arg en "${MODEL_EMB_NAME:-}" --arg eg "${MODEL_EMB_GPU:-}" \
      --arg tg "${MODEL_TTS_GPU:-}" --arg ti "$KOKORO_GPU_IMAGE" '
      def gpu($g): if $g == "" or $g == "nvidia.com/gpu" then {} else {resources: {requests: {"nvidia.com/gpu": null, ($g): "1"}, limits: {"nvidia.com/gpu": null, ($g): "1"}}} end;
      def model($s; $u; $n; $g; $chart): if $s == "chart" then {deploy: true} + gpu($g) + $chart else {deploy: false, endpoint: $u, servedModelName: $n} end;
      {models: ({
         llm: model($ls; $lu; $ln; $lg; {servedModelName: "llama-3.1-8b-instruct"}),
         stt: model($ss; $su; $sn; $sg; {args: ["--gpu-memory-utilization=0.85", "--max-num-seqs=8"]}),
         embeddings: model($es; $eu; $en; $eg; {args: ["--runner=pooling", "--max-model-len=8192", "--gpu-memory-utilization=0.85", "--max-num-seqs=8"]}),
         guardrails: {provider: "none", deploy: false}}
       + (if $q != "" and ([$ls, $ss, $es] | index("chart")) then {podLabels: {"kueue.x-k8s.io/queue-name": $q}} else {} end)
       + (if $tg != "" then {tts: ({image: $ti, resources: {requests: {cpu: "1", memory: "2Gi", ($tg): "1"}, limits: {cpu: "4", memory: "6Gi", ($tg): "1"}}}
            + (if $q != "" then {podLabels: {"kueue.x-k8s.io/queue-name": $q}} else {} end))} else {} end)),
       n8n: {storage: "10Gi"}}' ;;
  *)
    jq -n --argjson d "$llm_deploy" '{models:{llm:{deploy:$d,args:["--max-model-len=8192","--gpu-memory-utilization=0.45","--max-num-seqs=8","--enforce-eager","--enable-auto-tool-choice","--tool-call-parser=llama3_json"]},stt:{deploy:true,args:["--gpu-memory-utilization=0.15","--max-num-seqs=8","--enforce-eager"]},embeddings:{args:["--runner=pooling","--max-model-len=8192","--gpu-memory-utilization=0.12","--max-num-seqs=8","--enforce-eager"]},guardrails:{provider:"none",deploy:false}}}' ;;
  esac | jq --arg av "${AVATAR_CHOICE:-}" --arg ag "${AVATAR_GPU:-}" --arg an "${AVATAR_GPUS:-1}" --arg al "${AVATAR_LEVEL:-low}" --arg q "${GPU_QUEUE:-}" '
    # the avatar chosen in step 2; OpenSpline gets the GPUs assigned to it (one, or a pair for
    # high quality, whose two processes need more memory)
    def gpus($g; $n): if $g == "" then {} else {resources: {requests: {"nvidia.com/gpu": null, ($g): $n}, limits: {"nvidia.com/gpu": null, ($g): $n}}} end;
    # several avatars are offered in the UI, the first preselected
    ($av | split(",") | map(select(. != "" and . != "none"))) as $list
    | (if $av == "" then . else . * {voiceAgent: {avatarProvider: ($list[0] // "none")}} end)
    | (if ($list | length) > 1 then . * {voiceAgent: {avatarProviders: $list}} else . end)
    | if ($list | index("openspline")) != null then . * {openspline: ({quality: $al} * gpus($ag; $an)
        * (if $al == "high" then {resources: {requests: {memory: "16Gi"}, limits: {memory: "64Gi"}}} else {} end)
        * (if $q != "" then {podLabels: {"kueue.x-k8s.io/queue-name": $q}} else {} end))} else . end' > "$STATE_DIR/values-object.json"
}
# Namespace profile, per model: REMOTE_<X>_ENDPOINT when given, else the Models-as-a-Service
# model named MAAS_<X>_MODEL, else the chart's own model on a GPU of a Kueue queue with quota.
# A chart model needs this much GPU memory (GB): the 8B language model 24, Whisper and BGE-M3 10.
model_title() { case "$1" in LLM) echo "language model";; STT) echo "speech-to-text";; EMB) echo "embeddings";; TTS) echo "text-to-speech";; AVATAR) echo "avatar";; esac; }
chart_model() { case "$1" in LLM) echo "Llama 3.1 8B Instruct (4-bit)";; STT) echo "Whisper large-v3-turbo";; EMB) echo "BGE-M3";; TTS) echo "Kokoro";; AVATAR) echo "the OpenSpline avatar";; esac; }
gpu_size() { case "$1" in nvidia.com/mig-*) local s=${1##*.}; s=${s%gb}; echo "$s";; *) echo 999;; esac; }   # GB; a whole card counts as plenty
# assign_gpus <queue> <kind...>: gives each kind the smallest resource of the queue that fits;
# prints "<kind> <resource>" lines, or fails when the queue's quota cannot hold them all
assign_gpus() {
  local q=$1; shift; local -a res=() units=() out=(); local kind i best min lq r u
  while read -r lq r u _; do [ "$lq" = "$q" ] && { res+=("$r"); units+=("$u"); }; done < <(gpu_offers)
  for kind in "$@"; do
    case "$kind" in LLM) min=24;; TTS) min=4;; AVATAR) min=999;; *) min=10;; esac   # the avatar: a whole card, for 25 frames a second
    best=""
    for i in "${!res[@]}"; do
      [ "${units[$i]}" -gt 0 ] && [ "$(gpu_size "${res[$i]}")" -ge "$min" ] || continue
      if [ -z "$best" ] || [ "$(gpu_size "${res[$i]}")" -lt "$(gpu_size "${res[$best]}")" ]; then best=$i; fi
    done
    [ -n "$best" ] || return 1
    units[$best]=$((units[best] - 1)); out+=("$kind ${res[$best]}")
  done
  printf '%s\n' "${out[@]}"
}

# AVATAR_CHOICE holds the avatars offered in the UI, comma-separated, the first preselected
# (openspline,tavus), or none
avatar_list() { local p; for p in ${AVATAR_CHOICE//,/ }; do [ "$p" = none ] || echo "$p"; done; }
avatar_has() { [[ ",${AVATAR_CHOICE:-}," == *",$1,"* ]]; }
avatar_default() { echo "${AVATAR_CHOICE%%,*}"; }
# avatar_valid <list> <allowed...>: the list (comma-separated) names allowed avatars, and none only alone
avatar_valid() {
  local list=$1 p; shift
  [ -n "$list" ] || return 1
  for p in ${list//,/ }; do [[ " $* " == *" $p "* ]] || return 1; done
  [[ ",$list," != *",none,"* ]] || [ "$list" = none ]
}
# choose_avatar <suggestion> <allowed...>: sets AVATAR_CHOICE (AVATAR_PROVIDER or an earlier run answers it)
choose_avatar() {
  local def=$1 a; shift
  [ -n "$AVATAR_ENV" ] && ! avatar_valid "$AVATAR_ENV" "$@" && { bad "AVATAR_PROVIDER=$AVATAR_ENV: here use some of $* (comma-separated; none alone)"; return 1; }
  [ -n "${AVATAR_CHOICE:-}" ] && avatar_valid "$AVATAR_CHOICE" "$@" && def=$AVATAR_CHOICE
  say "  avatar video, one or more of these (comma-separated: the UI then offers each, the first preselected):"
  [[ " $* " == *" tavus "* ]] && say "     tavus       Tavus cloud: its key in step 5; the free plan has 25 minutes a month and one stream"
  [[ " $* " == *" bey "* ]] && say "     bey         Beyond Presence cloud: 1080p, animates the assistant's own voice; its key in step 5, billed per minute"
  [[ " $* " == *" openspline "* ]] && say "     openspline  self-hosted: OpenSpline on a whole GPU of $PROJECT; its first start takes 10 to 20 minutes"
  say "     none        audio only"
  while :; do
    ask a "avatars ($(echo "$*" | sed 's/ /, /g'))" "$def"
    a=$(printf '%s' "$a" | tr -d ' ' | tr '[:upper:]' '[:lower:]')
    avatar_valid "$a" "$@" && { AVATAR_CHOICE=$a; return 0; }
    say "     type some of: $* (comma-separated, for example openspline,tavus; none alone)"; [ "$YES" = 1 ] && return 1
  done
}
avatar_summary() {
  local p out=() n
  for p in $(avatar_list); do
    case "$p" in openspline) out+=("OpenSpline ${AVATAR_LEVEL:-low}${AVATAR_GPU:+ on ${AVATAR_GPUS:-1} x $AVATAR_GPU}");; tavus) out+=(Tavus);; *) out+=("$p");; esac
  done
  n=${#out[@]}
  if [ "$n" = 0 ]; then echo "no avatar"; elif [ "$n" = 1 ]; then echo "avatar ${out[0]}"; else echo "avatars $(IFS=,; echo "${out[*]}" | sed 's/,/, /g') (chosen in the UI)"; fi
}

# ---------------------------------------------------------------- status --------------------
STEPS=("Cluster and prerequisites" "Deployment profile" "Cluster bootstrap" "TURN certificate" "Keys and integrations" "Deploy with Argo CD" "n8n workflows" "Sample documents" "Verification")
turn_state() {  # step 4, both paths
  if [ "$TURN_SECRET" = yes ]; then echo "done|secret livekit-turn-tls present"
  elif [ -n "${TURN_SKIPPED:-}" ]; then echo "done|TURN skipped: voice works on open networks only; scripts/setup.sh --step 4 tries again"
  elif [ "$PROJECT_EXISTS" != yes ]; then echo "todo|after the bootstrap"; else echo "todo|secret missing"; fi
}
model_summary() { local k s out=""; for k in LLM STT EMB; do s=$(val "MODEL_${k}_SRC"); case "$s" in maas|remote) out+="$(model_title $k) $s $(val "MODEL_${k}_NAME"), ";; chart) out+="$(model_title $k) in the project ($(val "MODEL_${k}_GPU")), ";; esac; done; [ -n "${MODEL_TTS_GPU:-}" ] && out+="text-to-speech on $MODEL_TTS_GPU, "; [ -n "${AVATAR_CHOICE:-}" ] && out+="$(avatar_summary), "; echo "${out%, }"; }
step_state_namespace() {
  case "$1" in
  1) if [ "$LOGGED_IN" != 1 ]; then echo "attention|not logged in to a cluster"; elif [ "$PROJECT_ADMIN" != yes ]; then echo "attention|$USER_NAME is not an admin of project $PROJECT: run with PROJECT=<a project you administer>";
     elif [ -n "${STEP_1_DONE:-}" ]; then echo "done|project $PROJECT (admin), Kubernetes $KUBE_VERSION, ${LOCAL_QUEUES:+Kueue queues $LOCAL_QUEUES, }$(echo $MAAS_IDS | wc -w | tr -d ' ') Models-as-a-Service model(s)"; else echo "todo|project $PROJECT (admin); tools and permissions to check"; fi ;;
  2) if [ "${PROFILE:-}" = namespace ]; then echo "done|namespace: $(model_summary)${GPU_QUEUE:+, queue $GPU_QUEUE}"; else echo "todo|namespace profile: Models-as-a-Service where it has the model, else a GPU queue"; fi ;;
  3) if [ "$PROJECT_ADMIN" = yes ] && [ -n "${STEP_3_DONE:-}" ]; then echo "done|project $PROJECT ready${GPU_QUEUE:+, GPU queue $GPU_QUEUE}"; else echo "todo|project $PROJECT, permissions and GPU queue to check"; fi ;;
  4) turn_state ;;
  6) if [ "$HELM_STATE" = deployed ] && [ "${PODS_NOT_READY:-1}" = 0 ]; then echo "done|Helm release assistant deployed, ${ISVC_READY:-0}/${ISVC_TOTAL:-0} models Ready"; elif [ -n "$HELM_STATE" ]; then echo "attention|Helm release $HELM_STATE, ${PODS_NOT_READY} pod(s) not ready"; else echo "todo|not deployed"; fi ;;
  *) ACCESS=cluster step_state "$1" ;;
  esac
}
step_state() {  # prints done|todo|attention and a detail
  [ "$ACCESS" = namespace ] && { step_state_namespace "$1"; return; }
  case "$1" in
  1) if [ "$LOGGED_IN" != 1 ]; then echo "attention|not logged in to a cluster"; elif [ -z "$RHOAI_VERSION" ]; then echo "attention|OpenShift AI not found";
     elif [[ "$RHOAI_VERSION" == 2.* ]]; then echo "attention|OpenShift AI $RHOAI_VERSION; 3.x is required"; elif [ "$KSERVE" != Managed ]; then echo "todo|KServe is ${KSERVE:-unset} (bootstrap fixes it)";
     else echo "done|OpenShift $OCP_VERSION, OpenShift AI $RHOAI_VERSION, $NODE_COUNT node(s) $INSTANCE, ${GPUS:-0} GPU(s) ${GPU_PRODUCT:+($GPU_PRODUCT)}, KServe Managed"; fi ;;
  2) if [ -n "${PROFILE:-}" ]; then echo "done|$PROFILE${LLM_NS:+; language model $LLM_NS/$LLM_NAME}${AVATAR_CHOICE:+; $(avatar_summary)}"; else echo "todo|suggested: $(suggest_profile)"; fi ;;
  3) if [ "$PROJECT_EXISTS" = yes ] && [ "$ARGO_READY" = yes ] && [ -n "${STEP_3_DONE:-}" ]; then echo "done|project, Argo CD, GPU sharing (${GPU_ALLOC:-?} schedulable GPUs)";
     elif [ "$PROJECT_EXISTS" = yes ] && [ "$ARGO_READY" = yes ]; then echo "todo|project and Argo CD exist; run to verify GPU sharing and the model share"; else echo "todo|project ${PROJECT_EXISTS}, Argo CD ${ARGO_READY}"; fi ;;
  4) turn_state ;;
  5) if [ "$SECRETS_IN_CLUSTER" = yes ] && [ -n "${STEP_5_DONE:-}" ]; then echo "done|$SECRETS_FILE and the cluster secrets"; elif [ "$SECRETS_IN_CLUSTER" = yes ]; then echo "todo|cluster secrets exist; run to review the keys"; elif [ -f "$SECRETS_FILE" ]; then echo "todo|$SECRETS_FILE exists, not yet applied"; else echo "todo|no $SECRETS_FILE yet"; fi ;;
  6) if [ "$APP_STATE" = "Synced/Healthy" ] && [ "${PODS_NOT_READY:-1}" = 0 ]; then echo "done|application Synced/Healthy, ${ISVC_READY:-0}/${ISVC_TOTAL:-0} models Ready"; elif [ -n "$APP_STATE" ]; then echo "attention|application $APP_STATE, ${PODS_NOT_READY} pod(s) not ready"; else echo "todo|not deployed"; fi ;;
  7) if [ -n "${STEP_7_DONE:-}" ]; then echo "done|owner account, API key, workflows active"; elif [ -n "$N8N_URL" ]; then echo "todo|$N8N_URL"; else echo "todo|after the deployment"; fi ;;
  8) if [ -n "$DOCS_INDEXED" ] && [ "$DOCS_INDEXED" -ge 10 ]; then echo "done|$DOCS_INDEXED documents indexed, ${DOCS_CLASSIFIED:-0} inbox files classified"; elif [ -n "$DOCS_INDEXED" ]; then echo "todo|$DOCS_INDEXED documents indexed, ${DOCS_CLASSIFIED:-0} inbox files classified"; else echo "todo|after the deployment"; fi ;;
  9) if [ -n "${STEP_9_DONE:-}" ]; then echo "done|preflight passed $STEP_9_DONE"; else echo "todo|preflight not run"; fi ;;
  esac
}
show_status() {
  say ""; say "${B}Enterprise voice avatar assistant: setup${N}"
  if [ "$LOGGED_IN" = 1 ]; then say "  cluster $API as $USER_NAME, apps domain ${DOMAIN:-unknown}"; else say "  ${R}not logged in${N}: the script asks for the API URL, user and password (or a token) when run"; fi
  if [ "$ACCESS" = namespace ]; then say "  access: admin of project $PROJECT only, so the namespace path (Helm, no cluster changes)"; elif [ "$ACCESS" = cluster ]; then say "  access: cluster-admin, so the full path (operators, GPU sharing, Argo CD)"; fi
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
  for t in oc jq git openssl curl python3; do ensure_tool "$t" || return 1; done
  ensure_helm || return 1
  [ "$ACCESS" = namespace ] && { step1_namespace; return; }
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
step1_namespace() {
  ok "namespace access on $API as $USER_NAME: no cluster-admin, so the setup stays inside one project and installs with Helm"
  [ -n "$DOMAIN" ] && ok "apps domain $DOMAIN (from the console URL)" || { bad "could not work out the apps domain; run with DOMAIN=apps.<cluster domain>"; return 1; }
  [ "$PROJECT_ADMIN" = yes ] && ok "project $PROJECT: $USER_NAME is an admin" || { bad "$USER_NAME is not an admin of project $PROJECT; run with PROJECT=<a project you administer> (oc projects lists them)"; return 1; }
  local r missing=()
  for r in deployments services configmaps secrets persistentvolumeclaims serviceaccounts roles rolebindings jobs pods routes routes/custom-host; do
    oc auth can-i create "$r" -n "$PROJECT" >/dev/null 2>&1 || missing+=("$r")
  done
  [ "${#missing[@]}" = 0 ] && ok "can create every kind the chart needs in $PROJECT" || { bad "missing permissions in $PROJECT: create ${missing[*]}"; return 1; }
  if [ -n "$KUBE_VERSION" ]; then ok "Kubernetes $KUBE_VERSION${OCP_VERSION:+, OpenShift $OCP_VERSION}"; fi
  if [ "$KSERVE_API" = yes ] && oc auth can-i create inferenceservices -n "$PROJECT" >/dev/null 2>&1; then ok "KServe: models can be served in the project"; else note "no KServe access in the project: every model has to come from Models-as-a-Service or a remote endpoint"; fi
  if [ "$KUEUE" = yes ]; then
    ok "Kueue manages $PROJECT; local queues: $LOCAL_QUEUES"
    local lq res units until
    if [ -n "$GPU_OFFERS" ]; then while read -r lq res units until; do ok "GPU quota on queue $lq: $units x $res${until:+ (booked until $(until_text "$until"))}"; done < <(gpu_offers)
    else note "no queue of $PROJECT has GPU quota: models that need a GPU require a booking (or a remote endpoint)"; fi
  fi
  if [ -n "$MAAS_IDS" ]; then ok "Models-as-a-Service at $MAAS_URL: $(for i in $MAAS_IDS; do printf '%s ' "${i##*/}"; done)"; else note "no Models-as-a-Service found${MAAS_URL:+ at $MAAS_URL} (MAAS_URL=<url> points to one)"; fi
  local sc; sc=$(oc get storageclass -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{end}' 2>/dev/null)
  [ -n "$sc" ] && ok "default StorageClass $sc" || warn "no default StorageClass visible to this user; the volumes rely on the cluster having one"
  [ "$CERT_MANAGER" = yes ] && oc auth can-i create issuers.cert-manager.io -n "$PROJECT" >/dev/null 2>&1 && ok "cert-manager: the TURN certificate can be requested in the project (step 4)" || note "no cert-manager access in the project: step 4 skips TURN (voice then works on open networks only)"
  save DOMAIN "$DOMAIN"; save PROJECT "$PROJECT"; mark 1
}
step2() {
  [ "$ACCESS" = namespace ] && { step2_namespace; return; }
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
  if [ "$PROFILE" = remote ]; then choose_avatar tavus tavus bey none || return 1
  else
    choose_avatar tavus tavus bey openspline none || return 1
    avatar_has openspline && warn "OpenSpline takes one time-sliced share of a GPU, next to the models: on a busy card the video can fall below 25 frames a second"
  fi
  save AVATAR_CHOICE "$AVATAR_CHOICE"; unsave AVATAR_GPU; unsave AVATAR_GPUS; unsave AVATAR_LEVEL
  save PROFILE "$PROFILE"; write_values_object; ok "profile $PROFILE saved ($STATE_DIR/values-object.json), $(avatar_summary)"; mark 2
}
step2_namespace() {
  say "${B}Step 2: deployment profile${N} (each model from Models-as-a-Service when it has it, else served in $PROJECT on a GPU queue)"
  unsave PROFILE   # a choice that stops half-way leaves the step to do
  local k id v q="" lines="" lq need=(); local -A src=() url=() name=() gpu=()
  for k in LLM STT EMB; do
    v="REMOTE_${k}_ENDPOINT"
    if [ -n "${!v:-}" ]; then
      src[$k]=remote; url[$k]=${!v}; v="REMOTE_${k}_MODEL"; name[$k]=${!v:-}
      [ -n "${name[$k]}" ] || { bad "REMOTE_${k}_ENDPOINT needs REMOTE_${k}_MODEL (the model id)"; return 1; }
    elif v="MAAS_${k}_MODEL"; id=$(maas_find "${!v}"); then
      src[$k]=maas; url[$k]=$(maas_endpoint "$id"); name[$k]=${id##*/}
    else
      src[$k]=chart; need+=("$k")
    fi
  done
  # the avatar: OpenSpline is suggested when a queue has a whole GPU for it next to the models
  local fits=""; local -a kinds=()
  if [ "$KUEUE" = yes ] && [ -z "${GPU_RESOURCE:-}" ]; then
    for lq in ${KUEUE_QUEUE:-$(gpu_offers | awk '{print $1}' | sort -u)}; do assign_gpus "$lq" AVATAR ${need[@]+"${need[@]}"} >/dev/null && { fits=$lq; break; }; done
  fi
  choose_avatar "$([ -n "$fits" ] && echo openspline,tavus || echo tavus)" tavus bey openspline none || return 1
  # OpenSpline's quality: high (the Pro model) needs a GPU pair to stay real time, low one GPU
  if avatar_has openspline; then
    case "${AVATAR_QUALITY:-auto}" in
      low)  kinds+=(AVATAR) ;;
      high) kinds+=(AVATAR AVATAR) ;;
      auto)
        # high when a queue has the pair next to the models, unless it would push Kokoro off the
        # GPU it gets with low quality (seconds per sentence on CPU cost more than the detail gains)
        kinds+=(AVATAR)
        if [ "$KUEUE" = yes ] && [ -z "${GPU_RESOURCE:-}" ]; then
          local -a tts=(); [ "${TTS_GPU:-auto}" != false ] && tts=(TTS)
          for lq in ${KUEUE_QUEUE:-$(gpu_offers | awk '{print $1}' | sort -u)}; do
            if assign_gpus "$lq" AVATAR AVATAR ${need[@]+"${need[@]}"} ${tts[@]+"${tts[@]}"} >/dev/null \
              || { ! assign_gpus "$lq" AVATAR ${need[@]+"${need[@]}"} ${tts[@]+"${tts[@]}"} >/dev/null \
                   && assign_gpus "$lq" AVATAR AVATAR ${need[@]+"${need[@]}"} >/dev/null; }; then
              kinds+=(AVATAR); break
            fi
          done
        fi ;;
      *) bad "AVATAR_QUALITY=$AVATAR_QUALITY: use low, high or auto"; return 1 ;;
    esac
  fi
  kinds+=(${need[@]+"${need[@]}"})
  if [ "${#kinds[@]}" -gt 0 ]; then
    [ "${#need[@]}" -eq 0 ] || [ "$KSERVE_API" = yes ] || { bad "$(for k in "${need[@]}"; do printf '%s, ' "$(model_title "$k")"; done | sed 's/, $//'): not on Models-as-a-Service, and the project cannot serve models (no KServe); set REMOTE_<LLM|STT|EMB>_ENDPOINT and _MODEL"; return 1; }
    if [ -n "${KUEUE_QUEUE:-}" ] && [ -n "${GPU_RESOURCE:-}" ]; then
      q=$KUEUE_QUEUE; lines=$(for k in "${kinds[@]}"; do echo "$k $GPU_RESOURCE"; done)   # e.g. a queue that borrows without quota of its own
    elif [ "$KUEUE" = yes ]; then
      for lq in ${KUEUE_QUEUE:-$(gpu_offers | awk '{print $1}' | sort -u)}; do lines=$(assign_gpus "$lq" "${kinds[@]}") && { q=$lq; break; }; done
    else
      lines=$(for k in "${kinds[@]}"; do echo "$k ${GPU_RESOURCE:-nvidia.com/gpu}"; done)   # no Kueue: plain GPU requests
      warn "no Kueue in $PROJECT: the models request ${GPU_RESOURCE:-nvidia.com/gpu} directly; a project admin cannot see whether the nodes have free GPUs"
    fi
    if [ -z "$lines" ]; then
      bad "not enough GPU quota for $(for k in "${kinds[@]}"; do printf '%s, ' "$(chart_model "$k")"; done | sed 's/, $//')$([ "${#need[@]}" -gt 0 ] && echo ", which Models-as-a-Service does not offer")."
      say "     required: one GPU or MIG slice per model, 10 GB or more for Whisper and BGE-M3 (MIG 1g.18gb is enough), 24 GB or more for the language model$(avatar_has openspline && echo ", a whole GPU for the OpenSpline avatar (two for AVATAR_QUALITY=high)")"
      say "     available: $( [ -n "$GPU_OFFERS" ] && gpu_offers | awk '{printf "%s x %s on queue %s; ", $3, $2, $1}' || echo "no queue of $PROJECT with GPU quota")"
      say "     options: book GPUs for $PROJECT (on a shared cluster: the console's GPU booking page), then run scripts/setup.sh again;"
      say "              or a remote endpoint per model: REMOTE_STT_ENDPOINT=... REMOTE_STT_MODEL=... (LLM, EMB likewise), keys in $SECRETS_FILE"
      avatar_has openspline && say "              or another avatar: AVATAR_PROVIDER=tavus (or none) scripts/setup.sh --step 2$([ "${AVATAR_QUALITY:-}" = high ] && echo "; or AVATAR_QUALITY=low (one GPU)")"
      return 1
    fi
    while read -r k v; do gpu[$k]=$v; done <<< "$lines"
  fi
  # Kokoro on a GPU slice the queue has left over: a sentence in a fraction of a second instead of
  # seconds on CPU. Only when it fits next to the models above (TTS_GPU=false keeps it on CPU).
  if [ "${TTS_GPU:-auto}" != false ] && [ "$KUEUE" = yes ] && [ -n "$GPU_OFFERS" ] && [ -z "${GPU_RESOURCE:-}" ]; then
    local with_tts="" tq
    for tq in ${q:-${KUEUE_QUEUE:-$(gpu_offers | awk '{print $1}' | sort -u)}}; do
      with_tts=$(assign_gpus "$tq" ${kinds[@]+"${kinds[@]}"} TTS) && { q=$tq; break; }
      with_tts=""
    done
    [ -n "$with_tts" ] && { lines=$with_tts; while read -r k v; do gpu[$k]=$v; done <<< "$with_tts"; }
  fi
  local avatar_gpus; avatar_gpus=$(grep -c '^AVATAR ' <<< "${lines:-}" || true)
  save GPU_QUEUE "$q"
  for k in LLM STT EMB; do
    save "MODEL_${k}_SRC" "${src[$k]}"; save "MODEL_${k}_URL" "${url[$k]:-}"; save "MODEL_${k}_NAME" "${name[$k]:-}"; save "MODEL_${k}_GPU" "${gpu[$k]:-}"
    case "${src[$k]}" in
      maas)   ok "$(model_title $k): Models-as-a-Service ${name[$k]} (${url[$k]})" ;;
      remote) ok "$(model_title $k): remote endpoint ${name[$k]} (${url[$k]})" ;;
      chart)  ok "$(model_title $k): $(chart_model $k) served in $PROJECT on 1 x ${gpu[$k]}${q:+ from queue $q}" ;;
    esac
  done
  save MODEL_TTS_GPU "${gpu[TTS]:-}"
  if [ -n "${gpu[TTS]:-}" ]; then ok "text-to-speech: Kokoro on 1 x ${gpu[TTS]}${q:+ from queue $q} ($KOKORO_GPU_IMAGE)"
  else ok "text-to-speech: Kokoro on CPU, with one synthesis thread per CPU of its limit (no GPU slice left over; TTS_GPU=false keeps it there)"; fi
  local level=low; [ "${avatar_gpus:-0}" -ge 2 ] && level=high
  save AVATAR_CHOICE "$AVATAR_CHOICE"; save AVATAR_GPU "${gpu[AVATAR]:-}"; save AVATAR_GPUS "${avatar_gpus:-0}"; save AVATAR_LEVEL "$level"
  local av
  for av in $(avatar_list); do
    case "$av" in
      openspline) ok "avatar: OpenSpline in $level quality, self-hosted on $avatar_gpus x ${gpu[AVATAR]}${q:+ from queue $q} ($([ "$level" = high ] && echo "the Pro model on a GPU pair" || echo "the Lite model; two whole GPUs give high quality"); its first start installs it and downloads its weights, 10 to 20 minutes)" ;;
      tavus)      ok "avatar: Tavus (its key in step 5)" ;;
      bey)        ok "avatar: Beyond Presence (its key in step 5; the faces are voiceAgent.beyFaces)" ;;
    esac
  done
  if [ -z "$(avatar_list)" ]; then ok "avatar: none (voice sessions are audio-only)"
  elif [ "$(avatar_list | wc -l)" -gt 1 ]; then ok "the UI offers these avatars, $(avatar_default) preselected; the person picks one before a voice session"; fi
  PROFILE=namespace; save PROFILE namespace; write_values_object; ok "profile namespace saved ($STATE_DIR/values-object.json); no guardrail model"; mark 2
}
step3() {
  [ "$ACCESS" = namespace ] && { step3_namespace; return; }
  say "${B}Step 3: cluster bootstrap${N} (operators, KServe, GPU sharing, model share, Argo CD, project)"
  [ -n "${PROFILE:-}" ] || { bad "choose the profile first (step 2)"; return 1; }
  local log; log=$(logfile bootstrap); local slices; slices=$(profile_slices "$PROFILE")
  local frac=0.55
  say "  running scripts/bootstrap-cluster.sh with GPU_SLICES=$slices LLM_GPU_FRACTION=$frac (log $log)"
  PROJECT="$PROJECT" GPU_SLICES="$slices" LLM_NAME="${LLM_NS:+$LLM_NAME}" LLM_GPU_FRACTION="$frac" LOG_FILE="$log" "$ROOT/scripts/bootstrap-cluster.sh" && { mark 3; return 0; }
  bad "bootstrap reported problems; see $log, fix, and run: scripts/setup.sh --step 3"; return 1
}
step3_namespace() {
  say "${B}Step 3: project check${N} (namespace access: nothing is installed or changed outside $PROJECT)"
  [ "$PROJECT_ADMIN" = yes ] || { bad "$USER_NAME is not an admin of project $PROJECT; run with PROJECT=<a project you administer>"; return 1; }
  ok "project $PROJECT exists and $USER_NAME administers it"
  if [ -n "${GPU_QUEUE:-}" ] && [ -z "${GPU_RESOURCE:-}" ]; then
    local lq res units until found=0
    while read -r lq res units until; do
      [ "$lq" = "$GPU_QUEUE" ] || continue; found=1
      ok "queue $GPU_QUEUE: $units x $res${until:+, booked until $(until_text "$until")}"
      [ -n "$until" ] && [ "$until" -lt $(( $(date +%s) + 7200 )) ] && warn "the booking shown by the queue ends within two hours ($(until_text "$until")); the models on it stop when it ends, so check or extend it on the booking page"
    done < <(gpu_offers)
    [ "$found" = 1 ] || { bad "queue $GPU_QUEUE has no GPU quota any more (did the booking end?); book again, then run scripts/setup.sh --step 2"; return 1; }
  elif [ -n "${GPU_QUEUE:-}" ]; then note "queue $GPU_QUEUE with $GPU_RESOURCE as given (KUEUE_QUEUE, GPU_RESOURCE)"
  else ok "no model needs a GPU in the project"; fi
  mark 3
}
step4() {
  say "${B}Step 4: TURN certificate${N} (voice through corporate networks needs TURN over TLS with a trusted certificate)"
  [ "$PROJECT_EXISTS" = yes ] || { bad "project missing; run step 3 first"; return 1; }
  local host="livekit-turn-${PROJECT}.${DOMAIN}" secret=livekit-turn-tls
  [ "$ACCESS" = namespace ] && { step4_namespace "$host" "$secret"; return; }
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
  [ -n "${ACME_EMAIL:-}" ] || { warn "TURN skipped: voice works on open networks only. Rerun later: scripts/setup.sh --step 4"; save TURN_SKIPPED 1; mark 4; return 0; }
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
  ok "certificate Ready for $host (renews itself)"; unsave TURN_SKIPPED; mark 4
}
# With project access the cluster's wildcard certificate is out of reach: a namespaced
# cert-manager Issuer requests one for the TURN host from Let's Encrypt (HTTP-01, before the
# chart's passthrough route claims the host). When that does not work, the setup continues
# without TURN and a later run picks the certificate up.
step4_namespace() {
  local host=$1 secret=$2
  turn_skip() { warn "TURN skipped: $1. Voice works on open networks only; scripts/setup.sh --step 4 tries again, --step 6 then turns TURN on"; save TURN_SKIPPED 1; mark 4; }
  cert_ready() { [ "$(oc get certificate "$secret" -n "$PROJECT" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = "True" ]; }
  if [ "$TURN_SECRET" = yes ] && { cert_ready || ! oc get certificate "$secret" -n "$PROJECT" >/dev/null 2>&1; }; then ok "secret $PROJECT/$secret present"; unsave TURN_SKIPPED; mark 4; return 0; fi
  [ "$CERT_MANAGER" = yes ] && oc auth can-i create issuers.cert-manager.io -n "$PROJECT" >/dev/null 2>&1 && oc auth can-i create certificates.cert-manager.io -n "$PROJECT" >/dev/null 2>&1 \
    || { turn_skip "no cert-manager access in $PROJECT"; return 0; }
  note "the cluster's wildcard certificate is not readable with project access; cert-manager requests one for $host from Let's Encrypt (the apps domain must be reachable from the internet)"
  local def=""; [[ "$USER_NAME" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] && def=$USER_NAME
  local email=${ACME_EMAIL:-$def}
  [ "$YES" = 1 ] || ask email "E-mail for Let's Encrypt (expiry notices; empty to skip TURN for now)" "$email"
  [ -n "$email" ] || { turn_skip "no e-mail for Let's Encrypt"; return 0; }
  save ACME_EMAIL "$email"
  # the ACME solver pod that answers the challenge keeps off the nodes known to be broken
  local solver_pod=""
  [ -n "${AVOID_NODES:-}" ] && solver_pod="            podTemplate:
              spec:
                affinity:
                  nodeAffinity:
                    requiredDuringSchedulingIgnoredDuringExecution:
                      nodeSelectorTerms:
                        - matchFields:
                            - key: metadata.name
                              operator: NotIn
                              values: [$(echo $AVOID_NODES | tr ' ' ',')]"
  oc apply -f - <<YAML >/dev/null || { turn_skip "the Issuer or Certificate was refused"; return 0; }
apiVersion: cert-manager.io/v1
kind: Issuer
metadata:
  name: letsencrypt-http01
  namespace: ${PROJECT}
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ${email}
    privateKeySecretRef:
      name: letsencrypt-http01-account
    solvers:
      - http01:
          ingress:
            ingressClassName: openshift-default
${solver_pod}
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
    kind: Issuer
YAML
  ok "Issuer letsencrypt-http01 and Certificate $PROJECT/$secret applied; waiting for the ACME challenge"
  local waited=0
  until cert_ready; do
    if [ "$waited" -ge 600 ]; then
      note "challenge state: $(oc get challenges.acme.cert-manager.io -n "$PROJECT" -o jsonpath='{range .items[*]}{.status.state} {.status.reason}{"\n"}{end}' 2>/dev/null | head -1 | cut -c1-200)"
      turn_skip "the certificate is not Ready after 10 minutes (oc describe certificate $secret -n $PROJECT)"; return 0
    fi
    sleep 15; waited=$((waited + 15)); [ $((waited % 60)) -eq 0 ] && note "waiting for the ACME challenge (${waited}s)"
  done
  ok "certificate Ready for $host (renews itself)"; unsave TURN_SKIPPED; mark 4
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
# Models-as-a-Service: one API key for the models served there, created with the user's own
# login and bound to a subscription that includes all of them (a user with several
# subscriptions would otherwise have to name one in a header on every call, which the services
# cannot do). Runs inside step 5, which defines put().
maas_ok() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Authorization: Bearer $1" "$2/models")" = 200 ]; }
ensure_maas_key() {
  local k url="" names=() tok sub body r key i v
  for k in LLM STT EMB; do [ "$(val "MODEL_${k}_SRC")" = maas ] && { url=${url:-$(val "MODEL_${k}_URL")}; names+=("$(val "MODEL_${k}_NAME")"); }; done
  [ -n "$url" ] || return 0
  say ""; say "  ${B}Models-as-a-Service key${N} for ${names[*]} (created with your login; nothing to do in a browser)"
  if [ -n "${MAAS_API_KEY:-}" ] && maas_ok "$MAAS_API_KEY" "$url"; then ok "MAAS_API_KEY in $SECRETS_FILE works"
  else
    [ -n "${MAAS_API_KEY:-}" ] && warn "MAAS_API_KEY in $SECRETS_FILE is refused; creating a new key"
    tok=$(oc whoami -t 2>/dev/null); [ -n "$tok" ] || { bad "this login has no token to create a key with (oc whoami -t); log in with a token, or put MAAS_API_KEY in $SECRETS_FILE"; return 1; }
    sub=${MAAS_SUBSCRIPTION:-$(curl -s --max-time 20 -H "Authorization: Bearer $tok" "$MAAS_URL/maas-api/v1/subscriptions" 2>/dev/null \
      | jq -r --argjson need "$(printf '%s\n' "${names[@]}" | jq -R . | jq -sc .)" '[.[]? | select([.model_refs[]?.name] as $m | all($need[]; . as $n | $m | index($n)))] | sort_by((.subscription_id_header == "system-authenticated"), -(.priority // 0)) | .[0].subscription_id_header // empty' 2>/dev/null)}
    body=$(jq -nc --arg n "voice-avatar-assistant-$PROJECT" --arg s "$sub" '{name: $n, description: "Enterprise voice avatar assistant, created by scripts/setup.sh"} + (if $s != "" then {subscription: $s} else {} end)')
    r=$(curl -s --max-time 30 -X POST -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' -d "$body" "$MAAS_URL/maas-api/v1/api-keys")
    key=$(printf '%s' "$r" | jq -r '.key // empty' 2>/dev/null)
    [ -n "$key" ] || { bad "Models-as-a-Service did not create a key: $(printf '%s' "$r" | jq -r '.error // .message // .' 2>/dev/null | head -1 | cut -c1-200)"; return 1; }
    put MAAS_API_KEY "$key"; MAAS_API_KEY=$key
    for i in 1 2 3 4 5 6; do maas_ok "$key" "$url" && break; sleep 5; done
    maas_ok "$key" "$url" || { bad "the new key is refused by $url/models; check the subscription (MAAS_SUBSCRIPTION=<name>) and run --step 5 again"; return 1; }
    ok "key created${sub:+ for subscription $sub} (expires $(printf '%s' "$r" | jq -r '.expiresAt // "never"')); saved to $SECRETS_FILE as MAAS_API_KEY"
  fi
  for k in LLM STT EMB; do
    [ "$(val "MODEL_${k}_SRC")" = maas ] || continue
    [ "$k" = EMB ] && v=EMBEDDINGS_API_KEY || v=${k}_API_KEY
    put "$v" "$MAAS_API_KEY"; printf -v "$v" '%s' "$MAAS_API_KEY"; export "$v"
  done
  ok "the models served by Models-as-a-Service use that key"
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
  local slack_skipped=0
  if [ -n "${SLACK_BOT_TOKEN:-}" ]; then ok "SLACK_BOT_TOKEN already in the file"; else
    say "  5a. On your laptop open https://api.slack.com/apps > Create New App > From a manifest > choose the workspace,"
    say "      paste the manifest below (also saved as $manifest) and create the app:"
    sed 's/^/        /' "$manifest"
    pause "" || { skipped SLACK_BOT_TOKEN "Slack"; slack_skipped=1; }
  fi
  while [ "$slack_skipped" = 0 ]; do
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
  local want_tavus=0; { [ -z "${AVATAR_CHOICE:-}" ] || avatar_has tavus; } && want_tavus=1
  if [ "$want_tavus" = 0 ]; then
    if avatar_has openspline; then ok "not needed: the avatar is OpenSpline, self-hosted (chosen in step 2)"; else ok "not needed: no avatar (chosen in step 2)"; fi
  fi
  while [ "$want_tavus" = 1 ]; do
    [ -n "${TAVUS_API_KEY:-}" ] && ok "TAVUS_API_KEY already in the file" || need_key TAVUS_API_KEY "5f. On your laptop open https://platform.tavus.io > API Keys > Create, then paste the key" '^[A-Za-z0-9_-]{16,}$' "the key from the Tavus API Keys page" "the avatar video (voice sessions run audio-only)"
    [ -n "${TAVUS_API_KEY:-}" ] || break
    local code; code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "x-api-key: $TAVUS_API_KEY" https://tavusapi.com/v2/replicas)
    case "$code" in 200) ok "Tavus accepts the key"; break;; 401|403) say "     ${Y}Tavus rejects that key${N} (HTTP $code); paste it again, or type Skip"; put TAVUS_API_KEY ""; unset TAVUS_API_KEY;; *) warn "Tavus not reachable from here (HTTP $code); keeping the key unverified"; break;; esac
  done

  # ---- Beyond Presence --------------------------------------------------------------------
  if avatar_has bey; then
    say ""; say "  ${B}Beyond Presence${N} (avatar video at 1080p, from the assistant's own voice). Billed per minute of video."
    while :; do
      [ -n "${BEY_API_KEY:-}" ] && ok "BEY_API_KEY already in the file" || need_key BEY_API_KEY "5i. On your laptop open https://bey.studio > API Keys > Create, then paste the key" '^[^[:space:]]{16,}$' "the key from the Beyond Presence Studio" "the Beyond Presence avatar (left out of the UI)"
      [ -n "${BEY_API_KEY:-}" ] || break
      local bcode; bcode=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "x-api-key: $BEY_API_KEY" https://api.bey.dev/v1/auth/verify)
      case "$bcode" in 2??) ok "Beyond Presence accepts the key"; break;; 401|403) say "     ${Y}Beyond Presence rejects that key${N} (HTTP $bcode); paste it again, or type Skip"; put BEY_API_KEY ""; unset BEY_API_KEY;; *) warn "Beyond Presence not reachable from here (HTTP $bcode); keeping the key unverified"; break;; esac
    done
  fi

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
  if [ "${PROFILE:-}" = namespace ]; then
    ensure_maas_key || return 1
    for k in LLM STT EMB; do
      [ "$(val "MODEL_${k}_SRC")" = remote ] || continue
      [ "$k" = EMB ] && v=EMBEDDINGS_API_KEY || v=${k}_API_KEY
      [ -n "${!v:-}" ] && ok "$v already in the file" || need_key "$v" "$v for the remote $(model_title $k) endpoint (none if it needs no key)" '.' "" "the remote $(model_title $k)"
    done
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
  local key slack=off docs=off avatar=off keys=(SLACK_BOT_TOKEN SLACK_SIGNING_SECRET GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_DOCS_FOLDER_ID)
  in_cluster() { [ -n "$(oc get secret assistant-integrations -n "$PROJECT" -o jsonpath="{.data.$1}" 2>/dev/null)" ]; }
  [ "$want_tavus" = 1 ] && keys+=(TAVUS_API_KEY)
  avatar_has bey && keys+=(BEY_API_KEY)
  for key in "${keys[@]}"; do
    if in_cluster "$key"; then ok "$key in the cluster"; else warn "$key empty in the cluster (skipped; that feature stays off)"; fi
  done
  in_cluster SLACK_BOT_TOKEN && slack=on
  in_cluster GOOGLE_SERVICE_ACCOUNT_JSON && in_cluster GOOGLE_DOCS_FOLDER_ID && docs=on
  local av offered=()
  for av in $( [ "$want_tavus" = 1 ] && [ -z "${AVATAR_CHOICE:-}" ] && echo tavus || avatar_list ); do
    case "$av" in
      tavus) in_cluster TAVUS_API_KEY && offered+=(Tavus) ;;
      bey) in_cluster BEY_API_KEY && offered+=("Beyond Presence") ;;
      openspline) offered+=("OpenSpline (self-hosted)") ;;
    esac
  done
  [ "${#offered[@]}" -gt 0 ] && avatar=$(IFS=,; echo "${offered[*]}" | sed 's/,/ and /g')
  ok "step 6 deploys with Slack $slack, Google Docs $docs and avatar $avatar (Slack, Google Docs and Tavus follow the keys); requests are approved in the admin portal$([ "$slack" = on ] && echo " and in Slack")"
  if [ "$want_tavus" = 1 ] && ! in_cluster TAVUS_API_KEY; then
    if avatar_has openspline; then note "without a Tavus key the UI offers OpenSpline only; add TAVUS_API_KEY to $SECRETS_FILE, then --step 5 and --step 6, to offer Tavus too"
    else note "without a Tavus key, voice sessions are audio-only; add TAVUS_API_KEY to $SECRETS_FILE, then --step 5 and --step 6, to bring the avatar in (or AVATAR_PROVIDER=openspline scripts/setup.sh --step 2 for the self-hosted one)"; fi
  fi
  note "admin portal password (generated, kept on re-runs): oc extract secret/assistant-admin -n $PROJECT --keys=ADMIN_PASSWORD --to=-"
  mark 5
}
step6_namespace() {
  say "${B}Step 6: deploy with Helm${N} (release assistant in $PROJECT)"
  [ "${PROFILE:-}" = namespace ] || { bad "choose the profile first (step 2)"; return 1; }
  write_values_object   # from the choices saved in step 2
  local log extra=(); log=$(logfile deploy)
  [ "$TURN_SECRET" = yes ] || { extra+=(--set livekit.turn.enabled=false); note "no TURN certificate: LiveKit runs without TURN (voice on open networks only)"; }
  say "  values chart/values-demo-cluster.yaml plus $STATE_DIR/values-object.json, secrets from $SECRETS_FILE (log $log)"
  say "  this takes 5 to 20 minutes: image pulls, and the weights of the models served in the project$(avatar_has openspline && echo "; the OpenSpline avatar's first start up to 40")"
  [ -n "${AVOID_NODES:-}" ] && note "pods avoid the nodes $AVOID_NODES, found unable to start containers by an earlier run"
  # shellcheck disable=SC1090
  set -a; [ -f "$SECRETS_FILE" ] && . "$SECRETS_FILE"; set +a
  # deploy.sh moves stuck pods off a node that cannot start containers and writes it to the file;
  # the saved list keeps every later install and test pod off it
  local rc=0
  env PROJECT="$PROJECT" DOMAIN="$DOMAIN" SECRETS_FILE="$SECRETS_FILE" LOG_FILE="$log" RUN_TESTS=1 MODELS= \
    AVOID_NODES="${AVOID_NODES:-}" AVOID_NODES_FILE="$STATE_DIR/avoid-nodes" AVATAR_PROVIDER="${AVATAR_CHOICE:-}" \
    "$ROOT/scripts/deploy.sh" -f "$ROOT/chart/values-demo-cluster.yaml" -f "$STATE_DIR/values-object.json" "${extra[@]}" || rc=$?
  [ -s "$STATE_DIR/avoid-nodes" ] && save AVOID_NODES "$(tr '\n' ' ' < "$STATE_DIR/avoid-nodes" | sed 's/ *$//')"
  [ "$rc" = 0 ] && { mark 6; return 0; }
  bad "deployment reported problems; see $log, fix, and run: scripts/setup.sh --step 6"; return 1
}
step6() {
  [ "$ACCESS" = namespace ] && { step6_namespace; return; }
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
  env PROJECT="$PROJECT" SECRETS_FILE="$SECRETS_FILE" VALUES_OBJECT_FILE="$STATE_DIR/values-object.json" LLM_NAME="$LLM_NAME" LOG_FILE="$log" RUN_TESTS=1 AVATAR_PROVIDER="$(avatar_default)" "${llm_env[@]}" "$ROOT/scripts/deploy-argocd.sh" && { mark 6; return 0; }
  bad "deployment reported problems; see $log, fix, and run: scripts/setup.sh --step 6"; return 1
}
step7() {
  say "${B}Step 7: n8n${N} (owner account and API key are created by the chart's n8n-setup job; this verifies the workflows)"
  [ -n "$N8N_URL" ] || { bad "n8n route not found; run step 6 first"; return 1; }
  local key waited=0; key=$(oc get secret assistant-n8n-api -n "$PROJECT" -o jsonpath='{.data.N8N_API_KEY}' 2>/dev/null | base64 -d 2>/dev/null)
  while [ -z "$key" ] && [ "$waited" -lt 600 ]; do   # the job runs during the Argo CD sync; give it 10 minutes
    local pod; pod=$(oc get pods -n "$PROJECT" -l job-name=n8n-setup --no-headers 2>/dev/null | tail -1)
    if [ -z "$pod" ]; then
      if [ "$ACCESS" = namespace ]; then bad "job n8n-setup has not run (no pod); helm status assistant -n $PROJECT shows the release state; scripts/setup.sh --step 6 runs it again"
      else bad "job n8n-setup has not run (no pod); oc get applications.argoproj.io voice-avatar-assistant -n openshift-gitops shows the sync state"; fi
      return 1
    fi
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
  # indexed = with chunks: the inbox files are classified, not indexed, and do not count
  local waited=0 n=0 c=0
  while [ "$waited" -lt 900 ]; do
    read -r n c < <(doc_counts); n=${n:-0}; c=${c:-0}
    [ "$n" -ge 10 ] && break; sleep 20; waited=$((waited + 20)); [ $((waited % 60)) -eq 0 ] && note "$n documents indexed, $c inbox files classified after ${waited}s"
  done
  [ "$n" -ge 10 ] || { bad "only $n documents indexed after 15 min; oc logs deploy/ingestion -n $PROJECT --tail=50; scripts/n8n-executions.sh"; return 1; }
  ok "$n documents indexed, $c inbox files classified"; NS="$PROJECT" "$ROOT/scripts/check-index.sh" | sed 's/^/  /'; mark 8
}
step9() {
  say "${B}Step 9: verification${N}"
  local extra=(); [ -f "$STATE_DIR/values-object.json" ] && extra=(-f "$STATE_DIR/values-object.json")
  # the namespace profile's overlay holds every endpoint; the cluster path keeps the language model's on the side
  local llm_set=(); [ "$ACCESS" != namespace ] && [ -f "$HOME/assistant-cluster.env" ] && { . "$HOME/assistant-cluster.env"; llm_set=(--set "models.llm.endpoint=$LLM_ENDPOINT" --set "models.llm.servedModelName=$LLM_MODEL"); }
  say "  \$ NS=$PROJECT scripts/demo-preflight.sh -f chart/values-demo-cluster.yaml ${extra[*]:-} --set global.domain=$DOMAIN ${llm_set[*]:-}"
  # the test pod avoids the known broken nodes and moves off a newly found one (scripts/test-services.sh)
  if NS="$PROJECT" AVOID_NODES="${AVOID_NODES:-}" AVOID_NODES_FILE="$STATE_DIR/avoid-nodes" "$ROOT/scripts/demo-preflight.sh" -f "$ROOT/chart/values-demo-cluster.yaml" "${extra[@]}" --set "global.domain=$DOMAIN" "${llm_set[@]}"; then
    mark 9; say ""; say "  ${G}Ready for the demo.${N}"; say "  frontend $FRONTEND_URL"; say "  n8n      $N8N_URL (login $(oc get secret assistant-n8n -n "$PROJECT" -o jsonpath='{.data.N8N_OWNER_EMAIL}' 2>/dev/null | base64 -d), password N8N_OWNER_PASSWORD in $SECRETS_FILE)"
    say "  admin    $FRONTEND_URL/admin (sign in with your name and the password from: oc extract secret/assistant-admin -n $PROJECT --keys=ADMIN_PASSWORD --to=-)"
    say "  Walk through docs/demo-script.md: a cited text answer, a voice session (the browser asks for the microphone), a request by voice with its Slack card, the archive button."
    [ "${SLACK_INTERACTIVITY_HOST:-}" = "n8n-$PROJECT.$DOMAIN" ] || say "  Check once: the Slack app's Interactivity request URL must be $N8N_URL/webhook/slack-interactions (step 5 asks about it)."
    say "  Next cluster: clone, scripts/setup.sh."
  else bad "preflight reported problems (docs/troubleshooting.md); fix and run: scripts/setup.sh --step 9"; return 1; fi
}

# confirm_target: the first change on a cluster and project waits for a yes, so a laptop logged
# in to another cluster (or a forgotten KUBECONFIG) cannot be set up by mistake
confirm_target() {
  local t="$API $PROJECT"
  [ "${CONFIRMED_TARGET:-}" = "$t" ] && return 0
  confirm "Set up the demo on $API as $USER_NAME, in project $PROJECT ($ACCESS access)?" \
    || { say "  Nothing changed. Log in to the intended cluster (oc login, or KUBECONFIG) or set PROJECT, then run scripts/setup.sh again."; exit 1; }
  save CONFIRMED_TARGET "$t"
}

# ---------------------------------------------------------------- another laptop ------------
# What only the machine that ran setup before has is rebuilt from the cluster: the keys in
# SECRETS_FILE (from the secrets created then), the avoided nodes (from the release's node
# exclusions) and the internal environment notes. Only what is missing is filled in.
RESTORE_KEYS="assistant-integrations:SLACK_BOT_TOKEN assistant-integrations:SLACK_SIGNING_SECRET
  assistant-integrations:TAVUS_API_KEY assistant-integrations:TAVUS_FACE_ID assistant-integrations:TAVUS_PAL_ID
  assistant-integrations:BEY_API_KEY
  assistant-integrations:SIMLI_API_KEY assistant-integrations:SIMLI_FACE_ID assistant-integrations:GOOGLE_DOCS_FOLDER_ID
  assistant-models:LLM_API_KEY assistant-models:STT_API_KEY assistant-models:TTS_API_KEY
  assistant-models:EMBEDDINGS_API_KEY assistant-models:GUARDRAILS_API_KEY assistant-models:HF_TOKEN
  assistant-n8n:N8N_OWNER_EMAIL assistant-n8n:N8N_OWNER_PASSWORD"
cluster_secret() { oc get secret "$1" -n "$PROJECT" -o "jsonpath={.data.$2}" 2>/dev/null | base64 -d 2>/dev/null; }
file_has() { grep -q "^$1=." "$SECRETS_FILE" 2>/dev/null; }
file_put() { { grep -v "^$1=" "$SECRETS_FILE" || true; } > "$SECRETS_FILE.tmp"; printf '%s=%s\n' "$1" "$2" >> "$SECRETS_FILE.tmp"; mv "$SECRETS_FILE.tmp" "$SECRETS_FILE"; chmod 600 "$SECRETS_FILE"; }
restore_secrets_file() {
  oc get secret assistant-integrations -n "$PROJECT" >/dev/null 2>&1 || return 0   # nothing deployed yet
  [ -f "$SECRETS_FILE" ] || { cp "$ROOT/secrets.env.example" "$SECRETS_FILE" && chmod 600 "$SECRETS_FILE"; }
  local pair key v k url restored=()
  for pair in $RESTORE_KEYS; do
    key=${pair#*:}; file_has "$key" && continue
    v=$(cluster_secret "${pair%%:*}" "$key")
    [ -n "$v" ] && [ "$v" != none ] || continue
    file_put "$key" "$v"; restored+=("$key")
  done
  # the Google service account goes back into the file the key was read from
  if ! file_has GOOGLE_SERVICE_ACCOUNT_FILE && v=$(cluster_secret assistant-integrations GOOGLE_SERVICE_ACCOUNT_JSON) && [ -n "$v" ]; then
    (umask 077; printf '%s\n' "$v" > "$STATE_DIR/google-sa.json")
    file_put GOOGLE_SERVICE_ACCOUNT_FILE "$STATE_DIR/google-sa.json"; restored+=(GOOGLE_SERVICE_ACCOUNT_FILE)
  fi
  # the Models-as-a-Service key is the one the models served there use
  if ! file_has MAAS_API_KEY && [ -n "${MAAS_URL:-}" ]; then
    for k in LLM EMBEDDINGS; do
      url=$(oc get configmap assistant-config -n "$PROJECT" -o "jsonpath={.data.${k}_BASE_URL}" 2>/dev/null)
      [[ "$url" == "$MAAS_URL"* ]] || continue
      v=$(cluster_secret assistant-models "${k}_API_KEY"); [ -n "$v" ] && [ "$v" != none ] || continue
      file_put MAAS_API_KEY "$v"; restored+=(MAAS_API_KEY); break
    done
  fi
  [ "${#restored[@]}" -gt 0 ] && ok "keys restored from the cluster's secrets into $SECRETS_FILE: ${restored[*]}"
  return 0
}
restore_avoid_nodes() {
  [ -s "$STATE_DIR/avoid-nodes" ] && return 0
  local nodes
  nodes=$(oc get deployments -n "$PROJECT" -l app.kubernetes.io/instance=assistant -o json 2>/dev/null | jq -r '[.items[].spec.template.spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[]?.matchFields[]? | select(.key == "metadata.name" and .operator == "NotIn") | .values[]] | unique | .[]' 2>/dev/null)
  [ -n "$nodes" ] || return 0
  printf '%s\n' $nodes > "$STATE_DIR/avoid-nodes"
  AVOID_NODES=$(tr '\n' ' ' < "$STATE_DIR/avoid-nodes" | sed 's/ *$//')
  ok "avoided nodes restored from the release's node exclusions: $AVOID_NODES"
}
write_private_environment() {
  local f="$ROOT/docs/handoff/private/environment.md" groups gpu_pods lq r u until
  [ -s "$f" ] && return 0
  mkdir -p "${f%/*}" || return 0
  groups=$(oc auth whoami -o jsonpath='{.status.userInfo.groups}' 2>/dev/null | jq -r 'join(", ")' 2>/dev/null)
  gpu_pods=$(oc get pods -n "$PROJECT" -o json 2>/dev/null | jq -r '.items[] | [.spec.containers[].resources.limits // {} | to_entries[] | select(.key | startswith("nvidia.com/")) | "\(.value) x \(.key)"] as $g | select($g | length > 0) | "| \(.metadata.name) | \(.spec.nodeName // "not scheduled") | \($g | join(", ")) |"' 2>/dev/null)
  {
    printf '# Internal environment values (not in git)\n\n'
    printf 'Generated by scripts/setup.sh on %s from the cluster; delete this file and run setup again to\n' "$(date '+%Y-%m-%d %H:%M')"
    printf 'refresh it. `.gitignore` keeps this folder out of the public repository: never commit it.\n\n'
    printf '## Cluster\n'
    printf -- '- API: %s\n- Console: %s\n- Apps domain: %s\n' "$(oc whoami --show-server 2>/dev/null)" "$(oc whoami --show-console 2>/dev/null)" "${DOMAIN:-unknown}"
    printf -- '- User: %s%s\n' "$USER_NAME" "${groups:+ (groups: $groups)}"
    printf -- '- Project: %s, %s access\n- Kubeconfig: %s\n\n' "$PROJECT" "$ACCESS" "${KUBECONFIG:-~/.kube/config}"
    printf 'Log in from a Mac (zsh). Get a token from the console: your name, Copy login command, Display Token.\n\n'
    printf '```bash\nexport KUBECONFIG=%s && read -rs "TOKEN?Paste the API token: " && echo && oc login --token="$TOKEN" --server=%s && unset TOKEN && oc whoami\n```\n\n' "${KUBECONFIG:-~/.kube/config}" "$(oc whoami --show-server 2>/dev/null)"
    if [ "$ACCESS" = namespace ]; then
      printf '## Models-as-a-Service\n- URL: %s (inference: <URL>/prelude-maas/<model>/v1)\n' "${MAAS_URL:-not found}"
      printf -- '- Models: %s\n\n' "$(for r in $MAAS_IDS; do printf '%s ' "${r##*/}"; done)"
      printf '## GPU queues (Kueue)\n| Queue | Resource | Units | Booked until |\n|---|---|---|---|\n'
      while read -r lq r u until; do [ -n "$lq" ] && printf '| %s | %s | %s | %s |\n' "$lq" "$r" "$u" "$(until_text "$until")"; done < <(gpu_offers)
      printf '\n'
    fi
    printf '## Nodes\n- Avoided (cannot start containers): %s\n\n' "${AVOID_NODES:-none}"
    if [ -n "$gpu_pods" ]; then printf '| GPU pod | Node | GPUs |\n|---|---|---|\n%s\n' "$gpu_pods"; fi
  } > "$f"
  chmod 600 "$f"
  ok "wrote ${f#"$ROOT"/} (internal values of this cluster, kept out of git)"
}
restore_local_state() {
  [ "$LOGGED_IN" = 1 ] && [ "$PROJECT_EXISTS" = yes ] || return 0
  restore_secrets_file
  restore_avoid_nodes
  write_private_environment
}

# ---------------------------------------------------------------- main ----------------------
ensure_tool jq quiet || exit 1
[ "$MODE" = status ] || ensure_login || { discover; show_status; exit 1; }
say "$(ts) discovering the cluster (a few seconds)"
discover; show_status
case "$MODE" in
status) exit 0 ;;
step) [ -n "$ONLY" ] || { echo "usage: scripts/setup.sh --step N"; exit 1; }; confirm_target; restore_local_state; ensure_helm || exit 1; run_step "$ONLY"; rc=$?; discover; show_status; exit $rc ;;
esac
confirm_target
restore_local_state
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
