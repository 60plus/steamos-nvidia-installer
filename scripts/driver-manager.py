#!/usr/bin/python3 -I
"""Desktop frontend for installed SteamOS driver changes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import html
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent
TOOL = '/usr/bin/steamos-nvidia-driver'


def dialog(kind, text, *options):
    args = ['zenity', kind, '--title', 'Change NVIDIA Driver']
    if '--width' not in options:
        args.extend(['--width', '820', '--height', '360'])
    if kind != '--list':
        args.append('--no-wrap')
    result = subprocess.run([*args, '--text', html.escape(text), *options],
                            text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or 'Could not display the NVIDIA Driver window.')
    return result


def versions(indexes):
    found = {}
    for package, index in indexes.items():
        found[package] = set(re.findall(re.escape(package) + r'-([0-9.]+-[0-9.]+)-x86_64\.pkg\.tar\.zst', index))
    candidates = [v for v in found['nvidia-utils'] if all(
        any(x.rsplit('-', 1)[0] == v.rsplit('-', 1)[0] for x in found[p])
        for p in ('nvidia-open-dkms', 'lib32-nvidia-utils'))]
    return sorted(candidates, key=lambda v: tuple(int(x) for x in re.split(r'[.-]', v)), reverse=True)


def supported_gpus(document):
    # Only the current-driver table, never the legacy tables further down.
    section = re.split(r'<h3[^>]*>\s*Current NVIDIA GPUs\s*</h3>', document, maxsplit=1, flags=re.I)
    if len(section) != 2:
        raise ValueError('NVIDIA support table is unavailable.')
    table = section[1].split('</table>', 1)[0]
    entries = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S | re.I):
        cells = [html.unescape(re.sub(r'<[^>]+>', '', cell)).strip()
                 for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)]
        if len(cells) < 2:
            continue
        name = ' '.join(cells[0].split())
        # Our open-module GeForce scope starts at Turing.
        family = re.search(r'GeForce (GTX 16|RTX [2-9]0)[0-9]{2}', name)
        ids = cells[1].upper().split()
        if family and len(ids) in (1, 3) and all(re.fullmatch(r'[0-9A-F]{4}', x) for x in ids):
            entries.append((family.group(1), tuple(ids)))
    return entries


def detected_gpus(root=Path('/sys/bus/pci/devices')):
    devices = []
    for path in root.iterdir():
        if (path / 'vendor').read_text().strip() == '0x10de' and (path / 'class').read_text().startswith('0x03'):
            devices.append(tuple((path / field).read_text().strip().removeprefix('0x').upper()
                                 for field in ('device', 'subsystem_vendor', 'subsystem_device')))
    if not devices:
        raise ValueError('No NVIDIA display adapter was detected.')
    return devices


def gpu_summary(document, devices):
    entries = supported_gpus(document)
    if not all(any(ids == device or (len(ids) == 1 and ids[0] == device[0])
                   for _, ids in entries) for device in devices):
        return None
    return ', '.join(sorted({family for family, _ in entries}))


def compatible_versions(candidates, devices, fetch):
    releases = list(dict.fromkeys(v.rsplit('-', 1)[0] for v in candidates))
    def check(release):
        try:
            return release, gpu_summary(fetch(release), devices)
        except (OSError, ValueError, subprocess.SubprocessError):
            return release, None
    with ThreadPoolExecutor(max_workers=3) as pool:
        summaries = dict(pool.map(check, releases))
    return [(v, summaries[v.rsplit('-', 1)[0]]) for v in candidates if summaries[v.rsplit('-', 1)[0]]]


def worker(action, version):
    if action == 'install' and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+-[0-9]+(?:\.[0-9]+)*', version):
        raise ValueError('Invalid package version.')
    command = [TOOL, action] + ([version] if action == 'install' else [])
    print('Preparation may appear paused while downloading or compiling. Please wait and keep the PC on.\n', flush=True)
    print('Enter your SteamOS password if requested. No password is stored.\n', flush=True)
    result = subprocess.run(['sudo', 'systemd-run', '--unit=steamos-driver-change',
                             '--collect', '--wait', '--pipe', *command])
    if result.returncode:
        dialog('--error', 'The operation did not complete. Read the output in this window. Do not reboot to test a failed change.')
        return result.returncode
    message = 'Driver prepared.' if action == 'install' else 'Previous system selected.'
    if dialog('--question', message + '\nRestart now? Choose Later to keep working.',
              '--ok-label', 'Restart', '--cancel-label', 'Later').returncode == 0:
        subprocess.run(['systemctl', 'reboot'], check=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shortcut', action='store_true')
    parser.add_argument('--run', choices=['install', 'rollback'])
    parser.add_argument('version', nargs='?', default='')
    args = parser.parse_args()
    if args.shortcut:
        if 'VARIANT_ID=steamdeck-oobe' not in Path('/etc/os-release').read_text():
            folder = subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip()
            if folder and Path(folder).is_absolute() and Path(folder).is_dir():
                old = Path(folder) / 'NVIDIA Driver.desktop'
                if old.is_file() and 'Exec=/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-manager.py' in old.read_text():
                    old.unlink()
                target = Path(folder) / 'Change NVIDIA Driver.desktop'
                if not target.exists():
                    shutil.copyfile('/usr/share/applications/steamos-nvidia-driver.desktop', target)
                    os.chmod(target, 0o755)
        return 0
    if args.run:
        return worker(args.run, args.version)
    if 'VARIANT_ID=steamdeck-oobe' in Path('/etc/os-release').read_text():
        raise ValueError('Use NVIDIA Driver in the installed system, not on the installer USB.')
    current = Path('/sys/module/nvidia/version').read_text().strip()
    choice = dialog('--list', 'Current driver: ' + current + '\nChoose an action.\nLoading versions and preparing a driver can take several minutes with no visible progress. Please wait.',
                    '--column', 'Action', 'Choose driver version', 'Return to previous driver')
    if choice.returncode:
        return 0
    action = 'rollback'
    version = ''
    if choice.stdout.strip() == 'Choose driver version':
        spec = importlib.util.spec_from_file_location('driver_change', BASE / 'driver-change.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print('Loading available versions from the Arch archive...', flush=True)
        indexes = {p: module.run('curl', '--fail', '--silent', '--show-error', '--location',
                    '--connect-timeout', '15', '--max-time', '90',
                    f'https://archive.archlinux.org/packages/{p[0]}/{p}/') for p in module.CORE}
        def fetch_support(release):
            return module.run('curl', '--fail', '--silent', '--show-error', '--location',
                              '--connect-timeout', '10', '--max-time', '25',
                              f'https://download.nvidia.com/XFree86/Linux-x86_64/{release}/README/supportedchips.html')
        candidates = versions(indexes)[:10]
        compatible = compatible_versions(candidates, detected_gpus(), fetch_support)
        available = [v for v, _ in compatible]
        if not available:
            raise ValueError('No verified driver match was found among the ten latest complete package sets. Check your network. This manager supports GeForce GTX 16 and RTX GPUs; missing NVIDIA support data is not treated as compatibility.')
        rows = [cell for version, families in compatible
                for cell in (version, families, '🟢 GPU match (NVIDIA)')]
        selected = dialog('--list', 'Current driver: ' + current + '\nShowing NVIDIA-documented matches for your GPU. Families below describe the driver range, not a guarantee for every model or SteamOS setup.',
                          '--width', '1000', '--height', '540', '--column', 'Version', '--column', 'GPU families (NVIDIA)', '--column', 'Your GPU', *rows)
        if selected.returncode:
            return 0
        version = selected.stdout.strip()
        if version not in available:
            raise ValueError('Invalid selection.')
        module.resolve(version, module.read_config(module.BASE / 'driver.conf'))
        action = 'install'
    text = ('Prepare ' + version + '?\nRequires about 17 GiB free on /home and replaces the inactive OS slot. '
            'Finish pending OS updates first. Preparation may appear paused while downloading or compiling. Please wait and keep the PC on until it finishes.' if action == 'install'
            else 'Select the previous system? The tool checks whether it is still available before changing the boot selection.')
    if dialog('--question', text, '--ok-label', 'Continue', '--cancel-label', 'Cancel').returncode:
        return 0
    # The terminal provides normal sudo authentication and live service output.
    # systemd keeps preparation running if this window is closed.
    return subprocess.call(['konsole', '--hold', '-e', '/usr/bin/python3', '-I',
                            str(BASE / 'driver-manager.py'), '--run', action, version])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        dialog('--error', str(error))
        sys.exit(1)
