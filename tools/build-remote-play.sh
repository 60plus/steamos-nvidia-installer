#!/bin/bash
# Build the optional receiver artifact against stable SteamOS libraries.
set -euo pipefail
root=$(realpath "${1:?Usage: build-remote-play.sh BUILD_ROOT OUTPUT}")
out=$(realpath -m "${2:?Output directory required}")
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=build-root-guard.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/build-root-guard.sh"
[[ ! -e $out ]] || { printf 'Output directory already exists: %s\n' "$out" >&2; exit 1; }
require_stable_build_root "$root"
chroot "$root" pacman -S --noconfirm gcc glibc linux-api-headers meson ninja pkgconf libva libdrm libglvnd ffnvcodec-headers
commit=a03711106b5e297a64a704c876aeb776cbce957b
work=$(chroot "$root" mktemp -d /tmp/remote-play-build.XXXXXX)
chroot "$root" git -C "$work" init -q
chroot "$root" git -C "$work" remote add origin https://github.com/elFarto/nvidia-vaapi-driver.git
chroot "$root" git -C "$work" fetch --depth 1 origin "$commit"
chroot "$root" git -C "$work" checkout --detach "$commit"
for patch in "$repo"/patches/nvidia-vaapi-driver/*.patch; do
    cp "$patch" "$root$work/$(basename "$patch")"
    chroot "$root" git -C "$work" apply --check "$work/$(basename "$patch")"
    chroot "$root" git -C "$work" apply "$work/$(basename "$patch")"
done
chroot "$root" meson setup "$work/build" "$work" --buildtype=release
chroot "$root" ninja -C "$work/build" -j "${JOBS:-4}"
cp "$repo/scripts/remote-play-env.c" "$root$work/receiver-env.c"
chroot "$root" gcc -Wall -Wextra -Werror -O2 -shared -fPIC -Wl,-z,relro,-z,now -o "$work/receiver-env.so" "$work/receiver-env.c"
# The 32-bit Steam launcher needs a valid no-op DSO at the expanded $LIB path.
printf 'void receiver_environment_32bit_noop(void) {}\n' > "$root$work/noop.c"
chroot "$root" gcc -m32 -nostdlib -shared -fPIC -Wl,-z,relro,-z,now -o "$work/receiver-env32.so" "$work/noop.c"
mkdir -p "$out/dri" "$out/lib" "$out/lib32" "$out/licenses"
cp "$root$work/build/nvidia_drv_video.so" "$out/dri/"
cp "$root$work/receiver-env.so" "$out/lib/"
cp "$root$work/receiver-env32.so" "$out/lib32/receiver-env.so"
cp "$root$work/COPYING" "$out/licenses/nvidia-vaapi-driver-LICENSE"
cp "$repo/scripts/remote-play-env.c" "$out/receiver-env.c"
python3 - "$out" "$commit" "$repo" <<'META'
import hashlib,json,sys
from pathlib import Path
out,commit,repo=Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3])
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
meta={'source':'https://github.com/elFarto/nvidia-vaapi-driver','commit':commit,
      'scope':'experimental SDR Remote Play receiver; stable SteamOS 3.8',
      'patches':{p.name:sha(p) for p in sorted((repo/'patches/nvidia-vaapi-driver').glob('*.patch'))},
      'files':{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}}
(out/'remote-play-build.json').write_text(json.dumps(meta,indent=2)+'\n')
META
