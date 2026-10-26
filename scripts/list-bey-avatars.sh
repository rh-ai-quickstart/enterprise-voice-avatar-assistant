#!/usr/bin/env bash
# Lists the Beyond Presence avatars you can offer in the UI (chart value voiceAgent.beyFaces): the
# stock avatars and your own trained ones. The API key comes from BEY_API_KEY or from the
# assistant-integrations secret in NS and is never printed.
#   NS=voice-avatar-assistant scripts/list-bey-avatars.sh
set -euo pipefail
NS="${NS:-voice-avatar-assistant}"
if [ -z "${BEY_API_KEY:-}" ]; then
  BEY_API_KEY=$(oc get secret assistant-integrations -n "$NS" -o jsonpath='{.data.BEY_API_KEY}' | base64 -d)
fi
[ -n "$BEY_API_KEY" ] || { echo "BEY_API_KEY is empty: export it or add it to secret assistant-integrations in $NS" >&2; exit 1; }
export BEY_API_KEY
python3 - <<'PY'
import json
import os
import urllib.parse
import urllib.request

key = os.environ["BEY_API_KEY"]
avatars, params = [], {"limit": 50}
while True:
    request = urllib.request.Request(
        f"https://api.bey.dev/v1/avatars?{urllib.parse.urlencode(params)}", headers={"x-api-key": key}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    avatars.extend(body.get("data") or [])
    if not body.get("has_more") or not body.get("next_cursor"):
        break
    params = {"limit": 50, "cursor": body["next_cursor"]}

ready = [a for a in avatars if a.get("status") in (None, "available")]
print(f"{'avatar id':<42} {'kind':<8} name")
for a in sorted(ready, key=lambda a: (a.get("visibility") != "public", (a.get("name") or "").lower())):
    kind = "stock" if a.get("visibility") == "public" else "yours"
    print(f"{a.get('id', ''):<42} {kind:<8} {a.get('name', '')}")
print(f"\n{len(ready)} avatars ready to use. See them in Bey Studio: https://bey.studio/avatars")
print("Add the ones you want to chart values as voiceAgent.beyFaces entries: id, name, gender (female|male).")
PY
