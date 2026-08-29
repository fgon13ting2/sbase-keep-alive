#!/usr/bin/env python3
"""
Supabase Keep-Alive Script
Bisa jalan di: GitHub Actions, cron lokal, Hermes

Config via environment variables:
  SUPABASE_URL_1, SUPABASE_KEY_1, SUPABASE_NAME_1
  SUPABASE_URL_2, SUPABASE_KEY_2, SUPABASE_NAME_2
  ...dst
  KEEP_ALIVE_TABLE (default: keep-alive)
  KEEP_ALIVE_COLUMN (default: name)
  DISCORD_WEBHOOK  (optional: Discord webhook URL for notifications)
  DISCORD_PING_ALL (optional: set to "true" to @everyone on failure)
"""
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from urllib.parse import urlparse

# ponytail: load .env dari root repo / cwd untuk test lokal — env vars asli
# selalu menang (setdefault); di GitHub Actions .env tidak ada, jadi no-op.
for _dir in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.getcwd()):
    _p = os.path.join(_dir, ".env")
    if os.path.isfile(_p):
        for _l in open(_p):
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _v = _l.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
        break

TABLE = os.environ.get("KEEP_ALIVE_TABLE", "keep-alive")
COLUMN = os.environ.get("KEEP_ALIVE_COLUMN", "name")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DISCORD_PING_ALL = os.environ.get("DISCORD_PING_ALL", "").lower() == "true"

# Optional: send only on failure
ONLY_ON_FAILURE = os.environ.get("DISCORD_ONLY_ON_FAILURE", "false").lower() == "true"


def get_projects():
    projects = []
    for i in range(1, 100):
        url = os.environ.get(f"SUPABASE_URL_{i}")
        key = os.environ.get(f"SUPABASE_KEY_{i}")
        if not url or not key:
            break
        projects.append({
            "name": os.environ.get(f"SUPABASE_NAME_{i}", f"project-{i}"),
            "url": url.rstrip("/"),
            "key": key,
        })
    return projects


