#!/bin/bash
# Shared by image builds and the updater running on the installed system.

pc_require_space() {
  local path="$1" required_mb="$2" available
  available="$(df -Pm "$path" | awk 'END {print $4}')"
  [[ "$available" =~ ^[0-9]+$ ]] && (( available >= required_mb )) || {
    echo "Not enough free space on $path: available ${available:-unknown} MiB, need at least ${required_mb} MiB." >&2
    return 1
  }
}

# The caller must have validated and disabled the inactive update slot.
# A small rootfs can have unused space stranded in metadata block groups.
pc_recover_update_space() {
  local root="$1" usage
  pc_require_space "$root" 256 2>/dev/null && return 0
  [[ "$root" != / && $(findmnt -n -o FSTYPE --mountpoint "$root") == btrfs ]] || {
    echo 'Space recovery requires a separate Btrfs update mount.' >&2
    return 1
  }
  echo 'Recovering unused Btrfs allocation in the update slot. Please wait.'
  # Filtered, bounded passes only; never convert profiles or delete files.
  for usage in 0 10 30 50; do
    btrfs balance start "-musage=$usage,limit=1" "$root" || {
      # ENOSPC can occur after a block group was released. Measure again.
      echo 'Metadata compaction did not fully complete; checking free space.' >&2
    }
    btrfs filesystem sync "$root" || return 1
    pc_require_space "$root" 256 2>/dev/null && return 0
  done
  pc_require_space "$root" 256
}

# Only use on the inactive, protected update slot or a disposable image.
# Btrfs compression makes the uncompressed payload an unsuitable space limit.
pc_copy_update_payload() {
  local source="$1" target="$2" files="$3" modules="$4" kernel="$5"
  pc_recover_update_space "$target" || return 1
  rsync -a --files-from="$files" "$source/" "$target/" || {
    echo 'Driver payload copy failed; the update slot must remain disabled.' >&2
    return 1
  }
  rsync -a "$modules" "$target/usr/lib/modules/$kernel/" || {
    echo 'Driver module copy failed; the update slot must remain disabled.' >&2
    return 1
  }
  # Wait for compression and delayed allocation before measuring free space.
  sync -f "$target" || return 1
  pc_recover_update_space "$target" || return 1
}

pc_package_matches() {
  local package="$1" version="$2"
  grep -oE "$package-[0-9][^\"< ]*-x86_64\\.pkg\\.tar\\.zst" |
    awk -v prefix="$package-$version" '
      index($0, prefix) == 1 {
        suffix=substr($0, length(prefix)+1)
        if (suffix ~ /^[.-]/) print
      }'
}

pc_changed_packages() {
  # Include packages that changed version, not only newly installed names.
  awk 'FILENAME == ARGV[1] {before[$1]=$2; next} !($1 in before) || before[$1] != $2 {print $1}' "$1" "$2"
}

pc_check_module() {
  local root="$1" kernel="$2" name="$3" expected="${4:-}" path magic version
  path="$(modinfo -b "$root" -k "$kernel" -F filename "$name")" || return 1
  [[ -s "$path" ]] || return 1
  magic="$(modinfo -F vermagic "$path")" || return 1
  [[ "${magic%% *}" == "$kernel" ]] || return 1
  if [[ -n "$expected" ]]; then
    version="$(modinfo -F version "$path")" || return 1
    [[ "$version" == "$expected" || "$version" == "${expected#v}" ]] || return 1
  fi
}

pc_check_driver() {
  local root="$1" kernel="$2" driver="$3" xpadneo="${4:-}" name
  for name in nvidia nvidia_modeset nvidia_drm nvidia_uvm; do
    pc_check_module "$root" "$kernel" "$name" "${driver%-*}" || return 1
  done
  [[ -s "$root/usr/share/vulkan/icd.d/nvidia_icd.json" ]] || return 1
  [[ -d "$root/usr/lib/holo/pacmandb/local/nvidia-utils-$driver" ]] || return 1
  if [[ -n "$xpadneo" ]]; then
    pc_check_module "$root" "$kernel" hid_xpadneo "$xpadneo" || return 1
    [[ -s "$root/etc/udev/rules.d/60-xpadneo.rules" &&
       -s "$root/etc/udev/rules.d/70-xpadneo-disable-hidraw.rules" &&
       -s "$root/etc/modules-load.d/xpadneo.conf" ]] || return 1
  fi
}

pc_slot_complete() {
  local root="$1" kernel="$2" expected="$3" driver="$4" xpadneo="${5:-}"
  [[ -f "$root/usr/lib/steamos-nvidia/complete" ]] || return 1
  [[ "$(cat "$root/usr/lib/steamos-nvidia/complete")" == "$kernel $expected" ]] || return 1
  pc_check_driver "$root" "$kernel" "$driver" "$xpadneo" || return 1
  pc_check_addons "$root" || return 1
  grep -Fxq "# steamos-nvidia shared-update-hook v1" "$root/usr/lib/rauc/post-install.sh" || return 1
  [[ -x "$root/usr/lib/steamos-nvidia/repatch.sh" &&
     -x "$root/usr/bin/steamos-update.orig" ]] || return 1
  grep -q 'self-healing' "$root/usr/bin/steamos-update" || return 1
  grep -q 'nvidia-drm.modeset=1' "$root/etc/default/grub" || return 1
  grep -q 'nvidia-drm.modeset=1' "$root/efi/EFI/steamos/grub.cfg" || return 1
}

pc_write_complete() {
  local root="$1" kernel="$2" fingerprint="$3"
  printf '%s %s\n' "$kernel" "$fingerprint" > "$root/usr/lib/steamos-nvidia/complete.part"
  mv "$root/usr/lib/steamos-nvidia/complete.part" "$root/usr/lib/steamos-nvidia/complete"
}

