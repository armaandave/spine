"""Seed the test backend's dev user (port 8010, test accounts only) with airing shows it is caught up on, so Spine
Dev's Up next tile has rows: each show is set to Watching and every aired episode marked watched, through the API.

Run it through `scripts/ios/test-backend.sh seed-up-next`. Shows leave Up next as their episodes air, so run it again
when the tile empties: marking watched catches them up again."""
import json
import time
import urllib.error
import urllib.request

base = "http://127.0.0.1:8010/api/v1"
SHOWS = {
    "The Simpsons": "456",
    "South Park": "2190",
    "Grey's Anatomy": "1416",
    "Saturday Night Live": "1667",
    "Survivor": "14658",
}

req = urllib.request.Request(
    base + "/auth/dev-sign-in/", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
)
tok = json.load(urllib.request.urlopen(req))
access = tok.get("access") or tok.get("access_token") or (tok.get("tokens") or {}).get("access")
headers = {"Authorization": "Bearer " + access, "Content-Type": "application/json"}


def call(method, path, body=None):
    data = json.dumps(body or {}).encode() if method != "GET" else None
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()[:300]


for title, media_id in SHOWS.items():
    for action in ("watching", "mark_watched"):
        status, body = call("POST", f"/tracking/tmdb/tv/{media_id}/actions/{action}/")
        state = body.get("tv", body.get("tracking", {})) if isinstance(body, dict) else body
        caught = state.get("caught_up") if isinstance(state, dict) else None
        print(f"{title}: {action} -> {status} caught_up={caught}")
        time.sleep(2)

status, body = call("GET", "/tracking/up-next/")
rows = body.get("results", []) if isinstance(body, dict) else body
print("up-next:", status, [(row["media"]["title"], (row.get("airing") or {}).get("air_date")) for row in rows]
      if isinstance(rows, list) else rows)
