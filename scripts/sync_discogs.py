
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "Vinyl.Date.Night"
OUTPUT = Path("collection.json")
TOKEN = os.environ.get("DISCOGS_TOKEN", "").strip()

BASE_URL = (
    "https://api.discogs.com/users/"
    + urllib.parse.quote(USERNAME, safe="")
    + "/collection/folders/0/releases"
)

headers = {
    "User-Agent": "OldHeadRecords/1.0 (personal collection sync)",
    "Accept": "application/json",
}

if TOKEN:
    headers["Authorization"] = f"Discogs token={TOKEN}"


def fetch_page(page):
    params = urllib.parse.urlencode({
        "page": page,
        "per_page": 100,
        "sort": "added",
        "sort_order": "desc",
    })
    url = f"{BASE_URL}?{params}"

    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=40) as response:
                return json.load(response)

        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))

        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


def main():
    records = []
    page = 1

    while True:
        data = fetch_page(page)
        releases = data.get("releases", [])

        for item in releases:
            info = item.get("basic_information") or {}

            artists = info.get("artists") or []
            artist = ", ".join(
                a.get("name", "") for a in artists
                if a.get("name")
            ) or "Unknown Artist"

            labels = info.get("labels") or []
            formats = info.get("formats") or []

            format_text = ", ".join(
                f.get("name", "") for f in formats
                if f.get("name")
            )

            record = {
                "id": str(item.get("id") or info.get("id") or ""),
                "instance_id": str(item.get("instance_id") or ""),
                "artist": artist,
                "title": info.get("title") or "Untitled",
                "year": str(info.get("year") or ""),
                "format": format_text,
                "label": ", ".join(
                    label.get("name", "") for label in labels
                    if label.get("name")
                ),
                "notes": "",
                "folder": str(item.get("folder_id") or "0"),
                "cover_image": info.get("cover_image") or "",
                "thumb": info.get("thumb") or "",
                "master_id": info.get("master_id") or 0,
            }

            records.append(record)

        pagination = data.get("pagination") or {}
        total_pages = int(pagination.get("pages") or 1)

        print(f"Retrieved page {page} of {total_pages}")

        if page >= total_pages:
            break

        page += 1
        time.sleep(1.2)

    if not records:
        raise RuntimeError(
            "Discogs returned no records. Existing data was not replaced."
        )

    payload = {
        "username": USERNAME,
        "last_synced": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Saved {len(records)} records to {OUTPUT}")


if __name__ == "__main__":
    main()
