#!/usr/bin/env python3
"""File-shortcut behaviour checks; no desktop or real folder is opened."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from file_shortcuts import FileShortcuts


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.home_patch = patch('file_shortcuts.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.processes = []
        self.files = FileShortcuts(self.processes)

    @patch('file_shortcuts.subprocess.Popen')
    @patch('file_shortcuts.subprocess.run')
    def test_default_opens_home(self, run, popen):
        self.assertTrue(self.files.apply('FILES OPEN DEFAULT'))
        run.assert_not_called()
        self.assertEqual(popen.call_args.args[0][-2:], ['/usr/bin/xdg-open', str(self.home)])
        self.assertEqual(self.processes, [popen.return_value])
        self.assertNotIn('shell', popen.call_args.kwargs)

    @patch('file_shortcuts.subprocess.Popen')
    @patch('file_shortcuts.subprocess.run')
    def test_all_configured_folders_and_spaces(self, run, popen):
        for key in self.files.folders:
            if key == 'DEFAULT':
                continue
            folder = self.home / ('Mes dossiers ' + key)
            folder.mkdir()
            run.return_value = subprocess.CompletedProcess([], 0, str(folder) + '\n', '')
            self.assertTrue(self.files.apply('FILES OPEN ' + key))
            self.assertEqual(run.call_args.args[0], ['/usr/bin/xdg-user-dir', key])
            self.assertEqual(popen.call_args.args[0][-1], str(folder))

    @patch('file_shortcuts.subprocess.run', side_effect=FileNotFoundError)
    def test_missing_xdg_tool_uses_standard_directory(self, _):
        self.assertEqual(self.files.destination('DOWNLOAD'), self.home / 'Downloads')

    @patch('file_shortcuts.subprocess.run')
    def test_invalid_config_falls_back(self, run):
        for output, code in [('', 0), ('relative/path\n', 0), ('/elsewhere\n', 1)]:
            run.return_value = subprocess.CompletedProcess([], code, output, '')
            self.assertEqual(self.files.destination('DOCUMENTS'), self.home / 'Documents')

    @patch('file_shortcuts.subprocess.Popen')
    @patch('file_shortcuts.subprocess.run')
    def test_unavailable_folder_does_not_launch_or_create(self, run, popen):
        folder = self.home / 'missing'
        run.return_value = subprocess.CompletedProcess([], 0, str(folder), '')
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            self.files.apply('FILES OPEN DOCUMENTS')
        popen.assert_not_called()
        self.assertFalse(folder.exists())

    @patch('file_shortcuts.subprocess.Popen')
    @patch('file_shortcuts.subprocess.run')
    def test_rejects_arbitrary_paths_and_commands(self, run, popen):
        for message in ['FILES OPEN /tmp', 'FILES OPEN DOWNLOAD;whoami', 'FILES OPEN DEFAULT\n',
                        'FILES OPEN DEFAULT extra', 'FILES OPEN TRASH', 'FILES OPEN', '']:
            self.assertFalse(self.files.apply(message))
        run.assert_not_called()
        popen.assert_not_called()

    @patch('file_shortcuts.subprocess.run')
    def test_disabled_xdg_folder_can_resolve_to_home(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, str(self.home), '')
        self.assertEqual(self.files.destination('DESKTOP'), self.home)

    @patch('file_shortcuts.subprocess.Popen', side_effect=FileNotFoundError)
    def test_launch_error_is_reported(self, _):
        with self.assertRaises(FileNotFoundError):
            self.files.apply('FILES OPEN DEFAULT')
        self.assertEqual(self.processes, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
