#!/usr/bin/env bash
# Minimal long-running web_app fixture for HEAXHub multi-host E2E tests.
#
# Uses only Python stdlib so dependencies are zero. Reads the runtime contract
# from the environment, then execs a tiny http.server that serves:
#   - GET /healthz  -> 200 "ok"
#   - GET /*        -> 200 HTML containing $APP_ID and $ROOT_PATH
#
# Environment contract (matches service_manager / service_manager_dev):
#   APP_ID      app identifier (also embedded in the response body)
#   PORT        TCP port to bind (mandatory)
#   BIND_HOST   bind address, default 127.0.0.1
#   ROOT_PATH   public base path Caddy mounts us at, e.g. "/apps/demo-a"

set -euo pipefail

: "${PORT:?PORT must be set by the launcher}"
: "${APP_ID:=unknown-app}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
ROOT_PATH="${ROOT_PATH:-}"

PY="${PYTHON:-python3}"

echo "[streamlit-hello] app_id=${APP_ID} bind=${BIND_HOST}:${PORT} root_path=${ROOT_PATH}" >&2

exec "$PY" - "$BIND_HOST" "$PORT" "$APP_ID" "$ROOT_PATH" <<'PY'
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

bind_host = sys.argv[1]
port = int(sys.argv[2])
app_id = sys.argv[3]
root_path = sys.argv[4]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        # Caddy strips the prefix before forwarding, so the upstream sees "/".
        # Accept either the bare "/healthz" or the (defensive) prefixed form.
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", f"{root_path}/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        body = (
            "<!doctype html><html><body>"
            f"<h1>streamlit-hello fixture</h1>"
            f"<p>app_id={app_id}</p>"
            f"<p>root_path={root_path}</p>"
            f"<p>raw_path={path}</p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # noqa: A003
        # Quieter logs — tee through stderr only on demand.
        if os.environ.get("STREAMLIT_HELLO_VERBOSE"):
            sys.stderr.write("[streamlit-hello] " + (fmt % args) + "\n")


server = ThreadingHTTPServer((bind_host, port), Handler)
print(f"[streamlit-hello] serving on {bind_host}:{port}", file=sys.stderr, flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
PY
