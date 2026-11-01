#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Download a pinned template to a local user file; never install or activate it."""
import argparse
import hashlib
import os
from pathlib import Path
import urllib.request

URL = 'https://raw.githubusercontent.com/Bogdan7c/macbook-bcm43602-wifi-fix/35dd57c5b2b0fc8f76af5c6c2a45ead010692421/files/brcmfmac43602-pcie.txt'
EXPECTED = '9f757ace460a40c80a32202e09d507351a8c497f2dee22ba0f8a0a5cb1aebaf7'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination', type=Path)
    args = p.parse_args()
    if os.geteuid() == 0:
        p.error('Download as your normal user; root is not needed')
    with urllib.request.urlopen(URL, timeout=20) as response:
        data = response.read(16385)
    if len(data) > 16384 or hashlib.sha256(data).hexdigest() != EXPECTED:
        raise SystemExit('Template checksum/size differs from the reviewed source; nothing written')
    fd = os.open(args.destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
    print('Pinned template saved locally. See upstream provenance; it has not been installed.')


if __name__ == '__main__':
    main()
