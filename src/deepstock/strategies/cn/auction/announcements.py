"""Public official disclosures, captured before normalization; no Tushare fallback."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import unquote, urljoin, urlsplit, parse_qs
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from deepstock.data.capture import capture_response
from deepstock.data.store import ROOT, DataStore, read_clean_csv

SHANGHAI = ZoneInfo('Asia/Shanghai')
SOURCES = ('cninfo', 'sse', 'szse')
URLS = {
    'cninfo': 'https://www.cninfo.com.cn/new/hisAnnouncement/query',
    'sse': 'https://query.sse.com.cn/security/stock/queryCompanyBulletin.do',
    'szse': 'https://www.szse.cn/api/disc/announcement/annList',
}
REFERERS = {'cninfo': 'https://www.cninfo.com.cn/', 'sse': 'https://www.sse.com.cn/',
            'szse': 'https://www.szse.cn/'}
DOCUMENT_HOSTS = {'static.cninfo.com.cn', 'www.cninfo.com.cn', 'www.sse.com.cn', 'static.sse.com.cn',
                  'www.szse.cn', 'disc.static.szse.cn'}


class AnnouncementUnavailable(RuntimeError):
    pass


class VerificationDeferred(AnnouncementUnavailable):
    """The round's verification budget is spent; this is not a failed attempt."""
    pass


def plain_title(value):
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]*>', '', str(value or '')))).strip()


