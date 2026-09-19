#!/usr/bin/python3 -I
"""Prepare a pinned NVIDIA driver through Valve's inactive-slot updater."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

BASE = Path('/usr/lib/steamos-nvidia')
REQUEST = Path('/run/steamos-nvidia-driver')
STATE = Path('/home/.steamos-nvidia-driver')
MANIFEST = Path('/etc/steamos-atomupd/manifest.json')
CORE = ('nvidia-utils', 'nvidia-open-dkms', 'lib32-nvidia-utils')
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+)+-[0-9]+(?:\.[0-9]+)*')
FIELDS = ('product', 'release', 'variant', 'arch', 'version', 'buildid')


def run(*args, capture=True):
    result = subprocess.run(args, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ''


def read_config(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z_][A-Z_0-9]*', key):
            raise ValueError('Invalid driver configuration')
        tokens = shlex.split(value)
        if len(tokens) > 1:
            raise ValueError('Invalid driver configuration value')
        result[key] = tokens[0] if tokens else ''
    return result


def manifest(path=MANIFEST):
    value = json.loads(path.read_text())
    result = {key: value[key] for key in FIELDS}
    if not all(isinstance(v, str) and v for v in result.values()):
        raise ValueError('Incomplete SteamOS manifest')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', result['buildid']):
        raise ValueError('Unsupported SteamOS build ID')
    return result


def resolve(version, current):
    if not VERSION.fullmatch(version):
        raise ValueError('Use a full package version, for example 610.57.04-1')
    pkgver = version.rsplit('-', 1)[0]
    urls = []
    for pkg in CORE:
        base = f'https://archive.archlinux.org/packages/{pkg[0]}/{pkg}/'
        index = run('curl', '--fail', '--silent', '--show-error', '--location',
                    '--connect-timeout', '15', '--max-time', '90', '--retry', '2', base)
        versions = re.findall(re.escape(pkg) + r'-([0-9.]+-[0-9.]+)-x86_64\.pkg\.tar\.zst', index)
        candidates = [v for v in versions if v.rsplit('-', 1)[0] == pkgver]
        if pkg == 'nvidia-utils':
            candidates = [v for v in candidates if v == version]
        if not candidates:
            raise ValueError(f'No archived {pkg} package for {version}')
        selected = max(candidates, key=lambda v: tuple(int(n) for n in v.rsplit('-', 1)[1].split('.')))
        urls.append(f'{base}{pkg}-{selected}-x86_64.pkg.tar.zst')
    # Keep the already pinned support libraries. Pacman rejects unmet new dependencies.
    for url in current['PKG_URLS'].split():
        if any(f'/{pkg}/' in url for pkg in CORE):
            continue
        if not re.fullmatch(r'https://archive\.archlinux\.org/packages/[a-z0-9]/[a-z0-9-]+/[a-zA-Z0-9.+_-]+\.pkg\.tar\.zst', url):
            raise ValueError('Support package must have a permanent Arch archive URL')
        urls.append(url)
    config = dict(current, DRIVER_SPEC=version, DRIVER_VERSION=version, PKG_URLS=' '.join(urls))
    allowed = {'DRIVER_SPEC', 'DRIVER_VERSION', 'PKG_URLS', 'ADD_XPADNEO',
               'XPADNEO_VERSION', 'XPADNEO_SHA256', 'TRIM_CUDA'}
    if set(config) != allowed:
        raise ValueError('Unexpected or missing driver configuration fields')
    return ''.join(f'{k}={shlex.quote(v)}\n' for k, v in config.items())


def private_dir(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError(f'Unsafe transaction directory: {path}')


def private_read(path):
    private_dir(path.parent)
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError(f'Unsafe transaction file: {path}')
    return path.read_text()


def write_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.transaction-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def integration_bytes():
    path = BASE / 'integration-version.json'
    return path.read_bytes() if path.exists() else b''


def identity():
    return hashlib.sha256((BASE / 'driver.conf').read_bytes() + MANIFEST.read_bytes() + integration_bytes()).hexdigest()


def boot_slot():
    slot = run('steamos-bootconf', 'this-image')
    if slot not in ('A', 'B'):
        raise ValueError('Only installed A/B systems are supported')
    return slot


def protect(slot):
    run('steamos-bootconf', '--image', slot, 'config', '--no-create',
        '--set', 'image-invalid', '1', '--set', 'boot-requested-at', '0',
        '--set', 'boot-attempts', '0')
    run('sync', '-f', '/esp/SteamOS/conf')


def request_config():
    if not REQUEST.exists():
        return str(BASE / 'driver.conf')
    request = json.loads(private_read(REQUEST / 'request.json'))
    config = private_read(REQUEST / 'driver.conf')
    if request['source'] != boot_slot() or request['identity'] != identity():
        raise ValueError('Driver request no longer matches the running system')
    if hashlib.sha256(config.encode()).hexdigest() != request['config_sha256']:
        raise ValueError('Driver request checksum mismatch')
    return str(REQUEST / 'driver.conf')


def check_target(root):
    if not REQUEST.exists():
        return
    request_config()
    request = json.loads(private_read(REQUEST / 'request.json'))
    if manifest(Path(root) / 'etc/steamos-atomupd/manifest.json') != request['manifest']:
        raise ValueError('Updater supplied a different SteamOS build; refusing driver change')
    write_json(REQUEST / 'started.json', {'started': True})


def mark_ready():
    if REQUEST.exists():
        request_config()
        write_json(REQUEST / 'ready.json', {'ready': True})


def install(version, integration=None):
    if REQUEST.exists():
        raise ValueError('A driver request already exists; reboot before retrying')
    source = boot_slot()
    target = 'B' if source == 'A' else 'A'
    if run('steamos-bootconf', 'selected-image') != source:
        raise ValueError('Another slot is already selected; reboot before changing drivers')
    if run('atomupd-manager', 'get-update-status') in ('in-progress', 'paused', 'successful'):
        raise ValueError('Finish the pending SteamOS update and reboot first')
    if '# steamos-nvidia shared-update-hook v1' not in Path('/usr/lib/rauc/post-install.sh').read_text():
        raise ValueError('Shared NVIDIA update hook is required')
    if shutil.disk_usage('/home').free < 12 * 1024**3:
        raise ValueError('At least 12 GiB free on /home is required')
    build = manifest()
    config = (BASE / 'driver.conf').read_text() if integration else resolve(version, read_config(BASE / 'driver.conf'))
    private_dir(STATE)
    previous = json.loads(private_read(STATE / 'last.json')) if (STATE / 'last.json').exists() else None
    private_dir(REQUEST)
    (REQUEST / 'driver.conf').write_text(config)
    os.chmod(REQUEST / 'driver.conf', 0o600)
    transaction = {'source': source, 'target': target, 'identity': identity(),
                   'manifest': build, 'version': version,
                   'config_sha256': hashlib.sha256(config.encode()).hexdigest(), 'status': 'preparing',
                   'target_integration_sha256': hashlib.sha256(integration_bytes()).hexdigest()}
    if integration:
        folder = REQUEST / 'integration'
        folder.mkdir(mode=0o700)
        for name in ('installer-manifest.json', 'installer-manifest.sig', 'installer-bundle.tar'):
            shutil.copyfile(Path(integration['folder']) / name, folder / name)
            os.chmod(folder / name, 0o600)
        transaction['integration'] = {k: integration[k] for k in ('version', 'manifest_sha256')}
        data = json.dumps(transaction['integration'], indent=2) + '\n'
        transaction['target_integration_sha256'] = hashlib.sha256(data.encode()).hexdigest()
    write_json(REQUEST / 'request.json', transaction)
    write_json(STATE / 'last.json', transaction)
    try:
        # Clone the immutable root with a fresh filesystem UUID, then use Valve's migration.
        run('unshare', '--mount', '--propagation', 'slave', '/usr/lib/steamos-nvidia/driver-stage.sh', capture=False)
        if not (REQUEST / 'ready.json').is_file():
            raise ValueError('Updater did not complete the requested NVIDIA repair')
        if run('steamos-bootconf', 'selected-image') != target:
            raise ValueError('Prepared driver slot was not selected')
        transaction['status'] = 'ready'
        write_json(STATE / 'last.json', transaction)
        print('System prepared. Reboot to test it. The previous slot is retained.')
    except BaseException:
        if (REQUEST / 'started.json').exists():
            protect(target)
        transaction['status'] = 'failed'
        write_json(STATE / 'last.json', transaction)
        if not (REQUEST / 'started.json').exists() and previous and previous.get('status') == 'ready':
            write_json(STATE / 'last.json', previous)
        raise
    finally:
        # Only our fixed, root-owned request directory is removed.
        shutil.rmtree(REQUEST)


def rollback():
    state = json.loads(private_read(STATE / 'last.json'))
    current = boot_slot()
    if state.get('source') not in ('A', 'B') or state.get('target') not in ('A', 'B') or state['source'] == state['target']:
        raise ValueError('Invalid saved slot identifiers')
    if state['status'] != 'ready' or current not in (state['source'], state['target']):
        raise ValueError('No completed driver change is available to roll back')
    source = state['source']
    if current == source:
        if identity() != state['identity']:
            raise ValueError('Previous system changed; refusing stale rollback')
        candidate = json.loads(run('steamos-chroot', '--partset', state['target'], '--',
            '/usr/bin/python3', '-I', '-c',
            "import hashlib,json; from pathlib import Path; print(json.dumps({'sha':hashlib.sha256(Path('/usr/lib/steamos-nvidia/driver.conf').read_bytes()).hexdigest(),'manifest':json.loads(Path('/etc/steamos-atomupd/manifest.json').read_text()),'integration':hashlib.sha256(Path('/usr/lib/steamos-nvidia/integration-version.json').read_bytes() if Path('/usr/lib/steamos-nvidia/integration-version.json').exists() else b'').hexdigest()}))"))
        if candidate.get('integration', hashlib.sha256(b'').hexdigest()) != state.get('target_integration_sha256', hashlib.sha256(b'').hexdigest()):
            raise ValueError('Prepared integrations changed; refusing stale rollback')
        if candidate['sha'] != state['config_sha256'] or any(candidate['manifest'].get(k) != v for k, v in state['manifest'].items()):
            raise ValueError('Prepared slot was replaced; refusing to cancel another update')
        protect(state['target'])
    else:
        if hashlib.sha256(integration_bytes()).hexdigest() != state.get('target_integration_sha256', hashlib.sha256(b'').hexdigest()):
            raise ValueError('Current integrations changed after the transaction')
        if manifest() != state['manifest'] or hashlib.sha256((BASE / 'driver.conf').read_bytes()).hexdigest() != state['config_sha256']:
            raise ValueError('Current system changed after the driver transaction')
        previous = run('steamos-chroot', '--partset', source, '--',
                       '/usr/bin/python3', '-I', '-c',
                       "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('/usr/lib/steamos-nvidia/driver.conf').read_bytes()+Path('/etc/steamos-atomupd/manifest.json').read_bytes()+(Path('/usr/lib/steamos-nvidia/integration-version.json').read_bytes() if Path('/usr/lib/steamos-nvidia/integration-version.json').exists() else b'')).hexdigest())")
        if previous != state['identity']:
            raise ValueError('Previous slot was replaced; refusing stale rollback')
        run('steamos-chroot', '--partset', source, '--', '/bin/bash', '-c',
            'set -e; source /usr/lib/steamos-nvidia/pc-support.sh; source /usr/lib/steamos-nvidia/driver.conf; '
            'for k in /usr/lib/modules/*neptune*; do [[ -d $k ]] || exit 1; '
            'pc_check_driver / "${k##*/}" "$DRIVER_VERSION"; done; pc_check_addons /')
        run('steamos-bootconf', '--image', source, 'set-mode', 'reboot')
        run('sync', '-f', '/esp/SteamOS/conf')
    state['status'] = 'rollback-selected'
    write_json(STATE / 'last.json', state)
    print('Previous system selected. Reboot to return to it.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'install'):
        sub.add_parser(name).add_argument('version')
    for name in ('rollback', 'status', 'request-config', 'mark-ready'):
        sub.add_parser(name)
    sub.add_parser('check-target').add_argument('root')
    args = parser.parse_args()
    if args.command == 'plan':
        print(resolve(args.version, read_config(BASE / 'driver.conf')), end='')
        return
    if os.geteuid() != 0:
        raise ValueError('Run this command with sudo')
    if args.command == 'request-config':
        print(request_config())
        return
    if args.command == 'check-target':
        check_target(args.root)
        return
    if args.command == 'mark-ready':
        mark_ready()
        return
    if args.command == 'status':
        print(private_read(STATE / 'last.json') if (STATE / 'last.json').exists() else 'No driver change recorded.')
        return
    with open('/run/steamos-nvidia-update.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'install':
            install(args.version)
        else:
            rollback()


if __name__ == '__main__':
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['LC_ALL'] = 'C'
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Driver change failed: {error}', file=sys.stderr)
        sys.exit(1)
