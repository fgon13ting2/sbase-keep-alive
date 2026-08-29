#!/usr/bin/env python3
"""Self-test keep-alive.py: mock server 540 (paused) & 200+403 (ok, write ditolak RLS)."""
import http.server, threading, subprocess, os, sys

class H(http.server.BaseHTTPRequestHandler):
    mode = "paused"
    def log_message(self, *a): pass
    def _send(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if H.mode == "paused":
            self._send(540, b'{"message":"Project is paused"}')
        else:
            self._send(200, b'[{"name":"placeholder"}]')
    def do_PATCH(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send(403, b'{"message":"row-level security violation"}')

srv = http.server.HTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

env = dict(os.environ)
env.update(SUPABASE_URL_1=f"http://127.0.0.1:{port}", SUPABASE_KEY_1="test-key", SUPABASE_NAME_1="mock-project")
env.pop("DISCORD_WEBHOOK", None)

H.mode = "paused"
r1 = subprocess.run([sys.executable, "scripts/keep-alive.py"], env=env, capture_output=True, text=True)
print("=== KASUS PAUSED (540) ===")
print(r1.stdout.strip())
print("exit code:", r1.returncode)
assert r1.returncode == 1 and "PAUSED" in r1.stdout, "540 harus terdeteksi PAUSED & exit 1"

H.mode = "ok"
r2 = subprocess.run([sys.executable, "scripts/keep-alive.py"], env=env, capture_output=True, text=True)
print("=== KASUS OK (200) + write ditolak (403) ===")
print(r2.stdout.strip())
print("exit code:", r2.returncode)
assert r2.returncode == 0 and "write=no" in r2.stdout, "SELECT ok + write ditolak harus tetap sukses"

srv.shutdown()
print("\nSEMUA TEST LULUS")
