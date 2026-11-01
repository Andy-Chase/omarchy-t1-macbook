#!/usr/bin/env python3
"""Offline behaviour tests: no network connections or real browsers."""
from concurrent.futures import Future
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import patch
from news import News, MAX_BYTES, clean_title, parse_feed, safe_url, stamp

RSS = b'''<rss><channel>
<item><title>Old headline</title><link>https://example.com/old</link><pubDate>Wed, 28 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>New &amp; useful</title><link>https://example.com/new</link><pubDate>Thu, 29 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>Duplicate</title><link>https://example.com/new</link></item>
<item><title>Bad</title><link>javascript:alert(1)</link></item>
</channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>AI &amp; people</title><link rel="self" href="https://example.com/feed"/>
<link rel="alternate" href="/article"/><updated>2026-10-29T12:00:00Z</updated>
</entry></feed>'''


class FeedTests(unittest.TestCase):
    def test_rss_sorted_deduplicated_and_bad_url_rejected(self):
        entries = parse_feed(RSS, 'https://example.com/feed')
        self.assertEqual([e['title'] for e in entries], ['New & useful', 'Old headline'])
        self.assertEqual(entries[0]['id'], hashlib.sha256(b'https://example.com/new').hexdigest()[:16])

    def test_atom_alternate_relative_link(self):
        entry = parse_feed(ATOM, 'https://example.com/feed')[0]
        self.assertEqual(entry['url'], 'https://example.com/article')
        self.assertEqual(entry['published'], stamp('Thu, 29 Oct 2026 12:00:00 GMT'))

    def test_titles_are_plain_bounded_text(self):
        self.assertEqual(clean_title('&lt;b&gt;Hello&lt;/b&gt;\nworld\t!\u202e'), 'Hello world !')
        self.assertEqual(len(clean_title('a' * 500)), 240)

    def test_malformed_empty_dtd_and_oversize_feeds(self):
        for data in [b'no xml', b'<rss><channel/></rss>', b'<!DOCTYPE x><rss/>', b'x' * (MAX_BYTES + 1)]:
            with self.assertRaises(ValueError): parse_feed(data, 'https://example.com/')

    def test_urls_exclude_non_web_schemes_credentials_and_controls(self):
        for url in ['file:///etc/passwd', 'javascript:foo', 'https://u:p@example.com/',
                    'https://example.com/\nfoo', '--browser', 'https://[invalid']:
            self.assertFalse(safe_url(url))
        self.assertTrue(safe_url('https://example.com/a?q=one&x=two'))

    def test_missing_or_invalid_dates(self):
        self.assertEqual(stamp(None), 0)
        self.assertEqual(stamp('garbage'), 0)
        self.assertGreater(stamp('2026-10-29T12:00:00Z'), 0)


class NewsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        env.start();self.addCleanup(env.stop)
        self.processes = []
        self.news = News(self.processes, self.root / 'cache')
        self.addCleanup(self.news.close)
        self.entries = parse_feed(RSS, 'https://example.com/feed')

    def seed(self):
        self.news.entries[0] = self.entries.copy()
        self.news.fetched[0] = time.time()
        self.news.remember(self.entries)
        self.news.select(0)

    def complete(self, outcome):
        future = Future()
        if isinstance(outcome, Exception): future.set_exception(outcome)
        else: future.set_result(outcome)
        self.news.future = future
        self.news.loading_category = 0
        self.news.attempt[0] = time.monotonic()

    def test_no_network_when_hidden(self):
        with patch.object(self.news.executor, 'submit') as submit:
            self.news.packet()
            submit.assert_not_called()

    def test_selection_persists_and_invalid_requests_are_ignored(self):
        self.assertTrue(self.news.select(1))
        restored = News([], self.root / 'cache')
        self.addCleanup(restored.close)
        self.assertEqual(restored.category, 1)
        for msg in ['NEWS SELECT 2', 'NEWS SELECT 1\n', 'NEWS ARTICLE /tmp', 'NEWS NEXT extra']:
            self.assertFalse(self.news.apply(msg))

    def test_fetch_is_submitted_without_waiting_on_event_loop(self):
        future = Future()
        with patch.object(self.news.executor, 'submit', return_value=future) as submit:
            self.news.select(0)
            self.assertIn('NEWS 0 0 - 0 0 0\t', self.news.packet())
            self.assertIs(self.news.future, future)
            self.news.packet()
            submit.assert_called_once()
            future.set_result(self.entries)
            packet = self.news.packet()
            self.assertIn('NEWS 0 1 ', packet)
            self.assertTrue((self.root / 'cache/0.json').is_file())

    def test_refresh_preserves_current_article_when_order_changes(self):
        self.seed()
        self.news.apply('NEWS NEXT')
        before = self.news.current(0)['id']
        self.complete(list(reversed(self.entries)))
        self.news.tick()
        self.assertEqual(self.news.current(0)['id'], before)

    def test_failure_retains_cache_and_marks_cached(self):
        self.seed()
        self.complete(OSError('offline'))
        packet = self.news.packet()
        self.assertIn('NEWS 0 2 ', packet)
        self.assertIn('New & useful', packet)
        with patch.object(self.news.executor, 'submit') as submit:
            self.news.packet();submit.assert_not_called()

    def test_failure_without_cache_is_explicit(self):
        self.news.select(0)
        self.complete(OSError('offline'))
        self.assertIn('NEWS 0 -1 - 0 0 0\tNews unavailable', self.news.packet())

    def test_stale_disk_cache_restored_with_stable_identifiers(self):
        directory = self.root / 'cache'
        directory.mkdir()
        (directory / '0.json').write_text(json.dumps({'entries': self.entries, 'fetched': time.time() - 3600}))
        restored = News([], directory)
        self.addCleanup(restored.close)
        self.assertIn('NEWS 0 2 ', restored.packet())
        self.assertEqual(restored.current(0)['id'], self.entries[0]['id'])

    def test_cache_rejects_arbitrary_urls(self):
        directory = self.root / 'cache';directory.mkdir()
        (directory / '0.json').write_text(json.dumps({'entries': [dict(self.entries[0], url='file:///tmp/a')], 'fetched': time.time()}))
        restored = News([], directory);self.addCleanup(restored.close)
        self.assertEqual(restored.entries[0], [])

    @patch('news.subprocess.Popen')
    def test_exact_displayed_article_can_open_after_refresh(self, popen):
        self.seed()
        token = self.entries[0]['id']
        self.complete([self.entries[1]])
        self.news.tick()
        self.assertTrue(self.news.apply('NEWS ARTICLE ' + token))
        self.assertEqual(popen.call_args.args[0][-2:], ['/usr/bin/xdg-open', self.entries[0]['url']])
        self.assertNotIn('shell', popen.call_args.kwargs)
        self.assertEqual(self.processes, [popen.return_value])
        self.assertFalse(self.news.apply('NEWS ARTICLE ' + token))  # Debounce.

    @patch('news.subprocess.Popen')
    def test_lock_and_hidden_state_block_article_launches(self, popen):
        self.seed()
        message = 'NEWS ARTICLE ' + self.entries[0]['id']
        self.assertFalse(self.news.apply(message, locked=True))
        self.assertFalse(self.news.apply('NEWS SELECT 1', locked=True))
        self.news.apply('NEWS CLOSE')
        self.assertFalse(self.news.apply(message))
        self.assertFalse(self.news.apply('NEWS ARTICLE ' + '0' * 16))
        popen.assert_not_called()

    def test_backend_packets_are_accepted_by_compiled_renderer(self):
        renderer = Path(__file__).resolve().parents[1] / 'build/selector'
        if not renderer.is_file(): self.skipTest('Build selector first')
        packets = [self.news.packet()]
        self.seed();packets.append(self.news.packet())
        self.news.entries[0][0]['title'] = '😀' * 240
        self.news.failed[0] = True;packets.append(self.news.packet())
        self.news.entries[0] = [];self.news.index[0] = 0;packets.append(self.news.packet())
        for packet in packets:
            self.assertLess(len(packet.encode('utf-8')), 2048)
            self.assertEqual(subprocess.run([str(renderer), '--check-news-packet', packet]).returncode, 0)

    def test_previous_wraps_and_forward_returns_to_same_article(self):
        self.seed()
        original = self.news.current(0)['id']
        self.assertTrue(self.news.apply('NEWS PREV'))
        self.assertEqual(self.news.current(0)['id'], self.entries[-1]['id'])
        self.assertTrue(self.news.apply('NEWS NEXT'))
        self.assertEqual(self.news.current(0)['id'], original)
        self.assertFalse(self.news.apply('NEWS PREV', locked=True))
        self.assertEqual(self.news.current(0)['id'], original)
        self.news.show(False)
        self.assertFalse(self.news.apply('NEWS PREV'))

    def test_previous_on_empty_and_single_article_feeds(self):
        self.news.select(0)
        self.assertTrue(self.news.apply('NEWS PREV'))
        self.assertEqual(self.news.index[0], 0)
        self.seed();self.news.entries[0] = [self.entries[0]]
        self.assertTrue(self.news.apply('NEWS PREV'))
        self.assertEqual(self.news.index[0], 0)
        self.assertFalse(self.news.apply('NEWS PREV extra'))

    def test_next_cycles_and_close_stops_refreshes(self):
        self.seed()
        original = self.news.current(0)['id']
        self.news.apply('NEWS NEXT');self.assertNotEqual(self.news.current(0)['id'], original)
        self.news.apply('NEWS NEXT');self.assertEqual(self.news.current(0)['id'], original)
        self.news.apply('NEWS CLOSE');self.assertFalse(self.news.visible)


if __name__ == '__main__': unittest.main(verbosity=2)
