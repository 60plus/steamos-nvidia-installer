#!/usr/bin/env python3
"""Build a signed installer integration release; never uploads or publishes it."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer_update', ROOT / 'scripts/installer-update.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def build(output, version, steamos, notes, key, mangoapp_dir=None):
    if not m.VERSION_RE.fullmatch(version):
        raise ValueError('Invalid release version')
    output.mkdir(parents=True, exist_ok=False)
    installer = (ROOT / 'steamos-nvidia-installer.sh').read_text()
    contents = {}
    for name in m.FILES:
        if name in m.OPTIONAL_FILES:
            if mangoapp_dir is not None:
                contents[name] = (mangoapp_dir / name).read_bytes()
            continue
        if name == 'repatch.sh':
            contents[name] = (installer.split("<<'REPATCH'\n", 1)[1].split('\nREPATCH\n', 1)[0] + '\n').encode()
        elif name == 'steamos-update':
            contents[name] = (installer.split("<<'WRAP'\n", 1)[1].split('\nWRAP\n', 1)[0] + '\n').encode()
        else:
            folder = 'lib' if name == 'pc-support.sh' else 'images' if name.endswith('.png') else 'scripts'
            contents[name] = (ROOT / folder / name).read_bytes()
    bundle = output / 'installer-bundle.tar'
    with tarfile.open(bundle, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(contents.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data); member.mode = m.FILES[name][1]
            archive.addfile(member, io.BytesIO(data))
    manifest = {'format': 1, 'version': version, 'steamos': steamos,
                'notes': notes, 'bundle_sha256': m.digest(bundle.read_bytes()),
                'files': {name: m.digest(data) for name, data in contents.items()}}
    m.validate_manifest(manifest)
    path = output / 'installer-manifest.json'
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(key), '-rawin',
                    '-in', str(path), '-out', str(output / 'installer-manifest.sig')], check=True)
    print('Release assets prepared:', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default=(ROOT / 'VERSION').read_text().strip())
    parser.add_argument('--steamos', action='append', required=True)
    parser.add_argument('--notes', type=Path, required=True)
    parser.add_argument('--mangoapp-dir', type=Path, help='Include a built overlay artifact and its provenance/license')
    parser.add_argument('--signing-key', type=Path, required=True)
    args = parser.parse_args()
    build(args.output, args.version, args.steamos, args.notes.read_text(), args.signing_key, args.mangoapp_dir)
