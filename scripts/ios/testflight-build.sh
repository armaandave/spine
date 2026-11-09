#!/usr/bin/env bash
# Start an Xcode Cloud build of myapp-main now, for when Armaan says "make a TestFlight build"
# (scripts/ios/SHIP.md, "TestFlight builds"). It archives whatever myapp-main holds and
# uploads it to TestFlight like a push-started build. This spends Xcode Cloud hours and
# writes to App Store Connect, so run it only on his request.
#
# Usage: scripts/ios/testflight-build.sh
set -euo pipefail

workflow=402D425F-065C-42A4-87AB-E95241728CC8   # Xcode Cloud "Default" (AGENTS.md section 17)
branch_ref=6b291a38-5c98-4865-ab0d-735fc894cc8f # myapp-main in the workflow's repository
asc=(node "$HOME/.appstoreconnect/asc.mjs")

body=$(printf '{"data":{"type":"ciBuildRuns","relationships":{"workflow":{"data":{"type":"ciWorkflows","id":"%s"}},"sourceBranchOrTag":{"data":{"type":"scmGitReferences","id":"%s"}}}}}' "$workflow" "$branch_ref")
out=$(ASC_APP_ID=6825566855 "${asc[@]}" post /v1/ciBuildRuns "$body")
number=$(printf '%s' "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("data",{}).get("attributes",{}).get("number",""))' 2>/dev/null || true)
[ -n "$number" ] || { echo "STOPPED: Xcode Cloud didn't start a build:" >&2; printf '%s\n' "$out" >&2; exit 1; }
echo "STARTED: Xcode Cloud build run $number of myapp-main ($(git rev-parse --short origin/myapp-main 2>/dev/null || echo '?'))."
echo "Then follow SHIP.md step 9: wait for VALID, add it to \"Me\", tag the commit."
