#!/bin/bash
# Compile the project's addons and build an installer from an original recovery image.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
usage() {
  cat <<'HELP'
Usage: sudo bash tools/build-complete.sh [OPTIONS] RECOVERY.img
  --workdir DIR   New directory on a Linux filesystem (must not exist)
  --source FILE   Updater source configuration (default: config/github-stable.json)
  --driver SPEC   NVIDIA package version (default: 610.57.04-1)
  --keep-cuda     Keep compute libraries (default removes them)
  --help         Show this help
Builds MangoApp, Gamescope, Remote Play and NVENC from pinned sources.
Requires a clean SteamOS 3.8.14 recovery image and at least 50 GiB free for work.
Output is written next to the input. Existing output is never overwritten.
HELP
}
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
original=("$@")
image='' work='' source="$repo/config/github-stable.json" driver=610.57.04-1
trim=(--trim-cuda)
while (( $# )); do
  case "$1" in
    --help) usage; exit 0;;
    --workdir|--source|--driver)
      (( $# >= 2 )) || die "Missing value for $1"
      case "$1" in --workdir) work=$2;; --source) source=$2;; --driver) driver=$2;; esac
      shift 2;;
    --keep-cuda) trim=(); shift;;
    -*) die "Unknown option: $1";;
    *) [[ -z $image ]] || die 'Specify exactly one input image'; image=$1; shift;;
  esac
done
[[ -n $image ]] || { usage; exit 2; }
[[ $EUID == 0 ]] || die 'Run with sudo inside the Linux build environment.'
[[ $driver == latest || $driver =~ ^[0-9]+(\.[0-9]+)*(-[0-9]+)?$ ]] || die 'Invalid driver version.'
image=$(realpath -e -- "$image")
source=$(realpath -e -- "$source")
[[ -f $image && $image == *.img && $(basename "$image") != *-nvidia*.img ]] || die 'Use the original unpacked recovery .img.'
[[ -r $source && -f $source ]] || die 'Updater source configuration is missing.'
output="${image%.img}-nvidia-usbinstall.img"
partial="${output%.img}.partial.img"
[[ ! -e $output && ! -e $output.sha256 ]] || die "Output already exists: $output. Move it aside before starting."
[[ ! -e $partial ]] || die "An unfinished image from an earlier build is in the way: $partial. It is not flashable. Delete it before starting."
[[ -n $work ]] || work="$(dirname "$image")/complete-build-$(date +%Y%m%d-%H%M%S)"
work=$(realpath -m -- "$work")
[[ ! -e $work && -d $(dirname "$work") ]] || die 'Work directory must be new and its parent must exist.'
[[ $work != *[:,\\]* && $work != *$'\n'* ]] || die 'Work path cannot contain commas, colons, backslashes or newlines.'
for tool in unshare flock chroot mount umount mountpoint losetup blkid findmnt rsync python3 tee sha256sum; do
  command -v "$tool" >/dev/null || die "Missing host tool: $tool"
done
if [[ ${STEAMOS_COMPLETE_NAMESPACE:-0} != 1 ]]; then
  exec unshare --mount --pid --fork --kill-child --mount-proc --propagation private -- env STEAMOS_COMPLETE_NAMESPACE=1 bash "$0" "${original[@]}"
fi
exec 8>/run/steamos-nvidia-complete.lock
flock -n 8 || die 'Another complete build is running.'
bash "$repo/tools/check-build-host.sh" "$image" "$(dirname "$work")"
free=$(df -Pm "$(dirname "$work")" | awk 'END {print $4}')
(( free >= 51200 )) || die 'Allow at least 50 GiB free for compilation, plus the input image and output filesystem space.'
# Check JSON without executing configuration content. The installer validates its schema.
python3 - "$source" <<'PY'
import json,sys
with open(sys.argv[1]) as f: value=json.load(f)
assert isinstance(value,dict) and value.get('public_key') and value.get('release_api'), 'Invalid updater source'
PY
# The installer's own preconditions, before hours of compiling. The artifact
# directories do not exist yet, so they are deliberately not passed.
printf 'Checking installer preconditions before compiling.\n'
bash "$repo/steamos-nvidia-installer.sh" --preflight \
  --driver "$driver" "${trim[@]}" \
  --installer-update-source "$source" "$image"
