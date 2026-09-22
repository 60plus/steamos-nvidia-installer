import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / 'steamos-nvidia-installer.sh').read_text(encoding='utf-8')
# Heredoc bodies (the repair script, wrappers and files written into the image)
# run on SteamOS, not on the build host. Blank them and keep line numbers.
HEREDOC = re.compile(r"<<-?'?(\w+)'?\n.*?\n\1\n", re.S)
INSTALLER_HOST = HEREDOC.sub(
    lambda m: m.group(0).split('\n', 1)[0] + '\n' * m.group(0).count('\n'), INSTALLER)
BUILD_HOST_PART = INSTALLER.split("<<'REPATCH'\n", 1)[0]
PROBE = (ROOT / 'tools' / 'check-build-host.sh').read_text(encoding='utf-8')
HOST_SCRIPTS = {
    'steamos-nvidia-installer.sh host code': INSTALLER_HOST,
    **{f'tools/{path.name}': path.read_text(encoding='utf-8')
       for path in sorted((ROOT / 'tools').glob('*.sh'))},
}
# pacman or pacman-key used as a command word on the host. Chroot command
# strings (in_chroot "pacman ...") and messages are removed first; a quoted
# "$(pacman ...)" is host code and is kept.
PACMAN = re.compile(r'(?<![\w./-])(?:/\S*/)?pacman(?:-key)?(?![\w.-])')
COMMANDS = re.compile(r'\$\(|[|;&`]')
NOT_HOST = re.compile(r'\b(?:in_chroot|log|warn|die|echo|printf)\s+"(?:[^"\\]|\\.)*"')
COMMENT = re.compile(r'(?:^|\s)#.*')


def host_code(line):
    return COMMENT.sub('', NOT_HOST.sub('""', line))


def host_pacman(line):
    """Return the first pacman call in this line that would run on the host.

    Anything left of pacman inside the same command counts, so a wrapper such
    as timeout, or the full /usr/bin/pacman path, is still a host call. Only a
    chroot earlier in the same command hands pacman the image's own root.
    """
    for command in COMMANDS.split(host_code(line)):
        for found in PACMAN.finditer(command):
            if 'chroot' not in command[:found.start()]:
                return found.group(0)
    return None


def tool_lists(text):
    return [line.split(' in ', 1)[1].split(';', 1)[0].split()
            for line in text.splitlines() if line.lstrip().startswith('for tool in ')]


class BuildHostWithoutPacman(unittest.TestCase):
    def test_build_host_never_runs_pacman_directly(self):
        for name, text in HOST_SCRIPTS.items():
            for number, line in enumerate(text.splitlines(), 1):
                with self.subTest(script=name, line=number):
                    self.assertIsNone(host_pacman(line), line)

    def test_detector_matches_host_pacman_calls(self):
        for host in ('pacman -Q --dbpath "$MNT/usr/lib/holo/pacmandb" | LC_ALL=C sort > "$WORKDIR/pkgs-before.txt"',
                     'files="$(pacman -Qqlp "$archive")"', 'if pacman -Q x; then',
                     'LC_ALL=C pacman -Q', 'pacman-key --init',
                     '/usr/bin/pacman -Q --dbpath "$MNT/x"', 'timeout 60 pacman -Sy',
                     'env -i pacman -Q', 'xargs pacman -Qi < list', 'nice -n 19 pacman -Q',
                     'sudo -u root pacman -Q', 'chroot_done && pacman -Sy',
                     'chroot "$MNT" cp "$(pacman -Qqlp x)" /tmp'):
            self.assertIsNotNone(host_pacman(host), host)
        for chrooted in ('chroot "$MNT" pacman -Q --dbpath /usr/lib/holo/pacmandb',
                         'in_chroot "pacman-key --init && pacman-key --populate"',
                         "in_chroot 'pacman --config $PACCONF -Sy'",
                         'chroot "$root" pacman -S --noconfirm gcc',
                         'chroot "$MNT" /usr/bin/pacman -Q',
                         "'build_packages': check_output(['chroot', str(root), 'pacman', '-Q'])",
                         'die "pacman -U failed"',
                         '--dbpath "$MNT/usr/lib/holo/pacmandb"',
                         'grep -q jupiter "$root/etc/pacman.conf"'):
            self.assertIsNone(host_pacman(chrooted), chrooted)

    def test_host_tool_checks_do_not_require_pacman(self):
        for name, text in HOST_SCRIPTS.items():
            for tools in tool_lists(text):
                with self.subTest(script=name):
                    self.assertNotIn('pacman', tools)
        for text in (BUILD_HOST_PART, PROBE):
            self.assertTrue(any('readelf' in tools for tools in tool_lists(text)))

    def test_pristine_package_list_uses_the_image_pacman(self):
        self.assertIn('chroot "$MNT" pacman -Q --dbpath /usr/lib/holo/pacmandb', BUILD_HOST_PART)


if __name__ == '__main__':
    unittest.main()
