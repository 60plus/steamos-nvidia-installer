"""The artifact builders must refuse a build root from the wrong SteamOS channel.

Artifacts are compiled against the build root's libraries and then shipped in a
stable image, so a beta, Preview or main root can produce a binary that fails to
load on the machine that receives it. Only the file inspection is exercised here;
the privileged part needs root and a mounted /proc.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_EXE") or shutil.which("bash")
GUARD = ROOT / "tools" / "build-root-guard.sh"
BUILDERS = ["build-mangoapp.sh", "build-gamescope.sh", "build-remote-play.sh", "build-nvenc.sh"]

STABLE = "\n".join(f"[{name}-3.8.1x]\nServer = https://example.invalid/$repo"
                   for name in ["jupiter", "holo", "core", "extra", "multilib"])


def check(os_release, pacman_conf):
    """Run check_stable_steamos_root against a throwaway root."""
    with tempfile.TemporaryDirectory() as tmp:
        etc = Path(tmp) / "etc"
        etc.mkdir()
        if os_release is not None:
            (etc / "os-release").write_text(os_release, encoding="utf-8")
        if pacman_conf is not None:
            (etc / "pacman.conf").write_text(pacman_conf, encoding="utf-8")
        return subprocess.run(
            [BASH, "-c", f'set -euo pipefail; source "{GUARD}"; check_stable_steamos_root "$1"', "test", tmp],
            capture_output=True, text=True, timeout=30,
        )


class StableRootIsAccepted(unittest.TestCase):
    def test_stable_release_passes_without_a_warning(self):
        for version in ['3.8.14', '"3.8.16"']:
            with self.subTest(version=version):
                result = check(f"ID=steamos\nVERSION_ID={version}\n", "[options]\n" + STABLE)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")

    def test_an_untested_point_release_warns_but_still_builds(self):
        result = check("ID=steamos\nVERSION_ID=3.8.18\n", "[options]\n" + STABLE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.8.14 or 3.8.16", result.stderr)

    def test_a_commented_out_repository_is_not_a_repository(self):
        conf = "[options]\n#[jupiter-3.9]\n" + STABLE
        self.assertEqual(check("ID=steamos\nVERSION_ID=3.8.16\n", conf).returncode, 0)


class WrongRootIsRefused(unittest.TestCase):
    def refused(self, os_release, pacman_conf):
        result = check(os_release, pacman_conf)
        self.assertEqual(result.returncode, 1, "the guard accepted a root it should refuse")
        self.assertNotEqual(result.stderr.strip(), "", "a refusal must say why")
        return result

    def test_beta_preview_and_main_channels(self):
        for channel in ["3.8", "3.9", "main"]:
            with self.subTest(channel=channel):
                conf = f"[options]\n[jupiter-{channel}]\n[holo-{channel}]\n"
                self.assertIn(channel, self.refused("ID=steamos\nVERSION_ID=3.8.16\n", conf).stderr)

    def test_one_stray_repository_among_stable_ones(self):
        conf = "[options]\n" + STABLE + "\n[multilib-3.9]\n"
        self.assertIn("multilib-3.9", self.refused("ID=steamos\nVERSION_ID=3.8.16\n", conf).stderr)

    def test_not_steamos_at_all(self):
        for identity in ["ID=arch", "ID=steamos-holo", "ID=holo"]:
            with self.subTest(identity=identity):
                self.refused(identity + "\nVERSION_ID=3.8.16\n", "[options]\n" + STABLE)

    def test_wrong_series(self):
        self.refused("ID=steamos\nVERSION_ID=3.9.2\n", "[options]\n" + STABLE)

    def test_missing_or_empty_configuration(self):
        self.refused("ID=steamos\nVERSION_ID=3.8.16\n", None)
        self.refused(None, "[options]\n" + STABLE)
        self.refused("ID=steamos\nVERSION_ID=3.8.16\n", "")
        self.refused("ID=steamos\nVERSION_ID=3.8.16\n", "[options]\n")


class EveryBuilderUsesTheGuard(unittest.TestCase):
    def test_all_four_artifact_builders_call_it(self):
        for name in BUILDERS:
            with self.subTest(builder=name):
                text = (ROOT / "tools" / name).read_text(encoding="utf-8")
                self.assertIn("build-root-guard.sh", text)
                self.assertIn('require_stable_build_root "$root"', text)

    def test_no_builder_keeps_its_own_weaker_check(self):
        # The old checks accepted a beta root and exited without a message.
        for name in BUILDERS:
            with self.subTest(builder=name):
                text = (ROOT / "tools" / name).read_text(encoding="utf-8")
                self.assertNotIn(r"(main|3\.9)", text)
                self.assertNotIn("grep -q '^ID=steamos'", text)


if __name__ == "__main__":
    unittest.main()