pc_install_display_policy() {
  local root="$1" session="$1/usr/lib/steamos/gamescope-session" temp
  local hook='. /usr/lib/steamos-nvidia/display-session.sh'
  [[ -f "$session" ]] || { echo "Missing Gamescope session: $session" >&2; return 1; }
  # Retire the ineffective legacy environment override, preserving Valve's file.
  if grep -Fxq "$hook" "$session"; then
    temp="$(mktemp "$session.XXXXXX")" || return 1
    grep -Fxv "$hook" "$session" > "$temp"
    chmod --reference="$session" "$temp"
    mv "$temp" "$session"
  fi
  [[ -f "$root/usr/lib/steamos-nvidia/hdr-defaults.py" ]] || return 1
  mkdir -p "$root/usr/lib/systemd/user/steam-launcher.service.d"
  cat > "$root/usr/lib/systemd/user/steam-launcher.service.d/10-nvidia-hdr-default.conf" <<'HDR_SERVICE'
[Service]
# Seed missing settings before Steam reads config.vdf. Never overwrite a choice.
ExecStartPre=/usr/bin/python3 /usr/lib/steamos-nvidia/hdr-defaults.py
HDR_SERVICE
  [[ -f "$root/usr/lib/steamos-nvidia/safe-graphics.py" ]] || return 1
  mkdir -p "$root/usr/bin" "$root/usr/share/applications" "$root/usr/lib/systemd/user/gamescope-session.service.d"
  ln -sfn /usr/lib/steamos-nvidia/safe-graphics.py "$root/usr/bin/steamos-nvidia-safe-graphics"
  cat > "$root/usr/lib/systemd/user/gamescope-session.service.d/20-nvidia-safe-graphics.conf" <<'SAFE_SERVICE'
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /usr/lib/steamos-nvidia/safe-graphics.py launch
SAFE_SERVICE
  cat > "$root/usr/share/applications/steamos-nvidia-safe-graphics.desktop" <<'SAFE_DESKTOP'
[Desktop Entry]
Type=Application
Name=Enable Safe Graphics for next Game Mode start
Exec=/usr/bin/steamos-nvidia-safe-graphics on
Icon=video-display
Terminal=true
Categories=Settings;
SAFE_DESKTOP
  cat > "$root/usr/share/applications/steamos-nvidia-normal-graphics.desktop" <<'NORMAL_DESKTOP'
[Desktop Entry]
Type=Application
Name=Restore normal Game Mode graphics
Exec=/usr/bin/steamos-nvidia-safe-graphics off
Icon=video-display
Terminal=true
Categories=Settings;
NORMAL_DESKTOP
  rm -f "$root/usr/lib/steamos-nvidia/display-session.sh" "$root/usr/bin/steamos-nvidia-display"
}

