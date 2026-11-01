# SPDX-License-Identifier: GPL-2.0-only
"""Small RSS/Atom news reader. Network work never runs on the session event loop."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = (
    ('AI', 'The Verge', 'https://www.theverge.com/rss/ai-artificial-intelligence/index.xml'),
    ('Global', 'BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
)
REFRESH = 900
MAX_BYTES = 2_000_000
ATOM = '{http://www.w3.org/2005/Atom}'


def clean_title(value):
    value = re.sub(r'<[^>]*>', '', html.unescape(value or ''))
    value = ''.join(' ' if c.isspace() else c for c in value if c.isspace() or not unicodedata.category(c).startswith('C'))
    return ' '.join(value.split())[:240]


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        return False
    try:
        p = urllib.parse.urlsplit(value)
        return p.scheme in ('https', 'http') and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


def stamp(value):
    try:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return max(0, min(int(date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()), 4102444800))
    except (TypeError, ValueError, OverflowError, AttributeError):
        return 0


def parse_feed(data, base):
    declarations = data.replace(b'\x00', b'').upper()
    if len(data) > MAX_BYTES or b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise ValueError('Feed is too large or contains a document type')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError('Malformed feed XML') from exc
    nodes = root.findall('./channel/item') if root.tag == 'rss' else root.findall(ATOM + 'entry')
    entries, seen = [], set()
    for node in nodes[:200]:
        title = clean_title(node.findtext('title') or node.findtext(ATOM + 'title'))
        link = node.findtext('link')
        if not link:
            link = next((e.get('href') for e in node.findall(ATOM + 'link')
                         if e.get('rel', 'alternate') == 'alternate'), '')
        url = urllib.parse.urljoin(base, (link or '').strip())
        if not title or not safe_url(url) or url in seen:
            continue
        seen.add(url)
        date = node.findtext('pubDate') or node.findtext(ATOM + 'published') or node.findtext(ATOM + 'updated')
        entries.append({'id': hashlib.sha256(url.encode()).hexdigest()[:16],
                        'title': title, 'url': url, 'published': stamp(date)})
    entries.sort(key=lambda item: item['published'], reverse=True)
    if not entries:
        raise ValueError('Feed has no usable articles')
    return entries[:20]


def fetch_feed(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'OmarchyTouchbarNews/1.0 (personal RSS reader)',
                                                  'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return parse_feed(response.read(MAX_BYTES + 1), response.geturl())


class News:
    def __init__(self, processes, directory=None):
        self.processes = processes
        self.directory = directory or Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'omarchy-touchbar/news'
        self.preference = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'omarchy-touchbar/news-category'
        self.category = 0
        try:
            value = int(self.preference.read_text())
            if value in (0, 1): self.category = value
        except (OSError, ValueError):
            pass
        self.visible = False
        self.entries = [[], []]
        self.fetched = [0., 0.]
        self.failed = [False, False]
        self.attempt = [float('-inf'), float('-inf')]
        self.index = [0, 0]
        self.links = OrderedDict()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='touchbar-news')
        self.future = None
        self.loading_category = None
        self.last_open = float('-inf')
        for category in range(2):
            try:
                data = json.loads((self.directory / f'{category}.json').read_text())
                entries = data['entries']
                if not isinstance(entries, list) or len(entries) > 20: continue
                checked = []
                for entry in entries:
                    url = entry['url']
                    if not safe_url(url): continue
                    title = clean_title(entry['title'])
                    if title:
                        checked.append({'id': hashlib.sha256(url.encode()).hexdigest()[:16],
                                        'title': title, 'url': url,
                                        'published': max(0, min(int(entry['published']), 4102444800))})
                fetched = float(data['fetched'])
                if not 0 <= fetched <= time.time() + 60: continue
                self.entries[category] = checked
                self.fetched[category] = fetched
                self.remember(checked)
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                pass

    def remember(self, entries):
        for entry in entries:
            self.links[entry['id']] = entry['url']
            self.links.move_to_end(entry['id'])
        while len(self.links) > 200: self.links.popitem(last=False)

    @staticmethod
    def atomic_write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(content)
        temporary.replace(path)

    def show(self, visible):
        self.visible = visible

    def select(self, category):
        if category not in (0, 1): return False
        self.category = category
        self.visible = True
        try: self.atomic_write(self.preference, str(category) + '\n')
        except OSError: pass
        return True

    def tick(self):
        if self.future is not None and self.future.done():
            category = self.loading_category
            try:
                entries = self.future.result()
                current = self.current(category)
                self.entries[category] = entries
                self.index[category] = next((i for i, item in enumerate(entries)
                                            if current and item['id'] == current['id']), 0)
                self.fetched[category] = time.time()
                self.failed[category] = False
                self.remember(entries)
                try:
                    self.atomic_write(self.directory / f'{category}.json', json.dumps(
                        {'fetched': self.fetched[category], 'entries': entries}))
                except OSError: pass
            except Exception:
                self.failed[category] = True
            self.future = None
        c = self.category
        if self.visible and self.future is None and time.time() - self.fetched[c] >= REFRESH:
            if time.monotonic() - self.attempt[c] >= (60 if self.failed[c] else REFRESH):
                self.attempt[c] = time.monotonic()
                self.loading_category = c
                self.future = self.executor.submit(fetch_feed, FEEDS[c][2])

    def current(self, category):
        items = self.entries[category]
        return items[self.index[category] % len(items)] if items else None

    def packet(self):
        self.tick()
        c = self.category
        item = self.current(c)
        stale = self.failed[c] or time.time() - self.fetched[c] >= REFRESH
        status = (2 if stale else 1) if item else (-1 if self.failed[c] else 0)
        token = item['id'] if item else '-'
        published = item['published'] if item else 0
        title = item['title'] if item else ('News unavailable — try again shortly' if self.failed[c] else 'Loading headlines…')
        return f"NEWS {c} {status} {token} {published} {self.index[c] + 1 if item else 0} {len(self.entries[c])}\t{title}"

    def apply(self, message, locked=False):
        if message == 'NEWS CLOSE':
            self.show(False)
            return True
        if locked: return False
        if message in ('NEWS SELECT 0', 'NEWS SELECT 1'):
            return self.select(int(message[-1]))
        if message in ('NEWS NEXT', 'NEWS PREV') and self.visible:
            c = self.category
            if self.entries[c]: self.index[c] = (self.index[c] + (1 if message == 'NEWS NEXT' else -1)) % len(self.entries[c])
            return True
        match = re.fullmatch(r'NEWS ARTICLE ([a-f0-9]{16})', message)
        if not match or not self.visible or time.monotonic() - self.last_open < .7: return False
        url = self.links.get(match[1])
        if not url or not safe_url(url): return False
        command = ['/usr/bin/xdg-open', url]
        if Path('/usr/bin/uwsm-app').is_file(): command = ['/usr/bin/uwsm-app', '--', *command]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self.processes.append(process)
        self.last_open = time.monotonic()
        return True

    def close(self):
        self.visible = False
        self.executor.shutdown(wait=False, cancel_futures=True)


if __name__ == '__main__':
    # Optional real-network diagnostic; never opens articles or changes preferences.
    import sys
    failed = False
    for category, source, url in FEEDS:
        try:
            items = fetch_feed(url)
            print(f'{category}: {source}: {len(items)} articles; newest: {items[0]["title"]}')
        except Exception as exc:
            failed = True
            print(f'{category}: unavailable: {exc}')
    sys.exit(int(failed))
