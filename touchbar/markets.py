# SPDX-License-Identifier: GPL-2.0-only
"""Delayed market quotes with explicit quote dates and previous-session changes."""
import concurrent.futures
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


SPECS = (
    ('metals', 'XAU', 'USD'), ('metals', 'XAG', 'USD'),
    ('yahoo', '^AXJO', 'AUD'), ('yahoo', '^GSPC', 'USD'),
    ('yahoo', 'BTC-USD', 'USD'), ('yahoo', 'AUDEUR=X', 'EUR'),
    ('yahoo', 'AUDUSD=X', 'USD'), ('yahoo', 'AUDJPY=X', 'JPY'),
)


def positive(value):
    if isinstance(value, bool):
        raise ValueError('Boolean price')
    value = float(value)
    if not math.isfinite(value) or not 0 < value < 1e12:
        raise ValueError('Invalid price')
    return value


def quote(price, previous, timestamp, daily=False):
    price, previous = positive(price), positive(previous)
    change = (price / previous - 1) * 100
    if not math.isfinite(change):
        raise ValueError('Invalid daily change')
    direction = 1 if change > 0.00005 else -1 if change < -0.00005 else 0
    return dict(price=price, change=change, direction=direction,
                timestamp=int(timestamp), daily=daily)


def parse_yahoo(payload, symbol, currency):
    result = payload['chart']['result'][0]
    meta = result['meta']
    if meta.get('symbol') != symbol or meta.get('currency') != currency:
        raise ValueError('Unexpected instrument or currency')
    timestamp = int(meta['regularMarketTime'])
    zone = ZoneInfo(meta.get('exchangeTimezoneName') or 'UTC')
    date = dt.datetime.fromtimestamp(timestamp, zone).date()
    closes = result['indicators']['quote'][0]['close']
    prior = []
    for stamp, close in zip(result['timestamp'], closes):
        if close is not None and dt.datetime.fromtimestamp(stamp, zone).date() < date:
            prior.append((stamp, positive(close)))
    if not prior:
        raise ValueError('Previous trading close unavailable')
    # chartPreviousClose is the start-of-range baseline, not yesterday's close.
    previous = max(prior)[1]
    return quote(meta['regularMarketPrice'], previous, timestamp)


def get_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (OmarchyTouchBar/1.1)', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=6) as response:
        data = response.read(256_001)
    if len(data) > 256_000:
        raise ValueError('Oversized quote response')
    return json.loads(data)


def iso_timestamp(raw):
    moment = dt.datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError('Quote timestamp requires timezone')
    stamp = int(moment.timestamp())
    if not 0 < stamp <= time.time()+300:
        raise ValueError('Invalid quote timestamp')
    return stamp


def parse_metal_spot(payload, symbol, provider):
    if provider == 'goldprice.dev':
        if payload.get('symbol') not in (symbol, symbol+'-USD-SPOT') or payload.get('quote_currency') != 'USD' or payload.get('unit') != 'troy_ounce' or payload.get('contract_type') != 'spot':
            raise ValueError('Unexpected metal, currency or unit')
        stamp = iso_timestamp(payload['computed_at'])
        stale = payload.get('is_stale') is not False
    else:
        if payload.get('symbol') != symbol or payload.get('currency', 'USD') != 'USD':
            raise ValueError('Unexpected metal or currency')
        stamp = iso_timestamp(payload['updatedAt'])
        stale = False
    return dict(price=positive(payload['price']), timestamp=stamp, daily=False,
                old=stale or time.time()-stamp > 4*86400, failed=False,
                direction=2, change=None, source=provider)


def metal_previous_close(payload, symbol, timestamp):
    if payload.get('symbol') not in (symbol, symbol+'-USD-SPOT') or payload.get('interval') != '1d':
        raise ValueError('Unexpected historical instrument or interval')
    date = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).date()
    settled = []
    for bar in payload.get('bars', []):
        stamp = iso_timestamp(bar['bar_start'])
        if bar.get('is_closed') is True and dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date() < date and bar.get('close') is not None:
            settled.append((stamp, positive(bar['close'])))
    if not settled:
        raise ValueError('Previous settled spot close unavailable')
    stamp, price = max(settled)
    if timestamp-stamp > 7*86400:
        raise ValueError('Previous close is too old')
    return price


