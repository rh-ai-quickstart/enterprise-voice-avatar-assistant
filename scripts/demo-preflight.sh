#!/usr/bin/env bash
# Pre-flight before a demo: models Ready, Argo CD status (when the project is managed by
# Argo CD), the connectivity test pod, the public API proxy and the admin portal, and the n8n
# webhooks. Read-only apart from the test pod.
#
# Usage: NS=<namespace> scripts/demo-preflight.sh [-f values.yaml]
#   The values arguments are passed to the test pod render (use the same file as the
#   deployment, for example -f chart/values-demo-cluster.yaml).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${NS:-$(oc project -q)}"
rc=0

echo "=== models ==="
if oc get isvc -n "$NS" >/dev/null 2>&1 && [ -n "$(oc get isvc -n "$NS" -o name)" ]; then
  oc get isvc -n "$NS" -o custom-columns='NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status'
  oc get isvc -n "$NS" -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' | grep -qv True && rc=1
else
  echo "  no InferenceServices in $NS (remote models)"
fi

echo "=== pods not running ==="
oc get pods -n "$NS" --field-selector=status.phase!=Running,status.phase!=Succeeded --no-headers 2>/dev/null | grep -v '^$' || echo "  none"

APP=$(oc get applications.argoproj.io -A -o jsonpath="{range .items[?(@.spec.destination.namespace==\"$NS\")]}{.metadata.namespace}/{.metadata.name} sync={.status.sync.status} health={.status.health.status}{'\n'}{end}" 2>/dev/null)
[ -n "$APP" ] && { echo "=== argo cd ==="; echo "  $APP"; }

echo "=== connectivity test pod ==="
"$ROOT/scripts/test-services.sh" "$@" || rc=1

if oc get route frontend -n "$NS" >/dev/null 2>&1; then
  FE="https://$(oc get route frontend -n "$NS" -o jsonpath='{.spec.host}')"
  code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
  echo "=== public API proxy ==="
  if [ "$(code "$FE/api/v1/tickets")" = 404 ]; then echo "  internal routes refused (GET /api/v1/tickets: 404)"
  else echo "  GET /api/v1/tickets is reachable from outside: the frontend image predates the allowlist"; rc=1; fi
  case "$(code "$FE/api/v1/admin/me")" in
    401) echo "  admin portal answers at $FE/admin (sign-in required)" ;;
    404) echo "  admin portal off (admin.enabled=false)" ;;
    *)   echo "  admin portal: unexpected answer from $FE/api/v1/admin/me"; rc=1 ;;
  esac
fi

if oc get route n8n -n "$NS" >/dev/null 2>&1; then
  N8N="https://$(oc get route n8n -n "$NS" -o jsonpath='{.spec.host}')"
  echo "=== n8n webhooks ==="
  for p in chat object-created classify request-intake slack-interactions ticket-decided archive-transcript; do
    msg=$(curl -s --max-time 15 "$N8N/webhook/$p" | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('message',''))
except Exception: print('no JSON response')")
    case "$msg" in *"GET requests"*) printf '  %-20s REGISTERED\n' "$p" ;; *) printf '  %-20s MISSING (%s)\n' "$p" "${msg:0:60}"; rc=1 ;; esac
  done
fi

[ "$rc" = 0 ] && echo "PRE-FLIGHT OK" || echo "PRE-FLIGHT: check the lines above"
exit $rc
