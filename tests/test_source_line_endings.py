import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SourceLineEndings(unittest.TestCase):
    def test_linux_builder_inputs_have_lf(self):
        paths = [ROOT / 'VERSION', *sorted((ROOT / 'patches').rglob('*.patch'))]
        self.assertGreater(len(paths), 1)
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                data = path.read_bytes()
                self.assertNotIn(b'\r', data)
                self.assertTrue(data.endswith(b'\n'))
