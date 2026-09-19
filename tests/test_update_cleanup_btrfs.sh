#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
work=$(mktemp -d /tmp/ab-cleanup.XXXXXX)
dev=
finish() {
  mountpoint -q "$work/root" && umount "$work/root"
  [[ -z "$dev" ]] || losetup -d "$dev"
  rm -rf -- "$work"
}
trap finish EXIT
mkdir "$work/root"
truncate -s 256M "$work/root.img"
mkfs.btrfs -q -f "$work/root.img"
dev=$(losetup --find --show "$work/root.img")
mount "$dev" "$work/root"
mkdir -p "$work/root/usr/lib/steamos-nvidia"
echo preserved > "$work/root/unrelated"
echo stale > "$work/root/usr/lib/steamos-nvidia/complete"
echo partial > "$work/root/usr/lib/steamos-nvidia/complete.part"
python3 - "$work/cleanup.sh" <<'PY'
import sys
from pathlib import Path
s=Path('steamos-nvidia-installer.sh').read_text().split("<<'REPATCH'\n",1)[1].split('\nREPATCH\n',1)[0]
s='cleanup() {\n'+s.split('cleanup() {\n',1)[1].split('trap cleanup EXIT',1)[0]
Path(sys.argv[1]).write_text(s)
PY
source "$work/cleanup.sh"
NEWROOT="$work/root"; WORK="$work/unused"; MERGED="$work/merged"; WORKIMG="$work/unused.img"
SUCCESS=0; WAS_RO=1
cleanup
set -e
mkdir -p "$work/root"
mount "$dev" "$work/root"
[[ $(btrfs property get "$work/root" ro) == ro=true ]]
[[ ! -e "$work/root/usr/lib/steamos-nvidia/complete" ]]
[[ ! -e "$work/root/usr/lib/steamos-nvidia/complete.part" ]]
grep -qx preserved "$work/root/unrelated"
echo 'Actual repatch cleanup: markers removed, unrelated file preserved, Btrfs read-only restored.'
