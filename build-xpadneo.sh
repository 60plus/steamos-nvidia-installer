#!/bin/bash
# Compatibility entry point. All options are handled by the main installer.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/steamos-nvidia-installer.sh" --xpadneo "$@"
