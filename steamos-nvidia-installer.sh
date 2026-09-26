#!/bin/bash
#
# steamos-nvidia-installer.sh - turn a CLEAN SteamOS OOBE repair image into a
# one-click USB installer with NVIDIA (RTX) driver support baked in.
#
# Installs the nvidia-open driver version this project has tested, recorded in
# config/build-baselines.json (Valve's own mirror only pins an older 575.x) - or
# any branch you name with --driver, including "latest" for whatever Arch ships
# today. The version is resolved once at build time, pinned to
# permanent archive.archlinux.org URLs, and the on-device self-heal repatch
# reuses those exact packages, so the installed system stays on one known
# driver even across OS updates. Safety: NVIDIA's userspace
# blobs target ancient glibc, and the Arch-compiled helpers the newer
# drivers need (egl-wayland2) are small - but the build still extracts every
# downloaded package and verifies no binary needs a newer glibc than the
# image ships (frozen SteamOS 3.8 = glibc 2.41; current Arch = 2.43, so
# blind installs of Arch-compiled libs are NOT safe in general).
#
#   sudo ./steamos-nvidia-installer.sh steamdeck-oobe-repair-<ver>.img
#
# Output: <image>-nvidia-usbinstall.img  →  dd to a USB stick, boot it on the
# target machine (UEFI, Secure Boot off), double-click
# "Install SteamOS (NVIDIA) to Hard Drive", pick a disk, done.
# The input image is copied first and never modified.
#
# What it does, in one pass over one copy:
#   1. Builds nvidia-open (DKMS) against the image's exact neptune kernel in
#      a throwaway overlayfs chroot, using Valve's frozen Arch mirror - the
#      toolchain/headers never enter the image. Copies only the driver
#      payload (modules, nvidia-utils, lib32, egl-*, GSP firmware) into the
#      rootfs and registers it in the pacman db.
#   2. Blacklists nouveau + enables nvidia-drm KMS via modprobe.d AND the
#      kernel cmdline (grub.cfg on the efi partition + /etc/default/grub -
#      the latter is what the installed system's regenerated grub uses).
#   3. Makes OS updates SELF-HEALING (default): updating from within Steam
#      works - Valve's updater stages the new OS in the spare A/B slot as
#      usual, then a shared RAUC completion hook rebuilds the NVIDIA
#      driver for the new OS (in a chroot on the new slot, from that
#      version's own repo branch) before the reboot prompt appears. If the
#      rebuild fails, the update is cancelled and the machine keeps booting
#      the current working system. Alternatives: --hold-updates makes Steam
#      always report "up to date" (old behaviour), --no-hold-updates leaves
#      stock update behaviour (an OS update then removes the driver!).
#   4. Adds the one-click installer: Valve's own repair_device.sh (which
#      installs by CLONING the running system, so the driver propagates)
#      patched for generic hardware - target-disk override, /dev/sdX
#      partition-suffix autodetect, NVMe-sanitize skipped on non-NVMe and
#      tolerated when a drive doesn't implement it -
#      plus a zenity disk-picker wrapper, a desktop icon, and a limited
#      passwordless installer command for deck. Ordinary sudo requires the
#      account password.
#
# Options:
#   --driver SPEC      Which NVIDIA driver to install. "latest" (default) =
#                      whatever current Arch ships. Otherwise a branch or
#                      version prefix - 580, 580.105.08, 580.105.08-4 - and
#                      the newest matching build is taken from the Arch
#                      archive. SteamOS itself ships 575.x; nvidia-open
#                      needs Turing (RTX 20xx) or newer whichever you pick.
#   --hold-updates     Hard-hold OS updates instead of self-healing (Steam
#                      always shows "up to date").
#   --no-hold-updates  Stock update behaviour - DANGER: an OS update boots an
#                      unpatched system (A/B fallback saves you, driver lost).
#   --no-installer     Skip step 4 (produce a plain bootable patched OS).
#   --trim-cuda        Drop CUDA/OpenCL/NVVM/OptiX libs (~350 MB smaller).
#   --skip-sigcheck    Disable pacman signature checks in the build chroot.
#   --preflight        Run every host-side check and exit without building.
#   --workdir DIR      Build dir (~3 GB; default: alongside the output).
#                      Kept between runs - caches the driver build.
#
# Host needs: Linux with losetup, btrfs-progs, rsync, curl, kmod, zstd,
# python3 and readelf (binutils). Package queries use the image's own pacman
# through chroot, so the host does not need pacman.
# Notes: nvidia-open = RTX 20xx+ (Turing) only. Target machines need UEFI +
# Secure Boot off. First boot of an installed system lands in the gamescope
# Steam setup; if it black-screens: Ctrl+Alt+F3 → steamos-session-select plasma.

set -euo pipefail

# ---------------------------------------------------------------- helpers
log()  { printf '\e[1;35m[nvidia-usb]\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m[warn]\e[0m %s\n' "$*" >&2; }
die()  { printf '\e[1;31m[fail]\e[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------- args
UPDATE_MODE=selfheal   # selfheal | hold | stock
ADD_INSTALLER=1
TRIM_CUDA=0
SKIP_SIG=0
PREFLIGHT=0
# Default to the driver this project has tested, not to whatever Arch shipped
# today. "latest" remains available through --driver for a deliberate choice.
# A copy of the script without the repository keeps the old behaviour and says so.
DRIVER_SPEC="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['tested_driver'])" \
  "$(dirname "$(realpath "$0")")/config/build-baselines.json" 2>/dev/null)" || DRIVER_SPEC=""
if [[ -z "$DRIVER_SPEC" ]]; then
  DRIVER_SPEC=latest   # latest | <branch or version prefix, e.g. 580>
  printf '\e[1;33m[warn]\e[0m config/build-baselines.json is unavailable; building with --driver latest, which this project has not tested.\n' >&2
fi
WORKDIR=""
INSTALLER_UPDATE_SOURCE=""
MANGOAPP_DIR=""
GAMESCOPE_DIR=""
REMOTE_PLAY_DIR=""
NVENC_DIR=""
IMG=""
ADD_XPADNEO=0
EXPERIMENTAL_BETA=0
EXPERIMENTAL_PREVIEW=0
XPADNEO_VERSION=v0.10.4

ORIGINAL_ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --driver)          DRIVER_SPEC="${2:?--driver needs an argument}"; shift ;;
    --experimental-beta) EXPERIMENTAL_BETA=1 ;;
    --experimental-preview) EXPERIMENTAL_PREVIEW=1 ;;
    --hold-updates)    UPDATE_MODE=hold ;;
    --no-hold-updates) UPDATE_MODE=stock ;;
    --no-installer)    ADD_INSTALLER=0 ;;
    --trim-cuda)       TRIM_CUDA=1 ;;
    --xpadneo)         ADD_XPADNEO=1 ;;
    --no-xpadneo)      ADD_XPADNEO=0 ;;
    --xpadneo-version) XPADNEO_VERSION="${2:?--xpadneo-version needs an argument}"; ADD_XPADNEO=1; shift ;;
    --skip-sigcheck)   SKIP_SIG=1 ;;
    --preflight)       PREFLIGHT=1 ;;
    --nvenc-dir) NVENC_DIR="${2:?--nvenc-dir needs an artifact directory}"; shift ;;
    --remote-play-dir) REMOTE_PLAY_DIR="${2:?--remote-play-dir needs an artifact directory}"; shift ;;
    --gamescope-dir) GAMESCOPE_DIR="${2:?--gamescope-dir needs an artifact directory}"; shift ;;
    --mangoapp-dir) MANGOAPP_DIR="${2:?--mangoapp-dir needs an artifact directory}"; shift ;;
    --installer-update-source) INSTALLER_UPDATE_SOURCE="${2:?--installer-update-source needs a JSON source file}"; shift ;;
    --workdir)         WORKDIR="${2:?--workdir needs an argument}"; shift ;;
    -h|--help)
      sed -n '2,/^$/{/^#/p}' "$0" | sed 's/^# \{0,1\}//'
      printf '\n  --experimental-beta  Select SteamOS beta for this test image (selfheal only).\n'
      printf '  --experimental-preview  Select SteamOS Preview for this test image (selfheal only).\n'
      printf '\n  --nvenc-dir DIR  Include experimental 32-bit VAAPI to NVENC bridge.\n'
      printf '\n  --remote-play-dir DIR  Include experimental SDR Remote Play receiver.\n'
      printf '\n  --gamescope-dir DIR  Include experimental stable capture correction.\n'
      printf '\n  --mangoapp-dir DIR  Include the corrected MangoApp artifact.\n'
      printf '\n  --installer-update-source FILE  Configure signed installer release updates.\n'
      printf '\n  --xpadneo          Include optional xpadneo and rebuild it on OS updates.\n'
      printf '  --no-xpadneo       Build without xpadneo (default).\n'
      printf '  --xpadneo-version  Exact tag, such as v0.10.4 (also enables xpadneo).\n'
      exit 0 ;;
    -*)                die "Unknown option: $1" ;;
    *)                 [[ -z "$IMG" ]] || die "Only one input image is allowed"; IMG="$1" ;;
  esac
  shift
done

