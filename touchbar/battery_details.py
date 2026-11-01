# SPDX-License-Identifier: GPL-2.0-only
"""Read the same formatted battery data as Omarchy's top-bar power panel."""
import subprocess
import threading


class BatteryDetails:
    fields = ('percentage', 'state', 'rate', 'size', 'time', 'cycles', 'threshold', 'profile')
    profile_order = ('power-saver', 'balanced', 'performance')

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.operation_lock = threading.Lock()
        self.changing_profile = False
        self.values = {}
        threading.Thread(target=self._poll, name='touchbar-battery-details', daemon=True).start()

    @staticmethod
    def run(*args):
        return subprocess.run(args, check=True, text=True, capture_output=True, timeout=3).stdout

    @classmethod
    def read_profiles(cls):
        raw = cls.run('/usr/bin/omarchy-powerprofiles-list', '--active-state')
        available = set()
        active = None
        for line in raw.splitlines():
            name, sep, state = line.partition('\t')
            if sep and name in cls.profile_order and state in ('0', '1'):
                available.add(name)
                if state == '1':
                    active = name
        return [name for name in cls.profile_order if name in available], active

    @classmethod
    def read_status(cls):
        values = {}
        try:
            raw = cls.run('/usr/bin/omarchy-battery-status', '--shell')
            for line in raw.splitlines():
                key, sep, value = line.partition('\t')
                if sep and key in cls.fields:
                    values[key] = value
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            _, active = cls.read_profiles()
            if active:
                values['profile'] = active
        except (OSError, subprocess.SubprocessError):
            pass
        return values

    def _poll(self):
        while True:
            self.wake.clear()
            with self.operation_lock:
                values = self.read_status()
                with self.lock:
                    self.values = values
            self.wake.wait(5)

    def cycle_profile(self):
        with self.lock:
            if self.changing_profile:
                return
            self.changing_profile = True
        threading.Thread(target=self._cycle_profile, name='touchbar-power-profile', daemon=True).start()

    def _cycle_profile(self):
        try:
            with self.operation_lock:
                profiles, active = self.read_profiles()
                if active not in profiles or len(profiles) < 2:
                    return
                target = profiles[(profiles.index(active) + 1) % len(profiles)]
                self.run('/usr/bin/omarchy-powerprofiles-set', 'autodetect', target)
                # Report the actual state, including if another control changed it.
                values = self.read_status()
                with self.lock:
                    self.values = values
                print('POWER_PROFILE_SELECTED ' + target, flush=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print('POWER_PROFILE_ERROR ' + str(exc), flush=True)
        finally:
            with self.lock:
                self.changing_profile = False
            self.wake.set()

    def refresh(self):
        self.wake.set()

    def packet(self):
        with self.lock:
            values = [self.values.get(key, '-') for key in self.fields]
        # Bounded printable ASCII fields; no command output can inject packets.
        clean = [''.join(c for c in value if 32 <= ord(c) <= 126)[:47] or '-' for value in values]
        return 'BATINFO ' + '\t'.join(clean)
