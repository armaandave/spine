#!/usr/bin/env bash
# Which machine this checkout's iOS builds, simulators and test runs use: Armaan's laptop or
# HomeMac (scripts/ios/LAPTOP.md). The answer is saved in the checkout's own git folder, so
# it survives a chat being summarized and every later step reads the same one. Removing the
# worktree removes it.
#
# Usage: scripts/ios/machine.sh              prints laptop, homemac or unset
#        scripts/ios/machine.sh set <laptop|homemac>
set -euo pipefail

file="$(git rev-parse --absolute-git-dir)/spine-ios-machine"

case "${1:-}" in
  "")
    if [ -s "$file" ]; then cat "$file"; else echo unset; fi
    ;;
  set)
    case "${2:-}" in
      laptop|homemac) echo "$2" > "$file"; echo "$2" ;;
      *) echo "Usage: $0 set <laptop|homemac>" >&2; exit 2 ;;
    esac
    ;;
  *)
    sed -n '2,9p' "$0"; exit 2
    ;;
esac
