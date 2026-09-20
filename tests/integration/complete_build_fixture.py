# Run explicitly as root inside a disposable Linux VM with loop/Btrfs support.
# Usage: sudo unshare --mount --pid --fork --kill-child --mount-proc --propagation private python3 tests/integration/complete_build_fixture.py /path/to/test-parent
# Compilers and final image builder are fixtures; this is not a release build.
import os,pathlib,subprocess,tempfile,shutil,hashlib,sys
P=pathlib.Path
base=P(tempfile.mkdtemp(prefix='complete-test-',dir=sys.argv[1]))
repo=base/'repo';(repo/'tools').mkdir(parents=True);(repo/'config').mkdir()
for name in ['build-complete.sh','check-build-host.sh']:shutil.copy(P(__file__).resolve().parents[2]/'tools'/name,repo/'tools'/name)
(repo/'config/github-stable.json').write_text('{"public_key":"fixture","release_api":"https://example.invalid/"}')
fake=base/'bin';fake.mkdir()
def script(p,s):p.write_text('#!/bin/bash\nset -euo pipefail\n'+s);p.chmod(0o755)
script(fake/'df',"printf 'Filesystem 1M-blocks Used Available Use%% Mounted on\\nfixture 200000 0 200000 0%% /\\n'\n")
script(fake/'chroot','exit 0\n')
for name in ['mangoapp','gamescope','remote-play','nvenc']:
 script(repo/'tools'/('build-'+name+'.sh'),f'''mountpoint -q "$1/proc"
[[ -f "$1/etc/os-release" ]]
echo built > "$1/compile-marker"
[[ ${{FAIL_COMPONENT:-}} != {name} ]] || exit 23
mkdir "$2"
echo {name} > "$2/artifact"
''')
script(repo/'steamos-nvidia-installer.sh','''image="${@: -1}"
printf output > "${image%.img}-nvidia-usbinstall.img"
''')
image=base/'recovery.img';subprocess.run(['truncate','-s','384M',image],check=True)
subprocess.run(['sfdisk',str(image)],input='label: gpt\nstart=2048, name="rootfs-A"\n',text=True,check=True,stdout=subprocess.DEVNULL)
loop=subprocess.check_output(['losetup','-f','--show','-P',image],text=True).strip()
mnt=base/'seed';mnt.mkdir()
try:
 subprocess.run(['mkfs.btrfs','-q',loop+'p1'],check=True)
 subprocess.run(['mount',loop+'p1',mnt],check=True)
 for x in ['etc','proc','dev','sys']:(mnt/x).mkdir()
 (mnt/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\n')
finally:
 subprocess.run(['umount',mnt],check=True);subprocess.run(['losetup','-d',loop],check=True)
sha=lambda:hashlib.sha256(image.read_bytes()).hexdigest()
before=sha();env=dict(os.environ,PATH=str(fake)+':'+os.environ['PATH'])
for fail in ['gamescope','']:
 env['FAIL_COMPONENT']=fail
 work=base/('failed' if fail else 'success')
 r=subprocess.run(['bash',str(repo/'tools/build-complete.sh'),'--workdir',str(work),str(image)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 (base/('failure.log' if fail else 'success.log')).write_text(r.stdout)
 assert (r.returncode!=0) if fail else (r.returncode==0),r.stdout
 if fail:assert 'Building gamescope' in r.stdout,r.stdout
 assert sha()==before,'Input changed'
 mounts=subprocess.check_output(['findmnt','-rn','-o','TARGET'],text=True)
 assert not any(x.startswith(str(work)) for x in mounts.splitlines()),mounts
 assert not subprocess.check_output(['losetup','-j',str(image)],text=True)
 if fail:assert not (base/'recovery-nvidia-usbinstall.img').exists()
 else:assert (base/'recovery-nvidia-usbinstall.img.sha256').exists()
r=subprocess.run(['bash',str(repo/'tools/build-complete.sh'),str(image)],env=env,capture_output=True,text=True)
assert r.returncode!=0 and 'Output already exists' in r.stderr
print('PASS: real partition/loop/Btrfs/OverlayFS orchestration, success + compile failure cleanup, input unchanged, overwrite rejected. Compilation, space report and final image builder are fixtures.')
print('Evidence:',base)
