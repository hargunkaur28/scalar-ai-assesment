"""Temporary diagnostic: a Python function with zero project imports.

If this responds but /api/redact does not, the Python runtime and api/ routing
are fine and the fault is in redact.py or its bundling. If neither responds but
/api/ping.js does, file-based Python functions are not being built at all.
Delete once the deployment is confirmed working.
"""

import json
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "runtime": "python"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
