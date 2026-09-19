#!/usr/bin/python3
"""Seed Steam's per-display HDR preferences before its first read of config.vdf."""
import fcntl
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

TOKEN = re.compile(r'\s+|//[^\n]*|"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')


def tokens(text):
    result = []
    position = 0
    for match in TOKEN.finditer(text):
        if match.start() != position:
            raise ValueError('Unrecognized VDF syntax')
        position = match.end()
        raw = match.group()
        if raw.isspace() or raw.startswith('//'):
            continue
        if raw.startswith('"'):
            value = re.sub(r'\\(["\\])', r'\1', raw[1:-1])
        else:
            value = raw
        result.append((value, match.start(), match.end(), raw))
    if position != len(text):
        raise ValueError('Incomplete VDF string')
    return result


def parse(text):
    stream = tokens(text)
    index = 0

    def block(nested=False):
        nonlocal index
        entries = []
        while index < len(stream):
            key = stream[index]
            if key[3] == '}':
                if not nested:
                    raise ValueError('Unexpected closing brace')
                index += 1
                return entries, key[1]
            if key[3] == '{':
                raise ValueError('Expected a key')
            index += 1
            if index >= len(stream):
                raise ValueError('Missing value')
            value = stream[index]
            index += 1
            if value[3] == '{':
                children, end = block(True)
                entries.append((key[0], children, end))
            elif value[3] == '}':
                raise ValueError('Missing value before closing brace')
            else:
                entries.append((key[0], value[0], value[1]))
        if nested:
            raise ValueError('Unclosed block')
        return entries, len(text)

    return block()[0]


def unique(entries, name):
    found = [entry for entry in entries if entry[0].casefold() == name.casefold()]
    if len(found) > 1:
        raise ValueError('Duplicate VDF key: ' + name)
    return found[0] if found else None


def profile_id(text):
    """Steam's per-display profile: MurmurHash3 x86_32, seed 0x417."""
    data = text.encode('utf-8')
    mask = 0xffffffff
    value = 0x417

    def rotate(number, bits):
        return ((number << bits) | (number >> (32 - bits))) & mask

    def mix(number):
        return (rotate((number * 0xcc9e2d51) & mask, 15) * 0x1b873593) & mask

    end = len(data) // 4 * 4
    for offset in range(0, end, 4):
        value ^= mix(int.from_bytes(data[offset:offset + 4], 'little'))
        value = (rotate(value, 13) * 5 + 0xe6546b64) & mask
    if end < len(data):
        value ^= mix(int.from_bytes(data[end:], 'little'))
    value ^= len(data)
    value ^= value >> 16
    value = value * 0x85ebca6b & mask
    value ^= value >> 13
    value = value * 0xc2b2ae35 & mask
    return str(value ^ (value >> 16))


def display_identity(edid, vendors):
    """Match Gamescope's EDID manufacturer lookup and product-name descriptor."""
    if len(edid) < 128 or edid[:8] != bytes.fromhex('00ffffffffffff00'):
        raise ValueError('Missing or invalid EDID base block')
    if sum(edid[:128]) & 255:
        raise ValueError('Invalid EDID checksum')
    vendor = int.from_bytes(edid[8:10], 'big')
    code = ''.join(chr(((vendor >> shift) & 31) + 64) for shift in (10, 5, 0))
    if not re.fullmatch('[A-Z]{3}', code):
        raise ValueError('Invalid EDID manufacturer')
    model = ''
    for offset in range(54, 126, 18):
        if edid[offset:offset + 5] == bytes([0, 0, 0, 252, 0]):
            model = edid[offset + 5:offset + 18].split(b'\n', 1)[0].rstrip(b' ').decode('ascii')
    return vendors.get(code, code) + '-' + model


def discover_profiles(drm=Path('/sys/class/drm'), pnp=Path('/usr/share/hwdata/pnp.ids')):
    vendors = {}
    if pnp.exists():
        for line in pnp.read_text(encoding='utf-8').splitlines():
            if '\t' in line:
                code, name = line.split('\t', 1)
                vendors[code] = name
    # Steam uses this profile until Gamescope announces the actual connector.
    profiles = {profile_id('unknown'): 'unknown'}
    for status in sorted(drm.glob('card*-*/status')):
        if status.read_text().strip() != 'connected':
            continue
        identity = display_identity(status.with_name('edid').read_bytes(), vendors)
        profiles[profile_id(identity)] = identity
    if len(profiles) == 1:
        raise ValueError('No connected display with readable EDID; refusing to guess its HDR profile')
    return profiles