[[ "$XPADNEO_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Use an exact xpadneo tag such as v0.10.4"
[[ $EXPERIMENTAL_BETA == 0 || $EXPERIMENTAL_PREVIEW == 0 ]] || die "Choose one experimental channel"
[[ $EXPERIMENTAL_PREVIEW == 0 || $UPDATE_MODE == selfheal ]] || die "Experimental Preview requires self-healing updates"
[[ $EXPERIMENTAL_BETA == 0 || $UPDATE_MODE == selfheal ]] || die "Experimental beta requires self-healing updates"
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
[[ -f "$SCRIPT_DIR/VERSION" ]] || die "Missing VERSION. Clone the full repository before building."
INSTALLER_VERSION="$(cat "$SCRIPT_DIR/VERSION")"
[[ "$INSTALLER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+([.-][0-9A-Za-z]+)*)?$ ]] \
  || die "Invalid installer VERSION"
log "Installer version: $INSTALLER_VERSION"
[[ -f "$SCRIPT_DIR/scripts/notification-renderer.py" && -f "$SCRIPT_DIR/lib/pc-support.sh" && -f "$SCRIPT_DIR/scripts/steamos-nvidia-diagnostics" && -f "$SCRIPT_DIR/scripts/hdr-defaults.py" && -f "$SCRIPT_DIR/scripts/safe-graphics.py" && -f "$SCRIPT_DIR/scripts/bluetooth-resume.py" && -f "$SCRIPT_DIR/scripts/install-target.py" && -f "$SCRIPT_DIR/scripts/patch-repair.py" ]] \
  || die "Missing support files. Clone the full repository before building."
# shellcheck source=lib/pc-support.sh
source "$SCRIPT_DIR/lib/pc-support.sh"
[[ $EUID -eq 0 ]] || die "Run as root (sudo)."
if [[ -n "$NVENC_DIR" ]]; then
  NVENC_DIR="$(realpath "$NVENC_DIR")"
  [[ -r "$NVENC_DIR/nvenc-build.json" ]] || die "Incomplete NVENC artifact"
fi
if [[ -n "$REMOTE_PLAY_DIR" ]]; then
  REMOTE_PLAY_DIR="$(realpath "$REMOTE_PLAY_DIR")"
  [[ -r "$REMOTE_PLAY_DIR/remote-play-build.json" ]] || die "Incomplete Remote Play artifact"
fi
if [[ -n "$GAMESCOPE_DIR" ]]; then
  # The artifact is carried, never forced. pc_install_gamescope selects it only
  # when the running SteamOS and its Gamescope package both match, and returns
  # to Valve's build on anything else, so a beta or Preview image can hold it
  # without using it. That selection is proven in both directions on hardware.
  GAMESCOPE_DIR="$(realpath "$GAMESCOPE_DIR")"
  for file in root/usr/bin/gamescope gamescope-build.json Gamescope-LICENSE; do
    [[ -r "$GAMESCOPE_DIR/$file" ]] || die "Incomplete Gamescope artifact"
  done
fi
if [[ -n "$MANGOAPP_DIR" ]]; then
  MANGOAPP_DIR="$(realpath "$MANGOAPP_DIR")"
  for file in mangoapp mangoapp-build.json MangoHud-LICENSE; do
    [[ -r "$MANGOAPP_DIR/$file" ]] || die "Incomplete MangoApp artifact"
  done
fi
if [[ -n "$INSTALLER_UPDATE_SOURCE" ]]; then
  [[ $UPDATE_MODE == selfheal && -r "$INSTALLER_UPDATE_SOURCE" ]] || die "Installer updates need self-healing mode and a readable source file"
  INSTALLER_UPDATE_SOURCE="$(realpath "$INSTALLER_UPDATE_SOURCE")"
fi
# Keep build mounts out of udev and other host service namespaces.
# Otherwise lazy unmounts can leave the output image open after completion.
# A PID namespace also removes chroot daemons before the caller can hash the image.
if [[ "${STEAMOS_NVIDIA_PRIVATE_MOUNTS:-0}" != 1 ]]; then
  command -v unshare >/dev/null || die "Missing host tool: unshare"
  exec unshare --mount --pid --fork --kill-child --mount-proc --propagation private -- env STEAMOS_NVIDIA_PRIVATE_MOUNTS=1 bash "$0" "${ORIGINAL_ARGS[@]}"
fi
[[ "$DRIVER_SPEC" == latest || "$DRIVER_SPEC" =~ ^[0-9]+(\.[0-9]+)*(-[0-9]+)?$ ]] \
  || die "--driver takes 'latest' or a version prefix like 580 / 580.105.08 / 580.105.08-4"
if [[ -z "$IMG" ]]; then
  # No image given - look for exactly one clean repair image next to the script.
  script_dir="$(dirname "$(realpath "$0")")"
  mapfile -t candidates < <(find "$script_dir" -maxdepth 1 -name '*.img' ! -name '*-nvidia*.img' | sort)
  case ${#candidates[@]} in
    0) die "No image given and no *.img found in $script_dir. Usage: $0 [options] <clean-oobe-repair.img>" ;;
    1) IMG="${candidates[0]}"; log "Auto-detected image: $IMG" ;;
    *) die "Multiple images in $script_dir - pass one explicitly:$(printf '\n  %s' "${candidates[@]}")" ;;
  esac
fi
[[ -f "$IMG" ]] || die "Image not found: $IMG"
for tool in losetup blkid btrfs rsync curl depmod sed awk tar zstd python3 readelf modinfo flock sha256sum timeout udevadm; do
  command -v "$tool" >/dev/null || die "Missing host tool: $tool"
done

IMG="$(realpath "$IMG")"
OUT="${IMG%.img}-nvidia-usbinstall.img"
# Build under a name nobody will flash. The final name appears only after a
# successful run, so an interrupted build leaves nothing that looks finished.
PARTIAL="${OUT%.img}.partial.img"
# match the FILENAME only - the containing dir may itself be called
# "steamos-nvidia-installer" (the repo clone), which must not trip this guard
[[ "$(basename "$IMG")" == *-nvidia*.img ]] && die "Input looks like an already-patched image - start from the clean repair image."
exec 9>/run/steamos-nvidia-build.lock
flock -n 9 || die "Another image build is running"
pc_require_space "$(dirname "$OUT")" 20000 || die "Free space check failed"

if [[ $PREFLIGHT == 1 ]]; then
  # Everything above is read-only. Stop here, before the previous output is
  # touched and before the 8 GB copy, so a complete build learns in seconds
  # instead of after an hour of compiling.
  timeout 15 udevadm control --ping >/dev/null 2>&1 \
    || die "udev is not answering; the build needs it to keep desktop automounts off the loop device"
  # Both Arch hosts matter: the archive serves the pinned packages, and the
  # package search resolves anything pinned as "latest", which always includes
  # the companion egl-wayland2.
  pc_curl -fsSIL "https://archive.archlinux.org/packages/n/nvidia-utils/" -o /dev/null \
    || die "archive.archlinux.org is not reachable; the pinned driver packages cannot be fetched"
  pc_curl -fsSL "https://archlinux.org/packages/search/json/?name=nvidia-utils" -o /dev/null \
    || die "archlinux.org is not reachable; a driver version cannot be resolved"
  log "Preflight passed for $IMG. Nothing was built and nothing was changed."
  exit 0
fi
[[ -e "$PARTIAL" ]] && { warn "Removing an unfinished output from an earlier run: $PARTIAL"; rm -f "$PARTIAL"; }
[[ -e "$OUT" ]] && warn "A previous output exists. It is replaced only if this build succeeds: $OUT"

[[ -n "$WORKDIR" ]] || WORKDIR="$(dirname "$OUT")/.nvidia-usb-work"
MNT="$WORKDIR/mnt"          # rootfs mount
EFIMNT="$WORKDIR/efi"       # efi-A mount
HOMEMNT="$WORKDIR/home"     # home mount
UPPER="$WORKDIR/upper"      # overlay upper (build residue, cached)
OVLWORK="$WORKDIR/ovlwork"
MERGED="$WORKDIR/merged"
LOOPDEV=""
UDEV_RULE=/run/udev/rules.d/90-steamos-nvidia-installer.rules

# ---------------------------------------------------------------- cleanup
cleanup() {
  set +e
  for m in "$MERGED"/dev/pts "$MERGED"/dev "$MERGED"/sys "$MERGED"/proc \
           "$MERGED" "$EFIMNT" "$HOMEMNT" "$MNT"; do
    if mountpoint -q "$m" 2>/dev/null; then
      umount -R "$m" 2>/dev/null || umount -Rl "$m" 2>/dev/null
    fi
  done
  # sweep any udisks automounts of OUR loop device only
  if [[ -n "$LOOPDEV" ]]; then
    findmnt -rn -o TARGET,SOURCE | awk -v l="$LOOPDEV" '$2 ~ "^"l {print $1}' \
      | tac | while read -r m; do umount "$m" 2>/dev/null; done
    losetup -d "$LOOPDEV" 2>/dev/null
  fi
  if [[ -f "$UDEV_RULE" ]]; then
    rm -f "$UDEV_RULE"
    udevadm control --reload 2>/dev/null
  fi
}
trap cleanup EXIT

in_chroot() { chroot "$MERGED" /bin/bash -c "$*"; }

mkdir -p "$MNT" "$EFIMNT" "$HOMEMNT" "$UPPER" "$OVLWORK" "$MERGED"

# stale mounts from an interrupted previous run
for m in "$MERGED" "$EFIMNT" "$HOMEMNT" "$MNT"; do
  if mountpoint -q "$m" 2>/dev/null; then
    warn "Stale mount from a previous run at $m - unmounting"
    umount -R "$m" 2>/dev/null || umount -Rl "$m"
  fi
done

# A run killed mid-transaction leaves pacman's lock behind in the cached
# overlay, and the next run then dies with "unable to lock database". The build
# lock above and the PID namespace rule out a pacman that is still alive, so the
# lock is stale. Throw the whole overlay away rather than the lock file alone: a
# transaction stopped part way through can also have left half extracted files
# that no package owns. The package download cache sits outside the overlay and
# is kept.
if [[ -e "$UPPER/usr/lib/holo/pacmandb/db.lck" ]]; then
  warn "A previous package transaction was interrupted - discarding the build overlay, the driver is built again"
  rm -rf "${UPPER:?}" "${OVLWORK:?}"
  mkdir -p "$UPPER" "$OVLWORK"
fi

# keep udisks/desktop automounters away from loop partitions during the run
mkdir -p /run/udev/rules.d
echo 'SUBSYSTEM=="block", KERNEL=="loop*", ENV{UDISKS_IGNORE}="1"' > "$UDEV_RULE"
udevadm control --reload

# ------------------------------------------------------------- copy image
log "Copying image → $PARTIAL (~8 GB)"
cp --reflink=auto "$IMG" "$PARTIAL"

# ------------------------------------------------------------- loop mount
LOOPDEV="$(losetup -f --show -P "$PARTIAL")"
log "Loop device: $LOOPDEV"

ROOTPART="" EFIPART="" HOMEPART=""
for part in "$LOOPDEV"p*; do
  case "$(blkid -p -s PART_ENTRY_NAME -o value "$part" 2>/dev/null)" in
    rootfs-A) ROOTPART="$part" ;;
    efi-A)    EFIPART="$part" ;;
    home)     HOMEPART="$part" ;;
  esac
done
[[ -n "$ROOTPART" && -n "$EFIPART" && -n "$HOMEPART" ]] \
  || die "rootfs-A/efi-A/home partitions not found - is this a SteamOS image?"

FSUUID="$(blkid -p -s UUID -o value "$ROOTPART")"
findmnt -rn -S "UUID=$FSUUID" >/dev/null 2>&1 \
  && die "A filesystem with UUID $FSUUID is already mounted (another copy of this image?). Unmount it first."

log "Mounting rootfs + efi + home"
mount -o compress-force=zstd:3 "$ROOTPART" "$MNT"
mount "$EFIPART" "$EFIMNT"
mount "$HOMEPART" "$HOMEMNT"

if [[ "$(btrfs property get "$MNT" ro)" == "ro=true" ]]; then
  log "Clearing btrfs read-only property"
  btrfs property set "$MNT" ro false
fi

# ------------------------------------------------- discover image details
KVER=""
for d in "$MNT/usr/lib/modules/"*neptune*; do
  [[ -d "$d" ]] && KVER="$(basename "$d")" && break
done
[[ -n "$KVER" ]] || die "No neptune kernel found in image"
log "Image kernel: $KVER"

PACDB="$MNT/usr/lib/holo/pacmandb/local"
KPKG_DIR=""
for d in "$PACDB"/linux-neptune-*-[0-9]*; do
  [[ -d "$d" ]] || continue
  case "$(basename "$d")" in
    *-headers-*|*firmware*|*rtw*) continue ;;
  esac
  KPKG_DIR="$d"; break
done
[[ -n "$KPKG_DIR" ]] || die "Could not find installed kernel package in pacman db"
KPKG_FULL="$(basename "$KPKG_DIR")"
KPKG_NAME="${KPKG_FULL%-*-*}"
KPKG_VERREL="${KPKG_FULL#"$KPKG_NAME"-}"
log "Kernel package: $KPKG_NAME $KPKG_VERREL"

JUPITER_REPO="$(awk -F'[][]' '/^\[jupiter-/{print $2; exit}' "$MNT/etc/pacman.conf")"
[[ -n "$JUPITER_REPO" ]] || die "No jupiter repo in image pacman.conf"
MIRROR="$(awk '/^Server/{print $3; exit}' "$MNT/etc/pacman.d/mirrorlist")"
HDR_URL="${MIRROR/\$repo/$JUPITER_REPO}"
HDR_URL="${HDR_URL/\$arch/x86_64}/${KPKG_NAME}-headers-${KPKG_VERREL}-x86_64.pkg.tar.zst"
pc_curl -fsSIL "$HDR_URL" -o /dev/null \
  || die "Could not access exact-match headers in Valve's pool: $HDR_URL"
log "Headers package: $(basename "$HDR_URL")"

# -------------------------------------------- resolve the driver packages
# The driver set comes from Arch, not Valve's frozen mirror (which pins an
# older 575.x). Default is whatever current Arch ships; --driver <branch>
# takes the newest build of that branch out of the Arch archive instead.
# Either way the resolved URLs are pinned to permanent
# archive.archlinux.org paths (mirror URLs die when Arch bumps the version)
# - the same URLs are recorded in the image for the self-heal repatch.
ARCHIVE_URL=https://archive.archlinux.org/packages

