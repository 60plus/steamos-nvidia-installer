from pathlib import Path
import tempfile
import unittest

from test_pc_support import ROOT, shell


class PackageDatabase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db = "usr/lib/holo/pacmandb/local"

    def entry(self, root, directory, name):
        p = self.path / root / self.db / directory
        p.mkdir(parents=True)
        (p / "desc").write_text("%NAME%\n" + name + "\n")
        return p

    def run_copy(self):
        return shell(
            'set -eu; source lib/pc-support.sh; root="$PWD/$1"; '
            'pc_copy_package_db "$root/merged" "$root/target" example',
            self.path.name)

    def test_upgrade_replaces_old_database_entry(self):
        old = self.entry("target", "example-1-1", "example")
        self.entry("merged", "example-2-1", "example")
        result = self.run_copy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())
        self.assertTrue((self.path / "target" / self.db / "example-2-1/desc").exists())

    def test_similar_package_name_is_preserved(self):
        unrelated = self.entry("target", "example-32bit-1-1", "example-32bit")
        self.entry("merged", "example-2-1", "example")
        result = self.run_copy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(unrelated.exists())

    def test_missing_source_keeps_old_entry(self):
        old = self.entry("target", "example-1-1", "example")
        result = self.run_copy()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(old.exists())

    def test_ambiguous_source_keeps_old_entry(self):
        old = self.entry("target", "example-1-1", "example")
        self.entry("merged", "example-2-1", "example")
        self.entry("merged", "example-3-1", "example")
        result = self.run_copy()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(old.exists())
