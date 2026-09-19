import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_updater', ROOT / 'scripts/installer-update.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)

class PublicReleaseSource(unittest.TestCase):
    def test_public_source_is_github_stable_with_public_key_only(self):
        cfg = json.loads((ROOT / 'config/github-stable.json').read_text())
        self.assertEqual(cfg['release_api'], 'https://api.github.com/repos/60plus/steamos-nvidia-installer/releases/')
        self.assertEqual(cfg['download_origin'], 'https://github.com')
        self.assertIs(cfg['allow_prerelease'], False)
        self.assertTrue(cfg['public_key'].startswith('-----BEGIN PUBLIC KEY-----'))
        self.assertNotIn('PRIVATE KEY', cfg['public_key'])
        self.assertEqual(set(cfg), {'name','release_api','download_origin','allow_prerelease','public_key'})

    def test_missing_release_does_not_try_another_source(self):
        cfg = json.loads((ROOT / 'config/github-stable.json').read_text())
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, 'source', return_value=cfg), patch.object(updater, 'download', side_effect=RuntimeError('no release')) as download:
            with self.assertRaises(RuntimeError):
                updater.fetch('latest', Path(tmp))
            self.assertEqual(download.call_count, 1)
            self.assertEqual(download.call_args.args[0], cfg['release_api'] + 'latest')