---
name: spine-test-on-iphone
description: >-
  Test a Spine feature, branch, or worktree on Armaan's iPhone. Use when the user says
  "test this on my iPhone", "build this to my phone", "try this worktree on my phone",
  or similar. Backend changes are tested against the branch's own backend running on
  the Mac, so nothing is deployed; then it builds and installs the app.
  Prefer this over the generic build-to-iphone skill inside the Spine repo.
---

**Required first step:** read `scripts/ios/TEST_ON_IPHONE.md` (repo root) in full and
follow it. It is the single source of truth for this workflow; this file is only a
pointer. If the current checkout doesn't have that file yet (an older branch), read
`~/projects/spine/scripts/ios/TEST_ON_IPHONE.md` instead.

Core rules, in case of doubt:

1. `scripts/ios/feature-status.sh` says how to build: `local-backend` means start
   `scripts/ios/local-backend.sh` (under agent-cleanup, in the background), then run
   `scripts/ios/build-to-iphone.sh --local`. Nothing is deployed.
2. Never merge, push, deploy, or point a build at port 8000 (production) in this
   workflow. Shipping is the spine-ship skill.
3. `scripts/ios/build-to-iphone.sh` builds and installs the current checkout.
