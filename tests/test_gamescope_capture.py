import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class CaptureIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'usr/lib/steamos-nvidia/gamescope'
        (self.base / 'bin').mkdir(parents=True)
        (self.root / 'etc').mkdir()
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.8.16"\n')
        session = self.root / 'usr/lib/steamos/gamescope-session'
        session.parent.mkdir(parents=True)
        session.write_text('exec gamescope \\\n --steam\n')
        (self.base / 'bin/gamescope').write_bytes(b'test-binary')
        (self.base / 'Gamescope-LICENSE').write_text('test-license')
        (self.base / 'gamescope-build.json').write_text(json.dumps({
            'commit': '2b79e07b3da1723c7e5c5f44f18de36c6cb78b9e',
            'files': {'usr/bin/gamescope': hashlib.sha256(b'test-binary').hexdigest()}}))
        self.override = self.root / 'usr/lib/systemd/user/gamescope-session.service.d/30-nvidia-capture.conf'

    def run_install(self, version='gamescope 3.16.23.4-1', loader='0'):
        code = '''source lib/pc-support.sh
chroot() {
  if [[ $2 == pacman ]]; then printf '%s\\n' "$TEST_VERSION"; else return "$TEST_LOADER"; fi
}
pc_install_gamescope "$1"
'''
        return subprocess.run(['bash', '-c', code, 'test', str(self.root)], cwd=ROOT,
            env={**os.environ, 'TEST_VERSION': version, 'TEST_LOADER': loader}, capture_output=True, text=True)

    def test_update_from_recovery_activates_and_future_version_falls_back(self):
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.8.14"\n')
        self.assertEqual(self.run_install('gamescope 3.16.23.2-1').returncode, 0)
        self.assertFalse(self.override.exists())
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.8.16"\n')
        self.assertEqual(self.run_install().returncode, 0)
        self.assertIn('/usr/lib/steamos-nvidia/gamescope/bin', self.override.read_text())
        self.assertEqual(self.run_install().returncode, 0)
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.9.1"\n')
        self.assertEqual(self.run_install('gamescope 3.16.26-2').returncode, 0)
        self.assertFalse(self.override.exists())
        self.assertTrue((self.base / 'status.txt').read_text().startswith('stock'))

    def set_artifact_commit(self, commit):
        data = json.loads((self.base / 'gamescope-build.json').read_text())
        data['commit'] = commit
        (self.base / 'gamescope-build.json').write_text(json.dumps(data))

    def test_a_later_point_release_keeps_the_correction_when_the_package_matches(self):
        """The selection follows the Gamescope package, not a SteamOS point release.

        Valve moved stable from 3.8.16 to 3.8.28 on 22 September 2026. The rule used
        to name 3.8.16, so the correction switched itself off on an ordinary system
        update and Remote Play from the machine went back to a black picture, which
        the maintainer hit on 2026-09-25.
        """
        self.set_artifact_commit('154f435a2c0026510545b7b7524d104bed253cb3')
        for release in ['3.8.28', '3.8.16', '3.8.99']:
            with self.subTest(release=release):
                (self.root / 'etc/os-release').write_text(f'VERSION_ID="{release}"\n')
                self.assertEqual(self.run_install('gamescope 3.16.23.6-1').returncode, 0)
                self.assertIn('/usr/lib/steamos-nvidia/gamescope/bin', self.override.read_text())

    def test_an_artifact_for_another_package_still_stands_aside(self):
        # The 3.16.23.4 artifact must not be carried onto a system shipping 3.16.23.6.
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.8.28"\n')
        self.assertEqual(self.run_install('gamescope 3.16.23.6-1').returncode, 0)
        self.assertFalse(self.override.exists())
        self.assertTrue((self.base / 'status.txt').read_text().startswith('stock'))

    def test_an_unknown_artifact_is_refused_rather_than_trusted(self):
        self.set_artifact_commit('0' * 40)
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported Gamescope capture artifact', result.stderr)
        self.assertFalse(self.override.exists())

    def test_corruption_and_incompatible_libraries_cannot_activate(self):
        self.assertNotEqual(self.run_install(loader='1').returncode, 0)
        self.assertFalse(self.override.exists())
        (self.base / 'bin/gamescope').write_bytes(b'corrupt')
        self.assertNotEqual(self.run_install().returncode, 0)
        self.assertFalse(self.override.exists())

    def test_new_package_or_session_format_retains_stock(self):
        self.assertEqual(self.run_install('gamescope 3.16.23.4-2').returncode, 0)
        self.assertFalse(self.override.exists())
        (self.root / 'usr/lib/steamos/gamescope-session').write_text('exec /usr/bin/gamescope\n')
        self.assertEqual(self.run_install().returncode, 0)
        self.assertFalse(self.override.exists())

    def test_no_artifact_is_noop(self):
        shutil.rmtree(self.base)
        self.assertEqual(self.run_install().returncode, 0)
        self.assertFalse(self.override.exists())
    def test_manifest_survives_capture_activation_and_fallback(self):
        import re
        source = (ROOT / 'lib/pc-support.sh').read_text()
        block = source.split('pc_write_addon_manifest() {', 1)[1].split('if [[', 1)[0]
        for rel in re.findall(r'(?m)^    (\S+)', block):
            rel = rel.rstrip(')')
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture')
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.8.14"\n')
        self.assertEqual(self.run_install('gamescope 3.16.23.2-1').returncode, 0)
        def manifest(command):
            return subprocess.run(['bash', '-c',
                'source lib/pc-support.sh; ' + command, 'test', str(self.root)],
                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(manifest('pc_write_addon_manifest "$1"').returncode, 0)
        check = 'cd "$1" && sha256sum --check usr/lib/steamos-nvidia/addons.sha256'
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.8.16"\n')
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(manifest(check).returncode, 0)
        self.assertEqual(manifest('pc_write_addon_manifest "$1"').returncode, 0)
        (self.root / 'etc/os-release').write_text('VERSION_ID="3.9.1"\n')
        self.assertEqual(self.run_install('gamescope 3.16.26-2').returncode, 0)
        self.assertEqual(manifest(check).returncode, 0)
        (self.base / 'bin/gamescope').write_bytes(b'corrupt')
        self.assertNotEqual(manifest(check).returncode, 0)
