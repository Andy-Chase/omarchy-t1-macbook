# SPDX-License-Identifier: GPL-2.0-only
"""A premature user-service start mustn't publish a dead renderer socket."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('session_bridge', Path(__file__).parents[1] / 'touchbar/session-bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'XDG_RUNTIME_DIR': '/run/user/42424242'}, clear=True))
        self.stack.enter_context(patch.object(bridge.os, 'getuid', return_value=42424242))
        self.stack.enter_context(patch('sys.argv', ['bridge', '--socket', '/run/user/42424242/omarchy-touchbar-selector-test.sock', '--persistent']))
        self.stack.enter_context(patch.object(bridge.Path, 'exists', return_value=False))
        self.stack.enter_context(patch.object(bridge.Path, 'unlink'))
        self.sockets = self.stack.enter_context(patch.object(bridge.socket, 'socket'))
        self.selector = self.stack.enter_context(patch.object(bridge.selectors, 'DefaultSelector'))
        self.query = self.stack.enter_context(patch.object(bridge, 'query', return_value=(1, 2, 2)))

    def test_missing_compositor_environment_leaves_no_socket(self):
        with self.assertRaisesRegex(RuntimeError, 'Hyprland environment not ready'):
            bridge.main()
        self.sockets.assert_not_called()
        self.query.assert_not_called()

    def test_unavailable_compositor_does_not_publish_renderer_socket(self):
        os.environ['HYPRLAND_INSTANCE_SIGNATURE'] = 'test'
        self.sockets.return_value.connect.side_effect = ConnectionRefusedError('compositor starting')
        with self.assertRaises(ConnectionRefusedError):
            bridge.main()
        self.sockets.return_value.bind.assert_not_called()
        self.assertEqual(self.sockets.call_count, 1)
        self.query.assert_not_called()

    def test_failed_initial_state_does_not_publish_renderer_socket(self):
        os.environ['HYPRLAND_INSTANCE_SIGNATURE'] = 'test'
        self.query.side_effect = RuntimeError('compositor state unavailable')
        with self.assertRaisesRegex(RuntimeError, 'compositor state unavailable'):
            bridge.main()
        self.sockets.return_value.bind.assert_not_called()
        self.assertEqual(self.sockets.call_count, 1)

    def test_ready_session_publishes_socket_and_cleans_up_on_exit(self):
        os.environ['HYPRLAND_INSTANCE_SIGNATURE'] = 'test'
        event, server = MagicMock(), MagicMock()
        self.sockets.side_effect = [event, server]
        for name in ('screen_locked', 'MediaControls', 'MediaButtons', 'Nightlight', 'BatteryDetails',
                     'WifiDetails', 'Markets', 'FileShortcuts', 'News', 'Piano', 'Weather', 'enabled_features'):
            self.stack.enter_context(patch.object(bridge, name))
        self.stack.enter_context(patch.object(bridge.signal, 'signal'))
        self.selector.return_value.select.side_effect = RuntimeError('test loop exit')
        def bind_when_ready(_):
            self.query.assert_called_once()
            event.connect.assert_called_once()
        server.bind.side_effect = bind_when_ready
        with self.assertRaisesRegex(RuntimeError, 'test loop exit'):
            bridge.main()
        server.bind.assert_called_once_with('/run/user/42424242/omarchy-touchbar-selector-test.sock')
        server.listen.assert_called_once_with(1)
        server.close.assert_called_once()
        event.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
