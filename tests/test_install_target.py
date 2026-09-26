import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('target', ROOT / 'scripts/install-target.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
spec = importlib.util.spec_from_file_location('repair', ROOT / 'scripts/patch-repair.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def disk(name, dev, usb=False):
    return {'name': name, 'maj:min': dev, 'type': 'disk', 'size': 32 * 1024**3,
            'log-sec': 512, 'ro': False, 'model': 'Test SSD', 'serial': '123',
            'tran': 'usb' if usb else 'sata', 'mountpoints': [None], 'children': []}


def layout(d):
    prefix = d['name'] + ('p' if d['name'][-1].isdigit() else '')
    d['children'] = [{'name': prefix + str(i), 'type': 'part', 'maj:min': '9:'+str(i),
                      'size': size * t.MIB, 'partlabel': label, 'fstype': fs,
                      'mountpoints': [None]} for i, (label, fs, size) in enumerate(t.PARTS, 1)]
    return d


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.source = disk('/dev/sda', '8:0', True)
        self.source['children'] = [{'name': '/dev/sda3', 'maj:min': '8:3', 'type': 'part',
                                    'size': 5120*t.MIB, 'mountpoints': ['/']}]
        self.target = disk('/dev/sdb', '8:16', True)
        self.nodes = [self.source, self.target]

    def check(self, mode='all', expected=None):
        return t.validate(self.nodes, '8:3', self.target['name'], mode, expected)

    def test_usb_included_but_installer_excluded(self):
        self.assertEqual(t.candidates(self.nodes, '8:3'), [self.target])
        self.assertIn('USB (external)', t.description(self.target))
        self.assertEqual(self.check(), self.target)
        with self.assertRaises(t.InvalidTarget):
            t.validate(self.nodes, '8:3', '/dev/sda', 'all')

    def test_unknown_or_ambiguous_source_stops(self):
        for root in ['', 'overlay', '0:12']:
            with self.assertRaises(t.InvalidTarget):
                t.candidates(self.nodes, root)
        self.nodes.append(copy.deepcopy(self.source))
        with self.assertRaises(t.InvalidTarget):
            t.candidates(self.nodes, '8:3')

    def test_disk_changed_after_confirmation(self):
        expected = t.identity(self.target)
        self.target['serial'] = 'different disk'
        with self.assertRaises(t.InvalidTarget):
            self.check(expected=expected)

    def test_mounted_swap_readonly_small_and_4kn_rejected(self):
        original = copy.deepcopy(self.target)
        for changes in [{'mountpoints': ['/mnt/data']}, {'mountpoints': ['[SWAP]']},
                        {'ro': True}, {'size': 1024}, {'log-sec': 4096}]:
            with self.subTest(changes=changes):
                self.target.clear(); self.target.update(copy.deepcopy(original)); self.target.update(changes)
                with self.assertRaises(t.InvalidTarget): self.check()
        self.target.clear(); self.target.update(layout(original))
        self.target['children'][0]['mountpoints'] = ['/media/esp']
        with self.assertRaises(t.InvalidTarget): self.check()

    def test_a_disk_holding_a_mounted_system_is_offered(self):
        """The installer used to report no target at all on a machine with a system.

        Measured in the installer environment on 2026-09-25: udisks2 mounts var-A,
        var-B and home of the installed system under /run/media/deck, so every real
        disk counted as busy. The maintainer had to delete the partitions by hand and
        the installer then erased the disk anyway.
        """
        layout(self.target)
        for part, point in [(6, '/run/media/deck/var1'), (7, '/run/media/deck/var'),
                            (8, '/run/media/deck/home')]:
            self.target['children'][part - 1]['mountpoints'] = [point]
        self.assertEqual(t.candidates(self.nodes, '8:3'), [self.target],
                         'a disk with an installed system must still be offered')
        self.assertIn('an existing SteamOS installation', t.description(self.target))

    def test_the_list_says_what_each_disk_holds(self):
        self.assertIn('holds no partitions', t.description(self.target))
        layout(self.target)
        self.assertIn('holds an existing SteamOS installation', t.description(self.target))
        self.target['children'] = [{'name': '/dev/sdb1', 'type': 'part', 'maj:min': '9:1',
                                    'size': 32 * 1024**3, 'partlabel': 'primary',
                                    'fstype': 'ext4', 'mountpoints': [None]}]
        self.assertIn('holds 1 partition of other data', t.description(self.target))

    def test_the_chosen_disk_is_unmounted_before_validation(self):
        layout(self.target)
        self.target['children'][7]['mountpoints'] = ['/run/media/deck/home']
        with patch.object(t.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0)) as run:
            released = t.release_disk(self.target)
        self.assertEqual(released, ['/dev/sdb8 from /run/media/deck/home'])
        self.assertEqual(run.call_args[0][0][:3], ['udisksctl', 'unmount', '-b'])

    def test_a_partition_that_will_not_release_stops_with_its_name(self):
        layout(self.target)
        self.target['children'][7]['mountpoints'] = ['/run/media/deck/home']
        with patch.object(t.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(t.InvalidTarget) as caught:
                t.release_disk(self.target)
        self.assertIn('/dev/sdb8', str(caught.exception))
        self.assertIn('/run/media/deck/home', str(caught.exception))

    def test_swap_is_named_rather_than_silently_unmounted(self):
        layout(self.target)
        self.target['children'][5]['mountpoints'] = ['[SWAP]']
        with self.assertRaises(t.InvalidTarget) as caught:
            t.release_disk(self.target)
        self.assertIn('swapoff', str(caught.exception))

    def test_validation_still_refuses_a_disk_left_mounted(self):
        # release_disk runs first; if anything is still held, nothing may be written.
        layout(self.target)
        self.target['children'][7]['mountpoints'] = ['/run/media/deck/home']
        with self.assertRaises(t.InvalidTarget):
            self.check('system')

    def test_standard_layout_sata_and_nvme_pass(self):
        for name in ['/dev/sdb', '/dev/nvme0n1', '/dev/mmcblk0']:
            self.target['name'] = name
            layout(self.target)
            self.check('system')

    def test_incomplete_wrong_or_small_root_layout_rejected(self):
        layout(self.target)
        good = copy.deepcopy(self.target)
        for change in ['missing', 'swapped', 'filesystem', 'short-root', 'extra']:
            with self.subTest(change=change):
                self.target.clear(); self.target.update(copy.deepcopy(good))
                parts = self.target['children']
                if change == 'missing': parts.pop()
                if change == 'swapped': parts[3]['name'], parts[4]['name'] = parts[4]['name'], parts[3]['name']
                if change == 'filesystem': parts[3]['fstype'] = 'ext4'
                if change == 'short-root': parts[4]['size'] = 1024
                if change == 'extra': parts.append(copy.deepcopy(parts[0]))
                with self.assertRaises(t.InvalidTarget): self.check('system')

    def test_cancel_does_not_invoke_sudo(self):
        with patch.object(t, 'hardware_check', return_value='GPU checked'), patch.object(t, 'snapshot', return_value=(self.nodes, '8:3')), \
             patch.object(t.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '')) as ui, \
             patch.object(t.subprocess, 'call') as execute:
            self.assertEqual(t.select('all'), 0)
            execute.assert_not_called()
            self.assertEqual(ui.call_count, 1)

    def test_confirmation_then_guard_rechecks_disk(self):
        with patch.object(t, 'hardware_check', return_value='GPU checked'), patch.object(t, 'snapshot', return_value=(self.nodes, '8:3')), \
             patch.object(t.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, '/dev/sdb\n'), subprocess.CompletedProcess([], 0)]) as ui, \
             patch.object(t.subprocess, 'call', return_value=0) as execute, patch.object(t, 'finish_install', return_value=0):
            self.assertEqual(t.select('all'), 0)
            args = execute.call_args.args[0]
            self.assertEqual(args[:5], ['sudo', '-n', '/usr/lib/steamos-nvidia/install-authorized', 'all', '/dev/sdb'])
            self.assertEqual(args[5], t.identity(self.target))


