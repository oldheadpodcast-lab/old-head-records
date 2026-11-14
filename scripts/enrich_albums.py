
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COLLECTION_FILE = Path("collection.json")
OUTPUT_FILE = Path("album_metadata.json")
TEST_LIMIT = 25

HEADERS = {
    "User-Agent": "OldHeadRecords/1.0 (personal vinyl catalog)",
    "Accept": "application/json",
}


def get_json(url):
    max_attempts = 5

    for attempt in range(max_attempts):
        try:
            request = urllib.request.Request(
                url,
                headers=HEADERS
            )

            with urllib.request.urlopen(
                request,
                timeout=30
            ) as response:
                return json.load(response)

        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise

            if attempt == max_attempts - 1:
                raise

            wait = min(10 * (2 ** attempt), 120)

            retry_after = error.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = max(wait, int(retry_after))

            print(
                f"HTTP {error.code}: "
                f"Retrying in {wait} seconds..."
            )
            time.sleep(wait)

        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == max_attempts - 1:
                raise

            wait = min(10 * (2 ** attempt), 120)

            print(
                f"Connection error: {error}. "
                f"Retrying in {wait} seconds..."
            )
            time.sleep(wait)


def normalize(value):
    value = value.casefold()
    value = re.sub(r"[^\w]+", " ", value)
    return " ".join(value.split())

def clean_artist(value):
    return re.sub(r"\s+\(\d+\)$", "", value).strip()

def musicbrainz_request(path, params=None):
    url = "https://musicbrainz.org/ws/2/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    # MusicBrainz requests must be rate-limited.
    time.sleep(1.2)
    return get_json(url)


def search_musicbrainz(artist, title):
    query = f'artist:"{artist}" AND releasegroup:"{title}"'

    data = musicbrainz_request(
        "release-group/",
        {
            "query": query,
            "fmt": "json",
            "limit": 100,
        },
    )

    matches = []

    for group in data.get("release-groups", []):
        if normalize(group.get("title", "")) != normalize(title):
            continue

        credits = group.get("artist-credit", [])

        credited_artists = [
            item.get("artist", {}).get("name", "")
            for item in credits
            if isinstance(item, dict)
        ]

        if normalize(artist) not in [
            normalize(name) for name in credited_artists
        ]:
            continue

        matches.append(group)

    if not matches:
        return None

    # Prefer full-length albums, then EPs,
    # followed by other release types.
    type_priority = {
        "Album": 0,
        "EP": 1,
        "Broadcast": 2,
        "Other": 3,
        "Single": 4,
    }

    matches.sort(
        key=lambda group: (
            type_priority.get(
                group.get("primary-type", ""),
                5,
            ),
            group.get("first-release-date") or "9999",
        )
    )

    best = matches[0]

    return {
        "id": best.get("id", ""),
        "original_year": best.get(
            "first-release-date", ""
        )[:4],
        "release_type": best.get("primary-type", ""),
    }

    query = f'artist:"{artist}" AND releasegroup:"{title}"'

    data = musicbrainz_request(
        "release-group/",
        {
            "query": query,
            "fmt": "json",
            "limit": 25,
        },
    )

    for group in data.get("release-groups", []):
        if normalize(group.get("title", "")) != normalize(title):
            continue

        credits = group.get("artist-credit", [])
        credited_artists = [
            item.get("artist", {}).get("name", "")
            for item in credits
            if isinstance(item, dict)
        ]

        if normalize(artist) not in [
            normalize(name) for name in credited_artists
        ]:
            continue

        return {
            "id": group.get("id", ""),
            "original_year": group.get(
                "first-release-date", ""
            )[:4],
            "release_type": group.get("primary-type", ""),
        }

    return None


