# SPDX-License-Identifier: GPL-2.0-only
"""Network telemetry from the same native commands as Omarchy's network panel."""
import subprocess
import threading
import time


class WifiDetails:
    fields = ('kind', 'name', 'link', 'signal', 'band', 'traffic', 'ip', 'latency', 'iface')

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.opened = False
        self.values = {}
        self.previous = None
        threading.Thread(target=self._poll, name='touchbar-wifi-details', daemon=True).start()

    @staticmethod
    def run(*args):
        return subprocess.run(args, check=True, text=True, capture_output=True, timeout=4).stdout

    def read_status(self, detailed):
        try:
            parts = self.run('/usr/bin/omarchy-network-status').rstrip('\n').split('\t')
            kind = parts[0] if parts[0] in ('wifi', 'ethernet', 'disconnected') else 'unknown'
            values = {'kind': kind, 'name': parts[1] if len(parts) > 1 else '-'}
            if kind not in ('wifi', 'ethernet'):
                self.previous = None
                return values
            if len(parts) > 2 and parts[2].isdigit():
                values['signal'] = parts[2] + '%'
            if not detailed:
                self.previous = None
                return values
            info = dict(line.split('\t', 1) for line in self.run('/usr/bin/omarchy-network-status', '--verbose').splitlines() if '\t' in line)
            if not info.get('iface'):
                self.previous = None
                return {'kind': 'unknown'}
            values.update(iface=info['iface'], ip=info.get('ip', '-'))
            if kind == 'wifi':
                values['name'] = info.get('ssid') or values['name']
                values['link'] = info.get('bitrate', '').strip() or '-'
                if info.get('signal_dbm'):
                    values['signal'] = info['signal_dbm'] + ' dBm'
                freq = float(info.get('freq') or 0)
                values['band'] = '2.4 GHz' if 2400 <= freq < 2500 else '5 GHz' if 4900 <= freq < 5925 else '6 GHz' if 5925 <= freq < 7125 else '-'
            else:
                values['link'] = info.get('speed', '-') + ' Mbit/s' if info.get('speed', '').isdigit() else '-'
                values['band'] = 'Ethernet'
            router, internet = info.get('router_ping_ms'), info.get('internet_ping_ms')
            values['latency'] = f'{router or "-"} / {internet or "-"} ms'
            if info.get('rx_bytes', '').isdigit() and info.get('tx_bytes', '').isdigit():
                current = (info['iface'], time.monotonic(), int(info['rx_bytes']), int(info['tx_bytes']))
                old = self.previous
                if old and old[0] == current[0] and current[1] > old[1] and current[2] >= old[2] and current[3] >= old[3]:
                    factor = 8 / (current[1] - old[1]) / 1_000_000
                    values['traffic'] = f'{(current[2]-old[2])*factor:.1f} / {(current[3]-old[3])*factor:.1f} Mbps'
                self.previous = current
            else:
                self.previous = None
            return values
        except (OSError, subprocess.SubprocessError, ValueError):
            self.previous = None
            return {'kind': 'unknown'}

    def _poll(self):
        while True:
            self.wake.clear()
            with self.lock:
                opened = self.opened
            values = self.read_status(opened)
            with self.lock:
                # A close/open transition must not publish old detail fields.
                if opened == self.opened:
                    self.values = values
            self.wake.wait(5)

    def show(self, opened):
        with self.lock:
            if self.opened == opened:
                return
            self.opened = opened
        self.wake.set()

    def packet(self):
        with self.lock:
            values = [self.values.get(key, '-') for key in self.fields]
        clean = []
        for value in values:
            text = ''.join(c for c in value if c.isprintable())
            clean.append(text.encode('utf-8')[:127].decode('utf-8', errors='ignore') or '-')
        return 'WIFIINFO ' + '\t'.join(clean)