class RepairPatchTests(unittest.TestCase):
    def fixture(self):
        constants = '\n'.join('PART_SIZE_'+n+'="'+str(v)+'"' for n,v in {'ESP':256,'EFI':64,'ROOT':5120,'VAR':256,'HOME':100}.items())
        constants += '\n'+'\n'.join('FS_'+n+'='+str(i) for i,n in enumerate(['ESP','EFI_A','EFI_B','ROOT_A','ROOT_B','VAR_A','VAR_B','HOME'],1))
        return constants + '''
cmd steamos-chroot --no-overlay --disk "$DISK" --partset A -- steamcl-install --flags restricted --force-extra-removable
case "$1" in
all)
  writePartitionTable=1
  sanitize_all
  repair_steps
  prompt_reboot done
  ;;
system)
  writeOS=1
  repair_steps
  prompt_reboot done
  ;;
esac
'''

    def test_validation_precedes_destructive_steps(self):
        s = r.patch(self.fixture())
        self.assertLess(s.index('--guard'), s.index('sanitize_all'))
        system = s.split('\nsystem)\n')[1]
        self.assertLess(system.index('--guard'), system.index('repair_steps'))
        self.assertLess(system.index('--verify-boot'), system.index('Installation and boot verification complete.'))
        self.assertNotIn('prompt_reboot', s)

    def test_changed_valve_contract_is_rejected(self):
        for old,new in [('PART_SIZE_ROOT="5120"','PART_SIZE_ROOT="8192"'),
                        ('FS_HOME=8','FS_HOME=9'),('--flags restricted','--flags verbose')]:
            with self.subTest(old=old), self.assertRaises(ValueError):
                r.patch(self.fixture().replace(old,new))
        with self.assertRaises(ValueError): r.patch(r.patch(self.fixture()))

