#!/bin/bash
# Probe a Linux image-build environment without modifying the recovery image.
set -euo pipefail
die() { echo "FAIL: $*" >&2; exit 1; }
if [[ ${1:-} == --help || $# != 2 ]]; then
  echo "Usage: sudo bash tools/check-build-host.sh RECOVERY.img EXISTING_WORK_DIRECTORY"
  echo "Checks dependencies, space, namespaces, loop devices, Btrfs and OverlayFS."
  echo "Creates and removes a temporary probe in the work directory."
  [[ ${1:-} == --help ]] && exit 0
  exit 2
fi
[[ $EUID == 0 ]] || die "Run with sudo on the Linux build host."
[[ $(uname -m) == x86_64 ]] || die "An x86_64 Linux build environment is required."
for tool in losetup blkid btrfs rsync curl depmod sed awk tar zstd python3 readelf modinfo flock sha256sum timeout unshare findmnt mkfs.btrfs mount umount truncate realpath df mktemp; do
  command -v "$tool" >/dev/null || die "Missing tool: $tool. Install it with the host's package manager; see Build machine in the manual's Before you start page."
done
image=$(realpath -e -- "$1")
work=$(realpath -e -- "$2")
[[ -f $image && -r $image && -s $image ]] || die "Recovery image is missing, empty or unreadable."
[[ -d $work ]] || die "Create the work directory first."
[[ $work != *[:,\\]* && $work != *$'\n'* ]] || die "Use a work path without commas, colons, backslashes or newlines."
[[ $image == *.img && $(basename "$image") != *-nvidia*.img ]] || die "Use an unpacked, original recovery .img."
for directory in "$(dirname "$image")" "$work"; do
  fs=$(findmnt -n -o FSTYPE -T "$directory")
  case "$fs" in ext4|btrfs|xfs) ;; *) die "$directory uses $fs. Use an ext4, Btrfs or XFS Linux filesystem, not a Windows shared folder.";; esac
  free=$(df -Pm "$directory" | awk 'END {print $4}')
  (( free >= 28000 )) || die "$directory has $free MiB free; allow at least 28000 MiB for the base build and cache, plus space for compiling optional artifacts."
done
if [[ ${STEAMOS_HOST_PROBE_NAMESPACE:-0} != 1 ]]; then
  exec unshare --mount --pid --fork --kill-child --mount-proc --propagation private -- env STEAMOS_HOST_PROBE_NAMESPACE=1 bash "$0" "$image" "$work"
fi
probe=$(mktemp -d "$work/.host-probe.XXXXXXXX")
loop=''
cleanup() {
  local rc=$? clean=1
  trap - EXIT
  if mountpoint -q "$probe/merged"; then umount "$probe/merged" || clean=0; fi
  if mountpoint -q "$probe/root"; then umount "$probe/root" || clean=0; fi
  if [[ -n $loop ]]; then losetup -d "$loop" || clean=0; fi
  if (( clean )); then rm -rf -- "$probe"; else echo "Cleanup needs attention: $probe" >&2; rc=1; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$probe/root" "$probe/merged" "$probe/upper" "$probe/work"
truncate -s 256M "$probe/probe.img"
mkfs.btrfs -q "$probe/probe.img"
loop=$(losetup --find --show "$probe/probe.img")
mount "$loop" "$probe/root"
echo original > "$probe/root/check"
mount -t overlay overlay -o "lowerdir=$probe/root,upperdir=$probe/upper,workdir=$probe/work" "$probe/merged"
echo changed > "$probe/merged/check"
[[ $(cat "$probe/root/check") == original && $(cat "$probe/merged/check") == changed ]] || die "Overlay copy-on-write test failed."
echo "PASS: host dependencies, space, private mounts, loop device, Btrfs and OverlayFS."
echo "This is a host check, not recovery-image or artifact compatibility validation."
