#!/usr/bin/env python3
"""
Supabase Keep-Alive Script
Bisa jalan di: GitHub Actions, Vercel, cron lokal, Hermes

Config via environment variables:
  SUPABASE_URL_1, SUPABASE_KEY_1, SUPABASE_NAME_1
  SUPABASE_URL_2, SUPABASE_KEY_2, SUPABASE_NAME_2
  ...dst
  KEEP_ALIVE_TABLE (default: keep-alive)
  KEEP_ALIVE_COLUMN (default: name)
"""
import os
import sys
import json
import urllib.request
import urllib.error

TABLE = os.environ.get("KEEP_ALIVE_TABLE", "keep-alive")
COLUMN = os.environ.get("KEEP_ALIVE_COLUMN", "name")


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

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
