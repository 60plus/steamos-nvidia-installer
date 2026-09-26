import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class NvencInstallation(unittest.TestCase):
    def test_recreates_update_files_without_touching_receiver_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=root/'usr/lib/steamos-nvidia/nvenc'
            names=['lib32/nvidia_drv_video.so','nvenc-helper','COPYING']
            for name in names:
                p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
            meta={'commit':'3a58095f1833c997fd4f0a73ce3fa0300cdc20fc','files':{n:hashlib.sha256((base/n).read_bytes()).hexdigest() for n in names}}
            (base/'nvenc-build.json').write_text(json.dumps(meta))
            receiver=base.parent/'remote-play/dri/nvidia_drv_video.so'
            receiver.parent.mkdir(parents=True);receiver.write_bytes(b'accepted decoder')
            code='source lib/pc-support.sh\nchroot() { return 0; }\npc_install_nvenc "$1"'
            run=lambda:subprocess.run(['bash','-c',code,'test',str(root)],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(run().returncode,0)
            outputs=[root/p for p in ['usr/lib32/dri/nvidia_drv_video.so','usr/lib/systemd/user/steamos-nvidia-nvenc.service','usr/lib/systemd/user/steam-launcher.service.d/45-nvidia-nvenc.conf','usr/lib/systemd/user/app-steam@.service.d/45-nvidia-nvenc.conf']]
            self.assertEqual(outputs[2].read_text(),outputs[3].read_text(),'both launchers must start the helper')
            self.assertIn('UnsetEnvironment=LIBVA_DRIVER_NAME',outputs[3].read_text())
            saved=[p.read_bytes() for p in outputs]
            for p in outputs:p.unlink()
            self.assertEqual(run().returncode,0)
            self.assertEqual([p.read_bytes() for p in outputs],saved)
            self.assertEqual(receiver.read_bytes(),b'accepted decoder')
            (base/names[0]).write_bytes(b'corrupt')
            self.assertNotEqual(run().returncode,0)

    def test_no_artifact_means_no_service_or_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['bash','-c','source lib/pc-support.sh; pc_install_nvenc "$1"','test',tmp],cwd=ROOT,check=True)
            self.assertEqual(list(Path(tmp).iterdir()),[])
