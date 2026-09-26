#!/usr/bin/env bash
# Imports (creates or updates) every workflow in chart/files/n8n-workflows through the n8n
# public API and activates it. Matching is by workflow name, so re-running the
# script updates the workflows in place.
#
# Credentials attached to nodes in the editor (for example the Slack API
# credential) survive re-imports: they are copied from the existing workflow
# by node name.
#
# Usage:
#   N8N_URL=https://n8n-<ns>.<domain> N8N_API_KEY=<key from Settings > n8n API> scripts/import-workflows.sh [dir]
set -euo pipefail
: "${N8N_URL:?set N8N_URL to the n8n base URL}"
# the API key created by the chart's n8n-setup job, unless one is given in the environment
[ -n "${N8N_API_KEY:-}" ] || N8N_API_KEY=$(oc get secret assistant-n8n-api -n "${NS:-${NAMESPACE:-voice-avatar-assistant}}" -o jsonpath='{.data.N8N_API_KEY}' 2>/dev/null | base64 -d 2>/dev/null || true)
: "${N8N_API_KEY:?create an API key in n8n under Settings > n8n API and export N8N_API_KEY}"
DIR="${1:-$(dirname "$0")/../chart/files/n8n-workflows}"

python3 - "$N8N_URL" "$N8N_API_KEY" "$DIR" <<'PY'
import glob, json, os, sys, urllib.error, urllib.request

base, key, directory = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/api/v1{path}", data=data, method=method,
                                 headers={"X-N8N-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"{method} {path} failed: {exc.code} {detail}")

existing = {}
cursor = None
while True:
    page = call("GET", "/workflows?limit=100" + (f"&cursor={cursor}" if cursor else ""))
    for wf in page.get("data", []):
        existing[wf["name"]] = wf["id"]
    cursor = page.get("nextCursor")
    if not cursor:
        break

files = sorted(glob.glob(os.path.join(directory, "*.json")))
if not files:
    raise SystemExit(f"no workflow files in {directory}")
for path in files:
    wf = json.load(open(path))
    body = {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": wf.get("settings", {"executionOrder": "v1"})}
    if wf.get("staticData") is not None:
        body["staticData"] = wf["staticData"]
    if wf["name"] in existing:
        wid = existing[wf["name"]]
        # The API replaces the node list wholesale, which would drop credentials
        # attached in the editor; carry them over by node name.
        current = call("GET", f"/workflows/{wid}")
        creds = {n["name"]: n["credentials"] for n in current.get("nodes", []) if n.get("credentials")}
        for node in body["nodes"]:
            if not node.get("credentials") and node["name"] in creds:
                node["credentials"] = creds[node["name"]]
        call("PUT", f"/workflows/{wid}", body)
        action = "updated"
    else:
        wid = call("POST", "/workflows", body)["id"]
        action = "created"
    try:
        call("POST", f"/workflows/{wid}/activate")
        state = "active"
    except SystemExit as exc:
        state = f"not activated ({exc})"
    print(f"{action:8s} {wf['name']:40s} id={wid} {state}")
print("\nWebhook URLs (production):")
for path in ("chat", "object-created", "classify", "request-intake", "slack-interactions", "archive-transcript"):
    print(f"  {base}/webhook/{path}")
PY
