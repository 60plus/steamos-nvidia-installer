import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer_update', Path(__file__).parents[1] / 'scripts/installer-update.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def manifest():
    return dict(format=1, version='0.1.0-dev.1', steamos=['3.8.16'], notes='Update tools',
                bundle_sha256='0'*64, files={name: m.digest(b'test') for name in m.FILES})


class SteamosCompatibilityIsRecordedNotEnforced(unittest.TestCase):
    """A declared version list must not block the releases people actually run.

    Every release so far declared 3.8.16. Valve moved stable to 3.8.28 on
    2026-09-22, so the published updater would have refused on current stable,
    and the next point release would do it again. The list now records what was
    tested: below the oldest entry there is nothing to stand on and the update
    still refuses, at or above it the update proceeds and says what it saw. The
    integration itself ends in pc_check_addons under set -e, so a release that
    genuinely does not fit fails there instead of degrading quietly.
    """

    def warn(self, version, tested=('3.8.16', '3.8.28')):
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            m.check_steamos(version, list(tested), 'Release')
        return captured.getvalue()

    def test_a_tested_release_passes_without_saying_anything(self):
        self.assertEqual('', self.warn('3.8.28'))
        self.assertEqual('', self.warn('3.8.16'))

    def test_a_newer_point_release_passes_and_names_both_sides(self):
        message = self.warn('3.8.29')
        self.assertIn('3.8.29', message)
        self.assertIn('3.8.16', message)
        self.assertIn('3.8.28', message)

    def test_a_newer_series_passes_too(self):
        self.assertIn('3.9.1', self.warn('3.9.1'))

    def test_older_than_anything_tested_is_refused_and_names_the_oldest(self):
        with self.assertRaises(ValueError) as refusal:
            self.warn('3.8.15')
        self.assertIn('3.8.15', str(refusal.exception))
        self.assertIn('3.8.16', str(refusal.exception))

    def test_an_unreadable_version_warns_rather_than_crashing(self):
        self.assertIn('snapshot', self.warn('snapshot'))


