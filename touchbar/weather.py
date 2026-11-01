# SPDX-License-Identifier: GPL-2.0-only
"""Read the same in-memory weather report used by Omarchy's top-bar widget.

Only the shell plugin fetches weather. IPC and disk caching run in one daemon
thread so they cannot stall the Touch Bar's controls or one-second heartbeat.
"""
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
from settings import read_settings

EPOCH = dt.date(1970, 1, 1)
CODES = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65,
         66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
# Exact Nerd Font symbols from the packaged Omarchy weather Model.js.
GLYPHS = {0xe32b, 0xe30d, 0xe32e, 0xe302, 0xe33d, 0xe346, 0xe313,
          0xe333, 0xe308, 0xe327, 0xe30a, 0xe3ad, 0xe31d, 0xe318, 0xe31a}


def fetch_open_meteo(location):
    """Optional explicit-location fallback; never performs IP geolocation."""
    lat, lon = location
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (lat, lon)) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Invalid weather coordinates')
    query = urllib.parse.urlencode(dict(latitude=lat, longitude=lon, timezone='auto', forecast_days=7,
                                       daily='weather_code,temperature_2m_max,temperature_2m_min',
                                       hourly='temperature_2m,weather_code,precipitation_probability,is_day',
                                       current='weather_code', temperature_unit='celsius'))
    request = urllib.request.Request('https://api.open-meteo.com/v1/forecast?' + query,
                                     headers={'User-Agent': 'OmarchyT1/0.1'})
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read(256001)
    if len(raw) > 256000:
        raise ValueError('Oversized weather response')
    forecast = json.loads(raw)
    code = forecast.get('current', {}).get('weather_code')
    icon = 0xe30d if code == 0 else 0xe302 if code in (1, 2, 3) else 0xe308 if code in (51, 53, 55, 61, 63, 65, 80, 81, 82) else 0xe313
    snapshot = dict(forecast=forecast, unit='C', fetched=time.time(), icon=chr(icon), name='Open-Meteo')
    normalize(snapshot)
    return snapshot


def normalize(snapshot):
    """Validate all seven dates and temperatures; never turn missing data into 0°."""
    raw = snapshot['forecast']
    zone = ZoneInfo(raw['timezone'])
    daily = raw['daily']
    first = dt.date.fromisoformat(daily['time'][0])
    days = []
    unit = snapshot['unit']
    if unit not in ('C', 'F'):
        raise ValueError('Invalid temperature unit')
    if raw['daily_units']['temperature_2m_max'] != '°C':
        raise ValueError('Unexpected source temperature unit')
    for i in range(7):
        date = dt.date.fromisoformat(daily['time'][i])
        if date != first + dt.timedelta(days=i):
            raise ValueError('Nonconsecutive forecast dates')
        values = [daily['temperature_2m_max'][i], daily['temperature_2m_min'][i]]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
               not math.isfinite(v) or not -100 <= v <= 70 for v in values):
            raise ValueError('Missing or invalid temperature')
        code = daily['weather_code'][i]
        if code not in CODES or values[0] < values[1]:
            raise ValueError('Invalid daily conditions')
        if unit == 'F':
            values = [v * 9 / 5 + 32 for v in values]
        days.append([(date - EPOCH).days, code, round(values[0]), round(values[1])])
    icon = ord(snapshot['icon']) if len(snapshot.get('icon', '')) == 1 else -1
    fetched = float(snapshot['fetched'])
    if not math.isfinite(fetched) or fetched <= 0:
        raise ValueError('Missing source timestamp')
    hours = {}
    hourly = raw.get('hourly') or {}
    hourly_units = raw.get('hourly_units') or {}
    if hourly_units.get('temperature_2m') == '°C':
        for i, timestamp in enumerate(hourly.get('time', [])[:192]):
            try:
                moment = dt.datetime.fromisoformat(timestamp)
                date = (moment.date() - EPOCH).days
                if date not in {day[0] for day in days} or moment.minute != 0:
                    continue
                code = hourly['weather_code'][i]
                temp = hourly['temperature_2m'][i]
                if code not in CODES or isinstance(temp, bool) or not isinstance(temp, (int, float)) or not math.isfinite(temp) or not -100 <= temp <= 70:
                    continue
                rain_values = hourly.get('precipitation_probability', [])
                rain = rain_values[i] if i < len(rain_values) else None
                rain = round(rain) if not isinstance(rain, bool) and isinstance(rain, (int, float)) and math.isfinite(rain) and 0 <= rain <= 100 else -1
                daylight = hourly.get('is_day', [])
                daylight = daylight[i] if i < len(daylight) and daylight[i] in (0, 1) else -1
                if unit == 'F':
                    temp = temp * 9 / 5 + 32
                hours.setdefault(date, {})[moment.hour] = [moment.hour, code, round(temp), rain, daylight]
            except (ValueError, TypeError, KeyError, IndexError):
                continue
    return dict(fetched=fetched, timezone=str(zone), icon=icon if icon in GLYPHS else -1,
                days=days, hours=hours, unit=unit, name=str(snapshot.get('name', 'Weather'))[:80])


