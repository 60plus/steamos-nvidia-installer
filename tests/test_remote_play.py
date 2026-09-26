import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receiver_env', ROOT/'scripts/remote-play-env.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ReceiverEnvironment(unittest.TestCase):
    def test_preserves_existing_and_deduplicates(self):
        existing = '/a.so:' + module.PRELOAD + ' /b.so'
        self.assertEqual(module.environment_line(existing), 'LD_PRELOAD="'+module.PRELOAD+' /a.so /b.so"\n')
        self.assertEqual(module.environment_line(existing, False), 'LD_PRELOAD="/a.so /b.so"\n')
        with self.assertRaises(ValueError): module.environment_line('/a.so\nOTHER=value')

    @unittest.skipUnless(shutil.which('gcc') and os.name == 'posix', 'Linux C compiler required')
    def test_constructor_only_changes_receiver_and_honors_opt_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            subprocess.run(['gcc','-shared','-fPIC','-Wall','-Wextra','-Werror','-o',str(p/'env.so'),str(ROOT/'scripts/remote-play-env.c')],check=True)
            (p/'probe.c').write_text('#include <stdlib.h>\n#include <stdio.h>\nint main(void){const char *v=getenv("LIBVA_DRIVER_NAME"); puts(v?v:"unset");return 0;}\n')
            subprocess.run(['gcc','-o',str(p/'streaming_client'),str(p/'probe.c')],check=True)
            shutil.copyfile(p/'streaming_client',p/'game');(p/'game').chmod(0o755)
            shutil.copyfile(p/'streaming_client',p/'streaming_client_extra');(p/'streaming_client_extra').chmod(0o755)
            env={**os.environ,'LD_PRELOAD':str(p/'env.so'),'LIBVA_DRIVER_NAME':'original'}
            for name,expected in [('streaming_client','nvidia'),('game','original'),('streaming_client_extra','original')]:
                result=subprocess.run([str(p/name)],env=env,capture_output=True,text=True,check=True)
                self.assertEqual(result.stdout.strip(),expected)
                self.assertEqual(result.stderr,'')
            env['STEAMOS_NVIDIA_REMOTE_PLAY']='0'
            self.assertEqual(subprocess.check_output([str(p/'streaming_client')],env=env,text=True).strip(),'original')

class ReceiverInstallation(unittest.TestCase):
    def test_reinstall_preserves_artifact_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'usr/lib/steamos-nvidia/remote-play'
            names=['dri/nvidia_drv_video.so','lib/receiver-env.so','lib32/receiver-env.so','licenses/nvidia-vaapi-driver-LICENSE','receiver-env.c']
            for name in names:
                f=base/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(name.encode())
            meta={'commit':'a03711106b5e297a64a704c876aeb776cbce957b','files':{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in names}}
            (base/'remote-play-build.json').write_text(json.dumps(meta))
            (base.parent/'remote-play-env.py').write_text('test')
            code='source lib/pc-support.sh\nchroot() { return 0; }\npc_install_remote_play "$1"'
            run=lambda:subprocess.run(['bash','-c',code,'test',str(root)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(run().returncode,0)
            drop=root/'usr/lib/systemd/user/steam-launcher.service.d/40-nvidia-remote-play.conf'
            saved=drop.read_bytes();drop.unlink()
            self.assertEqual(run().returncode,0);self.assertEqual(drop.read_bytes(),saved)
            (base/names[0]).write_bytes(b'corrupt')
            self.assertNotEqual(run().returncode,0)

class EveryReceiverPatchReachesTheBuild(unittest.TestCase):
    """A patch that nothing applies is worse than no patch: it reads as fixed.

    0002 bounds the wait for a surface resolve. Measured on 2026-09-26 on RTX 5060
    with NVIDIA 610.57.04: after a Remote Play session ended, the receiver's main
    thread stayed in pthread_cond_wait under nvExportSurfaceHandle at no CPU,
    ignored SIGTERM, needed SIGKILL, and gamescope kept its last black frame on
    screen the whole time. The builder and the README have to keep up with
    whatever lands in the directory, and the correction must stay bounded.
    """

    DIRECTORY = ROOT / 'patches/nvidia-vaapi-driver'
    PATCHES = sorted(DIRECTORY.glob('*.patch'))

    def test_the_builder_applies_the_whole_directory(self):
        script = (ROOT / 'tools/build-remote-play.sh').read_text(encoding='utf-8')
        self.assertTrue(self.PATCHES)
        self.assertIn('patches/nvidia-vaapi-driver/*.patch', script)
        self.assertIn('apply --check', script)
        # The recorded metadata has to cover the same directory, or an image can
        # carry a patch its own build record does not mention.
        self.assertIn("sorted((repo/'patches/nvidia-vaapi-driver').glob('*.patch'))", script)

    def test_each_patch_is_documented_by_file_name(self):
        readme = (self.DIRECTORY / 'README.md').read_text(encoding='utf-8')
        for patch in self.PATCHES:
            with self.subTest(patch=patch.name):
                self.assertIn(patch.name, readme)

    def test_every_resolve_wait_the_receiver_patch_touches_is_bounded(self):
        text = (self.DIRECTORY / '0002-release-unresolved-surfaces.patch').read_text(encoding='utf-8')
        added = [line[1:] for line in text.splitlines()
                 if line.startswith('+') and not line.startswith('+++')]
        removed = [line[1:] for line in text.splitlines()
                   if line.startswith('-') and not line.startswith('---')]
        self.assertTrue(removed, 'the patch no longer removes anything')
        for line in removed:
            self.assertIn('pthread_cond_wait(', line)
        self.assertTrue([line for line in added if 'pthread_cond_timedwait(' in line])
        self.assertEqual([], [line for line in added if 'pthread_cond_wait(' in line])
        self.assertTrue([line for line in added if 'ETIMEDOUT' in line])