pc_set_ini_section() {
  local file="$1" section="$2" settings="$3" temp
  mkdir -p "$(dirname "$file")"
  if [[ -f "$file" ]]; then
    [[ -e "$file.before-xpadneo" ]] || cp -p "$file" "$file.before-xpadneo"
  else
    install -m 644 /dev/null "$file"
  fi
  temp="$(mktemp "$file.XXXXXX")" || return 1
  # Keep unrelated settings and comments; consolidate the section and its keys.
  if ! awk -v target="$section" -v settings="$settings" '
    BEGIN {
      n=split(settings, lines, "\n")
      for (i=1; i<=n; i++) { split(lines[i], pair, "="); managed[pair[1]]=1 }
    }
    /^[[:space:]]*\[/ {
      section=$0
      sub(/^[[:space:]]*\[/, "", section)
      sub(/\].*$/, "", section)
      active=(section == target)
      if (active) next
    }
    active {
      key=$0
      sub(/^[[:space:]]*/, "", key)
      sub(/[[:space:]]*=.*/, "", key)
      if (!(key in managed) && $0 !~ /^[[:space:]]*$/) kept=kept $0 "\n"
      next
    }
    { print }
    END {
      print "[" target "]"
      printf "%s", kept
      print settings
    }
  ' "$file" > "$temp"; then
    rm -f "$temp"
    return 1
  fi
  chmod --reference="$file" "$temp"
  chown --reference="$file" "$temp"
  mv "$temp" "$file"
}

pc_configure_xpadneo_bluetooth() {
  local root="$1"
  pc_set_ini_section "$root/etc/bluetooth/main.conf" General \
    $'ControllerMode=dual\nJustWorksRepairing=confirm'
  pc_set_ini_section "$root/etc/bluetooth/main.conf" LE \
    $'MinConnectionInterval=7\nMaxConnectionInterval=9\nConnectionLatency=0'
  pc_set_ini_section "$root/etc/bluetooth/input.conf" General \
    $'UserspaceHID=true\nClassicBondedOnly=false\nLEAutoSecurity=false'
}

pc_write_runtime_info() {
  local root="$1" kernel="$2" driver="$3" xpadneo="$4" trim="$5"
  mkdir -p "$root/usr/lib/steamos-nvidia"
  {
    printf 'Recorded UTC: %s\n' "$(date -u +%FT%TZ)"
    printf 'Kernel: %s\nNVIDIA: %s\nxpadneo: %s\nTrim CUDA: %s\n' \
      "$kernel" "$driver" "${xpadneo:-disabled}" "$trim"
    printf 'Hardware validation: not recorded by this script\n'
    cat "$root/etc/os-release"
  } > "$root/usr/lib/steamos-nvidia/runtime-info.txt.part"
  mv "$root/usr/lib/steamos-nvidia/runtime-info.txt.part" \
    "$root/usr/lib/steamos-nvidia/runtime-info.txt"
}

pc_install_xpadneo() {
  local root="$1" merged="$2" archive="$3" version="$4" kernel="$5" f
  [[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  [[ -s "$archive" ]] || return 1
  rm -rf "${merged:?}/tmp/xpadneo"
  mkdir -p "$merged/tmp/xpadneo"
  tar -xzf "$archive" --strip-components=1 -C "$merged/tmp/xpadneo"
  # Do not run upstream's install target: it reloads the host udev daemon.
  chroot "$merged" /bin/bash -seu -- "$version" <<'XPAD_BUILD'
version="$1"
cd /tmp/xpadneo
make VERSION="$version" build
if [[ -d "/var/lib/dkms/hid-xpadneo/$version" ]]; then
  dkms remove "hid-xpadneo/$version" --all
fi
rm -rf "/usr/src/hid-xpadneo-$version"
cp -a hid-xpadneo "/usr/src/hid-xpadneo-$version"
dkms add "hid-xpadneo/$version"
XPAD_BUILD
  chroot "$merged" dkms build "hid-xpadneo/$version" -k "$kernel"
  chroot "$merged" dkms install "hid-xpadneo/$version" -k "$kernel" --force
  mkdir -p "$root/etc/udev/rules.d" "$root/etc/modprobe.d" "$root/etc/modules-load.d"
  for f in 60-xpadneo.rules 70-xpadneo-disable-hidraw.rules; do
    install -m 644 "$merged/tmp/xpadneo/hid-xpadneo/etc-udev-rules.d/$f" "$root/etc/udev/rules.d/$f"
  done
  install -m 644 "$merged/tmp/xpadneo/hid-xpadneo/etc-modprobe.d/xpadneo.conf" "$root/etc/modprobe.d/xpadneo.conf"
  printf 'uhid\nhid_xpadneo\n' > "$root/etc/modules-load.d/xpadneo.conf"
  printf 'softdep hid_xpadneo pre: uhid\n' > "$root/etc/modprobe.d/99-xpadneo.conf"
  chroot "$merged" modinfo -k "$kernel" uhid >/dev/null
  pc_configure_xpadneo_bluetooth "$root"
}

pc_copy_package_db() {
  local merged="$1" root="$2" pkg="$3" db entry name source="" count=0
  db=usr/lib/holo/pacmandb/local
  # Use the recorded name; glob prefixes can match other packages.
  for entry in "$merged/$db/$pkg"-[0-9]*; do
    [[ -f "$entry/desc" ]] || continue
    name="$(awk '/^%NAME%$/ {getline; print; exit}' "$entry/desc")"
    [[ "$name" == "$pkg" ]] || continue
    source="$entry"; count=$((count + 1))
  done
  [[ $count -eq 1 ]] || { echo "Ambiguous or missing package metadata: $pkg" >&2; return 1; }
  local staged
  staged="$(mktemp -d)"
  cp -a "$source" "$staged/" || { rmdir "$staged"; return 1; }
  for entry in "$root/$db/$pkg"-[0-9]*; do
    [[ -f "$entry/desc" ]] || continue
    name="$(awk '/^%NAME%$/ {getline; print; exit}' "$entry/desc")"
    [[ "$name" != "$pkg" ]] || rm -rf "$entry"
  done
  mkdir -p "$root/$db"
  cp -a "$staged/$(basename "$source")" "$root/$db/"
  rm -rf "$staged"
}

# Enable in user sessions; do not change pairing or adapter settings.
pc_install_bluetooth_resume() {
  local root="$1" unit=steamos-nvidia-bluetooth-resume.service
  [[ -f "$root/usr/lib/steamos-nvidia/bluetooth-resume.py" ]] || return 1
  chroot "$root" /usr/bin/python3 -c 'import dbus; from gi.repository import GLib' || {
    echo 'Bluetooth audio resume requires python-dbus and python-gobject.' >&2
    return 1
  }
  mkdir -p "$root/usr/lib/systemd/user/default.target.wants" || return 1
  cat > "$root/usr/lib/systemd/user/$unit" <<'BT_SERVICE'
[Unit]
Description=Reconnect previous Bluetooth audio after resume

[Service]
ExecStart=/usr/bin/python3 /usr/lib/steamos-nvidia/bluetooth-resume.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
BT_SERVICE
  ln -sfn "../$unit" "$root/usr/lib/systemd/user/default.target.wants/$unit"
}

# Manifest covers shipped files, not preferences or version-dependent capture status.
pc_write_addon_manifest() {
  local root="$1"
  (cd "$root" && sha256sum \
    usr/lib/steamos-nvidia/hdr-defaults.py \
    usr/lib/steamos-nvidia/safe-graphics.py \
    usr/lib/steamos-nvidia/bluetooth-resume.py \
    usr/lib/steamos-nvidia/install-target.py \
    usr/bin/steamos-nvidia-diagnostics \
    usr/lib/systemd/user/steam-launcher.service.d/10-nvidia-hdr-default.conf \
    usr/lib/systemd/user/gamescope-session.service.d/20-nvidia-safe-graphics.conf \
    usr/lib/systemd/user/steamos-nvidia-bluetooth-resume.service \
    usr/share/applications/steamos-nvidia-safe-graphics.desktop \
    usr/share/applications/steamos-nvidia-normal-graphics.desktop) > "$root/usr/lib/steamos-nvidia/addons.sha256"
  if [[ -d "$root/usr/lib/steamos-nvidia/nvenc" ]]; then
    (cd "$root" && find usr/lib/steamos-nvidia/nvenc -type f -print0 | sort -z | xargs -0 sha256sum && sha256sum usr/lib32/dri/nvidia_drv_video.so usr/lib/systemd/user/steamos-nvidia-nvenc.service usr/lib/systemd/user/steam-launcher.service.d/45-nvidia-nvenc.conf) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -d "$root/usr/lib/steamos-nvidia/remote-play" ]]; then
    (cd "$root" && find usr/lib/steamos-nvidia/remote-play -type f -print0 | sort -z | xargs -0 sha256sum && sha256sum usr/lib/steamos-nvidia/remote-play-env.py usr/lib/systemd/user/steam-launcher.service.d/40-nvidia-remote-play.conf) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -d "$root/usr/lib/steamos-nvidia/gamescope" ]]; then
    (cd "$root" && find usr/lib/steamos-nvidia/gamescope -type f ! -path usr/lib/steamos-nvidia/gamescope/status.txt -print0 | sort -z | xargs -0 sha256sum) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -f "$root/usr/lib/steamos-nvidia/mangoapp" ]]; then
    (cd "$root" && sha256sum usr/lib/steamos-nvidia/mangoapp usr/lib/steamos-nvidia/mangoapp-build.json usr/lib/steamos-nvidia/MangoHud-LICENSE usr/lib/systemd/user/gamescope-mangoapp.service.d/30-nvidia-metrics.conf) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -f "$root/usr/lib/steamos-nvidia/driver-change.py" ]]; then
    (cd "$root" && sha256sum usr/lib/steamos-nvidia/driver-change.py usr/lib/steamos-nvidia/driver-stage.sh) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -f "$root/usr/lib/steamos-nvidia/driver-manager.py" ]]; then
    (cd "$root" && sha256sum usr/lib/steamos-nvidia/driver-manager.py usr/lib/steamos-nvidia/change-nvidia-driver.png usr/share/applications/steamos-nvidia-driver.desktop etc/xdg/autostart/steamos-nvidia-driver-shortcut.desktop) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -f "$root/usr/lib/steamos-nvidia/installer-update.py" ]]; then
    (cd "$root" && sha256sum usr/lib/steamos-nvidia/installer-update.py usr/lib/steamos-nvidia/installer-update-ui.py usr/lib/steamos-nvidia/installer-update.png usr/lib/steamos-nvidia/installer-update-source.json usr/lib/steamos-nvidia/integration-version.json usr/share/applications/steamos-installer-update.desktop etc/xdg/autostart/steamos-installer-update-shortcut.desktop) >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
  fi
  if [[ -f "$root/usr/lib/steamos-nvidia/install-authorized" ]]; then
    (cd "$root" && sha256sum usr/lib/steamos-nvidia/install-authorized \
      usr/lib/steamos-nvidia/installer/repair_device.sh etc/sudoers.d/zz-steamos-installer) \
      >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
    if [[ -f "$root/usr/lib/steamos-nvidia/installer/steamos-branch" ]]; then
      (cd "$root" && sha256sum usr/lib/steamos-nvidia/installer/steamos-branch) \
        >> "$root/usr/lib/steamos-nvidia/addons.sha256" || return 1
    fi
  fi
}

