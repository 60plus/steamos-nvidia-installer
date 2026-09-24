#!/bin/bash
# Shared checks for the artifact builders. Sourced, never executed.
#
# The artifacts are compiled against the libraries of the build root and then
# shipped inside a stable SteamOS image. A beta, Preview or main root carries a
# newer glibc and C++ runtime, so an artifact built there can fail at load time
# on the machine that receives it. Refuse the wrong channel outright; a
# different point release inside stable only warrants a warning.

check_stable_steamos_root() {
  local root="$1" stray
  [[ -f "$root/etc/os-release" && -f "$root/etc/pacman.conf" ]] \
    || { printf 'Not a prepared SteamOS build root, os-release or pacman.conf is missing: %s\n' "$root" >&2; return 1; }
  grep -Eq '^ID="?steamos"?$' "$root/etc/os-release" \
    || { printf 'The build root is not SteamOS: %s\n' "$root" >&2; return 1; }
  grep -Eq '^VERSION_ID="?3\.8\.' "$root/etc/os-release" \
    || { printf 'The build root is not a SteamOS 3.8 release: %s\n' "$root" >&2; return 1; }
  if stray=$(grep -E '^\[' "$root/etc/pacman.conf" | grep -Ev '^\[(options|[a-z0-9_-]+-3\.8\.1x)\]'); then
    printf 'The build root uses package repositories outside the stable 3.8.1x series:\n%s\n' "$stray" >&2
    printf 'Beta, Preview and main libraries do not belong in a stable image.\n' >&2
    return 1
  fi
  grep -Eq '^\[[a-z0-9_-]+-3\.8\.1x\]' "$root/etc/pacman.conf" \
    || { printf 'The build root has no stable 3.8.1x package repository: %s\n' "$root" >&2; return 1; }
  grep -Eq '^VERSION_ID="?3\.8\.1[46]"?$' "$root/etc/os-release" \
    || printf 'Warning: the build root is not SteamOS 3.8.14 or 3.8.16. Artifacts built here need fresh compatibility testing.\n' >&2
}

require_stable_build_root() {
  local root="$1"
  [[ $EUID == 0 ]] || { printf 'Run with sudo.\n' >&2; return 1; }
  [[ "$root" != / ]] || { printf 'Refusing to build against the running system. Use a disposable build root.\n' >&2; return 1; }
  check_stable_steamos_root "$root" || return 1
  # The root must already have /proc, /dev and DNS available.
  mountpoint -q "$root/proc" \
    || { printf 'The build root has no /proc mounted. Prepare it first: %s\n' "$root" >&2; return 1; }
}
