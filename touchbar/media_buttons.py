# SPDX-License-Identifier: GPL-2.0-only
"""Microphone mute and Omarchy media control, with background-only status polling."""
import json
import os
import subprocess
import threading


class MediaButtons:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.state = (-1, -1)
        threading.Thread(target=self._poll, name='touchbar-media-status', daemon=True).start()

    @staticmethod
    def run(*args):
        result = subprocess.run(args, check=True, text=True, capture_output=True,
                                timeout=1, env={**os.environ, 'LC_ALL': 'C'})
        return result.stdout.strip()

    @classmethod
    def read_status(cls):
        mic, playing = -1, -1
        try:
            raw = cls.run('/usr/bin/pactl', 'get-source-mute', '@DEFAULT_SOURCE@')
            mic = {'Mute: yes': 1, 'Mute: no': 0}.get(raw, -1)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            report = json.loads(cls.run('/usr/bin/omarchy', 'shell', 'media', 'status'))
            if report.get('hasPlayer') is True and isinstance(report.get('playing'), bool):
                playing = int(report['playing'])
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
            pass
        return mic, playing

    def _poll(self):
        while True:
            self.wake.clear()
            state = self.read_status()
            with self.lock:
                self.state = state
            self.wake.wait(1)

    def packet(self):
        with self.lock:
            mic, playing = self.state
        return f'AUX {mic} {playing}'

    def apply(self, message):
        if message == 'BUTTON MIC_MUTE':
            self.run('/usr/bin/pactl', 'set-source-mute', '@DEFAULT_SOURCE@', 'toggle')
        elif message == 'BUTTON PLAYPAUSE':
            result = self.run('/usr/bin/omarchy', 'shell', 'media', 'playPause')
            if result not in ('ok', 'unhandled'):
                raise RuntimeError('Unexpected media response')
        else:
            return False
        self.wake.set()
        return True