pc_check_addons() {
  local root="$1" unit=steamos-nvidia-bluetooth-resume.service
  [[ -s "$root/usr/lib/steamos-nvidia/addons.sha256" ]] || {
    echo 'Missing addon manifest.' >&2; return 1;
  }
  (cd "$root" && sha256sum --check usr/lib/steamos-nvidia/addons.sha256) || return 1
  [[ $(readlink "$root/usr/bin/steamos-nvidia-safe-graphics") == /usr/lib/steamos-nvidia/safe-graphics.py ]] || {
    echo 'Safe Graphics command link is missing or changed.' >&2; return 1;
  }
  [[ $(readlink "$root/usr/lib/systemd/user/default.target.wants/$unit") == "../$unit" ]] || {
    echo 'Bluetooth audio resume activation link is missing or changed.' >&2; return 1;
  }
}

# The target's loader checks its own dependency graph and symbol versions.
# --list does not start NVIDIA applications or require a GPU.
pc_check_userspace() {
  local root="$1" bits loader directory library output failed=0
  for bits in 64 32; do
    if [[ $bits == 64 ]]; then
      loader=/usr/lib/ld-linux-x86-64.so.2
      directory=/usr/lib
    else
      loader=/usr/lib32/ld-linux.so.2
      directory=/usr/lib32
    fi
    for library in libGLX_nvidia.so.0 libEGL_nvidia.so.0; do
      printf '\nChecking %s-bit %s/%s\n' "$bits" "$directory" "$library"
      if output="$(chroot "$root" /usr/bin/env -i LC_ALL=C "$loader" --list "$directory/$library" 2>&1)"; then
        printf '%s\n' "$output"
      else
        printf '%s\n' "$output" >&2
        printf 'FAILED: %s-bit NVIDIA dependencies in the target system.\n' "$bits" >&2
        failed=1
      fi
    done
  done
  printf '\nChecking 64-bit NVIDIA management library\n'
  if output="$(chroot "$root" /usr/bin/env -i LC_ALL=C /usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/libnvidia-ml.so.1 2>&1)"; then
    printf '%s\n' "$output"
  else
    printf '%s\n' "$output" >&2
    failed=1
  fi
  return "$failed"
}

pc_write_userspace_report() {
  local root="$1" report="$1/usr/lib/steamos-nvidia/userspace-check.txt" status=0
  mkdir -p "$root/usr/lib/steamos-nvidia" || return 1
  {
    printf 'Checked UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'NVIDIA dependency check; not a GPU rendering test.\n'
    pc_check_userspace "$root" || status=$?
    printf '\nResult: %s\n' "$status"
  } > "$report.part" 2>&1
  mv "$report.part" "$report" || return 1
  cat "$report"
  return "$status"
}

# Shared timeout and retry policy for public metadata and package downloads.
pc_curl() {
  curl --connect-timeout 15 --max-time 300 --retry 2 --retry-delay 2 --retry-max-time 600 "$@"
}

