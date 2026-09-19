#!/usr/bin/env python3
"""Preserve existing preloads while enabling the receiver-only environment helper."""
import os
from pathlib import Path
import tempfile

PRELOAD = '/usr/lib/steamos-nvidia/remote-play/$LIB/receiver-env.so'

def environment_line(existing, enabled=True):
    if any(c in existing for c in '\n\r\x00'):
        raise ValueError('Invalid preload environment')
    entries = existing.replace(':', ' ').split()
    entries = [entry for entry in entries if entry != PRELOAD]
    if enabled:
        entries.insert(0, PRELOAD)
    value = ' '.join(entries).replace('\\', '\\\\').replace('"', '\\"')
    return 'LD_PRELOAD="' + value + '"\n'

def main():
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    enabled = os.environ.get('STEAMOS_NVIDIA_REMOTE_PLAY') != '0'
    text = environment_line(os.environ.get('LD_PRELOAD', ''), enabled)
    fd, name = tempfile.mkstemp(prefix='.remote-play-', dir=runtime)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(name, runtime / 'steamos-nvidia-remote-play.env')
    finally:
        Path(name).unlink(missing_ok=True)

if __name__ == '__main__':
    main()
