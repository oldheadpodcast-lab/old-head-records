
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

COLLECTION_FILE = Path("collection.json")
OUTPUT_FILE = Path("album_metadata.json")

HEADERS = {
    "User-Agent": "OldHeadRecords/1.0 (personal music library)",
    "Accept": "application/json",
}

def get_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

def clean_artist(name):
    return re.sub(r"\s+\(\d+\)$", "", name).strip()

def search_musicbrainz(artist, title):
    query = f'artist:"{artist}" AND releasegroup:"{title}"'
    url = (
        "https://musicbrainz.org/ws/2/release-group/?"
        + urllib.parse.urlencode({
            "query": query,
            "fmt": "json",
            "limit": 5,
        })
    )

    data = get_json(url)

    for result in data.get("release-groups", []):
        result_title = result.get("title", "").casefold()

        if result_title == title.casefold():
            return {
                "musicbrainz_id": result.get("id"),
                "original_year": (
                    result.get("first-release-date", "")[:4]
                ),
                "release_type": result.get("primary-type", ""),
            }

    return None

def wikipedia_summary(artist, title):
    search = f"{title} {artist} album"

    url = (
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": search,
            "srlimit": 3,
            "format": "json",
        })
    )

    data = get_json(url)

    for result in data.get("query", {}).get("search", []):
        page_title = result.get("title", "")

        if title.casefold() not in page_title.casefold():
            continue

        summary_url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(page_title.replace(" ", "_"))
        )

        try:
            summary = get_json(summary_url)
        except Exception:
            continue

        description = summary.get("extract", "")

        if description:
            return {
                "description": description,
                "source": summary.get("content_urls", {})
                    .get("desktop", {}).get("page", ""),
            }

    return None

def main():
    collection = json.loads(
        COLLECTION_FILE.read_text(encoding="utf-8")
    )

    records = collection.get("records", [])[:5]

    if OUTPUT_FILE.exists():
        metadata = json.loads(
            OUTPUT_FILE.read_text(encoding="utf-8")
        )
    else:
        metadata = {}

    seen = set()

    for record in records:
        artist = clean_artist(record.get("artist", ""))
        title = record.get("title", "").strip()
        key = f"{artist.casefold()}|{title.casefold()}"

        if not artist or not title or key in seen:
            continue

        seen.add(key)

        if key in metadata:
            continue

        print(f"Researching: {artist} - {title}")

        entry = {
            "artist": artist,
            "title": title,
            "original_year": "",
            "description": "",
            "source": "",
            "musicbrainz_id": "",
        }

        try:
            mb = search_musicbrainz(artist, title)

            if mb:
                entry["original_year"] = mb["original_year"]
                entry["musicbrainz_id"] = mb["musicbrainz_id"]

                wiki = wikipedia_summary(artist, title)

                if wiki:
                    entry["description"] = wiki["description"]
                    entry["source"] = wiki["source"]

        except Exception as error:
            print(f"Research unavailable: {error}")

        metadata[key] = entry

        OUTPUT_FILE.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        time.sleep(1.2)

    print(f"Saved metadata for {len(metadata)} unique titles.")

if __name__ == "__main__":
    main()
