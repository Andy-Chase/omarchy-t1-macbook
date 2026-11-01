# SPDX-License-Identifier: GPL-2.0-only
"""Load a bounded, root-owned hardware-service configuration."""
import json
import os
from pathlib import Path
import pwd
import stat


def load_config(path=Path('/etc/omarchy-t1/desktop.json')):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Desktop configuration must be a root-owned regular file without group/other writes')
    if info.st_size > 4096:
        raise RuntimeError('Desktop configuration is oversized')
    data = json.loads(path.read_text())
    uid = data.get('uid')
    if type(uid) is not int or not 1000 <= uid < 2**31:
        raise RuntimeError('Desktop UID must identify a regular user')
    user = pwd.getpwuid(uid)
    if user.pw_name != data.get('user') or user.pw_dir != data.get('home'):
        raise RuntimeError('Configured desktop user no longer matches the account database')
    features = data.get('features')
    allowed = {'chatgpt', 'weather', 'markets', 'piano', 'files', 'news'}
    if not isinstance(features, list) or any(not isinstance(x, str) or x not in allowed for x in features):
        raise RuntimeError('Invalid optional features')
    return data
