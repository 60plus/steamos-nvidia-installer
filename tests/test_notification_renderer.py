import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('notifications', Path(__file__).resolve().parents[1] / 'scripts/notification-renderer.py')
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


class Notifications(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'steamui').mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.target = self.root / 'steamui' / n.ASSET
        self.original = b'prefix;' + n.BEFORE + b';suffix'
        self.target.write_bytes(self.original)
        self.guard = patch.object(n, 'ORIGINAL_SHA256', hashlib.sha256(self.original).hexdigest())
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def run_action(self, action):
        return n.update(self.root, self.state, action)

    def test_roundtrip_and_idempotence(self):
        self.assertEqual(self.run_action('apply'), 'selected embedded renderer')
        patched = self.target.read_bytes()
        self.assertIn(n.AFTER, patched)
        self.assertEqual(len(patched), len(self.original))
        self.assertEqual(self.run_action('apply'), 'already patched')
        self.assertEqual(self.target.read_bytes(), patched)
        self.assertEqual(self.run_action('restore'), 'restored original renderer')
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_unknown_client_with_same_snippet_is_not_modified(self):
        self.target.write_bytes(self.original + b'new Steam version')
        for action in ['apply', 'restore']:
            self.assertIn('unsupported', self.run_action(action))
        self.assertEqual(list(self.state.iterdir()), [])

    def test_corrupt_backup_prevents_restore(self):
        self.run_action('apply')
        next(self.state.iterdir()).write_bytes(b'bad backup')
        before = self.target.read_bytes()
        with self.assertRaises(ValueError):
            self.run_action('restore')
        self.assertEqual(before, self.target.read_bytes())

    def test_updated_file_never_restored_from_old_backup(self):
        self.run_action('apply')
        self.target.write_bytes(b'new client')
        self.assertIn('unsupported', self.run_action('restore'))
        self.assertEqual(self.target.read_bytes(), b'new client')

    def test_symlink_rejected(self):
        self.target.unlink()
        other = self.root / 'other'
        other.write_bytes(self.original)
        self.target.symlink_to(other)
        with self.assertRaises(OSError):
            self.run_action('apply')
        self.assertEqual(other.read_bytes(), self.original)

    def test_startup_applies_and_reloads_once(self):
        with patch.object(n, 'browser_processes', return_value=[42]), \
                patch.object(n.os, 'pidfd_open', return_value=99), \
                patch.object(n.os, 'close') as close, \
                patch.object(n.time, 'sleep'), \
                patch.object(n.signal, 'pidfd_send_signal') as kill:
            self.assertIn('reloaded', n.startup(self.root, self.state))
            kill.assert_called_once_with(99, n.signal.SIGTERM)
            close.assert_called_once_with(99)

    def test_startup_unknown_asset_never_restarts_browser(self):
        self.target.write_bytes(b'unknown client')
        with patch.object(n, 'browser_processes', return_value=[42]), \
                patch.object(n.os, 'pidfd_open', return_value=99), \
                patch.object(n.os, 'close'), patch.object(n.time, 'sleep'), \
                patch.object(n.signal, 'pidfd_send_signal') as kill:
            self.assertIn('unsupported', n.startup(self.root, self.state))
            kill.assert_not_called()

    def test_disabled_startup_does_not_touch_browser(self):
        (self.state / 'disabled').touch()
        with patch.object(n, 'browser_processes') as processes:
            self.assertIn('disabled', n.startup(self.root, self.state))
            processes.assert_not_called()

    def test_legacy_patch_migrates_without_losing_original(self):
        self.target.write_bytes(self.original.replace(n.BEFORE, n.LEGACY_AFTER))
        self.assertEqual(self.run_action('apply'), 'selected embedded renderer')
        self.assertEqual(len(self.target.read_bytes()), len(self.original))
        self.run_action('restore')
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_status_does_not_write(self):
        self.assertEqual(self.run_action('status'), 'original renderer on disk')
        self.assertEqual(list(self.state.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
