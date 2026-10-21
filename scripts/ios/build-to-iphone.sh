#!/usr/bin/env bash
# Build the Spine iOS app from the current checkout (myapp-main or any worktree),
# install it on the paired iPhone, and open it. Uncommitted changes in the checkout are
# included. Installing replaces the Spine app on the phone (TestFlight builds included);
# its data stays.
#
# Usage: scripts/ios/build-to-iphone.sh [--local] [--build-only] [--device <id>]
#   --local        talk to this checkout's backend on the Mac instead of production, and
#                  open signed in as its dev user. scripts/ios/local-backend.sh must be
#                  running from this checkout; the iPhone must be on the Mac's Wi-Fi.
#   --build-only   compile for a generic iPhone; do not touch the phone
#   --device       override the phone (default: SPINE_DEVICE_ID or Armaan's iPhone 14 Pro Max)
set -euo pipefail

device=${SPINE_DEVICE_ID:-5717B25A-53E3-532D-8303-931EE324948F}
build_only=0
local_backend=0
while [ $# -gt 0 ]; do
  case "$1" in
    --local) local_backend=1 ;;
    --build-only) build_only=1 ;;
    --device) device=$2; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "STOPPED: $*" >&2; exit 1; }

top=$(git rev-parse --show-toplevel)
project="$top/ios/Spine/Spine.xcodeproj"
scheme=Spine
scheme_file="$project/xcshareddata/xcschemes/$scheme.xcscheme"
[ -f "$scheme_file" ] || die "no $scheme scheme at $scheme_file"
app_name=$(awk '/BuildableName = ".*\.app"/ { sub(/.*BuildableName = "/, ""); sub(/".*/, ""); print; exit }' "$scheme_file")
[ -n "$app_name" ] || die "could not read the app name from the scheme"
# Debug builds read the SpineAPIBaseURL Info.plist key; Release builds always use production.
configuration=Debug

api_override=()
launch_args=()
if [ $local_backend -eq 1 ]; then
  if [ $build_only -eq 1 ]; then
    local_api="http://$(scutil --get LocalHostName).local:8001"
  else
    # Run the helper next to this script, so older worktrees can use myapp-main's copy.
    local_api=$("$(cd "$(dirname "$0")" && pwd)/local-backend.sh" --status 2>/dev/null | sed -n 's/^LOCAL_API: //p' | tail -n 1 || true)
    [ -n "$local_api" ] || die "this checkout's local backend isn't running. Start it in the background first: ~/.local/bin/agent-cleanup run --owner <task> --label spine-local-backend -- scripts/ios/local-backend.sh"
  fi
  api_override=("SPINE_API_BASE_URL=$local_api")
  launch_args=(--dev-sign-in)
fi

# One stable build cache per checkout, so worktrees never share build products.
key=$(printf '%s' "$top" | shasum | cut -c1-8)
build_dir="$HOME/Library/Developer/Xcode/DerivedData/spine-device-$(basename "$top")-$key"
log="$build_dir.log"

echo "Checkout: $top"
echo "Branch:   $(git -C "$top" symbolic-ref --quiet --short HEAD || echo detached) ($(git -C "$top" log -1 --format='%h %s'))"
if [ -n "$(git -C "$top" status --porcelain -- ios)" ]; then
  echo "Note:     uncommitted iOS changes are included in this build"
fi

if [ $build_only -eq 1 ]; then
  destination="generic/platform=iOS"
else
  line=$(xcrun devicectl list devices 2>/dev/null | grep -F "$device" || true)
  [ -n "$line" ] || die "iPhone $device is not paired with this Mac. Check that it is unlocked, trusted, and has Developer Mode on."
  case "$line" in
    *unavailable*) die "iPhone is paired but unavailable. Unlock it and keep it on the same Wi-Fi or plugged in." ;;
  esac
  echo "iPhone:   $(printf '%s' "$line" | awk -F'  +' '{ print $1 }')"
  destination="id=$device"
fi

echo "Building $scheme ($configuration)... log: $log"
mkdir -p "$(dirname "$build_dir")"
if ! xcodebuild -project "$project" -scheme "$scheme" -configuration "$configuration" \
    -destination "$destination" -derivedDataPath "$build_dir" \
    -allowProvisioningUpdates ${api_override[@]+"${api_override[@]}"} build > "$log" 2>&1; then
  grep -E "error:|BUILD FAILED" "$log" | head -n 30 >&2 || true
  die "build failed; full log at $log. Nothing was installed."
fi

app="$build_dir/Build/Products/$configuration-iphoneos/$app_name"
[ -d "$app" ] || die "build succeeded but $app is missing"
bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Info.plist")
api=$(/usr/libexec/PlistBuddy -c 'Print :SpineAPIBaseURL' "$app/Info.plist" 2>/dev/null || echo unknown)
echo "Built:    $app_name ($bundle_id), talks to $api"
if [ $local_backend -eq 1 ] && [ "$api" != "$local_api" ]; then
  die "the build talks to $api instead of $local_api; nothing was installed"
fi

if [ $build_only -eq 1 ]; then
  echo "BUILD ONLY: nothing was installed."
  exit 0
fi

xcrun devicectl device install app --device "$device" "$app" >/dev/null
xcrun devicectl device process launch --device "$device" --terminate-existing "$bundle_id" ${launch_args[@]+"${launch_args[@]}"} >/dev/null
echo "INSTALLED: $app_name is open on the iPhone."
if [ $local_backend -eq 1 ]; then
  echo "It talks to $local_api and opened signed in as the dev user. If iOS asks to find devices on"
  echo "the local network, tap Allow. Opened later from the home screen, it stays on that backend."
fi
