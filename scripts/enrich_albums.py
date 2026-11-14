"""Conservative album metadata research for Old Head Records.

Batch mode: research up to 25 pending records per run and save progress.
Does not change the website or the Discogs collection.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

COLLECTION_FILE = Path('collection.json')
OUTPUT_FILE = Path('album_metadata.json')
TEST_LIMIT = 25
HEADERS = {'User-Agent': 'OldHeadRecords/1.1 (personal music catalog; GitHub oldheadpodcast-lab/old-head-records)', 'Accept': 'application/json'}
LAST_MB_REQUEST = 0.0


def normalize(value):
    return ' '.join(re.sub(r'[^\w]+', ' ', str(value).casefold()).split())


def clean_artist(value):
    return re.sub(r'\s+\(\d+\)$', '', str(value)).strip()


def get_json(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            delay = min(5 * 2 ** attempt, 60)
            retry_after = exc.headers.get('Retry-After')
            if retry_after:
                if retry_after.isdigit():
                    delay = max(delay, min(int(retry_after), 120))
                else:
                    try:
                        delay = max(delay, min(max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()), 120))
                    except (ValueError, TypeError, OverflowError):
                        pass
            print(f'HTTP {exc.code}; retrying in {delay:.0f}s', flush=True)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise
            delay = 5 * 2 ** attempt
            print(f'Connection issue: {exc}; retrying in {delay}s', flush=True)
            time.sleep(delay)


def musicbrainz_request(path, params):
    global LAST_MB_REQUEST
    wait = 1.2 - (time.monotonic() - LAST_MB_REQUEST)
    if wait > 0:
        time.sleep(wait)
    LAST_MB_REQUEST = time.monotonic()
    url = 'https://musicbrainz.org/ws/2/' + path + '?' + urllib.parse.urlencode(params)
    return get_json(url)


def search_musicbrainz(artist, title):
    query = f'artist:"{artist}" AND releasegroup:"{title}"'
    data = musicbrainz_request('release-group/', {'query': query, 'fmt': 'json', 'limit': 100})
    matches = []
    for group in data.get('release-groups', []):
        if normalize(group.get('title', '')) != normalize(title):
            continue
        credits = [part.get('artist', {}).get('name', '') for part in group.get('artist-credit', []) if isinstance(part, dict)]
        if normalize(artist) not in [normalize(name) for name in credits]:
            continue
        matches.append(group)
    if not matches:
        return None
    # This is a provisional preference, not a verified Discogs release-type match.
    priority = {'Album': 0, 'EP': 1, 'Broadcast': 2, 'Other': 3, 'Single': 4}
    matches.sort(key=lambda item: (priority.get(item.get('primary-type'), 5), item.get('first-release-date') or '9999'))
    best = matches[0]
    return {'id': best.get('id', ''), 'original_year': best.get('first-release-date', '')[:4], 'release_type': best.get('primary-type', '')}


def valid_wikipedia_summary(summary, artist, title):
    """Reject non-release pages; deliberately favor precision over coverage."""
    if summary.get('type') == 'disambiguation':
        return False
    article_title = normalize(summary.get('title', ''))
    album_title = normalize(title)
    description = normalize(summary.get('extract', ''))
    page_type = normalize(summary.get('description', ''))
    if not album_title or not description or not page_type:
        return False
    # Avoid substring matches like 'Danzig' -> 'Free City of Danzig'.
    if article_title != album_title and not article_title.startswith(album_title + ' album') and not article_title.startswith(album_title + ' ep') and not article_title.startswith(album_title + ' soundtrack'):
        return False
    release_terms = ('album', 'ep', 'extended play', 'single', 'soundtrack', 'compilation')
    if not any(re.search(r'\b' + re.escape(term) + r'\b', page_type) for term in release_terms):
        return False
    if any(re.search(r'\b' + re.escape(term) + r'\b', page_type) for term in ('tour', 'city', 'film', 'television series', 'video game')):
        return False
    # A clear artist mention in the article is required.
    return normalize(artist) in description


def get_wikipedia_description(mbid, artist, title):
    def fetch_summary(page_title):
        endpoint = 'https://en.wikipedia.org/api/rest_v1/page/summary/'
        return get_json(endpoint + urllib.parse.quote(urllib.parse.unquote(page_title).replace(' ', '_'), safe='()_'))

    data = musicbrainz_request(f'release-group/{mbid}', {'inc': 'url-rels', 'fmt': 'json'})
    candidates = []
    for relation in data.get('relations', []):
        url = relation.get('url', {}).get('resource', '')
        if relation.get('type') == 'wikipedia' and url.startswith('https://en.wikipedia.org/wiki/'):
            candidates.append(url.split('/wiki/', 1)[1])
    try:
        search_url = 'https://en.wikipedia.org/w/api.php?' + urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': f'intitle:"{title}" "{artist}"', 'srlimit': 10, 'format': 'json'})
        results = get_json(search_url)
        candidates.extend(item['title'] for item in results.get('query', {}).get('search', []) if item.get('title'))
    except Exception as exc:
        print(f'Wikipedia search unavailable for {artist} - {title}: {exc}', flush=True)
    for candidate in dict.fromkeys(candidates):
        try:
            summary = fetch_summary(candidate)
        except Exception as exc:
            print(f'Wikipedia page unavailable: {exc}', flush=True)
            continue
        if valid_wikipedia_summary(summary, artist, title):
            source = summary.get('content_urls', {}).get('desktop', {}).get('page', '')
            if source:
                return {'description': summary['extract'].strip(), 'source': source}
    return None


def research_album(artist, title):
    entry = {'artist': artist, 'title': title, 'original_year': '', 'release_type': '', 'description': '', 'source': '', 'musicbrainz_id': '', 'status': 'not_found'}
    match = search_musicbrainz(artist, title)
    if not match:
        return entry
    entry.update({'musicbrainz_id': match['id'], 'original_year': match['original_year'], 'release_type': match['release_type'], 'status': 'matched'})
    wiki = get_wikipedia_description(match['id'], artist, title)
    if wiki:
        entry.update(wiki)
        entry['status'] = 'described'
    return entry


def main():
    collection = json.loads(COLLECTION_FILE.read_text(encoding='utf-8'))
    records = collection.get('records', [])
    if OUTPUT_FILE.exists():
        metadata = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
        if not isinstance(metadata, dict):
            raise ValueError('album_metadata.json must contain a JSON object')
    else:
        metadata = {}

    attempted = 0
    seen = set()
    for record in records:
        artist = clean_artist(record.get('artist', ''))
        title = str(record.get('title', '')).strip()
        if not artist or not title:
            continue
        key = f'{artist.casefold()}|{title.casefold()}'
        if key in seen:
            continue
        seen.add(key)
        # Previously researched albums are retained; transient errors are retried.
        if metadata.get(key, {}).get('status') in ('described', 'matched', 'not_found'):
            continue
        if attempted >= TEST_LIMIT:
            break
        attempted += 1
        print(f'Researching: {artist} - {title}', flush=True)
        try:
            metadata[key] = research_album(artist, title)
        except Exception as exc:
            print(f'Research failed for {artist} - {title}: {exc}', flush=True)
            metadata[key] = {'artist': artist, 'title': title, 'original_year': '', 'release_type': '', 'description': '', 'source': '', 'musicbrainz_id': '', 'status': 'error'}
        OUTPUT_FILE.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(
        f'Attempted {attempted} albums this run; saved {len(metadata)} total; '
        f'descriptions: {sum(bool(v.get("description")) for v in metadata.values())}; '
        f'errors awaiting retry: {sum(v.get("status") == "error" for v in metadata.values())}',
        flush=True,
    )


if __name__ == '__main__':
    main()
