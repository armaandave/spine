#!/bin/bash
# Build a frozen snapshot of the site (no hot reload) into stable/ (or another folder).
# Serve it with: ROOT=$PWD/stable node tools/serve-stable.mjs   (port 5198)
# Usage: tools/publish-stable.sh [outdir]   (default stable; the demo loop builds frozen/, served on :5197)
set -e
cd "$(dirname "$0")/.."
out="${1:-stable}"
npx tsc -p . >/dev/null
rm -rf "$out.tmp"
npx vite build --outDir "$out.tmp" --emptyOutDir >/dev/null
rm -rf "$out" && mv "$out.tmp" "$out"
echo "$out built $(date +%T) from $(git rev-parse --short HEAD)$(git diff --quiet || echo '+changes')" | tee "$out/BUILD.txt"