pc_package_fallback() {
  local url="$1" file package repo
  file="${url##*/}"
  [[ "$file" =~ ^(.+)-([^-]+)-([^-]+)-x86_64\.pkg\.tar\.zst$ ]] || return 1
  package="${BASH_REMATCH[1]}"
  case "$package" in
    nvidia-utils|nvidia-open-dkms|egl-wayland2) repo=extra ;;
    lib32-nvidia-utils) repo=multilib ;;
    *) return 1 ;;
  esac
  if [[ "$url" == "https://archive.archlinux.org/packages/${package:0:1}/$package/$file" ]]; then
    printf 'https://geo.mirror.pkgbuild.com/%s/os/x86_64/%s\n' "$repo" "$file"
  elif [[ "$url" == "https://geo.mirror.pkgbuild.com/$repo/os/x86_64/$file" ]]; then
    printf 'https://archive.archlinux.org/packages/%s/%s/%s\n' "${package:0:1}" "$package" "$file"
  else
    return 1
  fi
}

pc_download() {
  local url="$1" destination="$2" alternate="${3:-}" temp code status candidate
  mkdir -p "$(dirname "$destination")" || return 1
  temp="$(mktemp "$destination.part.XXXXXX")" || return 1
  for candidate in "$url" "$alternate"; do
    [[ -n "$candidate" ]] || continue
    status=0
    code="$(pc_curl --fail --silent --show-error --location --output "$temp" --write-out '%{http_code}' "$candidate")" || status=$?
    if [[ $status -eq 0 && -s "$temp" ]]; then
      mv -f "$temp" "$destination" || { rm -f "$temp"; return 1; }
      return 0
    fi
    case "$code" in
      404|410) printf 'Exact file unavailable (HTTP %s): %s\n' "$code" "$candidate" >&2 ;;
      *) printf 'Download failed (curl %s, HTTP %s): %s\n' "$status" "${code:-unknown}" "$candidate" >&2 ;;
    esac
    # Never publish a partial download or replace it with a different version.
    : > "$temp"
  done
  rm -f "$temp"
  return 1
}

pc_download_package() {
  local fallback
  fallback="$(pc_package_fallback "$1")" || fallback=""
  pc_download "$1" "$2" "$fallback"
}

