"""Bounded FRED v2 context. Not a real-time economic surprise/trade signal."""
import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENDPOINT = 'https://api.stlouisfed.org/fred/v2/release/observations'
RELEASE = 221
SELECTED = {'NFCI', 'ANFCI'}
MAX_PAGES = 12
PAGE_LIMIT = 5000
MAX_BYTES = 8_000_000


def instant(text):
    d = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if d.utcoffset() is None:
        raise ValueError('timezone required')
    return d


def collect(fetch, received_at, previous=None):
    now = instant(received_at)
    cursor, cursors, size, seen, updates, metadata = None, set(), 0, {}, {}, {}
    for page_no in range(MAX_PAGES):
        page = fetch(cursor)
        size += len(json.dumps(page).encode())
        if size > MAX_BYTES:
            raise ValueError('byte budget exceeded')
        if page.get('release', {}).get('release_id') != RELEASE:
            raise ValueError('wrong release')
        more = page.get('has_more')
        if more not in (True, False, 'true', 'false') or isinstance(more, int) and not isinstance(more, bool):
            raise ValueError('invalid pagination')
        if not isinstance(page.get('series'), list):
            raise ValueError('missing series')
        count = 0
        for series in page['series']:
            sid = series['series_id']
            updated = series['last_updated']
            if instant(updated) > now:
                raise ValueError('future update')
            if sid in updates and updates[sid] != updated:
                raise ValueError('mixed release updates; retry whole release next run')
            updates[sid] = updated
            observations = series['observations']
            count += len(observations)
            if count > PAGE_LIMIT:
                raise ValueError('observation limit exceeded')
            if sid not in SELECTED:
                continue
            meta = {k: series.get(k) for k in ('title', 'frequency', 'units', 'seasonal_adjustment', 'copyright_id')}
            if sid in metadata and metadata[sid] != meta:
                raise ValueError('metadata changed across pages')
            metadata[sid] = meta
            for obs in observations:
                day = obs['date']
                if date.fromisoformat(day) > now.date():
                    raise ValueError('future observation')
                value = obs['value']
                if not isinstance(value, str):
                    raise ValueError('value must be string')
                if value != '.':
                    try:
                        if not Decimal(value).is_finite():
                            raise ValueError('nonfinite value')
                    except InvalidOperation as error:
                        raise ValueError('invalid value') from error
                key = (sid, day)
                if key in seen and seen[key] != value:
                    raise ValueError('conflicting duplicate')
                seen[key] = value
        if more in (False, 'false'):
            break
        cursor = page.get('next_cursor')
        if not isinstance(cursor, str) or not cursor or cursor in cursors or len(cursor) > 1000:
            raise ValueError('invalid/repeated cursor')
        cursors.add(cursor)
    else:
        raise ValueError('page budget exceeded; partial result rejected')
    records = []
    old = {r['seriesId']: r for r in (previous or {}).get('records', [])} if (previous or {}).get('status') == 'AVAILABLE' else {}
    for sid in sorted(SELECTED):
        rows = sorted((day, value) for (name, day), value in seen.items() if name == sid)
        if not rows:
            raise ValueError('selected series missing')
        latest = rows[-1]
        history_hash = hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()
        records.append({'seriesId': sid, 'observationDate': latest[0], 'value': None if latest[1] == '.' else latest[1],
                        'seriesUpdatedAt': updates[sid], 'availableAt': received_at,
                        'historyHash': history_hash, 'changedSincePrevious': None if sid not in old else old[sid]['historyHash'] != history_hash,
                        'metadata': metadata[sid], 'sourceUrl': 'https://fred.stlouisfed.org/series/' + sid,
                        'attribution': 'Federal Reserve Bank of Chicago, retrieved via FRED, Federal Reserve Bank of St. Louis'})
    return {'schema': 'release-context/1', 'releaseId': RELEASE, 'status': 'AVAILABLE', 'receivedAt': received_at,
            'complete': True, 'pages': page_no + 1, 'records': records, 'signalAuthority': False,
            'source': ENDPOINT, 'note': 'Latest revised history; not an as-released vintage or surprise. Available only at collection time.'}


def refresh(out='feed/release-context.json'):
    path = Path(out)
    previous = json.loads(path.read_text()) if path.exists() else None
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    key = os.environ.get('FRED_API_KEY', '')
    def fetch(cursor):
        params = {'release_id': RELEASE, 'format': 'json', 'limit': PAGE_LIMIT}
        if cursor:
            params['next_cursor'] = cursor
        request = Request(ENDPOINT + '?' + urlencode(params), headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'agentic-feed-research/1'})
        # Bounded transport, no logging URLs/headers or server error bodies.
        for attempt in range(3):
            try:
                with urlopen(request, timeout=20) as response:
                    raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('response too large')
                return json.loads(raw)
            except ValueError:
                raise
            except Exception:
                if attempt == 2:
                    raise RuntimeError('FRED transport failed') from None
                time.sleep(2 ** attempt)
    try:
        if not key:
            raise ValueError('missing key')
        snapshot = collect(fetch, now, previous)
        # Actual completion time is the earliest consumer availability.
        completed = datetime.now(timezone.utc).isoformat(timespec='seconds')
        snapshot['receivedAt'] = completed
        for record in snapshot['records']:
            record['availableAt'] = completed
    except Exception:
        snapshot = {'schema': 'release-context/1', 'releaseId': RELEASE, 'status': 'UNAVAILABLE', 'receivedAt': now,
                    'complete': False, 'records': [], 'signalAuthority': False,
                    'reason': 'Fetch/validation failed; no partial or previous values promoted'}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(snapshot, indent=2) + '\n')
    temp.replace(path)
    print('FRED v2 context:', snapshot['status'])
    return snapshot


if __name__ == '__main__':
    refresh()
