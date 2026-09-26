#!/usr/bin/python3
"""Select and validate an installation disk before calling Valve's repair tool."""
import argparse
import csv
import re
import html
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile

MIB = 1024 * 1024
# Matches the supported Valve repair image layout. Build-time checks pin it.
MIN_DISK_BYTES = 11238 * MIB
PARTS = [
    ('esp', 'vfat', 256), ('efi-A', 'vfat', 64), ('efi-B', 'vfat', 64),
    ('rootfs-A', 'btrfs', 5120), ('rootfs-B', 'btrfs', 5120),
    ('var-A', 'ext4', 256), ('var-B', 'ext4', 256), ('home', 'ext4', 100),
]


class InvalidTarget(ValueError):
    pass


def run(args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def flatten(nodes, parent=None):
    result = []
    for node in nodes:
        item = dict(node, parent=parent)
        result.append(item)
        result.extend(flatten(node.get('children', []), item))
    return result


def source_disk(nodes, root_id):
    matches = [n for n in flatten(nodes) if n.get('maj:min') == root_id]
    if len(matches) != 1 or matches[0]['type'] != 'part':
        raise InvalidTarget('Cannot identify the installer disk safely. Boot directly from the installer USB.')
    root = matches[0]
    parent = root['parent']
    if not parent or parent['type'] != 'disk' or parent['parent'] is not None:
        raise InvalidTarget('Unsupported installer storage layout. No disks will be changed.')
    return parent, root


def busy(disk):
    return any(any(m for m in (n.get('mountpoints') or [])) for n in flatten([disk]))


def identity(disk):
    return json.dumps([disk['name'], disk['maj:min'], int(disk['size']),
                       disk.get('serial'), disk.get('model'), disk.get('tran')], separators=(',', ':'))


def candidates(nodes, root_id):
    # A mounted partition does not hide a disk. The desktop's automounter picks up
    # an installed system as soon as the installer starts, so the only disk that was
    # ever offered was an empty one, and the maintainer had to delete partitions by
    # hand before the installer would erase them anyway. Hiding the disk protected
    # nothing, and a partition editor has none of the checks this tool applies. The
    # installer's own disk stays excluded by device number, and release_disk unmounts
    # the chosen one before validation, which still refuses anything left mounted.
    source, _ = source_disk(nodes, root_id)
    return [d for d in nodes if d['type'] == 'disk' and d['maj:min'] != source['maj:min']
            and not d.get('ro') and int(d['size']) >= MIN_DISK_BYTES
            and int(d.get('log-sec') or 0) == 512]


def contents(disk):
    """Name what is already on a disk, so erasing it is a deliberate choice.

    This is the protection that replaces hiding a disk: a data disk now appears in
    the list too, and the only thing standing between it and an erase is what the
    reader is told here and in the confirmation that follows.
    """
    parts = [p for p in disk.get('children', []) if p.get('type') == 'part']
    if not parts:
        return 'no partitions'
    if [p.get('partlabel') for p in parts] == [label for label, _, _ in PARTS]:
        return 'an existing SteamOS installation'
    return '%d partition%s of other data' % (len(parts), '' if len(parts) == 1 else 's')


def validate(nodes, root_id, target, mode, expected=None):
    source, root = source_disk(nodes, root_id)
    matches = [d for d in nodes if d['name'] == target and d['type'] == 'disk']
    if len(matches) != 1:
        raise InvalidTarget('The selected disk is no longer available.')
    disk = matches[0]
    if disk['maj:min'] == source['maj:min']:
        raise InvalidTarget('The installer disk cannot be used as the destination.')
    if expected is not None and identity(disk) != expected:
        raise InvalidTarget('The selected disk changed. Close the installer and select it again.')
    if any(n.get('ro') for n in flatten([disk])) or busy(disk):
        raise InvalidTarget('The selected disk is read-only or in use. Unmount its partitions and disable swap on it first.')
    if int(disk.get('log-sec') or 0) != 512:
        raise InvalidTarget('This installer requires a disk with 512-byte logical sectors.')
    if int(disk['size']) < MIN_DISK_BYTES:
        raise InvalidTarget('The selected disk is too small for the SteamOS partition layout.')
    if mode == 'all':
        if int(root['size']) > 5120 * MIB:
            raise InvalidTarget('The installer root is larger than the supported target partition.')
    elif mode == 'system':
        parts = disk.get('children', [])
        if len(parts) != 8 or any(p['type'] != 'part' or p.get('children') for p in parts):
            raise InvalidTarget('Reinstall requires the standard eight-partition SteamOS layout.')
        prefix = target + ('p' if target[-1].isdigit() else '')
        indexed = {p['name']: p for p in parts}
        for number, (label, fs, minimum) in enumerate(PARTS, 1):
            p = indexed.get(prefix + str(number))
            needed = max(minimum * MIB, int(root['size'])) if label.startswith('rootfs-') else minimum * MIB
            if not p or p.get('partlabel') != label or p.get('fstype') != fs or int(p['size']) < needed:
                raise InvalidTarget(f'Partition {number} must be {label}, {fs}, with sufficient space.')
    else:
        raise InvalidTarget('Unsupported installation mode.')
    return disk


def snapshot():
    # Btrfs mount MAJ:MIN is an anonymous ID, not the backing block device.
    source = run(['findmnt', '-n', '-o', 'SOURCE', '/']).split('[', 1)[0]
    if not source.startswith('/dev/'):
        raise InvalidTarget('Cannot identify the installer block device.')
    device = os.stat(source)
    if not stat.S_ISBLK(device.st_mode):
        raise InvalidTarget('The installer source is not a block device.')
    root_id = f'{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}'
    fields = 'NAME,TYPE,MAJ:MIN,SIZE,MODEL,SERIAL,TRAN,RO,LOG-SEC,MOUNTPOINTS,PARTLABEL,FSTYPE'
    nodes = json.loads(run(['lsblk', '--json', '--bytes', '--paths', '-o', fields]))['blockdevices']
    return nodes, root_id


def description(disk):
    bus = 'USB (external)' if disk.get('tran') == 'usb' else disk.get('tran') or 'unknown bus'
    return (f"{int(disk['size']) / 1024**3:.1f} GiB / {disk.get('model') or 'Unknown model'}"
            f" / {bus} / holds {contents(disk)}")


def release_disk(disk):
    """Unmount whatever the desktop mounted on the chosen disk, before it is erased.

    Measured in the installer environment on 2026-09-25: udisks2 had mounted var-A,
    var-B and home of an installed system under /run/media/deck, so the disk counted
    as busy and no target was offered at all.
    """
    released = []
    for node in flatten([disk]):
        for point in (node.get('mountpoints') or []):
            if not point:
                continue
            if point == '[SWAP]':
                raise InvalidTarget(f'{node["name"]} is in use as swap. Run '
                                    f'"sudo swapoff {node["name"]}" and select the disk again.')
            unmounted = subprocess.run(['udisksctl', 'unmount', '-b', node['name']],
                                       capture_output=True, text=True, timeout=60)
            if unmounted.returncode != 0:
                unmounted = subprocess.run(['sudo', '-n', 'umount', node['name']],
                                           capture_output=True, text=True, timeout=60)
            if unmounted.returncode != 0:
                raise InvalidTarget(f'Could not unmount {node["name"]} from {point}. '
                                    'Close anything using it, then select the disk again.')
            released.append(f'{node["name"]} from {point}')
    if released:
        print('Unmounted before installation: ' + ', '.join(released))
    return released


def guard(target, mode, expected):
    nodes, root_id = snapshot()
    return validate(nodes, root_id, target, mode, expected)


def check_boot_files(root, expected):
    boot = root / 'efi/boot/bootx64.efi'
    flag = root / 'efi/boot/steamcl-restricted'
    if not boot.is_file() or boot.stat().st_size == 0 or not flag.is_file():
        raise InvalidTarget('The selected disk is missing its standalone UEFI boot files.')
    if boot.read_bytes() != expected.read_bytes():
        raise InvalidTarget('The standalone UEFI loader does not match the installed loader.')


def verify_boot(target):
    # Use only the selected disk's ESP, never the host ESP.
    run(['udevadm', 'settle', '--timeout=10'])
    nodes, root_id = snapshot()
    disk = validate(nodes, root_id, target, 'system', os.environ.get('STEAMOS_TARGET_ID'))
    esp = target + ('p' if target[-1].isdigit() else '') + '1'
    with tempfile.TemporaryDirectory(prefix='steamos-boot-check-') as temp:
        subprocess.run(['mount', '-o', 'ro', esp, temp], check=True, timeout=15)
        try:
            check_boot_files(Path(temp), Path('/usr/lib/steamos-efi/x86_64-efi/steamcl.efi'))
            print(f"UEFI boot files verified on {disk['name']}.")
        finally:
            subprocess.run(['umount', temp], check=True, timeout=15)


def hardware_check(root=Path('/sys/bus/pci/devices')):
    """Check every NVIDIA display adapter against the running image driver offline."""
    devices = {}
    for device in root.iterdir():
        if ((device / 'vendor').read_text().strip() == '0x10de'
                and (device / 'class').read_text().startswith('0x03')):
            devices[device.name.lower()] = device
    if not devices:
        raise InvalidTarget('No NVIDIA graphics card was detected. Use this installer on a supported NVIDIA PC.')
    for address, device in devices.items():
        if (device / 'driver').resolve().name != 'nvidia':
            raise InvalidTarget(f'NVIDIA GPU {address} is not using the NVIDIA driver in this image. '
                                'GTX 10xx and older are not supported. For a newer card, check the driver and boot logs before installing.')
    try:
        output = run(['/usr/bin/nvidia-smi', '--query-gpu=pci.bus_id,name,driver_version',
                      '--format=csv,noheader,nounits'])
        package = run(['/usr/bin/pacman', '-Q', 'nvidia-utils']).split()
    except (OSError, subprocess.SubprocessError) as error:
        raise InvalidTarget('The NVIDIA driver could not be checked. Check nvidia-smi and the boot logs before installing.') from error
    if len(package) != 2 or package[0] != 'nvidia-utils':
        raise InvalidTarget('Cannot identify the NVIDIA driver package in this image.')
    expected = package[1].rsplit('-', 1)[0]
    found = {}
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if len(row) != 3:
            raise InvalidTarget('Unexpected NVIDIA driver response. Installation cannot continue.')
        bus, name, version = [value.strip() for value in row]
        match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', bus)
        if not match:
            raise InvalidTarget('Cannot match the NVIDIA driver to the detected graphics card.')
        domain, slot, dev, function = match.groups()
        address = f'{int(domain, 16):04x}:{slot.lower()}:{dev.lower()}.{function}'
        if address in found or version != expected:
            raise InvalidTarget('The running NVIDIA driver does not match the image package. Restart the installer and check again.')
        if not re.search(r'GeForce (?:GTX 16|RTX [2-9]0)[0-9]{2}(?:\b|\s)', name):
            raise InvalidTarget(f'{name} is outside this installer’s supported GeForce GTX 16xx / RTX range.')
        found[address] = name
    if set(found) != set(devices):
        raise InvalidTarget('Not every NVIDIA graphics card is available through the image driver. Check the boot logs before installing.')
    return 'GPU: ' + ', '.join(found.values()) + f'\nNVIDIA driver: {expected} (running and matched to this image)'


def select(mode):
    hardware = hardware_check()
    nodes, root_id = snapshot()
    disks = candidates(nodes, root_id)
    if not disks:
        raise InvalidTarget('No available target disk. Connect an internal or USB disk with enough space and 512-byte logical sectors. A disk that already holds a system can be used; its partitions are unmounted for you.')
    title = 'Install SteamOS to disk' if mode == 'all' else 'Reinstall SteamOS, keep home'
    message = ('Select an internal or external USB disk. All data on the selected disk will be erased.'
               if mode == 'all' else 'Select an existing SteamOS disk. The OS will be replaced; games and data in /home will be kept.')
    args = ['zenity', '--list', '--radiolist', '--title', title, '--text', html.escape(hardware + '\n\n' + message),
            '--column', '', '--column', 'Disk', '--column', 'Size / Model / Connection',
            '--width', '700', '--height', '340']
    for disk in disks:
        args.extend(['FALSE', disk['name'], description(disk)])
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode != 0:
        return 0
    target = result.stdout.strip()
    chosen = next((d for d in disks if d['name'] == target), None)
    if chosen is None:
        raise InvalidTarget('No valid disk selected.')
    token = identity(chosen)
    # Unmounting is not destructive and nothing is written yet, so it happens before
    # the confirmation. validate() then refuses anything that is still held.
    release_disk(chosen)
    disk = guard(target, mode, token)
    text = f"{target}\n{description(disk)}\n\n"
    text += ('All data on this disk will be permanently erased.' if mode == 'all'
             else 'OS partitions will be replaced. Games and data in /home will be preserved.')
    if disk.get('tran') == 'usb':
        text += '\n\nKeep this external disk connected during installation and use. After shutdown, remove only the installer USB, then select this disk in the UEFI boot menu.'
    else:
        text += '\n\nAfter shutdown, remove the installer USB and boot this disk.'
    result = subprocess.run(['zenity', '--question', '--no-wrap', '--title', 'Final confirmation',
                             '--ok-label', 'ERASE AND INSTALL' if mode == 'all' else 'REINSTALL',
                             '--cancel-label', 'Cancel', '--text', html.escape(text)])
    if result.returncode != 0:
        return 0
    # The repair tool repeats validation as root immediately before any writes.
    result = subprocess.call(['sudo', '-n', '/usr/lib/steamos-nvidia/install-authorized',
                              mode, target, token])
    if result != 0:
        raise InvalidTarget('The installation did not complete. Check the terminal output before trying again.')
    return finish_install()


def finish_install():
    result = subprocess.run(['zenity', '--question', '--no-wrap',
                             '--title', 'Installation complete', '--ok-label', 'Proceed',
                             '--cancel-label', 'Cancel', '--text',
                             'SteamOS was installed successfully.\n\nChoose Proceed to shut down, or Cancel to stay in the installer desktop.\nAfter shutdown, remove only the installer USB.'])
    if result.returncode == 0:
        subprocess.run(['systemctl', 'poweroff'], check=True)
    elif result.returncode != 1:
        raise InvalidTarget('The completion dialog could not be displayed. Installation is complete; shut down from the desktop when ready.')
    return 0



def execute_install(mode, target, token):
    if os.geteuid() != 0:
        raise InvalidTarget('Installation requires the authorized system helper.')
    if mode not in ('all', 'system') or not token:
        raise InvalidTarget('Invalid installation request.')
    env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root',
           'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'STEAMOS_TARGET_DISK': target, 'STEAMOS_TARGET_ID': token,
           'NOPROMPT': '1'}
    # The disk probe also runs with a trusted PATH, before any privileged write.
    os.environ.clear()
    os.environ.update(env)
    guard(target, mode, token)
    hardware_check()
    return subprocess.call(['/usr/bin/bash', '/usr/lib/steamos-nvidia/installer/repair_device.sh', mode],
                           env=env, cwd='/')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['all', 'system'])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--execute', nargs=2, metavar=('DISK', 'IDENTITY'))
    group.add_argument('--guard', metavar='DISK')
    group.add_argument('--verify-boot', metavar='DISK')
    args = parser.parse_args()
    try:
        if args.execute:
            return execute_install(args.mode, *args.execute)
        if args.verify_boot:
            verify_boot(args.verify_boot)
            return 0
        if args.guard:
            guard(args.guard, args.mode, os.environ.get('STEAMOS_TARGET_ID'))
            return 0
        return select(args.mode)
    except (InvalidTarget, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        message = f'Installation stopped: {error}'
        print(message, file=sys.stderr)
        if not args.guard and not args.execute:
            subprocess.run(['zenity', '--error', '--no-wrap', '--text', html.escape(message)])
        return 1


if __name__ == '__main__':
    sys.exit(main())
