---
name: spine-test-on-iphone
description: >-
  Test a Spine feature, branch, or worktree on Armaan's iPhone. Use when the user says
  "test this on my iPhone", "build this to my phone", "try this worktree on my phone",
  or similar. It installs the "Spine Dev" app next to the real Spine app; Spine Dev talks
  to the always-on test backend on the Mac, which serves the branch's backend changes when
  it has any, so nothing is deployed and production's data is never touched.
  Prefer this over the generic build-to-iphone skill inside the Spine repo.
---

**Required first step:** read `scripts/ios/TEST_ON_IPHONE.md` (repo root) in full and
follow it. It is the single source of truth for this workflow; this file is only a
pointer. If the current checkout doesn't have that file yet (an older branch), read
`~/projects/spine/scripts/ios/TEST_ON_IPHONE.md` instead.

Core rules, in case of doubt:

1. `scripts/ios/feature-status.sh`; if its verdict is `local-backend`, run
   `scripts/ios/test-backend.sh use` (the always-on test backend serves this checkout
   for 4 hours). Then `scripts/ios/build-to-iphone.sh`, run under agent-cleanup with
   `--queue ios` (heavy iOS work takes turns), installs Spine Dev (the Debug
   build, its own app with a DEV icon) signed in as the test backend's dev user. Nothing
   is deployed.
2. Debug builds never talk to production. Never test with the user's real account or the
   TestFlight Spine app, never merge, push or deploy, and never point a build at port
   8000 (production) in this workflow. Shipping is the spine-ship skill.