mkdir -- "$work"
exec > >(tee "$work/build.log") 2>&1
printf 'Work directory: %s\nInput: %s\n' "$work" "$image"
lower="$work/lower" root="$work/root" loop=''
cleanup_mounts() {
  local ok=0
  # pacman-key can leave gpg-agent running with devices open inside the chroot.
  # Stop only processes whose root is our owned build root, never host services.
  python3 - "$root" <<'STOP'
import os,signal,sys,time
from pathlib import Path
root=sys.argv[1]
def owned():
    result=[]
    for p in Path('/proc').glob('[0-9]*/root'):
        try:
            if os.readlink(p)==root: result.append(int(p.parent.name))
        except OSError: pass
    return result
for sig in (signal.SIGTERM, signal.SIGKILL):
    for pid in owned():
        try: os.kill(pid,sig)
        except ProcessLookupError: pass
    for _ in range(30):
        if not owned(): break
        time.sleep(0.1)
if owned(): raise SystemExit('Build-root processes did not stop')
STOP
  if mountpoint -q "$root"; then umount -R "$root" || ok=1; fi
  if mountpoint -q "$lower"; then umount "$lower" || ok=1; fi
  if [[ -n $loop && $ok == 0 ]]; then losetup -d "$loop" && loop='' || ok=1; fi
  return "$ok"
}
finish() {
  local rc=$?
  trap - EXIT
  if ! cleanup_mounts; then echo "Mount cleanup failed. Keep $work intact for inspection." >&2; rc=1; fi
  if (( rc )); then echo "Build failed. Preserve $work/build.log; do not flash an incomplete output." >&2; fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$lower" "$root" "$work/upper" "$work/overlay-work" "$work/artifacts"
loop=$(losetup --read-only --find --show --partscan "$image")
partition=''
for part in "$loop"p*; do
  if [[ $(blkid -p -s PART_ENTRY_NAME -o value "$part" 2>/dev/null || true) == rootfs-A ]]; then partition=$part; break; fi
done
[[ -n $partition ]] || die 'Recovery image has no rootfs-A partition.'
[[ $(blkid -p -s TYPE -o value "$partition") == btrfs ]] || die 'Expected a Btrfs SteamOS recovery root.'
mount -o ro,rescue=nologreplay "$partition" "$lower"
# Read metadata, never source shell content from an external image.
grep -Eq '^ID="?steamos"?$' "$lower/etc/os-release" || die 'Not a SteamOS recovery image.'
grep -Eq '^VERSION_ID="?3\.8\.14"?$' "$lower/etc/os-release" || die 'Complete build currently requires SteamOS 3.8.14; other baselines need validation.'
mount -t overlay overlay -o "index=off,lowerdir=$lower,upperdir=$work/upper,workdir=$work/overlay-work" "$root"
mount -t proc proc "$root/proc"
mount --rbind /dev "$root/dev"
mount --make-rslave "$root/dev"
mount --rbind /sys "$root/sys"
mount --make-rslave "$root/sys"
# Give the build chroot a resolver that really has a nameserver. A host that
# resolves through systemd-resolved's NSS module leaves /etc/resolv.conf as the
# stock comment-only file. The chroot has no such module, so the build would
# fail much later inside pacman with "Could not resolve host". The uplink file
# is tried before the stub file, because the stub listener can be turned off.
set_chroot_resolver() {
  local root="$1" candidate
  shift
  for candidate in "$@"; do
    [[ -r "$candidate" ]] || continue
    grep -Eq '^[[:space:]]*nameserver[[:space:]]+[^[:space:]#]' "$candidate" || continue
    rm -f -- "$root/etc/resolv.conf"          # whiteout in upper only
    cp -L -- "$candidate" "$root/etc/resolv.conf" || return 1
    printf 'Build chroot resolver: %s\n' "$candidate" >&2
    return 0
  done
  return 1
}
set_chroot_resolver "$root" \
  /etc/resolv.conf /run/systemd/resolve/resolv.conf /run/systemd/resolve/stub-resolv.conf \
  || die 'No resolv.conf on this build host has a nameserver line, so the build chroot cannot resolve names. Point /etc/resolv.conf at /run/systemd/resolve/stub-resolv.conf, or write a nameserver line into it, then start the build again.'
# Never disable package signatures. Initialize a private keyring in the overlay.
chroot "$root" pacman-key --init
chroot "$root" pacman-key --populate
chroot "$root" pacman -Sy --noconfirm
for component in mangoapp gamescope remote-play nvenc; do
  printf '\nBuilding %s. Compilation can remain quiet for several minutes.\n' "$component"
  bash "$repo/tools/build-$component.sh" "$root" "$work/artifacts/$component"
done
cleanup_mounts || die 'Cannot detach the artifact build environment; image build not started.'
# Only discard our new, now-unmounted compilation layer after all artifacts succeeded.
rm -rf -- "$work/upper" "$work/overlay-work"
printf '\nAll artifacts built. Starting installer image build.\n'
bash "$repo/steamos-nvidia-installer.sh" \
  --driver "$driver" "${trim[@]}" --workdir "$work/driver-cache" \
  --mangoapp-dir "$work/artifacts/mangoapp" \
  --gamescope-dir "$work/artifacts/gamescope" \
  --remote-play-dir "$work/artifacts/remote-play" \
  --nvenc-dir "$work/artifacts/nvenc" \
  --installer-update-source "$source" "$image"
[[ -s $output ]] || die 'Builder returned without an output image.'
(cd -- "$(dirname "$output")" && sha256sum -- "$(basename "$output")") > "$output.sha256"
printf '\nComplete: %s\nChecksum: %s.sha256\nArtifacts and log: %s\n' "$output" "$output" "$work"

