#!/usr/bin/env bash
# Uploads the demo document set into the object store of a deployed release, so the ingestion
# (documents/) and classification (inbox/) workflows run on it: the object store announces every
# new object to n8n (WF2). Policies and procedures go to `documents`, invoices and contracts to
# `inbox`. Files of your own can be given as paths; BUCKET picks their bucket.
#
# Runs from anywhere with `oc` logged in; the upload goes through the ingestion pod, which has
# the object store's address and credentials.
#
# Usage: NS=<namespace> scripts/load-sample-docs.sh [file ...]
#   NS       target namespace (default: current project)
#   BUCKET   bucket for every file given (default: inbox for invoice-* and contract-*, else documents)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${NS:-$(oc project -q)}"
SAMPLES="$ROOT/data/sample-docs"
FILES=()
for arg in "$@"; do
  if [ -f "$arg" ]; then FILES+=("$arg"); else FILES+=("$SAMPLES/$arg"); fi
done
if [ ${#FILES[@]} -eq 0 ]; then
  for f in "$SAMPLES"/*.md "$SAMPLES"/*.docx "$SAMPLES"/*.pdf; do [ "$(basename "$f")" = README.md ] || FILES+=("$f"); done
fi
oc get deploy/ingestion -n "$NS" >/dev/null

for f in "${FILES[@]}"; do
  [ -f "$f" ] || { echo "skip $f (not a file)"; continue; }
  name="$(basename "$f")"
  case "$name" in
    invoice-*|contract-*) bucket="${BUCKET:-inbox}" ;;
    *)                    bucket="${BUCKET:-documents}" ;;
  esac
  oc exec -i -n "$NS" deploy/ingestion -- .venv/bin/python -c '
import mimetypes, sys
from app import storage
bucket, key = sys.argv[1], sys.argv[2]
storage.put_bytes(bucket, key, sys.stdin.buffer.read(), mimetypes.guess_type(key)[0])
' "$bucket" "$name" < "$f"
  printf '%-45s -> %s\n' "$name" "$bucket"
done
echo
echo "Uploaded. Watch the n8n executions (WF2 ingestion, WF3 classification) and the Slack channels."
