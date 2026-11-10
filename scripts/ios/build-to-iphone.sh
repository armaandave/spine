#!/usr/bin/env bash
# Build the "Spine Dev" app (Debug, bundle ID com.armaan.Spine.dev) from the current
# checkout (myapp-main or any worktree), install it on the paired iPhone, and open it.
# Uncommitted changes in the checkout are included. Spine Dev sits next to the real Spine
# app from TestFlight and never touches it or production: it talks to the always-on test
# backend on the Mac (scripts/ios/test-backend.sh) and opens signed in as its dev user. If
# the branch has backend changes production doesn't have, the test backend must be serving
# this checkout first (scripts/ios/test-backend.sh use). The iPhone must be on the Mac's Wi-Fi.
#
# Usage: scripts/ios/build-to-iphone.sh [--laptop] [--build-only] [--device <id>]
#   --laptop       build and install from Armaan's laptop instead of this Mac
#                  (scripts/ios/LAPTOP.md); the phone must be plugged into the laptop or on its Wi-Fi
#   --build-only   compile for a generic iPhone; do not touch the phone
#   --local        accepted for older instructions; changes nothing
#   --device       override the phone (default: SPINE_DEVICE_ID or Armaan's iPhone 14 Pro Max)
set -euo pipefail

device=${SPINE_DEVICE_ID:-5717B25A-53E3-532D-8303-931EE324948F}
all_args=("$@")
build_only=0
on_laptop=0
use_laptop=0
while [ $# -gt 0 ]; do
  case "$1" in
    --local) ;;
    --build-only) build_only=1 ;;
    --laptop) use_laptop=1 ;;
    # Internal: laptop.sh runs this on the laptop with the test backend's address and what it
    # serves, already checked here, since the test backend lives on this Mac.
    --on-laptop) on_laptop=1; given_api=$2; given_serving=$3; shift 2 ;;
    --device) device=$2; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
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
grep -q 'PRODUCT_BUNDLE_IDENTIFIER = com.armaan.Spine.dev;' "$project/project.pbxproj" ||
  die "this checkout predates Spine Dev, so its Debug build would replace the real Spine app. Merge origin/myapp-main into the branch first; nothing was built."
# Debug is the Spine Dev app; it reads the SpineAPIBaseURL Info.plist key and never uses
# production. Release (com.armaan.Spine) is only built by Xcode Cloud for TestFlight.
configuration=Debug

# Run the helpers next to this script, so older worktrees can use myapp-main's copies.
here=$(cd "$(dirname "$0")" && pwd)
if [ $on_laptop -eq 1 ]; then
  local_api=$given_api
  serving=$given_serving
elif [ $build_only -eq 1 ]; then
  local_api=$("$here/test-backend.sh" api)
  serving="(not checked)"
else
  backend=$("$here/test-backend.sh" status 2>&1) || die "${backend#STOPPED: }"
  local_api=$(printf '%s\n' "$backend" | sed -n 's/^TEST_API: //p')
  serving=$(printf '%s\n' "$backend" | sed -n 's/^SERVING: //p')
  served=$(printf '%s\n' "$backend" | sed -n 's/^SERVED_CHECKOUT: //p')
  verdict=$("$here/feature-status.sh" 2>/dev/null | sed -n 's/^VERDICT: //p')
  if [ "$verdict" = local-backend ] && [ "$served" != "$top" ]; then
    die "this branch has backend changes production doesn't have, and the test backend serves $serving. Serve this checkout first: scripts/ios/test-backend.sh use"
  fi
fi

if [ $use_laptop -eq 1 ]; then
  grep -q -- '--on-laptop' "$top/scripts/ios/build-to-iphone.sh" ||
    die "this checkout predates laptop builds. Merge origin/myapp-main into the branch first; nothing was built."
  args=(--on-laptop "$local_api" "$serving" --device "$device")
  [ $build_only -eq 1 ] && args+=(--build-only)
  echo "Building on the laptop; this Mac's ios queue isn't used."
  exec "$here/laptop.sh" run scripts/ios/build-to-iphone.sh "${args[@]}"
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

sign_args=()
if [ $on_laptop -eq 1 ]; then
  # Over SSH the login keychain is locked, so sign from the agent keychain that
  # laptop-signing-setup.sh made (scripts/ios/LAPTOP.md).
  kc=$HOME/Library/Keychains/spine-agent.keychain-db
  [ -f "$kc" ] && [ -s "$HOME/spine-agent/.keychain-pass" ] ||
    die "the laptop can't sign phone builds yet. Armaan runs this once in Terminal on the laptop: $top/scripts/ios/laptop-signing-setup.sh"
  # One laptop phone build at a time, since they change the keychain search list.
  if [ -z "${SPINE_LAPTOP_SIGNING:-}" ]; then
    SPINE_LAPTOP_SIGNING=1 exec lockf -s "$HOME/spine-agent/.signing.lock" "$0" "${all_args[@]}"
  fi
  security unlock-keychain -p "$(cat "$HOME/spine-agent/.keychain-pass")" "$kc"
  # codesign only finds the key while its keychain is on the search list (with
  # --keychain alone it still fails with errSecInternalComponent), so list it first for
  # this build and put the list back afterwards, even if the build fails.
  original_keychains=()
  while IFS= read -r line; do
    line=${line#"${line%%[![:space:]]*}"}; line=${line#\"}; line=${line%\"}
    [ -n "$line" ] && original_keychains+=("$line")
  done < <(security list-keychains -d user)
  trap 'security list-keychains -d user -s "${original_keychains[@]}"' EXIT
  security list-keychains -d user -s "$kc" "${original_keychains[@]}"
  sign_args=("OTHER_CODE_SIGN_FLAGS=--keychain $kc")
fi

echo "Building $scheme ($configuration)... log: $log"
mkdir -p "$(dirname "$build_dir")"
if ! xcodebuild -project "$project" -scheme "$scheme" -configuration "$configuration" \
    -destination "$destination" -derivedDataPath "$build_dir" \
    -allowProvisioningUpdates "SPINE_API_BASE_URL=$local_api" ${sign_args[@]+"${sign_args[@]}"} build > "$log" 2>&1; then
  grep -E "error:|BUILD FAILED" "$log" | head -n 30 >&2 || true
  die "build failed; full log at $log. Nothing was installed."
fi

app="$build_dir/Build/Products/$configuration-iphoneos/$app_name"
[ -d "$app" ] || die "build succeeded but $app is missing"
bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app/Info.plist")
api=$(/usr/libexec/PlistBuddy -c 'Print :SpineAPIBaseURL' "$app/Info.plist" 2>/dev/null || echo unknown)
echo "Built:    $app_name ($bundle_id), talks to $api ($serving)"
if [ "$api" != "$local_api" ]; then
  die "the build talks to $api instead of $local_api; nothing was installed"
fi

if [ $build_only -eq 1 ]; then
  echo "BUILD ONLY: nothing was installed."
  exit 0
fi

xcrun devicectl device install app --device "$device" "$app" >/dev/null
xcrun devicectl device process launch --device "$device" --terminate-existing "$bundle_id" --dev-sign-in >/dev/null
echo "INSTALLED: Spine Dev ($bundle_id) is open on the iPhone, next to the real Spine app."
echo "It talks to the test backend at $local_api, serving $serving, and opened signed in as the"
echo "dev user. If iOS asks to find devices on the local network, tap Allow."
