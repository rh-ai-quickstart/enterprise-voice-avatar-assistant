#!/bin/sh
set -e
SERVER=/opt/app-root/etc/nginx.default.d
HTTP=/opt/app-root/etc/nginx.d
sed -i "s|__RAG_API_UPSTREAM__|${RAG_API_UPSTREAM:-rag-api:8080}|g" "$SERVER/app.conf" "$SERVER/admin.conf"
if [ "${ADMIN_ENABLED:-true}" = "false" ]; then
  # No admin portal: its page answers 404, and /api/v1/admin/* is refused like any other internal route
  printf 'location /admin {\n    default_type text/plain;\n    return 404 "The admin portal is turned off (admin.enabled=false)";\n}\n' > "$SERVER/admin.conf"
  sed -i '/# admin-portal$/d' "$HTTP/api-allowlist.conf"
fi
exec nginx -g "daemon off;"