def fetch_metal(symbol):
    try:
        payload = get_json('https://api.goldprice.dev/v1/spot/'+symbol+'-USD-SPOT')
        row = parse_metal_spot(payload, symbol, 'goldprice.dev')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'METAL_SOURCE_FALLBACK {symbol} {type(exc).__name__}', flush=True)
        payload = get_json('https://api.gold-api.com/price/'+symbol+'/USD')
        row = parse_metal_spot(payload, symbol, 'gold-api.com')
    try:
        date = dt.datetime.fromtimestamp(row['timestamp'], dt.timezone.utc).date()
        query = urllib.parse.urlencode({'symbol': symbol+'-USD-SPOT', 'interval': '1d',
                                      'from': (date-dt.timedelta(days=10)).isoformat(),
                                      'to': date.isoformat(), 'limit': 15})
        previous = metal_previous_close(get_json('https://api.goldprice.dev/v1/bars?'+query), symbol, row['timestamp'])
        result = quote(row['price'], previous, row['timestamp'])
        row.update(change=result['change'], direction=result['direction'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A missing day-change baseline must not discard a valid spot price.
        print(f'METAL_DAY_CHANGE_UNAVAILABLE {symbol} {type(exc).__name__}', flush=True)
    return row


def fetch(index):
    provider, symbol, currency = SPECS[index]
    if provider == 'metals':
        return fetch_metal(symbol)
    if provider != 'yahoo':
        raise ValueError('Unknown quote provider')
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='') + '?interval=1d&range=1mo'
    row = parse_yahoo(get_json(url), symbol, currency)
    age = time.time() - row['timestamp']
    if age < -86400:
        raise ValueError('Quote date is in the future')
    row['old'] = age > (7200 if index == 4 else 4 * 86400)
    row['failed'] = False
    return row


class Markets:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.opened = False
        self.last_fetch = float('-inf')
        self.rows = [{} for _ in SPECS]
        self.cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home()/'.cache'))) / 'omarchy-touchbar/markets.json'
        try:
            cached = json.loads(self.cache.read_text())
            if isinstance(cached, list) and len(cached) == len(SPECS):
                for index, row in enumerate(cached):
                    if isinstance(row, dict):
                        try:
                            positive(row['price'])
                            timestamp = int(row['timestamp'])
                            if not 0 < timestamp <= time.time()+86400:
                                continue
                            self.rows[index] = {**row, 'failed': True}
                        except (ValueError, TypeError, KeyError):
                            continue
        except (OSError, ValueError, TypeError, KeyError):
            pass
        threading.Thread(target=self._poll, name='touchbar-markets', daemon=True).start()

    def show(self, opened):
        with self.lock:
            if self.opened == opened:
                return
            self.opened = opened
        self.wake.set()

    def _poll(self):
        while True:
            self.wake.clear()
            with self.lock:
                due = self.opened and time.monotonic()-self.last_fetch >= 300
            if due:
                self.last_fetch = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    pending = {pool.submit(fetch, index): index for index in range(len(SPECS))}
                    for future in concurrent.futures.as_completed(pending):
                        index = pending[future]
                        try:
                            row = future.result()
                        except Exception as exc:
                            with self.lock:
                                row = {**self.rows[index], 'failed': True}
                            print(f'MARKET_QUOTE_ERROR {SPECS[index][1]} {type(exc).__name__}', flush=True)
                        with self.lock:
                            self.rows[index] = row
                try:
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    with self.lock:
                        raw = json.dumps(self.rows, allow_nan=False)
                    temp = self.cache.with_suffix('.tmp')
                    temp.write_text(raw); temp.replace(self.cache)
                except (OSError, ValueError):
                    pass
            self.wake.wait(30)

    def packets(self):
        with self.lock:
            rows = [dict(row) for row in self.rows]
        packets = []
        for index, row in enumerate(rows):
            value, change, stamp, direction = '-', 'Loading', 'Delayed quotes', 2
            if row.get('price'):
                price = positive(row['price'])
                value = f'US${price:,.2f}' if index in (0, 1, 4) else f'{price:.4f}' if index in (5, 6) else f'{price:.2f}' if index == 7 else f'{price:,.2f}'
                moment = dt.datetime.fromtimestamp(int(row['timestamp']), ZoneInfo('Australia/Brisbane'))
                stamp = moment.strftime('%d/%m daily' if row.get('daily') else '%d/%m %H:%M')
                age = time.time() - int(row['timestamp'])
                old = age > (7200 if index == 4 else 4*86400)
                if row.get('failed') or row.get('old') or old:
                    change = 'Cached' if row.get('failed') else 'Old quote'
                elif row.get('change') is None:
                    change = 'No day change'
                else:
                    change = f'{float(row["change"]):+.2f}%'
                    direction = int(row['direction'])
            elif row.get('failed'):
                change, stamp = 'Unavailable', 'Feed error'
            packets.append(f'MARKET {index} {direction}\t{value}\t{change}\t{stamp}')
        return tuple(packets)


if __name__ == '__main__':
    if '--metals-check' in sys.argv:
        successful = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            checks = {pool.submit(fetch, index): index for index in (0, 1)}
            for future in concurrent.futures.as_completed(checks):
                symbol = SPECS[checks[future]][1]
                try:
                    row = future.result()
                    print('METAL_CHECK '+symbol+' '+json.dumps(row), flush=True)
                    if row.get('old'):
                        successful = False
                except Exception as exc:
                    successful = False
                    print('METAL_CHECK_FAILED '+symbol+' '+type(exc).__name__+' '+str(exc), flush=True)
        sys.exit(0 if successful else 1)
    for index, spec in enumerate(SPECS):
        try:
            print(spec[1], json.dumps(fetch(index)))
        except Exception as exc:
            print(spec[1], 'UNAVAILABLE', type(exc).__name__, str(exc))
