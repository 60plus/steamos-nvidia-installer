import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'tools/build-installer-release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ReleaseNotification(unittest.TestCase):
    def test_signed_support_script_installs_exact_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            support = root / 'support.sh'
            support.write_bytes(m.bundled_support())
            subprocess.run(['bash', '-n', str(support)], check=True)
            subprocess.run(['bash', '-c', 'source "$1"; pc_install_bundled_notification_renderer "$2"',
                            'test', str(support), str(root)], check=True)
            target = root / 'usr/lib/steamos-nvidia/notification-renderer.py'
            self.assertEqual(target.read_bytes(), (ROOT / 'scripts/notification-renderer.py').read_bytes())
            subprocess.run(['bash', '-c', 'source "$1"; pc_install_bundled_notification_renderer "$2"',
                            'test', str(support), str(root)], check=True)
            self.assertEqual(target.read_bytes(), (ROOT / 'scripts/notification-renderer.py').read_bytes())
