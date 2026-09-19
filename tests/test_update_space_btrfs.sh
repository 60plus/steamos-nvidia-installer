#!/bin/bash
# Run as root on Linux with Btrfs support. Uses only temporary files.
set -euo pipefail
source lib/pc-support.sh
work=$(mktemp -d)
dev=
cleanup() {
  mountpoint -q "$work/target" && umount "$work/target"
  [[ -z "$dev" ]] || losetup -d "$dev"
  rm -rf -- "$work"
}
trap cleanup EXIT
mkdir -p "$work/source/usr/lib" "$work/modules/updates" "$work/target"
truncate -s 768M "$work/fs.img"
mkfs.btrfs -q -f "$work/fs.img"
dev=$(losetup --find --show "$work/fs.img")
mount -o compress-force=zstd:3 "$dev" "$work/target"
mkdir -p "$work/target/usr/lib/modules/test"
printf 'preserve me\n' > "$work/target/unrelated"
printf 'module\n' > "$work/modules/updates/test.ko"
# This deliberately exceeds the entire filesystem before compression.
python3 - "$work/source/usr/lib/driver.so" <<'PY'
import sys
with open(sys.argv[1], 'wb') as f:
    block = bytes(range(256)) * 4096
    for _ in range(900):
        f.write(block)
PY
ln -s driver.so "$work/source/usr/lib/driver-link.so"
printf 'usr/lib/driver.so\nusr/lib/driver-link.so\n' > "$work/files"
if pc_require_space "$work/target" 1156; then
  echo 'Fixture does not reproduce the old space rejection' >&2
  exit 1
fi
pc_copy_update_payload "$work/source" "$work/target" "$work/files" "$work/modules/updates" test
cmp "$work/source/usr/lib/driver.so" "$work/target/usr/lib/driver.so"
[[ $(readlink "$work/target/usr/lib/driver-link.so") == driver.so ]]
# Retrying an interrupted update must preserve unrelated files.
pc_copy_update_payload "$work/source" "$work/target" "$work/files" "$work/modules/updates" test
grep -qx 'preserve me' "$work/target/unrelated"
printf 'Compressed payload accepted and retry passed.\n'
df -Pm "$work/target"
# Incompressible payload must still fail when the reserve is exhausted.
dd if=/dev/urandom of="$work/source/usr/lib/random.so" bs=1M count=600 status=none
printf 'usr/lib/random.so\n' > "$work/files"
if pc_copy_update_payload "$work/source" "$work/target" "$work/files" "$work/modules/updates" test; then
  echo 'Insufficient-space payload was incorrectly accepted' >&2
  exit 1
fi
printf 'Insufficient-space payload rejected.\n'
# Failures must propagate even when the caller tests the function status.
rsync() { return 23; }
pc_require_space() { return 0; }
if pc_copy_update_payload "$work/source" "$work/target" "$work/files" "$work/modules/updates" test; then
  echo 'Copy failure was incorrectly accepted' >&2
  exit 1
fi
printf 'Copy failure rejected.\n'