class SnapshotTests(unittest.TestCase):
    def test_btrfs_subvolume_uses_block_device_id(self):
        import json
        import os
        import stat
        from types import SimpleNamespace
        data = [disk('/dev/sda', '8:0', True)]
        with patch.object(t, 'run', side_effect=['/dev/sda3[/rootfs]', json.dumps({'blockdevices': data})]), \
             patch.object(t.os, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 3))) as device:
            nodes, root_id = t.snapshot()
            self.assertEqual(root_id, '8:3')
            device.assert_called_once_with('/dev/sda3')
            self.assertEqual(nodes, data)

    def test_overlay_source_stops(self):
        with patch.object(t, 'run', return_value='overlay'), self.assertRaises(t.InvalidTarget):
            t.snapshot()

class BootFilesTests(unittest.TestCase):
    def test_fallback_requires_exact_loader_and_same_disk_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = root / 'expected.efi'
            expected.write_bytes(b'test-loader')
            with self.assertRaises(t.InvalidTarget): t.check_boot_files(root, expected)
            folder = root / 'efi/boot'
            folder.mkdir(parents=True)
            (folder / 'bootx64.efi').write_bytes(expected.read_bytes())
            with self.assertRaises(t.InvalidTarget): t.check_boot_files(root, expected)
            (folder / 'steamcl-restricted').touch()
            t.check_boot_files(root, expected)
            (folder / 'bootx64.efi').write_bytes(b'wrong-loader')
            with self.assertRaises(t.InvalidTarget): t.check_boot_files(root, expected)

