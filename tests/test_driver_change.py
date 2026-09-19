import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('driver_change', Path(__file__).parents[1] / 'scripts/driver-change.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CURRENT = dict(DRIVER_SPEC='610.57.04-1', DRIVER_VERSION='610.57.04-1', PKG_URLS='',
               ADD_XPADNEO='0', XPADNEO_VERSION='v0.10.4', XPADNEO_SHA256='', TRIM_CUDA='1')
BUILD = dict(product='steamos', release='holo', variant='steamdeck', arch='amd64', version='3.8.16', buildid='20260716.1')

class DriverChange(unittest.TestCase):
    def test_repatch_forces_install_and_checks_requested_module_version(self):
        from test_pc_support import REPATCH
        self.assertIn('dkms install --force -m nvidia -v ${DRIVER_VERSION%-*}', REPATCH)
        self.assertIn('pc_check_module "$MERGED" "$KVER" "$module" "${DRIVER_VERSION%-*}"', REPATCH)
        self.assertLess(REPATCH.index('dkms install --force'), REPATCH.index('pc_copy_update_payload'))

    def test_reads_saved_config_with_sha256_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'driver.conf'
            path.write_text('XPADNEO_SHA256=""\nTRIM_CUDA=1\n')
            self.assertEqual(m.read_config(path), {'XPADNEO_SHA256': '', 'TRIM_CUDA': '1'})

    def test_exact_version_and_companion_version(self):
        def fetch(*args, **kwargs):
            package = args[-1].strip('/').split('/')[-1]
            return ''.join(f'{package}-{v}-x86_64.pkg.tar.zst ' for v in ['610.57.04-1', '610.57.04-2', '610.99.0-1'])
        with patch.object(m, 'run', side_effect=fetch):
            result = m.resolve('610.57.04-1', CURRENT)
        self.assertIn('nvidia-utils-610.57.04-1-x86_64', result)
        self.assertIn('nvidia-open-dkms-610.57.04-2-x86_64', result)
        self.assertNotIn('610.99', result)

    def test_bad_versions_never_fetch(self):
        with patch.object(m, 'run') as run:
            for version in ['latest', '610', '../610.1-1', '610.1-1;id', '610.1-1\n']:
                with self.assertRaises(ValueError):
                    m.resolve(version, CURRENT)
            run.assert_not_called()

    def test_missing_companion_and_nonarchive_support_rejected(self):
        with patch.object(m, 'run', return_value=''):
            with self.assertRaises(ValueError):
                m.resolve('610.57.04-1', CURRENT)
        with patch.object(m, 'run', return_value=' '.join(f'{p}-610.57.04-1-x86_64.pkg.tar.zst' for p in m.CORE)):
            with self.assertRaises(ValueError):
                m.resolve('610.57.04-1', dict(CURRENT, PKG_URLS='http://untrusted/file'))

    def test_target_build_must_match_every_manifest_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp)
            with patch.object(m, 'REQUEST', request), patch.object(m, 'request_config'), patch.object(m, 'private_read', return_value=json.dumps({'manifest': BUILD})), patch.object(m, 'write_json') as write:
                for field in BUILD:
                    with patch.object(m, 'manifest', return_value=dict(BUILD, **{field: 'different'})):
                        with self.assertRaises(ValueError):
                            m.check_target('/target')
                write.assert_not_called()
                with patch.object(m, 'manifest', return_value=BUILD):
                    m.check_target('/target')
                write.assert_called_once()

    def test_updater_rejection_does_not_disable_unrelated_slot(self):
        self.install_failure(started=False)

    def test_repair_failure_keeps_target_disabled(self):
        self.install_failure(started=True)

    def install_failure(self, started):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request'
            def run(*args, **kwargs):
                if args == ('steamos-bootconf', 'selected-image'):
                    return 'A'
                if args == ('atomupd-manager', 'get-update-status'):
                    return 'idle'
                if args == ('unshare', '--mount', '--propagation', 'slave', '/usr/lib/steamos-nvidia/driver-stage.sh'):
                    if started:
                        (request / 'started.json').touch()
                    raise ValueError('test failure')
                return ''
            original_read = Path.read_text
            def read(path, *args, **kwargs):
                if str(path) == '/usr/lib/rauc/post-install.sh':
                    return '# steamos-nvidia shared-update-hook v1'
                return original_read(path, *args, **kwargs)
            with patch.object(m, 'REQUEST', request), patch.object(m, 'STATE', Path(tmp)), patch.object(m, 'boot_slot', return_value='A'), patch.object(m, 'run', side_effect=run), patch.object(m, 'private_dir', side_effect=lambda p: p.mkdir(exist_ok=True)), patch.object(m, 'manifest', return_value=BUILD), patch.object(m, 'resolve', return_value='pin'), patch.object(m, 'read_config', return_value=CURRENT), patch.object(m, 'identity', return_value='old'), patch.object(m.shutil, 'disk_usage') as usage, patch.object(m, 'protect') as protect, patch.object(Path, 'read_text', read):
                usage.return_value.free = 20 * 1024**3
                with self.assertRaises(ValueError):
                    m.install('610.57.04-1')
                self.assertEqual(protect.called, started)
                self.assertFalse(request.exists())
                self.assertEqual(json.loads((Path(tmp) / 'last.json').read_text())['status'], 'failed')

    def test_cancel_rejects_replaced_candidate(self):
        state = dict(status='ready', source='A', target='B', identity='original', manifest=BUILD, config_sha256='expected')
        with patch.object(m, 'private_read', return_value=json.dumps(state)), patch.object(m, 'boot_slot', return_value='A'), patch.object(m, 'identity', return_value='original'), patch.object(m, 'run', return_value=json.dumps({'sha': 'other', 'manifest': BUILD})), patch.object(m, 'protect') as protect:
            with self.assertRaises(ValueError):
                m.rollback()
            protect.assert_not_called()

    def test_cancel_marks_rollback_selected(self):
        state = dict(status='ready', source='A', target='B', identity='original', manifest=BUILD, config_sha256='expected')
        with patch.object(m, 'private_read', return_value=json.dumps(state)), patch.object(m, 'boot_slot', return_value='A'), patch.object(m, 'identity', return_value='original'), patch.object(m, 'run', return_value=json.dumps({'sha': 'expected', 'manifest': BUILD})), patch.object(m, 'protect') as protect, patch.object(m, 'write_json') as write:
            m.rollback()
            protect.assert_called_once_with('B')
            self.assertEqual(write.call_args.args[1]['status'], 'rollback-selected')

    def test_stale_rollback_refused_without_boot_changes(self):
        state = dict(status='ready', source='A', target='B', identity='original', manifest=BUILD)
        with patch.object(m, 'private_read', return_value=json.dumps(state)), patch.object(m, 'boot_slot', return_value='A'), patch.object(m, 'identity', return_value='changed'), patch.object(m, 'run') as run:
            with self.assertRaises(ValueError):
                m.rollback()
            run.assert_not_called()

