#!/bin/bash
set -euo pipefail

# Build the upstream NVIDIA SteamOS image with xpadneo v0.10.4 added.
# The upstream installer is copied to a temporary file and patched in-memory;
# the checked-in steamos-nvidia-installer.sh is never modified.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$ROOT_DIR/steamos-nvidia-installer.sh"
XPADNEO_VERSION="v0.10.4"

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -f "$UPSTREAM" ]] || { echo "Missing $UPSTREAM" >&2; exit 1; }
[[ $# -eq 1 ]] || { echo "Usage: $0 steamdeck-oobe-repair-<version>.img" >&2; exit 1; }
IMG="$(realpath "$1")"
[[ -f "$IMG" ]] || { echo "Image not found: $IMG" >&2; exit 1; }

PATCHED="$ROOT_DIR/.steamos-nvidia-installer-xpadneo.tmp.sh"
trap 'rm -f "$PATCHED"' EXIT
cp "$UPSTREAM" "$PATCHED"
chmod 755 "$PATCHED"

python3 - "$PATCHED" "$XPADNEO_VERSION" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
s = path.read_text()
needle = 'log "Built nvidia-open $NVIDIA_VER for $KVER"\n'
if needle not in s:
    raise SystemExit("ERROR: upstream installer changed; xpadneo insertion point was not found")
if "XPADNEO_VERSION=" in s:
    raise SystemExit("ERROR: temporary installer already contains xpadneo")

block = r'''log "Built nvidia-open $NVIDIA_VER for $KVER"

# --------------------------------------------------------------- xpadneo
# Xbox One/Series X|S Bluetooth LE controller support.
# xpadneo v0.10.4 is built against the exact SteamOS kernel in this image.
XPADNEO_VERSION="v0.10.4"
XPADNEO_SRC="$WORKDIR/xpadneo"

log "Preparing xpadneo $XPADNEO_VERSION for Xbox Series X|S Bluetooth"

rm -rf "$XPADNEO_SRC"
git clone --depth 1 --branch "$XPADNEO_VERSION" \
  https://github.com/atar-axis/xpadneo.git "$XPADNEO_SRC" \
  || die "Failed to clone xpadneo $XPADNEO_VERSION"

rm -rf "$MERGED/tmp/xpadneo"
cp -a "$XPADNEO_SRC" "$MERGED/tmp/xpadneo"

# xpadneo v0.10 uses a make-based DKMS installer. Supplying VERSION avoids
# the installer's git describe requirement inside the SteamOS chroot.
log "Installing xpadneo DKMS source"
in_chroot "cd /tmp/xpadneo && make VERSION='$XPADNEO_VERSION' install" \
  || die "xpadneo make install failed"

log "Building xpadneo for kernel $KVER"
in_chroot "dkms build 'hid-xpadneo/$XPADNEO_VERSION' -k '$KVER'" \
  || die "xpadneo DKMS build failed for $KVER"

log "Installing xpadneo for kernel $KVER"
in_chroot "dkms install 'hid-xpadneo/$XPADNEO_VERSION' -k '$KVER' --force" \
  || die "xpadneo DKMS install failed for $KVER"

# DKMS installs xpadneo under kernel/drivers/hid. The NVIDIA installer only
# copies the updates tree into the final rootfs, so copy the compiled module
# into that tree as well and let depmod rebuild the module index later.
mkdir -p "$UPPER/usr/lib/modules/$KVER/updates/dkms"
XPADNEO_MODULE="$(find "$MERGED/usr/lib/modules/$KVER" -type f \( -name 'hid-xpadneo.ko' -o -name 'hid-xpadneo.ko.zst' -o -name 'hid-xpadneo.ko.xz' -o -name 'hid-xpadneo.ko.gz' \) -print -quit)"
[[ -n "$XPADNEO_MODULE" ]] || die "xpadneo module was not found after DKMS install"
cp -a "$XPADNEO_MODULE" "$UPPER/usr/lib/modules/$KVER/updates/dkms/"

# xpadneo's own rules/config are needed in the final SteamOS rootfs.
mkdir -p "$MNT/etc/modules-load.d" "$MNT/etc/modprobe.d"
cat > "$MNT/etc/modules-load.d/xpadneo.conf" <<'XPADNEO_MODULES'
hid_xpadneo
XPADNEO_MODULES

cat > "$MNT/etc/modprobe.d/99-xpadneo.conf" <<'XPADNEO_MODPROBE'
# Added by steamos-nvidia-installer xpadneo integration
softdep hid_xpadneo pre: uhid
XPADNEO_MODPROBE

# xpadneo installs udev rules under /etc/udev/rules.d. Copy them explicitly
# because the upstream installer copies the NVIDIA payload selectively.
for f in 60-xpadneo.rules 70-xpadneo-disable-hidraw.rules; do
  if [[ -f "$MERGED/etc/udev/rules.d/$f" ]]; then
    mkdir -p "$MNT/etc/udev/rules.d"
    cp -a "$MERGED/etc/udev/rules.d/$f" "$MNT/etc/udev/rules.d/"
  elif [[ -f "$MERGED/usr/lib/udev/rules.d/$f" ]]; then
    mkdir -p "$MNT/usr/lib/udev/rules.d"
    cp -a "$MERGED/usr/lib/udev/rules.d/$f" "$MNT/usr/lib/udev/rules.d/"
  fi
done

# Xbox Series X|S BLE workaround recommended by xpadneo documentation.
# It prevents the exact "connected/rumble works but no input" class of
# problems seen with modern Xbox BLE controllers on BlueZ.
if [[ -f "$MNT/etc/bluetooth/main.conf" ]]; then
  if ! grep -q '^\[LE\]' "$MNT/etc/bluetooth/main.conf"; then
    cat >> "$MNT/etc/bluetooth/main.conf" <<'XPADNEO_BT'

[LE]
MinConnectionInterval=7
MaxConnectionInterval=9
ConnectionLatency=0
XPADNEO_BT
  fi
else
  mkdir -p "$MNT/etc/bluetooth"
  cat > "$MNT/etc/bluetooth/main.conf" <<'XPADNEO_BT'
[General]
ControllerMode=dual
JustWorksRepairing=confirm

[LE]
MinConnectionInterval=7
MaxConnectionInterval=9
ConnectionLatency=0
XPADNEO_BT
fi

if [[ -f "$MNT/etc/bluetooth/input.conf" ]]; then
  if ! grep -q '^UserspaceHID=true' "$MNT/etc/bluetooth/input.conf"; then
    cat >> "$MNT/etc/bluetooth/input.conf" <<'XPADNEO_INPUT'

[General]
UserspaceHID=true
ClassicBondedOnly=false
LEAutoSecurity=false
XPADNEO_INPUT
  fi
else
  cat > "$MNT/etc/bluetooth/input.conf" <<'XPADNEO_INPUT'
[General]
UserspaceHID=true
ClassicBondedOnly=false
LEAutoSecurity=false
XPADNEO_INPUT
fi

in_chroot "depmod -a '$KVER'"
log "xpadneo $XPADNEO_VERSION built successfully for $KVER"

'''
path.write_text(s.replace(needle, block, 1))
PY

bash -n "$PATCHED"

# xpadneo needs git in the build chroot. The upstream NVIDIA script already
# has the host-side git requirement, but its chroot package list does not.
# Add git to the temporary script's build-only package installation.
python3 - "$PATCHED" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
s=s.replace('in_chroot "pacman --config $PACCONF -S $PACOPTS dkms"',
            'in_chroot "pacman --config $PACCONF -S $PACOPTS dkms git"', 1)
s=s.replace("BUILD_ONLY_RE='^(dkms|nvidia-open-dkms|patch|gcc|gcc-libs|make|binutils|libisl|libmpc|mpfr|pahole|python-setuptools|linux-neptune.*-headers|.*-headers)$'",
            "BUILD_ONLY_RE='^(dkms|git|nvidia-open-dkms|patch|gcc|gcc-libs|make|binutils|libisl|libmpc|mpfr|pahole|python-setuptools|linux-neptune.*-headers|.*-headers)$'", 1)
p.write_text(s)
PY

bash -n "$PATCHED"

exec "$PATCHED" "$IMG"
