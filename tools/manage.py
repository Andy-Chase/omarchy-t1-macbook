#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install source-built T1 components, with staging and transactional recovery.

`install` changes files only. `activate` is a separate, explicit hardware step.
An alternate --root never invokes systemd, DKMS, nmcli or boot-image tooling.
"""
import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

VERSION = '0.1.0'
MODEL = 'MacBookPro13,2'
TESTED_KERNEL = '7.2.8-9-omarchy'
STATE = '/var/lib/omarchy-t1'
LIB = '/usr/local/lib/omarchy-touchbar'
HERE = Path(__file__).resolve()
SOURCE = HERE.parent.parent
FEATURES = {'chatgpt', 'weather', 'markets', 'piano', 'files', 'news'}
TEMPLATE_SHA256 = '9f757ace460a40c80a32202e09d507351a8c497f2dee22ba0f8a0a5cb1aebaf7'
BOARD = '/usr/lib/firmware/brcm/brcmfmac43602-pcie.Apple Inc.-MacBookPro13,2.txt'
USER_UNIT = '''[Unit]
Description=Omarchy T1 desktop bridge
PartOf=graphical-session.target
After=graphical-session-pre.target
StartLimitIntervalSec=0
[Service]
Type=exec
ExecStart=/usr/bin/python3 /usr/local/lib/omarchy-touchbar/session-bridge.py --socket %t/omarchy-touchbar-selector-desktop.sock --persistent
Restart=on-failure
RestartSec=3
TimeoutStopSec=5
UMask=0077
NoNewPrivileges=yes
[Install]
WantedBy=graphical-session.target
'''
BASIC_UNIT = '''[Unit]
Description=Attach Apple T1 preset Touch Bar controls
After=systemd-modules-load.service systemd-udev-trigger.service
ConditionPathExists=/sys/bus/acpi/devices/APP7777:00
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/apple-t1-touchbar-bind
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
'''
DESKTOP_UNIT = '''[Unit]
Description=Custom Omarchy T1 Touch Bar
After=apple-t1-touchbar.service systemd-udev-trigger.service
Wants=apple-t1-touchbar.service
ConditionPathExists=/sys/bus/acpi/devices/APP7777:00
StartLimitIntervalSec=120
StartLimitBurst=3
[Service]
Type=exec
ExecStart=/usr/bin/python3 /usr/local/lib/omarchy-touchbar/hardware-service.py
ExecStopPost=/usr/bin/python3 /usr/local/lib/omarchy-touchbar/hardware-service.py --restore
Restart=on-failure
RestartSec=10
TimeoutStopSec=45
KillMode=mixed
RuntimeDirectory=omarchy-touchbar
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
UMask=0077
LimitCORE=0
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
[Install]
WantedBy=multi-user.target
'''
SLEEP_UNIT = '''[Unit]
Description=Prepare Omarchy T1 Touch Bar around sleep
Before=sleep.target
StopWhenUnneeded=yes
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/systemctl stop omarchy-touchbar.service
ExecStop=/usr/bin/systemctl --no-block start omarchy-touchbar.service
TimeoutStartSec=50
[Install]
WantedBy=sleep.target
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class User:
    name: str
    uid: int
    gid: int
    home: str


def account(name):
    value = pwd.getpwnam(name)
    if value.pw_uid < 1000 or not Path(value.pw_dir).is_absolute() or value.pw_dir == '/':
        raise RuntimeError('Choose a regular desktop account')
    # These fields become systemd/config paths, not shell text.
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', value.pw_name):
        raise RuntimeError('Unsupported desktop account name')
    return User(value.pw_name, value.pw_uid, value.pw_gid, value.pw_dir)


class Tree:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if str(root) != '/' and self.root == Path('/'):
            raise RuntimeError('An alternate staging root must not resolve to the live filesystem')
        if not self.root.is_dir():
            raise RuntimeError('Root directory must already exist')
        self.live = self.root == Path('/')

    def path(self, name):
        value = PurePosixPath(name)
        if not value.is_absolute() or '..' in value.parts:
            raise RuntimeError('Invalid destination path')
        path = self.root.joinpath(*value.parts[1:])
        # Never traverse a user-created parent symlink under root privileges.
        for parent in [self.root, *list(path.parents)[:-1][::-1]]:
            if parent == self.root or self.root in parent.parents:
                if parent.is_symlink():
                    raise RuntimeError('Refusing symlink parent: ' + str(parent))
                privileged = any(name.startswith(prefix) for prefix in ('/usr/local/', '/usr/src/', '/etc/', '/var/lib/omarchy-t1/'))
                if self.live and privileged and parent.exists():
                    info = parent.stat()
                    if info.st_uid != 0 or info.st_mode & 0o022:
                        raise RuntimeError('Privileged destination has an unsafe parent: ' + str(parent))
        if not path.parent.resolve().is_relative_to(self.root):
            raise RuntimeError('Destination escapes installation root')
        return path

    def require_mutation(self):
        if self.live and os.geteuid() != 0:
            raise RuntimeError('Use sudo from the desktop terminal for live installation')

    def sysfs_path(self, name):
        """Resolve a read-only kernel probe, allowing aliases only inside sysfs.

        Write destinations must continue to use path(), whose parent-symlink
        guard is deliberately stricter. Staged probes must stay in staged sysfs.
        """
        value = PurePosixPath(name)
        if not value.is_absolute() or '..' in value.parts or value.parts[1:2] != ('sys',):
            raise RuntimeError('Hardware probes must use an absolute sysfs path')
        target = self.root.joinpath(*value.parts[1:]).resolve(strict=True)
        if not target.is_relative_to(self.root / 'sys'):
            raise RuntimeError('Hardware probe escapes the installation sysfs tree')
        return target


def capture(path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return {'kind': 'absent'}
    attrs = dict(mode=stat.S_IMODE(st.st_mode), uid=st.st_uid, gid=st.st_gid)
    if stat.S_ISLNK(st.st_mode):
        return dict(kind='link', target=os.readlink(path), **attrs)
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError('Refusing non-regular destination: ' + str(path))
    data = path.read_bytes()
    return dict(kind='file', data=base64.b64encode(data).decode(), sha256=digest(data), **attrs)


def same(a, b):
    return a.get('kind') == b.get('kind') and (
        a.get('kind') == 'absent' or
        (a.get('kind') == 'file' and a.get('sha256') == b.get('sha256')) or
        (a.get('kind') == 'link' and a.get('target') == b.get('target')))


def write_record(path, value, live=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if value['kind'] == 'absent':
        path.unlink(missing_ok=True)
        return
    fd, temp = tempfile.mkstemp(prefix='.t1-', dir=path.parent)
    temp = Path(temp)
    try:
        if value['kind'] == 'link':
            os.close(fd)
            temp.unlink()
            temp.symlink_to(value['target'])
            if live:
                os.lchown(temp, value['uid'], value['gid'])
        else:
            with os.fdopen(fd, 'wb') as out:
                out.write(base64.b64decode(value['data'], validate=True))
                out.flush()
                os.fsync(out.fileno())
            temp.chmod(value['mode'])
            if live:
                os.chown(temp, value['uid'], value['gid'])
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def file_record(data, mode=0o644, uid=0, gid=0):
    if isinstance(data, str):
        data = data.encode()
    return dict(kind='file', data=base64.b64encode(data).decode(), sha256=digest(data), mode=mode, uid=uid, gid=gid)


def state_path(tree, component):
    if component not in {'basic', 'desktop', 'wifi'}:
        raise RuntimeError('Unknown component')
    return tree.path(f'{STATE}/{component}/manifest.json')


def load_state(tree, component):
    p = state_path(tree, component)
    if not p.exists():
        return None
    info = p.lstat()
    if not stat.S_ISREG(info.st_mode) or (tree.live and (info.st_uid or info.st_mode & 0o077)):
        raise RuntimeError('Installation manifest must be a private root-owned regular file')
    data = json.loads(p.read_text())
    if data.get('component') != component:
        raise RuntimeError('Installation manifest component mismatch')
    return data


def save_state(tree, component, state):
    p = state_path(tree, component)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.parent.chmod(0o700)
    write_record(p, file_record(json.dumps(state, indent=2) + '\n', 0o600), tree.live)


def pending_path(tree, component):
    return state_path(tree, component).with_name('pending.json')


def recover(tree, component):
    tree.require_mutation()
    path = pending_path(tree, component)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (tree.live and (info.st_uid or info.st_mode & 0o077)):
        raise RuntimeError('Recovery journal must be a private root-owned regular file')
    journal = json.loads(path.read_text())
    for name, previous in reversed(list(journal['before'].items())):
        write_record(tree.path(name), previous, tree.live)
    if journal['old_state']:
        save_state(tree, component, journal['old_state'])
    else:
        state_path(tree, component).unlink(missing_ok=True)
    path.unlink()
    print('Interrupted file transaction rolled back; no services were activated by install.')


def install(tree, component, files, adopt=False, metadata=None):
    tree.require_mutation()
    if pending_path(tree, component).exists():
        raise RuntimeError('Interrupted installation detected; run recover for this component first')
    old = load_state(tree, component)
    if component == 'desktop' and old and old['metadata'].get('user', {}).get('uid') != (metadata or {}).get('user', {}).get('uid'):
        raise RuntimeError('Uninstall the desktop component before changing its selected account')
    originals = dict(old['originals']) if old else {}
    before = {}
    # Validate the entire plan before any destination is changed.
    for name in files:
        current = capture(tree.path(name))
        if old and name in old['installed']:
            if not same(current, old['installed'][name]):
                raise RuntimeError('Locally changed managed file: ' + name)
        elif current['kind'] != 'absent' and not adopt:
            raise RuntimeError('Existing file requires --adopt-existing after review: ' + name)
        if name not in originals:
            originals[name] = current
        before[name] = current
    removed = set(old['installed']) - set(files) if old else set()
    for name in removed:
        current = capture(tree.path(name))
        if not same(current, old['installed'][name]):
            raise RuntimeError('Locally changed obsolete file: ' + name)
        before[name] = current
    journal = pending_path(tree, component)
    journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    journal.parent.chmod(0o700)
    write_record(journal, file_record(json.dumps({'before': before, 'old_state': old}), 0o600), tree.live)
    changed = []
    try:
        for name, value in files.items():
            changed.append(name)
            write_record(tree.path(name), value, tree.live)
        for name in removed:
            changed.append(name)
            write_record(tree.path(name), originals[name], tree.live)
        result = dict(version=VERSION, component=component, originals=originals, installed=files, metadata=metadata or {})
        # Preserve activation bookkeeping across upgrades.
        if old and 'activation' in old:
            result['activation'] = old['activation']
        save_state(tree, component, result)
        journal.unlink()
    except BaseException:
        for name in reversed(changed):
            write_record(tree.path(name), before[name], tree.live)
        if old:
            save_state(tree, component, old)
        else:
            state_path(tree, component).unlink(missing_ok=True)
        journal.unlink(missing_ok=True)
        raise
    return result


def run(*args, check=True, timeout=180):
    return subprocess.run(args, check=check, text=True, capture_output=True, timeout=timeout)


def userctl(user, *args, check=True):
    return run('runuser', '-u', user['name'], '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{user["uid"]}',
               f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{user["uid"]}/bus',
               'systemctl', '--user', *args, check=check)


def check_hardware(tree, wifi=False):
    if tree.sysfs_path('/sys/class/dmi/id/product_name').read_text().strip() != MODEL:
        raise RuntimeError('This candidate supports MacBookPro13,2 only')
    if wifi:
        matches = []
        for p in tree.sysfs_path('/sys/bus/pci/devices').iterdir():
            try:
                ids = [tree.sysfs_path(f'/sys/bus/pci/devices/{p.name}/{n}').read_text().strip() for n in ('vendor', 'device', 'subsystem_vendor', 'subsystem_device')]
                if ids == ['0x14e4', '0x43ba', '0x106b', '0x0157']:
                    matches.append(p)
            except OSError:
                pass
        if len(matches) != 1:
            raise RuntimeError('Expected exactly one matching BCM43602 Apple subsystem')
    else:
        found = []
        for p in tree.sysfs_path('/sys/bus/usb/devices').iterdir():
            try:
                if tree.sysfs_path(f'/sys/bus/usb/devices/{p.name}/idVendor').read_text().strip() == '05ac' and tree.sysfs_path(f'/sys/bus/usb/devices/{p.name}/idProduct').read_text().strip() == '8600':
                    found.append(p)
            except OSError:
                pass
        if len(found) != 1:
            raise RuntimeError('Expected one normal T1 05ac:8600; recovery-mode firmware startup needs separate diagnosis')


def dkms_config(package, modules):
    value = f'PACKAGE_NAME="{package}"\nPACKAGE_VERSION="{VERSION}"\nAUTOINSTALL="yes"\n'
    for index, module in enumerate(modules):
        value += f'BUILT_MODULE_NAME[{index}]="{module}"\nDEST_MODULE_LOCATION[{index}]="/updates/dkms"\n'
    value += 'MAKE[0]="make KDIR=/usr/lib/modules/$kernelver/build"\n'
    return value


def basic_files(source=SOURCE):
    result = {}
    for p in (source / 'drivers/legacy').iterdir():
        if (p.suffix in {'.c', '.h'} and not p.name.endswith('.mod.c')) or p.name == 'Makefile':
            result[f'/usr/src/omarchy-t1-basic-{VERSION}/{p.name}'] = file_record(p.read_bytes())
    result[f'/usr/src/omarchy-t1-basic-{VERSION}/dkms.conf'] = file_record(dkms_config('omarchy-t1-basic', ['apple-ibridge', 'apple-ib-tb', 'apple-ib-als']))
    result['/usr/local/sbin/apple-t1-touchbar-bind'] = file_record((source / 'runtime/apple-t1-touchbar-bind').read_bytes(), 0o755)
    result['/etc/systemd/system/apple-t1-touchbar.service'] = file_record(BASIC_UNIT)
    return result


def desktop_files(user, features, source=SOURCE, weather_location=None):
    if set(features) - FEATURES:
        raise RuntimeError('Unknown optional feature')
    binary = source / 'build/selector'
    if not binary.is_file() or binary.read_bytes()[:4] != b'\x7fELF':
        raise RuntimeError('Build the renderer as your normal user with make first')
    result = {f'{LIB}/selector': file_record(binary.read_bytes(), 0o755)}
    for p in (source / 'touchbar').glob('*.py'):
        result[f'{LIB}/{p.name}'] = file_record(p.read_bytes())
    for name in ('hardware-service.py', 'config.py'):
        result[f'{LIB}/{name}'] = file_record((source / 'runtime' / name).read_bytes())
    config = dict(user=user.name, uid=user.uid, home=user.home, features=sorted(set(features)))
    if weather_location is not None:
        if 'weather' not in features or len(weather_location) != 2:
            raise RuntimeError('Weather coordinates require the weather feature')
        lat, lon = weather_location
        if not all(math.isfinite(x) for x in (lat, lon)) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise RuntimeError('Weather coordinates are outside valid ranges')
        config['weather_location'] = weather_location
    result['/etc/omarchy-t1/desktop.json'] = file_record(json.dumps(config) + '\n')
    result['/etc/systemd/system/omarchy-touchbar.service'] = file_record(DESKTOP_UNIT)
    result['/etc/systemd/system/omarchy-touchbar-sleep.service'] = file_record(SLEEP_UNIT)
    result['/etc/udev/rules.d/99-omarchy-touchbar.rules'] = file_record(
        'SUBSYSTEM=="drm", KERNEL=="card[0-9]*", DRIVERS=="appletbdrm", ENV{ID_SEAT}="seat-touchbar-selector", TAG-="master-of-seat"\n'
        'SUBSYSTEM=="input", ATTRS{idVendor}=="05ac", ATTRS{idProduct}=="8600", ATTRS{bConfigurationValue}=="2", ENV{ID_SEAT}="seat-touchbar-selector"\n')
    result['/etc/systemd/user/omarchy-t1-session.service'] = file_record(USER_UNIT)
    for p in (source / 'drivers/graphics').iterdir():
        if (p.suffix == '.c' and not p.name.endswith('.mod.c')) or p.name == 'Makefile':
            result[f'/usr/src/omarchy-t1-graphics-{VERSION}/{p.name}'] = file_record(p.read_bytes())
    result[f'/usr/src/omarchy-t1-graphics-{VERSION}/dkms.conf'] = file_record(dkms_config('omarchy-t1-graphics', ['appletbdrm', 'omarchy-t1-raw']))
    return result


def recovery_files(component):
    # Each component carries an independent offline management command. It can
    # recover/uninstall even if the original source checkout has been deleted.
    path = f'/usr/local/lib/omarchy-t1/{component}-manage.py'
    wrapper = f'#!/bin/sh\nexec /usr/bin/python3 {path} --component {component} "$@"\n'
    return {path: file_record(HERE.read_bytes()),
            f'/usr/local/sbin/omarchy-t1-{component}': file_record(wrapper, 0o755)}


def wifi_files(tree, template, country, revision, band):
    if not re.fullmatch(r'[A-Z]{2}', country) or country == '00' or not 0 <= revision <= 999:
        raise RuntimeError('Supply your real two-letter country and a firmware revision (0–999)')
    if band not in {'2.4', '5'}:
        raise RuntimeError('Choose the separately tested 2.4 or 5 GHz mode')
    raw = Path(template).read_bytes()
    if digest(raw) != TEMPLATE_SHA256:
        raise RuntimeError('Board-data template differs from the reviewed pinned source')
    rom = tree.sysfs_path('/sys/firmware/efi/efivars/ROM-4d1ede05-38c7-4a6a-9cc6-4bcca8b38c14').read_bytes()
    if len(rom) != 10 or rom[4] & 1 or rom[4:] in (bytes(6), bytes.fromhex('00904c0df43e')):
        raise RuntimeError('Valid local EFI ROM address unavailable; no board data changed')
    data = raw.decode('ascii')
    replacements = dict(macaddr=':'.join(f'{x:02x}' for x in rom[4:]), ccode=country, regrev=str(revision),
                        boardflags3='0x00000003' if band == '2.4' else '0x00000300')
    for key, value in replacements.items():
        data, count = re.subn(r'^' + key + r'=.*$', key + '=' + value, data, flags=re.M)
        if count != 1:
            raise RuntimeError('Missing or duplicate board-data field: ' + key)
    # Don't overwrite or fight another feature-disable policy.
    existing = []
    folder = tree.path('/etc/modprobe.d')
    for p in folder.glob('*.conf'):
        if p.name == 'omarchy-t1-wifi.conf':
            continue
        for line in p.read_text().splitlines():
            if re.match(r'^\s*options\s+brcmfmac\s', line):
                existing.extend(re.findall(r'feature_disable=(\S+)', line.split('#', 1)[0]))
    if any(int(x, 0) != 0x82000 for x in existing):
        raise RuntimeError('An existing brcmfmac feature_disable policy needs manual review')
    return {BOARD: file_record(data, 0o600),
            '/etc/mkinitcpio.conf.d/omarchy_t1_wifi.conf': file_record(f'FILES+=("{BOARD}")\n'),
            '/etc/modprobe.d/omarchy-t1-wifi.conf': file_record('options brcmfmac feature_disable=0x82000\n')}


def stop_component(state):
    component = state['component']
    if component == 'desktop':
        run('systemctl', 'disable', '--now', 'omarchy-touchbar-sleep.service', check=False)
        userctl(state['metadata']['user'], 'disable', '--now', 'omarchy-t1-session.service', check=False)
        run('systemctl', 'disable', '--now', 'omarchy-touchbar.service')
    elif component == 'basic':
        run('systemctl', 'disable', '--now', 'apple-t1-touchbar.service')


def service_snapshot(component, metadata):
    units = ['apple-t1-touchbar.service'] if component == 'basic' else ['omarchy-touchbar.service', 'omarchy-touchbar-sleep.service', 'apple-t1-touchbar.service']
    result = {'system': {}, 'user': {}}
    for unit in units:
        result['system'][unit] = {'enabled': run('systemctl', 'is-enabled', unit, check=False).stdout.strip(),
                                  'active': run('systemctl', 'is-active', unit, check=False).stdout.strip() == 'active'}
    if component == 'desktop':
        user = metadata['user']
        if not Path(f'/run/user/{user["uid"]}/bus').exists():
            raise RuntimeError('Log in as the selected desktop user before installing the desktop component')
        for unit in ('omarchy-t1-session.service', 'omarchy-touchbar-session.service'):
            result['user'][unit] = {'enabled': userctl(user, 'is-enabled', unit, check=False).stdout.strip(),
                                    'active': userctl(user, 'is-active', unit, check=False).stdout.strip() == 'active'}
    return result


def restore_services(state):
    snapshot = state['metadata'].get('prior_services')
    if not snapshot:
        return
    for scope, records in snapshot.items():
        for unit, value in records.items():
            call = (lambda *a: userctl(state['metadata']['user'], *a, check=False)) if scope == 'user' else (lambda *a: run('systemctl', *a, check=False))
            if value['enabled'] in ('enabled', 'enabled-runtime'):
                call('enable', *(['--runtime'] if value['enabled'] == 'enabled-runtime' else []), unit)
            elif value['enabled'] not in ('masked', 'masked-runtime', 'static', 'indirect'):
                call('disable', unit)
            call('start' if value['active'] else 'stop', unit)


def uninstall(tree, component):
    tree.require_mutation()
    state = load_state(tree, component)
    if not state:
        raise RuntimeError('Component is not managed by this installer')
    if pending_path(tree, component).exists():
        raise RuntimeError('Recover the interrupted installation before uninstalling')
    if component == 'basic' and load_state(tree, 'desktop'):
        raise RuntimeError('Remove the desktop component before removing its basic fallback')
    for name, expected in state['installed'].items():
        if not same(capture(tree.path(name)), expected):
            raise RuntimeError('Managed file changed; preserve and review it before removal: ' + name)
    if tree.live and state.get('activation'):
        stop_component(state)
        if state['activation'].get('dkms_owned'):
            package = 'omarchy-t1-basic' if component == 'basic' else 'omarchy-t1-graphics'
            run('dkms', 'remove', '-m', package, '-v', VERSION, '--all')
    before = {name: capture(tree.path(name)) for name in state['installed']}
    journal = pending_path(tree, component)
    write_record(journal, file_record(json.dumps({'before': before, 'old_state': state}), 0o600), tree.live)
    try:
        for name in reversed(list(state['installed'])):
            write_record(tree.path(name), state['originals'][name], tree.live)
        if tree.live and state.get('activation'):
            if component == 'wifi':
                run('limine-mkinitcpio', 'linux-omarchy', timeout=300)
            else:
                run('systemctl', 'daemon-reload')
                if component == 'desktop':
                    run('udevadm', 'control', '--reload-rules')
                    userctl(state['metadata']['user'], 'daemon-reload', check=False)
                restore_services(state)
    except BaseException:
        # Keep the journal and manifest so recovery remains available after an
        # interrupted restore or failed boot-image rebuild.
        raise
    state_path(tree, component).unlink()
    journal.unlink()
    print('Original files restored. Save work and shut down/power on if original controls remain dark.')


def activate(tree, component, reuse=False, untested=False):
    tree.require_mutation()
    if not tree.live:
        raise RuntimeError('Activation is forbidden with an alternate --root')
    state = load_state(tree, component)
    if not state:
        raise RuntimeError('Install this component first')
    if pending_path(tree, component).exists():
        raise RuntimeError('Recover the interrupted installation before activation')
    check_hardware(tree, wifi=component == 'wifi')
    if os.uname().release != TESTED_KERNEL and not untested:
        raise RuntimeError('Untested kernel; build and review first, then explicitly use --allow-untested-kernel')
    for name, expected in state['installed'].items():
        if not same(capture(tree.path(name)), expected):
            raise RuntimeError('Managed file differs from installation: ' + name)
    if component == 'desktop':
        run(str(tree.path(f'{LIB}/selector')), '--self-test', timeout=30)
    activation = state.setdefault('activation', {'dkms_owned': False, 'attempted': True})
    save_state(tree, component, state)
    if component == 'wifi':
        backup = tree.path(f'{STATE}/wifi/boot-before')
        backup.mkdir(mode=0o700, exist_ok=True)
        checksums = {}
        for name in ('/boot/EFI/Linux/omarchy_linux-omarchy.efi', '/boot/limine.conf'):
            source = tree.path(name)
            if not source.is_file():
                raise RuntimeError('Expected Omarchy Limine boot file is missing; review the boot layout before activation')
            destination = backup / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
                destination.chmod(0o600)
                if digest(destination.read_bytes()) != digest(source.read_bytes()):
                    raise RuntimeError('Boot backup verification failed')
            checksums[destination.name] = digest(destination.read_bytes())
        (backup / 'SHA256.json').write_text(json.dumps(checksums, indent=2) + '\n')
        run('limine-mkinitcpio', 'linux-omarchy', timeout=300)
        activation['ready_for_reboot'] = True
        save_state(tree, component, state)
        print('Board data included in boot image. Current connection was not reset; reboot when ready.')
        return
    modules = ['apple_ibridge', 'apple_ib_tb', 'apple_ib_als'] if component == 'basic' else ['appletbdrm', 'omarchy_t1_raw']
    package = 'omarchy-t1-basic' if component == 'basic' else 'omarchy-t1-graphics'
    if not reuse:
        # Don't silently replace another DKMS module provider.
        for module in modules:
            info = run('modinfo', '-n', module, check=False)
            if info.returncode == 0 and '/updates/' in info.stdout and package not in info.stdout:
                registered = run('dkms', 'status', '-m', package, '-v', VERSION, check=False)
                if not registered.stdout.strip():
                    raise RuntimeError('Existing out-of-tree modules need review; --reuse-modules uses them without changing DKMS')
        registered = run('dkms', 'status', '-m', package, '-v', VERSION, check=False)
        if not registered.stdout.strip():
            run('dkms', 'add', '-m', package, '-v', VERSION)
        activation['dkms_owned'] = True
        save_state(tree, component, state)
        run('dkms', 'build', '-m', package, '-v', VERSION, '-k', os.uname().release, timeout=300)
        run('dkms', 'install', '-m', package, '-v', VERSION, '-k', os.uname().release, timeout=300)
    for module in modules:
        run('modinfo', '-n', module)
    run('systemctl', 'daemon-reload')
    if component == 'basic':
        run('systemctl', 'enable', '--now', 'apple-t1-touchbar.service')
    else:
        run('modinfo', '-n', 'apple_ib_tb')
        user = state['metadata']['user']
        if not Path(f'/run/user/{user["uid"]}/bus').exists():
            raise RuntimeError('The selected desktop user must be logged in to activate the desktop component')
        run('udevadm', 'control', '--reload-rules')
        userctl(user, 'daemon-reload')
        userctl(user, 'disable', '--now', 'omarchy-touchbar-session.service', check=False)
        userctl(user, 'enable', '--now', 'omarchy-t1-session.service')
        userctl(user, 'restart', 'omarchy-t1-session.service')
        run('systemctl', 'enable', 'omarchy-touchbar.service', 'omarchy-touchbar-sleep.service')
        run('systemctl', 'restart', 'omarchy-touchbar.service')
        run('systemctl', 'is-active', 'omarchy-touchbar.service')
        userctl(user, 'is-active', 'omarchy-t1-session.service')
        invocation = run('systemctl', 'show', 'omarchy-touchbar.service', '-p', 'InvocationID', '--value').stdout.strip()
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            output = run('journalctl', '_SYSTEMD_INVOCATION_ID=' + invocation, '--no-pager', '-o', 'cat', timeout=8).stdout
            if 'SELECTOR_READY persistent' in output and f'RENDERER_UID {user["uid"]}' in output:
                break
            time.sleep(1)
        else:
            run('systemctl', 'stop', 'omarchy-touchbar.service', check=False)
            run('systemctl', 'start', 'apple-t1-touchbar.service', check=False)
            raise RuntimeError('Renderer failed readiness; original bindings selected. Review logs; firmware illumination may require shutdown/power-on')
    activation['started'] = True
    save_state(tree, component, state)
    print('Services started. Physical operation still needs confirmation.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan', 'install', 'activate', 'uninstall', 'recover', 'status'])
    p.add_argument('--component', required=True, choices=['basic', 'desktop', 'wifi'])
    p.add_argument('--root', default='/')
    p.add_argument('--user', default=os.environ.get('SUDO_USER') or os.environ.get('USER'))
    p.add_argument('--features', default='', help='Comma-separated optional pages; default is core controls only')
    p.add_argument('--weather-location', help='Explicit fallback latitude,longitude; sent to Open-Meteo only if shared weather is unavailable')
    p.add_argument('--template', type=Path)
    p.add_argument('--country')
    p.add_argument('--regrev', type=int, default=0)
    p.add_argument('--band', choices=['2.4', '5'])
    p.add_argument('--adopt-existing', action='store_true')
    p.add_argument('--reuse-modules', action='store_true')
    p.add_argument('--allow-untested-kernel', action='store_true')
    args = p.parse_args()
    tree = Tree(args.root)
    try:
        if args.action == 'status':
            state = load_state(tree, args.component)
            print(json.dumps({'component': args.component, 'managed': bool(state), 'version': state.get('version') if state else None,
                              'activation': state.get('activation') if state else None}, indent=2))
            return
        if args.action == 'uninstall':
            uninstall(tree, args.component)
            return
        if args.action == 'recover':
            recover(tree, args.component)
            return
        if args.action == 'activate':
            activate(tree, args.component, args.reuse_modules, args.allow_untested_kernel)
            return
        metadata = {}
        if args.component == 'basic':
            files = basic_files()
        elif args.component == 'desktop':
            user = account(args.user)
            features = [x for x in args.features.split(',') if x]
            location = [float(x) for x in args.weather_location.split(',')] if args.weather_location else None
            files = desktop_files(user, features, weather_location=location)
            metadata['user'] = dict(name=user.name, uid=user.uid, gid=user.gid, home=user.home)
            metadata['features'] = features
        else:
            if not args.template or not args.country or not args.band:
                raise RuntimeError('Wi-Fi requires --template, --country and --band; template data is not bundled')
            files = wifi_files(tree, args.template, args.country, args.regrev, args.band)
            metadata.update(country=args.country, regrev=args.regrev, band=args.band)
        files.update(recovery_files(args.component))
        if args.action == 'install':
            check_hardware(tree, wifi=args.component == 'wifi')
            previous = load_state(tree, args.component)
            if tree.live and args.component != 'wifi':
                metadata['prior_services'] = previous['metadata'].get('prior_services') if previous else service_snapshot(args.component, metadata)
            install(tree, args.component, files, args.adopt_existing, metadata)
            print('FILES_INSTALLED; activation is a separate command. Staged root:' if not tree.live else 'FILES_INSTALLED; activate after reviewing the plan.', tree.root)
        else:
            print(json.dumps({'component': args.component, 'root': str(tree.root), 'files': sorted(files),
                              'note': 'Plan only; no files, drivers, services or connections changed'}, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        # Never print board file contents or subprocess diagnostics containing private identifiers.
        print('ERROR: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