def get_wikipedia_description(mbid, artist, title):
    data = musicbrainz_request(
        f"release-group/{mbid}",
        {"inc": "url-rels", "fmt": "json"},
    )

    def fetch_summary(page_title):
        url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(
                urllib.parse.unquote(page_title).replace(" ", "_"),
                safe="()_",
            )
        )
        return get_json(url)

    # First use Wikipedia links attached to MusicBrainz.
    for relation in data.get("relations", []):
        if relation.get("type") != "wikipedia":
            continue

        page_url = relation.get("url", {}).get("resource", "")

        if "en.wikipedia.org/wiki/" not in page_url:
            continue

        page_title = page_url.split("/wiki/", 1)[1]

        try:
            summary = fetch_summary(page_title)
            description = summary.get("extract", "").strip()

            if description and summary.get("type") != "disambiguation":
                return {
                    "description": description,
                    "source": page_url,
                }
        except Exception as error:
            print(f"Wikipedia linked-page error: {error}")

    # Search Wikipedia when MusicBrainz has no usable link.
    search_url = (
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode({
            "action": "query",
            "list": "search",
            "srsearch": f'intitle:"{title}" "{artist}"',
            "srlimit": 10,
            "format": "json",
        })
    )

    try:
        results = get_json(search_url)
    except Exception as error:
        print(f"Wikipedia search error: {error}")
        return None

    for result in results.get("query", {}).get("search", []):
        page_title = result.get("title", "")

        if normalize(title) not in normalize(page_title):
            continue

        try:
            summary = fetch_summary(page_title)
        except Exception:
            continue

        description = summary.get("extract", "").strip()

        if not description or summary.get("type") == "disambiguation":
            continue

        # Confirm the article mentions the artist and album.
        if normalize(artist) not in normalize(description):
            continue

        if normalize(title) not in normalize(
            summary.get("title", "")
        ):
            continue

        source = summary.get("content_urls", {}).get(
            "desktop", {}
        ).get("page", "")

        if source:
            return {
                "description": description,
                "source": source,
            }

    return None

    # Retrieve the Wikipedia page linked to the
    # exact MusicBrainz release group.
    data = musicbrainz_request(
        f"release-group/{mbid}",
        {
            "inc": "url-rels",
            "fmt": "json",
        },
    )

    wikipedia_urls = []

    for relation in data.get("relations", []):
        if relation.get("type") != "wikipedia":
            continue

        url = relation.get("url", {}).get("resource", "")

        if url.startswith("https://en.wikipedia.org/wiki/"):
            wikipedia_urls.append(url)

    for page_url in wikipedia_urls:
        page_title = page_url.split("/wiki/", 1)[1]

        summary_url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + page_title
        )

        try:
            summary = get_json(summary_url)
        except (urllib.error.URLError, ValueError):
            continue

        description = summary.get("extract", "").strip()

        if description and summary.get("type") != "disambiguation":
            return {
                "description": description,
                "source": page_url,
            }

    return None

def research_album(artist, title):
    entry = {
        "artist": artist,
        "title": title,
        "original_year": "",
        "release_type": "",
        "description": "",
        "source": "",
        "musicbrainz_id": "",
        "status": "not_found",
    }

    match = search_musicbrainz(artist, title)

    if not match:
        return entry

    entry["musicbrainz_id"] = match["id"]
    entry["original_year"] = match["original_year"]
    entry["release_type"] = match["release_type"]
    entry["status"] = "matched"

    wiki = get_wikipedia_description(match["id"], artist, title)

    if wiki:
        entry["description"] = wiki["description"]
        entry["source"] = wiki["source"]
        entry["status"] = "described"

    return entry

def main():
    collection = json.loads(
        COLLECTION_FILE.read_text(encoding="utf-8")
    )

    records = collection.get("records", [])[:TEST_LIMIT]

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

        if not artist or not title:
            continue

        key = f"{artist.casefold()}|{title.casefold()}"

        if key in seen:
            continue

        seen.add(key)

        existing = metadata.get(key, {})

        # Keep completed results.
        if (
            existing.get("status") == "described"
            and existing.get("description")
        ):
            continue

        print(f"Researching: {artist} - {title}")

        try:
            result = research_album(artist, title)
        except Exception as error:
            print(f"Research error: {error}")
            # Preserve any previous successful data.
            continue

        if (
            existing.get("description")
            and not result.get("description")
        ):
            continue

        metadata[key] = result

        OUTPUT_FILE.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"Processed {len(seen)} unique test albums.")
    print(f"Stored {len(metadata)} metadata entries.")

if __name__ == "__main__":
    main()