class OrphanBuildFiles(unittest.TestCase):
    def test_only_unowned_exact_files_are_allowed(self):
        from test_pc_support import shell
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'usr/bin').mkdir(parents=True)
            for name in ('owned', 'orphan'):
                (root / 'usr/bin' / name).touch()
            archive = root / 'tools.pkg.tar.zst'
            archive.touch()
            result = shell("""source lib/pc-support.sh
pacman() { printf '%s\\n' / /usr/bin/owned /usr/bin/orphan /usr/bin/absent; }
chroot() { [[ ${*: -1} == /usr/bin/owned ]]; }
pc_build_overwrites "$1" "$2"
""", str(root), str(archive))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'usr/bin/orphan')

    def test_archive_read_failure_is_not_ignored(self):
        from test_pc_support import shell
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.pkg.tar.zst'
            archive.touch()
            result = shell('source lib/pc-support.sh; pacman() { return 1; }; pc_build_overwrites "$1" "$2"', tmp, str(archive))
            self.assertNotEqual(result.returncode, 0)

class ObsoleteDriverFiles(unittest.TestCase):
    def test_removes_only_old_manifest_files(self):
        from test_pc_support import shell
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'usr/lib/holo/pacmandb/local/nvidia-utils-1.0-1'
            db.mkdir(parents=True)
            lib = root / 'usr/lib'
            for name in ('old.so', 'keep.so', 'unrelated.so'):
                (lib / name).touch()
            (db / 'files').write_text('%FILES%\nusr/lib/\nusr/lib/old.so\nusr/lib/keep.so\n')
            wanted = root / 'wanted'
            wanted.write_text('usr/lib/keep.so\n')
            result = shell('source lib/pc-support.sh; pc_remove_obsolete_driver_files "$1" "$2"', tmp, str(wanted))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((lib / 'old.so').exists())
            self.assertTrue((lib / 'keep.so').exists())
            self.assertTrue((lib / 'unrelated.so').exists())

    def test_rejects_escape_before_removing_anything(self):
        from test_pc_support import shell
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'usr/lib/holo/pacmandb/local/nvidia-utils-1.0-1'
            db.mkdir(parents=True)
            old = root / 'usr/lib/old.so'
            old.touch()
            (db / 'files').write_text('%FILES%\nusr/lib/old.so\n../../outside\n')
            wanted = root / 'wanted'
            wanted.touch()
            result = shell('source lib/pc-support.sh; pc_remove_obsolete_driver_files "$1" "$2"', tmp, str(wanted))
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(old.exists())

if __name__ == '__main__':
    unittest.main()
