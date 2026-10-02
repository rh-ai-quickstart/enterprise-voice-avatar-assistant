#!/usr/bin/env bash
# Request handling on the cluster's language model: does the RAG API tell service requests from
# questions, and does an answer ever claim to have logged or ordered something it did not? Runs the
# cases of scripts/eval-requests.py inside the RAG API pod, once with the code deployed there and once
# with this checkout's services/rag-api/app (copied to /tmp/eval in the pod; nothing restarts), so a
# prompt change can be judged on the real model before it is merged. No conversation, ticket or
# document is read or written. The checkout must not need packages the deployed image lacks.
#
# Usage: NS=<namespace> scripts/eval-requests.sh [deployed|checkout|both] [runs per answer case]
#   defaults: both, 3 runs
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${NS:-$(oc project -q)}"
WHICH="${1:-both}"
RUNS="${2:-3}"
PY=/opt/app-root/src/.venv/bin/python
oc get deploy/rag-api -n "$NS" >/dev/null

if [ "$WHICH" != checkout ]; then
  echo "##### deployed code: $(oc get deploy/rag-api -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}' | sed 's/.*://')"
  oc exec -i deploy/rag-api -n "$NS" -- "$PY" - deployed "$RUNS" < "$ROOT/scripts/eval-requests.py"
  echo
fi
if [ "$WHICH" != deployed ]; then
  echo "##### this checkout: $(git -C "$ROOT" rev-parse --abbrev-ref HEAD) $(git -C "$ROOT" rev-parse --short HEAD)"
  tar -C "$ROOT/services/rag-api" --exclude=__pycache__ -cf - app \
    | oc exec -i deploy/rag-api -n "$NS" -- sh -c 'rm -rf /tmp/eval && mkdir -p /tmp/eval && tar -xf - -C /tmp/eval'
  oc exec -i deploy/rag-api -n "$NS" -- sh -c "cd /tmp/eval && $PY - checkout $RUNS" < "$ROOT/scripts/eval-requests.py"
  oc exec deploy/rag-api -n "$NS" -- rm -rf /tmp/eval
fi
