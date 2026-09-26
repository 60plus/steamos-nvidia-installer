#!/usr/bin/python3 -I
"""Install signed project releases through the existing inactive-slot transaction."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit

BASE = Path('/usr/lib/steamos-nvidia')
CONFIG = BASE / 'installer-update-source.json'
VERSION_RE = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?')
MAX_BUNDLE = 32 * 1024 * 1024
# Only project-owned files. Packages cannot replace arbitrary OS files or trust settings.
OPTIONAL_FILES = {'mangoapp', 'mangoapp-build.json', 'MangoHud-LICENSE'}
FILES = {
    'mangoapp': ('usr/lib/steamos-nvidia/mangoapp', 0o755),
    'mangoapp-build.json': ('usr/lib/steamos-nvidia/mangoapp-build.json', 0o644),
    'MangoHud-LICENSE': ('usr/lib/steamos-nvidia/MangoHud-LICENSE', 0o644),
    'pc-support.sh': ('usr/lib/steamos-nvidia/pc-support.sh', 0o644),
    'repatch.sh': ('usr/lib/steamos-nvidia/repatch.sh', 0o755),
    'steamos-update': ('usr/bin/steamos-update', 0o755),
    'steamos-nvidia-diagnostics': ('usr/bin/steamos-nvidia-diagnostics', 0o755),
    **{name: ('usr/lib/steamos-nvidia/' + name, 0o755) for name in
       ('hdr-defaults.py', 'safe-graphics.py', 'bluetooth-resume.py', 'install-target.py',
        'driver-change.py', 'driver-stage.sh', 'driver-manager.py', 'installer-update.py', 'installer-update-ui.py')},
    **{name: ('usr/lib/steamos-nvidia/' + name, 0o644) for name in
       ('change-nvidia-driver.png', 'installer-update.png')},
}


def driver():
    spec = importlib.util.spec_from_file_location('driver_change', BASE / 'driver-change.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source():
    info = CONFIG.lstat()
    if CONFIG.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Update source must be root-owned and not writable by other users')
    cfg = json.loads(CONFIG.read_text())
    if set(cfg) != {'name', 'release_api', 'download_origin', 'allow_prerelease', 'public_key'}:
        raise ValueError('Invalid update source configuration')
    if not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9_./-]+/releases/', cfg['release_api']):
        raise ValueError('An HTTPS release API is required')
    if not isinstance(cfg['allow_prerelease'], bool) or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?', cfg['download_origin']):
        raise ValueError('Invalid release channel or download origin')
    if not cfg['public_key'].startswith('-----BEGIN PUBLIC KEY-----'):
        raise ValueError('This build has no release signing key configured')
    return cfg


def download(url, target, limit):
    if not url.startswith('https://'):
        raise ValueError('Only HTTPS downloads are allowed')
    subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location',
                    '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15',
                    '--max-time', '180', '--max-filesize', str(limit), '--output', str(target), url], check=True)
    if not 0 < target.stat().st_size <= limit:
        raise ValueError('Invalid release download size')


def verify_signature(manifest, signature, public_key, folder):
    key = folder / 'key.pem'
    key.write_text(public_key)
    subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(key),
                    '-rawin', '-in', str(manifest), '-sigfile', str(signature)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def validate_manifest(value):
    if set(value) != {'format', 'version', 'steamos', 'bundle_sha256', 'files', 'notes'} or value['format'] != 1:
        raise ValueError('Unsupported release format')
    if not isinstance(value['version'], str) or not VERSION_RE.fullmatch(value['version']):
        raise ValueError('Invalid installer version')
    if not isinstance(value['notes'], str) or len(value['notes']) > 12000:
        raise ValueError('Invalid release notes')
    if not isinstance(value['steamos'], list) or not value['steamos'] or not all(
            isinstance(v, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', v) for v in value['steamos']):
        raise ValueError('Release must declare supported SteamOS versions')
    if not isinstance(value['files'], dict):
        raise ValueError('Release files must be an object')
    names = set(value['files'])
    required = set(FILES) - OPTIONAL_FILES
    optional = names & OPTIONAL_FILES
    if names - set(FILES) or required - names or optional not in (set(), OPTIONAL_FILES):
        raise ValueError('Release has missing or unexpected files')
    for sha in [value['bundle_sha256'], *value['files'].values()]:
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('Invalid release checksum')
    return value


def check_steamos(version, tested, what):
    """The declared list records what was tested, not what is permitted.

    Refusing everything else turned a Valve point release into a silent block:
    every release so far declared 3.8.16, Valve shipped 3.8.28, and the published
    updater would have refused on current stable. Below the oldest tested release
    there is nothing to stand on, so that still refuses. At or above it the update
    proceeds and says plainly that this SteamOS was never tested, because the
    integration ends in pc_check_addons under set -e and fails loudly rather than
    degrading quietly if the release does not fit.
    """
    def parts(value):
        try:
            return tuple(int(number) for number in value.split('.'))
        except ValueError:
            return None
    if version in tested:
        return
    floor = min(tested, key=parts)
    running = parts(version)
    if running is not None and running < parts(floor):
        raise ValueError(what + ': SteamOS ' + version + ' is older than ' + floor +
                         ', the oldest release this integration was tested on')
    print('Warning: this integration was tested on SteamOS ' +
          ', '.join(sorted(tested, key=parts)) + ' and this system reports ' + version +
          '. Continuing. The integration checks every addon and fails if it does not fit.',
          file=sys.stderr)


def release_page(cfg, tag):
    """Where a person can read the full notes.

    Same rule as the asset URLs below: the repository path comes from the
    configured API and the origin from the configured web origin, so a release
    server that has been taken over cannot send the reader somewhere else.
    """
    path = urlsplit(cfg['release_api']).path.split('/repos/', 1)[-1].strip('/')
    return cfg['download_origin'] + '/' + path + '/tag/' + tag


def fetch(tag, folder, bundle=False):
    cfg = source()
    if tag != 'latest' and not re.fullmatch(r'v[0-9A-Za-z.-]+', tag):
        raise ValueError('Invalid release tag')
    api = cfg['release_api'] + ('latest' if tag == 'latest' else 'tags/' + tag)
    listing = tag == 'latest' and cfg['allow_prerelease']
    if listing:
        api = cfg['release_api'].rstrip('/') + '?per_page=20&limit=20'
    metadata = folder / 'release.json'
    download(api, metadata, 1024 * 1024)
    release = json.loads(metadata.read_text())
    if listing:
        release = next((item for item in release if not item.get('draft')), None)
        if release is None:
            raise ValueError('No published release found')
    if release.get('prerelease') and not cfg['allow_prerelease']:
        raise ValueError('This source does not allow prereleases')
    if release.get('draft'):
        raise ValueError('Draft releases cannot be installed')
    assets = {}
    for asset in release.get('assets', []):
        if asset['name'] in assets:
            raise ValueError('Duplicate release asset')
        parts = urlsplit(asset['browser_download_url'])
        if parts.scheme != 'https' or not parts.path.startswith('/') or parts.fragment:
            raise ValueError('Invalid asset URL')
        # Use the configured web origin, even if the server advertises an old host.
        assets[asset['name']] = cfg['download_origin'] + parts.path + ('?' + parts.query if parts.query else '')
    for name, limit in [('installer-manifest.json', 128 * 1024), ('installer-manifest.sig', 1024)]:
        download(assets[name], folder / name, limit)
    verify_signature(folder / 'installer-manifest.json', folder / 'installer-manifest.sig', cfg['public_key'], folder)
    value = validate_manifest(json.loads((folder / 'installer-manifest.json').read_text()))
    if release['tag_name'] != 'v' + value['version'] or (tag != 'latest' and tag != release['tag_name']):
        raise ValueError('Release tag and signed version differ')
    value = dict(value, tag=release['tag_name'], source=cfg['name'],
                 page=release_page(cfg, release['tag_name']),
                 manifest_sha256=digest((folder / 'installer-manifest.json').read_bytes()))
    if bundle:
        download(assets['installer-bundle.tar'], folder / 'installer-bundle.tar', MAX_BUNDLE)
    return value


def read_bundle(path, manifest):
    if path.stat().st_size > MAX_BUNDLE or digest(path.read_bytes()) != manifest['bundle_sha256']:
        raise ValueError('Bundle checksum mismatch')
    content = {}
    total = 0
    with tarfile.open(path, 'r:') as archive:
        for member in archive:
            if member.name not in manifest['files'] or member.name in content or not member.isfile():
                raise ValueError('Unsafe or unexpected archive member')
            total += member.size
            if member.size < 0 or total > MAX_BUNDLE:
                raise ValueError('Release payload too large')
            data = archive.extractfile(member).read()
            if digest(data) != manifest['files'][member.name]:
                raise ValueError('Release file checksum mismatch')
            content[member.name] = data
    if set(content) != set(manifest['files']):
        raise ValueError('Incomplete release bundle')
    return content


def installed():
    path = BASE / 'integration-version.json'
    if path.exists():
        return json.loads(path.read_text())['version']
    return 'unknown'


def verify_target_device(root, run):
    # Btrfs mount MAJ:MIN can be an anonymous device, so inspect its source.
    device = run('findmnt', '-n', '-o', 'SOURCE', '--mountpoint', str(root)).split('[', 1)[0]
    if not device.startswith('/dev/') or '\n' in device:
        raise ValueError('Target must be a mounted block filesystem')
    actual = os.stat(device)
    expected = os.stat('/dev/disk/by-partsets/other/rootfs')
    active = os.stat('/dev/disk/by-partsets/self/rootfs')
    if not all(stat.S_ISBLK(item.st_mode) for item in (actual, expected, active)) or actual.st_rdev != expected.st_rdev or actual.st_rdev == active.st_rdev:
        raise ValueError('Target is not the inactive root filesystem')


def apply_target(root):
    d = driver()
    request = json.loads(d.private_read(d.REQUEST / 'request.json'))
    item = request.get('integration')
    if not item:
        return
    d.request_config()
    root = Path(root).resolve()
    if root == Path('/') or d.manifest(root / 'etc/steamos-atomupd/manifest.json') != request['manifest']:
        raise ValueError('Refusing an active or unrelated target')
    # Prove the destination is the inactive root, not merely a matching directory.
    verify_target_device(root, d.run)
    folder = d.REQUEST / 'integration'
    raw = folder / 'installer-manifest.json'
    if digest(raw.read_bytes()) != item['manifest_sha256']:
        raise ValueError('Transaction manifest changed')
    verify_signature(raw, folder / 'installer-manifest.sig', source()['public_key'], folder)
    value = validate_manifest(json.loads(raw.read_text()))
    if value['version'] != item['version']:
        raise ValueError('Release is incompatible with this transaction')
    check_steamos(request['manifest']['version'], value['steamos'], 'Transaction')
    payload = read_bundle(folder / 'installer-bundle.tar', value)
    for name, data in payload.items():
        rel, mode = FILES[name]
        target = root / rel
        # Refuse symlinks in all destination components, including the final file.
        if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
            raise ValueError('Symlink in update destination')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    (root / 'usr/lib/steamos-nvidia/integration-version.json').write_text(json.dumps(
        {'version': value['version'], 'manifest_sha256': item['manifest_sha256']}, indent=2) + '\n')
    subprocess.run(['/bin/bash', '-c',
        'set -e; source "$1/usr/lib/steamos-nvidia/pc-support.sh"; '
        'pc_install_display_policy "$1"; pc_install_bluetooth_resume "$1"; '
        'pc_install_mangoapp "$1"; pc_install_gamescope "$1"; pc_install_remote_play "$1"; pc_install_nvenc "$1"; pc_install_update_policy "$1"; pc_install_driver_manager "$1"; '
        'pc_install_installer_permissions "$1"; pc_install_installer_update "$1"; pc_write_addon_manifest "$1"; pc_check_addons "$1"',
        'installer-update', str(root)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check'); check.add_argument('tag', nargs='?', default='latest')
    install = sub.add_parser('install'); install.add_argument('tag'); install.add_argument('manifest_sha256')
    sub.add_parser('rollback'); sub.add_parser('status'); sub.add_parser('request-mode')
    sub.add_parser('apply-target').add_argument('root')
    args = parser.parse_args()
    if args.command == 'check':
        with tempfile.TemporaryDirectory(prefix='installer-check-') as tmp:
            value = fetch(args.tag, Path(tmp))
            print(json.dumps(dict(value, installed=installed())))
        return
    if os.geteuid() != 0:
        raise ValueError('Run with sudo')
    if args.command == 'apply-target':
        apply_target(args.root); return
    d = driver()
    if args.command == 'request-mode':
        request = json.loads(d.private_read(d.REQUEST / 'request.json')) if d.REQUEST.exists() else {}
        print('integration' if request.get('integration') else 'none')
        return
    if args.command == 'status':
        print(d.private_read(d.STATE / 'last.json') if (d.STATE / 'last.json').exists() else 'No transaction recorded.')
        return
    with open('/run/steamos-nvidia-update.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'rollback':
            d.rollback(); return
        with tempfile.TemporaryDirectory(prefix='installer-update-') as tmp:
            folder = Path(tmp)
            value = fetch(args.tag, folder, bundle=True)
            if value['manifest_sha256'] != args.manifest_sha256:
                raise ValueError('Release changed since confirmation; check again')
            if value['version'] == installed():
                raise ValueError('This integration version is already installed')
            check_steamos(d.manifest()['version'], value['steamos'], 'Release')
            read_bundle(folder / 'installer-bundle.tar', value)
            d.install(d.read_config(BASE / 'driver.conf')['DRIVER_VERSION'], integration={
                'version': value['version'], 'manifest_sha256': value['manifest_sha256'], 'folder': str(folder)})


if __name__ == '__main__':
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['LC_ALL'] = 'C'
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f'Installer update failed: {error}', file=sys.stderr)
        sys.exit(1)
