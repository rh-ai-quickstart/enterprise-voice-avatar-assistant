#!/usr/bin/env bash
# Lists the documents known to the ingestion service with their chunk counts (inbox files
# that were only classified show none), flags duplicate file names, and optionally runs a
# search to spot stale text after documents were rewritten.
#
# Usage: NS=<namespace> scripts/check-index.sh ["text that should not be found any more"]
set -uo pipefail
NS="${NS:-$(oc project -q)}"
QUERY="${1:-}"

echo "=== documents (indexed = chunks > 0) ==="
oc exec deploy/rag-api -n "$NS" -- .venv/bin/python -c "
import urllib.request, json, collections
d = json.load(urllib.request.urlopen('http://ingestion:8080/v1/documents'))
items = d if isinstance(d, list) else d.get('documents', d.get('items', []))
names = collections.Counter(str(x.get('source') or x.get('key')) for x in items)
for x in sorted(items, key=lambda x: str(x.get('source') or x.get('key'))):
    print(f\"  {str(x.get('source') or x.get('key')):45s} chunks={str(x.get('chunks', x.get('chunk_count'))):5s} id={str(x.get('doc_id', x.get('id')))[:8]}\")
dups = [n for n, c in names.items() if c > 1]
print('duplicate file names:', dups or 'none')
"

if [ -n "$QUERY" ]; then
  # /v1/search is internal: run it from the RAG API pod, which has the token in its environment
  echo "=== search: $QUERY (stale text would score high) ==="
  oc exec deploy/rag-api -n "$NS" -- .venv/bin/python -c "
import json, os, sys, urllib.request
req = urllib.request.Request('http://localhost:8080/v1/search', data=json.dumps({'query': sys.argv[1], 'top_k': 3}).encode(),
                             headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ.get('INTERNAL_API_TOKEN', '')})
for h in json.load(urllib.request.urlopen(req, timeout=60))['hits']:
    print(' ', round(h['score'], 2), h['source'], '|', h['snippet'][:90].replace(chr(10), ' '))
" "$QUERY"
fi
