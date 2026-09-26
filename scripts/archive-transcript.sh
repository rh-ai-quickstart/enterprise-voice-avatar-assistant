#!/usr/bin/env bash
# Archives a conversation the way the chat's Archive button does: the RAG API records the archive
# (the admin portal lists it), creates the Google Doc when that integration is on, and starts the
# archival workflow (WF5), which re-ingests the transcript and reports back.
# Usage: NS=<namespace> scripts/archive-transcript.sh [session_id]
# Without an argument the newest conversation in the RAG API is used.
set -euo pipefail
NS="${NS:-$(oc project -q)}"
SID="${1:-}"
if [ -z "$SID" ]; then
  SID=$(oc exec deploy/rag-api -n "$NS" -- .venv/bin/python -c "from app import memory; rows = memory.run('SELECT session_id FROM conversations ORDER BY updated_at DESC LIMIT 1', fetch=True) or []; print(rows[0]['session_id'] if rows else '')")
  [ -n "$SID" ] || { echo "no conversation found; pass a session id"; exit 1; }
  echo "newest session: $SID"
fi
oc exec deploy/rag-api -n "$NS" -- curl -sf -X POST "http://localhost:8080/v1/sessions/$SID/archive"
echo
echo "archival requested for $SID; the admin portal (Conversations) shows the archive and whether it was indexed"