pc_active_root_id() {
  local source
  source="$(findmnt -rn -o SOURCE /)" || return 1
  source="${source%%\[*}"
  [[ "$source" == /dev/* ]] || return 1
  lsblk -dn -o MAJ:MIN "$source"
}

# Explicit opt-in for experimental images; default builds retain Valve's channel.
pc_select_beta_branch() { pc_select_experimental_branch "$1" beta; }

pc_select_experimental_branch() {
  python3 - "$1" "$2" <<'PYBRANCH'
import configparser
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
branch = sys.argv[2]
if branch not in ('beta', 'preview'):
    raise SystemExit('Unsupported experimental update channel')
manifest = json.loads((root / 'etc/steamos-atomupd/manifest.json').read_text())
if manifest.get('variant') not in ('steamdeck', 'steamdeck-oobe'):
    raise SystemExit('Experimental channels currently support only the steamdeck variant')
path = root / 'etc/steamos-atomupd/preferences.conf'
config = configparser.ConfigParser()
config.optionxform = str
if path.exists():
    config.read(path)
if not config.has_section('Choices'):
    config.add_section('Choices')
config['Choices']['Variant'] = 'steamdeck'
config['Choices']['Branch'] = branch
with path.open('w') as output:
    config.write(output, space_around_delimiters=False)
PYBRANCH
}

# RAUC runs this hook for both atomupd D-Bus and the legacy CLI.
pc_install_update_policy() {
  python3 - "$1/usr/lib/rauc/post-install.sh" <<'PYHOOK'
import os
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
# Preserve Valve's activation mode and the inspected tool namespace.
anchors = [f'(( ERR == 0 )) && steamos-bootconf --image $UPDATED_SLOT set-mode {mode}'
           for mode in ('reboot', 'first-boot')]
anchors.append('(( ERR == 0 )) && holo-bootconf --image $UPDATED_SLOT set-mode first-boot')
matches = [candidate for candidate in anchors if candidate in text]
if len(matches) != 1 or text.count(matches[0]) != 1:
    raise SystemExit('Unsupported RAUC activation step; refusing to install update hook')
anchor = matches[0]
block = """# steamos-nvidia shared-update-hook v1
if (( ERR == 0 )); then
    [[ "$UPDATED_SLOT" == A || "$UPDATED_SLOT" == B ]] || fail "Unknown NVIDIA update slot"
    [[ "$UPDATED_SLOT" != "$BOOTED_SLOT" ]] || fail "Refusing to repair the active slot"
    steamos-bootconf --image "$UPDATED_SLOT" config --no-create --set image-invalid 1 --set boot-requested-at 0 --set boot-attempts 0 || fail "Cannot protect NVIDIA update slot"
    sync -f /esp/SteamOS/conf || fail "Cannot sync NVIDIA update protection"
    /usr/lib/steamos-nvidia/repatch.sh other >> /var/log/steamos-nvidia-repatch.log 2>&1 || fail "NVIDIA repair failed; the updated slot remains disabled"
fi
# steamos-nvidia shared-update-hook end
"""
if 'holo-bootconf' in anchor:
    block = block.replace('    steamos-bootconf ', '    holo-bootconf ')
legacy_guard = """# steamos-nvidia update-mount-guard v1
# Keep atime writes out of the frozen var-backed etc overlay.
if [[ ${STEAMOS_NVIDIA_UPDATE_MOUNT_NS:-0} != 1 ]]; then
    exec /usr/bin/unshare --mount --propagation private /usr/bin/env STEAMOS_NVIDIA_UPDATE_MOUNT_NS=1 /bin/bash "$0" "$@"
fi
unset STEAMOS_NVIDIA_UPDATE_MOUNT_NS
/usr/bin/mount -o remount,bind,noatime /var || exit 1
/usr/bin/mount -o remount,bind,noatime /etc || exit 1
# steamos-nvidia update-mount-guard end
"""
# Receive systemd automounts, but never propagate our mounts back to the host.
guard = legacy_guard.replace('update-mount-guard v1', 'update-mount-guard v2').replace('--propagation private', '--propagation slave')
if text.startswith('#!/bin/bash\n' + legacy_guard) and text.count(legacy_guard) == 1:
    text = text.replace(legacy_guard, guard, 1)
    upgraded_legacy_guard = True
else:
    upgraded_legacy_guard = False
if not text.startswith('#!/bin/bash\n'):
    raise SystemExit('Unsupported RAUC interpreter')
if 'update-mount-guard' in text and (text.count(guard) != 1 or not text.startswith('#!/bin/bash\n' + guard)):
    raise SystemExit('Unexpected existing update mount guard')
if 'shared-update-hook' in text and not (block + anchor in text and text.count('shared-update-hook v1') == 1):
    raise SystemExit('Unexpected existing NVIDIA update hook')
updated = text if block + anchor in text else text.replace(anchor, block + anchor)
if guard not in updated:
    updated = updated.replace('#!/bin/bash\n', '#!/bin/bash\n' + guard, 1)
# Only the inspected Valve ordering is supported.
finalize = 'steamos-chroot --partset $UPDATED_SLOT -- steamos-finalize-install --no-kernel'
if 'holo-bootconf' in anchor:
    finalize = """declare -ar FINISH=({holo,steamos}-finalize-install steamos-boot-install)
declare -i  FINISHED=0

for finish in ${FINISH[@]}
do
    if holo-chroot --partset $UPDATED_SLOT -- $finish --no-kernel
    then
        FINISHED=1
        break
    fi
done

if [ $FINISHED -ne 1 ]
then
    err "Failed to install bootloaders (tried: ${FINISH[*]})"
fi"""
    # Keep Valve's shutdown catch-up after activation. It migrates /etc and /var,
    # not the repaired /usr payload. Never replace this with early activation.
    trigger = 'echo "$BOOTED_SLOT" > "$HOLO_BOOTED_SLOT_SYNC_TRIGGER"'
    if text.count(trigger) != 1 or text.index(trigger) < text.index(anchor):
        raise SystemExit('Unsupported RAUC shutdown synchronization')
if text.count(finalize) != 1 or text.index(finalize) > text.index(anchor):
    raise SystemExit('Unsupported RAUC finalization order')
if updated == text and not upgraded_legacy_guard:
    sys.exit(0)
temp = path.with_name(path.name + '.nvidia-part')
try:
    temp.write_text(updated)
    os.chmod(temp, path.stat().st_mode)
    os.replace(temp, path)
finally:
    temp.unlink(missing_ok=True)
PYHOOK
}

# Preview exposes steamos-update as an absolute alias to holo-update.
# Keep the original executable inside this slot, without following a host path.
pc_preserve_update_command() {
  local root="$1" command="$1/usr/bin/steamos-update" original="$1/usr/bin/steamos-update.orig"
  [[ ! -L "$original" && -x "$original" ]] && return 0
  if [[ -L "$command" ]]; then
    [[ $(readlink "$command") == /usr/bin/holo-update &&
       ! -L "$root/usr/bin/holo-update" && -x "$root/usr/bin/holo-update" ]] || return 1
    cp --remove-destination "$root/usr/bin/holo-update" "$original" || return 1
    rm -- "$command" || return 1
  else
    [[ -x "$command" ]] || return 1
    mv -- "$command" "$original" || return 1
  fi
}

# Only this fixed, root-owned entry point can run without a password.
pc_install_installer_permissions() {
  local root="$1" dest="$1/usr/lib/steamos-nvidia" old="$1/etc/sudoers.d/zz-deck-nopasswd"
  [[ -f "$dest/installer/repair_device.sh" ]] || return 0
  grep -Fxq 'VENDORED_BIOS_UPDATE=/usr/lib/steamos-nvidia/installer/no-vendored-bios' "$dest/installer/repair_device.sh" || return 1
  grep -Fxq 'VENDORED_CONTROLLER_UPDATE=/usr/lib/steamos-nvidia/installer/no-vendored-controller' "$dest/installer/repair_device.sh" || return 1
  mkdir -p "$root/etc/sudoers.d"
  cat > "$dest/install-authorized" <<'AUTHORIZED'
#!/bin/sh
# Isolated Python ignores caller-provided Python paths and startup files.
test "$#" -eq 3 || exit 2
exec /usr/bin/python3 -I /usr/lib/steamos-nvidia/install-target.py "$1" --execute "$2" "$3"
AUTHORIZED
  chmod 755 "$dest/install-authorized"
  printf '%s\n' 'deck ALL=(root) NOPASSWD: NOSETENV: /usr/lib/steamos-nvidia/install-authorized' > "$root/etc/sudoers.d/zz-steamos-installer"
  chmod 440 "$root/etc/sudoers.d/zz-steamos-installer"
  chown -R 0:0 "$dest/installer" "$dest/install-authorized" "$root/etc/sudoers.d/zz-steamos-installer"
  chroot "$root" /usr/sbin/visudo -cf /etc/sudoers.d/zz-steamos-installer || return 1
  if [[ -f "$old" ]]; then
    if [[ $(cat "$old") == 'deck ALL=(ALL) NOPASSWD: ALL' ]]; then
      rm -f "$old"
    else
      echo 'Custom zz-deck-nopasswd rule requires review; refusing to replace it.' >&2
      return 1
    fi
  fi
}

# Exact unowned paths only, for reinstalling build tools in a disposable overlay.
pc_build_overwrites() {
  local root="$1" archive file relative files result=""
  shift
  for archive in "$@"; do
    [[ -f "$archive" ]] || continue
    files="$(pacman -Qqlp "$archive")" || return 1
    while IFS= read -r file; do
      relative="${file#/}"
      [[ -e "$root/$relative" || -L "$root/$relative" ]] || continue
      [[ ! -d "$root/$relative" || -L "$root/$relative" ]] || continue
      [[ "$relative" =~ ^[a-zA-Z0-9_./+@-]+$ && "$relative" != *../* ]] || return 1
      chroot "$root" pacman -Qo -- "/$relative" >/dev/null 2>&1 && continue
      result+="${result:+,}$relative"
    done <<< "$files"
  done
  printf '%s' "$result"
}

# Only for an inactive slot: reclaim old versioned NVIDIA files before copying.
pc_remove_obsolete_driver_files() {
  python3 - "$1" "$2" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
wanted = set(Path(sys.argv[2]).read_text().splitlines())
obsolete = set()
for package in ('nvidia-utils', 'lib32-nvidia-utils'):
    for manifest in (root / 'usr/lib/holo/pacmandb/local').glob(package + '-[0-9]*/files'):
        recording = False
        for line in manifest.read_text().splitlines():
            if line.startswith('%'):
                recording = line == '%FILES%'
                continue
            if not recording or not line or line.endswith('/') or line in wanted:
                continue
            relative = Path(line)
            if relative.is_absolute() or '..' in relative.parts:
                raise SystemExit('Invalid path in NVIDIA package database')
            path = root / relative
            if not path.parent.resolve().is_relative_to(root):
                raise SystemExit('NVIDIA package path leaves the inactive root')
            if path.is_symlink() or path.is_file():
                obsolete.add(path)
for path in sorted(obsolete):
    path.unlink()
print(f'Removed {len(obsolete)} obsolete NVIDIA files from the inactive slot')
PY
}

# Install the launcher and create the desktop shortcut only in the installed OS.
pc_install_driver_manager() {
  local root="$1"
  [[ -f "$root/usr/lib/steamos-nvidia/driver-manager.py" ]] || return 1
  mkdir -p "$root/usr/share/applications" "$root/etc/xdg/autostart"
  cat > "$root/usr/share/applications/steamos-nvidia-driver.desktop" <<'DRIVER_DESKTOP'
[Desktop Entry]
Type=Application
Name=Change NVIDIA Driver
Comment=Change the NVIDIA driver or return to the previous version
Exec=/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-manager.py
Icon=/usr/lib/steamos-nvidia/change-nvidia-driver.png
Terminal=false
Categories=System;Settings;
DRIVER_DESKTOP
  cat > "$root/etc/xdg/autostart/steamos-nvidia-driver-shortcut.desktop" <<'DRIVER_SHORTCUT'
[Desktop Entry]
Type=Application
Name=NVIDIA Driver shortcut
Exec=/usr/bin/python3 -I /usr/lib/steamos-nvidia/driver-manager.py --shortcut
NoDisplay=true
DRIVER_SHORTCUT
}

# Desktop project updater; source and signing key are supplied by the image build.
pc_install_installer_update() {
  local root="$1"
  [[ -f "$root/usr/lib/steamos-nvidia/installer-update.py" ]] || return 0
  mkdir -p "$root/usr/share/applications" "$root/etc/xdg/autostart"
  ln -sfn /usr/lib/steamos-nvidia/installer-update.py "$root/usr/bin/steamos-nvidia-installer-update"
  cat > "$root/usr/share/applications/steamos-installer-update.desktop" <<'UPDATE_DESKTOP'
[Desktop Entry]
Type=Application
Name=SteamOS NVIDIA Installer Update
Comment=Update installer tools and fixes
Exec=/usr/bin/python3 -I /usr/lib/steamos-nvidia/installer-update-ui.py
Icon=/usr/lib/steamos-nvidia/installer-update.png
Terminal=false
Categories=System;Settings;
UPDATE_DESKTOP
  cat > "$root/etc/xdg/autostart/steamos-installer-update-shortcut.desktop" <<'UPDATE_SHORTCUT'
[Desktop Entry]
Type=Application
Name=SteamOS NVIDIA Installer Update shortcut
Exec=/usr/bin/python3 -I /usr/lib/steamos-nvidia/installer-update-ui.py --shortcut
NoDisplay=true
UPDATE_SHORTCUT
}


pc_install_mangoapp() {
  local root="$1" base="$1/usr/lib/steamos-nvidia"
  [[ -f "$base/mangoapp" ]] || return 0
  python3 - "$base" <<'MANGO_VERIFY'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);m=json.loads((p/'mangoapp-build.json').read_text())
if hashlib.sha256((p/'mangoapp').read_bytes()).hexdigest()!=m['sha256']:
    raise SystemExit('MangoApp artifact checksum mismatch')
if not (p/'MangoHud-LICENSE').is_file():
    raise SystemExit('MangoHud license is missing')
MANGO_VERIFY
  [[ $? == 0 ]] || return 1
  chroot "$root" /usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/steamos-nvidia/mangoapp >/dev/null || return 1
  mkdir -p "$root/usr/lib/systemd/user/gamescope-mangoapp.service.d"
  cat > "$root/usr/lib/systemd/user/gamescope-mangoapp.service.d/30-nvidia-metrics.conf" <<'MANGO_SERVICE'
[Service]
ExecStart=
ExecStart=/usr/lib/steamos-nvidia/mangoapp
MANGO_SERVICE
}

# Keep the capture backport separate from Valve's executable. Unknown target
# versions retain stock Gamescope instead of carrying an older compositor forward.
pc_install_gamescope() {
  local root="$1" base="$1/usr/lib/steamos-nvidia/gamescope" result version
  local override="$1/usr/lib/systemd/user/gamescope-session.service.d/30-nvidia-capture.conf"
  [[ -d "$base" ]] || return 0
  python3 - "$base" <<'CAPTURE_VERIFY'
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]); m=json.loads((p/'gamescope-build.json').read_text())
if m.get('commit') != '2b79e07b3da1723c7e5c5f44f18de36c6cb78b9e':
    raise SystemExit('Unsupported Gamescope capture artifact')
