---
name: spine-test-on-iphone
description: >-
  Test a Spine feature, branch, or worktree on Armaan's iPhone. Use when the user says
  "test this on my iPhone", "build this to my phone", "try this worktree on my phone",
  or similar. Every test runs against the branch's own backend on the Mac, so nothing
  is deployed and production's data is never touched; it installs the "Spine Dev" app
  next to the real Spine app.
  Prefer this over the generic build-to-iphone skill inside the Spine repo.
---

**Required first step:** read `scripts/ios/TEST_ON_IPHONE.md` (repo root) in full and
follow it. It is the single source of truth for this workflow; this file is only a
pointer. If the current checkout doesn't have that file yet (an older branch), read
`~/projects/spine/scripts/ios/TEST_ON_IPHONE.md` instead.

Core rules, in case of doubt:

1. Every phone test is the same: start `scripts/ios/local-backend.sh` (under
   agent-cleanup, in the background), then run `scripts/ios/build-to-iphone.sh`. It
   installs Spine Dev (the Debug build, its own app with a DEV icon) signed in as the
   local dev user. Nothing is deployed.
2. Debug builds never talk to production. Never test with the user's real account or the
   TestFlight Spine app, never merge, push or deploy, and never point a build at port
   8000 (production) in this workflow. Shipping is the spine-ship skill.