PKG_URLS=""            # pinned URLs, space-separated (also goes in driver.conf)
PKG_URL_ARR=()         # same, indexable alongside PKG_FILES
PKG_FILES=()           # local filenames in $WORKDIR/pkgs
FETCHED=0              # how many of PKG_FILES are already downloaded
DRIVER_VERSION=""      # nvidia-utils pkgver-pkgrel
NV_PKGVER=""           # pkgver only, for cross-package consistency check
PIN_VER=""             # version pin_pkg just resolved

# pin_pkg <pkg> <spec> - resolve one package and add it to the pinned set.
# spec "latest" = what current Arch has (archive URL when it's there yet,
# else the mirror); anything else = newest archived build whose version
# starts with that prefix ("580", "580.105.08", "580.105.08-4").
pin_pkg() {
  local pkg="$1" spec="$2" repo ver file url index
  if [[ "$spec" == latest ]]; then
    read -r ver file repo < <(pc_curl -fsSL "https://archlinux.org/packages/search/json/?name=$pkg" \
      | python3 -c 'import json,sys
r=[p for p in json.load(sys.stdin)["results"]
   if p["repo"] in ("core","extra","multilib") and p["arch"] == "x86_64"]
if not r: raise SystemExit(1)
p=r[0]; print(p["pkgver"]+"-"+str(p["pkgrel"]), p["filename"], p["repo"])') \
      || die "Could not resolve $pkg from archlinux.org"
    url="$ARCHIVE_URL/${pkg:0:1}/$pkg/$file"
    if ! pc_curl -fsSIL "$url" -o /dev/null; then
      url="https://geo.mirror.pkgbuild.com/$repo/os/x86_64/$file"
      pc_curl -fsSIL "$url" -o /dev/null || die "$pkg $ver not on archive.archlinux.org nor the mirror"
      warn "$pkg not yet in the Arch archive - pinning mirror URL (may go stale)"
    fi
  else
    # the archive keeps every build ever released; newest match wins
    index="$(pc_curl -fsSL "$ARCHIVE_URL/${pkg:0:1}/$pkg/")" \
      || die "Could not read the Arch archive index for $pkg; package availability is unknown"
    file="$(printf '%s\n' "$index" | pc_package_matches "$pkg" "$spec" | sort -uV | tail -1 || true)"
    [[ -n "$file" ]] || die "Archive index loaded, but no $pkg build matches '$spec'"
    ver="${file#"$pkg"-}"; ver="${ver%-x86_64.pkg.tar.zst}"
    url="$ARCHIVE_URL/${pkg:0:1}/$pkg/$file"
  fi
  PKG_URLS+="${PKG_URLS:+ }$url"
  PKG_URL_ARR+=("$url")
  PKG_FILES+=("$file")
  PIN_VER="$ver"
  log "  $pkg $ver"
}

# fetch_pins - download whatever pin_pkg has added since the last call
fetch_pins() {
  mkdir -p "$WORKDIR/pkgs"
  local i f
  for (( i=FETCHED; i<${#PKG_FILES[@]}; i++ )); do
    f="${PKG_FILES[$i]}"
    if [[ -s "$WORKDIR/pkgs/$f" ]]; then
      log "Cached: $f"
    else
      log "Downloading $f"
      pc_download_package "${PKG_URL_ARR[$i]}" "$WORKDIR/pkgs/$f" \
        || die "Could not download pinned package $f; no alternate version will be selected"
    fi
  done
  FETCHED=${#PKG_FILES[@]}
}

log "Resolving NVIDIA driver packages from Arch Linux (--driver $DRIVER_SPEC)"
pin_pkg nvidia-utils "$DRIVER_SPEC"
DRIVER_VERSION="$PIN_VER"; NV_PKGVER="${PIN_VER%-*}"
log "Driver pinned: nvidia-open $DRIVER_VERSION"

# Fetch nvidia-utils first: its own dependency list decides which support
# packages have to come from Arch too - egl-wayland2 only became a
# dependency at 590, so pulling it in for older branches would be wrong.
fetch_pins

# Module source + 32-bit userspace must match nvidia-utils exactly. On
# "latest" they're resolved the same way (a just-bumped package may not be
# in the archive yet); the skew check catches a mirror caught mid-bump.
COMPANION_SPEC="$DRIVER_SPEC"
[[ "$COMPANION_SPEC" == latest ]] || COMPANION_SPEC="$NV_PKGVER"
for pkg in nvidia-open-dkms lib32-nvidia-utils; do
  pin_pkg "$pkg" "$COMPANION_SPEC"
  [[ "$PIN_VER" == "$NV_PKGVER"-* ]] \
    || die "Version skew: $pkg is $PIN_VER but nvidia-utils is $DRIVER_VERSION (mirror mid-update?) - retry in an hour"
done

# ...plus the support packages Valve's frozen repo doesn't carry at all, so
# they can only come from Arch. Every other nvidia-utils dependency
# (libglvnd, egl-wayland, egl-gbm, egl-x11) is resolved inside the build
# chroot from Valve's own mirror, which keeps the image self-consistent -
# don't add them here. egl-wayland2 only became a dependency at branch 590,
# so which of these apply depends on the driver actually chosen.
ARCH_ONLY_DEPS=" egl-wayland2 "
while read -r dep; do
  [[ -n "$dep" && "$ARCH_ONLY_DEPS" == *" $dep "* ]] || continue
  log "  $DRIVER_VERSION also needs $dep, which Valve's repo predates"
  pin_pkg "$dep" latest
done < <(tar -xOf "$WORKDIR/pkgs/${PKG_FILES[0]}" .PKGINFO \
         | awk '$1 == "depend" { print $3 }' | sed 's/[<>=].*//')
fetch_pins

# ---------------------------------------------------- glibc compatibility
# Current Arch compiles against a newer glibc than frozen SteamOS ships.
# NVIDIA's own blobs target ancient glibc so they're fine, but anything
# Arch-compiled (egl-wayland2, and whoever joins the dep list in future
# driver releases) can silently require symbols the image doesn't have.
# Extract everything and refuse to build if any ELF needs more than the
# image's glibc.
IMG_GLIBC="$(basename "$(echo "$PACDB"/glibc-[0-9]*)" | sed -E 's/^glibc-([0-9]+\.[0-9]+).*/\1/')"
[[ "$IMG_GLIBC" =~ ^[0-9]+\.[0-9]+$ ]] || die "Could not determine image glibc version"
log "Checking payload glibc requirements against image glibc $IMG_GLIBC"
SCAN="$WORKDIR/glibc-scan"
rm -rf "$SCAN"; mkdir -p "$SCAN"
for f in "${PKG_FILES[@]}"; do
  mkdir -p "$SCAN/${f%%.pkg.tar.zst}"
  tar -xf "$WORKDIR/pkgs/$f" -C "$SCAN/${f%%.pkg.tar.zst}"
done
# readelf fails on non-ELF executables (scripts) - mustn't kill the pipeline
MAX_GLIBC="$({ find "$SCAN" -type f \( -name '*.so*' -o -perm -111 \) \
  -exec readelf -V {} + 2>/dev/null || true; } | grep -o 'GLIBC_[0-9.]*' \
  | sed 's/^GLIBC_//' | sort -uV | tail -1)"
[[ -n "$MAX_GLIBC" ]] || die "glibc scan found no ELF version references - scan broken?"
if [[ "$(printf '%s\n' "$MAX_GLIBC" "$IMG_GLIBC" | sort -V | tail -1)" != "$IMG_GLIBC" ]]; then
  die "Driver payload needs glibc $MAX_GLIBC but the image only has $IMG_GLIBC - current Arch has drifted too far; this needs the .run-installer approach instead"
fi
log "OK: payload needs at most glibc $MAX_GLIBC (image has $IMG_GLIBC)"
rm -rf "$SCAN"

# --------------------------------------------------------- overlay chroot
# A cached overlay from a previous run of a DIFFERENT driver version has to
# go: pacman would happily downgrade in place, but the old version's stray
# files and modules would ride along into the image. (The package cache in
# $WORKDIR/pkgs is kept - only the build residue is thrown away.)
if compgen -G "$UPPER/usr/lib/holo/pacmandb/local/nvidia-utils-[0-9]*" >/dev/null; then
  CACHED_VER="$(basename "$(echo "$UPPER"/usr/lib/holo/pacmandb/local/nvidia-utils-[0-9]*)")"
  CACHED_VER="${CACHED_VER#nvidia-utils-}"
  if [[ "$CACHED_VER" != "$DRIVER_VERSION" ]]; then
    log "Cached build is nvidia $CACHED_VER but $DRIVER_VERSION is pinned - clearing the build overlay"
    rm -rf "${UPPER:?}" "${OVLWORK:?}"
    mkdir -p "$UPPER" "$OVLWORK"
  fi
fi

# Reusing an overlay from another image or addon selection can mix kernels
# and leave a disabled addon in the output. The package download cache is kept.
CACHE_KEY="$( {
  sha256sum "$SCRIPT_DIR/scripts/notification-renderer.py" "$IMG" "$SCRIPT_DIR/steamos-nvidia-installer.sh" "$SCRIPT_DIR/lib/pc-support.sh" "$SCRIPT_DIR/scripts/hdr-defaults.py" "$SCRIPT_DIR/scripts/safe-graphics.py" "$SCRIPT_DIR/scripts/bluetooth-resume.py" "$SCRIPT_DIR/scripts/install-target.py" "$SCRIPT_DIR/scripts/patch-repair.py"
  sha256sum "$SCRIPT_DIR/VERSION"
  if [[ -n "$NVENC_DIR" ]]; then
    (cd "$NVENC_DIR" && find . -type f -print0 | sort -z | xargs -0 sha256sum)
  fi
  if [[ -n "$REMOTE_PLAY_DIR" ]]; then
    (cd "$REMOTE_PLAY_DIR" && find . -type f -print0 | sort -z | xargs -0 sha256sum)
  fi
  if [[ -n "$GAMESCOPE_DIR" ]]; then
    sha256sum "$GAMESCOPE_DIR/root/usr/bin/gamescope" "$GAMESCOPE_DIR/gamescope-build.json" "$GAMESCOPE_DIR/Gamescope-LICENSE"
  fi
  if [[ -n "$MANGOAPP_DIR" ]]; then
    sha256sum "$MANGOAPP_DIR/mangoapp" "$MANGOAPP_DIR/mangoapp-build.json" "$MANGOAPP_DIR/MangoHud-LICENSE"
  fi
  if [[ -n "$INSTALLER_UPDATE_SOURCE" ]]; then
    sha256sum "$INSTALLER_UPDATE_SOURCE" "$SCRIPT_DIR/scripts/installer-update.py" "$SCRIPT_DIR/scripts/installer-update-ui.py" "$SCRIPT_DIR/images/installer-update.png"
  else
    printf 'installer updates disabled\n'
  fi
  printf '%s\n' "$DRIVER_VERSION" "$ADD_XPADNEO" "$XPADNEO_VERSION"
} | sha256sum | cut -d ' ' -f1)"
if [[ ! -f "$WORKDIR/build-key" || "$(cat "$WORKDIR/build-key")" != "$CACHE_KEY" ]]; then
  rm -rf "${UPPER:?}" "${OVLWORK:?}"
  mkdir -p "$UPPER" "$OVLWORK"
fi
printf '%s\n' "$CACHE_KEY" > "$WORKDIR/build-key"

log "Setting up overlay build chroot (build residue stays out of the image)"
# index=off: allows reusing the upperdir even if a lazily-unmounted overlay
# from an interrupted previous run still references it (enables resume).
mount -t overlay overlay \
  -o "index=off,lowerdir=$MNT,upperdir=$UPPER,workdir=$OVLWORK" "$MERGED"
mount -t proc proc "$MERGED/proc"
mount --rbind /sys "$MERGED/sys";  mount --make-rslave "$MERGED/sys"
mount --rbind /dev "$MERGED/dev";  mount --make-rslave "$MERGED/dev"
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
set_chroot_resolver "$MERGED" \
  /etc/resolv.conf /run/systemd/resolve/resolv.conf /run/systemd/resolve/stub-resolv.conf \
  || die "No resolv.conf on this build host has a nameserver line, so the build chroot cannot resolve names. Point /etc/resolv.conf at /run/systemd/resolve/stub-resolv.conf, or write a nameserver line into it, then start the build again."

PACOPTS="--noconfirm --needed"
PACCONF="/etc/pacman.conf"
if [[ $SKIP_SIG -eq 1 ]]; then
  sed 's/^SigLevel.*/SigLevel = Never/' "$MERGED/etc/pacman.conf" \
    > "$MERGED/tmp/pacman-nosig.conf"
  PACCONF="/tmp/pacman-nosig.conf"
  warn "pacman signature verification DISABLED for the build"
fi

if [[ $SKIP_SIG -eq 0 && ! -d "$MERGED/etc/pacman.d/gnupg/private-keys-v1.d" ]]; then
  log "Initialising pacman keyring in chroot"
  in_chroot "pacman-key --init && pacman-key --populate" \
    || die "Keyring init failed - rerun with --skip-sigcheck if you accept unsigned installs"
fi

# Resume: if a previous run already built everything in the overlay for THIS
# driver version, skip the download/compile and go straight to payload
# extraction. (Version check matters: Arch may have bumped since the cached
# build - then the overlay must be brought up to the newly pinned version.)
if compgen -G "$UPPER/usr/lib/modules/$KVER/updates/dkms/nvidia.ko*" >/dev/null \
   && [[ "$(in_chroot "pacman -Q nvidia-utils 2>/dev/null" | awk '{print $2}')" == "$DRIVER_VERSION" ]]; then
  log "Overlay already contains a built nvidia $DRIVER_VERSION module - reusing previous build"
else
  log "Downloading exact-match kernel headers"
  pc_download "$HDR_URL" "$MERGED/tmp/headers.pkg.tar.zst" || die "Could not download exact-match kernel headers"

  log "Refreshing pacman databases"
  in_chroot "pacman --config $PACCONF -Sy"

  log "Installing headers + dkms (from Valve's mirror)"
  in_chroot "pacman --config $PACCONF -U $PACOPTS /tmp/headers.pkg.tar.zst"
  in_chroot "pacman --config $PACCONF -S $PACOPTS dkms"

  log "Installing pinned Arch driver packages (compiles the module, takes a few minutes)"
  rm -rf "$MERGED/tmp/nvpkgs"; mkdir -p "$MERGED/tmp/nvpkgs"
  for f in "${PKG_FILES[@]}"; do cp "$WORKDIR/pkgs/$f" "$MERGED/tmp/nvpkgs/"; done
  in_chroot "pacman --config $PACCONF -U $PACOPTS /tmp/nvpkgs/*.pkg.tar.zst" \
    || die "pacman -U failed. If it was a signature/keyring error (frozen image keyring vs current Arch packagers), rerun with --skip-sigcheck - the packages came over HTTPS from Arch infrastructure."

  if ! compgen -G "$MERGED/usr/lib/modules/$KVER/updates/dkms/nvidia.ko*" >/dev/null; then
    log "DKMS hook didn't build for $KVER - forcing"
    in_chroot "dkms autoinstall -k $KVER"
    compgen -G "$MERGED/usr/lib/modules/$KVER/updates/dkms/nvidia.ko*" >/dev/null \
      || die "nvidia module failed to build for $KVER (check output above)"
  fi
fi
NVIDIA_VER="$(in_chroot "pacman -Q nvidia-utils" | awk '{print $2}')"
[[ "$NVIDIA_VER" == "$DRIVER_VERSION" ]] \
  || die "Chroot has nvidia-utils $NVIDIA_VER but $DRIVER_VERSION was pinned - stale overlay? Delete $WORKDIR and rerun."
log "Built nvidia-open $NVIDIA_VER for $KVER"

mkdir -p "$MNT/usr/lib/steamos-nvidia"
install -m 644 "$SCRIPT_DIR/lib/pc-support.sh" "$MNT/usr/lib/steamos-nvidia/pc-support.sh"
install -m 644 "$SCRIPT_DIR/scripts/notification-renderer.py" "$MNT/usr/lib/steamos-nvidia/notification-renderer.py"
install -m 644 "$SCRIPT_DIR/scripts/hdr-defaults.py" "$MNT/usr/lib/steamos-nvidia/hdr-defaults.py"
install -m 755 "$SCRIPT_DIR/scripts/safe-graphics.py" "$MNT/usr/lib/steamos-nvidia/safe-graphics.py"
install -m 755 "$SCRIPT_DIR/scripts/bluetooth-resume.py" "$MNT/usr/lib/steamos-nvidia/bluetooth-resume.py"
install -m 755 "$SCRIPT_DIR/scripts/install-target.py" "$MNT/usr/lib/steamos-nvidia/install-target.py"
install -m 755 "$SCRIPT_DIR/scripts/steamos-nvidia-diagnostics" "$MNT/usr/bin/steamos-nvidia-diagnostics"
XPADNEO_SHA256=""
if [[ $ADD_XPADNEO -eq 1 ]]; then
  # Keep the exact source archive on the system for subsequent kernel updates.
  XPADNEO_ARCHIVE="$MNT/usr/lib/steamos-nvidia/xpadneo-source.tar.gz"
  curl -fL --retry 3 --connect-timeout 30 \
    "https://github.com/atar-axis/xpadneo/archive/refs/tags/$XPADNEO_VERSION.tar.gz" \
    -o "$XPADNEO_ARCHIVE.part" || die "Could not download xpadneo"
  mv "$XPADNEO_ARCHIVE.part" "$XPADNEO_ARCHIVE"
  XPADNEO_SHA256="$(sha256sum "$XPADNEO_ARCHIVE" | cut -d ' ' -f1)"
  pc_install_xpadneo "$MNT" "$MERGED" "$XPADNEO_ARCHIVE" "$XPADNEO_VERSION" "$KVER"
fi

# SteamOS's lib32-mangohud is missing a dependency: /usr/lib32/libMangoHud.so
# (and libMangoHud_opengl.so) carry a hard DT_NEEDED on libxkbcommon.so.0, but
# the image ships only the 64-bit libxkbcommon. The gamescope session preloads
# the overlay system-wide, so any game with a 32-bit component or anti-cheat
# helper fails the preload - seen as a SIGSEGV a minute or two after launch.
# Taken from the image's OWN frozen mirror (multilib-3.8.1x carries 1.10.0-1,
# matching the 64-bit libxkbcommon already installed), so no current-Arch
# library enters the image. Sits outside the resume branch above so a warm
# --workdir predating this still picks it up; --needed makes it a no-op after
# that. Joins the payload automatically via the package version comparison.
log "Installing lib32-libxkbcommon (missing dep of SteamOS's lib32-mangohud)"
in_chroot "pacman --config $PACCONF -Sy" || warn "pacman -Sy failed - trying the cached db"
in_chroot "pacman --config $PACCONF -S $PACOPTS lib32-libxkbcommon" \
  || die "could not install lib32-libxkbcommon from the image's frozen mirror"

# "Before" = the pristine image's own pacman db, read from the rootfs itself -
# NOT the overlay chroot's, whose db carries installs cached in the overlay
# upper layer from previous runs and would make the diff come out empty.
# The image's own pacman reads it, so non-Arch hosts need no pacman.
chroot "$MNT" pacman -Q --dbpath /usr/lib/holo/pacmandb | LC_ALL=C sort > "$WORKDIR/pkgs-before.txt" \
  || die "Could not read the recovery image package database"
in_chroot "pacman -Q" | LC_ALL=C sort > "$WORKDIR/pkgs-after.txt"

# ----------------------------------------------------- compute the payload
# New packages minus build-only toolchain = what ships in the image.
# nvidia-open-dkms is build-only too: it's the module SOURCE (~70 MB); the
# compiled module is copied from /usr/lib/modules separately.
BUILD_ONLY_RE='^(dkms|nvidia-open-dkms|patch|gcc|gcc-libs|make|binutils|libisl|libmpc|mpfr|pahole|python-setuptools|linux-neptune.*-headers|.*-headers)$'
mapfile -t NEW_PKGS < <(pc_changed_packages "$WORKDIR/pkgs-before.txt" "$WORKDIR/pkgs-after.txt" \
                        | grep -Ev "$BUILD_ONLY_RE")
[[ ${#NEW_PKGS[@]} -gt 0 ]] || die "Payload package list came out empty - check $WORKDIR/pkgs-*.txt"
log "Payload packages: ${NEW_PKGS[*]}"

FILELIST="$WORKDIR/payload-files.txt"
: > "$FILELIST"
for pkg in "${NEW_PKGS[@]}"; do
  in_chroot "pacman -Qlq $pkg" >> "$FILELIST"
done

if [[ $TRIM_CUDA -eq 1 ]]; then
  log "Trimming CUDA/OpenCL/NVVM/OptiX libraries"
  grep -Ev 'libcuda|libcudadebugger|libnvidia-nvvm|libnvidia-opencl|libnvoptix|nvidia-cuda-mps|OpenCL' \
    "$FILELIST" > "$FILELIST.trim" && mv "$FILELIST.trim" "$FILELIST"
fi
sed 's|^/||' "$FILELIST" > "$FILELIST.rel"

# Space check: pacman -Qlq lists directories too - size only files/symlinks.
PAYLOAD_MB="$(set +o pipefail; cd "$MERGED" && while IFS= read -r p; do
    if [[ -f "$p" || -L "$p" ]]; then printf '%s\0' "$p"; fi
  done < "$FILELIST.rel" | { du -scm --no-dereference --files0-from=- 2>/dev/null || true; } | tail -1 | cut -f1)"
[[ "$PAYLOAD_MB" =~ ^[0-9]+$ ]] || die "Could not size the payload"
MODULES_MB="$(du -sm "$UPPER/usr/lib/modules/$KVER/updates" | cut -f1)"
AVAIL_MB="$(df -m --output=avail "$MNT" | tail -1 | tr -d ' ')"
log "Payload ≈ ${PAYLOAD_MB} MB files + ${MODULES_MB} MB modules (before btrfs zstd); rootfs has ${AVAIL_MB} MB free"
# The destination is a disposable copy mounted with Btrfs compression.
# Uncompressed sizes are useful diagnostics, not an allocation estimate.
pc_require_space "$MNT" 256 || die "Rootfs needs at least 256 MiB free before copying"
if (( PAYLOAD_MB + MODULES_MB + 256 > AVAIL_MB )); then
  warn "Uncompressed payload exceeds free space; checking actual Btrfs allocation after copying"
fi

# --------------------------------------------------- install into rootfs
log "Copying driver payload into the image rootfs"
rsync -a --files-from="$FILELIST.rel" "$MERGED/" "$MNT/"   || die "Driver copy failed; the output image is incomplete"
rsync -a "$UPPER/usr/lib/modules/$KVER/updates" "$MNT/usr/lib/modules/$KVER/"   || die "Module copy failed; the output image is incomplete"
# Btrfs compression and delayed allocation must finish before checking space.
sync -f "$MNT"
pc_require_space "$MNT" 256   || die "Less than 256 MiB remains after copying; the output image is incomplete"

log "Registering payload packages in the image's pacman db"
for pkg in "${NEW_PKGS[@]}"; do
  pc_copy_package_db "$MERGED" "$MNT" "$pkg"
done

log "Running depmod + ldconfig in the image"
chroot "$MNT" depmod "$KVER"
chroot "$MNT" ldconfig
pc_write_userspace_report "$MNT" || die "NVIDIA userspace dependencies are incompatible; see userspace-check.txt in the image"

log "Writing modprobe config (blacklist nouveau, enable nvidia KMS)"
cat > "$MNT/etc/modprobe.d/99-nvidia-patch.conf" <<'EOF'
# Added by steamos-nvidia-installer
blacklist nouveau
options nouveau modeset=0
options nvidia-drm modeset=1 fbdev=1
options nvidia NVreg_PreserveVideoMemoryAllocations=1
EOF

log "Enabling nvidia suspend/resume services"
chroot "$MNT" systemctl enable nvidia-suspend nvidia-resume nvidia-hibernate 2>/dev/null \
  || warn "Could not enable nvidia power services (non-fatal)"

# ------------------------------------------------- OOBE steam-reset fix
# The recovery image ships an OOBE build of steam-jupiter whose
# /usr/bin/steam wrapper deletes ~/.steam and ~/.local/share/Steam on every
# launch ("always start with a fresh steam per boot"). Installed systems
# are a clone of the running USB, so every boot wiped the user's Steam
# login, settings and installed games until the first OS update swapped in
# the normal wrapper - and any later reflash brought the wipe back
# (issue #6). Neutralise just the delete; the wrapper's bootstrap handling
# is left alone.
# Resolve absolute symlinks inside the image, not against the build host.
STEAM_WRAPPER_PATH="$(chroot "$MNT" readlink -f /usr/bin/steam)"
[[ "$STEAM_WRAPPER_PATH" == /* ]] || die "Could not resolve the image Steam wrapper"
STEAM_WRAPPER="$MNT$STEAM_WRAPPER_PATH"
if [[ -f "$STEAM_WRAPPER" ]] \
   && grep -q 'rm -rf --one-file-system.*STEAM_LINKS' "$STEAM_WRAPPER"; then
  log "Disabling the OOBE steam wrapper's per-boot Steam data wipe"
  sed -i '/rm -rf --one-file-system.*STEAM_LINKS/ s|.*|  : # per-boot Steam data wipe disabled by steamos-nvidia-installer|' \
    "$STEAM_WRAPPER"
  grep -q 'wipe disabled by steamos-nvidia-installer' "$STEAM_WRAPPER" \
    || die "steam wrapper patch failed"
else
  warn "OOBE steam wrapper wipe not found - skipping (upstream wrapper may have changed)"
fi

# --------------------------------------------------------- update strategy
# OOBE day-1 auto-migration stays masked in all modes except stock - a
# surprise multi-GB update mid-first-boot is bad UX even when self-healing.
if [[ $UPDATE_MODE != stock ]]; then
  [[ -f "$MNT/usr/lib/systemd/system/steamos-finish-oobe-migration.service" ]] \
    && ln -sf /dev/null "$MNT/etc/systemd/system/steamos-finish-oobe-migration.service"
fi

if [[ $UPDATE_MODE == hold ]]; then
  log "Holding OS updates: masking updater services, stubbing CLIs"
  [[ -f "$MNT/usr/lib/systemd/system/atomupd.service" ]] \
    && ln -sf /dev/null "$MNT/etc/systemd/system/atomupd.service"
  for bin in steamos-update steamos-update-os steamos-atomupd-client; do
    [[ -f "$MNT/usr/bin/$bin" && ! -f "$MNT/usr/bin/$bin.orig" ]] || continue
    mv "$MNT/usr/bin/$bin" "$MNT/usr/bin/$bin.orig"
    cat > "$MNT/usr/bin/$bin" <<'EOF'
#!/bin/bash
# Stubbed by steamos-nvidia-installer: an OS update would replace the rootfs
# and remove the NVIDIA driver. Original saved as $0.orig.
echo "OS updates are held on this system (NVIDIA-patched image)." >&2
# 7 = "no update available" to keep the Steam UI happy
exit 7
EOF
    chmod 755 "$MNT/usr/bin/$bin"
  done
fi

if [[ $UPDATE_MODE == selfheal ]]; then
  log "Installing self-healing update machinery"
  mkdir -p "$MNT/usr/lib/steamos-nvidia"

  # pinned driver record - repatch installs these exact packages (instead of
  # the slot's frozen repo, which is what the valve-driver variant does)
  cat > "$MNT/usr/lib/steamos-nvidia/driver.conf" <<EOF
# Written by steamos-nvidia-installer at image build time.
# repatch.sh installs the driver from these pinned URLs; to move to a newer
# driver later, use steamos-nvidia-driver to prepare the inactive slot.
# Do not edit these pins by hand during an update.
DRIVER_SPEC="$DRIVER_SPEC"
DRIVER_VERSION="$DRIVER_VERSION"
PKG_URLS="$PKG_URLS"
ADD_XPADNEO=$ADD_XPADNEO
XPADNEO_VERSION="$XPADNEO_VERSION"
XPADNEO_SHA256="$XPADNEO_SHA256"
TRIM_CUDA=$TRIM_CUDA
EOF
  chmod 644 "$MNT/usr/lib/steamos-nvidia/driver.conf"

  # ---- on-device re-patch tool: rebuilds the driver inside the OTHER slot
  cat > "$MNT/usr/lib/steamos-nvidia/repatch.sh" <<'REPATCH'
#!/bin/bash
# steamos-nvidia repatch - rebuild + install the NVIDIA driver into another
# partition set (normally "other", right after an OS update staged there).
# Run as root. Idempotent: exits 0 immediately if the slot already has the
# driver for its kernel. Logs to stdout (the update wrapper redirects).
set -euo pipefail

PARTSET="${1:-other}"
[[ "$PARTSET" == other ]] || { echo "Only the inactive partset 'other' may be patched." >&2; exit 1; }
exec 9>/run/steamos-nvidia-repatch.lock
flock -n 9 || { echo "Another driver repair is running." >&2; exit 1; }
# shellcheck source=lib/pc-support.sh
source /usr/lib/steamos-nvidia/pc-support.sh
# shellcheck source=/dev/null
DRIVER_CONFIG="$(/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-change.py request-config)"
source "$DRIVER_CONFIG"
: "${ADD_XPADNEO:=0}" "${XPADNEO_VERSION:=v0.10.4}" "${XPADNEO_SHA256:=}" "${TRIM_CUDA:=0}"
FINGERPRINT="$( { sha256sum < "$DRIVER_CONFIG"; sha256sum /usr/lib/steamos-nvidia/notification-renderer.py /usr/lib/steamos-nvidia/pc-support.sh /usr/lib/steamos-nvidia/hdr-defaults.py /usr/lib/steamos-nvidia/safe-graphics.py /usr/lib/steamos-nvidia/bluetooth-resume.py /usr/lib/steamos-nvidia/install-target.py /usr/lib/steamos-nvidia/driver-change.py /usr/lib/steamos-nvidia/driver-stage.sh /usr/lib/steamos-nvidia/driver-manager.py /usr/lib/steamos-nvidia/repatch.sh; } | sha256sum | cut -d ' ' -f1)"
EXPECTED_XPADNEO=""
[[ $ADD_XPADNEO -eq 0 ]] || EXPECTED_XPADNEO="$XPADNEO_VERSION"
WAS_RO=0
SUCCESS=0
log() { echo "[repatch] $*"; }
die() { echo "[repatch] FAIL: $*" >&2; exit 1; }

ROOTDEV="/dev/disk/by-partsets/$PARTSET/rootfs"
EFIDEV="/dev/disk/by-partsets/$PARTSET/efi"
[[ -b "$ROOTDEV" && -b "$EFIDEV" ]] || die "partset '$PARTSET' not found (single-slot system?)"

ACTIVE_ID="$(pc_active_root_id)" || die "Cannot identify the active root block device"
TARGET_ID="$(lsblk -dn -o MAJ:MIN "$ROOTDEV")"
[[ -n "$ACTIVE_ID" && -n "$TARGET_ID" && "$ACTIVE_ID" != "$TARGET_ID" ]] \
  || die "Cannot verify that the target is an inactive rootfs"
pc_require_space /home 9216 || die "Not enough build space on /home"
NEWROOT="$(mktemp -d /tmp/repatch-root.XXXXXX)"
# SteamOS /home is ext4 with casefold enabled, which overlayfs rejects as an
# upperdir - so the build workspace lives inside a plain ext4 loopback image
# on /home (space for the build, no casefold).
WORKIMG="$(mktemp /home/.steamos-nvidia-work.XXXXXX.img)"
WORK="$(mktemp -d /tmp/repatch-work.XXXXXX)"
UPPER="$WORK/upper"; OVLWORK="$WORK/ovlwork"; MERGED="$WORK/merged"

cleanup() {
  set +e
  if mountpoint -q "$NEWROOT"; then
    if [[ $SUCCESS -eq 0 ]]; then
      rm -f "$NEWROOT/usr/lib/steamos-nvidia/complete" "$NEWROOT/usr/lib/steamos-nvidia/complete.part"
    fi
    btrfs filesystem sync "$NEWROOT"
    [[ $WAS_RO -eq 0 ]] || btrfs property set "$NEWROOT" ro true
  fi
  for m in "$MERGED"/dev/pts "$MERGED"/dev "$MERGED"/sys "$MERGED"/proc "$MERGED" \
           "$NEWROOT"/efi "$NEWROOT"/dev/pts "$NEWROOT"/dev "$NEWROOT"/sys "$NEWROOT"/proc "$NEWROOT" \
           "$WORK"; do
    mountpoint -q "$m" 2>/dev/null && { umount -R "$m" 2>/dev/null || umount -Rl "$m" 2>/dev/null; }
  done
  rmdir "$NEWROOT" "$WORK" 2>/dev/null
  rm -f "$WORKIMG"
}
trap cleanup EXIT

truncate -s 8G "$WORKIMG"
mkfs.ext4 -q -F "$WORKIMG"
mount -o loop "$WORKIMG" "$WORK"
mkdir -p "$UPPER" "$OVLWORK" "$MERGED"

log "Mounting $ROOTDEV"
mount -o compress-force=zstd:3 "$ROOTDEV" "$NEWROOT"
/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-change.py check-target "$NEWROOT"
WAS_RO=0
if [[ "$(btrfs property get "$NEWROOT" ro)" == "ro=true" ]]; then
  WAS_RO=1; btrfs property set "$NEWROOT" ro false
fi

KVER=""
for d in "$NEWROOT/usr/lib/modules/"*neptune*; do
  [[ -d "$d" ]] && KVER="$(basename "$d")" && break
done
[[ -n "$KVER" ]] || die "no neptune kernel in $PARTSET rootfs"
log "Target kernel: $KVER"

mkdir -p "$NEWROOT/efi"
mount "$EFIDEV" "$NEWROOT/efi"
# Integration-only transactions clone the same OS and keep its existing driver.
# Verify that driver instead of downloading and compiling it again.
if [[ -f /usr/lib/steamos-nvidia/installer-update.py ]] && [[ $(/usr/bin/python3 -I /usr/lib/steamos-nvidia/installer-update.py request-mode) == integration ]]; then
  /usr/bin/python3 -I /usr/lib/steamos-nvidia/installer-update.py apply-target "$NEWROOT" || die "Installer integration update failed"
  # shellcheck disable=SC1091
  source "$NEWROOT/usr/lib/steamos-nvidia/pc-support.sh"
  pc_check_driver "$NEWROOT" "$KVER" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" || die "Driver validation failed"
  pc_check_addons "$NEWROOT" || die "Addon validation failed"
  pc_write_userspace_report "$NEWROOT" || die "NVIDIA library validation failed"
  pc_write_runtime_info "$NEWROOT" "$KVER" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" "$TRIM_CUDA"
  pc_require_space "$NEWROOT" 256 || die "Insufficient target space"
  pc_write_complete "$NEWROOT" "$KVER" "$FINGERPRINT"
  pc_slot_complete "$NEWROOT" "$KVER" "$FINGERPRINT" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" || die "Slot validation failed"
  btrfs filesystem sync "$NEWROOT"
  sync -f "$NEWROOT"; sync -f "$NEWROOT/efi"
  [[ $WAS_RO -eq 0 ]] || btrfs property set "$NEWROOT" ro true
  /usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-change.py mark-ready
  SUCCESS=1
  log "Installer tools updated; NVIDIA driver retained"
  exit 0
fi
if pc_slot_complete "$NEWROOT" "$KVER" "$FINGERPRINT" "$DRIVER_VERSION" "$EXPECTED_XPADNEO"; then
  log "The complete driver installation has already been checked for $KVER"
  /usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-change.py mark-ready
  SUCCESS=1
  exit 0
fi
rm -f "$NEWROOT/usr/lib/steamos-nvidia/complete"


PACDB="$NEWROOT/usr/lib/holo/pacmandb/local"
KPKG_DIR=""
for d in "$PACDB"/linux-neptune-*-[0-9]*; do
  [[ -d "$d" ]] || continue
  case "$(basename "$d")" in *-headers-*|*firmware*|*rtw*) continue ;; esac
  KPKG_DIR="$d"; break
done
[[ -n "$KPKG_DIR" ]] || die "kernel package not found in new slot's pacman db"
KPKG_FULL="$(basename "$KPKG_DIR")"
KPKG_NAME="${KPKG_FULL%-*-*}"
KPKG_VERREL="${KPKG_FULL#"$KPKG_NAME"-}"
JUPITER_REPO="$(awk -F'[][]' '/^\[jupiter-/{print $2; exit}' "$NEWROOT/etc/pacman.conf")"
MIRROR="$(awk '/^Server/{print $3; exit}' "$NEWROOT/etc/pacman.d/mirrorlist")"
HDR_URL="${MIRROR/\$repo/$JUPITER_REPO}"
HDR_URL="${HDR_URL/\$arch/x86_64}/${KPKG_NAME}-headers-${KPKG_VERREL}-x86_64.pkg.tar.zst"
log "Headers: $(basename "$HDR_URL")"
pc_curl -fsSIL "$HDR_URL" -o /dev/null || die "could not access matching headers in Valve's pool: $HDR_URL"

log "Building driver in overlay chroot (this takes 10-20 minutes)"
mount -t overlay overlay -o "index=off,lowerdir=$NEWROOT,upperdir=$UPPER,workdir=$OVLWORK" "$MERGED"
mount -t proc proc "$MERGED/proc"
mount --rbind /sys "$MERGED/sys"; mount --make-rslave "$MERGED/sys"
mount --rbind /dev "$MERGED/dev"; mount --make-rslave "$MERGED/dev"
rm -f "$MERGED/etc/resolv.conf"; cp -L /etc/resolv.conf "$MERGED/etc/resolv.conf"
in_chroot() { chroot "$MERGED" /bin/bash -c "$*"; }

[[ -d "$MERGED/etc/pacman.d/gnupg/private-keys-v1.d" ]] \
  || in_chroot "pacman-key --init && pacman-key --populate"
pc_download "$HDR_URL" "$MERGED/tmp/headers.pkg.tar.zst" || die "Could not download exact-match kernel headers"
in_chroot "pacman -Sy"
in_chroot "pacman -Q" | LC_ALL=C sort > "$WORK/before.txt"
BUILD_OVERWRITE=""
if [[ -d /run/steamos-nvidia-driver ]]; then
  in_chroot "pacman -Uw --noconfirm /tmp/headers.pkg.tar.zst"
  in_chroot "pacman -Sw --noconfirm --needed dkms"
  BUILD_OVERWRITE="$(pc_build_overwrites "$MERGED" "$MERGED/tmp/headers.pkg.tar.zst" "$MERGED"/var/cache/pacman/pkg/*.pkg.tar.zst)" || die "Cannot check orphan build files"
fi
BUILD_ARGS=""
[[ -z "$BUILD_OVERWRITE" ]] || BUILD_ARGS="--overwrite '$BUILD_OVERWRITE'"
in_chroot "pacman -U --noconfirm --needed $BUILD_ARGS /tmp/headers.pkg.tar.zst"
in_chroot "pacman -S --noconfirm --needed $BUILD_ARGS dkms"

# Same missing dependency the build side installs: without lib32-libxkbcommon
# the gamescope session's 32-bit MangoHud preload fails in every game with a
# 32-bit component. Non-fatal here - an overlay dependency must never brick an
# OS update. Lands in the payload via the before/after diff below.
in_chroot "pacman -S --noconfirm --needed lib32-libxkbcommon" \
  || log "WARNING: lib32-libxkbcommon install failed - 32-bit MangoHud overlay will not load"

# Driver = the exact pinned Arch packages this image was built with (NOT the
# slot's frozen repo - that only has Valve's older driver).
source "$DRIVER_CONFIG"
[[ -n "${PKG_URLS:-}" ]] || die "driver.conf has no PKG_URLS"
log "Installing pinned driver $DRIVER_VERSION"
in_chroot "mkdir -p /tmp/nvpkgs"
for u in $PKG_URLS; do
  pc_download_package "$u" "$MERGED/tmp/nvpkgs/${u##*/}" || die "Could not download pinned package: ${u##*/}; update remains disabled"
done
in_chroot "pacman -U --noconfirm --needed /tmp/nvpkgs/*.pkg.tar.zst" \
  || die "Driver package installation failed. Check the package signatures, keyring and dependencies in the log."
# A cloned slot already contains a module. DKMS may skip a downgrade unless
# forced; install into the disposable overlay and verify all module versions.
in_chroot "dkms install --force -m nvidia -v ${DRIVER_VERSION%-*} -k $KVER"
for module in nvidia nvidia_modeset nvidia_drm nvidia_uvm; do
  pc_check_module "$MERGED" "$KVER" "$module" "${DRIVER_VERSION%-*}" \
    || die "Wrong or missing $module module for $KVER"
done
if [[ $ADD_XPADNEO -eq 1 ]]; then
  ARCHIVE=/usr/lib/steamos-nvidia/xpadneo-source.tar.gz
  [[ -n "$XPADNEO_SHA256" && "$(sha256sum "$ARCHIVE" | cut -d ' ' -f1)" == "$XPADNEO_SHA256" ]] \
    || die "The saved xpadneo source archive is missing or has changed"
  pc_install_xpadneo "$NEWROOT" "$MERGED" "$ARCHIVE" "$XPADNEO_VERSION" "$KVER"
fi
in_chroot "pacman -Q" | LC_ALL=C sort > "$WORK/after.txt"

BUILD_ONLY_RE='^(dkms|nvidia-open-dkms|patch|gcc|gcc-libs|make|binutils|libisl|libmpc|mpfr|pahole|python-setuptools|linux-neptune.*-headers|.*-headers)$'
mapfile -t NEW_PKGS < <(pc_changed_packages "$WORK/before.txt" "$WORK/after.txt" | grep -Ev "$BUILD_ONLY_RE")
mapfile -t NEW_PKGS < <({
  printf '%s\n' "${NEW_PKGS[@]}"
  for u in $PKG_URLS; do
    f="${u##*/}"
    f="${f%-x86_64.pkg.tar.zst}"
    printf '%s\n' "${f%-*-*}"
  done
} | grep -v '^nvidia-open-dkms$' | sed '/^$/d' | LC_ALL=C sort -u)
[[ ${#NEW_PKGS[@]} -gt 0 ]] || die "payload list empty"
log "Payload: ${NEW_PKGS[*]}"

: > "$WORK/files.txt"
for pkg in "${NEW_PKGS[@]}"; do in_chroot "pacman -Qlq $pkg" >> "$WORK/files.txt"; done
if [[ $TRIM_CUDA -eq 1 ]]; then
  grep -Ev 'libcuda|libcudadebugger|libnvidia-nvvm|libnvidia-opencl|libnvoptix|nvidia-cuda-mps|OpenCL' \
    "$WORK/files.txt" > "$WORK/files.trim"
  mv "$WORK/files.trim" "$WORK/files.txt"
fi
sed 's|^/||' "$WORK/files.txt" > "$WORK/files.rel"
pc_require_space /home 1024 || die "Build consumed the remaining space on /home"
PAYLOAD_KB="$(du -sk "$UPPER/usr/lib/modules/$KVER/updates" | cut -f1)"
while IFS= read -r f; do
  [[ -f "$MERGED/$f" || -L "$MERGED/$f" ]] || continue
  size="$(du -sk "$MERGED/$f" | cut -f1)"
  PAYLOAD_KB=$((PAYLOAD_KB + size))
done < "$WORK/files.rel"
log "Uncompressed driver payload: $((PAYLOAD_KB / 1024)) MiB; checking actual space after Btrfs compression"
pc_remove_obsolete_driver_files "$NEWROOT" "$WORK/files.rel" || die "Could not remove obsolete driver files"
log "Copying driver into $PARTSET rootfs"
pc_copy_update_payload "$MERGED" "$NEWROOT" "$WORK/files.rel" \
  "$UPPER/usr/lib/modules/$KVER/updates" "$KVER" \
  || die "Driver copy or free-space check failed; the new rootfs is not ready"
for pkg in "${NEW_PKGS[@]}"; do
  pc_copy_package_db "$MERGED" "$NEWROOT" "$pkg"
done
chroot "$NEWROOT" depmod "$KVER"
chroot "$NEWROOT" ldconfig

cat > "$NEWROOT/etc/modprobe.d/99-nvidia-patch.conf" <<'EOF'
# Added by steamos-nvidia repatch
blacklist nouveau
options nouveau modeset=0
options nvidia-drm modeset=1 fbdev=1
options nvidia NVreg_PreserveVideoMemoryAllocations=1
EOF
chroot "$NEWROOT" systemctl enable nvidia-suspend nvidia-resume nvidia-hibernate 2>/dev/null || true

CMDLINE_ADD='rd.driver.blacklist=nouveau modprobe.blacklist=nouveau nvidia-drm.modeset=1 nvidia-drm.fbdev=1'
grep -q 'rd.driver.blacklist=nouveau' "$NEWROOT/etc/default/grub" \
  || sed -i -E "s#^(GRUB_CMDLINE_LINUX_DEFAULT=\")#\1$CMDLINE_ADD #" "$NEWROOT/etc/default/grub"

# propagate the self-healing machinery (repatch.sh + driver.conf) so the
# NEXT update is covered too
mkdir -p "$NEWROOT/usr/lib/steamos-nvidia"
cp -a /usr/lib/steamos-nvidia/. "$NEWROOT/usr/lib/steamos-nvidia/"
install -m 644 "$DRIVER_CONFIG" "$NEWROOT/usr/lib/steamos-nvidia/driver.conf"
ln -sfn /usr/lib/steamos-nvidia/driver-change.py "$NEWROOT/usr/bin/steamos-nvidia-driver"
pc_install_driver_manager "$NEWROOT" || die "Could not preserve the driver manager"
pc_install_installer_update "$NEWROOT" || die "Could not preserve the installer updater"
rm -f "$NEWROOT/usr/lib/steamos-nvidia/complete"
install -m 755 /usr/bin/steamos-nvidia-diagnostics "$NEWROOT/usr/bin/steamos-nvidia-diagnostics"
pc_install_update_policy "$NEWROOT" || die "Could not preserve the shared update hook"
pc_install_display_policy "$NEWROOT" || die "Could not restore the SDR default"
pc_install_mangoapp "$NEWROOT" || die "Could not restore MangoApp"
pc_install_gamescope "$NEWROOT" || die "Could not restore Gamescope capture correction"
pc_install_remote_play "$NEWROOT" || die "Could not restore Remote Play receiver"
pc_install_nvenc "$NEWROOT" || die "Could not restore NVENC bridge"
pc_install_bluetooth_resume "$NEWROOT" || die "Could not install Bluetooth audio resume support"
pc_preserve_update_command "$NEWROOT" || die "Could not preserve the native update command"
cp -a /usr/bin/steamos-update "$NEWROOT/usr/bin/steamos-update"
[[ -f "$NEWROOT/usr/lib/systemd/system/steamos-finish-oobe-migration.service" ]] \
  && ln -sf /dev/null "$NEWROOT/etc/systemd/system/steamos-finish-oobe-migration.service"
pc_install_installer_permissions "$NEWROOT" || die "Could not restore installer permissions"

# regenerate the new slot's grub.cfg with the nvidia cmdline
log "Regenerating grub config for $PARTSET"
mount -t proc proc "$NEWROOT/proc"
mount --rbind /sys "$NEWROOT/sys"; mount --make-rslave "$NEWROOT/sys"
mount --rbind /dev "$NEWROOT/dev"; mount --make-rslave "$NEWROOT/dev"
chroot "$NEWROOT" update-grub
grep -q 'rd.driver.blacklist=nouveau' "$NEWROOT/efi/EFI/steamos/grub.cfg" \
  || die "regenerated grub.cfg is missing the nvidia cmdline"

pc_check_driver "$NEWROOT" "$KVER" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" || die "Driver validation failed"
pc_check_addons "$NEWROOT" || die "Addon validation failed"
pc_write_userspace_report "$NEWROOT" || die "NVIDIA userspace dependencies are incompatible; the update slot remains disabled"
pc_write_runtime_info "$NEWROOT" "$KVER" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" "$TRIM_CUDA"
# Include package metadata and generated files in the final space check.
sync -f "$NEWROOT"
pc_recover_update_space "$NEWROOT" || die "Less than 256 MiB remains in the completed rootfs"
pc_write_complete "$NEWROOT" "$KVER" "$FINGERPRINT"
pc_slot_complete "$NEWROOT" "$KVER" "$FINGERPRINT" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" || die "Slot validation failed"
log "Syncing"
btrfs filesystem sync "$NEWROOT"
sync -f "$NEWROOT"; sync -f "$NEWROOT/efi"
[[ $WAS_RO -eq 0 ]] || btrfs property set "$NEWROOT" ro true
/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-change.py mark-ready
SUCCESS=1
log "OK - $PARTSET is NVIDIA-ready ($KVER)"
REPATCH
  chmod 755 "$MNT/usr/lib/steamos-nvidia/repatch.sh"

  # ---- legacy CLI wrapper; RAUC handles repair for both update entry points
  if [[ ! -f "$MNT/usr/bin/steamos-update.orig" ]]; then
    mv "$MNT/usr/bin/steamos-update" "$MNT/usr/bin/steamos-update.orig"
  fi
  cat > "$MNT/usr/bin/steamos-update" <<'WRAP'
#!/bin/bash
# steamos-update wrapper (steamos-nvidia self-healing updates).
# Runs Valve's updater with shared RAUC repair, or the legacy repair fallback.
# If repair fails, the new slot remains disabled and the
# bootloader keeps booting the current (working) image.
exec 8>/run/steamos-nvidia-update.lock
flock -n 8 || { echo "Another SteamOS update is running." >&2; exit 1; }
REAL=/usr/bin/steamos-update.orig
REPATCH=/usr/lib/steamos-nvidia/repatch.sh
LOG=/var/log/steamos-nvidia-repatch.log

is_apply=1
for a in "$@"; do
  case "$a" in check|--supports-duplicate-detection) is_apply=0 ;; esac
done

"$REAL" "$@"
rc=$?

# Edit the boot config of every slot EXCEPT the currently booted one.
# The conf files on the ESP are plain text; editing them directly is the
# only revert that reliably steers steamcl (set-mode booted does NOT undo a
# staged switch, and a zeroed boot-requested-at still gets retried while
# boot-attempts is nonzero - both verified the hard way).
edit_other_confs() {  # args: sed expressions
  local this conf key
  local -a targets=()
  this="$(steamos-bootconf this-image 2>/dev/null)" || return 1
  [[ -n "$this" && "$this" != */* && -f "/esp/SteamOS/conf/$this.conf" ]] || return 1
  for conf in /esp/SteamOS/conf/*.conf; do
    [[ -f "$conf" ]] || continue
    [[ "$(basename "$conf" .conf)" == "$this" ]] && continue
    for key in image-invalid boot-requested-at boot-attempts; do
      [[ "$(grep -c "^$key:" "$conf")" == 1 ]] || return 1
    done
    targets+=("$conf")
  done
  [[ ${#targets[@]} -gt 0 ]] || return 1
  # Validate every target before changing any boot entry.
  for conf in "${targets[@]}"; do
    sed -i "$@" "$conf" || return 1
  done
  sync -f /esp/SteamOS/conf 2>/dev/null || sync
}

# RAUC has already repaired and validated the target before activation.
# Keep the old path only for installations without the shared hook.
if grep -Fxq '# steamos-nvidia shared-update-hook v1' /usr/lib/rauc/post-install.sh; then
  exit "$rc"
fi

if [[ $rc -eq 0 && $is_apply -eq 1 ]]; then
  echo "Update staged. Building NVIDIA driver for the new OS (10-20 min, do NOT power off)..." >&2
  # Keep the staged slot ineligible while its driver files are being changed.
  edit_other_confs -e 's/^image-invalid:.*/image-invalid: 1/' || {
    echo "Could not protect the staged boot entry. Driver repair was not started." >&2
    exit 1
  }
  if "$REPATCH" other >> "$LOG" 2>&1; then
    # make sure the freshly patched slot is bootable (clears an
    # image-invalid left by a previously cancelled update)
    edit_other_confs -e 's/^image-invalid:.*/image-invalid: 0/' || {
      echo "Driver repair finished, but the new boot entry could not be enabled." >&2
      exit 1
    }
    echo "NVIDIA driver installed and the new boot entry is enabled." >&2
  else
    echo "!! NVIDIA driver rebuild FAILED - cancelling this update." >&2
    echo "!! The system will keep booting the current working version." >&2
    echo "!! Details: $LOG" >&2
    edit_other_confs \
      -e 's/^boot-requested-at:.*/boot-requested-at: 0/' \
      -e 's/^boot-attempts:.*/boot-attempts: 0/' \
      -e 's/^image-invalid:.*/image-invalid: 1/'
    steamos-bootconf set-mode booted 2>/dev/null
    exit 1
  fi
fi
exit $rc
WRAP
  chmod 755 "$MNT/usr/bin/steamos-update"
fi

# ----------------------------------------------------- kernel cmdline
# rd.driver.blacklist keeps the initramfs from loading its bundled nouveau,
# so no initramfs regeneration is needed. /etc/default/grub matters too:
# the installer's update-grub regenerates the target's grub.cfg from it.
CMDLINE_ADD='rd.driver.blacklist=nouveau modprobe.blacklist=nouveau nvidia-drm.modeset=1 nvidia-drm.fbdev=1'
log "Appending to kernel cmdline: $CMDLINE_ADD"
sed -i -E "s#(steamenv_boot[[:space:]]+linux[[:space:]]+/boot/vmlinuz[^\n]*)#\1 $CMDLINE_ADD#" \
  "$EFIMNT/EFI/steamos/grub.cfg"
grep -q 'rd.driver.blacklist=nouveau' "$EFIMNT/EFI/steamos/grub.cfg" \
  || die "grub.cfg edit failed - cmdline pattern not found"
if [[ -f "$MNT/etc/default/grub" ]]; then
  sed -i -E "s#^(GRUB_CMDLINE_LINUX_DEFAULT=\")#\1$CMDLINE_ADD #" "$MNT/etc/default/grub"
fi

# -------------------------------------------------- one-click installer
if [[ $ADD_INSTALLER -eq 1 ]]; then
  TOOLS="$HOMEMNT/deck/tools"
  DESKTOP="$HOMEMNT/deck/Desktop"
  [[ -f "$TOOLS/repair_device.sh" ]] \
    || die "No repair_device.sh in image home - is this the OOBE *repair* image?"

  log "Patching Valve's repair_device.sh for generic hardware"
  cp -a "$TOOLS/repair_device.sh" "$TOOLS/repair_device.sh.stock"
  # shellcheck disable=SC2016  # literal $ wanted in the patched script
  sed -i \
    -e 's|^DISK=/dev/nvme0n1$|DISK="${STEAMOS_TARGET_DISK:-/dev/nvme0n1}"|' \
    -e 's|^DISK_SUFFIX=p$|DISK_SUFFIX=""; [[ "$DISK" =~ [0-9]$ ]] \&\& DISK_SUFFIX="p"|' \
    "$TOOLS/repair_device.sh"
  grep -q 'STEAMOS_TARGET_DISK' "$TOOLS/repair_device.sh" || die "DISK patch failed"
  # skip NVMe sanitize for non-NVMe targets (it error-traps on SATA/virtio),
  # and tolerate NVMe drives that don't implement sanitize - some (e.g. WD
  # Gen3) return "Access Denied ... (0x4286)" and would abort the whole
  # install (issue #8). A failed sanitize just means the old data isn't
  # pre-erased; the install proceeds fine without it.
  # shellcheck disable=SC2016
  sed -i '/^all)$/,/^  ;;$/ s|^  sanitize_all$|  if [[ "$DISK" == /dev/nvme* ]]; then sanitize_all \|\| ewarn "NVMe sanitize failed or unsupported - continuing without it"; else ewarn "Non-NVMe target: skipping NVMe sanitize"; fi|' \
    "$TOOLS/repair_device.sh"
  grep -q 'skipping NVMe sanitize' "$TOOLS/repair_device.sh" || die "sanitize patch failed"
  grep -q 'sanitize failed or unsupported' "$TOOLS/repair_device.sh" || die "sanitize-tolerance patch failed"

  /usr/bin/python3 "$SCRIPT_DIR/scripts/patch-repair.py" "$TOOLS/repair_device.sh" \
    || die "Unsupported Valve installer layout or bootloader command"

  log "Installing disk-picker wrapper + desktop icons"
  cat > "$TOOLS/install_to_hd.sh" <<'WRAPPER'
#!/bin/bash
# UI and validation are shared by internal and external USB installations.
exec /usr/bin/python3 /usr/lib/steamos-nvidia/install-target.py "${1:-all}"
WRAPPER
  chmod 755 "$TOOLS/install_to_hd.sh"

  cat > "$DESKTOP/Install SteamOS NVIDIA.desktop" <<'ICON'
[Desktop Entry]
Name=Install SteamOS (NVIDIA) to Disk
GenericName=Install SteamOS (NVIDIA) to Disk
Comment=Install SteamOS on an internal disk or external USB disk, erasing the selected disk
Exec=/home/deck/tools/install_to_hd.sh all
Icon=drive-harddisk
Path=/home/deck
Terminal=true
Type=Application
StartupNotify=true
ICON
  chmod 755 "$DESKTOP/Install SteamOS NVIDIA.desktop"

  cat > "$DESKTOP/Upgrade SteamOS NVIDIA.desktop" <<'ICON'
[Desktop Entry]
Name=Upgrade SteamOS (NVIDIA) - keeps games & data
GenericName=Upgrade SteamOS (NVIDIA) - keeps games & data
Comment=Reinstall the OS partitions from this USB while preserving the home partition
Exec=/home/deck/tools/install_to_hd.sh system
Icon=system-software-update
Path=/home/deck
Terminal=true
Type=Application
StartupNotify=true
ICON
  chmod 755 "$DESKTOP/Upgrade SteamOS NVIDIA.desktop"

  chown -R 1000:1000 "$TOOLS/install_to_hd.sh" "$TOOLS/repair_device.sh" \
    "$TOOLS/repair_device.sh.stock" "$DESKTOP/Install SteamOS NVIDIA.desktop" \
    "$DESKTOP/Upgrade SteamOS NVIDIA.desktop"

  log "Installing the protected repair tool and limited installer permission"
  install -D -m 755 "$TOOLS/repair_device.sh" "$MNT/usr/lib/steamos-nvidia/installer/repair_device.sh"
  # Never execute optional firmware tools from the writable home directory.
  sed -i -e 's|^VENDORED_BIOS_UPDATE=.*|VENDORED_BIOS_UPDATE=/usr/lib/steamos-nvidia/installer/no-vendored-bios|' \
    -e 's|^VENDORED_CONTROLLER_UPDATE=.*|VENDORED_CONTROLLER_UPDATE=/usr/lib/steamos-nvidia/installer/no-vendored-controller|' \
    "$MNT/usr/lib/steamos-nvidia/installer/repair_device.sh"
  if [[ -f "$TOOLS/steamos-branch" ]]; then
    install -m 644 "$TOOLS/steamos-branch" "$MNT/usr/lib/steamos-nvidia/installer/steamos-branch"
  fi
  pc_install_installer_permissions "$MNT" || die "Could not restrict installer permissions"
fi

# Record the exact inputs without marking this candidate as hardware-tested.
# safe.directory is required because the build runs as root against a checkout
# owned by the build user. Without it git refuses with "detected dubious
# ownership", the error goes to /dev/null and the image records "unknown",
# which is how a candidate loses its provenance. Measured on 2026-09-26.
{
  printf 'Installer version: %s\n' "$INSTALLER_VERSION"
  printf 'Built UTC: %s\n' "$(date -u +%FT%TZ)"
  printf 'Source commit: %s\n' "$(git -C "$SCRIPT_DIR" -c safe.directory="$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
  printf 'Source working tree:\n'
  git -C "$SCRIPT_DIR" -c safe.directory="$SCRIPT_DIR" status --short --untracked-files=no 2>/dev/null || true
  printf 'Input image: %s\n' "$(basename "$IMG")"
  printf 'Input SHA256: %s\n' "$(sha256sum "$IMG" | cut -d ' ' -f1)"
  printf 'Update mode: %s\nInstaller: %s\nTrim CUDA: %s\nxpadneo enabled: %s\n' \
    "$UPDATE_MODE" "$ADD_INSTALLER" "$TRIM_CUDA" "$ADD_XPADNEO"
  printf 'Source file SHA256:\n'
  (cd "$SCRIPT_DIR" && sha256sum VERSION scripts/notification-renderer.py steamos-nvidia-installer.sh lib/pc-support.sh scripts/steamos-nvidia-diagnostics scripts/hdr-defaults.py scripts/safe-graphics.py scripts/bluetooth-resume.py scripts/install-target.py scripts/patch-repair.py)
} > "$MNT/usr/lib/steamos-nvidia/build-info.txt"

if [[ $EXPERIMENTAL_BETA == 1 ]]; then
  log "Experimental image: selecting SteamOS beta for OS updates"
  pc_select_beta_branch "$MNT" || die "Could not select the experimental beta branch"
fi

if [[ $EXPERIMENTAL_PREVIEW == 1 ]]; then
  log "Experimental image: selecting SteamOS Preview for OS updates"
  pc_select_experimental_branch "$MNT" preview || die "Could not select the experimental Preview branch"
fi

if [[ $UPDATE_MODE == selfheal ]]; then
  pc_install_update_policy "$MNT" || die "Could not install the shared update hook"
fi
if [[ -n "$MANGOAPP_DIR" ]]; then
  install -m 755 "$MANGOAPP_DIR/mangoapp" "$MNT/usr/lib/steamos-nvidia/mangoapp"
  install -m 644 "$MANGOAPP_DIR/mangoapp-build.json" "$MANGOAPP_DIR/MangoHud-LICENSE" "$MNT/usr/lib/steamos-nvidia/"
fi
pc_install_display_policy "$MNT" || die "Could not install the SDR default"
pc_install_mangoapp "$MNT" || die "Could not install MangoApp"
if [[ -n "$GAMESCOPE_DIR" ]]; then
  mkdir -p "$MNT/usr/lib/steamos-nvidia/gamescope/bin"
  install -m 755 "$GAMESCOPE_DIR/root/usr/bin/gamescope" "$MNT/usr/lib/steamos-nvidia/gamescope/bin/gamescope"
  install -m 644 "$GAMESCOPE_DIR/gamescope-build.json" "$GAMESCOPE_DIR/Gamescope-LICENSE" "$MNT/usr/lib/steamos-nvidia/gamescope/"
  if [[ -d "$GAMESCOPE_DIR/licenses" ]]; then
    cp -a "$GAMESCOPE_DIR/licenses" "$MNT/usr/lib/steamos-nvidia/gamescope/"
  fi
fi
pc_install_gamescope "$MNT" || die "Could not install Gamescope capture correction"
if [[ -n "$REMOTE_PLAY_DIR" ]]; then
  mkdir -p "$MNT/usr/lib/steamos-nvidia/remote-play"
  cp -a "$REMOTE_PLAY_DIR/." "$MNT/usr/lib/steamos-nvidia/remote-play/"
  install -m 755 "$SCRIPT_DIR/scripts/remote-play-env.py" "$MNT/usr/lib/steamos-nvidia/remote-play-env.py"
fi
pc_install_remote_play "$MNT" || die "Could not install Remote Play receiver"
if [[ -n "$NVENC_DIR" ]]; then
  mkdir -p "$MNT/usr/lib/steamos-nvidia/nvenc"
  cp -a "$NVENC_DIR/." "$MNT/usr/lib/steamos-nvidia/nvenc/"
fi
pc_install_nvenc "$MNT" || die "Could not install NVENC bridge"

pc_install_bluetooth_resume "$MNT" || die "Could not install Bluetooth audio resume support"
install -m 755 "$SCRIPT_DIR/scripts/driver-stage.sh" "$MNT/usr/lib/steamos-nvidia/driver-stage.sh"
install -m 755 "$SCRIPT_DIR/scripts/driver-change.py" "$MNT/usr/lib/steamos-nvidia/driver-change.py"
ln -sfn /usr/lib/steamos-nvidia/driver-change.py "$MNT/usr/bin/steamos-nvidia-driver"
install -m 755 "$SCRIPT_DIR/scripts/driver-manager.py" "$MNT/usr/lib/steamos-nvidia/driver-manager.py"
install -m 644 "$SCRIPT_DIR/images/change-nvidia-driver.png" "$MNT/usr/lib/steamos-nvidia/change-nvidia-driver.png"
pc_install_driver_manager "$MNT" || die "Could not install the driver manager"
if [[ -n "$INSTALLER_UPDATE_SOURCE" ]]; then
  [[ $UPDATE_MODE == selfheal ]] || die "Installer updates require self-healing mode"
  install -m 755 "$SCRIPT_DIR/scripts/installer-update.py" "$MNT/usr/lib/steamos-nvidia/installer-update.py"
  install -m 755 "$SCRIPT_DIR/scripts/installer-update-ui.py" "$MNT/usr/lib/steamos-nvidia/installer-update-ui.py"
  install -m 644 "$SCRIPT_DIR/images/installer-update.png" "$MNT/usr/lib/steamos-nvidia/installer-update.png"
  install -m 644 "$INSTALLER_UPDATE_SOURCE" "$MNT/usr/lib/steamos-nvidia/installer-update-source.json"
  python3 - "$MNT/usr/lib/steamos-nvidia/integration-version.json" "$INSTALLER_VERSION" <<'INTEGRATION_VERSION'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({'version': sys.argv[2]}, indent=2) + '\n')
INTEGRATION_VERSION
  chroot "$MNT" /usr/bin/python3 -I -c "import runpy; runpy.run_path('/usr/lib/steamos-nvidia/installer-update.py')['source']()" || die "Invalid installer update source"
  chroot "$MNT" /usr/bin/openssl version >/dev/null || die "Installer updates require OpenSSL"
  pc_install_installer_update "$MNT" || die "Could not install project updater"
fi
pc_write_addon_manifest "$MNT" || die "Could not record addon files"
pc_check_addons "$MNT" || die "Addon validation failed"

# ----------------------------------------------------------- sanity check
log "Sanity checks"
EXPECTED_XPADNEO=""
[[ $ADD_XPADNEO -eq 0 ]] || EXPECTED_XPADNEO="$XPADNEO_VERSION"
pc_check_driver "$MNT" "$KVER" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" || die "Driver validation failed"
pc_write_runtime_info "$MNT" "$KVER" "$DRIVER_VERSION" "$EXPECTED_XPADNEO" "$TRIM_CUDA"
grep -q 'blacklist nouveau' "$MNT/etc/modprobe.d/99-nvidia-patch.conf" || die "modprobe conf is empty/missing"
if [[ $UPDATE_MODE == selfheal ]]; then
  grep -q 'self-healing' "$MNT/usr/bin/steamos-update" || die "update wrapper missing"
  [[ -f "$MNT/usr/bin/steamos-update.orig" ]] || die "original steamos-update not preserved"
  grep -q 'repatch' "$MNT/usr/lib/steamos-nvidia/repatch.sh" || die "repatch tool missing"
  grep -q "^DRIVER_VERSION=\"$DRIVER_VERSION\"" "$MNT/usr/lib/steamos-nvidia/driver.conf" || die "driver.conf missing/wrong"
  [[ -L "$MNT/etc/systemd/system/atomupd.service" ]] && die "atomupd must NOT be masked in selfheal mode"
fi
compgen -G "$MNT/usr/lib/firmware/nvidia/*/gsp_*.bin" >/dev/null || warn "GSP firmware not found - nvidia-open needs it"
[[ -f "$MNT/usr/share/vulkan/icd.d/nvidia_icd.json" ]] || warn "Vulkan ICD json missing"
AVAIL_AFTER="$(df -m --output=avail "$MNT" | tail -1 | tr -d ' ')"
log "Rootfs free space after install: ${AVAIL_AFTER} MB"

# Flush all pending writes BEFORE flipping the subvolume read-only -
# flipping with delalloc data still queued can silently produce 0-byte files.
log "Syncing filesystems"
btrfs filesystem sync "$MNT"
sync -f "$MNT"; sync -f "$HOMEMNT"; sync -f "$EFIMNT"

log "Restoring btrfs read-only property"
btrfs property set "$MNT" ro true

log "Unmounting"
cleanup
trap - EXIT

# Same directory, so this is a rename. The flashable name exists only now.
mv -f "$PARTIAL" "$OUT" || die "Could not put the finished image in place: $OUT"

log "DONE - $OUT"
cat <<EOF

  Driver:  nvidia-open (DKMS) $NVIDIA_VER for kernel $KVER
           (latest Arch at build time, pinned - Valve's mirror only has 575.x)
$( case $UPDATE_MODE in
     selfheal) echo "  Updates: SELF-HEALING - updating from within Steam works; the SAME
           pinned driver is rebuilt for each new OS version automatically
           (adds 10-20 min per update; failed rebuilds cancel the update,
           system stays working). To change the driver later, use
           steamos-nvidia-driver on an installed self-healing A/B system." ;;
     hold)     echo "  Updates: OS updates HELD (atomupd + OOBE migration masked, CLIs stubbed)." ;;
     stock)    echo "  Updates: STOCK behaviour - an OS update will REMOVE the NVIDIA driver!" ;;
   esac )
$( [[ $ADD_INSTALLER -eq 1 ]] && echo "  Install: boot the USB → double-click \"Install SteamOS (NVIDIA) to
           Hard Drive\" → pick disk → machine powers off → remove USB, boot." )

  Flash:   sudo dd if="$OUT" of=/dev/sdX bs=4M status=progress conv=fsync
  Needs:   UEFI + Secure Boot off; RTX 20xx or newer (nvidia-open = Turing+).
  Cache:   $WORKDIR (speeds up reruns; safe to delete)
EOF