def ping_project(project):
    rest_url = f"{project['url']}/rest/v1/{TABLE}?select=id,{COLUMN}&limit=1"
    req = urllib.request.Request(rest_url)
    req.add_header("apikey", project["key"])
    req.add_header("Authorization", f"Bearer {project['key']}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # 540 = status code resmi "project paused" dari Supabase
        if e.code == 540:
            return {
                "project": project["name"],
                "status": "paused",
                "message": "PROJECT PAUSED — buka Supabase Dashboard → Resume project!",
                "code": e.code,
            }
        return {
            "project": project["name"],
            "status": "error",
            "message": str(e),
            "code": e.code,
        }
    except Exception as e:
        return {
            "project": project["name"],
            "status": "error",
            "message": str(e),
            "code": 0,
        }

    # ponytail: best-effort WRITE setelah SELECT supaya activity "nyata".
    # - SELECT include `id` supaya PATCH bisa pakai WHERE (tanpa WHERE,
    #   PostgREST error 400 "UPDATE requires a WHERE clause" -> no-op).
    # - Tabel kosong -> INSERT (return=representation) ambil id, lalu update
    #   (self-healing: tabel yang belum pernah disetup pun jadi aman).
    # - RLS nolak tulis (401/403/409) -> tetap bukan error: SELECT kehitung.
    wrote = "no"
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        hdrs = {
            "apikey": project["key"],
            "Authorization": f"Bearer {project['key']}",
            "Content-Type": "application/json",
        }
        row_id = None
        if data and isinstance(data[0], dict):
            row_id = data[0].get("id")
        if row_id is None and not data:
            body = json.dumps({COLUMN: f"keep-alive {stamp}"}).encode("utf-8")
            req_ins = urllib.request.Request(
                f"{project['url']}/rest/v1/{TABLE}",
                data=body, method="POST",
                headers=dict(hdrs, Prefer="return=representation"),
            )
            with urllib.request.urlopen(req_ins, timeout=15) as resp_ins:
                ins = json.loads(resp_ins.read())
                wrote = "insert"
                if ins and isinstance(ins, list) and isinstance(ins[0], dict):
                    row_id = ins[0].get("id")
        if row_id is not None:
            body = json.dumps({COLUMN: f"keep-alive {stamp}"}).encode("utf-8")
            req_up = urllib.request.Request(
                f"{project['url']}/rest/v1/{TABLE}?id=eq.{row_id}",
                data=body, method="PATCH",
                headers=dict(hdrs, Prefer="return=minimal"),
            )
            with urllib.request.urlopen(req_up, timeout=15):
                wrote = "insert+update" if wrote == "insert" else "update"
    except Exception:
        pass  # tulis ditolak = oke, baca sudah dihitung sebagai activity

    return {
        "project": project["name"],
        "status": "ok",
        "rows": len(data),
        "code": 200,
        "wrote": wrote,
    }


def get_channel_name(webhook_url):
    """Extract channel name from webhook URL for display."""
    try:
        parts = webhook_url.rstrip("/").split("/")
        return f"#{parts[-3]}" if len(parts) >= 3 else "unknown"
    except Exception:
        return "unknown"


def send_discord_webhook(webhook_url, results, failed, ping_all, thread_name=None):
    """Send to one specific webhook/thread."""
    total = len(results)
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_display = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    channel = get_channel_name(webhook_url)

    if failed == 0:
        color = 0x00FF00
        title = f":white_check_mark: Supabase Keep-Alive — All {total} OK  ({channel})"
    else:
        paused = [r["project"] for r in results if r["status"] == "paused"]
        if paused:
            color = 0xFF0000
            title = f":red_circle: SUPERGENTING — PROJECT PAUSED: {', '.join(paused)}  ({channel})"
        else:
            color = 0xFF0000
            title = f":warning: Supabase Keep-Alive — {failed}/{total} FAILED  ({channel})"

    fields = []
    for r in results:
        icon = ":white_check_mark:" if r["status"] == "ok" else (
            ":red_circle:" if r["status"] == "paused" else ":x:")
        value = f"Status: `{r['status'].upper()}` | Code: `{r['code']}`"
        if r.get("wrote") is not None and r["status"] == "ok":
            w = r["wrote"]
            value += f" | Write: `{w if w != 'no' else 'no (baca saja)'}`"
        if r["status"] in ("error", "paused"):
            value += f"\n```{r['message'][:256]}```"
        fields.append({"name": f"{icon} {r['project']}", "value": value, "inline": False})

    embed = {
        "title": title,
        "color": color,
        "timestamp": now_iso,
        "fields": fields,
        "footer": {"text": f"Table: {TABLE} | Column: {COLUMN} | {now_display}"},
    }

    payload = {"embeds": [embed]}

    # Forum channel needs thread_name or thread_id
    if thread_name:
        payload["thread_name"] = thread_name

    if failed > 0 and ping_all:
        payload["content"] = "@everyone"

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "SupabaseKeepAlive/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"  Discord → {channel} (HTTP {resp.status})")
    except Exception as e:
        print(f"  Discord → {channel} FAILED: {e}")


def send_discord(results, failed):
    """Send notifications using master webhook + per-project thread IDs."""
    if not DISCORD_WEBHOOK:
        return

    if ONLY_ON_FAILURE and failed == 0:
        return

    # Group results by thread
    # DISCORD_WEBHOOK_TH_1 → thread_id for project-1 (post to existing thread)
    # If TH_ has no value → create new post in forum with thread_name (if NC_ set)
    groups: dict[str, list] = {}

    for i, r in enumerate(results, start=1):
        thread_id = os.environ.get(f"DISCORD_WEBHOOK_TH_{i}", "").strip()
        if not thread_id:
            thread_id = "__new__"  # fallback: create new post

        if thread_id not in groups:
            groups[thread_id] = []
        groups[thread_id].append(r)

    # default thread_name for new posts
    default_thread_name = os.environ.get("DISCORD_THREAD_NAME", "Supabase Keep-Alive")

    # Kirim per thread/group
    for thread_id, group_results in groups.items():
        url = DISCORD_WEBHOOK
        thread_name = None

        if thread_id == "__new__":
            # Forum channel: bikin post baru
            thread_name = default_thread_name
        else:
            # Post ke thread yang udah ada
            url = f"{DISCORD_WEBHOOK}?thread_id={thread_id}"

        group_failed = sum(1 for r in group_results if r["status"] != "ok")
        send_discord_webhook(url, group_results, group_failed, DISCORD_PING_ALL, thread_name)


def main():
    projects = get_projects()

    if not projects:
        print("ERROR: No Supabase projects configured!", file=sys.stderr)
        print("Set SUPABASE_URL_1, SUPABASE_KEY_1 env vars.", file=sys.stderr)
        sys.exit(1)

    print(f"Pinging {len(projects)} Supabase project(s)...")
    print(f"Table: {TABLE} | Column: {COLUMN}")
    print("-" * 50)

    results = [ping_project(p) for p in projects]

    failed = 0
    for r in results:
        icon = "OK" if r["status"] == "ok" else ("PAUSED" if r["status"] == "paused" else "FAIL")
        extra = f" [write={r.get('wrote')}]" if r["status"] == "ok" else ""
        print(f"  [{icon}] {r['project']}{extra}")
        if r["status"] in ("error", "paused"):
            print(f"         {r['message']}")
            failed += 1

    print("-" * 50)
    print(f"Done: {len(results) - failed}/{len(results)} success")

    send_discord(results, failed)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