def document_url(value):
    parsed = urlsplit(str(value))
    if (parsed.scheme != 'https' or parsed.hostname not in DOCUMENT_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise AnnouncementUnavailable('Disclosure document is outside official hosts')
    return 'https://' + parsed.hostname + unquote(parsed.path)


def identity(url, announcement_id=None):
    """Only official IDs/URLs establish identity; a title alone never merges events."""
    if announcement_id is not None and re.fullmatch(r'\d+', str(announcement_id)):
        return 'cninfo:' + str(announcement_id)
    parsed = urlsplit(str(url or ''))
    if parsed.hostname in {'static.cninfo.com.cn', 'www.cninfo.com.cn'}:
        match = re.search(r'/([0-9]+)\.pdf$', parsed.path, re.I)
        query = parse_qs(parsed.query).get('announcementId', [])
        number = match[1] if match else query[0] if query else ''
        if re.fullmatch(r'\d+', number):
            return 'cninfo:' + number
    if parsed.hostname in DOCUMENT_HOSTS:
        return 'url:' + document_url(url)
    return None


def _count(value):
    if isinstance(value, bool) or not str(value).isdigit():
        raise AnnouncementUnavailable('Missing/invalid pagination total')
    return int(value)


class OfficialAnnouncements:
    def __init__(self, client=None, *, capture=capture_response, pause=time.sleep,
                 page_size=100, max_pages=500, root=ROOT, max_documents=20,
                 document_seconds=60, monotonic=time.monotonic):
        self.client = client or httpx.Client(timeout=httpx.Timeout(18, connect=5), follow_redirects=False)
        self.owns_client = client is None
        self.capture = capture
        self.pause = pause
        self.page_size = page_size
        self.max_pages = max_pages
        self.root = Path(root)
        self.document_hashes = {}
        self.partial_versions = {}
        self.max_documents = max_documents
        self.document_seconds = document_seconds
        self.monotonic = monotonic
        self.document_deadline = None
        self.document_attempts = 0
        self.document_failures = set()
        self.document_aliases = {}

    def _document_remaining(self):
        if self.document_deadline is None:
            self.document_deadline = self.monotonic() + self.document_seconds
        remaining = self.document_deadline - self.monotonic()
        if remaining <= 0:
            raise VerificationDeferred('Disclosure verification time budget exhausted')
        return remaining

    def close(self):
        if self.owns_client:
            self.client.close()

    def page(self, source, day, page):
        if os.getenv('DEEPSTOCK_AUCTION_OFFLINE') == 'true':
            raise AnnouncementUnavailable('Offline auction study cannot call providers')
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': REFERERS[source]}
        if source == 'cninfo':
            # CNINFO silently resets pageNum when pageSize exceeds its 30-row cap.
            query = {'pageNum': str(page), 'pageSize': str(min(self.page_size, 30)), 'column': 'szse',
                     # The explicit time sort has unstable ties across pages;
                     # use the site's default ordering and verify every ID/total.
                     'tabName': 'fulltext', 'seDate': f'{day}~{day}', 'sortName': '',
                     'sortType': '', 'isHLtitle': 'false'}
            response = self.client.post(URLS[source], data=query, headers=headers)
        elif source == 'sse':
            query = {'isPagination': 'true', 'pageHelp.pageSize': self.page_size, 'pageHelp.pageNo': page,
                     'pageHelp.beginPage': page, 'pageHelp.endPage': page, 'pageHelp.cacheSize': 1,
                     'securityType': '0101,120100,020100,020200,120200', 'reportType': 'ALL',
                     'beginDate': day.isoformat(), 'endDate': day.isoformat()}
            response = self.client.get(URLS[source], params=query, headers=headers)
        else:
            query = {'pageNum': page, 'pageSize': min(self.page_size, 50),
                     'seDate': [day.isoformat(), day.isoformat()], 'channelCode': ['listedNotice_disc']}
            response = self.client.post(URLS[source], json=query, headers=headers)
        observed = datetime.now(SHANGHAI).isoformat(timespec='microseconds')
        try:
            payload = response.json()
        except ValueError:
            payload = {'non_json_body': response.text}
        version = self.capture(source.upper(), 'public-announcements', {
            'url': URLS[source], 'query': query, 'http_status': response.status_code,
            'observed_at': observed, 'response': payload,
        }, 'CN', restricted=True)
        response.raise_for_status()
        if not isinstance(payload, dict):
            raise AnnouncementUnavailable('Unexpected official response schema')
        if source == 'cninfo':
            total, rows = _count(payload.get('totalAnnouncement')), payload.get('announcements')
        elif source == 'sse':
            pagination = payload.get('pageHelp')
            if not isinstance(pagination, dict):
                raise AnnouncementUnavailable('Invalid SSE pagination schema')
            total, rows = _count(pagination.get('total')), payload.get('result')
        else:
            total, rows = _count(payload.get('announceCount')), payload.get('data')
        if rows is None and total == 0:
            rows = []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise AnnouncementUnavailable('Invalid disclosure rows')
        return rows, total, observed, version

    def normalize(self, source, native, day, observed, version):
        if source == 'cninfo':
            codes, names = [native['secCode']], [native.get('secName', '')]
            aid, key = native['announcementId'], str(native['announcementId'])
            title = native['announcementTitle']
            url = urljoin('https://static.cninfo.com.cn/', native['adjunctUrl'])
            published = datetime.fromtimestamp(int(native['announcementTime']) / 1000, SHANGHAI)
            disclosed_date = published.date()
        elif source == 'sse':
            codes, names = [native['SECURITY_CODE']], [native.get('SECURITY_NAME', '')]
            aid, key = None, native['URL']
            title, url = native['TITLE'], urljoin('https://www.sse.com.cn/', native['URL'])
            disclosed_date = date.fromisoformat(native['SSEDATE'][:10])
            # ADDDATE is retained as source metadata, never substituted for availability.
            published = None
        else:
            codes, names = native['secCode'], native.get('secName', [])
            if not isinstance(codes, list) or not isinstance(names, list):
                raise AnnouncementUnavailable('Invalid SZSE security references')
            aid, key = native.get('annId'), str(native['id'])
            title, url = native['title'], urljoin('https://www.szse.cn/', native['attachPath'])
            published = datetime.fromisoformat(native['publishTime']).replace(tzinfo=SHANGHAI)
            disclosed_date = published.date()
        if disclosed_date != day:
            raise AnnouncementUnavailable('Provider returned disclosures outside requested date')
        url = document_url(url)
        clean_title = plain_title(title)
        if not clean_title:
            raise AnnouncementUnavailable('Empty disclosure title')
        records = []
        for i, code in enumerate(codes):
            code = str(code)
            if not re.fullmatch(r'\d{6}', code):
                raise AnnouncementUnavailable('Invalid disclosure security code')
            # This strategy admits Shanghai/Shenzhen A shares, not fund/bond notices.
            if not re.fullmatch(r'[036]\d{5}', code):
                continue
            name = str(names[i]) if i < len(names) else ''
            headline = clean_title
            if source == 'szse' and name and headline.startswith((name + '：', name + ':')):
                headline = headline[len(name) + 1:].strip()
            records.append({'ts_code': code + ('.SH' if code.startswith('6') else '.SZ'),
                'name': name, 'ann_date': day.isoformat(), 'title': headline, 'url': url,
                'source': source, 'source_key': key, 'canonical_key': identity(url, aid),
                'published_at': published.isoformat() if published else None,
                'observed_at': observed, 'capture_version': version})
        return records

    def fetch(self, source, day):
        rows, native_seen, versions = [], set(), []
        self.partial_versions[source] = versions
        expected = None
        for page in range(1, self.max_pages + 1):
            if page > 1:
                self.pause(.15)
            native, total, observed, version = self.page(source, day, page)
            versions.append(version)
            if expected is not None and total != expected:
                raise AnnouncementUnavailable('Disclosure list changed during pagination; retry next poll')
            expected = total
            for item in native:
                key = str(item.get({'cninfo': 'announcementId', 'sse': 'URL', 'szse': 'id'}[source]))
                securities = item.get('SECURITY_CODE', item.get('secCode'))
                key += '|' + json.dumps(securities, sort_keys=True)
                if key in native_seen:
                    raise AnnouncementUnavailable('Repeated page/row; incomplete disclosure scan')
                native_seen.add(key)
                try:
                    rows.extend(self.normalize(source, item, day, observed, version))
                except (TypeError, ValueError, KeyError, OverflowError) as error:
                    raise AnnouncementUnavailable('Invalid disclosure metadata') from error
            if len(native_seen) == total:
                if not rows:
                    return pd.DataFrame(), {'source': source, 'status': 'complete', 'native_rows': total,
                                            'equity_rows': 0, 'versions': versions}
                frame = pd.DataFrame(rows)
                path = self.root / 'artifacts/providers/official-announcements' / (uuid4().hex + '.csv.gz')
                path.parent.mkdir(parents=True, exist_ok=True)
                frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
                registered = DataStore(self.root).import_file(path, {'provider': source.upper(), 'market': 'CN',
                    'origin': 'derived_provider_export', 'upstream_versions': versions, 'restricted': True,
                    'kind': 'announcements', 'timezone': 'Asia/Shanghai'})
                clean = read_clean_csv(path, version=registered['id'],
                                       dtype={'source_key': str, 'canonical_key': str})
                return clean, {'source': source, 'status': 'complete', 'native_rows': total,
                               'equity_rows': len(clean), 'versions': versions, 'clean_version': registered['id']}
            if not native or len(native_seen) > total:
                raise AnnouncementUnavailable('Pagination total does not match observed rows')
        raise AnnouncementUnavailable('Disclosure pagination bound reached; no silent truncation')

    def document_hash(self, url):
        """Resolve a cross-source ambiguity by bytes, never by title similarity."""
        url = document_url(url)
        if url in self.document_hashes:
            return self.document_hashes[url]
        self._document_remaining()
        if self.document_attempts >= self.max_documents:
            raise VerificationDeferred('Disclosure verification document budget exhausted')
        if url in self.document_failures:
            raise AnnouncementUnavailable('Disclosure verification already failed this round')
        self.document_attempts += 1
        self.document_failures.add(url)
        aliases = [url]
        for hop in range(4):
            self.pause(.15)
            timeout = min(18, self._document_remaining())
            with self.client.stream('GET', aliases[-1], headers={'User-Agent': 'Mozilla/5.0'},
                                    follow_redirects=False, timeout=timeout) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if hop == 3 or not response.headers.get('location'):
                        raise AnnouncementUnavailable('Disclosure redirect limit reached')
                    target = document_url(urljoin(aliases[-1], response.headers['location']))
                    if target in aliases:
                        raise AnnouncementUnavailable('Disclosure redirect loop')
                    aliases.append(target)
                    continue
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    self._document_remaining()
                    content.extend(chunk)
                    if len(content) > 20 * 1024 * 1024:
                        raise AnnouncementUnavailable('Disclosure PDF exceeds verification limit')
                break
        if not bytes(content).lstrip().startswith(b'%PDF-'):
            raise AnnouncementUnavailable('Disclosure response is not a PDF; identity remains unconfirmed')
        digest = hashlib.sha256(content).hexdigest()
        folder = self.root / 'artifacts/providers/official-announcements/documents'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + '.pdf')
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(content)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise AnnouncementUnavailable('Retained disclosure checksum mismatch')
        self.capture('OfficialAnnouncements', 'document-identity', {'url': url, 'aliases': aliases, 'sha256': digest,
                     'bytes': len(content), 'local_document': str(path.relative_to(self.root))}, 'CN', restricted=True)
        for alias in aliases:
            self.document_hashes[alias] = digest
            self.document_aliases[alias] = tuple(aliases)
            self.document_failures.discard(alias)
        return digest


def fetch_official_announcements(day, service):
    frames, reports = {}, []
    for source in SOURCES:
        try:
            frame, report = service.fetch(source, day)
            frames[source] = frame
        except (httpx.HTTPError, ValueError, KeyError, RuntimeError) as error:
            report = {'source': source, 'status': 'unavailable', 'reason': f'{type(error).__name__}: {error}'[:250],
                      'versions': list(getattr(service, 'partial_versions', {}).get(source, []))}
        reports.append(report)
    # CNINFO is the consolidated main view; SZSE's annId shares its exact ID.
    # SSE is captured independently and supplies Shanghai when CNINFO is unavailable.
    selected = [frames[s] for s in ('cninfo', 'szse') if s in frames]
    if 'cninfo' not in frames and 'sse' in frames:
        selected.append(frames['sse'])
    coverage = ('cninfo' in frames or {'sse', 'szse'}.issubset(frames))
    frame = pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()
    if not frame.empty:
        frame = frame.sort_values('observed_at', kind='stable')
    return frame, reports, coverage