if hashlib.sha256((p/'bin/gamescope').read_bytes()).hexdigest() != m['files']['usr/bin/gamescope']:
    raise SystemExit('Gamescope capture checksum mismatch')
if not (p/'Gamescope-LICENSE').is_file():
    raise SystemExit('Missing Gamescope license')
CAPTURE_VERIFY
  [[ $? == 0 ]] || return 1
  result=stock
  version=$(chroot "$root" pacman -Q gamescope 2>/dev/null) || version=unknown
  if grep -Eq '^VERSION_ID="?3\.8\.16"?$' "$root/etc/os-release" &&
     [[ $version == 'gamescope 3.16.23.4-1' ]] &&
     grep -Eq '^exec gamescope[[:space:]]' "$root/usr/lib/steamos/gamescope-session"; then
    chroot "$root" /usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/steamos-nvidia/gamescope/bin/gamescope >/dev/null || return 1
    mkdir -p "$(dirname "$override")"
    cat > "$override" <<'CAPTURE_SERVICE'
[Service]
Environment="PATH=/usr/lib/steamos-nvidia/gamescope/bin:/usr/local/sbin:/usr/local/bin:/usr/bin"
CAPTURE_SERVICE
    result=capture-backport
  else
    rm -f "$override"
  fi
  printf '%s (%s)\n' "$result" "$version" > "$base/status.txt"
  echo "Gamescope selection: $result ($version)"
}

