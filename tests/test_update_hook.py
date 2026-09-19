import tempfile
from pathlib import Path
import unittest
from test_pc_support import shell, INSTALLER, REPATCH

class SharedUpdateHook(unittest.TestCase):
    def test_hook_is_installed_only_for_selfheal_and_preserved_before_completion(self):
        self.assertIn('if [[ $UPDATE_MODE == selfheal ]]; then\n  pc_install_update_policy "$MNT"', INSTALLER)
        self.assertLess(REPATCH.index('pc_install_update_policy "$NEWROOT"'), REPATCH.rindex('pc_write_complete "$NEWROOT"'))

    def test_install_repeat_and_reject_changed_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usr/lib/rauc/post-install.sh'
            path.parent.mkdir(parents=True)
            original = '''#!/bin/bash
steamos-chroot --partset $UPDATED_SLOT -- steamos-finalize-install --no-kernel
(( ERR == 0 )) && steamos-bootconf --image $UPDATED_SLOT set-mode reboot
'''
            path.write_text(original)
            code = 'source lib/pc-support.sh; pc_install_update_policy "$1"'
            result = shell(code, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            first = path.read_bytes()
            # Upgrading a previous shared hook adds only mount protection.
            guard_start = first.index(b'# steamos-nvidia update-mount-guard v2')
            guard_end = first.index(b'# steamos-nvidia update-mount-guard end\n') + len(b'# steamos-nvidia update-mount-guard end\n')
            path.write_bytes(first[:guard_start] + first[guard_end:])
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(path.read_bytes(), first)
            # Upgrade the exact old guard; unknown edits still fail closed.
            legacy = first.replace(b'update-mount-guard v2', b'update-mount-guard v1').replace(b'--propagation slave', b'--propagation private')
            path.write_bytes(legacy)
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(path.read_bytes(), first)
            path.write_bytes(legacy.replace(b'noatime /etc', b'relatime /etc'))
            self.assertNotEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(path.read_bytes(), legacy.replace(b'noatime /etc', b'relatime /etc'))
            for bad in [original.replace('set-mode reboot', 'set-mode changed'), original + original, first.decode().replace('noatime /etc', 'relatime /etc')]:
                path.write_text(bad)
                self.assertNotEqual(shell(code, tmp).returncode, 0)
                self.assertEqual(path.read_text(), bad)

    def test_failure_prevents_activation_and_success_orders_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usr/lib/rauc/post-install.sh'
            path.parent.mkdir(parents=True)
            for mode, rc in ((mode, rc) for mode in ('reboot', 'first-boot') for rc in (0, 42)):
                path.write_text('''#!/bin/bash
set -eu
ERR=0; UPDATED_SLOT=A; BOOTED_SLOT=B
fail() { echo "$*"; exit 1; }
steamos-chroot() { echo finalize; }
steamos-bootconf() { echo "bootconf $*"; }
sync() { :; }
steamos-chroot --partset $UPDATED_SLOT -- steamos-finalize-install --no-kernel
(( ERR == 0 )) && steamos-bootconf --image $UPDATED_SLOT set-mode reboot
'''.replace('set-mode reboot', 'set-mode ' + mode))
                self.assertEqual(shell('source lib/pc-support.sh; pc_install_update_policy "$1"', tmp).returncode, 0)
                text = path.read_text()
                # These tests exercise activation ordering; mount isolation is verified in the VM.
                start = text.index('# steamos-nvidia update-mount-guard v2')
                end = text.index('# steamos-nvidia update-mount-guard end\n') + len('# steamos-nvidia update-mount-guard end\n')
                self.assertIn('--mount --propagation slave', text[start:end])
                self.assertIn('remount,bind,noatime /etc || exit 1', text[start:end])
                text = (text[:start] + text[end:]).replace('/usr/lib/steamos-nvidia/repatch.sh other >> /var/log/steamos-nvidia-repatch.log 2>&1', f'(echo repair; exit {rc})')
                path.write_text(text)
                result = shell('bash "$1"', str(path))
                self.assertEqual(result.returncode, 0 if rc == 0 else 1, result.stdout + result.stderr)
                self.assertIn('--set image-invalid 1', result.stdout)
                self.assertEqual(('set-mode ' + mode) in result.stdout, rc == 0)
                self.assertLess(result.stdout.index('--set image-invalid 1'), result.stdout.index('repair'))

    def test_first_boot_preserved_and_mixed_or_preview_formats_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usr/lib/rauc/post-install.sh'
            path.parent.mkdir(parents=True)
            original = ('#!/bin/bash\n'
                        'steamos-chroot --partset $UPDATED_SLOT -- steamos-finalize-install --no-kernel\n'
                        '(( ERR == 0 )) && steamos-bootconf --image $UPDATED_SLOT set-mode first-boot\n')
            code = 'source lib/pc-support.sh; pc_install_update_policy "$1"'
            path.write_text(original)
            self.assertEqual(shell(code, tmp).returncode, 0)
            installed = path.read_bytes()
            self.assertIn(b'set-mode first-boot', installed)
            self.assertNotIn(b'set-mode reboot', installed)
            self.assertLess(installed.index(b'repatch.sh other'), installed.index(b'set-mode first-boot'))
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(path.read_bytes(), installed)
            for bad in (original + original.splitlines()[-1].replace('first-boot', 'reboot') + '\n',
                        original.replace('steamos-', 'holo-'),
                        original.replace('steamos-finalize-install', 'unknown-finalize')):
                path.write_text(bad)
                self.assertNotEqual(shell(code, tmp).returncode, 0)
                self.assertEqual(path.read_text(), bad)

    def test_beta_channel_requires_opt_in_and_known_variant(self):
        import configparser
        import json
        self.assertIn('EXPERIMENTAL_BETA=0', INSTALLER)
        self.assertIn('if [[ $EXPERIMENTAL_BETA == 1 ]]; then', INSTALLER)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'etc/steamos-atomupd'
            directory.mkdir(parents=True)
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps({'variant': 'steamdeck'}))
            preferences = directory / 'preferences.conf'
            preferences.write_text('[Choices]\nBranch=stable\nVariant=steamdeck\n[Extra]\nKeep=yes\n')
            code = 'source lib/pc-support.sh; pc_select_beta_branch "$1"'
            self.assertEqual(shell(code, tmp).returncode, 0)
            config = configparser.ConfigParser()
            config.read(preferences)
            self.assertEqual(config['Choices']['Branch'], 'beta')
            self.assertEqual(config['Extra']['Keep'], 'yes')
            before = preferences.read_bytes()
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(preferences.read_bytes(), before)
            manifest.write_text(json.dumps({'variant': 'steamdeck-oobe'}))
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(preferences.read_bytes(), before)
            manifest.write_text(json.dumps({'variant': 'unknown'}))
            self.assertNotEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(preferences.read_bytes(), before)

    def test_preview_channel_is_explicit_and_rejects_unknown_branch(self):
        import configparser
        import json
        self.assertIn('EXPERIMENTAL_PREVIEW=0', INSTALLER)
        self.assertIn('Choose one experimental channel', INSTALLER)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'etc/steamos-atomupd'
            directory.mkdir(parents=True)
            (directory / 'manifest.json').write_text(json.dumps({'variant': 'steamdeck'}))
            code = 'source lib/pc-support.sh; pc_select_experimental_branch "$1" "$2"'
            self.assertEqual(shell(code, tmp, 'preview').returncode, 0)
            preferences = directory / 'preferences.conf'
            config = configparser.ConfigParser()
            config.read(preferences)
            self.assertEqual(config['Choices']['Branch'], 'preview')
            before = preferences.read_bytes()
            self.assertNotEqual(shell(code, tmp, 'main').returncode, 0)
            self.assertEqual(preferences.read_bytes(), before)

    def test_native_update_alias_is_preserved_inside_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'usr/bin'
            directory.mkdir(parents=True)
            native = directory / 'holo-update'
            native.write_text('#!/bin/sh\necho native\n')
            native.chmod(0o755)
            command = directory / 'steamos-update'
            command.symlink_to('/usr/bin/holo-update')
            code = 'source lib/pc-support.sh; pc_preserve_update_command "$1"'
            self.assertEqual(shell(code, tmp).returncode, 0)
            original = directory / 'steamos-update.orig'
            self.assertFalse(original.is_symlink())
            self.assertEqual(original.read_bytes(), native.read_bytes())
            self.assertFalse(command.exists())
            command.write_text('#!/bin/sh\necho wrapper\n')
            self.assertEqual(shell(code, tmp).returncode, 0)
            self.assertEqual(original.read_bytes(), native.read_bytes())
            original.unlink()
            command.unlink()
            command.symlink_to('/unexpected/update')
            self.assertNotEqual(shell(code, tmp).returncode, 0)
            self.assertTrue(command.is_symlink())

    def test_preview_finalization_failure_blocks_repair_and_activation(self):
        # Representative 3.9 finalizer fallback and deferred shutdown marker.
        original = """#!/bin/bash
set -eu
ERR=0; UPDATED_SLOT=A; BOOTED_SLOT=B
HOLO_BOOTED_SLOT_SYNC_TRIGGER=/dev/null
fail() { echo "$*"; exit 1; }
err() { ERR=1; }
holo-chroot() { echo finalize; return "$FINALIZER_RESULT"; }
holo-bootconf() { echo "bootconf $*"; }
sync() { :; }
declare -ar FINISH=({holo,steamos}-finalize-install steamos-boot-install)
declare -i  FINISHED=0

for finish in ${FINISH[@]}
do
    if holo-chroot --partset $UPDATED_SLOT -- $finish --no-kernel
    then
        FINISHED=1
        break
    fi
done

if [ $FINISHED -ne 1 ]
then
    err "Failed to install bootloaders (tried: ${FINISH[*]})"
fi
(( ERR == 0 )) && holo-bootconf --image $UPDATED_SLOT set-mode first-boot
if [ $ERR == 0 ]; then
    echo "$BOOTED_SLOT" > "$HOLO_BOOTED_SLOT_SYNC_TRIGGER"
    echo shutdown-sync
fi
exit $ERR
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usr/lib/rauc/post-install.sh'
            path.parent.mkdir(parents=True)
            code = 'source lib/pc-support.sh; pc_install_update_policy "$1"'
            for finalizer, repair in ((0, 0), (1, 0), (0, 42)):
                path.write_text(original)
                result = shell(code, tmp)
                self.assertEqual(result.returncode, 0, result.stderr)
                installed = path.read_text()
                self.assertEqual(shell(code, tmp).returncode, 0)
                self.assertEqual(path.read_text(), installed)
                self.assertLess(installed.index('repatch.sh other'), installed.index('set-mode first-boot'))
                start = installed.index('# steamos-nvidia update-mount-guard v2')
                end = installed.index('# steamos-nvidia update-mount-guard end\n') + len('# steamos-nvidia update-mount-guard end\n')
                runnable = (installed[:start] + installed[end:]).replace('/usr/lib/steamos-nvidia/repatch.sh other >> /var/log/steamos-nvidia-repatch.log 2>&1', f'(echo repair; exit {repair})')
                path.write_text(runnable)
                result = shell(f'FINALIZER_RESULT={finalizer} bash "$1"', str(path))
                success = finalizer == 0 and repair == 0
                self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
                self.assertEqual('set-mode first-boot' in result.stdout, success)
                self.assertEqual('shutdown-sync' in result.stdout, success)
                self.assertEqual('repair' in result.stdout, finalizer == 0)
            for bad in (original.replace('FINISHED=1', 'FINISHED=0'),
                        original.replace('echo "$BOOTED_SLOT" > "$HOLO_BOOTED_SLOT_SYNC_TRIGGER"', ':'),
                        installed.replace('FINISHED=1', 'FINISHED=0')):
                path.write_text(bad)
                self.assertNotEqual(shell(code, tmp).returncode, 0)
                self.assertEqual(path.read_text(), bad)
