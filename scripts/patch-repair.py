#!/usr/bin/python3
"""Add preflight and boot checks to the supported Valve recovery script."""
from pathlib import Path
import re
import sys


def patch(text):
    sizes = {'ESP': 256, 'EFI': 64, 'ROOT': 5120, 'VAR': 256, 'HOME': 100}
    for name, size in sizes.items():
        if not re.search(r'^PART_SIZE_' + name + r'="' + str(size) + r'"(?:\s|$)', text, re.M):
            raise ValueError('Unsupported Valve partition sizes')
    for number, name in enumerate(['ESP', 'EFI_A', 'EFI_B', 'ROOT_A', 'ROOT_B', 'VAR_A', 'VAR_B', 'HOME'], 1):
        if not re.search(r'^FS_' + name + '=' + str(number) + '$', text, re.M):
            raise ValueError('Unsupported Valve partition numbering')
    loader = 'cmd steamos-chroot --no-overlay --disk "$DISK" --partset A -- steamcl-install --flags restricted --force-extra-removable'
    if text.count(loader) != 1 or 'install-target.py' in text:
        raise ValueError('Unexpected bootloader command or already patched installer')
    for mode, first_write in [('all', '  writePartitionTable=1'), ('system', '  writeOS=1')]:
        start = text.index('\n' + mode + ')\n')
        end = text.index('\n  ;;', start)
        block = text[start:end]
        if block.count(first_write) != 1 or block.count('  repair_steps\n') != 1:
            raise ValueError('Unexpected repair entry point')
        guard = ('  /usr/bin/python3 /usr/lib/steamos-nvidia/install-target.py ' + mode +
                 ' --guard "${STEAMOS_TARGET_DISK:?Select the installation disk first}" || exit 1\n')
        block = block.replace(first_write, guard + first_write, 1)
        block = block.replace('  repair_steps\n', '  repair_steps\n' +
                              '  /usr/bin/python3 /usr/lib/steamos-nvidia/install-target.py ' + mode +
                              ' --verify-boot "$DISK" || exit 1\n', 1)
        # The unprivileged frontend owns the final shutdown confirmation.
        block, count = re.subn(r'^  prompt_reboot [^\n]+$',
                               '  echo "Installation and boot verification complete."', block, flags=re.M)
        if count != 1:
            raise ValueError('Unexpected completion entry point')
        text = text[:start] + block + text[end:]
    return text


if __name__ == '__main__':
    path = Path(sys.argv[1])
    path.write_text(patch(path.read_text()), encoding='utf-8')
