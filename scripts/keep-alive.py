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
    rest_url = f"{project['url']}/rest/v1/{TABLE}?select={COLUMN}&limit=1"
    req = urllib.request.Request(rest_url)
    req.add_header("apikey", project["key"])
    req.add_header("Authorization", f"Bearer {project['key']}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return {
                "project": project["name"],
                "status": "ok",
                "rows": len(data),
                "code": resp.status,
            }
    except urllib.error.HTTPError as e:
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
        color = 0xFF0000
        title = f":warning: Supabase Keep-Alive — {failed}/{total} FAILED  ({channel})"

    fields = []
    for r in results:
        icon = ":white_check_mark:" if r["status"] == "ok" else ":x:"
        value = f"Status: `{r['status'].upper()}` | Code: `{r['code']}`"
        if r["status"] == "error":
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
        icon = "OK" if r["status"] == "ok" else "FAIL"
        print(f"  [{icon}] {r['project']}")
        if r["status"] == "error":
            print(f"         {r['message']}")
            failed += 1

    print("-" * 50)
    print(f"Done: {len(results) - failed}/{len(results)} success")

    send_discord(results, failed)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
