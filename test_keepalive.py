#!/usr/bin/env python3
"""Self-test keep-alive.py (mock server):

 1. 540 (project paused)          -> [PAUSED], exit 1
 2. 200 + row ada + update OK     -> [OK]   write=update, exit 0
                                  (PATCH harus pakai ?id=eq.<id>)
 3. 200 + row ada + update 403    -> [OK]   write=no, exit 0 (RLS nolak)
 4. 200 + tabel kosong ([])       -> [OK]   write=insert+update, exit 0
                                  (INSERT return=representation + PATCH id)
"""
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.parse

# State observasi request yang dikirim script (diisi mock server)
SEEN = {"patch_paths": [], "post_prefers": [], "has_insert_post": False}


class H(http.server.BaseHTTPRequestHandler):
    mode = "paused"  # paused | full | deny | empty

    def log_message(self, *a):
        pass

    def _send(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if H.mode == "paused":
            self._send(540, b'{"message":"Project is paused"}')
        elif H.mode == "empty":
            self._send(200, b"[]")
        else:  # full / deny: row ada, dengan id (schema asli)
            self._send(200, b'[{"id":6,"name":"placeholder"}]')

    def do_POST(self):
        SEEN["has_insert_post"] = True
        prefer = self.headers.get("Prefer", "")
        SEEN["post_prefers"].append(prefer)
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        # return=representation -> balikin row baru dengan id (mis. 7)
        self._send(201, b'[{"id":7,"name":"keep-alive inserted"}]')

    def do_PATCH(self):
        SEEN["patch_paths"].append(urllib.parse.urlparse(self.path).path +
                                   ("?" + urllib.parse.urlparse(self.path).query))
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if H.mode == "deny":
            self._send(403, b'{"message":"row-level security violation"}')
        else:
            self._send(200, b"")


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

env = dict(os.environ)
env.update(SUPABASE_URL_1=f"http://127.0.0.1:{port}",
           SUPABASE_KEY_1="test-key",
           SUPABASE_NAME_1="mock-project")
env.pop("DISCORD_WEBHOOK", None)


def run_case(label, mode, must_contain, must_exit):
    H.mode = mode
    r = subprocess.run([sys.executable, "scripts/keep-alive.py"],
                       env=env, capture_output=True, text=True)
    print(f"=== {label} ===")
    print(r.stdout.strip())
    print("exit code:", r.returncode)
    assert r.returncode == must_exit, f"{label}: exit={r.returncode} != {must_exit}"
    for part in must_contain:
        assert part in r.stdout, f"{label}: stdout harus berisi {part!r}"
    return r


# Kasus 1: project PAUSED (540)
run_case("KASUS PAUSED (540)", "paused", ["PAUSED"], 1)

# Kasus 2: OK + row ada + update sukses -> PATCH harus ?id=eq.6
SEEN["patch_paths"] = []
run_case("KASUS OK (200, row ada) + update OK", "full", ["write=update"], 0)
assert SEEN["patch_paths"] and "id=eq.6" in SEEN["patch_paths"][0], \
    f"PATCH harus pakai WHERE id=eq.6, dapat: {SEEN['patch_paths']}"
print("  (verifikasi: PATCH URL =", SEEN["patch_paths"][0], ")")

# Kasus 3: OK + update ditolak RLS (403) -> tetap sukses, write=no
run_case("KASUS OK (200, row ada) + update 403 (RLS)", "deny", ["write=no"], 0)

# Kasus 4: OK + tabel kosong -> INSERT (return=representation) + PATCH id=eq.7
SEEN["patch_paths"] = []
SEEN["post_prefers"] = []
run_case("KASUS OK (200, TABEL KOSONG) -> self-healing", "empty",
         ["write=insert+update"], 0)
assert any("return=representation" in p for p in SEEN["post_prefers"]), \
    f"INSERT harus pakai Prefer return=representation, dapat: {SEEN['post_prefers']}"
assert SEEN["patch_paths"] and "id=eq.7" in SEEN["patch_paths"][0], \
    f"PATCH setelah insert harus id=eq.7, dapat: {SEEN['patch_paths']}"
print("  (verifikasi: POST Prefer =", SEEN["post_prefers"], ")")
print("  (verifikasi: PATCH URL =", SEEN["patch_paths"][0], ")")

srv.shutdown()
print("\nSEMUA TEST LULUS")