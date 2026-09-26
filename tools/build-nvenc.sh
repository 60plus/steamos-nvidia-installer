#!/bin/bash
# Build the optional NVENC bridge against stable SteamOS libraries.
set -euo pipefail
root=$(realpath "${1:?Usage: build-nvenc.sh BUILD_ROOT OUTPUT}")
out=$(realpath -m "${2:?Output directory required}")
# shellcheck source=build-root-guard.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/build-root-guard.sh"
[[ ! -e $out ]] || { printf 'Output directory already exists: %s\n' "$out" >&2; exit 1; }
require_stable_build_root "$root"
chroot "$root" pacman -S --noconfirm gcc glibc linux-api-headers meson ninja pkgconf libva libdrm libglvnd ffnvcodec-headers lib32-glibc lib32-gcc-libs lib32-libglvnd lib32-libva lib32-libdrm
commit=3a58095f1833c997fd4f0a73ce3fa0300cdc20fc
work=$(chroot "$root" mktemp -d /tmp/nvenc-build.XXXXXX)
chroot "$root" git -C "$work" init -q
chroot "$root" git -C "$work" remote add origin https://github.com/efortin/nvidia-vaapi-driver.git
chroot "$root" git -C "$work" fetch --depth 1 origin "$commit"
chroot "$root" git -C "$work" checkout --detach "$commit"
sed -i "s@'/usr/lib/i386-linux-gnu/pkgconfig', '/usr/share/pkgconfig', '/usr/lib/pkgconfig'@'/usr/lib32/pkgconfig', '/usr/share/pkgconfig'@" "$root$work/cross-i386.txt"
chroot "$root" meson setup "$work/build64" "$work" --buildtype=release
chroot "$root" ninja -C "$work/build64" -j "${JOBS:-4}"
chroot "$root" meson setup "$work/build32" "$work" --cross-file "$work/cross-i386.txt" --buildtype=release
chroot "$root" ninja -C "$work/build32" -j "${JOBS:-4}"
mkdir -p "$out/lib32"
cp "$root$work/build32/nvidia_drv_video.so" "$out/lib32/"
cp "$root$work/build64/nvenc-helper" "$root$work/COPYING" "$out/"
cp "$root$work/cross-i386.txt" "$out/"
python3 - "$out" "$commit" <<'META'
import hashlib,json,sys
from pathlib import Path
out,commit=Path(sys.argv[1]),sys.argv[2]
meta={'source':'https://github.com/efortin/nvidia-vaapi-driver','commit':commit,
      'scope':'experimental 32-bit VAAPI encoding through 64-bit NVENC helper',
      'build_adjustment':'Arch lib32 pkg-config search path; cross file included',
      'files':{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}}
(out/'nvenc-build.json').write_text(json.dumps(meta,indent=2)+'\n')
META
