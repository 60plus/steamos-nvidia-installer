#!/bin/bash
# Build an experimental stable Gamescope artifact in a disposable SteamOS root.
set -euo pipefail
root=$(realpath "${1:?Usage: build-gamescope.sh STEAMOS_BUILD_ROOT OUTPUT_DIRECTORY}")
out=$(realpath -m "${2:?Output directory is required}")
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=build-root-guard.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/build-root-guard.sh"
[[ ! -e $out ]] || { printf 'Output directory already exists: %s\n' "$out" >&2; exit 1; }
require_stable_build_root "$root"
# Recovery images strip headers while retaining the package database entries.
# Reinstall build libraries; --needed would leave those headers missing.
chroot "$root" pacman -S --noconfirm gcc glibc linux-api-headers git meson ninja cmake \
  pkgconf python-mako glslang vulkan-headers vulkan-icd-loader wayland-protocols \
  wayland libdrm libx11 libxcb libxau libxdmcp xorgproto libxcomposite libxdamage \
  libxres xcb-util-errors xcb-util-wm xcb-util-renderutil libxfixes libxext libxi \
  libxrender libxrandr libxinerama libxmu libxt libxtst libxft libxpresent libxshmfence \
  libxxf86vm libxcursor libxfont2 libxkbfile libxkbcommon libxkbcommon-x11 libsm libice \
  freerdp pixman libinput seatd pipewire libpipewire libdecor libei luajit libavif \
  aom rav1e libdisplay-info libliftoff glm benchmark catch2 libcap hwdata libpng \
  lcms2 util-linux-libs xorg-xwayland sdl2-compat systemd-libs dbus libffi expat zlib
commit=2b79e07b3da1723c7e5c5f44f18de36c6cb78b9e
work=$(chroot "$root" mktemp -d /tmp/gamescope-build.XXXXXX)
printf 'Build source directory: %s%s\n' "$root" "$work"
chroot "$root" git -C "$work" init -q
chroot "$root" git -C "$work" remote add origin https://github.com/ValveSoftware/gamescope.git
chroot "$root" git -C "$work" fetch --depth 1 origin "$commit"
chroot "$root" git -C "$work" checkout --detach "$commit"
chroot "$root" git -C "$work" submodule update --init --recursive
for patch in "$repo"/patches/gamescope/*.patch; do
  cp "$patch" "$root$work/$(basename "$patch")"
  chroot "$root" git -C "$work" apply --check "$work/$(basename "$patch")"
  chroot "$root" git -C "$work" apply "$work/$(basename "$patch")"
done
chroot "$root" meson setup "$work/build" "$work" --buildtype=release --prefix=/usr
chroot "$root" ninja -C "$work/build" -j "${JOBS:-4}"
chroot "$root" meson test -C "$work/build" --print-errorlogs
cp "$repo/tools/test-gamescope-capture.py" "$root$work/test-capture.py"
chroot "$root" python3 "$work/test-capture.py" "$work"
chroot "$root" /usr/lib/ld-linux-x86-64.so.2 --list "$work/build/src/gamescope" >/dev/null
chroot "$root" "$work/build/src/gamescope" --version
chroot "$root" env DESTDIR="$work/stage" meson install -C "$work/build" --no-rebuild
mkdir -p "$out"
cp -a "$root$work/stage" "$out/root"
cp "$root$work/LICENSE" "$out/Gamescope-LICENSE"
python3 - "$out" "$commit" "$repo/patches/gamescope" "$root" "$work" <<'PY'
import hashlib,json,sys,shutil,subprocess
from pathlib import Path
out,commit,patches,root=Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]),Path(sys.argv[4])
source=root/sys.argv[5].lstrip('/')
for p in source.rglob('*'):
    if p.is_file() and not p.is_symlink() and p.name.lower().startswith(('license','copying','copyright')) and '.git' not in p.parts and 'build' not in p.relative_to(source).parts:
        dest=out/'licenses'/p.relative_to(source)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,dest)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
metadata={'source':'https://github.com/ValveSoftware/gamescope','commit':commit,
'upstream_fix':'ff6b924fd0634a51d0fb3755c56c01dca1daadc1','base_version':'3.16.23.4',
'status':'experimental; hardware acceptance required',
'build_packages':subprocess.check_output(['chroot',str(root),'pacman','-Q'],text=True).splitlines(),
'submodules':subprocess.check_output(['chroot',str(root),'git','-C',sys.argv[5],'submodule','status','--recursive'],text=True).splitlines(),
'build_os_release':(root/'etc/os-release').read_text(),
'patches':{p.name:sha(p) for p in sorted(patches.glob('*.patch'))},
'files':{str(p.relative_to(out/'root')):sha(p) for p in sorted((out/'root').rglob('*')) if p.is_file() and not p.is_symlink()}}
(out/'gamescope-build.json').write_text(json.dumps(metadata,indent=2)+'\n')
PY
printf 'Experimental artifact (not installed): %s\n' "$out"