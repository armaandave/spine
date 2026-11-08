#!/usr/bin/env bash
# One-time setup, run by Armaan in Terminal ON THE LAPTOP (not over SSH): lets agents on
# HomeMac sign phone builds there. Over SSH the login keychain stays locked, so codesign
# fails with errSecInternalComponent. This copies your signing identities (certificate and
# key) into their own keychain, ~/Library/Keychains/spine-agent.keychain-db, whose random password
# lives in ~/spine-agent/.keychain-pass (readable only by you); laptop phone builds unlock it each
# time and point codesign at it. The keychain isn't added to the search list, so Xcode on the
# laptop never sees the identity twice. Re-run it after a new signing certificate.
#
# macOS asks for your login password to allow the export, once per identity.
set -euo pipefail

[ -z "${SSH_CONNECTION:-}" ] || { echo "Run this in Terminal on the laptop itself, not over SSH." >&2; exit 1; }

kc=spine-agent.keychain
kc_path=~/Library/Keychains/spine-agent.keychain-db
pass_file=~/spine-agent/.keychain-pass

mkdir -p ~/spine-agent
[ -s "$pass_file" ] || (umask 077; openssl rand -hex 24 > "$pass_file")
pass=$(cat "$pass_file")
[ -f "$kc_path" ] || security create-keychain -p "$pass" "$kc"
security set-keychain-settings "$kc_path"  # no auto-lock
security unlock-keychain -p "$pass" "$kc_path"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
p12_pass=$(openssl rand -hex 16)
echo "Exporting the signing identities from your login keychain (allow the prompts)..."
security export -k login.keychain -t identities -f pkcs12 -P "$p12_pass" -o "$tmp/ids.p12"
security import "$tmp/ids.p12" -k "$kc_path" -P "$p12_pass" -T /usr/bin/codesign -T /usr/bin/security >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pass" "$kc_path" >/dev/null

echo
security find-identity -v -p codesigning "$kc_path" | grep 'Apple Development' ||
  { echo "No Apple Development identity landed in $kc_path." >&2; exit 1; }
echo "Done. Agents on HomeMac can now sign Spine Dev phone builds on this laptop."
