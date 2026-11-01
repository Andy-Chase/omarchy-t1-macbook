# SPDX-License-Identifier: GPL-2.0-only
import datetime as dt
import io
import json
import math
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.parse

from settings import read_settings
from weather import fetch_open_meteo, normalize, encode, hourly_packets


class WeatherTests(unittest.TestCase):
    def forecast(self):
        dates = [(dt.date.today() + dt.timedelta(days=i)).isoformat() for i in range(7)]
        return {'timezone': 'UTC', 'daily_units': {'temperature_2m_max': '°C'},
                'daily': {'time': dates, 'temperature_2m_max': [22] * 7, 'temperature_2m_min': [12] * 7, 'weather_code': [0] * 7},
                'current': {'weather_code': 0}, 'hourly_units': {'temperature_2m': '°C'},
                'hourly': {'time': [dates[0] + 'T12:00'], 'temperature_2m': [20], 'weather_code': [0], 'precipitation_probability': [10], 'is_day': [1]}}

    def test_api_response_becomes_valid_existing_daily_and_hourly_packets(self):
        response = io.BytesIO(json.dumps(self.forecast()).encode())
        with patch('weather.urllib.request.urlopen', return_value=response) as request:
            snapshot = fetch_open_meteo([52.52, 13.41])
        url = request.call_args.args[0].full_url
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query['latitude'], ['52.52'])
        self.assertEqual(query['timezone'], ['auto'])
        self.assertEqual(query['forecast_days'], ['7'])
        report = normalize(snapshot)
        self.assertTrue(encode(report).startswith('WEATHER 1 '))
        self.assertTrue(hourly_packets(report, 1)[0].startswith('HOURS 1 '))

    def test_invalid_coordinates_do_not_send_network_requests(self):
        with patch('weather.urllib.request.urlopen') as request:
            for location in [[91, 0], [0, -181], [float('nan'), 0], [True, 0], ['0', 0]]:
                with self.assertRaises(ValueError):
                    fetch_open_meteo(location)
            request.assert_not_called()

    def test_oversized_or_malformed_response_is_not_exposed(self):
        for data in [b'X' * 256001, b'{}', b'not json']:
            with patch('weather.urllib.request.urlopen', return_value=io.BytesIO(data)):
                with self.assertRaises((ValueError, KeyError)):
                    fetch_open_meteo([0, 0])

    def test_settings_invalid_json_and_wrong_top_level_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config'
            for text in ['bad json', '[]', 'null']:
                path.write_text(text)
                self.assertEqual(read_settings(path), {})
