#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Print bounded read-only diagnostics without MACs, SSIDs, serials or journals."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def read(path):
    try:
        return Path(path).read_text().strip()[:120]
    except OSError:
        return None


def package(name):
    try:
        value = subprocess.run(['pacman', '-Q', name], capture_output=True, text=True, timeout=3)
        return value.stdout.strip() if value.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    t1 = 0
    recovery = 0
    for p in Path('/sys/bus/usb/devices').glob('*'):
        if read(p / 'idVendor') == '05ac':
            product = read(p / 'idProduct')
            t1 += product == '8600'
            recovery += product == '1281'
    wifi = []
    for p in Path('/sys/bus/pci/devices').glob('*'):
        if read(p / 'vendor') == '0x14e4' and read(p / 'device') == '0x43ba':
            wifi.append({key: read(p / key) for key in ['vendor', 'device', 'subsystem_vendor', 'subsystem_device']})
    commands = ['cc', 'make', 'pkg-config', 'python3', 'dkms', 'modinfo', 'systemctl', 'hyprctl',
                'brightnessctl', 'pactl', 'pw-cat', 'nmcli', 'iw', 'limine-mkinitcpio', 'xdg-open', 'xdg-user-dir']
    result = {'model': read('/sys/class/dmi/id/product_name'), 'kernel': os.uname().release,
              'architecture': os.uname().machine, 'omarchy_package': package('omarchy'),
              'legacy_source_version_marker': read('/usr/share/omarchy/version'),
              'normal_t1_count': t1, 'recovery_t1_count': recovery, 'wifi_devices': wifi,
              'commands_available': {x: bool(shutil.which(x)) for x in commands},
              'kernel_headers_present': Path(f'/usr/lib/modules/{os.uname().release}/build/Makefile').exists(),
              'board_data_present': Path('/usr/lib/firmware/brcm/brcmfmac43602-pcie.Apple Inc.-MacBookPro13,2.txt').exists(),
              'note': 'No connection changes, network requests, firmware reads or journal collection performed'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
