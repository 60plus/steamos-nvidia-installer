import os
import hashlib
import json
import configparser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_EXE") or shutil.which("bash")
INSTALLER = (ROOT / "steamos-nvidia-installer.sh").read_text(encoding="utf-8")
REPATCH = INSTALLER.split("<<'REPATCH'\n", 1)[1].split("\nREPATCH\n", 1)[0]
UPDATE = INSTALLER.split("<<'WRAP'\n", 1)[1].split("\nWRAP\n", 1)[0]


def shell(code, *args):
    return subprocess.run(
        [BASH, "-c", code, "test", *args], cwd=ROOT,
        text=True, capture_output=True, timeout=30,
        env={**os.environ, "LC_ALL": "C"},
    )


class Scripts(unittest.TestCase):
    def test_overlay_install_rejects_bad_artifacts_before_service_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'usr/lib/steamos-nvidia'
            base.mkdir(parents=True)
            command = 'source lib/pc-support.sh; chroot() { return "${LOADER_RESULT:-0}"; }; pc_install_mangoapp "$1"'
            target = root / 'usr/lib/systemd/user/gamescope-mangoapp.service.d/30-nvidia-metrics.conf'
            self.assertEqual(shell(command, tmp).returncode, 0)
            self.assertFalse(target.exists())
            (base / 'mangoapp').write_bytes(b'fake test artifact')
            (base / 'mangoapp-build.json').write_text(json.dumps({'sha256': '0'*64}))
            (base / 'MangoHud-LICENSE').write_text('test license')
            self.assertNotEqual(shell(command, tmp).returncode, 0)
            self.assertFalse(target.exists())
            (base / 'mangoapp-build.json').write_text(json.dumps({'sha256': hashlib.sha256((base / 'mangoapp').read_bytes()).hexdigest()}))
            (base / 'MangoHud-LICENSE').unlink()
            self.assertNotEqual(shell(command, tmp).returncode, 0)
            self.assertFalse(target.exists())
            (base / 'MangoHud-LICENSE').write_text('test license')
            self.assertNotEqual(shell('LOADER_RESULT=1; '+command, tmp).returncode, 0)
            self.assertFalse(target.exists())
            result = shell(command, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ExecStart=/usr/lib/steamos-nvidia/mangoapp', target.read_text())

    def test_syntax_including_generated_scripts(self):
        for file in ["steamos-nvidia-installer.sh", "build-xpadneo.sh",
                     "lib/pc-support.sh", "scripts/steamos-nvidia-diagnostics"]:
            result = subprocess.run([BASH, "-n", file], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for body in [REPATCH, UPDATE]:
            result = subprocess.run([BASH, "-n"], input=body, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrapper_preserves_help_and_options(self):
        result = shell('bash build-xpadneo.sh --driver 580 --trim-cuda --help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--xpadneo-version", result.stdout)
        self.assertIn("Secure Boot off", result.stdout)

    def test_xpadneo_default_and_cli_overrides(self):
        parser = INSTALLER.split('[[ "$XPADNEO_VERSION" =~', 1)[0]
        cases = [
            ([], "0"),
            (["--no-xpadneo"], "0"),
            (["--no-xpadneo", "--xpadneo"], "1"),
            (["--xpadneo", "--no-xpadneo"], "0"),
            (["--no-xpadneo", "--xpadneo-version", "v0.10.4"], "1"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    [BASH, "-s", "--", *args],
                    input=parser + '\n' + 'printf "%s" "$ADD_XPADNEO"',
                    text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_cli_preserves_arguments_for_mount_namespace(self):
        parser = INSTALLER.split('[[ "$XPADNEO_VERSION" =~', 1)[0]
        args = ["--trim-cuda", "--no-xpadneo", "image with spaces.img"]
        result = subprocess.run(
            [BASH, "-s", "--", *args],
            input=parser + '\n' + 'printf "%s\\n" "${ORIGINAL_ARGS[@]}"',
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), args)

    def test_rejects_multiple_images_before_building(self):
        result = shell("bash steamos-nvidia-installer.sh first.img second.img")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Only one input image", result.stderr)

    def test_rejects_unpinned_xpadneo_version(self):
        result = shell("bash steamos-nvidia-installer.sh --xpadneo-version latest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact xpadneo tag", result.stderr)

    def test_runtime_keeps_xpadneo_and_diagnostics(self):
        self.assertIn('pc_install_xpadneo "$NEWROOT"', REPATCH)
        self.assertIn('"$XPADNEO_SHA256"', REPATCH)
        self.assertIn("steamos-nvidia-diagnostics", REPATCH)
        self.assertIn('[[ $TRIM_CUDA -eq 1 ]]', REPATCH)

    def test_completion_is_checked_after_grub(self):
        self.assertLess(REPATCH.index('chroot "$NEWROOT" update-grub'),
                        REPATCH.rindex('pc_write_complete "$NEWROOT"'))
        self.assertLess(REPATCH.index('pc_slot_complete "$NEWROOT"', REPATCH.index('pc_write_complete')),
                        REPATCH.rindex("SUCCESS=1"))

    def test_update_checks_final_space_before_completion(self):
        self.assertIn('pc_copy_update_payload "$MERGED" "$NEWROOT"', REPATCH)
        self.assertNotIn('pc_require_space "$NEWROOT" "$((PAYLOAD_KB', REPATCH)
        final_check = REPATCH.index('pc_require_space "$NEWROOT" 256')
        self.assertLess(REPATCH.index('pc_write_runtime_info "$NEWROOT"'), final_check)
        self.assertLess(final_check, REPATCH.rindex('pc_write_complete "$NEWROOT"'))

    def test_builder_cleanup_has_no_updater_variables(self):
        cleanup = INSTALLER.split("cleanup() {\n", 1)[1].split("trap cleanup EXIT", 1)[0]
        self.assertNotIn("NEWROOT", cleanup)
        self.assertNotIn("SUCCESS", cleanup)


class Support(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.arg = self.path.name

    def run_helper(self, code):
        return shell('set -eu; source lib/pc-support.sh; root="$PWD/$1"; ' + code, self.arg)

    def test_bluetooth_defaults_match_original_wrapper(self):
        result = self.run_helper('pc_configure_xpadneo_bluetooth "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)
        main = configparser.ConfigParser()
        main.read(self.path / "etc/bluetooth/main.conf")
        self.assertEqual(dict(main["General"]), {
            "controllermode": "dual", "justworksrepairing": "confirm"})
        self.assertEqual(dict(main["LE"]), {
            "minconnectioninterval": "7", "maxconnectioninterval": "9",
            "connectionlatency": "0"})
        inputs = configparser.ConfigParser()
        inputs.read(self.path / "etc/bluetooth/input.conf")
        self.assertEqual(dict(inputs["General"]), {
            "userspacehid": "true", "classicbondedonly": "false",
            "leautosecurity": "false"})

    def test_bluetooth_merges_sections_and_preserves_backup(self):
        directory = self.path / "etc/bluetooth"
        directory.mkdir(parents=True)
        original = ("# local settings\n[General]\nName=Living room\n"
                    "ControllerMode = bredr\n[Policy]\nAutoEnable=true\n"
                    "[LE]\nMinConnectionInterval=30\n[General]\n"
                    "JustWorksRepairing=never\n[LE]\nConnectionLatency=5\n")
        main = directory / "main.conf"
        main.write_text(original)
        (directory / "input.conf").write_text(
            "[General]\nUserspaceHID=true\nClassicBondedOnly=true\n"
            "LEAutoSecurity=true\nIdleTimeout=15\n")
        result = self.run_helper('pc_configure_xpadneo_bluetooth "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)
        first = main.read_bytes()
        result = self.run_helper('pc_configure_xpadneo_bluetooth "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(main.read_bytes(), first)
        self.assertEqual((directory / "main.conf.before-xpadneo").read_text(), original)
        parsed = configparser.ConfigParser()
        parsed.read(main)
        self.assertEqual(parsed["General"]["Name"], "Living room")
        self.assertEqual(parsed["General"]["ControllerMode"], "dual")
        self.assertEqual(parsed["Policy"]["AutoEnable"], "true")
        self.assertEqual(parsed["LE"]["ConnectionLatency"], "0")
        parsed.read(directory / "input.conf")
        self.assertEqual(parsed["General"]["IdleTimeout"], "15")
        self.assertEqual(parsed["General"]["ClassicBondedOnly"], "false")
        self.assertEqual(parsed["General"]["LEAutoSecurity"], "false")
        self.assertIn("# local settings", main.read_text())

    def test_bluetooth_does_not_touch_host_or_sibling_root(self):
        sibling = self.path / "other/etc/bluetooth"
        sibling.mkdir(parents=True)
        (sibling / "main.conf").write_text("unchanged")
        result = self.run_helper('pc_configure_xpadneo_bluetooth "$root/target"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((sibling / "main.conf").read_text(), "unchanged")

    def test_runtime_info_tracks_new_slot_without_changing_origin(self):
        (self.path / "etc").mkdir()
        (self.path / "etc/os-release").write_text('VERSION_ID="3.8.10"\n')
        dest = self.path / "usr/lib/steamos-nvidia"
        dest.mkdir(parents=True)
        (dest / "build-info.txt").write_text("original build")
        result = self.run_helper(
            'pc_write_runtime_info "$root" old-kernel 610.1-1 v0.10.4 1; '
            'pc_write_runtime_info "$root" new-kernel 610.1-1 "" 0')
        self.assertEqual(result.returncode, 0, result.stderr)
        info = (dest / "runtime-info.txt").read_text()
        self.assertIn("Kernel: new-kernel", info)
        self.assertNotIn("old-kernel", info)
        self.assertIn("xpadneo: disabled", info)
        self.assertIn("Trim CUDA: 0", info)
        self.assertEqual((dest / "build-info.txt").read_text(), "original build")
        self.assertFalse((dest / "runtime-info.txt.part").exists())

    def display_fixture(self):
        session = self.path / "usr/lib/steamos/gamescope-session"
        session.parent.mkdir(parents=True)
        session.write_text('#!/bin/bash\nexport STEAM_GAMESCOPE_HDR_SUPPORTED=1\nread_gamescope_env() { :; }\n')
        helper = self.path / "usr/lib/steamos-nvidia/hdr-defaults.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("# fixture")
        (helper.parent / "safe-graphics.py").write_text("# fixture")
        return session

    def test_display_policy_preserves_stock_session_and_is_repeatable(self):
        session = self.display_fixture()
        original = session.read_bytes()
        result = self.run_helper('pc_install_display_policy "$root"; pc_install_display_policy "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(session.read_bytes(), original)
        dropin = self.path / "usr/lib/systemd/user/steam-launcher.service.d/10-nvidia-hdr-default.conf"
        self.assertIn("ExecStartPre=/usr/bin/python3 /usr/lib/steamos-nvidia/hdr-defaults.py", dropin.read_text())

    def test_display_policy_removes_only_obsolete_override(self):
        session = self.display_fixture()
        original = session.read_text()
        session.write_text(original + '. /usr/lib/steamos-nvidia/display-session.sh\n')
        old = self.path / "usr/lib/steamos-nvidia/display-session.sh"
        old.write_text("export DXVK_HDR=0\n")
        result = self.run_helper('pc_install_display_policy "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(session.read_text(), original)
        self.assertFalse(old.exists())

    def test_missing_hdr_initializer_rejects_installation(self):
        self.display_fixture()
        (self.path / "usr/lib/steamos-nvidia/hdr-defaults.py").unlink()
        result = self.run_helper('pc_install_display_policy "$root"')
        self.assertNotEqual(result.returncode, 0)

    def test_package_selection_accepts_full_revision_and_literal_prefix(self):
        (self.path / "packages").write_text("\n".join([
            'nvidia-utils-610.57.04-1-x86_64.pkg.tar.zst',
            'nvidia-utils-610.57.04-10-x86_64.pkg.tar.zst',
            'nvidia-utils-610.57.041-1-x86_64.pkg.tar.zst',
            'nvidia-utils-615.71.09-1-x86_64.pkg.tar.zst']))
        result = self.run_helper(
            'pc_package_matches nvidia-utils 610.57.04-1 < "$root/packages"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "nvidia-utils-610.57.04-1-x86_64.pkg.tar.zst")
        result = self.run_helper(
            'pc_package_matches nvidia-utils 610.57.04 < "$root/packages" | sort -V')
        self.assertEqual(result.stdout.splitlines(), [
            "nvidia-utils-610.57.04-1-x86_64.pkg.tar.zst",
            "nvidia-utils-610.57.04-10-x86_64.pkg.tar.zst"])

    def test_changed_versions_are_included(self):
        (self.path / "before").write_text("nvidia-utils 580-1\nsame 1\n")
        (self.path / "after").write_text("nvidia-utils 610-1\nsame 1\nnew-package 2\n")
        result = self.run_helper('pc_changed_packages "$root/before" "$root/after"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["nvidia-utils", "new-package"])

    def test_empty_package_baseline(self):
        (self.path / "before").write_text("")
        (self.path / "after").write_text("new-package 1\n")
        result = self.run_helper('pc_changed_packages "$root/before" "$root/after"')
        self.assertEqual(result.stdout, "new-package\n")

    def test_recovery_skips_balance_when_space_is_sufficient(self):
        result = self.run_helper('pc_require_space() { return 0; }; btrfs() { exit 99; }; pc_recover_update_space "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_rejects_live_root_and_non_btrfs(self):
        for target in ['/', '$root']:
            result = self.run_helper('pc_require_space() { return 1; }; findmnt() { echo ext4; }; btrfs() { exit 99; }; pc_recover_update_space "' + target + '"')
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('separate Btrfs', result.stderr)

    def test_recovery_rechecks_after_partial_balance_failure(self):
        result = self.run_helper('pc_require_space() { test -e "$root/reclaimed"; }; findmnt() { echo btrfs; }; btrfs() { if [[ "$1" == balance ]]; then touch "$root/reclaimed"; return 1; fi; }; pc_recover_update_space "$root"')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_is_bounded_and_does_not_accept_low_space(self):
        result = self.run_helper('pc_require_space() { return 1; }; findmnt() { echo btrfs; }; btrfs() { printf "%s\\n" "$*" >> "$root/calls"; }; pc_recover_update_space "$root"')
        self.assertEqual(result.returncode, 1, result.stderr)
        calls = (self.path / 'calls').read_text().splitlines()
        balances = [line for line in calls if line.startswith('balance')]
        self.assertEqual(len(balances), 4)
        self.assertTrue(all('limit=1' in line and '-musage=' in line for line in balances))

    def test_payload_recovery_runs_before_copy_and_after_sync(self):
        result = self.run_helper('pc_recover_update_space() { echo recover; }; rsync() { echo copy; }; sync() { echo sync; }; pc_copy_update_payload source "$root" files modules kernel')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['recover', 'copy', 'copy', 'sync', 'recover'])

    def test_payload_recovery_failure_still_rejects_update(self):
        for stage in ('before', 'after'):
            code = ('pc_recover_update_space() { ' + ('return 1;' if stage == 'before' else '[[ ! -f "$root/copied" ]];') + ' }; '
                    'rsync() { touch "$root/copied"; }; sync() { :; }; '
                    'pc_copy_update_payload source "$root" files modules kernel')
            (self.path / 'copied').unlink(missing_ok=True)
            result = self.run_helper(code)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((self.path / 'copied').exists(), stage == 'after')

    def test_insufficient_space_fails(self):
        result = self.run_helper(
            'df() { printf "Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 20 19 1 95%% /\\n"; }; '
            'pc_require_space "$root" 100')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Not enough free space", result.stderr)

    def test_sufficient_space_passes(self):
        result = self.run_helper(
            'df() { printf "Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 200 10 190 5%% /\\n"; }; '
            'pc_require_space "$root" 100')
        self.assertEqual(result.returncode, 0, result.stderr)

    def fixture(self):
        files = {
            "usr/share/vulkan/icd.d/nvidia_icd.json": "{}",
            "usr/lib/steamos-nvidia/repatch.sh": "#!/bin/bash\n",
            "usr/bin/steamos-update.orig": "#!/bin/bash\n",
            "usr/bin/steamos-update": "# self-healing\n",
            "etc/default/grub": "nvidia-drm.modeset=1\n",
            "efi/EFI/steamos/grub.cfg": "nvidia-drm.modeset=1\n",
            "etc/udev/rules.d/60-xpadneo.rules": "rule",
            "etc/udev/rules.d/70-xpadneo-disable-hidraw.rules": "rule",
            "etc/modules-load.d/xpadneo.conf": "hid_xpadneo\n",
        }
        for name in ["nvidia", "nvidia_modeset", "nvidia_drm", "nvidia_uvm", "hid_xpadneo"]:
            files["modules/" + name + ".ko"] = "not a real kernel module"
            files["modules/" + name + ".ko.vermagic"] = "6.1-neptune SMP"
            files["modules/" + name + ".ko.version"] = "v0.10.4" if name == "hid_xpadneo" else "610.1"
        for name, body in files.items():
            path = self.path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        hook = self.path / "usr/lib/rauc/post-install.sh"
        hook.parent.mkdir(parents=True)
        hook.write_text("# steamos-nvidia shared-update-hook v1\n")
        (self.path / "usr/lib/holo/pacmandb/local/nvidia-utils-610.1-1").mkdir(parents=True)
        return r'''
pc_check_addons() { return 0; } # Driver fixture; addon failures tested separately.
chmod +x "$root/usr/lib/steamos-nvidia/repatch.sh" "$root/usr/bin/steamos-update.orig"
modinfo() {
  if [[ $1 == -b ]]; then printf '%s/modules/%s.ko\n' "$2" "$7"
  elif [[ $2 == vermagic ]]; then cat "$3.vermagic"
  elif [[ $2 == version ]]; then cat "$3.version"
  else return 1
  fi
}
'''

    def test_module_alone_is_not_completion(self):
        result = self.run_helper(self.fixture() +
            'pc_slot_complete "$root" 6.1-neptune fingerprint 610.1-1 v0.10.4')
        self.assertNotEqual(result.returncode, 0)

    def test_complete_slot_is_accepted(self):
        result = self.run_helper(self.fixture() +
            'pc_write_complete "$root" 6.1-neptune fingerprint; '
            'pc_slot_complete "$root" 6.1-neptune fingerprint 610.1-1 v0.10.4')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_fingerprint_is_rejected(self):
        result = self.run_helper(self.fixture() +
            'pc_write_complete "$root" 6.1-neptune old; '
            'pc_slot_complete "$root" 6.1-neptune new 610.1-1 v0.10.4')
        self.assertNotEqual(result.returncode, 0)

    def test_missing_xpadneo_is_rejected(self):
        prefix = self.fixture()
        (self.path / "modules/hid_xpadneo.ko").unlink()
        result = self.run_helper(prefix +
            'pc_check_driver "$root" 6.1-neptune 610.1-1 v0.10.4')
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_kernel_is_rejected(self):
        prefix = self.fixture()
        (self.path / "modules/nvidia.ko.vermagic").write_text("6.2-neptune SMP")
        result = self.run_helper(prefix + 'pc_check_driver "$root" 6.1-neptune 610.1-1')
        self.assertNotEqual(result.returncode, 0)

    def test_zero_byte_module_is_rejected(self):
        prefix = self.fixture()
        (self.path / "modules/nvidia.ko").write_text("")
        result = self.run_helper(prefix + 'pc_check_driver "$root" 6.1-neptune 610.1-1')
        self.assertNotEqual(result.returncode, 0)

    def test_missing_grub_configuration_is_rejected(self):
        prefix = self.fixture()
        (self.path / "efi/EFI/steamos/grub.cfg").write_text("")
        result = self.run_helper(prefix +
            'pc_write_complete "$root" 6.1-neptune fingerprint; '
            'pc_slot_complete "$root" 6.1-neptune fingerprint 610.1-1')
        self.assertNotEqual(result.returncode, 0)

    def test_failed_repatch_removes_marker_and_restores_readonly(self):
        cleanup = "cleanup() {\n" + REPATCH.split("cleanup() {\n", 1)[1].split("trap cleanup EXIT", 1)[0]
        result = self.run_helper(cleanup + r'''
NEWROOT="$root"; MERGED="$root/merged"; WORK="$root/work"; WORKIMG="$root/work.img"
SUCCESS=0; WAS_RO=1
mountpoint() { [[ "$2" == "$root" ]]; }
btrfs() { printf 'btrfs %s\n' "$*"; }
rm() { printf 'rm %s\n' "$*"; }
umount() { :; }
rmdir() { :; }
cleanup
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("complete.part", result.stdout)
        self.assertIn("ro true", result.stdout)

    def test_successful_repatch_preserves_marker(self):
        cleanup = "cleanup() {\n" + REPATCH.split("cleanup() {\n", 1)[1].split("trap cleanup EXIT", 1)[0]
        result = self.run_helper(cleanup + r'''
NEWROOT="$root"; MERGED="$root/merged"; WORK="$root/work"; WORKIMG="$root/work.img"
SUCCESS=1; WAS_RO=1
mountpoint() { [[ "$2" == "$root" ]]; }
btrfs() { printf 'btrfs %s\n' "$*"; }
rm() { printf 'rm %s\n' "$*"; }
umount() { :; }
rmdir() { :; }
cleanup
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("complete.part", result.stdout)
        self.assertIn("ro true", result.stdout)


if __name__ == "__main__":
    unittest.main()


class UpdateFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.rel = self.path.name
        (self.path / "boot").mkdir()
        (self.path / "boot/A.conf").write_text(
            "image-invalid: 0\nboot-requested-at: 0\nboot-attempts: 0\n")
        (self.path / "boot/B.conf").write_text(
            "image-invalid: 0\nboot-requested-at: 10\nboot-attempts: 3\n")

    def execute(self, repair_code=0, updater_code=0, argument="apply", shared=False):
        (self.path / "real").write_text("#!/bin/bash\nexit " + str(updater_code) + "\n")
        (self.path / "repatch").write_text(
            "#!/bin/bash\n"
            f"grep -qx 'image-invalid: 1' {self.rel}/boot/B.conf || exit 90\n"
            f"echo checked > {self.rel}/repair-ran\nexit {repair_code}\n")
        body = UPDATE.replace("/run/steamos-nvidia-update.lock", self.rel + "/lock")
        body = body.replace("/usr/bin/steamos-update.orig", self.rel + "/real")
        body = body.replace("/usr/lib/steamos-nvidia/repatch.sh", self.rel + "/repatch")
        body = body.replace("/var/log/steamos-nvidia-repatch.log", self.rel + "/log")
        body = body.replace("/esp/SteamOS/conf", self.rel + "/boot")
        body = body.replace("/usr/lib/rauc/post-install.sh", self.rel + "/rauc-hook")
        (self.path / "rauc-hook").write_text("# steamos-nvidia shared-update-hook v1\n" if shared else "# legacy\n")
        mocks = """flock() { return 0; }
sync() { return 0; }
steamos-bootconf() { [[ "$1" != this-image ]] || echo A; return 0; }
"""
        (self.path / "update").write_text(mocks + body)
        return shell('chmod +x "$1/real" "$1/repatch"; bash "$1/update" "$2"',
                     self.rel, argument)

    def test_shared_hook_does_not_run_repair_twice(self):
        for code in (0, 1):
            result = self.execute(updater_code=code, shared=True)
            self.assertEqual(result.returncode, code)
            self.assertFalse((self.path / "repair-ran").exists())

    def test_success_only_enables_slot_after_repair(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.path / "repair-ran").exists())
        self.assertIn("image-invalid: 0", (self.path / "boot/B.conf").read_text())
        self.assertIn("image-invalid: 0", (self.path / "boot/A.conf").read_text())

    def test_failed_repair_keeps_current_slot(self):
        result = self.execute(repair_code=1)
        self.assertEqual(result.returncode, 1, result.stderr)
        other = (self.path / "boot/B.conf").read_text()
        self.assertIn("image-invalid: 1", other)
        self.assertIn("boot-attempts: 0", other)
        self.assertIn("boot-requested-at: 0", other)
        self.assertIn("image-invalid: 0", (self.path / "boot/A.conf").read_text())

    def test_failed_updater_never_starts_repair(self):
        result = self.execute(updater_code=5)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertFalse((self.path / "repair-ran").exists())

    def test_check_does_not_repair_or_change_boot_entries(self):
        result = self.execute(argument="check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.path / "repair-ran").exists())
        self.assertIn("image-invalid: 0", (self.path / "boot/B.conf").read_text())

    def test_unknown_boot_format_stops_before_repair(self):
        (self.path / "boot/B.conf").write_text("different-format: true\n")
        result = self.execute()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.path / "repair-ran").exists())

    def test_missing_active_config_does_not_modify_any_slot(self):
        (self.path / "boot/A.conf").unlink()
        before = (self.path / "boot/B.conf").read_bytes()
        result = self.execute()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.path / "boot/B.conf").read_bytes(), before)
        self.assertFalse((self.path / "repair-ran").exists())

    def test_malformed_later_slot_does_not_partially_edit_earlier_slot(self):
        (self.path / "boot/C.conf").write_text("different-format: true\n")
        before = (self.path / "boot/B.conf").read_bytes()
        result = self.execute()
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.path / "boot/B.conf").read_bytes(), before)
        self.assertFalse((self.path / "repair-ran").exists())

    def test_terminated_repair_keeps_target_disabled(self):
        before = (self.path / "boot/A.conf").read_bytes()
        result = self.execute(repair_code='$(kill -TERM $$)')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((self.path / "boot/A.conf").read_bytes(), before)
        other = (self.path / "boot/B.conf").read_text()
        self.assertIn("image-invalid: 1", other)
        self.assertIn("boot-attempts: 0", other)



class BluetoothInstall(unittest.TestCase):
    def test_install_is_repeatable_and_dependency_failure_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / 'usr/lib/steamos-nvidia/bluetooth-resume.py'
            helper.parent.mkdir(parents=True)
            helper.write_text('# test helper\n')
            code = '''source lib/pc-support.sh
chroot() { return 0; }
pc_install_bluetooth_resume "$1" && pc_install_bluetooth_resume "$1"
'''
            result = shell(code, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            unit = root / 'usr/lib/systemd/user/steamos-nvidia-bluetooth-resume.service'
            link = unit.parent / 'default.target.wants' / unit.name
            self.assertEqual(link.resolve(), unit)
            self.assertIn('/usr/lib/steamos-nvidia/bluetooth-resume.py', unit.read_text())
            self.assertFalse((root / 'etc/bluetooth').exists())
            result = shell('source lib/pc-support.sh; chroot() { return 1; }; pc_install_bluetooth_resume "$1"', tmp)
            self.assertNotEqual(result.returncode, 0)


class AddonIntegrity(unittest.TestCase):
    def test_missing_changed_and_replaced_addons_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / 'usr/lib/steamos-nvidia'
            dest.mkdir(parents=True)
            for name in ['hdr-defaults.py', 'safe-graphics.py', 'bluetooth-resume.py', 'install-target.py']:
                shutil.copyfile(ROOT / 'scripts' / name, dest / name)
            (root / 'usr/lib/steamos').mkdir()
            (root / 'usr/lib/steamos/gamescope-session').write_text('# stock session\n')
            (root / 'usr/bin').mkdir()
            shutil.copyfile(ROOT / 'scripts/steamos-nvidia-diagnostics', root / 'usr/bin/steamos-nvidia-diagnostics')
            code = 'source lib/pc-support.sh; chroot() { return 0; }; pc_install_display_policy "$1" && pc_install_bluetooth_resume "$1" && pc_write_addon_manifest "$1" && pc_check_addons "$1"'
            result = shell(code, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            # An intact manifest must not hide changed files or a disabled link.
            helper = dest / 'hdr-defaults.py'
            original = helper.read_bytes()
            helper.write_text('broken')
            self.assertNotEqual(shell('source lib/pc-support.sh; pc_check_addons "$1"', tmp).returncode, 0)
            helper.write_bytes(original)
            link = root / 'usr/lib/systemd/user/default.target.wants/steamos-nvidia-bluetooth-resume.service'
            link.unlink()
            self.assertNotEqual(shell('source lib/pc-support.sh; pc_check_addons "$1"', tmp).returncode, 0)
            link.symlink_to('../steamos-nvidia-bluetooth-resume.service')
            helper.unlink()
            self.assertNotEqual(shell('source lib/pc-support.sh; pc_check_addons "$1"', tmp).returncode, 0)

    def test_update_checks_addons_before_completion_marker(self):
        self.assertLess(REPATCH.index('pc_check_addons "$NEWROOT"'), REPATCH.rindex('pc_write_complete "$NEWROOT"'))
        self.assertNotIn('pc_write_addon_manifest', REPATCH)

class UserspaceDependencies(unittest.TestCase):
    def test_missing_32bit_library_fails_and_records_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = '''source lib/pc-support.sh
chroot() {
  if [[ $* == *lib32/libGLX* ]]; then echo 'libc.so.6: version GLIBC_TEST not found' >&2; return 1; fi
  echo 'dependencies resolved'
}
pc_write_userspace_report "$1"
'''
            result = shell(code, tmp)
            self.assertNotEqual(result.returncode, 0)
            report = Path(tmp) / 'usr/lib/steamos-nvidia/userspace-check.txt'
            self.assertIn('GLIBC_TEST not found', report.read_text())
            self.assertIn('Result: 1', report.read_text())
            self.assertFalse(report.with_suffix('.txt.part').exists())

    def test_correct_loaders_and_clean_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = shell('source lib/pc-support.sh; chroot() { printf "%s\\n" "$*"; }; pc_write_userspace_report "$1"', tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('/usr/lib32/ld-linux.so.2 --list /usr/lib32/libEGL_nvidia.so.0', result.stdout)
            self.assertIn('/usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/libGLX_nvidia.so.0', result.stdout)
            self.assertEqual(result.stdout.count('/usr/bin/env -i LC_ALL=C'), 5)
            self.assertIn('Result: 0', result.stdout)

    def test_check_precedes_completion_and_uses_final_root(self):
        self.assertLess(REPATCH.index('pc_write_userspace_report "$NEWROOT"'), REPATCH.rindex('pc_write_complete "$NEWROOT"'))
        self.assertLess(REPATCH.index('chroot "$NEWROOT" ldconfig'), REPATCH.rindex('pc_write_userspace_report "$NEWROOT"'))
        self.assertLess(REPATCH.index('cp -a /usr/lib/steamos-nvidia/.'), REPATCH.rindex('pc_write_userspace_report "$NEWROOT"'))
        self.assertIn('pc_write_userspace_report "$MNT" || die', INSTALLER)