class InstallerUpdate(unittest.TestCase):
    def test_overlay_artifact_is_all_or_nothing_and_old_bundles_remain_valid(self):
        value = manifest()
        for name in m.OPTIONAL_FILES:
            del value['files'][name]
        m.validate_manifest(value)
        for name in m.OPTIONAL_FILES:
            partial = dict(value, files={**value['files'], name: '0'*64})
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.validate_manifest(partial)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bundle.tar'
            with tarfile.open(path, 'w') as archive:
                for name in value['files']:
                    member = tarfile.TarInfo(name); member.size = 4
                    archive.addfile(member, io.BytesIO(b'test'))
            value['bundle_sha256'] = m.digest(path.read_bytes())
            self.assertEqual(set(m.read_bundle(path, value)), set(value['files']))
            with tarfile.open(path, 'a') as archive:
                member = tarfile.TarInfo('mangoapp'); member.size = 4
                archive.addfile(member, io.BytesIO(b'test'))
            value['bundle_sha256'] = m.digest(path.read_bytes())
            with self.assertRaises(ValueError):
                m.read_bundle(path, value)

    def test_manifest_rejects_unknown_fields_and_invalid_versions(self):
        for changed in [dict(format=2), dict(version='../bad'), dict(steamos=['*']),
                        dict(files={'../../etc/passwd':'0'*64}), dict(bundle_sha256='bad')]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                m.validate_manifest(dict(manifest(), **changed))
        m.validate_manifest(manifest())

    def test_archive_integrity_and_member_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.tar'
            for bad in ('symlink', 'traversal', 'duplicate', 'missing', 'checksum', None):
                with tarfile.open(path,'w') as archive:
                    for name in m.FILES:
                        if bad=='missing' and name=='pc-support.sh':continue
                        member=tarfile.TarInfo(name); member.size=4
                        archive.addfile(member,io.BytesIO(b'test'))
                    if bad in ('symlink','traversal','duplicate'):
                        member=tarfile.TarInfo('../outside' if bad=='traversal' else 'pc-support.sh')
                        if bad=='symlink':member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd'
                        archive.addfile(member)
                value=manifest();value['bundle_sha256']=m.digest(path.read_bytes())
                if bad=='checksum':value['files']['pc-support.sh']='1'*64
                if bad:
                    with self.subTest(bad=bad),self.assertRaises(ValueError):m.read_bundle(path,value)
                else:self.assertEqual(set(m.read_bundle(path,value)),set(m.FILES))
                self.assertFalse((Path(tmp)/'outside').exists())

    def test_signature_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);key=folder/'private.pem';pub=folder/'public.pem'
            subprocess.run(['openssl','genpkey','-algorithm','ED25519','-out',str(key)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(pub)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            data=folder/'manifest';sig=folder/'signature';data.write_bytes(b'original')
            subprocess.run(['openssl','pkeyutl','-sign','-inkey',str(key),'-rawin','-in',str(data),'-out',str(sig)],check=True)
            m.verify_signature(data,sig,pub.read_text(),folder)
            data.write_bytes(b'changed')
            with self.assertRaises(subprocess.CalledProcessError):m.verify_signature(data,sig,pub.read_text(),folder)

    def test_plain_http_is_rejected_before_download(self):
        with patch.object(m.subprocess,'run') as run:
            with self.assertRaises(ValueError):m.download('http://example.com/file',Path('/tmp/no'),100)
            run.assert_not_called()

    def test_release_pins_signed_version_and_selected_digest(self):
        source=(Path(__file__).parents[1]/'scripts/installer-update.py').read_text()
        self.assertIn("release['tag_name'] != 'v' + value['version']",source)
        self.assertIn("value['manifest_sha256'] != args.manifest_sha256",source)

    def test_transaction_does_not_reuse_source_completion(self):
        stage=(Path(__file__).parents[1]/'scripts/driver-stage.sh').read_text()
        self.assertLess(stage.index('rm -f "$work/target/usr/lib/steamos-nvidia/complete"'),stage.index('dd if='))
        installer=(Path(__file__).parents[1]/'steamos-nvidia-installer.sh').read_text()
        repatch=installer.split("<<'REPATCH'\n",1)[1].split('\nREPATCH\n',1)[0]
        self.assertLess(repatch.index('apply-target "$NEWROOT"'),repatch.index('pc_write_complete "$NEWROOT"'))

    def test_source_rejects_untrusted_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config';path.write_text('{}');path.chmod(0o666)
            with patch.object(m,'CONFIG',path),self.assertRaises(ValueError):m.source()

    def test_asset_downloads_use_configured_origin(self):
        cfg=dict(name='test',release_api='https://api.example.com/repos/o/r/releases/',
                 download_origin='https://downloads.example.com',allow_prerelease=True,public_key='unused')
        release=dict(tag_name='v0.1.0-dev.1',prerelease=True,draft=False,assets=[
            dict(name=n,browser_download_url='https://old.example.com/releases/'+n)
            for n in ['installer-manifest.json','installer-manifest.sig']])
        calls=[]
        def download(url,target,limit):
            calls.append(url)
            if target.name=='release.json':target.write_text(json.dumps([release]))
            elif target.name=='installer-manifest.json':target.write_text(json.dumps(manifest()))
            else:target.write_bytes(b'signature')
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'source',return_value=cfg),patch.object(m,'download',side_effect=download),patch.object(m,'verify_signature'):
            value=m.fetch('latest',Path(tmp))
            self.assertEqual(value['version'],'0.1.0-dev.1')
            self.assertTrue(all(u.startswith(cfg['download_origin']) for u in calls[1:]))

    def test_stable_source_rejects_prerelease_before_asset_download(self):
        cfg=dict(name='stable',release_api='https://api.example.com/repos/o/r/releases/',allow_prerelease=False)
        def download(url,target,limit):target.write_text(json.dumps({'prerelease':True}))
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'source',return_value=cfg),patch.object(m,'download',side_effect=download) as fetch:
            with self.assertRaises(ValueError):m.fetch('latest',Path(tmp))
            self.assertEqual(fetch.call_count,1)

    def test_btrfs_target_uses_source_device_and_rejects_active_root(self):
        import stat
        from types import SimpleNamespace
        def info(path):
            return SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=1 if 'self' in str(path) or str(path)=='/dev/vda4' else 2)
        with patch.object(m.os,'stat',side_effect=info):
            m.verify_target_device('/target',lambda *a:'/dev/vda5[/]')
            with self.assertRaises(ValueError):m.verify_target_device('/target',lambda *a:'/dev/vda4')
            with self.assertRaises(ValueError):m.verify_target_device('/target',lambda *a:'overlay')
