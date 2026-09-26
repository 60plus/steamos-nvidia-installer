#!/bin/bash
# Build the pinned overlay inside a disposable, prepared SteamOS build root.
set -euo pipefail
root=$(realpath "${1:?Usage: build-mangoapp.sh STEAMOS_BUILD_ROOT OUTPUT_DIRECTORY}")
out=$(realpath -m "${2:?Output directory is required}")
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=build-root-guard.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/build-root-guard.sh"
[[ ! -e $out ]] || { printf 'Output directory already exists: %s\n' "$out" >&2; exit 1; }
require_stable_build_root "$root"
chroot "$root" pacman -S --noconfirm gcc git meson ninja python-mako pkgconf glibc linux-api-headers libx11 libxrandr libxext xorgproto wayland glfw dbus libdrm libglvnd zlib libxcb libxau libxdmcp libxkbcommon vulkan-icd-loader libffi libxrender libxfixes libxinerama libxcursor libxdamage systemd-libs expat libxnvctrl vulkan-headers wayland-protocols glslang
work=$(chroot "$root" mktemp -d /tmp/mango-build.XXXXXX)
chroot "$root" git -C "$work" init -q
chroot "$root" git -C "$work" remote add origin https://github.com/flightlessmango/MangoHud.git
commit=33c2c7ddbb72c15e19a42163d75424d5804f8ec8
chroot "$root" git -C "$work" fetch --depth 1 origin "$commit"
chroot "$root" git -C "$work" checkout --detach "$commit"
chroot "$root" git -C "$work" submodule update --init --recursive
for patch in "$repo"/patches/mangohud/*.patch; do
  cp "$patch" "$root$work/$(basename "$patch")"
  chroot "$root" git -C "$work" apply "$work/$(basename "$patch")"
done
chroot "$root" meson setup "$work/build" "$work" --buildtype=release -Dmangoapp=true -Dmangohudctl=false -Dinclude_doc=false -Dtests=disabled -Dmangoplot=disabled
chroot "$root" ninja -C "$work/build" -j "${JOBS:-4}" src/mangoapp
mkdir -p "$out"
cp "$root$work/build/src/mangoapp" "$out/mangoapp"
strip "$out/mangoapp"
cp "$root$work/LICENSE" "$out/MangoHud-LICENSE"
python3 - "$out" "$commit" "$repo/patches/mangohud" <<'PY'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1])
value={'source':'https://github.com/flightlessmango/MangoHud','commit':sys.argv[2],
'upstream_fix':'4e69793b9b77a394b8f7842a78de235eaf3859df',
'changes':['Refresh NVIDIA sensor settings each sampling cycle','Hide unsupported NVIDIA voltage and junction temperature fields','Show detected GPU, VRAM capacity and CPU in Steam overlay presets 3 and 4'],
'patches':{x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in sorted(Path(sys.argv[3]).glob('*.patch'))},
'sha256':hashlib.sha256((p/'mangoapp').read_bytes()).hexdigest()}
(p/'mangoapp-build.json').write_text(json.dumps(value,indent=2)+'\n')
PY
printf 'Overlay artifact: %s\n' "$out"
