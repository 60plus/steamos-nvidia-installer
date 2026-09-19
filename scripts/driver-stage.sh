#!/bin/bash
# Clone the immutable root, then let Valve migrate settings and boot artifacts.
set -euo pipefail
[[ $EUID -eq 0 && $# -eq 0 ]] || exit 2
source /usr/lib/steamos-nvidia/pc-support.sh
# Called in a private mount namespace. Reading /etc must not update atime on
# the /var-backed overlay while Valve temporarily freezes /var for copying.
mount -o remount,bind,noatime /var
mount -o remount,bind,noatime /etc
request=/run/steamos-nvidia-driver
[[ -f $request/request.json ]] || exit 1
self=$(steamos-bootconf this-image)
case "$self" in A) other=B;; B) other=A;; *) exit 1;; esac
[[ $(steamos-bootconf selected-image) == "$self" ]] || exit 1
[[ $(btrfs property get / ro) == ro=true ]] || { echo 'The running root must be read-only.' >&2; exit 1; }
source_dev=$(readlink -f /dev/disk/by-partsets/self/rootfs)
target_dev=$(readlink -f /dev/disk/by-partsets/other/rootfs)
active_id=$(pc_active_root_id)
[[ -b $source_dev && -b $target_dev && $(lsblk -dn -o MAJ:MIN "$source_dev") == "$active_id" ]] || exit 1
[[ $(lsblk -dn -o MAJ:MIN "$target_dev") != "$active_id" ]] || exit 1
declare -A seen=()
for part in rootfs var efi; do
    src=$(readlink -f "/dev/disk/by-partsets/self/$part")
    dst=$(readlink -f "/dev/disk/by-partsets/other/$part")
    [[ -b $src && -b $dst && "$src" != "$dst" ]] || exit 1
    [[ $(lsblk -dn -o PARTLABEL "$src") == "$part-$self" ]] || exit 1
    [[ $(lsblk -dn -o PARTLABEL "$dst") == "$part-$other" ]] || exit 1
    for device in "$src" "$dst"; do
        id=$(lsblk -dn -o MAJ:MIN "$device")
        [[ -n $id && ! ${seen[$id]+present} ]] || exit 1
        seen[$id]=1
    done
    [[ $(readlink -f "/dev/disk/by-partsets/$other/$part") == "$dst" ]] || exit 1
    if findmnt -rn -S "$dst" >/dev/null; then
        echo "Inactive $part is mounted; refusing to overwrite it." >&2
        exit 1
    fi
done
bytes=$(blockdev --getsize64 "$target_dev")
[[ $bytes =~ ^[0-9]+$ && $bytes -ge 2147483648 ]] || exit 1
pc_require_space /home "$((bytes / 1048576 + 12288))"
# Mask RAUC activation during this operation. Never remove a pre-existing mask.
[[ $(systemctl show rauc.service -p UnitFileState --value) != masked* ]] || exit 1
[[ $(busctl get-property de.pengutronix.rauc / de.pengutronix.rauc.Installer Operation) == 's "idle"' ]] || exit 1
masked=0
work=
image=
loop=
watchdog=
cleanup() {
    set +e
    [[ -z $watchdog ]] || kill "$watchdog" 2>/dev/null
    fsfreeze -u /var 2>/dev/null
    if [[ -n $work ]]; then
        mountpoint -q "$work/target" && umount "$work/target"
        mountpoint -q "$work/source" && umount "$work/source"
    fi
    [[ -z $loop ]] || losetup -d "$loop"
    [[ -z $image ]] || rm -f -- "$image"
    [[ -z $work ]] || rmdir "$work/target" "$work/source" "$work"
    [[ $masked -eq 0 ]] || systemctl unmask --runtime rauc.service
}
trap cleanup EXIT
systemctl mask --runtime rauc.service
masked=1
systemctl stop rauc.service
work=$(mktemp -d /run/driver-stage.XXXXXX)
mkdir "$work/source" "$work/target"
image=$(mktemp /home/.steamos-driver-root.XXXXXX.img)
truncate -s "$bytes" "$image"
mkfs.btrfs -q -f -L "rootfs-$other" "$image"
loop=$(losetup --find --show "$image")
mount -o ro "$source_dev" "$work/source"
mount -o compress-force=zstd:3 "$loop" "$work/target"
rsync -aHAXx --numeric-ids "$work/source/" "$work/target/"
pc_require_space "$work/target" 256
[[ $(blkid -s UUID -o value "$loop") != $(blkid -s UUID -o value "$source_dev") ]] || exit 1
/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-change.py check-target "$work/target"
# A new transaction must not reuse the source slot completion marker.
rm -f "$work/target/usr/lib/steamos-nvidia/complete"
btrfs filesystem sync "$work/target"
btrfs property set "$work/target" ro true
umount "$work/target"
umount "$work/source"
losetup -d "$loop"
loop=
steamos-bootconf --image "$other" config --no-create --set image-invalid 1 --set boot-requested-at 0 --set boot-attempts 0
sync -f /esp/SteamOS/conf
# The active root and its EFI/var are never overwritten.
dd if="$image" of="$target_dev" bs=16M conv=fsync status=progress
rm -f -- "$image"
image=
# This is the existing Valve migration, including the shared NVIDIA repair hook.
# No old casync seed index is valid for our freshly generated root filesystem.
export RAUC_UPDATE_SOURCE="$request" RAUC_BUNDLE_MOUNT_POINT="$request"
# Bound a stuck migration freeze; an early thaw makes Valve fail before activation.
(sleep 120; fsfreeze -u /var 2>/dev/null || true) &
watchdog=$!
/usr/lib/rauc/post-install.sh
kill "$watchdog" 2>/dev/null || true
watchdog=
