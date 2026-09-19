import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hdr_defaults', ROOT / 'scripts/hdr-defaults.py')
hdr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hdr)
IDS = ['2826124030', '933556229']


def values(text):
    root = hdr.unique(hdr.parse(text), 'InstallConfigStore')
    group = hdr.unique(root[1], 'Gamescope')
    item = hdr.unique(group[1], 'HDREnabled')
    return {key: value for key, value, _ in item[1]}


class HDRDefaults(unittest.TestCase):
    def test_profile_hash_matches_real_steam_and_unknown(self):
        self.assertEqual(hdr.profile_id('Microstep-MSI MAG272CQR'), '933556229')
        self.assertEqual(hdr.profile_id('unknown'), '2826124030')

    def test_new_config_uses_profile_block_not_scalar(self):
        result = hdr.seed_text('', IDS)
        self.assertEqual(values(result), dict.fromkeys(IDS, '0'))
        self.assertEqual(hdr.seed_text(result, IDS), result)

    def test_existing_on_and_off_survive_and_only_missing_is_added(self):
        for choice in ['0', '1']:
            text = '"InstallConfigStore" { "Gamescope" { "HDREnabled" { "933556229" "' + choice + '" "123" "1" } } }'
            result = hdr.seed_text(text, IDS)
            self.assertEqual(values(result), {'933556229': choice, '123': '1', '2826124030': '0'})
            self.assertEqual(hdr.seed_text(result, IDS), result)

    def test_legacy_scalar_is_migrated_without_touching_other_settings(self):
        text = '// header\n"InstallConfigStore" { "Gamescope" { "HDREnabled" "0" "Other" "value" } "Software" { "Keep" "yes" } }'
        result = hdr.seed_text(text, IDS)
        self.assertEqual(values(result), dict.fromkeys(IDS, '0'))
        self.assertIn('"Other" "value"', result)
        self.assertIn('"Software" { "Keep" "yes" }', result)
        self.assertTrue(result.startswith('// header'))

    def test_missing_blocks_and_case_insensitive_names(self):
        for text in ['"InstallConfigStore" { "Keep" "value" }', '"installconfigstore" { "gamescope" {} }']:
            self.assertEqual(values(hdr.seed_text(text, IDS)), dict.fromkeys(IDS, '0'))

    def test_invalid_or_ambiguous_config_stays_rejected(self):
        for text in ['"InstallConfigStore" {', '"OtherRoot" {}',
                     '"InstallConfigStore" { "Gamescope" "bad" }',
                     '"InstallConfigStore" { "Gamescope" {} "Gamescope" {} }',
                     '"InstallConfigStore" { "Gamescope" { "HDREnabled" "1" } }',
                     '"InstallConfigStore" { "Gamescope" { "HDREnabled" { "933556229" "1" "933556229" "0" } } }']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                hdr.seed_text(text, IDS)

    def test_backup_and_manual_choice_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.vdf'
            original = b'"InstallConfigStore" { "Keep" "value" }'
            path.write_bytes(original)
            self.assertTrue(hdr.seed_file(path, IDS))
            self.assertEqual(path.with_name('config.vdf.before-hdr-default').read_bytes(), original)
            chosen = path.read_text().replace('"933556229"\t\t"0"', '"933556229"\t\t"1"')
            path.write_text(chosen)
            self.assertFalse(hdr.seed_file(path, IDS))
            self.assertEqual(path.read_text(), chosen)

    def test_bad_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.vdf'
            path.write_text('"InstallConfigStore" {')
            with self.assertRaises(ValueError):
                hdr.seed_file(path, IDS)
            self.assertEqual(path.read_text(), '"InstallConfigStore" {')

    def test_edid_vendor_and_model(self):
        edid = bytearray(128)
        edid[:8] = bytes.fromhex('00ffffffffffff00')
        vendor = (13 << 10) | (19 << 5) | 9
        edid[8:10] = vendor.to_bytes(2, 'big')
        edid[54:59] = bytes([0, 0, 0, 252, 0])
        edid[59:72] = b'MSI MAG272CQR'
        edid[127] = -sum(edid[:127]) & 255
        self.assertEqual(hdr.display_identity(edid, {'MSI': 'Microstep'}), 'Microstep-MSI MAG272CQR')
        self.assertEqual(hdr.display_identity(edid, {}), 'MSI-MSI MAG272CQR')
        edid[20] ^= 1
        with self.assertRaises(ValueError):
            hdr.display_identity(edid, {})

    def test_no_display_does_not_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                hdr.discover_profiles(Path(directory), Path(directory) / 'missing-pnp')
