# SPDX-License-Identifier: GPL-2.0-only
"""Open the desktop user's file manager, using their XDG folder locations."""
from pathlib import Path
import subprocess


class FileShortcuts:
    folders = {
        'DEFAULT': None,
        'DOCUMENTS': 'Documents',
        'DOWNLOAD': 'Downloads',
        'DESKTOP': 'Desktop',
        'PICTURES': 'Pictures',
        'MUSIC': 'Music',
        'VIDEOS': 'Videos',
    }

    def __init__(self, processes):
        self.processes = processes

    def destination(self, key):
        fallback = self.folders[key]
        home = Path.home()
        if fallback is None:
            return home
        try:
            result = subprocess.run(
                ['/usr/bin/xdg-user-dir', key], capture_output=True,
                text=True, timeout=2, check=False)
            configured = Path(result.stdout.strip()) if result.stdout.strip() else None
            if result.returncode == 0 and configured is not None and configured.is_absolute():
                return configured
        except FileNotFoundError:
            pass
        return home / fallback

    def apply(self, message):
        # The root renderer sends fixed identifiers, never paths or shell commands.
        if message not in {'FILES OPEN ' + key for key in self.folders}:
            return False
        destination = self.destination(message.split()[-1])
        if not destination.is_dir():
            raise RuntimeError('The selected folder is unavailable')
        command = ['/usr/bin/xdg-open', str(destination)]
        if Path('/usr/bin/uwsm-app').is_file():
            command = ['/usr/bin/uwsm-app', '--', *command]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        self.processes.append(process)
        return True
