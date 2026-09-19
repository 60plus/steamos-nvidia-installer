from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


def shell(code, *args):
    return subprocess.run(['bash', '-c', 'source lib/pc-support.sh; '+code, 'test', *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=30)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        if self.path == '/retry' and self.server.calls.count('/retry') == 1:
            self.send_response(500); self.end_headers(); return
        if self.path in ['/ok', '/retry']:
            self.send_response(200); self.end_headers(); self.wfile.write(b'complete-package'); return
        if self.path == '/partial':
            self.send_response(200); self.send_header('Content-Length','100'); self.end_headers(); self.wfile.write(b'short'); return
        self.send_response(404); self.end_headers()


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), Handler)
        cls.server.calls = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_exact_package_fallback_both_directions(self):
        for pkg,repo in [('nvidia-utils','extra'),('nvidia-open-dkms','extra'),('lib32-nvidia-utils','multilib'),('egl-wayland2','extra')]:
            name=pkg+'-610.57.04-1-x86_64.pkg.tar.zst'
            archive=f'https://archive.archlinux.org/packages/{pkg[0]}/{pkg}/{name}'
            mirror=f'https://geo.mirror.pkgbuild.com/{repo}/os/x86_64/{name}'
            for a,b in [(archive,mirror),(mirror,archive)]:
                result=shell('pc_package_fallback "$1"',a)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout.strip(),b)
        result=shell('pc_package_fallback "$1"','https://example.org/nvidia-utils-610.57.04-1-x86_64.pkg.tar.zst')
        self.assertNotEqual(result.returncode,0)

    def test_missing_file_uses_explicit_alternative(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'package'
            result=shell('pc_download "$1" "$2" "$3"',self.url+'/missing',str(dest),self.url+'/ok')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Exact file unavailable (HTTP 404)',result.stderr)
            self.assertEqual(dest.read_bytes(),b'complete-package')
            self.assertEqual(list(Path(temp).glob('*.part.*')),[])

    def test_interrupted_transfer_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'package'; dest.write_bytes(b'previous-good-file')
            result=shell('pc_download "$1" "$2" "$3"',self.url+'/partial',str(dest),self.url+'/missing')
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(dest.read_bytes(),b'previous-good-file')
            self.assertEqual(list(Path(temp).glob('*.part.*')),[])

    def test_transient_server_failure_is_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            result=shell('pc_download "$1" "$2"',self.url+'/retry',str(Path(temp)/'package'))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(self.server.calls.count('/retry'),2)

    def test_retry_policy_is_bounded(self):
        result=shell('curl() { printf "%s\\n" "$@"; }; pc_curl --head https://example.org')
        flags=result.stdout.splitlines()
        for name,value in [('--connect-timeout','15'),('--max-time','300'),('--retry','2'),('--retry-max-time','600')]:
            self.assertEqual(flags[flags.index(name)+1],value)


class ActiveRootTests(unittest.TestCase):
    def test_btrfs_source_uses_block_id(self):
        result=shell('findmnt() { echo "/dev/sda3[/root]"; }; lsblk() { [[ $* == "-dn -o MAJ:MIN /dev/sda3" ]] || return 1; echo 8:3; }; pc_active_root_id')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'8:3')

    def test_unresolved_source_stops(self):
        result=shell('findmnt() { echo overlay; }; pc_active_root_id')
        self.assertNotEqual(result.returncode,0)


class ArchiveResolutionTests(unittest.TestCase):
    def resolve(self, curl_body):
        installer = (ROOT / 'steamos-nvidia-installer.sh').read_text()
        function = 'pin_pkg() {' + installer.split('pin_pkg() {', 1)[1].split('\n# fetch_pins', 1)[0]
        return shell('die() { echo "$*" >&2; exit 1; }; log() { :; }; ARCHIVE_URL=https://archive.archlinux.org/packages; pc_curl() { '+curl_body+'; }; '+function.rstrip()+'; pin_pkg nvidia-utils 610')

    def test_unreachable_index_is_not_reported_as_missing_version(self):
        result=self.resolve('return 7')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('availability is unknown',result.stderr)

    def test_loaded_index_without_matching_version(self):
        result=self.resolve('echo "<html>empty index</html>"')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Archive index loaded, but no',result.stderr)