class AuthorizedInstallTests(unittest.TestCase):
    def test_unprivileged_request_is_rejected(self):
        with patch.object(t.os, 'geteuid', return_value=1000), patch.object(t, 'guard') as guard:
            with self.assertRaises(t.InvalidTarget):
                t.execute_install('all', '/dev/sdb', 'identity')
            guard.assert_not_called()

    def test_invalid_target_never_executes_repair(self):
        with patch.dict(t.os.environ), patch.object(t.os, 'geteuid', return_value=0), patch.object(t, 'guard', side_effect=t.InvalidTarget('changed disk')), patch.object(t.subprocess, 'call') as run:
            with self.assertRaises(t.InvalidTarget):
                t.execute_install('all', '/dev/sdb', 'identity')
            run.assert_not_called()

    def test_protected_path_and_clean_environment(self):
        with patch.object(t, 'hardware_check', return_value='GPU checked'), patch.object(t.os, 'geteuid', return_value=0), patch.object(t, 'guard') as guard, patch.object(t.subprocess, 'call', return_value=0) as run, patch.dict(t.os.environ, {'BASH_ENV': '/home/deck/evil', 'PATH': '/home/deck/bin', 'FORCEBIOS': '1'}):
            self.assertEqual(t.execute_install('system', '/dev/sdb', 'token'), 0)
            guard.assert_called_once_with('/dev/sdb', 'system', 'token')
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['/usr/bin/bash', '/usr/lib/steamos-nvidia/installer/repair_device.sh', 'system'])
            self.assertNotIn('BASH_ENV', kwargs['env'])
            self.assertNotIn('FORCEBIOS', kwargs['env'])
            self.assertEqual(kwargs['env']['PATH'], '/usr/sbin:/usr/bin:/sbin:/bin')
            self.assertEqual(kwargs['env']['STEAMOS_TARGET_ID'], 'token')
            self.assertEqual(kwargs['env']['NOPROMPT'], '1')


class CompletionTests(unittest.TestCase):
    def test_cancel_or_closed_dialog_does_not_poweroff(self):
        with patch.object(t.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(t.finish_install(), 0)
            self.assertEqual(run.call_count, 1)

    def test_proceed_requests_shutdown(self):
        with patch.object(t.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(t.finish_install(), 0)
            self.assertEqual(run.call_args.args[0], ['systemctl', 'poweroff'])

    def test_broken_dialog_never_requests_shutdown(self):
        with patch.object(t.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2)) as run:
            with self.assertRaises(t.InvalidTarget): t.finish_install()
            self.assertEqual(run.call_count, 1)


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gpu = self.root / '0000:01:00.0'
        self.gpu.mkdir()
        (self.gpu / 'vendor').write_text('0x10de')
        (self.gpu / 'class').write_text('0x030000')
        # resolve() is mocked so these tests do not require symlink privileges.
        self.resolve = patch.object(Path, 'resolve', return_value=Path('/drivers/nvidia'))
        self.resolve.start()
        self.addCleanup(self.resolve.stop)

    def check(self, output='00000000:01:00.0, NVIDIA GeForce RTX 5060, 610.43.03'):
        with patch.object(t, 'run', side_effect=[output, 'nvidia-utils 610.43.03-5']):
            return t.hardware_check(self.root)

    def test_running_card_and_package_match(self):
        self.assertIn('RTX 5060', self.check())

    def test_missing_card(self):
        (self.gpu / 'vendor').write_text('0x1002')
        with self.assertRaises(t.InvalidTarget): self.check()

    def test_unbound_card(self):
        with patch.object(Path, 'resolve', return_value=Path('/drivers/nouveau')):
            with self.assertRaises(t.InvalidTarget): self.check()

    def test_wrong_driver_unsupported_gpu_and_missing_device(self):
        for output in ['00000000:01:00.0, NVIDIA GeForce RTX 5060, 600.0',
                       '00000000:01:00.0, NVIDIA GeForce GTX 1080, 610.43.03',
                       '00000000:02:00.0, NVIDIA GeForce RTX 5060, 610.43.03', '', 'bad response']:
            with self.subTest(output=output), self.assertRaises(t.InvalidTarget):
                self.check(output)

    def test_failure_prevents_disk_selection(self):
        with patch.object(t, 'hardware_check', side_effect=t.InvalidTarget('unsupported')), patch.object(t, 'snapshot') as snapshot:
            with self.assertRaises(t.InvalidTarget): t.select('all')
            snapshot.assert_not_called()

    def test_root_helper_rechecks_before_repair(self):
        with patch.dict(t.os.environ), patch.object(t.os, 'geteuid', return_value=0), patch.object(t, 'guard'), patch.object(t, 'hardware_check', side_effect=t.InvalidTarget('GPU changed')), patch.object(t.subprocess, 'call') as execute:
            with self.assertRaises(t.InvalidTarget): t.execute_install('all', '/dev/sdb', 'token')
            execute.assert_not_called()