def encode(report, now=None):
    now = time.time() if now is None else now
    today = (dt.datetime.fromtimestamp(now, ZoneInfo(report['timezone'])).date() - EPOCH).days
    age = now - report['fetched']
    if age > 172800 or age < -300:
        return 'WEATHER -1 -1 0 0'
    days = [d for d in report['days'] if d[0] >= today][:7]
    stale = age > 1800 or not days or days[0][0] != today or len(days) < 7
    icon = report['icon'] if age < 21600 and days and days[0][0] == today else -1
    fields = [2 if stale else 1, icon, len(days), int(report['unit'] == 'F')]
    # Negative epoch-day means Today; old cache dates keep their real weekday.
    for date, code, high, low in days:
        fields.extend([-date if date == today else date, code, high, low])
    return 'WEATHER ' + ' '.join(map(str, fields))


def hourly_packets(report, status, now=None):
    now = time.time() if now is None else now
    local = dt.datetime.fromtimestamp(now, ZoneInfo(report['timezone']))
    today = (local.date() - EPOCH).days
    result = []
    for day in report['days']:
        date = day[0]
        if date < today:
            continue
        rows = sorted(report.get('hours', {}).get(date, {}).values())
        fields = [status, date, len(rows), int(report['unit'] == 'F'), local.hour if date == today else -1]
        for row in rows:
            fields.extend(row)
        result.append('HOURS ' + ' '.join(map(str, fields)))
    return result


class Weather:
    def __init__(self, home=None, start=True):
        self.home = Path.home() if home is None else Path(home)
        self.cache = self.home / '.cache/omarchy-touchbar/weather.json'
        self.lock = threading.Lock()
        self.report = None
        self.status = 0
        self.wake = threading.Event()
        if start:
            threading.Thread(target=self._worker, name='touchbar-weather', daemon=True).start()

    def packet(self):
        with self.lock:
            report, status = self.report, self.status
        if not report:
            return f'WEATHER {status} -1 0 0'
        packet=encode(report)
        if status!=1 and packet.startswith('WEATHER 1 '):
            packet='WEATHER 2 '+packet[len('WEATHER 1 '):]
        return packet

    def packets(self):
        # One immutable report per batch keeps daily and hourly data in sync.
        with self.lock:
            report, status = self.report, self.status
        if not report:
            return [f'WEATHER {status} -1 0 0']
        now = time.time()
        packet = encode(report, now)
        if status != 1 and packet.startswith('WEATHER 1 '):
            packet = 'WEATHER 2 ' + packet[len('WEATHER 1 '):]
        state = int(packet.split()[1])
        return [packet] + (hourly_packets(report, state, now) if state > 0 else [])

    def refresh(self):
        # Poll the shared report sooner; does not trigger duplicate network calls.
        self.wake.set()

    def _worker(self):
        try:
            self.report = normalize(json.loads(self.cache.read_text()))
        except (OSError, KeyError, ValueError, TypeError, IndexError):
            pass
        settings = read_settings()
        location = settings.get('weather_location')
        if not isinstance(location, list) or len(location) != 2:
            location = None
        last_fallback = float('-inf')
        fallback_snapshot = None
        while True:
            try:
                try:
                    output = subprocess.check_output(
                        ['/usr/bin/omarchy', 'shell', 'omarchy.weather', 'touchbarSnapshot'],
                        text=True, stderr=subprocess.DEVNULL, timeout=3)
                    snapshot = json.loads(output)
                    normalize(snapshot)
                except (OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError, IndexError):
                    if location is None:
                        raise ValueError('Shared weather unavailable; no explicit fallback location configured')
                    if time.monotonic() - last_fallback >= (900 if fallback_snapshot else 60):
                        last_fallback = time.monotonic()
                        fallback_snapshot = fetch_open_meteo(location)
                    if not fallback_snapshot:
                        raise ValueError('Weather fallback unavailable')
                    snapshot = fallback_snapshot
                report = normalize(snapshot)
                with self.lock:
                    self.report, self.status = report, 1
                self.cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temp = self.cache.with_suffix('.tmp')
                temp.write_text(json.dumps(snapshot))
                temp.chmod(0o600)
                os.replace(temp, self.cache)
            except (OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError, IndexError):
                with self.lock:
                    self.status = -1
            self.wake.wait(10)
            self.wake.clear()
