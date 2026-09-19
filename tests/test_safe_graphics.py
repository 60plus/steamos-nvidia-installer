import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('safe', ROOT / 'scripts/safe-graphics.py')
safe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe)
SOURCE = '''#!/bin/bash
export STEAM_GAMESCOPE_HDR_SUPPORTED=1
export STEAM_GAMESCOPE_VRR_SUPPORTED=1
export STEAM_GAMESCOPE_COLOR_MANAGED=1
export STEAM_GAMESCOPE_VIRTUAL_WHITE=1
# Spawned in parallel to read values from gamescope
exec gamescope \\
 -w 1280 -h 800 \\
 -O '*',eDP-1
'''


class SafeGraphics(unittest.TestCase):
    def test_prefers_advertised_1080p60(self):
        self.assertEqual(safe.choose_mode([('HDMI-A-1', [(2560,1440,165),(1920,1080,59.94)])]), ('HDMI-A-1',1920,1080))

    def test_fallback_not_invented_for_unsupported_refresh(self):
        self.assertEqual(safe.choose_mode([('DP-3', [(1920,1080,144),(1280,720,60)])]), ('DP-3',1280,720))
        for displays in [[], [('DP-1',[(1920,1080,144)])], [('DP-1',[]),('HDMI-A-1',[])]]:
            with self.assertRaises(ValueError): safe.choose_mode(displays)

    def test_rejects_upstream_conflicts(self):
        for change in [SOURCE.replace('exec gamescope','exec /usr/bin/gamescope'), SOURCE.replace('-w 1280','-W 2560'), SOURCE.replace('-w 1280','--hdr-enabled'), SOURCE.replace("-O '*',eDP-1",'-O DP-1')]:
            with self.assertRaises(ValueError): safe.safe_session(change, ('DP-1',1920,1080))

    def test_safe_exports_and_arguments_reach_process(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); (d/'gamescope').write_text('#!/bin/sh\nprintf "%s %s %s %s\\n" "$STEAM_GAMESCOPE_HDR_SUPPORTED" "$STEAM_GAMESCOPE_VRR_SUPPORTED" "$STEAM_GAMESCOPE_COLOR_MANAGED" "$STEAM_GAMESCOPE_VIRTUAL_WHITE"\nprintf "%s\\n" "$@"\n')
            (d/'gamescope').chmod(0o755)
            body=safe.safe_session(SOURCE, ('HDMI-A-1',1920,1080))
            result=subprocess.run(['bash'],input=body,text=True,capture_output=True,env={**os.environ,'PATH':str(d)+':'+os.environ['PATH']})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(result.stdout.startswith('0 0 0 0\n'))
            self.assertIn('--force-composition\n-W\n1920\n-H\n1080\n-r\n60',result.stdout)
            self.assertIn('-O\nHDMI-A-1',result.stdout)

    def test_toggle_does_not_touch_steam_or_restart(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)/'config/safe-graphics'
            steam=Path(d)/'config.vdf';steam.write_text('saved settings')
            with patch.object(safe,'MODE',state):
                safe.set_mode(True);self.assertEqual(state.read_text(),'1\n')
                safe.set_mode(True);safe.set_mode(False);safe.set_mode(False)
                self.assertFalse(state.exists());self.assertEqual(steam.read_text(),'saved settings')

    def test_normal_launch_executes_stock_without_probing(self):
        with tempfile.TemporaryDirectory() as d, patch.object(safe,'MODE',Path(d)/'absent'), patch.object(safe.sys,'argv',['safe','launch']), patch.object(safe.os,'execv',side_effect=SystemExit) as call, patch.object(safe,'display_modes') as probe:
            with self.assertRaises(SystemExit): safe.main()
            call.assert_called_once_with(str(safe.STOCK),[str(safe.STOCK)])
            probe.assert_not_called()
