#!/usr/bin/python3
"""Select Steam's embedded notification renderer for one verified client asset."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import signal
import time
import tempfile

ASSET = 'chunk~2dcc5aaf7.js'
ORIGINAL_SHA256 = 'f9606b111203c9140dcdd6fc016a0ee00e4c8406aeb786187c42873f08d071fb'
BEFORE = b'l=s&&a,c=(0,ee.wv)();return o?.IsVRGamepadUIOverlayWindow()'
LEGACY_AFTER = b'l=s&&a,c=!0;return o?.IsVRGamepadUIOverlayWindow()'
# Keep the asset size unchanged; ordinary Steam startup checks file sizes.
# Full client verification or updates may still restore the original asset.
AFTER = b'l=s&&a,c=!0         ;return o?.IsVRGamepadUIOverlayWindow()'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Expected a regular file owned by the current user')
        return stream.read(), stat.S_IMODE(info.st_mode)


def replace(path, data, mode):
    fd, name = tempfile.mkstemp(prefix='.notification-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def update(steam, state, action):
    """Never infer compatibility from a filename or a matching snippet alone."""
    target = steam / 'steamui' / ASSET
    if not target.exists():
        return 'unsupported: verified Steam asset is absent'
    data, mode = read_regular(target)
    profiles = [
        (ORIGINAL_SHA256, BEFORE, AFTER, LEGACY_AFTER),
        ('1da08ac81e2ad5c56e868bc7b18cd940d0e990a142fa26bc2445dd9ec7813c98',
         b've=Tt&&Lt,ht=(0,Ce.wv)();return Wt?.IsVRGamepadUIOverlayWindow()',
         b've=Tt&&Lt,ht=!0         ;return Wt?.IsVRGamepadUIOverlayWindow()', None),
    ]
    for expected, before, after, old_after in profiles:
        original = digest(data) == expected
        restored = data.replace(after, before) if data.count(after) == 1 else b''
        patched = bool(restored) and digest(restored) == expected
        legacy_original = (data.replace(old_after, before)
                           if old_after and data.count(old_after) == 1 else b'')
        legacy = bool(legacy_original) and digest(legacy_original) == expected
        patched = patched or legacy
        if original or patched:
            break
    else:
        return 'unsupported: Steam asset changed; no files modified'
    if action == 'status':
        return ('legacy size-changing patch on disk' if legacy else
                'embedded renderer active on disk' if patched else 'original renderer on disk')
    backup = state / (expected + '.original')
    if action == 'restore':
        if original:
            return 'already original'
        saved, _ = read_regular(backup)
        if digest(saved) != expected:
            raise ValueError('Backup verification failed; no files modified')
        result = saved
    else:
        if patched and not legacy:
            return 'already patched'
        source = legacy_original if legacy else data
        if source.count(before) != 1 or after in source:
            raise ValueError('Unexpected renderer selection')
        if backup.exists() or backup.is_symlink():
            saved, _ = read_regular(backup)
            if saved != source:
                raise ValueError('Backup verification failed; no files modified')
        else:
            replace(backup, source, 0o600)
        result = source.replace(before, after)
        if len(result) != len(source):
            raise ValueError('Patch must preserve asset size')
    # Avoid replacing a client update that arrived while preparing the backup.
    if read_regular(target)[0] != data:
        raise ValueError('Steam asset changed during operation; retry at next start')
    replace(target, result, mode)
    return 'restored original renderer' if action == 'restore' else 'selected embedded renderer'



def browser_processes(proc=Path('/proc')):
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = (entry / 'cmdline').read_bytes().split(b'\0')
            if (argv and argv[0].split(b'/')[-1] == b'steamwebhelper'
                    and b'-nocrashdialog' in argv
                    and not any(x.startswith(b'--type=') for x in argv)):
                found.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return found


def startup(steam, state):
    # Steam's integrity check runs after ExecStartPre and restores modified UI.
    # Wait for its first browser, apply once, then reload that browser only.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if (state / 'disabled').exists():
            return 'Notification workaround disabled by user'
        pids = browser_processes()
        if len(pids) == 1:
            pid = pids[0]
            fd = os.pidfd_open(pid)
            try:
                time.sleep(2)
                if browser_processes() != [pid]:
                    continue
                with (state / 'lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    if (state / 'disabled').exists():
                        return 'Notification workaround disabled by user'
                    result = update(steam, state, 'apply')
                    if result == 'selected embedded renderer':
                        signal.pidfd_send_signal(fd, signal.SIGTERM)
                        return result + '; reloaded Steam browser once'
                    return result
            finally:
                os.close(fd)
        time.sleep(1)
    return 'Notification workaround skipped: Steam browser did not start in 120 seconds'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['apply', 'status', 'disable', 'enable', 'startup'], default='status', nargs='?')
    args = parser.parse_args()
    if os.getuid() == 0:
        parser.error('Run as the Steam user, without sudo')
    state = Path.home() / '.local/state/steamos-nvidia/notifications'
    if args.action == 'status':
        print(update(Path.home() / '.local/share/Steam', state, 'status'))
        if (state / 'disabled').exists():
            print('Automatic application disabled by user')
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.action == 'startup':
        print(startup(Path.home() / '.local/share/Steam', state))
        return
    disabled = state / 'disabled'
    with (state / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == 'disable':
            disabled.touch(mode=0o600)
        elif args.action == 'enable':
            disabled.unlink(missing_ok=True)
        action = {'disable': 'restore', 'enable': 'apply'}.get(args.action, args.action)
        if action == 'apply' and disabled.exists():
            print('Notification workaround disabled by user')
            return
        try:
            print(update(Path.home() / '.local/share/Steam', state, action))
        except (OSError, ValueError) as error:
            print('Notification workaround skipped: ' + str(error))
            raise SystemExit(1)


if __name__ == '__main__':
    main()
