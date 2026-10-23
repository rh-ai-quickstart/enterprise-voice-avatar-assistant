#!/usr/bin/env bash
# Runs the chart's connectivity test pod against a deployed release and prints its
# results. `helm test` does the same when the release was installed with Helm; this
# script also works for Argo CD deployments, where no Helm release exists: it renders
# the test pod from the chart, applies it, waits, prints the log, and deletes it.
#
# A node that cannot start containers (the pod's sandbox keeps failing, as when the node's
# container storage breaks) is left out and the pod started again elsewhere, up to three
# times. The pod also avoids AVOID_NODES (space-separated) and the nodes listed in
# AVOID_NODES_FILE, where newly found ones are added.
#
# Usage: NS=<namespace> scripts/test-services.sh [-f values.yaml] [--set key=value ...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${NS:-$(oc project -q)}"
RELEASE="${RELEASE:-assistant}"
POD="${RELEASE}-test-services"
AVOID_NODES="${AVOID_NODES:-}"
[ -s "${AVOID_NODES_FILE:-/nonexistent}" ] && AVOID_NODES=$(tr '\n' ' ' < "$AVOID_NODES_FILE" | sed 's/ *$//')
command -v helm >/dev/null || { echo "helm is required (https://helm.sh/docs/intro/install/)"; exit 1; }
trap 'oc delete pod "$POD" -n "$NS" --wait=false >/dev/null 2>&1 || true' EXIT

start_pod() {
  local avoid=(); [ -n "$AVOID_NODES" ] && avoid=(--set "global.avoidNodes={$(echo $AVOID_NODES | tr ' ' ',')}")
  oc delete pod "$POD" -n "$NS" --wait=true >/dev/null 2>&1 || true
  helm template "$RELEASE" "$ROOT/chart" --namespace "$NS" "$@" ${avoid[@]+"${avoid[@]}"} --show-only templates/test-model-access.yaml | oc apply -n "$NS" -f -
}
# wait_pod: 0 when the pod ran to the end, 2 when its node cannot start it (BAD_NODE), 1 after 240 s
wait_pod() {
  local i phase uid fails
  for i in $(seq 1 48); do
    phase=$(oc get pod "$POD" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    case "$phase" in Succeeded|Failed) return 0;; esac
    uid=$(oc get pod "$POD" -n "$NS" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)
    # events of earlier pods with the same name do not count: only this pod's uid
    fails=$(oc get events -n "$NS" --field-selector "involvedObject.uid=$uid,reason=FailedCreatePodSandBox" -o json 2>/dev/null | jq '[.items[] | .count // 1] | add // 0' 2>/dev/null || echo 0)
    if [ -n "$uid" ] && [ "${fails:-0}" -ge 3 ]; then
      BAD_NODE=$(oc get pod "$POD" -n "$NS" -o jsonpath='{.spec.nodeName}' 2>/dev/null || true)
      [ -n "$BAD_NODE" ] && return 2
    fi
    sleep 5
  done
  return 1
}

for attempt in 1 2 3; do
  start_pod "$@"
  rc=0; BAD_NODE=""; wait_pod || rc=$?
  [ "$rc" = 2 ] || break
  echo "node $BAD_NODE cannot start the test pod (its sandbox failed 3 times); starting it on another node. Tell the cluster admins: that node needs attention"
  AVOID_NODES="${AVOID_NODES:+$AVOID_NODES }$BAD_NODE"
  [ -n "${AVOID_NODES_FILE:-}" ] && printf '%s\n' $AVOID_NODES > "$AVOID_NODES_FILE"
done
oc logs -n "$NS" "$POD" 2>/dev/null || true
phase=$(oc get pod "$POD" -n "$NS" -o jsonpath='{.status.phase}')
[ "$phase" = "Succeeded" ] || { echo "test pod ended in phase $phase"; exit 1; }
