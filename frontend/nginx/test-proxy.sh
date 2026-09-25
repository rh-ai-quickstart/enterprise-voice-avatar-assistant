#!/usr/bin/env bash
# Checks the public /api proxy with the real nginx image against a stub RAG API: the routes the
# chat UI and the admin portal call reach it, every other route gets 404 (also through dot
# segments and encoded slashes), the event stream is not buffered, and ADMIN_ENABLED=false
# removes the admin routes. Needs docker and python3. CI runs it; locally:
#   frontend/nginx/test-proxy.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
IMAGE=assistant-proxy-test
UPSTREAM_PORT=18080
PORT=18081
failed=0
cleanup() { docker rm -f "$IMAGE" >/dev/null 2>&1 || true; [ -n "${STUB:-}" ] && { kill "$STUB" 2>/dev/null; wait "$STUB" 2>/dev/null; } || true; rm -rf "$WORK"; }
trap cleanup EXIT

cp "$HERE"/app.conf "$HERE"/admin.conf "$HERE"/api-allowlist.conf "$HERE"/entrypoint.sh "$WORK"/
echo '<html>app</html>' > "$WORK/index.html"
sed -n '/^FROM registry.access.redhat.com\/ubi9\/nginx/,$p' "$HERE/../Containerfile" \
  | sed 's|^COPY --from=build .*|COPY --chown=1001:0 index.html /opt/app-root/src/index.html|; s|nginx/||g' > "$WORK/Dockerfile"
docker build -q -t "$IMAGE" "$WORK" >/dev/null

cat > "$WORK/stub.py" <<'EOF'
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Stub(BaseHTTPRequestHandler):
    def handle_any(self):
        if self.path.startswith("/v1/admin/stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for n in range(2):
                self.wfile.write(f"id: {n}\nevent: activity\ndata: {time.time():.3f}\n\n".encode()); self.wfile.flush(); time.sleep(1)
            return
        body = json.dumps({"path": self.path, "client": self.headers.get("X-Client-Address")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any
    def log_message(self, *args): pass
ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Stub).serve_forever()
EOF
python3 "$WORK/stub.py" "$UPSTREAM_PORT" & STUB=$!
sleep 1; kill -0 "$STUB" 2>/dev/null || { echo "the stub RAG API could not listen on port $UPSTREAM_PORT"; exit 1; }

start() {
  docker rm -f "$IMAGE" >/dev/null 2>&1 || true
  docker run -d --name "$IMAGE" --user 1000680000:0 --add-host=host.docker.internal:host-gateway \
    -e RAG_API_UPSTREAM="host.docker.internal:$UPSTREAM_PORT" -e ADMIN_ENABLED="$1" -p "$PORT:8080" "$IMAGE" >/dev/null
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://localhost:$PORT/" && return 0; sleep 1; done
  docker logs "$IMAGE"; exit 1
}
# expect <status> <method> <path> [curl args]: the proxy answers with that status
expect() {
  local want=$1 method=$2 path=$3; shift 3
  local got; got=$(curl -s --path-as-is -o "$WORK/body" -w '%{http_code}' -X "$method" "$@" "http://localhost:$PORT$path")
  if [ "$got" = "$want" ]; then printf '  ok    %s %-7s %s\n' "$got" "$method" "$path"
  else printf '  FAIL  %s %-7s %s (expected %s): %s\n' "$got" "$method" "$path" "$want" "$(head -c 120 "$WORK/body")"; failed=1; fi
}

echo "== admin portal on"
start true
for r in "POST /api/v1/chat" "POST /api/v1/chat/stream" "GET /api/v1/info" "GET /api/v1/voice/token?session_id=s" \
         "GET /api/v1/voice/faces" "GET /api/v1/voice/faces/r1/poster" "GET /api/v1/sessions/s1/messages" \
         "GET /api/v1/sessions/s1/notifications" "POST /api/v1/sessions/s1/notifications/ack" \
         "POST /api/v1/sessions/s1/archive" "DELETE /api/v1/sessions/s1" "GET /api/v1/admin/me" \
         "POST /api/v1/admin/login" "PATCH /api/v1/admin/tickets/REQ-000001"; do
  expect 200 $r
done
for r in "GET /api/v1/chat" "PATCH /api/v1/tickets/REQ-000001" "GET /api/v1/tickets" "POST /api/v1/requests" \
         "POST /api/v1/search" "GET /api/v1/sessions/s1/transcript" "GET /api/v1/users/u1/memory" \
         "POST /api/v1/classify" "GET /api/v1/knowledge-gaps/digest" "POST /api/v1/internal/events" \
         "GET /api/v1/sessions/s1/../../tickets" "GET /api/v1/sessions/s1%2F..%2F..%2Ftickets/messages" \
         "GET /api/v1/chat/../tickets" "PUT /api/v1/admin/me"; do
  expect 404 $r
done
grep -q '"detail"' "$WORK/body" || { echo "  FAIL  a refused route should answer JSON, not the app"; failed=1; }
expect 200 GET /some/app/route
client=$(curl -s -H 'X-Forwarded-For: 198.51.100.1, 203.0.113.5' -H 'X-Client-Address: 192.0.2.1' "http://localhost:$PORT/api/v1/info")
case "$client" in *'"client": "203.0.113.5"'*) echo "  ok    X-Client-Address is the last X-Forwarded-For entry";; *) echo "  FAIL  X-Client-Address: $client"; failed=1;; esac
# Seconds between the two events as curl receives them; buffered, they would arrive together
spread=$(curl -sN "http://localhost:$PORT/api/v1/admin/stream" \
  | python3 -c 'import sys, time; t = [time.time() for line in sys.stdin if line.startswith("data:")]; print(f"{t[-1] - t[0]:.2f}" if len(t) > 1 else 0)')
if awk -v s="$spread" 'BEGIN{exit !(s >= 0.8)}'; then echo "  ok    stream events arrive as they are sent (${spread}s apart)"; else echo "  FAIL  stream buffered (${spread}s apart)"; failed=1; fi

echo "== admin portal off"
start false
expect 404 GET /api/v1/admin/me
expect 404 POST /api/v1/admin/login
expect 404 GET /api/v1/admin/stream
expect 200 POST /api/v1/chat

[ "$failed" = 0 ] && echo "proxy OK" || { echo "proxy checks failed"; exit 1; }
