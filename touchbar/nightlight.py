# SPDX-License-Identifier: GPL-2.0-only
"""Omarchy nightlight status and toggle, outside the session event loop."""
import json
import subprocess
import threading


class Nightlight:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.state = -1
        self.toggling = False
        threading.Thread(target=self._poll, name='touchbar-nightlight-status', daemon=True).start()

    @staticmethod
    def read_status():
        try:
            result = subprocess.run(['/usr/bin/omarchy-toggle-nightlight', '--status'],
                                    check=True, text=True, capture_output=True, timeout=2)
            report = json.loads(result.stdout)
            if isinstance(report.get('enabled'), bool) and isinstance(report.get('temperature'), (int, float)):
                return int(report['enabled'])
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            pass
        return -1

    def _poll(self):
        while True:
            self.wake.clear()
            value = self.read_status()
            with self.lock:
                self.state = value
            self.wake.wait(1)

    def packet(self):
        with self.lock:
            return f'NIGHTLIGHT {self.state}'

    def toggle(self):
        with self.lock:
            if self.toggling:
                return
            self.toggling = True
        threading.Thread(target=self._toggle, name='touchbar-nightlight-toggle', daemon=True).start()

    def _toggle(self):
        try:
            subprocess.run(['/usr/bin/omarchy-toggle-nightlight'], check=True,
                           text=True, capture_output=True, timeout=5)
            print('NIGHTLIGHT_TOGGLED', flush=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print('NIGHTLIGHT_ERROR ' + str(exc), flush=True)
        finally:
            with self.lock:
                self.toggling = False
            self.wake.set()