def seed_text(text, profiles):
    identifiers = sorted(set(profiles))
    if not identifiers or any(not re.fullmatch(r'[0-9]{1,10}', key) or int(key) > 0xffffffff for key in identifiers):
        raise ValueError('Expected numeric Steam display profiles')
    entries = parse(text)
    names = ['InstallConfigStore', 'Gamescope', 'HDREnabled']

    def subtree(level):
        if level == len(names):
            return '\n'.join('\t' * level + '"' + key + '"\t\t"0"' for key in identifiers) + '\n'
        indent = '\t' * level
        return indent + '"' + names[level] + '"\n' + indent + '{\n' + subtree(level + 1) + indent + '}\n'

    parent_end = len(text)
    for level, name in enumerate(names):
        node = unique(entries, name)
        if node is None:
            if level == 0 and entries:
                raise ValueError('Missing InstallConfigStore root')
            return text[:parent_end] + '\n' + subtree(level) + text[parent_end:]
        if not isinstance(node[1], list):
            if name != 'HDREnabled' or node[1] != '0':
                raise ValueError(name + ' must be a block')
            # Migrate only the ineffective legacy scalar 0.
            start = node[2]
            end = start + tokens(text[start:])[0][2]
            return text[:start] + '{\n' + subtree(3) + '\t\t}' + text[end:]
        entries, parent_end = node[1], node[2]
    missing = []
    for key in identifiers:
        node = unique(entries, key)
        if node is None:
            missing.append(key)
        elif isinstance(node[1], list) or node[1] not in ('0', '1'):
            raise ValueError('Invalid saved HDR choice for display ' + key)
    if not missing:
        return text
    addition = '\n' + ''.join('\t\t\t"' + key + '"\t\t"0"\n' for key in missing) + '\t\t'
    return text[:parent_end] + addition + text[parent_end:]


def seed_file(path, profiles):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / '.nvidia-hdr-default.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = path.read_bytes() if path.exists() else b''
        updated = seed_text(original.decode('utf-8'), profiles).encode('utf-8')
        if updated == original:
            return False
        # Preserve the original once, including unrelated settings and profiles.
        backup = path.with_name(path.name + '.before-hdr-default')
        if path.exists() and not backup.exists():
            with backup.open('xb') as out:
                out.write(original)
            os.chmod(backup, 0o600)
        fd, temporary = tempfile.mkstemp(prefix='.hdr-default-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(updated)
                out.flush()
                os.fsync(out.fileno())
            if path.exists():
                shutil.copymode(path, temporary)
                if path.read_bytes() != original:
                    raise ValueError('Steam config changed during initialization')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True


def main():
    # steam-jupiter uses this path on the supported SteamOS recovery image.
    path = Path.home() / '.local/share/Steam/config/config.vdf'
    try:
        if sys.argv[1:] == ['--status']:
            if not path.exists():
                print('HDR preference: not initialized')
                return 0
            root = unique(parse(path.read_text(encoding='utf-8')), 'InstallConfigStore')
            gamescope = unique(root[1], 'Gamescope') if root and isinstance(root[1], list) else None
            setting = unique(gamescope[1], 'HDREnabled') if gamescope and isinstance(gamescope[1], list) else None
            if setting and isinstance(setting[1], list):
                print('Saved HDR profiles: ' + ', '.join(key + '=' + str(value) for key, value, _ in setting[1]))
            else:
                print('HDR preference: missing or obsolete scalar setting')
            for key, name in discover_profiles().items():
                print('Detected profile: ' + key + ' (' + name + ')')
            return 0
        if sys.argv[1:]:
            raise ValueError('Use hdr-defaults.py [--status]')
        profiles = discover_profiles()
        changed = seed_file(path, profiles)
        print('HDR defaults: off for missing display profiles' if changed else 'HDR defaults: existing display choices preserved')
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        # Do not silently enter Game Mode with an uninitialized HDR setting.
        print('Could not initialize Steam HDR preference: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