# Optional receiver artifact. No files in the user's Steam installation are edited.
pc_install_remote_play() {
  local root="$1" base="$1/usr/lib/steamos-nvidia/remote-play"
  [[ -d "$base" ]] || return 0
  python3 - "$base" <<'REMOTE_VERIFY'
import hashlib,json,sys
from pathlib import Path
base=Path(sys.argv[1]);meta=json.loads((base/'remote-play-build.json').read_text())
required={'dri/nvidia_drv_video.so','lib/receiver-env.so','lib32/receiver-env.so','licenses/nvidia-vaapi-driver-LICENSE','receiver-env.c'}
if meta.get('commit')!='a03711106b5e297a64a704c876aeb776cbce957b' or not required <= meta.get('files',{}).keys():
    raise SystemExit('Incomplete remote-play artifact')
for name,digest in meta['files'].items():
    file=base/name
    if Path(name).is_absolute() or '..' in Path(name).parts or file.is_symlink() or not file.resolve().is_relative_to(base.resolve()) or not file.is_file():
        raise SystemExit('Unsafe remote-play artifact path')
    if hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
        raise SystemExit('Remote-play artifact checksum mismatch: '+name)
REMOTE_VERIFY
  [[ $? == 0 ]] || return 1
  [[ -f "$root/usr/lib/steamos-nvidia/remote-play-env.py" ]] || return 1
  chroot "$root" /usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/steamos-nvidia/remote-play/dri/nvidia_drv_video.so >/dev/null || return 1
  chroot "$root" /usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/steamos-nvidia/remote-play/lib/receiver-env.so >/dev/null || return 1
  mkdir -p "$root/usr/lib/systemd/user/steam-launcher.service.d"
  cat > "$root/usr/lib/systemd/user/steam-launcher.service.d/40-nvidia-remote-play.conf" <<'REMOTE_SERVICE'
[Service]
EnvironmentFile=-%t/steamos-nvidia-remote-play.env
ExecStartPre=/usr/bin/python3 /usr/lib/steamos-nvidia/remote-play-env.py
REMOTE_SERVICE
}

# Optional sending-side bridge; the private 64-bit receiver is not changed.
pc_install_nvenc() {
  local root="$1" base="$1/usr/lib/steamos-nvidia/nvenc"
  [[ -d "$base" ]] || return 0
  python3 - "$base" <<'NVENC_VERIFY'
import hashlib,json,sys
from pathlib import Path
base=Path(sys.argv[1]);meta=json.loads((base/'nvenc-build.json').read_text())
required={'lib32/nvidia_drv_video.so','nvenc-helper','COPYING'}
if meta.get('commit')!='3a58095f1833c997fd4f0a73ce3fa0300cdc20fc' or not required <= meta.get('files',{}).keys():
    raise SystemExit('Incomplete NVENC artifact')
for name,digest in meta['files'].items():
    file=base/name
    if Path(name).is_absolute() or '..' in Path(name).parts or file.is_symlink() or not file.resolve().is_relative_to(base.resolve()) or not file.is_file():
        raise SystemExit('Unsafe NVENC artifact path')
    if hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
        raise SystemExit('NVENC artifact checksum mismatch: '+name)
NVENC_VERIFY
  [[ $? == 0 ]] || return 1
  chroot "$root" /usr/lib/ld-linux-x86-64.so.2 --list /usr/lib/steamos-nvidia/nvenc/nvenc-helper >/dev/null || return 1
  chroot "$root" /usr/lib/ld-linux.so.2 --list /usr/lib/steamos-nvidia/nvenc/lib32/nvidia_drv_video.so >/dev/null || return 1
  install -D -m 755 "$base/lib32/nvidia_drv_video.so" "$root/usr/lib32/dri/nvidia_drv_video.so" || return 1
  mkdir -p "$root/usr/lib/systemd/user/steam-launcher.service.d"
  cat > "$root/usr/lib/systemd/user/steamos-nvidia-nvenc.service" <<'NVENC_UNIT'
[Unit]
Description=NVIDIA hardware encoding helper for Steam Remote Play
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/lib/steamos-nvidia/nvenc/nvenc-helper
Restart=on-failure
RestartSec=2
UMask=0077
NoNewPrivileges=yes
NVENC_UNIT
  cat > "$root/usr/lib/systemd/user/steam-launcher.service.d/45-nvidia-nvenc.conf" <<'NVENC_STEAM'
[Unit]
Wants=steamos-nvidia-nvenc.service
After=steamos-nvidia-nvenc.service
NVENC_STEAM
}
