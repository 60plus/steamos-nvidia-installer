"""The artifact builders must refuse a build root that mixes SteamOS lines.

Artifacts are compiled against the build root's libraries and then shipped in the
image built from that same root, so a beta, Preview or main repository can produce
a binary that fails to load on the machine that receives it. The rule is that every
repository belongs to the root's own stable line, not that the line is 3.8: a root
Valve has not shipped yet must build, with a warning. Only the file inspection is
exercised here; the privileged part needs root and a mounted /proc.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_EXE") or shutil.which("bash")
GUARD = ROOT / "tools" / "build-root-guard.sh"
BASELINES = json.loads((ROOT / "config" / "build-baselines.json").read_text(encoding="utf-8"))
VALIDATED_ROOTS = BASELINES["tested_build_root"]
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
        self.assertIn("3.8.18", result.stderr, "the warning must name the release it saw")
        for validated in VALIDATED_ROOTS:
            self.assertIn(validated, result.stderr, "the warning must list what was validated")

    def test_a_future_release_line_builds_once_its_own_repositories_are_used(self):
        # Valve replaces the recovery image on its own schedule. A whole new line
        # must not stop the build, as long as nothing from another line is mixed in.
        conf = "[options]\n" + "\n".join(f"[{name}-3.9.1x]" for name in ["jupiter", "holo", "core"])
        result = check("ID=steamos\nVERSION_ID=3.9.1\n", conf)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.9.1", result.stderr, "an unvalidated line still warns")

    def test_the_validated_list_comes_from_the_shared_baseline_file(self):
        # One table, so a new baseline is one edit rather than a hunt through scripts.
        self.assertEqual(sorted(BASELINES["tested_build_root"]), sorted(VALIDATED_ROOTS))
        guard = GUARD.read_text(encoding="utf-8")
        self.assertIn("config/build-baselines.json", guard)
        self.assertNotIn("3.8.14 or 3.8.16", guard, "the validated releases must not be hardcoded")

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
