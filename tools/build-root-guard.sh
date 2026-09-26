#!/bin/bash
# Shared checks for the artifact builders. Sourced, never executed.
#
# The artifacts are compiled against the libraries of the build root and then
# shipped inside the SteamOS image built from that same root. A root whose
# package repositories belong to another SteamOS line carries a different glibc
# and C++ runtime, so an artifact built there can fail at load time on the
# machine that receives it. Refuse a mixed root outright.
#
# A release we have not validated is a warning, not a refusal. The version
# number alone does not make an artifact incompatible, and Valve replaces the
# recovery image without asking us. Validated releases are listed in
# config/build-baselines.json.

steamos_baseline_list() {
  local file
  file="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/config/build-baselines.json"
  [[ -r "$file" ]] || return 1
  python3 -c "import json,sys;print(' '.join(json.load(open(sys.argv[1]))[sys.argv[2]]))" \
    "$file" "$1" 2>/dev/null
}

steamos_root_version() {
  sed -n 's/^VERSION_ID="\?\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' \
    "$1/etc/os-release" | head -n 1
}

check_stable_steamos_root() {
  local root="$1" stray version series escaped tested
  [[ -f "$root/etc/os-release" && -f "$root/etc/pacman.conf" ]] \
    || { printf 'Not a prepared SteamOS build root, os-release or pacman.conf is missing: %s\n' "$root" >&2; return 1; }
  grep -Eq '^ID="?steamos"?$' "$root/etc/os-release" \
    || { printf 'The build root is not SteamOS: %s\n' "$root" >&2; return 1; }
  version="$(steamos_root_version "$root")"
  [[ -n "$version" ]] \
    || { printf 'The build root has no usable VERSION_ID: %s\n' "$root" >&2; return 1; }
  series="${version%.*}"
  escaped="${series//./\\.}"
  # Every repository must be a stable one from this root's own release line.
  # Valve names the stable repositories after the point-release wildcard, as in
  # jupiter-3.8.1x, while beta, Preview and main carry a bare suffix such as
  # jupiter-3.9 or jupiter-main. If a future line abandons that convention the
  # refusal below names the repository it did not recognise, and the pattern
  # here is what to update after inspecting a real image of that line.
  if stray=$(grep -E '^\[' "$root/etc/pacman.conf" | grep -Ev "^\[(options|[a-z0-9_-]+-$escaped\.[0-9]+x)\]"); then
    printf 'The build root is SteamOS %s but uses package repositories from another release line or channel:\n%s\n' "$version" "$stray" >&2
    printf 'Libraries from a different line do not belong in an image built from this root.\n' >&2
    return 1
  fi
  grep -Eq "^\[[a-z0-9_-]+-$escaped\.[0-9]+x\]" "$root/etc/pacman.conf" \
    || { printf 'The build root has no stable SteamOS %s package repository: %s\n' "$series" "$root" >&2; return 1; }
  tested="$(steamos_baseline_list tested_build_root)" || tested=''
  if [[ -n "$tested" && " $tested " != *" $version "* ]]; then
    printf 'Warning: SteamOS %s is not a validated build root for this project (validated: %s).\n' "$version" "$tested" >&2
    printf 'The build continues. Artifacts built here need fresh compatibility testing.\n' >&2
  fi
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
