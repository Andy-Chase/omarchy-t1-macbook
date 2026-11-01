# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the real file transaction and configuration code in isolated roots."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import manage


class RootFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tree = manage.Tree(self.root)
        self.user = manage.User('other-owner', 4242, 4343, '/home/other-owner')

    def put(self, name, value):
        path = self.tree.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path


class InstallationTests(RootFixture):
    def test_install_upgrade_uninstall_restores_original_bytes_and_mode(self):
        path = self.put('/etc/example.conf', 'original\n')
        path.chmod(0o640)
        manage.install(self.tree, 'desktop', {'/etc/example.conf': manage.file_record('one')}, adopt=True)
        manage.install(self.tree, 'desktop', {'/etc/example.conf': manage.file_record('two')})
        self.assertEqual(path.read_text(), 'two')
        manage.uninstall(self.tree, 'desktop')
        self.assertEqual(path.read_text(), 'original\n')
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertIsNone(manage.load_state(self.tree, 'desktop'))

    def test_existing_unmanaged_file_is_not_overwritten_implicitly(self):
        path = self.put('/etc/example.conf', 'keep')
        with self.assertRaisesRegex(RuntimeError, 'adopt-existing'):
            manage.install(self.tree, 'wifi', {'/etc/example.conf': manage.file_record('replace')})
        self.assertEqual(path.read_text(), 'keep')
        self.assertFalse(manage.pending_path(self.tree, 'wifi').exists())

    def test_local_modifications_block_upgrade_and_uninstall(self):
        manage.install(self.tree, 'wifi', {'/etc/example.conf': manage.file_record('managed')})
        path = self.put('/etc/example.conf', 'locally edited')
        for action in [lambda: manage.install(self.tree, 'wifi', {'/etc/example.conf': manage.file_record('new')}),
                       lambda: manage.uninstall(self.tree, 'wifi')]:
            with self.assertRaisesRegex(RuntimeError, '[Cc]hanged'):
                action()
        self.assertEqual(path.read_text(), 'locally edited')

    def test_failed_write_restores_every_destination_and_manifest(self):
        path = self.put('/etc/original', 'keep')
        real = manage.write_record
        failed = False

        def fail_once(dest, record, live=False):
            nonlocal failed
            if dest.name == 'second' and not failed:
                failed = True
                raise OSError('simulated full filesystem')
            return real(dest, record, live)

        with patch('manage.write_record', side_effect=fail_once):
            with self.assertRaises(OSError):
                manage.install(self.tree, 'wifi', {'/etc/original': manage.file_record('new'),
                                                  '/etc/second': manage.file_record('two')}, adopt=True)
        self.assertEqual(path.read_text(), 'keep')
        self.assertFalse(self.tree.path('/etc/second').exists())
        self.assertIsNone(manage.load_state(self.tree, 'wifi'))

    def test_interrupted_install_recovery_restores_last_committed_version(self):
        manage.install(self.tree, 'wifi', {'/etc/board': manage.file_record('committed')})
        old = manage.load_state(self.tree, 'wifi')
        before = {'/etc/board': manage.capture(self.tree.path('/etc/board'))}
        journal = manage.pending_path(self.tree, 'wifi')
        manage.write_record(journal, manage.file_record(json.dumps({'before': before, 'old_state': old}), 0o600))
        self.put('/etc/board', 'interrupted replacement')
        with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
            manage.install(self.tree, 'wifi', {'/etc/board': manage.file_record('more')})
        manage.recover(self.tree, 'wifi')
        self.assertEqual(self.tree.path('/etc/board').read_text(), 'committed')
        self.assertEqual(manage.load_state(self.tree, 'wifi')['installed'], old['installed'])

    def test_parent_symlinks_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as external:
            (self.root / 'escape').symlink_to(external, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                manage.install(self.tree, 'wifi', {'/escape/file': manage.file_record('bad')})
            self.assertFalse((Path(external) / 'file').exists())
        with self.assertRaises(RuntimeError):
            self.tree.path('/etc/../outside')

    def test_alternate_root_cannot_silently_resolve_to_live_filesystem(self):
        link = self.root / 'live-root'
        link.symlink_to('/', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'live filesystem'):
            manage.Tree(link)

    def test_existing_destination_symlink_is_restored_without_touching_target(self):
        path = self.tree.path('/etc/unit')
        target = self.put('/etc/real-unit', 'do not change')
        path.symlink_to('real-unit')
        manage.install(self.tree, 'desktop', {'/etc/unit': manage.file_record('replacement')}, adopt=True)
        self.assertEqual(target.read_text(), 'do not change')
        manage.uninstall(self.tree, 'desktop')
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), 'real-unit')

    def test_component_removal_keeps_other_component_and_never_invokes_host_tools(self):
        with patch('manage.run', side_effect=AssertionError('Host command invoked')):
            manage.install(self.tree, 'wifi', {'/etc/wifi': manage.file_record('wifi')})
            manage.install(self.tree, 'desktop', {'/etc/desktop': manage.file_record('desktop')})
            manage.uninstall(self.tree, 'desktop')
        self.assertEqual(self.tree.path('/etc/wifi').read_text(), 'wifi')
        with self.assertRaisesRegex(RuntimeError, 'alternate'):
            manage.activate(self.tree, 'wifi')

    def test_desktop_plan_uses_different_account_and_closes_optional_features(self):
        files = manage.desktop_files(self.user, ['files', 'news'])
        record = files['/etc/omarchy-t1/desktop.json']
        config = json.loads(base64.b64decode(record['data']))
        self.assertEqual(config['uid'], 4242)
        self.assertEqual(config['home'], '/home/other-owner')
        self.assertEqual(config['features'], ['files', 'news'])
        self.assertIn('/etc/systemd/user/omarchy-t1-session.service', files)
        self.assertFalse(any(name.startswith('/home/') for name in files))
        with self.assertRaisesRegex(RuntimeError, 'Unknown'):
            manage.desktop_files(self.user, ['run-arbitrary-command'])

    def test_exact_hardware_gate_precedes_mutation(self):
        self.put('/sys/class/dmi/id/product_name', 'MacBookPro14,2\n')
        with self.assertRaisesRegex(RuntimeError, 'MacBookPro13,2'):
            manage.check_hardware(self.tree)
        self.assertFalse(self.tree.path('/etc').exists())

    def test_hardware_probes_follow_normal_dmi_usb_and_pci_sysfs_aliases(self):
        self.put('/sys/devices/virtual/dmi/id/product_name', 'MacBookPro13,2\n')
        dmi = self.root / 'sys/class/dmi'
        dmi.mkdir(parents=True)
        (dmi / 'id').symlink_to('../../devices/virtual/dmi/id', target_is_directory=True)
        for key, value in [('idVendor', '05ac'), ('idProduct', '8600')]:
            self.put('/sys/devices/test-t1/' + key, value + '\n')
        usb = self.root / 'sys/bus/usb/devices'
        usb.mkdir(parents=True)
        (usb / '1-3').symlink_to('../../../devices/test-t1', target_is_directory=True)
        for key, value in [('vendor', '0x14e4'), ('device', '0x43ba'), ('subsystem_vendor', '0x106b'), ('subsystem_device', '0x0157')]:
            self.put('/sys/devices/test-wifi/' + key, value + '\n')
        pci = self.root / 'sys/bus/pci/devices'
        pci.mkdir(parents=True)
        (pci / '0000:02:00.0').symlink_to('../../../devices/test-wifi', target_is_directory=True)
        manage.check_hardware(self.tree)
        manage.check_hardware(self.tree, wifi=True)
        self.assertEqual(self.tree.sysfs_path('/sys/class/dmi/id/product_name').read_text(), 'MacBookPro13,2\n')
        # Supporting kernel aliases for reads must not relax write protection.
        with self.assertRaisesRegex(RuntimeError, 'symlink parent'):
            self.tree.path('/sys/class/dmi/id/product_name')

    def test_staged_hardware_aliases_cannot_read_the_host_or_leave_sysfs(self):
        folder = self.root / 'sys/class/dmi'
        folder.mkdir(parents=True)
        (folder / 'id').symlink_to('/sys/class/dmi/id', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            manage.check_hardware(self.tree)
        for name in ['/etc/passwd', '/sys/../etc/passwd', 'sys/class/dmi/id']:
            with self.assertRaisesRegex(RuntimeError, 'absolute sysfs'):
                self.tree.sysfs_path(name)


class WifiTests(RootFixture):
    def template(self):
        text = 'macaddr=00:00:00:00:00:00\nccode=XX\nregrev=0\nboardflags3=0x00000300\n'
        template = self.root / 'synthetic-template.txt'
        template.write_text(text)
        self.put('/sys/firmware/efi/efivars/ROM-4d1ede05-38c7-4a6a-9cc6-4bcca8b38c14', '\x07\0\0\0\x02\x11\x22\x33\x44\x55')
        return template

    def test_wifi_requires_pinned_template_before_using_local_address(self):
        template = self.template()
        with self.assertRaisesRegex(RuntimeError, 'pinned'):
            manage.wifi_files(self.tree, template, 'AU', 0, '2.4')
        self.assertFalse(self.tree.path(manage.BOARD).exists())

    def test_wifi_preserves_template_and_substitutes_local_country_address_and_band(self):
        template = self.template()
        original = template.read_bytes()
        # Use a synthetic template to test transformation logic, not real RF data.
        with patch('manage.TEMPLATE_SHA256', manage.digest(original)):
            for band, flag in [('2.4', '0x00000003'), ('5', '0x00000300')]:
                files = manage.wifi_files(self.tree, template, 'AU', 0, band)
                board = base64.b64decode(files[manage.BOARD]['data']).decode()
                self.assertIn('macaddr=02:11:22:33:44:55', board)
                self.assertIn('ccode=AU\nregrev=0', board)
                self.assertIn('boardflags3=' + flag, board)
                self.assertEqual(files[manage.BOARD]['mode'], 0o600)
        self.assertEqual(template.read_bytes(), original)

    def test_wifi_refuses_conflicting_existing_driver_policy(self):
        template = self.template()
        self.put('/etc/modprobe.d/existing.conf', 'options brcmfmac feature_disable=0\n')
        with patch('manage.TEMPLATE_SHA256', manage.digest(template.read_bytes())):
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                manage.wifi_files(self.tree, template, 'AU', 0, '2.4')


if __name__ == '__main__':
    unittest.main()
