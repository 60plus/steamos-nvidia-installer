#!/usr/bin/python3 -I
"""Desktop interface for project integration releases."""
import argparse
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BASE = Path('/usr/lib/steamos-nvidia')
TITLE = 'SteamOS NVIDIA Installer Update'
TOOL = '/usr/bin/steamos-nvidia-installer-update'


def dialog(kind, text, *options):
    command = ['zenity', kind, '--title', TITLE, '--width', '860', '--height', '440',
               '--text', html.escape(text), *options]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr.strip() or 'Could not open the update window')
    return result


def worker(action, tag, sha):
    args = [TOOL, action] + ([tag, sha] if action == 'install' else [])
    print('Updates installer tools and fixes. The selected NVIDIA driver is retained.', flush=True)
    print('Preparation may look paused while copying the system or compiling. Please wait and keep the PC on.', flush=True)
    result = subprocess.run(['sudo', 'systemd-run', '--unit=steamos-installer-update',
                             '--collect', '--wait', '--pipe', *args])
    if result.returncode:
        dialog('--error', 'The operation failed. Read this terminal output. The failed update must not be selected manually.')
        return result.returncode
    if dialog('--question', 'System prepared. Restart now to use it?', '--ok-label', 'Restart', '--cancel-label', 'Later').returncode == 0:
        subprocess.run(['systemctl', 'reboot'], check=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument('--shortcut', action='store_true')
    parser.add_argument('--run', choices=['install', 'rollback'])
    parser.add_argument('tag', nargs='?', default='')
    parser.add_argument('sha', nargs='?', default='')
    args = parser.parse_args()
    if args.shortcut:
        if 'VARIANT_ID=steamdeck-oobe' not in Path('/etc/os-release').read_text():
            folder = subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip()
            if folder and Path(folder).is_absolute() and Path(folder).is_dir():
                target = Path(folder) / (TITLE + '.desktop')
                if not target.exists():
                    shutil.copyfile('/usr/share/applications/steamos-installer-update.desktop', target)
                    target.chmod(0o755)
        return 0
    if args.run:
        return worker(args.run, args.tag, args.sha)
    if 'VARIANT_ID=steamdeck-oobe' in Path('/etc/os-release').read_text():
        raise ValueError('Open this tool in the installed system, not the installer USB')
    config = json.loads((BASE / 'installer-update-source.json').read_text())
    choice = dialog('--list', 'Updates installer tools and fixes without reinstalling SteamOS.\nSource: ' + config['name'],
                    '--column', 'Action', 'Check for updates', 'Return to previous system')
    if choice.returncode:
        return 0
    action, tag, sha = 'rollback', '', ''
    if choice.stdout.strip() == 'Check for updates':
        progress = subprocess.Popen(['zenity', '--progress', '--pulsate', '--no-cancel', '--auto-close',
                                     '--title', TITLE, '--text', 'Checking signed release information...', '--width', '600'],
                                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            result = subprocess.run([TOOL, 'check'], text=True, capture_output=True)
        finally:
            progress.terminate(); progress.wait()
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'Could not check for updates')
        release = json.loads(result.stdout)
        if release['installed'] == release['version']:
            dialog('--info', 'Installer tools are up to date: ' + release['version'])
            return 0
        text = ('Installed tools: ' + release['installed'] + '\nAvailable: ' + release['version'] +
                '\nSource: ' + release['source'] + '\n\n' + release['notes'] +
                '\n\nPreparation replaces the inactive OS slot and needs about 17 GiB free on /home. '
                'Finish pending OS updates and reboot first. The operation may look paused; please wait.')
        action, tag, sha = 'install', release['tag'], release['manifest_sha256']
    else:
        text = 'Return to the system before the last driver or installer update? Games and shared home data are not rolled back. The previous slot must still be available.'
    if dialog('--question', text, '--ok-label', 'Continue', '--cancel-label', 'Cancel').returncode:
        return 0
    return subprocess.call(['konsole', '--hold', '-e', '/usr/bin/python3', '-I',
                            str(BASE / 'installer-update-ui.py'), '--run', action, tag, sha])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        dialog('--error', str(error))
        sys.exit(1)
