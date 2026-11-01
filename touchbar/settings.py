# SPDX-License-Identifier: GPL-2.0-only
"""Optional pages are selected by the reviewed installation configuration."""
import json
from pathlib import Path

FEATURES = {'chatgpt', 'weather', 'markets', 'piano', 'files', 'news'}


def read_settings(path=Path('/etc/omarchy-t1/desktop.json')):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled_features():
    try:
        value = read_settings()['features']
        if not isinstance(value, list) or any(not isinstance(x, str) or x not in FEATURES for x in value):
            return set()
        return set(value)
    except (OSError, ValueError, KeyError, TypeError):
        return set()
